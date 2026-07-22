"""The player's own Corp turn: a parallel resolution module, like rivals.py/
security.py — not a Scene.

First slice of "the player runs a corp instead of just a runner": the player
takes over one of the 3 seeded Factions (CorpState.faction_id) rather than
founding a new one, via a plain menu pick (screens/corp_screen.py) — there's no
in-fiction takeover mechanic yet, the same shortcut-before-the-real-gate
precedent TestMenu already sets for jumping straight into a fight.

Corp mode shares the runner's own day clock rather than keeping a separate
calendar: ShadowguyApp's day tick (app._apply_day_tick) collects each day's
territory income into CorpState.cash and resets daily_action_used, right
alongside the AI factions' own resolve_rival_day (which skips the player's
faction_id once this is set).

A turn is one real decision, shared by two mutually-exclusive moves gated on the
same CorpState.daily_action_used flag (the same "_used_today flag reset each
day" idiom Character.on_new_day() uses for health_kit_used_today):
  - expand_into a bordering neutral territory, the same area-control move
    rivals.py's AI factions make (reusing corpmap.expansion_candidates/
    claim_territory).
  - train_employees at the corp's one guaranteed ACADEMY (corpmap._make_academy),
    spending cash to grow one of CorpState.scientists/operatives/
    research_assistants (EmployeeCategory picks which — three separate pools,
    not one, since they're meant to eventually do different things for the
    corp).

Each faction's one guaranteed RESEARCH_FACILITY (corpmap._make_research_facility)
generates research_points every day too, at 1 RP per tier — collect_research is
the read side of that, and research_technology is what finally spends it.

TECHNOLOGIES is the researchable list; both entries are available from the start
(there's no prerequisite system, and nothing needs one yet). A tech's *effect*
isn't a field on Technology — it's read wherever it applies, keyed off the id:
  - Worker Surveillance: collect_income adds WORKER_SURVEILLANCE_INCOME_BONUS
    per held territory, and surveillance_targets/raise_surveillance only work
    once it's researched.
  - Brains 2: scientist_base_rate/assistant_rate return the better per-head
    research rates, which research_rate and collect_research read. It replaces
    the base rates rather than stacking with them, but facility efficiency
    upgrades still stack on top of the scientist rate — the two paths compose.
    Note this one compounds: faster research buys techs faster.

Neither research_technology nor the two territory bumps below touch
daily_action_used: RP and cash are their own gates, and the day's one *directed
move* is for expand/train/build. So a corp turn now has two independent budgets
— the daily slot, and whatever cash/RP has piled up.

Territory modifiers are no longer generation-only. raise_surveillance and
raise_development each buy one point of corpmap.TerritoryModifier on a held
district, and they chain: Development can only be bought once Security AND
Surveillance clear DEVELOPMENT_MIN_*, so Worker Surveillance is the route to
developing a district that didn't seed well enough. Note raise_surveillance
deliberately does NOT re-derive Development the way corpmap._development() does
at generation time — Development is its own purchase here, not a formula.

A research facility can also be upgraded, two ways, both sharing
expand_into/train_employees' one daily_action_used slot:
  - build_lab seats one more trained scientist actually working the facility
    (collect_research caps how many of the corp's scientists count by the
    facility's total lab_capacity), plus RESEARCH_ASSISTANTS_PER_LAB research
    assistants on top of that scientist (assistant_capacity).
  - build_efficiency_upgrade adds +1 RP/day to every scientist working that
    facility (research_rate) — research assistants are unaffected, they always
    add a flat RESEARCH_PER_ASSISTANT.
Both are strictly sequential — LAB_UPGRADE_COSTS/EFFICIENCY_UPGRADE_COSTS are
indexed by labs_built/efficiency_upgrades, so the second tier's cost isn't
reachable until the first is built.

Leaf-ish: imports corpmap only, never scene or app.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.corpmap import (
    MODIFIER_MAX,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    TerritoryModifier,
    claim_territory,
    expansion_candidates,
)

# First-slice numbers, not balance-simulated.
STARTING_CASH = 500

TERRITORY_INCOME_BASE = 10
TERRITORY_INCOME_PER_VALUE = 15

# Mirrors corpmap.safehouse_price's base + per-value shape: a richer neutral
# territory costs more to move into.
EXPANSION_COST_BASE = 150
EXPANSION_COST_PER_VALUE = 100

# Flat for now since nothing raises an Academy's tier yet (see corpmap.py). Same
# cost regardless of which EmployeeCategory is trained.
ACADEMY_TRAINING_COST = 200

# A research facility seats this many working scientists for free, before any
# lab is built.
BASE_LAB_CAPACITY = 1
# Cost of the 1st and 2nd extra lab, indexed by Location.labs_built -- strictly
# sequential, so the 2nd lab's cost/capacity isn't reachable without the 1st.
LAB_UPGRADE_COSTS = (2000, 5000)
MAX_LABS_BUILT = len(LAB_UPGRADE_COSTS)
# RP/day each working scientist adds, on top of the facility's own tier.
RESEARCH_PER_SCIENTIST = 1
# Cost of the 1st and 2nd efficiency upgrade, indexed by
# Location.efficiency_upgrades -- strictly sequential, same shape as
# LAB_UPGRADE_COSTS. Priced steeper than a lab: +1 RP/scientist compounds with
# however many scientists are staffed, so it can be worth more than +1 capacity.
EFFICIENCY_UPGRADE_COSTS = (3000, 7000)
MAX_EFFICIENCY_UPGRADES = len(EFFICIENCY_UPGRADE_COSTS)

# Each lab (including the free base one) seats this many research assistants,
# on top of its own scientist.
RESEARCH_ASSISTANTS_PER_LAB = 2
# RP/day each working research assistant adds — flat, unlike research_rate:
# efficiency upgrades boost scientists only.
RESEARCH_PER_ASSISTANT = 0.5


@dataclass(frozen=True)
class Technology:
    """One researchable corp technology. `cost` is in research points — this is
    the first thing in the game that actually spends them (collect_research had
    been accruing RP against nothing).

    Effects are *not* fields here: a tech's effect is read where it applies
    (collect_income for the income bonus, raise_surveillance for the ability),
    keyed off its id, rather than described by a generic bonus field the reader
    would then have to hunt for the consumer of. One tech, one place that asks
    "is it researched?" — the same shape jobs.archetype_specialist uses, derived
    at the point of use instead of tabulated.
    """

    id: str
    name: str
    cost: int  # research points
    description: str


WORKER_SURVEILLANCE_ID = "worker_surveillance"
BRAINS_2_ID = "brains_2"

# id, name, cost (RP), description. Both are researchable from the start —
# there's no prerequisite system, and nothing here needs one yet.
_TECHNOLOGY_ROWS = (
    (
        WORKER_SURVEILLANCE_ID,
        "Worker Surveillance",
        10,
        "Every territory you hold earns +{income}/day, and you can pay {bump}eb "
        "to raise Surveillance by 1 in any district you hold that isn't already at "
        f"{MODIFIER_MAX}.",
    ),
    (
        BRAINS_2_ID,
        "Brains 2",
        10,
        "Every working scientist produces {scientist}rp/day instead of "
        "{base_scientist}, and every working research assistant {assistant}rp/day "
        "instead of {base_assistant}.",
    ),
)

# What Worker Surveillance is worth, in the two places it lands. The income bonus
# is per *territory* (it exactly doubles TERRITORY_INCOME_BASE), so the tech keeps
# paying as the corp expands rather than becoming a rounding error.
WORKER_SURVEILLANCE_INCOME_BONUS = 10
# Cash per Surveillance bump. Deliberately NOT on the daily_action_used slot —
# unlike expand/train/build, this is repeatable within a day and cash is its only
# gate, so the tech's own income bonus partly funds its use.
SURVEILLANCE_BUMP_COST = 400

# Development is raised as a *purchase*, not re-derived (see raise_development):
# capital only lands where the block is already both policed and watched, so a
# district has to clear both thresholds before it can be built up at all. This
# mirrors _development()'s own "rises with Security and Surveillance" logic
# without turning it back into an automatic re-derivation. Same cash-gated,
# repeatable shape as SURVEILLANCE_BUMP_COST, priced steeper because Development
# is the modifier that actually does something today (it prices runner-side
# lodging and safehouses — see corpmap.lodging_cost/safehouse_price).
# First-slice numbers, not balance-simulated.
DEVELOPMENT_MIN_SECURITY = 3
DEVELOPMENT_MIN_SURVEILLANCE = 3
DEVELOPMENT_BUMP_COST = 800

# Brains 2 replaces both per-head research rates outright rather than adding to
# them — a flat better rate, not a stacking bonus, so there's one number in
# effect at a time and scientist_base_rate/assistant_rate just pick which.
# Efficiency upgrades still stack on top of the scientist rate (see
# research_rate), so the two upgrade paths compose rather than compete.
# Unlike Worker Surveillance's cash payoff this compounds — it makes research
# itself faster — which is why it costs the same 10 RP despite looking smaller.
# First-slice numbers, not balance-simulated.
BRAINS_2_RESEARCH_PER_SCIENTIST = 1.25
BRAINS_2_RESEARCH_PER_ASSISTANT = 0.75

# Descriptions are filled in from the constants above rather than repeating the
# numbers as prose, so a retune can't leave the shop text lying about the effect.
TECHNOLOGIES = [
    Technology(
        id=tech_id,
        name=name,
        cost=cost,
        description=description.format(
            income=WORKER_SURVEILLANCE_INCOME_BONUS,
            bump=SURVEILLANCE_BUMP_COST,
            scientist=BRAINS_2_RESEARCH_PER_SCIENTIST,
            base_scientist=RESEARCH_PER_SCIENTIST,
            assistant=BRAINS_2_RESEARCH_PER_ASSISTANT,
            base_assistant=RESEARCH_PER_ASSISTANT,
        ),
    )
    for tech_id, name, cost, description in _TECHNOLOGY_ROWS
]
TECHNOLOGIES_BY_ID = {tech.id: tech for tech in TECHNOLOGIES}

if any(tech.cost <= 0 for tech in TECHNOLOGIES):
    raise ValueError("a Technology must cost research points to be worth researching")


class EmployeeCategory(StrEnum):
    """What a training session at the Academy produces — nothing reads which
    category a hire belongs to yet beyond research_assistants feeding
    collect_research (that's the obvious next hook for the other two:
    scientists presumably feed research more directly, operatives presumably
    feed fieldwork), but the corp already needs to track them separately since
    they aren't fungible."""

    SCIENTIST = "scientist"
    OPERATIVE = "operative"
    RESEARCH_ASSISTANT = "research_assistant"


@dataclass
class CorpState:
    """The player's own corp: which Faction they run, its cash/research points/
    scientists/operatives/research_assistants on hand, and whether they've
    already spent today's one move (expand_into or train_employees — see
    module docstring)."""

    faction_id: str
    cash: int = STARTING_CASH
    research_points: float = 0
    scientists: int = 0
    operatives: int = 0
    research_assistants: int = 0
    daily_action_used: bool = False
    # Technology ids (TECHNOLOGIES_BY_ID) already researched. A set of ids, the
    # same shape Character.owned_programs/discovered_fixers use. Research is
    # permanent — nothing takes a tech back.
    researched: set[str] = field(default_factory=set)


def has_technology(corp_state: CorpState, technology_id: str) -> bool:
    return technology_id in corp_state.researched


def research_technology(corp_state: CorpState, technology_id: str) -> bool:
    """Spend research points to unlock a Technology permanently. Fails closed (no
    charge, no mutation) if it's already researched or the corp can't afford it.

    Deliberately NOT on the daily_action_used slot: RP is its own pacing gate
    (10 RP is ~10 days of research at the base rate), and double-gating a
    purchase behind the day's one *directed move* would make researching compete
    with expanding for no design reason. Same call the cash-gated territory
    bumps below make.
    """
    technology = TECHNOLOGIES_BY_ID[technology_id]
    if has_technology(corp_state, technology_id) or technology.cost > corp_state.research_points:
        return False
    corp_state.research_points -= technology.cost
    corp_state.researched.add(technology_id)
    return True


def collect_income(corp_state: CorpState, corp_map: CorpMap) -> int:
    """Flat daily income from every territory the player's faction holds, plus
    WORKER_SURVEILLANCE_INCOME_BONUS per territory once that tech is researched
    — per territory, not once, so the tech keeps paying as the corp expands."""
    owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
    bonus = (
        WORKER_SURVEILLANCE_INCOME_BONUS if has_technology(corp_state, WORKER_SURVEILLANCE_ID) else 0
    )
    return sum(TERRITORY_INCOME_BASE + bonus + TERRITORY_INCOME_PER_VALUE * t.value for t in owned)


def owned_research_facility(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    """The corp's research facility, or None if it holds none.

    Singular on purpose: a faction is seeded with exactly one
    (corpmap._make_research_facility), and expand_into only claims *neutral*
    ground, which never carries one — so a corp can't come to hold a second.
    collect_research and both upgrade actions all read this same one place.
    """
    return next(
        (
            location
            for territory in corp_map.territories.values()
            if territory.owner == corp_state.faction_id
            for location in territory.locations
            if location.kind == LocationKind.RESEARCH_FACILITY
        ),
        None,
    )


def lab_capacity(facility: Location) -> int:
    """How many scientists this facility can put to work: a free base seat plus
    one more per lab built there."""
    return BASE_LAB_CAPACITY + (facility.labs_built or 0)


def next_lab_cost(facility: Location) -> int | None:
    """Cost of this facility's next lab, or None once MAX_LABS_BUILT is reached."""
    labs_built = facility.labs_built or 0
    if labs_built >= MAX_LABS_BUILT:
        return None
    return LAB_UPGRADE_COSTS[labs_built]


def scientist_base_rate(corp_state: CorpState) -> float:
    """RP/day one working scientist adds before any facility efficiency upgrade —
    RESEARCH_PER_SCIENTIST, or Brains 2's better rate once that's researched."""
    if has_technology(corp_state, BRAINS_2_ID):
        return BRAINS_2_RESEARCH_PER_SCIENTIST
    return RESEARCH_PER_SCIENTIST


def assistant_rate(corp_state: CorpState) -> float:
    """RP/day one working research assistant adds. Flat regardless of facility —
    efficiency upgrades boost scientists only — but Brains 2 raises it."""
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


def assistant_capacity(facility: Location) -> int:
    """How many research assistants this facility can put to work: each lab
    seats RESEARCH_ASSISTANTS_PER_LAB of them, same lab count as lab_capacity."""
    return RESEARCH_ASSISTANTS_PER_LAB * lab_capacity(facility)


def collect_research(corp_state: CorpState, corp_map: CorpMap) -> float:
    """RP/day from the corp's research facility: its tier directly (1 RP at tier
    1), plus research_rate() for each scientist actually working it, plus
    assistant_rate() for each research assistant actually working it. Both
    per-head rates are raised by the Brains 2 technology.

    "Actually working" is the whole mechanic: lab_capacity/assistant_capacity
    cap how many of each count, so employees trained beyond the seats built for
    them produce nothing — headcount (train_employees) and capacity (build_lab)
    are two separate purchases.
    """
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return 0.0
    scientists = min(corp_state.scientists, lab_capacity(facility))
    assistants = min(corp_state.research_assistants, assistant_capacity(facility))
    return (
        (facility.research_tier or 0)
        + scientists * research_rate(corp_state, facility)
        + assistants * assistant_rate(corp_state)
    )


def _owned_territories(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Sorted by id, so every list built off this renders in a stable order."""
    return sorted(
        (t for t in corp_map.territories.values() if t.owner == corp_state.faction_id),
        key=lambda t: t.id,
    )


def surveillance_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds whose Surveillance isn't already at MODIFIER_MAX.
    Empty until Worker Surveillance is researched — the tech is what grants the
    ability at all, not just a discount on it."""
    if not has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        return []
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) < MODIFIER_MAX
    ]


def raise_surveillance(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay SURVEILLANCE_BUMP_COST to raise one held district's Surveillance by 1.

    Repeatable within a day (cash is the only gate — see SURVEILLANCE_BUMP_COST),
    so unlike expand_into/train_employees this never touches daily_action_used.
    Fails closed if the tech isn't researched, the district isn't a legal target
    (not held, or already at MODIFIER_MAX), or the corp can't afford it.

    Deliberately does NOT re-derive TerritoryModifier.DEVELOPMENT, though
    corpmap._development() reads Surveillance: Development is raised as its own
    purchase here (raise_development), gated on Security and Surveillance rather
    than recomputed from them. So a district can sit at high Surveillance and low
    Development — that's the gap raise_development exists to let the player close,
    not an inconsistency to auto-correct.
    """
    if territory_id not in {t.id for t in surveillance_targets(corp_state, corp_map)}:
        return False
    if SURVEILLANCE_BUMP_COST > corp_state.cash:
        return False
    territory = corp_map.territories[territory_id]
    corp_state.cash -= SURVEILLANCE_BUMP_COST
    territory.modifiers[TerritoryModifier.SURVEILLANCE] = (
        territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) + 1
    )
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


def expansion_cost(territory: Territory) -> int:
    return EXPANSION_COST_BASE + EXPANSION_COST_PER_VALUE * territory.value


def expand_into(corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random) -> bool:
    """Spend cash to claim a bordering neutral territory. Fails closed (no
    mutation, no charge) if the corp's already made its move today, the target
    isn't a legal candidate for this faction right now, or it can't afford it."""
    if corp_state.daily_action_used:
        return False
    if territory_id not in expansion_candidates(corp_map, corp_state.faction_id):
        return False
    territory = corp_map.territories[territory_id]
    cost = expansion_cost(territory)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    claim_territory(territory, corp_state.faction_id, rng)
    corp_state.daily_action_used = True
    return True


def _owned_academy(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    for territory in corp_map.territories.values():
        if territory.owner != corp_state.faction_id:
            continue
        for location in territory.locations:
            if location.kind == LocationKind.ACADEMY:
                return location
    return None


def train_employees(corp_state: CorpState, corp_map: CorpMap, category: EmployeeCategory) -> bool:
    """Spend cash on one training session at the corp's Academy, gaining that
    many scientists, operatives or research assistants (whichever `category`
    picks) equal to the Academy's tier. Shares expand_into's once-a-day slot —
    fails closed if the corp's already made its move today, holds no Academy
    (it always does once its territory has been claimed at all), or can't
    afford it."""
    if corp_state.daily_action_used:
        return False
    academy = _owned_academy(corp_state, corp_map)
    if academy is None or ACADEMY_TRAINING_COST > corp_state.cash:
        return False
    corp_state.cash -= ACADEMY_TRAINING_COST
    gained = academy.academy_tier or 0
    if category is EmployeeCategory.SCIENTIST:
        corp_state.scientists += gained
    elif category is EmployeeCategory.OPERATIVE:
        corp_state.operatives += gained
    else:
        corp_state.research_assistants += gained
    corp_state.daily_action_used = True
    return True


def build_lab(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next lab, raising its
    scientist capacity by one. Shares expand_into/train_employees' daily slot;
    fails closed if the corp's already made its move today, holds no Research
    Facility, has already built out to MAX_LABS_BUILT, or can't afford it."""
    if corp_state.daily_action_used:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_lab_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.labs_built = (facility.labs_built or 0) + 1
    corp_state.daily_action_used = True
    return True


def build_efficiency_upgrade(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next efficiency upgrade,
    raising research_rate there by one. Shares expand_into/train_employees'
    daily slot; fails closed if the corp's already made its move today, holds
    no Research Facility, has already built out to MAX_EFFICIENCY_UPGRADES, or
    can't afford it."""
    if corp_state.daily_action_used:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_efficiency_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.efficiency_upgrades = (facility.efficiency_upgrades or 0) + 1
    corp_state.daily_action_used = True
    return True
