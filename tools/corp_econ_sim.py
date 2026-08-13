"""Corp economy balance harness: how fast a corp actually grows, earns and researches.

Not part of the game and not imported by it — a developer tool, run by hand.

**Why it exists.** `tools/conflict_sim.py` measures the war; nothing measured the
economy that feeds it. DESIGN.md's corp sections flag income, the research ladder,
the Academy's training costs and the free actions as "first-slice, not
balance-simulated", and name `FUNDRAISE_PER_TERRITORY` against
`TERRITORY_INCOME_BASE`/`_PER_VALUE` as the pairing most likely to need tuning.

The corp economy is **deterministic** — no dice anywhere in income, research,
training or the upgrade ladder — so this is a day loop rather than a Monte Carlo.
The only randomness is which map a seed lays out (district values and bloc shape).

Runs the real day tick in `ShadowguyApp._apply_day_tick`'s order (income, research,
`action_points = 2`, `advance_training`) against a real `generate_corp_map`, and
spends the day through the real `corp_turn` actions — every one of which fails
closed, so a policy that asks for something unaffordable simply doesn't get it.

**Rival factions are off by default**, so growth here is the *unopposed ceiling*:
nobody else claims the neutral ground and nobody attacks. `--rivals` turns the AI
day on for a realism check (that path is `conflict_sim.py`'s subject, not this
one's).

Usage:

    uv run python tools/corp_econ_sim.py                    # policy comparison
    uv run python tools/corp_econ_sim.py --trace wide       # one run, day by day
    uv run python tools/corp_econ_sim.py --earn-rates       # eb/AP by cash action
    uv run python tools/corp_econ_sim.py --expansion-payback # which district to buy
    uv run python tools/corp_econ_sim.py --rivals           # with the AI acting
"""

import argparse
import random
import statistics
from dataclasses import dataclass, field

from shadowguy.character import Character
from shadowguy.corpmap import CorpMap, Territory, TerritoryModifier, expansion_candidates
from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.corp_turn import (
    ACADEMY_TRAINING_COST,
    ACADEMY_UPGRADE_COSTS,
    EFFICIENCY_UPGRADE_COSTS,
    DEVELOPMENT_MIN_SECURITY,
    DEVELOPMENT_MIN_SURVEILLANCE,
    FUNDRAISE_PER_TERRITORY,
    LAB_UPGRADE_COSTS,
    LOGISTICS_BASE_CAPACITY,
    LOGISTICS_STRAIN_COST,
    TERRITORY_INCOME_BASE,
    TERRITORY_INCOME_PER_VALUE,
    TERRITORY_UPKEEP,
    TRAINING_DAYS,
    CorpState,
    EmployeeCategory,
    TECHNOLOGIES,
    advance_training,
    assistant_capacity,
    build_efficiency_upgrade,
    build_lab,
    collect_income,
    collect_research,
    expand_into,
    development_targets,
    expansion_cost,
    fundraise,
    lab_capacity,
    next_academy_upgrade_cost,
    next_efficiency_cost,
    next_lab_cost,
    logistics_capacity,
    logistics_strain,
    owned_academy,
    owned_research_facility,
    prereqs_met,
    raise_development,
    raise_security,
    raise_surveillance,
    research_technology,
    security_bump_cost,
    security_targets,
    surveillance_bump_cost,
    surveillance_targets,
    upgrade_academy,
)
from shadowguy.rivals import resolve_rival_day

DAYS = 120
SEEDS = range(30)


# =============================================================================
# One run
# =============================================================================

@dataclass
class DaySnapshot:
    day: int
    territories: int
    cash: int
    research_points: float
    income: int
    research: float
    scientists: int
    assistants: int
    labs: int
    efficiency: int
    academy_tier: int
    techs: int


@dataclass
class RunResult:
    policy: str
    seed: int
    territories: int = 0
    cash: int = 0
    total_income: int = 0
    total_research: float = 0.0
    techs: int = 0
    first_tech_day: int | None = None
    third_tech_day: int | None = None
    scientists: int = 0
    assistants: int = 0
    labs: int = 0
    efficiency: int = 0
    academy_tier: int = 0
    final_income: int = 0
    final_research: float = 0.0
    # Logistics at the end of the run: how many districts the supply lines
    # support vs. what the corp actually holds, and what the overage costs.
    final_capacity: int = 0
    final_strain: int = 0
    development_bumps: int = 0
    security_bumps: int = 0
    surveillance_bumps: int = 0
    # How the day's two action points were actually used, summed over the run.
    ap_spent: int = 0
    ap_idle: int = 0
    fundraises: int = 0
    expansions: int = 0
    trainings: int = 0
    # Days the Academy sat idle with nothing training — the training slot is a
    # separate bottleneck from cash, and this is what shows which one binds.
    academy_idle_days: int = 0
    trace: list[DaySnapshot] = field(default_factory=list)


def _owned(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    return [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]


def _expansion_pick(corp_state: CorpState, corp_map: CorpMap, mode: str) -> str | None:
    """Which bordering neutral district to buy. `payback` buys the district that
    repays its own expansion_cost fastest, which is not the cheapest one: cost
    climbs EXPANSION_COST_PER_VALUE (100) per point of value while income climbs
    TERRITORY_INCOME_PER_VALUE (15), so a rich district is a *better* deal despite
    the sticker — see --expansion-payback."""
    candidates = list(expansion_candidates(corp_map, corp_state.faction_id))
    if not candidates:
        return None
    territories = [corp_map.territories[tid] for tid in candidates]
    affordable = [t for t in territories if expansion_cost(t, corp_state, corp_map) <= corp_state.cash]
    if not affordable:
        return None
    if mode == "cheapest":
        return min(affordable, key=lambda t: (expansion_cost(t, corp_state, corp_map), t.id)).id
    return min(
        affordable,
        key=lambda t: (
            expansion_cost(t, corp_state, corp_map) / max(1, _net_income(t)),
            t.id,
        ),
    ).id


def _net_income(territory: Territory) -> int:
    """One district's daily income after TERRITORY_UPKEEP — what a payback
    calculation has to divide by, since gross would price poor ground as a much
    better buy than it is."""
    return TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * territory.value - TERRITORY_UPKEEP


def _try_expand(corp_state: CorpState, corp_map: CorpMap, result: RunResult, rng: random.Random, mode: str) -> bool:
    target = _expansion_pick(corp_state, corp_map, mode)
    if target is None or not expand_into(corp_state, corp_map, target, rng):
        return False
    result.expansions += 1
    return True


def _needed_category(corp_state: CorpState, corp_map: CorpMap) -> EmployeeCategory | None:
    """Which researcher the corp actually has a seat for. Training beyond capacity
    produces nothing (collect_research's "actually working" rule), so a policy that
    trains blind is measuring the wrong thing — this only asks for a hire the
    facility can seat."""
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return None
    if corp_state.scientists < lab_capacity(facility, corp_state):
        return EmployeeCategory.SCIENTIST
    if corp_state.research_assistants < assistant_capacity(facility, corp_state):
        return EmployeeCategory.RESEARCH_ASSISTANT
    return None


def _try_train(corp_state: CorpState, corp_map: CorpMap, result: RunResult, day: int) -> bool:
    from shadowguy.corp_turn import train_employees

    category = _needed_category(corp_state, corp_map)
    if category is None or not train_employees(corp_state, corp_map, category, day):
        return False
    result.trainings += 1
    return True


def _try_build(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """The research ladder, cheapest rung first: a lab (capacity) or an efficiency
    upgrade (rate), whichever the facility can next afford."""
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    lab = next_lab_cost(facility)
    eff = next_efficiency_cost(facility)
    options = [(cost, fn) for cost, fn in ((lab, build_lab), (eff, build_efficiency_upgrade)) if cost is not None]
    for cost, fn in sorted(options):
        if cost <= corp_state.cash and fn(corp_state, corp_map):
            return True
    return False


def _try_academy(corp_state: CorpState, corp_map: CorpMap) -> bool:
    academy = owned_academy(corp_state, corp_map)
    if academy is None:
        return False
    cost = next_academy_upgrade_cost(academy)
    return cost is not None and cost <= corp_state.cash and upgrade_academy(corp_state, corp_map)


def _try_cash_action(corp_state: CorpState, corp_map: CorpMap, result: RunResult) -> bool:
    """The one AP-only earner left: fundraise, offered only under
    FUNDRAISE_CASH_CEILING (levy was removed — see Corp economy in DESIGN.md)."""
    if fundraise(corp_state, corp_map) is not None:
        result.fundraises += 1
        return True
    return False


# Cash a developing policy refuses to spend on Development, so building the ground
# up never starves the expansion it exists to support.
DEVELOP_CASH_RESERVE = 1000


def _under_threshold(territories, modifier, threshold):
    return [t for t in territories if t.modifiers.get(modifier, 0) < threshold]


def _spend_development(corp_state: CorpState, corp_map: CorpMap, result: RunResult) -> None:
    """Buy logistics capacity off the map: raise Development where it's already
    legal, and otherwise lift whichever of Security/Surveillance is still under its
    threshold on a district that could then be developed.

    None of the three bumps costs an action point (cash is their only gate), so
    this runs outside the AP loop the way _spend_research does. All three are
    technology-gated — raise_development needs none, but security_targets is empty
    without Private Security Force and surveillance_targets without Worker
    Surveillance — so a policy that hasn't researched them simply spends nothing
    here.
    """
    while corp_state.cash > DEVELOP_CASH_RESERVE:
        targets = development_targets(corp_state, corp_map)
        if targets:
            best = min(targets, key=lambda t: t.id)
            if raise_development(corp_state, corp_map, best.id):
                result.development_bumps += 1
                continue
        # Nothing developable yet: lift whichever threshold is in the way. Security
        # first — it's the one that used to be unmovable, and the cheaper bump of
        # the two once Rapid Response Teams is in.
        blocked_security = _under_threshold(
            security_targets(corp_state, corp_map),
            TerritoryModifier.SECURITY,
            DEVELOPMENT_MIN_SECURITY,
        )
        if blocked_security:
            cheapest = min(
                blocked_security, key=lambda t: (security_bump_cost(corp_state, t), t.id)
            )
            if not raise_security(corp_state, corp_map, cheapest.id):
                return
            result.security_bumps += 1
            continue
        blocked = [
            t
            for t in _under_threshold(
                surveillance_targets(corp_state, corp_map),
                TerritoryModifier.SURVEILLANCE,
                DEVELOPMENT_MIN_SURVEILLANCE,
            )
            if t.modifiers.get(TerritoryModifier.SECURITY, 0) >= DEVELOPMENT_MIN_SECURITY
        ]
        if not blocked:
            return
        cheapest = min(blocked, key=lambda t: (surveillance_bump_cost(corp_state, t), t.id))
        if not raise_surveillance(corp_state, corp_map, cheapest.id):
            return
        result.surveillance_bumps += 1


# Each policy is an ordered list of action names; the day spends its action points
# on the first one that succeeds, top down, and banks the AP if none do. "develop"
# is the exception: it costs no AP, so it's run once a day before the AP loop and
# is inert inside it.
POLICIES = {
    # Buy ground and nothing else — the income curve's ceiling.
    "wide": ["expand", "cash"],
    # Buy the research ladder and staff it; expand only with nothing else to do.
    "tall": ["build", "academy", "train", "cash", "expand"],
    # Ground first up to a point, then the ladder.
    "balanced": ["expand", "build", "train", "academy", "cash"],
    # Never expand: the pure research line, to isolate what a corp's *starting*
    # bloc can fund on its own.
    "research_only": ["build", "train", "academy", "cash"],
    # Expand, but build the ground up as it goes — the counterplay to logistics
    # strain, and the policy that shows whether developing actually pays.
    "developed": ["develop", "expand", "build", "train", "academy", "cash"],
    # Spend nothing, buy nothing: the passive-income floor a run starts on.
    "idle": [],
}


def _spend_research(corp_state: CorpState, result: RunResult, day: int, order: str) -> None:
    """Research is not on the action_points slot, so this runs every day until the
    RP won't stretch. `cheapest` buys whatever is reachable and affordable;
    `brains` prefers the research chain (compounding), `income` the surveillance
    chain (per-territory eb)."""
    preferred = {"brains": ("brains_2", "brains_3", "cognitive_uplink"),
                 "income": ("worker_surveillance", "panopticon_grid", "shadow_economy")}.get(order, ())
    while True:
        available = [
            tech
            for tech in TECHNOLOGIES
            if tech.id not in corp_state.researched
            and prereqs_met(corp_state, tech)
            and tech.cost <= corp_state.research_points
            and (tech.faction_id is None or tech.faction_id == corp_state.faction_id)
        ]
        if not available:
            return
        pick = next((t for t in available if t.id in preferred), None)
        pick = pick or min(available, key=lambda t: (t.cost, t.id))
        if not research_technology(corp_state, pick.id):
            return
        result.techs += 1
        if result.first_tech_day is None:
            result.first_tech_day = day
        if result.techs == 3 and result.third_tech_day is None:
            result.third_tech_day = day


def run_once(
    policy: str,
    seed: int,
    *,
    days: int = DAYS,
    expand_mode: str = "payback",
    tech_order: str = "cheapest",
    with_rivals: bool = False,
    keep_trace: bool = False,
) -> RunResult:
    rng = random.Random(seed)
    corp_map = generate_corp_map(list(FACTIONS), rng)
    faction_id = FACTIONS[0].id
    corp_state = CorpState(faction_id=faction_id)
    result = RunResult(policy=policy, seed=seed)
    actions = POLICIES[policy]

    for day in range(1, days + 1):
        # --- The day tick, in app._apply_day_tick's order -----------------
        if with_rivals:
            # runners=[] keeps this to the faction half of the rival day — the
            # independent-runner turn touches nothing the corp economy reads.
            resolve_rival_day(
                Character(name="unused"), corp_map, day, rng,
                player_faction_id=faction_id, corp_state=corp_state, runners=[],
            )
        if not _owned(corp_state, corp_map):
            break  # corp_defeated — only reachable with --rivals
        income = collect_income(corp_state, corp_map)
        corp_state.cash += income
        research = collect_research(corp_state, corp_map)
        corp_state.research_points += research
        corp_state.action_points = 2
        advance_training(corp_state, day)
        result.total_income += income
        result.total_research += research

        # --- Spend the day -------------------------------------------------
        _spend_research(corp_state, result, day, tech_order)
        if "develop" in actions:
            _spend_development(corp_state, corp_map, result)
        while corp_state.action_points > 0:
            for action in actions:
                acted = (
                    _try_expand(corp_state, corp_map, result, rng, expand_mode) if action == "expand"
                    else _try_build(corp_state, corp_map) if action == "build"
                    else _try_academy(corp_state, corp_map) if action == "academy"
                    else _try_train(corp_state, corp_map, result, day) if action == "train"
                    else _try_cash_action(corp_state, corp_map, result) if action == "cash"
                    else False
                )
                if acted:
                    result.ap_spent += 1
                    break
            else:
                result.ap_idle += corp_state.action_points
                break
        if corp_state.pending_recruit is None:
            result.academy_idle_days += 1

        if keep_trace:
            facility = owned_research_facility(corp_state, corp_map)
            academy = owned_academy(corp_state, corp_map)
            result.trace.append(DaySnapshot(
                day=day,
                territories=len(_owned(corp_state, corp_map)),
                cash=corp_state.cash,
                research_points=corp_state.research_points,
                income=income,
                research=research,
                scientists=corp_state.scientists,
                assistants=corp_state.research_assistants,
                labs=(facility.labs_built or 0) if facility else 0,
                efficiency=(facility.efficiency_upgrades or 0) if facility else 0,
                academy_tier=(academy.academy_tier or 0) if academy else 0,
                techs=result.techs,
            ))

    facility = owned_research_facility(corp_state, corp_map)
    academy = owned_academy(corp_state, corp_map)
    result.territories = len(_owned(corp_state, corp_map))
    result.cash = corp_state.cash
    result.scientists = corp_state.scientists
    result.assistants = corp_state.research_assistants
    result.labs = (facility.labs_built or 0) if facility else 0
    result.efficiency = (facility.efficiency_upgrades or 0) if facility else 0
    result.academy_tier = (academy.academy_tier or 0) if academy else 0
    result.final_capacity = logistics_capacity(corp_state, corp_map)
    result.final_strain = logistics_strain(corp_state, corp_map)
    result.final_income = collect_income(corp_state, corp_map)
    result.final_research = collect_research(corp_state, corp_map)
    return result


# =============================================================================
# Reports
# =============================================================================

def report_policies(batches: dict[str, list[RunResult]], days: int) -> None:
    print(f"\nPolicy comparison — day {days}, {len(next(iter(batches.values())))} maps:\n")
    header = (f"{'policy':<14} {'terr':>5} {'cash':>8} {'eb/day':>7} {'rp/day':>7} "
              f"{'techs':>6} {'tech1':>6} {'tech3':>6} {'sci':>4} {'asst':>5} "
              f"{'lab':>4} {'eff':>4} {'acad':>5} {'AP idle':>8}")
    print(header)
    print("-" * len(header))
    for policy, batch in batches.items():
        def mean(fn):
            return statistics.mean(fn(r) for r in batch)

        def day_mean(fn):
            vals = [fn(r) for r in batch if fn(r) is not None]
            return f"{statistics.mean(vals):.0f}" if vals else "never"

        ap_total = mean(lambda r: r.ap_spent + r.ap_idle)
        print(f"{policy:<14} {mean(lambda r: r.territories):>5.1f} {mean(lambda r: r.cash):>8.0f} "
              f"{mean(lambda r: r.final_income):>7.0f} {mean(lambda r: r.final_research):>7.1f} "
              f"{mean(lambda r: r.techs):>6.1f} {day_mean(lambda r: r.first_tech_day):>6} "
              f"{day_mean(lambda r: r.third_tech_day):>6} {mean(lambda r: r.scientists):>4.1f} "
              f"{mean(lambda r: r.assistants):>5.1f} {mean(lambda r: r.labs):>4.1f} "
              f"{mean(lambda r: r.efficiency):>4.1f} {mean(lambda r: r.academy_tier):>5.1f} "
              f"{mean(lambda r: r.ap_idle) / ap_total:>7.0%}")

    print("\n  Logistics — districts held vs. supplied, and what the overage costs:")
    print(f"  {'policy':<14} {'held':>6} {'supplied':>9} {'over':>6} {'strain/day':>11} "
          f"{'dev bumps':>10} {'sec bumps':>10} {'surv bumps':>11}")
    for policy, batch in batches.items():
        def mean(fn):
            return statistics.mean(fn(r) for r in batch)

        print(f"  {policy:<14} {mean(lambda r: r.territories):>6.1f} "
              f"{mean(lambda r: r.final_capacity):>9.1f} "
              f"{mean(lambda r: max(0, r.territories - r.final_capacity)):>6.1f} "
              f"{mean(lambda r: r.final_strain):>11.0f} "
              f"{mean(lambda r: r.development_bumps):>10.1f} "
              f"{mean(lambda r: r.security_bumps):>10.1f} "
              f"{mean(lambda r: r.surveillance_bumps):>11.1f}")

    print("\n  Academy idle (days with nothing training) and free-action use:")
    print(f"  {'policy':<14} {'idle%':>7} {'trainings':>10} {'expansions':>11} {'fundraises':>11}")
    for policy, batch in batches.items():
        def mean(fn):
            return statistics.mean(fn(r) for r in batch)

        print(f"  {policy:<14} {mean(lambda r: r.academy_idle_days) / days:>6.0%} "
              f"{mean(lambda r: r.trainings):>10.1f} {mean(lambda r: r.expansions):>11.1f} "
              f"{mean(lambda r: r.fundraises):>11.1f}")


def report_trace(result: RunResult) -> None:
    print(f"\nTrace — policy {result.policy}, seed {result.seed}:\n")
    header = (f"{'day':>4} {'terr':>5} {'cash':>8} {'eb/day':>7} {'rp':>7} {'rp/day':>7} "
              f"{'sci':>4} {'asst':>5} {'lab':>4} {'eff':>4} {'acad':>5} {'techs':>6}")
    print(header)
    print("-" * len(header))
    step = max(1, len(result.trace) // 30)
    for i, snap in enumerate(result.trace):
        if i % step and i != len(result.trace) - 1:
            continue
        print(f"{snap.day:>4} {snap.territories:>5} {snap.cash:>8} {snap.income:>7} "
              f"{snap.research_points:>7.1f} {snap.research:>7.1f} {snap.scientists:>4} "
              f"{snap.assistants:>5} {snap.labs:>4} {snap.efficiency:>4} "
              f"{snap.academy_tier:>5} {snap.techs:>6}")


def report_earn_rates() -> None:
    """What one action point buys, by earner. Passive income and the one remaining
    cash action are the corp's whole income model, priced against each other here
    rather than in isolation."""
    print("\nWhat one action point earns (eb), by district count and value:\n")
    print(f"{'districts':>10} {'fundraise':>11} {'net/day v=1':>13} {'net/day v=3':>13} "
          f"{'strain/day':>11}")
    print("-" * 80)
    for n in (3, 5, 8, 12, 20, 40, 80):
        # Net, not gross: passive income is what fundraising has to be priced
        # against, and gross overstates it by TERRITORY_UPKEEP per district --
        # which is what made a fundraise look like a day's income when it is
        # several. Strain assumes districts developed enough to carry themselves
        # halfway (the sim's own measured ~0.5 capacity per district).
        income1 = n * _net_income(Territory(id="x", name="X", x=0, y=0, owner="us", value=1))
        income3 = n * _net_income(Territory(id="x", name="X", x=0, y=0, owner="us", value=3))
        capacity = LOGISTICS_BASE_CAPACITY + n // 2
        over = max(0, n - capacity)
        strain = LOGISTICS_STRAIN_COST * over * (over + 1) // 2
        # Supplied, not held -- fundraise_amount is capped at logistics_capacity.
        raised = FUNDRAISE_PER_TERRITORY * min(n, capacity)
        print(f"{n:>10} {raised:>11} {income1:>13} {income3:>13} {strain:>11}")
    print(f"\n  fundraise: {FUNDRAISE_PER_TERRITORY}eb per *supplied* district "
          "(min(held, logistics_capacity)), capped to a corp under "
          "FUNDRAISE_CASH_CEILING, and the only AP-only earner left.")
    print("  Passive income is per day, net of TERRITORY_UPKEEP and of logistics "
          "strain; fundraising costs the AP that expanding or building would have used.")
    print("  Both columns scale with the same district count, which is why "
          "fundraise_amount is capped at logistics_capacity -- see Corp economy in "
          "DESIGN.md.")

    print("\nWhat the ladder costs, and what it returns:\n")
    print(f"  labs         {LAB_UPGRADE_COSTS}  -> +1 scientist seat each "
          f"(+2 assistant seats via RESEARCH_ASSISTANTS_PER_LAB)")
    print(f"  efficiency   {EFFICIENCY_UPGRADE_COSTS}  -> +1 rp/day per working scientist")
    print(f"  academy      {ACADEMY_UPGRADE_COSTS}  -> +1 hire per training batch")
    print(f"  training     {dict(ACADEMY_TRAINING_COST)}")
    print(f"  training days{ {k.value: v for k, v in TRAINING_DAYS.items()} }")
    ladder = sum(LAB_UPGRADE_COSTS) + sum(EFFICIENCY_UPGRADE_COSTS) + sum(ACADEMY_UPGRADE_COSTS)
    print(f"\n  Full ladder: {ladder}eb. At tier-1 Academy the same build needs "
          f"3 scientist batches ({3 * TRAINING_DAYS[EmployeeCategory.SCIENTIST]}d of slot) "
          f"+ 6 assistant batches ({6 * TRAINING_DAYS[EmployeeCategory.RESEARCH_ASSISTANT]}d) "
          f"through one Academy slot.")


def report_expansion_payback() -> None:
    """Cost climbs 100/value, income climbs 15/value — so payback *improves* with
    value, which is the opposite of what a cost curve usually implies."""
    print("\nExpansion payback by district value (corpmap_gen only rolls 1-3):\n")
    print(f"{'value':>6} {'cost':>7} {'gross/day':>10} {'net/day':>8} {'payback days':>13}")
    print("-" * 50)
    for value in range(0, 6):
        t = Territory(id="x", name="X", x=0, y=0, owner="neutral", value=value)
        # None, None -- the unsprawled sticker price, which is what this table is about.
        cost = expansion_cost(t, None, None)
        gross = TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * value
        net = gross - TERRITORY_UPKEEP
        payback = f"{cost / net:.1f}" if net > 0 else "never"
        print(f"{value:>6} {cost:>7} {gross:>10} {net:>8} {payback:>13}")
    print(f"\n  Net is gross - TERRITORY_UPKEEP ({TERRITORY_UPKEEP}). Cost shown is the "
          "unsprawled price; a corp already holding ground pays "
          "(1 + held / EXPANSION_SPRAWL_DIVISOR) times it.")
    print(f"  Net also ignores logistics: a corp already n districts past "
          f"logistics_capacity pays another {LOGISTICS_STRAIN_COST} × (n+1) eb/day for "
          "this one, which is what makes payback a function of how much you already "
          "hold rather than of the district alone.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=DAYS, help=f"days per run (default {DAYS})")
    parser.add_argument("--seeds", type=int, default=len(SEEDS),
                        help=f"maps per policy (default {len(SEEDS)})")
    parser.add_argument("--trace", metavar="POLICY", choices=sorted(POLICIES),
                        help="print one run day by day instead of the comparison")
    parser.add_argument("--seed", type=int, default=0, help="seed for --trace")
    parser.add_argument("--expand-pick", default="payback", choices=("payback", "cheapest"),
                        help="which neutral district an expanding policy buys")
    parser.add_argument("--tech-order", default="cheapest", choices=("cheapest", "brains", "income"),
                        help="which technology chain research prefers")
    parser.add_argument("--rivals", action="store_true",
                        help="run the AI factions' day too (contested growth)")
    parser.add_argument("--earn-rates", action="store_true",
                        help="price the cash actions and the research ladder against each other")
    parser.add_argument("--expansion-payback", action="store_true",
                        help="expansion cost vs income by district value")
    args = parser.parse_args()

    if args.earn_rates:
        report_earn_rates()
        return
    if args.expansion_payback:
        report_expansion_payback()
        return

    if args.trace:
        result = run_once(args.trace, args.seed, days=args.days, expand_mode=args.expand_pick,
                          tech_order=args.tech_order, with_rivals=args.rivals, keep_trace=True)
        report_trace(result)
        return

    batches = {
        policy: [
            run_once(policy, seed, days=args.days, expand_mode=args.expand_pick,
                     tech_order=args.tech_order, with_rivals=args.rivals)
            for seed in range(args.seeds)
        ]
        for policy in POLICIES
    }
    report_policies(batches, args.days)


if __name__ == "__main__":
    main()
