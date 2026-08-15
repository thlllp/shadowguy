"""The player's own Corp turn: a parallel resolution module, like rivals.py/
security.py — not a Scene.

The player runs one of the 5 seeded Factions (CorpState.faction_id) rather than
founding a new one. Two ways in, both building a CorpState: a corp-only run picks
one at New Game (screens/menu_screens.py's CorpSelectScreen), and a runner earns
one mid-run by buying a controlling stake at that corp's own HQ
(screens/shop_screens.py's CorpHQScreen, gated on rep + standing +
factions.TAKEOVER_COST).

Corp mode shares the runner's own day clock rather than keeping a separate
calendar: ShadowguyApp's day tick (app._apply_day_tick) collects each day's
territory income into CorpState.cash and resets action_points, right
alongside the AI factions' own resolve_rival_day (which skips the player's
faction_id once this is set).

**A corp turn has two independent budgets**, which is the one thing worth knowing
before reading any function here:

- **The day's action points**, gated on CorpState.action_points (2 per day).
  Most actions cost 1 AP: expand_into, attack_territory, deploy_operatives,
  train_employees, build_lab, build_efficiency_upgrade, build_research_facility,
  build_academy, and operative tasking (tail/gather_intel/sabotage/investigate).
- **Whatever cash/RP has piled up.** research_technology and the three territory
  bumps (raise_security, raise_surveillance, raise_development) deliberately do NOT touch
  action_points — RP and cash are their own pacing gates, and double-gating
  them behind action points would make researching compete with expanding for
  no design reason.

Two actions cost AP and nothing else, so a corp that's out of cash still has
something to spend the day on: fundraise (an emergency valve, only under
FUNDRAISE_CASH_CEILING) and survey (gather_intel's recon without the operative).

Each faction is seeded one RESEARCH_FACILITY and one ACADEMY (corpmap.add_research_facility
/add_academy, called by the generator). A corp can come to hold two of a kind
(capturing a rival's district takes its buildings with it) or none (losing its own
the same way); build_research_facility/build_academy are the way back from none.

This module is the corp system's *behaviour*. Its two halves of pure data live
next door and are imported straight back in, so `from shadowguy.corp_turn import
<anything>` still resolves for all 14 files that do it:

- **corp_rules.py** — the base numbers (income, upkeep, logistics, expansion
  price, training cost/time, lab and academy ladders, the contest dice) plus
  EmployeeCategory.
- **technologies.py** — TECHNOLOGIES, the researchable list, and the constants
  each technology's effect is worth. Chains gated by Technology.prereqs, rendered
  as a tree by screens.corp_screen.ResearchTreeScreen (see
  technology_tree_layout). A tech's *effect* is not a field on Technology — it is
  read wherever it applies, keyed off the id, so follow the id from those
  constants to its consumer (collect_income for the surveillance chain,
  scientist_base_rate/assistant_rate for the brains chain).

The arrow is corp_rules -> technologies -> corp_turn: a technology's description
quotes the base rate it replaces, and the code applying it is here.

The state classes stay here on purpose — CorpState/PendingRecruit/Sighting/
AttackResult are all pickled into a save (directly, or via rivals.RivalAction),
and pickle resolves a class by the module path it was written with.

Leaf-ish: imports corpmap only, never scene or app.
"""

import random
from dataclasses import dataclass, field
from typing import Literal

from shadowguy.corpmap import (
    MODIFIER_MAX,
    STARTING_ACADEMY_TIER,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    TerritoryModifier,
    add_academy,
    add_research_facility,
    attack_candidates,
    capture_territory,
    claim_territory,
    expansion_candidates,
)
from shadowguy.corp_rules import (
    ACADEMY_REBUILD_COST,
    ACADEMY_TRAINING_COST,
    ACADEMY_UPGRADE_COSTS,
    AP_COST,
    BASE_LAB_CAPACITY,
    CONTEST_DIE,
    DAILY_ACTION_POINTS,
    EFFICIENCY_UPGRADE_COSTS,
    EXPANSION_COST_BASE,
    EXPANSION_COST_PER_VALUE,
    EXPANSION_SPRAWL_DIVISOR,
    EmployeeCategory,
    LAB_UPGRADE_COSTS,
    LOGISTICS_BASE_CAPACITY,
    LOGISTICS_DEVELOPMENT_PER_SLOT,
    LOGISTICS_STRAIN_COST,
    MAX_EFFICIENCY_UPGRADES,
    MAX_LABS_BUILT,
    MAX_SIGHTINGS_LOG,
    MIN_ATTACK_FORCE,
    RESEARCH_ASSISTANTS_PER_LAB,
    RESEARCH_FACILITY_REBUILD_COST,
    RESEARCH_PER_ASSISTANT,
    RESEARCH_PER_SCIENTIST,
    STARTING_CASH,
    STARTING_OPERATIVE_MAX,
    TERRITORY_INCOME_BASE,
    TERRITORY_INCOME_PER_VALUE,
    TERRITORY_UPKEEP,
    TRAINING_DAYS,
)
from shadowguy.technologies import (
    ACCELERATED_METABOLISM_ID,
    ACCELERATED_OPERATIVE_COST,
    ACCELERATED_OPERATIVE_DAYS,
    BRAINS_2_ID,
    BRAINS_2_RESEARCH_PER_ASSISTANT,
    BRAINS_2_RESEARCH_PER_SCIENTIST,
    BRAINS_3_ID,
    BRAINS_3_RESEARCH_PER_ASSISTANT,
    BRAINS_3_RESEARCH_PER_SCIENTIST,
    COGNITIVE_UPLINK_ID,
    COGNITIVE_UPLINK_RESEARCH_PER_ASSISTANT,
    COGNITIVE_UPLINK_RESEARCH_PER_SCIENTIST,
    COMBAT_STIMS_ID,
    CONSOLIDATED_FUNDRAISE_PER_TERRITORY,
    CONSOLIDATED_HOLDINGS_ID,
    COUNTERINTEL_SIGHTINGS_LOG,
    COUNTERINTEL_SURVEILLANCE_COST,
    COUNTER_INTELLIGENCE_ID,
    CRUSADE_BONUS,
    CRUSADE_ID,
    DEEP_SURVEILLANCE_ID,
    DEVELOPMENT_BUMP_COST,
    DEVELOPMENT_MIN_SECURITY,
    DEVELOPMENT_MIN_SURVEILLANCE,
    EXTENDED_SECURITY_COST,
    EXTENDED_SECURITY_MAX,
    EXTENDED_SURVEILLANCE_COST,
    EXTENDED_SURVEILLANCE_MAX,
    FUNDRAISE_CASH_CEILING,
    FUNDRAISE_PER_TERRITORY,
    HARDENED_GARRISON_ID,
    HARDENED_GARRISON_MULTIPLIER,
    INVESTIGATION_COST,
    LOGISTICS_NETWORK_CAPACITY,
    LOGISTICS_NETWORK_ID,
    MARKET_MONOPOLY_ID,
    MARKET_MONOPOLY_INCOME_BONUS,
    MARTIAL_LAW_ID,
    OPTIMIZED_WORKFORCE_ID,
    PANOPTICON_GRID_ID,
    PANOPTICON_GRID_INCOME_BONUS,
    PRIVATE_SECURITY_ID,
    RAPID_DEPLOYMENT_ID,
    RAPID_RESPONSE_ID,
    RAPID_RESPONSE_SECURITY_COST,
    RESEARCH_EXPANSION_BONUS,
    RESEARCH_EXPANSION_ID,
    SECURITY_BUMP_COST,
    SHADOW_ECONOMY_ID,
    SHADOW_ECONOMY_INCOME_BONUS,
    SHOCK_ASSAULT_BONUS,
    SHOCK_ASSAULT_ID,
    SIGHTING_RESEARCH_BONUS,
    SIGNAL_INTERCEPT_ID,
    SIGNAL_INTERCEPT_RP,
    STIMS_OPERATIVE_COST,
    STIMS_OPERATIVE_DAYS,
    SUPPLY_CHAIN_EXPANSION_BASE,
    SUPPLY_CHAIN_ID,
    SUPPLY_CHAIN_LOGISTICS_CAPACITY,
    SURVEILLANCE_BUMP_COST,
    TECHNOLOGIES_BY_ID,
    TITHES_ID,
    TITHES_INCOME_BONUS,
    TOTAL_INFORMATION_AWARENESS_ID,
    TOTAL_WAR_BONUS,
    TOTAL_WAR_ID,
    Technology,
    WORKER_SURVEILLANCE_ID,
    WORKER_SURVEILLANCE_INCOME_BONUS,
    WORKFORCE_INCOME_BONUS,
)


# Re-exported, not read here. Every consumer of the corp system reaches it through
# this module (`from shadowguy.corp_turn import ...`, 14 files deep), so a name moving
# out to corp_rules/technologies must stay importable from here or the split becomes a
# rename of half the codebase. Most moved names are still used by the functions below
# and re-export themselves; these are the ones whose only readers are elsewhere --
# surveillance.py's detection rolls, rivals.py's AI defense bonus, the corp screens'
# research tree. The redundant `as` is how ruff is told the re-export is deliberate.
from shadowguy.corp_rules import (
    MAX_ACADEMY_TIER as MAX_ACADEMY_TIER,
)
from shadowguy.technologies import (
    DEEP_PROTOCOL_DETECTION_BONUS as DEEP_PROTOCOL_DETECTION_BONUS,
    DEEP_SURVEILLANCE_PROTOCOL_ID as DEEP_SURVEILLANCE_PROTOCOL_ID,
    DETECTION_CHANCE_BONUS as DETECTION_CHANCE_BONUS,
    EXTENDED_SURVEILLANCE_DETECTION as EXTENDED_SURVEILLANCE_DETECTION,
    FORTIFIED_DEFENSES_BONUS as FORTIFIED_DEFENSES_BONUS,
    FORTIFIED_DEFENSES_ID as FORTIFIED_DEFENSES_ID,
    GHOSTWIRE_DETECTION_BONUS as GHOSTWIRE_DETECTION_BONUS,
    ICE_CRACKED_NETWORKS_ID as ICE_CRACKED_NETWORKS_ID,
    INTERCEPTION_CHANCE as INTERCEPTION_CHANCE,
    OPERATION_INTERCEPT_ID as OPERATION_INTERCEPT_ID,
    TECHNOLOGIES as TECHNOLOGIES,
    technology_tree_layout as technology_tree_layout,
)

@dataclass
class Sighting:
    """One Surveillance hit: a known runner (the player, or a runners.RivalRunner)
    that surveillance.py caught inside this corp's own territory on a given day.

    Plain data, the same reason scene.Role holds no job_archetypes.StageType rather than a
    real job_archetypes.StageType field: corp_turn.py stays a leaf (imports corpmap only),
    so surveillance.py -- which does the actual detecting, and needs CorpState in
    turn -- can hold a list of these on CorpState without corp_turn.py importing
    surveillance.py back (that would be a cycle)."""

    kind: Literal["player", "runner"]
    actor_id: str  # "player", or a runners.RivalRunner.id
    territory_id: str
    day: int
    # Populated when Counter-Intelligence is researched: which faction the
    # detected runner belongs to (or "independent" when none). None otherwise.
    runner_faction_id: str | None = None
    # Whether Operation Intercept disrupted this runner's current activity.
    # Only ever True when the tech is researched and the interception roll hit.
    intercepted: bool = False


# Per-faction blog history, capped like Sighting/MAX_SIGHTINGS_LOG.
MAX_FACTION_EVENTS = 15


@dataclass
class FactionEvent:
    """One newsworthy thing a Faction did, for its corp website's blog
    (screens/info_screens.py's CorpWebsiteScreen): a territory claimed, a
    Technology researched, or a district seized off a rival. Populated for every
    Faction, not just the player's own — rivals.resolve_rival_day logs expansion,
    its own simplified per-faction research roll and every successful attack, and
    CorpScreen/ResearchTreeScreen log the player's own corp's
    expand_into/attack_territory/research_technology calls the same way, since
    resolve_rival_day skips whichever faction the player runs.

    "seizure" is deliberately its own kind rather than another "territory": both
    grow a corp's holdings, but only one of them took the ground off somebody, and
    from_faction_id is who. A corp's own site spins it as a win either way — the
    losing corp's site doesn't report it at all, which is the joke."""

    kind: Literal["territory", "technology", "seizure"]
    day: int
    territory_id: str | None = None  # kind == "territory" or "seizure"
    technology_id: str | None = None  # kind == "technology"
    from_faction_id: str | None = None  # kind == "seizure": who they took it from


def log_faction_event(
    events: dict[str, list[FactionEvent]], faction_id: str, event: FactionEvent
) -> None:
    """Prepend `event` to `faction_id`'s log (most-recent-first) and trim it back
    to MAX_FACTION_EVENTS, the same shape CorpState.sightings uses."""
    log = events.setdefault(faction_id, [])
    log.insert(0, event)
    del log[MAX_FACTION_EVENTS:]


def employee_plural(category: EmployeeCategory) -> str:
    """research_assistant -> "research assistants"; scientist/operative have no
    underscore to begin with, so this just adds the s."""
    return f"{category.replace('_', ' ')}s"


def operative_training_cost(corp_state: CorpState) -> int:
    """Base training cost for operatives, discounted by Meridian Biochem's
    Combat Stims or Accelerated Metabolism."""
    if has_technology(corp_state, ACCELERATED_METABOLISM_ID):
        return ACCELERATED_OPERATIVE_COST
    if has_technology(corp_state, COMBAT_STIMS_ID):
        return STIMS_OPERATIVE_COST
    return ACADEMY_TRAINING_COST[EmployeeCategory.OPERATIVE]


def operative_training_days(corp_state: CorpState) -> int:
    """Training duration for operatives, shortened by Meridian Biochem's
    Rapid Deployment or Accelerated Metabolism."""
    if has_technology(corp_state, ACCELERATED_METABOLISM_ID):
        return ACCELERATED_OPERATIVE_DAYS
    if has_technology(corp_state, RAPID_DEPLOYMENT_ID):
        return STIMS_OPERATIVE_DAYS
    return TRAINING_DAYS[EmployeeCategory.OPERATIVE]


def operative_max(_corp_state: CorpState) -> int:
    """The most untasked operatives this corp can hold in its pool at once.
    Garrisoned operatives (on Territory.garrison) don't count against this cap —
    it gates the pool size, not total headcount. Starts at STARTING_OPERATIVE_MAX;
    may be raised by a future technology."""
    return STARTING_OPERATIVE_MAX


def _add_operatives(corp_state: CorpState, count: int) -> int:
    """Add up to `count` operatives to the pool, clipped at operative_max — the
    shared mutator every path that grows the pool from outside it (training
    completing, a tasking operative returning) goes through, so the cap can't be
    missed by a future call site. Returns how many actually landed."""
    room = max(operative_max(corp_state) - corp_state.operatives, 0)
    added = min(count, room)
    corp_state.operatives += added
    return added


@dataclass
class PendingRecruit:
    """A training batch in progress at the Academy: which category is training,
    how many hires it yields (the Academy's tier when training began), and the
    day advance_training drops them into the pool. The Academy runs one batch at
    a time — CorpState.pending_recruit holds at most one."""

    category: EmployeeCategory
    count: int
    ready_day: int


@dataclass
class CorpState:
    """The player's own corp: which Faction they run, its cash/research points/
    scientists/operatives/research_assistants on hand, and how many action points
    remain today (2 per day)."""

    faction_id: str
    cash: int = STARTING_CASH
    research_points: float = 0
    scientists: int = 0
    operatives: int = 0
    research_assistants: int = 0
    action_points: int = DAILY_ACTION_POINTS
    # A training batch in progress at the Academy, or None when idle. The Academy
    # has a single training slot, so train_employees won't start a second batch
    # while this is set; advance_training clears it once its ready_day arrives.
    pending_recruit: PendingRecruit | None = None
    # Technology ids (TECHNOLOGIES_BY_ID) already researched. A set of ids, the
    # same shape Character.owned_programs/discovered_fixers use. Research is
    # permanent — nothing takes a tech back.
    researched: set[str] = field(default_factory=set)
    # Surveillance sightings logged against this corp's own territory,
    # most-recent-first, capped by MAX_SIGHTINGS_LOG (or COUNTERINTEL_SIGHTINGS_LOG
    # when Counter-Intelligence is researched). Stays empty
    # until surveillance.resolve_surveillance_day actually catches someone —
    # corp_turn.py never appends to this itself.
    sightings: list[Sighting] = field(default_factory=list)
    # Operatives currently out on a task (tail_runner / gather_intel). They leave
    # the pool when dispatched and return on the next day tick — see
    # return_tasking_operatives. Doesn't count against operative_max, so a corp at
    # cap can still send them out (they'll come back into a capped pool and be
    # lost above it — dispatch them wisely).
    tasking_operatives: int = 0


def has_technology(corp_state: CorpState, technology_id: str) -> bool:
    return technology_id in corp_state.researched


def prereqs_met(corp_state: CorpState, technology: Technology) -> bool:
    """Whether every one of a Technology's prereqs is already researched — True
    for a root technology (empty prereqs) for free, since all() of nothing is
    True."""
    return all(has_technology(corp_state, prereq) for prereq in technology.prereqs)


def research_technology(corp_state: CorpState, technology_id: str) -> bool:
    """Spend research points to unlock a Technology permanently. Fails closed (no
    charge, no mutation) if it's already researched, its prereqs aren't all
    researched yet, the corp can't afford it, or the technology is faction-gated
    to a different faction than the one the player is running.

    Deliberately NOT on the action_points slot: RP is its own pacing gate
    (10 RP is ~10 days of research at the base rate), and double-gating a
    purchase behind action points would make researching compete
    with expanding for no design reason. Same call the cash-gated territory
    bumps below make.
    """
    technology = TECHNOLOGIES_BY_ID[technology_id]
    if has_technology(corp_state, technology_id) or not prereqs_met(corp_state, technology):
        return False
    if technology.faction_id is not None and technology.faction_id != corp_state.faction_id:
        return False
    if technology.cost > corp_state.research_points:
        return False
    corp_state.research_points -= technology.cost
    corp_state.researched.add(technology_id)
    return True


def collect_income(corp_state: CorpState, corp_map: CorpMap) -> int:
    """Daily income from every territory the player's faction holds, **net of
    TERRITORY_UPKEEP per district and of logistics_strain**, plus
    whichever of the surveillance chain's per-territory bonuses are researched
    (WORKER_SURVEILLANCE_INCOME_BONUS, then PANOPTICON_GRID_INCOME_BONUS, then
    SHADOW_ECONOMY_INCOME_BONUS — summed, not replaced, unlike the Brains
    chain's research rates) — per territory, not once, so each tech keeps
    paying as the corp expands. Optimized Workforce (Prometheus) adds another
    per-territory bonus."""
    owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
    bonus = 0
    if has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        bonus += WORKER_SURVEILLANCE_INCOME_BONUS
    if has_technology(corp_state, PANOPTICON_GRID_ID):
        bonus += PANOPTICON_GRID_INCOME_BONUS
    if has_technology(corp_state, SHADOW_ECONOMY_ID):
        bonus += SHADOW_ECONOMY_INCOME_BONUS
    if has_technology(corp_state, MARKET_MONOPOLY_ID):
        bonus += MARKET_MONOPOLY_INCOME_BONUS
    elif has_technology(corp_state, OPTIMIZED_WORKFORCE_ID):
        bonus += WORKFORCE_INCOME_BONUS
    if has_technology(corp_state, TITHES_ID):
        bonus += TITHES_INCOME_BONUS
    gross = sum(TERRITORY_INCOME_BASE + bonus + TERRITORY_INCOME_PER_VALUE * t.value for t in owned)
    return gross - TERRITORY_UPKEEP * len(owned) - logistics_strain(corp_state, corp_map)


def logistics_capacity(corp_state: CorpState, corp_map: CorpMap) -> int:
    """How many districts this corp's supply lines support without straining:
    LOGISTICS_BASE_CAPACITY, plus one per LOGISTICS_DEVELOPMENT_PER_SLOT points of
    Development summed across the ground it holds, plus the logistics
    technologies.

    Development is counted over held districts only — neutral ground's Development
    belongs to nobody, and a district's contribution goes with it when a rival
    takes it."""
    development = sum(
        t.modifiers.get(TerritoryModifier.DEVELOPMENT, 0)
        for t in _owned_territories(corp_state, corp_map)
    )
    capacity = LOGISTICS_BASE_CAPACITY + development // LOGISTICS_DEVELOPMENT_PER_SLOT
    if has_technology(corp_state, LOGISTICS_NETWORK_ID):
        capacity += LOGISTICS_NETWORK_CAPACITY
    if has_technology(corp_state, SUPPLY_CHAIN_ID):
        capacity += SUPPLY_CHAIN_LOGISTICS_CAPACITY
    return capacity


def logistics_strain(corp_state: CorpState, corp_map: CorpMap) -> int:
    """Daily cash the corp bleeds running more districts than it can supply — 0
    while it's inside logistics_capacity, and triangular in the overage past it
    (the nth district over capacity costs n × LOGISTICS_STRAIN_COST). Charged in
    collect_income alongside TERRITORY_UPKEEP."""
    over = len(_owned_territories(corp_state, corp_map)) - logistics_capacity(corp_state, corp_map)
    if over <= 0:
        return 0
    return LOGISTICS_STRAIN_COST * over * (over + 1) // 2


def owned_research_facilities(corp_state: CorpState, corp_map: CorpMap) -> list[Location]:
    """Every research facility the corp holds, best first — highest research_rate,
    then most assistant seats, then id to break ties deterministically.

    A corp used to be able to hold exactly one (seeded by
    corpmap.add_research_facility; expand_into claims only neutral ground,
    which never carries one). attack_territory broke that: capture_territory
    hands over every Location standing on the district, so taking a rival's home
    bloc takes their labs with it. That is the case DESIGN.md flagged in advance —
    "if corps ever hold more than one facility, revisit collect_research's fill
    order" — and this ordering is that fill order restored.
    """
    return sorted(
        (
            location
            for territory in corp_map.territories.values()
            if territory.owner == corp_state.faction_id
            for location in territory.locations
            if location.kind == LocationKind.RESEARCH_FACILITY
        ),
        key=lambda f: (-research_rate(corp_state, f), -assistant_capacity(f, corp_state), f.id),
    )


def owned_research_facility(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    """The corp's *primary* research facility — the best one it holds, or None.

    This is where build_lab/build_efficiency_upgrade land, and it's a principled
    choice rather than an arbitrary one: collect_research fills scientists into
    this same facility first, so concentrating both capacity and efficiency on it
    is exactly what maximizes output. Upgrading anything else would be seating
    scientists at a worse rate while the best facility sat half empty.
    """
    facilities = owned_research_facilities(corp_state, corp_map)
    return facilities[0] if facilities else None


def lab_capacity(facility: Location, corp_state: CorpState | None = None) -> int:
    """How many scientists this facility can put to work: a free base seat plus
    one more per lab built there, plus one additional when Research Expansion is
    researched."""
    bonus = RESEARCH_EXPANSION_BONUS if corp_state is not None and has_technology(corp_state, RESEARCH_EXPANSION_ID) else 0
    return BASE_LAB_CAPACITY + (facility.labs_built or 0) + bonus


def next_lab_cost(facility: Location) -> int | None:
    """Cost of this facility's next lab, or None once MAX_LABS_BUILT is reached."""
    labs_built = facility.labs_built or 0
    if labs_built >= MAX_LABS_BUILT:
        return None
    return LAB_UPGRADE_COSTS[labs_built]


def scientist_base_rate(corp_state: CorpState) -> float:
    """RP/day one working scientist adds before any facility efficiency upgrade —
    RESEARCH_PER_SCIENTIST, or the best-researched Brains tier's rate. The chain
    replaces rather than stacks (see the Brains constants above), so this picks
    the highest tier held rather than summing them."""
    if has_technology(corp_state, COGNITIVE_UPLINK_ID):
        return COGNITIVE_UPLINK_RESEARCH_PER_SCIENTIST
    if has_technology(corp_state, BRAINS_3_ID):
        return BRAINS_3_RESEARCH_PER_SCIENTIST
    if has_technology(corp_state, BRAINS_2_ID):
        return BRAINS_2_RESEARCH_PER_SCIENTIST
    return RESEARCH_PER_SCIENTIST


def assistant_rate(corp_state: CorpState) -> float:
    """RP/day one working research assistant adds. Flat regardless of facility —
    efficiency upgrades boost scientists only — but the Brains chain raises it,
    same highest-tier-wins rule as scientist_base_rate."""
    if has_technology(corp_state, COGNITIVE_UPLINK_ID):
        return COGNITIVE_UPLINK_RESEARCH_PER_ASSISTANT
    if has_technology(corp_state, BRAINS_3_ID):
        return BRAINS_3_RESEARCH_PER_ASSISTANT
    if has_technology(corp_state, BRAINS_2_ID):
        return BRAINS_2_RESEARCH_PER_ASSISTANT
    return RESEARCH_PER_ASSISTANT


def research_rate(corp_state: CorpState, facility: Location) -> float:
    """RP/day one working scientist adds at this facility: the base rate (which
    Brains 2 raises) plus any efficiency upgrades built there. Takes corp_state
    because the rate is now a property of the corp's tech as well as the
    building — the two stack."""
    return scientist_base_rate(corp_state) + (facility.efficiency_upgrades or 0)


def next_efficiency_cost(facility: Location) -> int | None:
    """Cost of this facility's next efficiency upgrade, or None once
    MAX_EFFICIENCY_UPGRADES is reached."""
    efficiency_upgrades = facility.efficiency_upgrades or 0
    if efficiency_upgrades >= MAX_EFFICIENCY_UPGRADES:
        return None
    return EFFICIENCY_UPGRADE_COSTS[efficiency_upgrades]


def assistant_capacity(facility: Location, corp_state: CorpState | None = None) -> int:
    """How many research assistants this facility can put to work: each lab
    seats RESEARCH_ASSISTANTS_PER_LAB of them, same lab count as lab_capacity."""
    return RESEARCH_ASSISTANTS_PER_LAB * lab_capacity(facility, corp_state)


def collect_research(corp_state: CorpState, corp_map: CorpMap) -> float:
    """RP/day from the corp's research facility: its tier directly (1 RP at tier
    1), plus research_rate() for each scientist actually working it, plus
    assistant_rate() for each research assistant actually working it. Both
    per-head rates are raised by the Brains 2 technology.

    "Actually working" is the whole mechanic: lab_capacity/assistant_capacity
    cap how many of each count, so employees trained beyond the seats built for
    them produce nothing — headcount (train_employees) and capacity (build_lab)
    are two separate purchases.

    A corp holding more than one facility (only reachable by taking a rival's
    ground — see owned_research_facilities) fills them best-first: every scientist
    sits at the highest-rate facility with a seat free before any of them sits at
    a worse one. Each facility's own research_tier counts whether or not anyone is
    staffing it, exactly as it did in the single-facility case.

    Total Information Awareness (when researched) adds SIGHTING_RESEARCH_BONUS RP
    per sighting currently in the log — each detection feeds the research machine."""
    scientists_left = corp_state.scientists
    assistants_left = corp_state.research_assistants
    total = 0.0
    for facility in owned_research_facilities(corp_state, corp_map):
        total += facility.research_tier or 0
        working = min(scientists_left, lab_capacity(facility, corp_state))
        scientists_left -= working
        total += working * research_rate(corp_state, facility)
        aides = min(assistants_left, assistant_capacity(facility, corp_state))
        assistants_left -= aides
        total += aides * assistant_rate(corp_state)
    if has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        total += len(corp_state.sightings) * SIGHTING_RESEARCH_BONUS
    if has_technology(corp_state, SIGNAL_INTERCEPT_ID):
        total += len(corp_state.sightings) * SIGNAL_INTERCEPT_RP
    return total


def _owned_territories(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Sorted by id, so every list built off this renders in a stable order."""
    return sorted(
        (t for t in corp_map.territories.values() if t.owner == corp_state.faction_id),
        key=lambda t: t.id,
    )


def effective_surveillance_max(corp_state: CorpState) -> int:
    """Highest Surveillance level this corp can reach. Normally MODIFIER_MAX (5);
    Deep Surveillance raises it to EXTENDED_SURVEILLANCE_MAX (6)."""
    if has_technology(corp_state, DEEP_SURVEILLANCE_ID):
        return EXTENDED_SURVEILLANCE_MAX
    return MODIFIER_MAX


def surveillance_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds whose Surveillance isn't already at its effective
    maximum (MODIFIER_MAX, or EXTENDED_SURVEILLANCE_MAX with Deep Surveillance).
    Empty until Worker Surveillance is researched — the tech is what grants the
    ability at all, not just a discount on it."""
    if not has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        return []
    ceiling = effective_surveillance_max(corp_state)
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) < ceiling
    ]


def surveillance_bump_cost(corp_state: CorpState, territory: Territory) -> int:
    """Cost to raise this territory's Surveillance by 1. Counter-Intelligence
    drops the base cost; the extended level (5→6, Deep Surveillance required)
    costs EXTENDED_SURVEILLANCE_COST regardless."""
    level = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
    if level >= MODIFIER_MAX:
        return EXTENDED_SURVEILLANCE_COST
    if has_technology(corp_state, COUNTER_INTELLIGENCE_ID):
        return COUNTERINTEL_SURVEILLANCE_COST
    return SURVEILLANCE_BUMP_COST


def raise_surveillance(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay the level-appropriate Surveillance bump cost to raise one held
    district's Surveillance by 1.

    Repeatable within a day (cash is the only gate), so unlike
    expand_into/train_employees this never touches action_points. Fails
    closed if the tech isn't researched, the district isn't a legal target
    (not held, or already at its effective max), or the corp can't afford it."""
    if territory_id not in {t.id for t in surveillance_targets(corp_state, corp_map)}:
        return False
    territory = corp_map.territories[territory_id]
    cost = surveillance_bump_cost(corp_state, territory)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    territory.modifiers[TerritoryModifier.SURVEILLANCE] = (
        territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) + 1
    )
    return True


def effective_security_max(corp_state: CorpState) -> int:
    """Highest Security level this corp can reach. Normally MODIFIER_MAX (5);
    Martial Law raises it to EXTENDED_SECURITY_MAX (6). Mirrors
    effective_surveillance_max."""
    if has_technology(corp_state, MARTIAL_LAW_ID):
        return EXTENDED_SECURITY_MAX
    return MODIFIER_MAX


def security_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds whose Security isn't already at its effective
    maximum. Empty until Private Security Force is researched — the tech grants
    the ability at all, same as Worker Surveillance does for Surveillance."""
    if not has_technology(corp_state, PRIVATE_SECURITY_ID):
        return []
    ceiling = effective_security_max(corp_state)
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.SECURITY, 0) < ceiling
    ]


def security_bump_cost(corp_state: CorpState, territory: Territory) -> int:
    """Cost to raise this territory's Security by 1. Rapid Response Teams drops
    the base cost; the extended level (5→6, Martial Law required) costs
    EXTENDED_SECURITY_COST regardless."""
    level = territory.modifiers.get(TerritoryModifier.SECURITY, 0)
    if level >= MODIFIER_MAX:
        return EXTENDED_SECURITY_COST
    if has_technology(corp_state, RAPID_RESPONSE_ID):
        return RAPID_RESPONSE_SECURITY_COST
    return SECURITY_BUMP_COST


def raise_security(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay the level-appropriate Security bump cost to raise one held district's
    Security by 1 — the corp's only way to move Security after generation, and so
    the only way ground seeded below DEVELOPMENT_MIN_SECURITY ever becomes
    developable.

    Repeatable within a day (cash is the only gate), so like raise_surveillance
    and raise_development this never touches action_points. Fails closed if the
    tech isn't researched, the district isn't a legal target, or the corp can't
    afford it."""
    if territory_id not in {t.id for t in security_targets(corp_state, corp_map)}:
        return False
    territory = corp_map.territories[territory_id]
    cost = security_bump_cost(corp_state, territory)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    territory.modifiers[TerritoryModifier.SECURITY] = (
        territory.modifiers.get(TerritoryModifier.SECURITY, 0) + 1
    )
    return True


def sightings_log_cap(corp_state: CorpState) -> int:
    """How many entries the sightings log holds. Counter-Intelligence doubles it."""
    if has_technology(corp_state, COUNTER_INTELLIGENCE_ID):
        return COUNTERINTEL_SIGHTINGS_LOG
    return MAX_SIGHTINGS_LOG


def investigate_sighting_targets(
    corp_state: CorpState, corp_map: CorpMap,
) -> list[Sighting]:
    """Sightings that are eligible for investigation. Only available after Total
    Information Awareness is researched, and only when action_points are still
    available (investigation costs 1 AP). Each sighting can be investigated
    exactly once — spotted runners don't exist after the event."""
    if corp_state.action_points < AP_COST:
        return []
    if not has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        return []
    if corp_state.cash < INVESTIGATION_COST:
        return []
    return list(corp_state.sightings)


def investigate_sighting(
    corp_state: CorpState, sighting: Sighting, rng: random.Random,
) -> bool:
    """Pay INVESTIGATION_COST, deduct 1 AP, and gather
    actionable intel on a sighted runner. The intel itself is surfaced by the
    caller (CorpScreen) — this function only deducts the cost and marks the
    slot. Fails closed if the tech isn't researched, the day's move is already
    spent, or the corp can't afford it.

    Returns True on success so the caller can display the intel. The intel is
    deterministic (it's just reading fields the sighting already carries), not a
    random roll — the cash + daily-move cost is the gate."""
    if corp_state.action_points < AP_COST:
        return False
    if not has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        return False
    if INVESTIGATION_COST > corp_state.cash:
        return False
    corp_state.cash -= INVESTIGATION_COST
    corp_state.action_points -= AP_COST
    # The intel itself — what the caller displays — is just what the sighting
    # already carries (runner id, faction, territory, day, intercepted flag).
    # The cash buys the story beat; nothing is rolled.
    corp_state.sightings = [s for s in corp_state.sightings if s is not sighting]
    return True


def development_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds that are ready to be built up: Development below
    MODIFIER_MAX, and both Security and Surveillance already at their thresholds.
    Needs no technology — a district seeded well enough can be developed from day
    one; Worker Surveillance is simply how a district that *isn't* gets there."""
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.DEVELOPMENT, 0) < MODIFIER_MAX
        and t.modifiers.get(TerritoryModifier.SECURITY, 0) >= DEVELOPMENT_MIN_SECURITY
        and t.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) >= DEVELOPMENT_MIN_SURVEILLANCE
    ]


def raise_development(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay DEVELOPMENT_BUMP_COST to raise one held district's Development by 1,
    once it's policed and watched enough to justify the capital (see
    development_targets). Same cash-gated, repeatable, no-daily-slot shape as
    raise_surveillance; fails closed on an illegal target or short cash.

    This is the first thing in Corp mode with a *runner-side* consequence:
    Development prices lodging and safehouses (corpmap.lodging_cost /
    safehouse_price), so building a block up makes it dearer to sleep in.
    """
    if territory_id not in {t.id for t in development_targets(corp_state, corp_map)}:
        return False
    if DEVELOPMENT_BUMP_COST > corp_state.cash:
        return False
    territory = corp_map.territories[territory_id]
    corp_state.cash -= DEVELOPMENT_BUMP_COST
    territory.modifiers[TerritoryModifier.DEVELOPMENT] = (
        territory.modifiers.get(TerritoryModifier.DEVELOPMENT, 0) + 1
    )
    return True


# --- Free actions: 1 AP, 0eb --------------------------------------------------


def fundraise_amount(corp_state: CorpState, corp_map: CorpMap) -> int:
    """What one round of emergency fundraising would raise: FUNDRAISE_PER_TERRITORY
    per district held (or CONSOLIDATED_FUNDRAISE_PER_TERRITORY when Consolidated
    Holdings is researched). 0 for a corp holding nothing (which is a lost run
    anyway — see corp_defeated).

    **Counted over supplied districts, not held ones** — capped at
    logistics_capacity. Without the cap this scales linearly with exactly the
    district count logistics_strain punishes, and cancels it: a corp 40 districts
    past its capacity was raising more in one free action than the whole day's
    strain, every day, while still expanding. A corp can only shake down ground
    its own supply lines actually reach."""
    per_territory = CONSOLIDATED_FUNDRAISE_PER_TERRITORY if has_technology(corp_state, CONSOLIDATED_HOLDINGS_ID) else FUNDRAISE_PER_TERRITORY
    supplied = min(
        len(_owned_territories(corp_state, corp_map)),
        logistics_capacity(corp_state, corp_map),
    )
    return per_territory * supplied


def can_fundraise(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Whether fundraising is on the table at all — only while the corp is under
    FUNDRAISE_CASH_CEILING and still holds ground. Separate from `fundraise` so the
    Corp screen can leave the row off entirely for a solvent corp rather than
    showing one that always refuses."""
    return corp_state.cash < FUNDRAISE_CASH_CEILING and fundraise_amount(corp_state, corp_map) > 0


def fundraise(corp_state: CorpState, corp_map: CorpMap) -> int | None:
    """Spend the day's action point to raise fundraise_amount() in cash, costing
    nothing. The way out of a corp that's broke, holding ground, and therefore
    locked out of every cash-gated move on the board.

    Returns the eb raised, or None (nothing mutated, no AP spent) if the corp is
    out of AP or isn't broke enough to qualify."""
    if corp_state.action_points < AP_COST or not can_fundraise(corp_state, corp_map):
        return None
    amount = fundraise_amount(corp_state, corp_map)
    corp_state.cash += amount
    corp_state.action_points -= AP_COST
    return amount


def survey_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts a survey can read: everything bordering the corp's own ground that
    the corp doesn't hold, neutral and gang turf included. Wider than
    expansion_candidates/attack_candidates (which each list only the ground their
    own move can legally take) because looking at a block commits nothing."""
    if corp_state.action_points < AP_COST:
        return []
    owned = _owned_territories(corp_state, corp_map)
    seen = {
        conn_id
        for territory in owned
        for conn_id in territory.connections
        if corp_map.territories[conn_id].owner != corp_state.faction_id
    }
    return sorted((corp_map.territories[tid] for tid in seen), key=lambda t: t.id)


def survey(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> Territory | None:
    """Spend the day's action point to read a bordering district's garrison,
    Security and value from your own side of the line — gather_intel's job without
    the operative or the 50% roll, and correspondingly thinner (the caller reports
    no locations list). Returns the surveyed Territory for the caller to describe,
    or None on no AP or an illegal target."""
    if territory_id not in {t.id for t in survey_targets(corp_state, corp_map)}:
        return None
    corp_state.action_points -= AP_COST
    return corp_map.territories[territory_id]


def expansion_cost(
    territory: Territory, corp_state: CorpState | None, corp_map: CorpMap | None
) -> int:
    """What claiming this district costs, scaled by how much ground the corp
    already holds (EXPANSION_SPRAWL_DIVISOR). `corp_map` is what the sprawl
    multiplier is counted from; passing None for either prices the district as if
    the corp held nothing, which is only right for a preview that has no corp
    behind it.

    Both parameters are **required, not defaulted**: the sprawl multiplier is the
    whole point of this function at any real call site, and a defaulted-away
    corp_map would let a caller quietly display a price the corp will never be
    charged. A preview passes `None, None` and says so."""
    base = EXPANSION_COST_BASE
    if corp_state is not None and has_technology(corp_state, SUPPLY_CHAIN_ID):
        base = SUPPLY_CHAIN_EXPANSION_BASE
    price = base + EXPANSION_COST_PER_VALUE * territory.value
    if corp_state is None or corp_map is None:
        return price
    held = len(_owned_territories(corp_state, corp_map))
    return int(price * (1 + held / EXPANSION_SPRAWL_DIVISOR))


def expand_into(corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random) -> bool:
    """Spend cash to claim a bordering neutral territory. Fails closed (no
    mutation, no charge) if the corp's already made its move today, the target
    isn't a legal candidate for this faction right now, or it can't afford it."""
    if corp_state.action_points < AP_COST:
        return False
    if territory_id not in expansion_candidates(corp_map, corp_state.faction_id):
        return False
    territory = corp_map.territories[territory_id]
    cost = expansion_cost(territory, corp_state, corp_map)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    claim_territory(territory, corp_state.faction_id, rng)
    corp_state.action_points -= AP_COST
    return True


def defense_strength(territory: Territory, corp_state: CorpState | None = None) -> int:
    """What an attacker has to beat to take this district: the operatives
    stationed there plus its Security modifier.

    When the corp has researched Hardened Garrison, each garrisoned operative
    counts double (garrison × 2 + Security).

    Note the asymmetry with garrison: Security is bought once and keeps defending
    forever, while a garrison is spent by the fight that uses it, so a
    well-policed district is the durable half of a defense and troops are the
    half you have to keep replacing.
    """
    garrison = territory.garrison
    if corp_state is not None and has_technology(corp_state, HARDENED_GARRISON_ID):
        garrison *= HARDENED_GARRISON_MULTIPLIER
    return garrison + territory.modifiers.get(TerritoryModifier.SECURITY, 0)


def deployable_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp could station operatives on: simply everything it holds.
    Separate from _owned_territories only so the Corp screen reads in the same
    shape as surveillance_targets/development_targets."""
    return _owned_territories(corp_state, corp_map)


def deploy_operatives(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, count: int
) -> bool:
    """Station `count` of the corp's untasked operatives on a district it holds,
    moving them from CorpState.operatives onto Territory.garrison. Spends the
    day's action point budget, same slot as expand_into/train_employees — a redeploy
    is a real logistical decision, not a free click.

    One-way on purpose: there is no recall. Operatives committed to holding ground
    are committed, which is what stops a single stack from shuttling around the
    map defending everything in turn.

    Fails closed (no AP consumed, nothing mutated) if the corp has no AP,
    `count` isn't positive, it hasn't got that many operatives spare, or the
    district isn't one it holds.
    """
    if corp_state.action_points < AP_COST or count <= 0 or count > corp_state.operatives:
        return False
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner != corp_state.faction_id:
        return False
    corp_state.operatives -= count
    territory.garrison += count
    corp_state.action_points -= AP_COST
    return True


def return_tasking_operatives(corp_state: CorpState) -> int:
    """Bring operatives back from their task (tail_runner / gather_intel) at the
    start of a new day. Returns them to the pool, clipped to operative_max — any
    above the cap are lost (the corp can only field so many at once). Returns how
    many were actually returned (capped count), so the caller can report it."""
    if corp_state.tasking_operatives <= 0:
        return 0
    returning = corp_state.tasking_operatives
    corp_state.tasking_operatives = 0
    return _add_operatives(corp_state, returning)


def _dispatch_operative(corp_state: CorpState, rng: random.Random) -> bool:
    """Spend one operative from the pool on a tasking action: marks the day's
    move used and rolls the shared 50% success chance. Shared by
    tail_runner/gather_intel/sabotage, which differ only in target validation
    and in whether a failure still returns the operative to tasking_operatives
    (tail_runner/gather_intel always do; sabotage only on success) — that part
    stays with each caller."""
    corp_state.operatives -= 1
    corp_state.action_points -= AP_COST
    return rng.random() < 0.5


# --- Operative tasking: tail a sighted runner ---------------------------------


def tail_runner_targets(corp_state: CorpState) -> list[Sighting]:
    """Sightings of independent runners eligible for a tail. Only while the day's
    move is free, an operative is spare in the pool, and the sighting is of a
    runner (not the player)."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return []
    return [s for s in corp_state.sightings if s.kind == "runner"]


def tail_runner(corp_state: CorpState, sighting: Sighting, rng: random.Random) -> bool:
    """Dispatch one operative to tail a sighted runner. The operative leaves the
    pool immediately (to tasking_operatives), the day's move is spent, and a
    detection-style roll determines intel quality:

    - Success: the caller surfaces the intel (runner faction, last known activity,
      territory of origin). The sighting stays in the log — an investigated
      sighting is worth more RP with Total Information Awareness.
    - Failure: no intel; the operative simply returns next day via
      return_tasking_operatives.

    Returns True on success (a hit), so the caller can display the intel. Fails
    closed if the day's move is spent, no operatives are spare, or no runner
    sightings exist."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return False
    if sighting.kind != "runner":
        return False
    success = _dispatch_operative(corp_state, rng)
    corp_state.tasking_operatives += 1
    return success


# --- Operative tasking: gather intel on a territory ---------------------------


def intel_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Rival or neutral territories an operative can be sent to gather intel on.
    Only while the day's move is free and an operative is spare."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return []
    return [
        t for t in corp_map.territories.values()
        if t.owner != corp_state.faction_id
    ]


def gather_intel(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random,
) -> bool:
    """Dispatch one operative to scout a rival or neutral territory. The operative
    leaves the pool for tasking, the day's move is spent, and a roll determines
    intel quality:

    - Success (50%): the caller surfaces everything visible about the territory
      (owner, garrison count, modifiers, locations/buildings).
    - Failure: no intel; the operative returns next day.

    Returns True on success. Fails closed if the day's move is spent, no
    operatives are spare, or the territory is the corp's own."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return False
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner == corp_state.faction_id:
        return False
    success = _dispatch_operative(corp_state, rng)
    corp_state.tasking_operatives += 1
    return success


# --- Operative tasking: sabotage a rival territory ----------------------------


def sabotage_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Rival-held territories an operative can be sent to sabotage. Only while the
    day's move is free and an operative is spare. Neutral ground and the corp's own
    territory are excluded — sabotage is a hostile act."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return []
    return [
        t for t in corp_map.territories.values()
        if t.owner not in (corp_state.faction_id, "neutral")
    ]


def sabotage(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random,
) -> str | None:
    """Dispatch one operative to sabotage a rival-held territory. The operative
    leaves the pool for tasking (returns next day via return_tasking_operatives),
    the day's move is spent, and a roll determines the outcome:

    - Success (50%): the territory's Security drops by 1 (minimum 0). The
      operative returns alive. Returns the target territory's name for the
      caller to report.
    - Failure: the operative is captured or killed — they do NOT return (lost
      from tasking). Returns None.

    Fails closed (no move consumed, nothing mutated) if the day's move is spent,
    no operatives are spare, or the target isn't a rival-held territory."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return None
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner in (corp_state.faction_id, "neutral"):
        return None
    if _dispatch_operative(corp_state, rng):
        # Success: the operative did the job and reports back.
        territory.modifiers[TerritoryModifier.SECURITY] = max(
            0, territory.modifiers.get(TerritoryModifier.SECURITY, 0) - 1,
        )
        corp_state.tasking_operatives += 1
        return territory.name
    else:
        # Failure: operative lost — no tasking return.
        return None


@dataclass
class AttackResult:
    """What one resolved attack did, for the caller to report. Returned by
    attack_territory (and built by rivals.py for the AI's own attacks) rather than
    notified from inside, the same split security.NightResult keeps: the resolver
    returns data, the screen writes the prose."""

    territory_id: str
    defender_id: str  # the faction that held it going in
    committed: int
    attack_power: int
    defense_power: int
    captured: bool
    attacker_losses: int
    defender_losses: int


def resolve_attack(
    territory: Territory,
    attacker_id: str,
    committed: int,
    rng: random.Random,
    *,
    attack_bonus: int = 0,
    defense_bonus: int = 0,
    defender_corp_state: CorpState | None = None,
) -> AttackResult:
    """The contest itself, taking no corp state for the attacker — so rivals.py's
    AI factions (which have no CorpState) settle an attack through exactly the
    same dice the player does, and a test can drive it without building a corp.

    Both sides roll one CONTEST_DIE on top of their strength; the attacker needs
    to strictly exceed the defender, so a tie holds the ground. Losses land the
    same way either way — the attacker bleeds one operative per point of defense
    they had to grind through (capped at what they brought), the defender loses
    their whole garrison if the district falls and one per attacker if it doesn't.

    attack_bonus is added to the attacker's roll — the player's Shock Assault
    technology feeds it; the AI never does. defense_bonus is added to the
    defender's roll — Fortified Defenses technology feeds it when the defender
    has researched it.

    defender_corp_state feeds defense_strength's Hardened Garrison bonus. It's
    the *defender's* state, not the attacker's — only ever non-None when the
    player is the one holding the territory, since AI factions carry no
    CorpState of their own to research the tech into.

    Mutates `territory` (ownership and garrison) and returns the record.
    """
    defender_id = territory.owner
    defense = defense_strength(territory, defender_corp_state)
    attack_power = committed + rng.randint(1, CONTEST_DIE) + attack_bonus
    defense_power = defense + rng.randint(1, CONTEST_DIE) + defense_bonus
    captured = attack_power > defense_power

    attacker_losses = min(committed, defense)
    survivors = committed - attacker_losses
    if captured:
        defender_losses = territory.garrison
        capture_territory(territory, attacker_id)
        # Whoever walked out of the fight holds the ground they took: survivors
        # become the new garrison rather than going back in the pool. Taking a
        # district and leaving it empty would just invite it straight back.
        territory.garrison = survivors
    else:
        defender_losses = min(territory.garrison, committed)
        territory.garrison -= defender_losses
    return AttackResult(
        territory_id=territory.id,
        defender_id=defender_id,
        committed=committed,
        attack_power=attack_power,
        defense_power=defense_power,
        captured=captured,
        attacker_losses=attacker_losses,
        defender_losses=defender_losses,
    )


def attack_territory(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, committed: int, rng: random.Random
) -> AttackResult | None:
    """Throw `committed` operatives at a rival-held district bordering your own
    ground. Costs 1 AP, same slot as expand_into.

    Costs no cash — operatives *are* the cost, and they were paid for at the
    Academy.

    Returns None (no AP consumed, nothing mutated) if the corp has no AP,
    the target isn't a legal attack candidate right now, or it can't field
    that many operatives. On a repel the survivors come home to the pool; on a
    capture they stay as the new garrison (see resolve_attack).
    """
    if corp_state.action_points < AP_COST:
        return None
    if committed < MIN_ATTACK_FORCE or committed > corp_state.operatives:
        return None
    if territory_id not in attack_candidates(corp_map, corp_state.faction_id):
        return None
    territory = corp_map.territories[territory_id]
    corp_state.operatives -= committed
    bonus = 0
    if has_technology(corp_state, TOTAL_WAR_ID):
        bonus = TOTAL_WAR_BONUS
    elif has_technology(corp_state, SHOCK_ASSAULT_ID):
        bonus = SHOCK_ASSAULT_BONUS
    elif has_technology(corp_state, CRUSADE_ID):
        bonus = CRUSADE_BONUS
    result = resolve_attack(territory, corp_state.faction_id, committed, rng, attack_bonus=bonus)
    if not result.captured:
        corp_state.operatives += committed - result.attacker_losses
    corp_state.action_points -= AP_COST
    return result


@dataclass
class CorpDay:
    """What one day boundary did to the corp, for the caller to report.

    advance_corp_day applies all of it; this is only the record of how much, so
    app.py can say "+410eb, +3rp" without re-deriving any of it. Transient — never
    saved, so adding a field here needs no SAVE_VERSION bump."""

    income: int
    research: float
    trained: PendingRecruit | None
    returned_operatives: int


def advance_corp_day(corp_state: CorpState, corp_map: CorpMap, day: int) -> CorpDay:
    """The corp's own day boundary: collect the day's income and research, refill the
    action points, and land whatever the Academy and the tasking pool finished
    overnight.

    Lives here rather than inline in app._apply_day_tick, where it used to, because
    every line of it is a corp rule — the app's job is the *clock*, not what a day
    is worth. Deliberately not the whole of the corp's tick: surveillance
    (surveillance.resolve_surveillance_day) needs the runner, the rival roster and an
    rng, and the rivals have to have moved first, so the caller still sequences that.

    Call it only after corp_defeated has been checked: a corp with no ground left
    collects nothing, and the run is over regardless."""
    corp_state.cash += (income := collect_income(corp_state, corp_map))
    corp_state.research_points += (research := collect_research(corp_state, corp_map))
    corp_state.action_points = DAILY_ACTION_POINTS
    return CorpDay(
        income=income,
        research=research,
        trained=advance_training(corp_state, day),
        returned_operatives=return_tasking_operatives(corp_state),
    )


def corp_defeated(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Whether the corp has been broken up: it holds no territory at all.

    The one loss condition for Corp mode, checked on the day tick (app.py) after
    the rivals have had their turn. Territory is the right thing to key on rather
    than cash or headcount — every other corp system (income, research, training,
    attacking) is downstream of holding ground, so a corp with none of it has no
    move left to make.
    """
    return not any(t.owner == corp_state.faction_id for t in corp_map.territories.values())


def owned_academy(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    for territory in corp_map.territories.values():
        if territory.owner != corp_state.faction_id:
            continue
        for location in territory.locations:
            if location.kind == LocationKind.ACADEMY:
                return location
    return None


def train_employees(
    corp_state: CorpState, corp_map: CorpMap, category: EmployeeCategory, day: int
) -> bool:
    """Start one training batch at the corp's Academy: charge cash now and queue
    that many scientists, operatives or research assistants (whichever `category`
    picks, Academy-tier many) to land TRAINING_DAYS[category] days later, when
    advance_training completes it. Shares expand_into's once-a-day slot and the
    Academy's single training slot — fails closed if the corp's already made its
    move today, a batch is already training, holds no Academy (a rival can capture
    the one it was seeded — see build_academy), can't afford it, or (for operatives)
    the pool is already at or above the cap."""
    if corp_state.action_points < AP_COST or corp_state.pending_recruit is not None:
        return False
    academy = owned_academy(corp_state, corp_map)
    if academy is None:
        return False
    count = academy.academy_tier or 0
    if category is EmployeeCategory.OPERATIVE and corp_state.operatives + count > operative_max(corp_state):
        return False
    cost = (
        operative_training_cost(corp_state)
        if category is EmployeeCategory.OPERATIVE
        else ACADEMY_TRAINING_COST[category]
    )
    days = (
        operative_training_days(corp_state)
        if category is EmployeeCategory.OPERATIVE
        else TRAINING_DAYS[category]
    )
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    corp_state.pending_recruit = PendingRecruit(
        category=category,
        count=count,
        ready_day=day + days,
    )
    corp_state.action_points -= AP_COST
    return True


def advance_training(corp_state: CorpState, day: int) -> PendingRecruit | None:
    """Complete the Academy's training batch if `day` has reached its ready_day:
    add the trained hires to the matching pool, clear the slot, and return the
    finished batch for the caller to announce. Returns None while a batch is
    still training or the Academy is idle. Called once per day tick."""
    recruit = corp_state.pending_recruit
    if recruit is None or day < recruit.ready_day:
        return None
    if recruit.category is EmployeeCategory.SCIENTIST:
        corp_state.scientists += recruit.count
    elif recruit.category is EmployeeCategory.OPERATIVE:
        # Snapped to the cap rather than routed through _add_operatives' room-based
        # clip: unlike a tasking operative returning, the pool here may already sit
        # above operative_max (e.g. the cap dropping after the batch was queued), and
        # this is the point that resyncs it back down rather than adding on top.
        # The recruit's own count is trimmed to match what actually landed, so the
        # caller (app.py's completion toast) doesn't overstate the batch.
        before = corp_state.operatives
        corp_state.operatives = min(before + recruit.count, operative_max(corp_state))
        recruit.count = max(corp_state.operatives - before, 0)
    else:
        corp_state.research_assistants += recruit.count
    corp_state.pending_recruit = None
    return recruit


def next_academy_upgrade_cost(academy: Location) -> int | None:
    tier = academy.academy_tier or 0
    idx = tier - STARTING_ACADEMY_TIER
    if idx < 0 or idx >= len(ACADEMY_UPGRADE_COSTS):
        return None
    return ACADEMY_UPGRADE_COSTS[idx]


def upgrade_academy(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash and 1 AP to raise the academy's tier by one, training one more
    employee per batch. Fails closed if out of AP, no academy, already maxed, or
    can't afford the next upgrade."""
    if corp_state.action_points < AP_COST:
        return False
    academy = owned_academy(corp_state, corp_map)
    if academy is None:
        return False
    cost = next_academy_upgrade_cost(academy)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    academy.academy_tier = (academy.academy_tier or STARTING_ACADEMY_TIER) + 1
    corp_state.action_points -= AP_COST
    return True


def rebuild_academy_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp could stand a new academy on — empty while it still holds
    one anywhere, exactly like rebuild_facility_targets.

    An academy is the harsher of the two to lose. A corp with no research facility
    stops advancing; a corp with no academy stops producing operatives, and since
    operatives are the only way to attack *or* garrison, it has no counterplay left
    against the loss condition at all — just a slow slide. That's why this exists.
    """
    if owned_academy(corp_state, corp_map) is not None:
        return []
    return _owned_territories(corp_state, corp_map)


def build_academy(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Spend ACADEMY_REBUILD_COST to stand a new academy up on a held district, after
    a rival took the last one. Shares expand_into's daily slot.

    Fails closed if the corp has already moved today, still holds an academy, can't
    afford it, or names a district it doesn't hold.
    """
    if corp_state.action_points < AP_COST:
        return False
    if territory_id not in {t.id for t in rebuild_academy_targets(corp_state, corp_map)}:
        return False
    if ACADEMY_REBUILD_COST > corp_state.cash:
        return False
    corp_state.cash -= ACADEMY_REBUILD_COST
    add_academy(corp_map.territories[territory_id])
    corp_state.action_points -= AP_COST
    return True


def build_lab(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next lab, raising its
    scientist capacity by one. Shares expand_into/train_employees' daily slot;
    fails closed if the corp's already made its move today, holds no Research
    Facility, has already built out to MAX_LABS_BUILT, or can't afford it."""
    if corp_state.action_points < AP_COST:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_lab_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.labs_built = (facility.labs_built or 0) + 1
    corp_state.action_points -= AP_COST
    return True


def rebuild_facility_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp could stand a new research facility up on — **empty while
    it still holds one anywhere**.

    So this is a rebuild, not a second facility: capturing a rival's labs is the
    only way to run two (see owned_research_facilities), and a corp that has been
    stripped of its own isn't locked out of research for the rest of the run. It's
    also what keeps add_research_facility's id unique — a corp with a facility is
    never offered another.
    """
    if owned_research_facilities(corp_state, corp_map):
        return []
    return _owned_territories(corp_state, corp_map)


def build_research_facility(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Spend RESEARCH_FACILITY_REBUILD_COST to stand a new research facility up on a
    held district, after a rival took the last one. Shares expand_into's daily slot.

    The new facility starts bare — STARTING_RESEARCH_TIER, no labs, no efficiency
    upgrades — so whatever was built into the captured one is genuinely lost and has
    to be paid for again. *Where* is a real choice: a facility is captured with the
    district under it, so rebuilding on the border invites the same loss twice.

    Fails closed if the corp has already moved today, still holds a facility, can't
    afford it, or names a district it doesn't hold.
    """
    if corp_state.action_points < AP_COST:
        return False
    if territory_id not in {t.id for t in rebuild_facility_targets(corp_state, corp_map)}:
        return False
    if RESEARCH_FACILITY_REBUILD_COST > corp_state.cash:
        return False
    corp_state.cash -= RESEARCH_FACILITY_REBUILD_COST
    add_research_facility(corp_map.territories[territory_id])
    corp_state.action_points -= AP_COST
    return True


def build_efficiency_upgrade(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next efficiency upgrade,
    raising research_rate there by one. Shares expand_into/train_employees'
    daily slot; fails closed if the corp's already made its move today, holds
    no Research Facility, has already built out to MAX_EFFICIENCY_UPGRADES, or
    can't afford it."""
    if corp_state.action_points < AP_COST:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_efficiency_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.efficiency_upgrades = (facility.efficiency_upgrades or 0) + 1
    corp_state.action_points -= AP_COST
    return True
