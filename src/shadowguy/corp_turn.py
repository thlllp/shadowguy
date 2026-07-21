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
the read side of that; nothing spends RP yet, the same deferred-hook shape as
TerritoryModifier before Development got wired up.

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
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.corpmap import CorpMap, Location, LocationKind, Territory, claim_territory, expansion_candidates

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


def collect_income(corp_state: CorpState, corp_map: CorpMap) -> int:
    """Flat daily income from every territory the player's faction holds."""
    owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
    return sum(TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * t.value for t in owned)


def _owned_research_facilities(corp_state: CorpState, corp_map: CorpMap) -> list[Location]:
    return [
        location
        for territory in corp_map.territories.values()
        if territory.owner == corp_state.faction_id
        for location in territory.locations
        if location.kind == LocationKind.RESEARCH_FACILITY
    ]


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


def research_rate(facility: Location) -> int:
    """RP/day one working scientist adds at this facility: the base rate plus
    any efficiency upgrades built there."""
    return RESEARCH_PER_SCIENTIST + (facility.efficiency_upgrades or 0)


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
    """RP/day is a research facility's tier, directly — 1 RP at tier 1 — summed
    over every RESEARCH_FACILITY inside territory the corp currently holds, plus
    research_rate() for each scientist actually working one, plus
    RESEARCH_PER_ASSISTANT for each research assistant actually working one.
    Scientists fill the corp's own facilities highest-rate-first, and a
    facility's own lab_capacity/assistant_capacity caps how many of each count
    there, so the same employee can't be double-counted across separate
    facilities and a corp with more than one always staffs its best-upgraded
    facility's scientist seats first (assistants pay the same flat rate
    everywhere, so their fill order doesn't matter)."""
    facilities = _owned_research_facilities(corp_state, corp_map)
    tier_total = sum(facility.research_tier or 0 for facility in facilities)
    remaining_scientists = corp_state.scientists
    scientist_total = 0
    for facility in sorted(facilities, key=research_rate, reverse=True):
        working = min(remaining_scientists, lab_capacity(facility))
        scientist_total += working * research_rate(facility)
        remaining_scientists -= working
    assistant_capacity_total = sum(assistant_capacity(facility) for facility in facilities)
    working_assistants = min(corp_state.research_assistants, assistant_capacity_total)
    return tier_total + scientist_total + working_assistants * RESEARCH_PER_ASSISTANT


def owned_research_facility(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    facilities = _owned_research_facilities(corp_state, corp_map)
    return facilities[0] if facilities else None


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
