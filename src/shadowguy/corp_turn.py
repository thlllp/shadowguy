"""The player's own Corp turn: a parallel resolution module, like rivals.py/
security.py — not a Scene.

First slice of "the player runs a corp instead of just a runner": the player
takes over one of the 3 seeded Factions (CorpState.faction_id) rather than
founding a new one, via a plain menu pick (screens/corp_screen.py) — there's no
in-fiction takeover mechanic yet, the same shortcut-before-the-real-gate
precedent TestMenu already sets for jumping straight into a fight.

Corp mode shares the runner's own day clock rather than keeping a separate
calendar: ShadowguyApp.advance_day() collects each day's territory income into
CorpState.cash and resets daily_action_used, right alongside the AI factions'
own resolve_rival_day (which skips the player's faction_id once this is set).

A turn is one real decision, shared by two mutually-exclusive moves gated on the
same CorpState.daily_action_used flag (the same "_used_today flag reset each
day" idiom Character.rest() uses for health_kit_used_today):
  - expand_into a bordering neutral territory, the same area-control move
    rivals.py's AI factions make (reusing corpmap.expansion_candidates/
    claim_territory).
  - train_employees at the corp's one guaranteed ACADEMY (corpmap._make_academy),
    spending cash to grow one of CorpState.scientists/operatives (EmployeeCategory
    picks which — two separate pools, not one, since a scientist and an operative
    are meant to eventually do different things for the corp).

Each faction's one guaranteed RESEARCH_FACILITY (corpmap._make_research_facility)
generates research_points every day too, at 1 RP per tier — collect_research is
the read side of that; nothing spends RP yet, the same deferred-hook shape as
TerritoryModifier before Development got wired up.

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


class EmployeeCategory(StrEnum):
    """What a training session at the Academy produces — nothing reads which
    category a hire belongs to yet (that's the obvious next hook: scientists
    presumably feed research, operatives presumably feed fieldwork), but the
    corp already needs to track them separately since they aren't fungible."""

    SCIENTIST = "scientist"
    OPERATIVE = "operative"


@dataclass
class CorpState:
    """The player's own corp: which Faction they run, its cash/research points/
    scientists/operatives on hand, and whether they've already spent today's one
    move (expand_into or train_employees — see module docstring)."""

    faction_id: str
    cash: int = STARTING_CASH
    research_points: int = 0
    scientists: int = 0
    operatives: int = 0
    daily_action_used: bool = False


def collect_income(corp_state: CorpState, corp_map: CorpMap) -> int:
    """Flat daily income from every territory the player's faction holds."""
    owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
    return sum(TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * t.value for t in owned)


def collect_research(corp_state: CorpState, corp_map: CorpMap) -> int:
    """RP/day is a research facility's tier, directly — 1 RP at tier 1 — summed
    over every RESEARCH_FACILITY inside territory the corp currently holds."""
    return sum(
        location.research_tier or 0
        for territory in corp_map.territories.values()
        if territory.owner == corp_state.faction_id
        for location in territory.locations
        if location.kind == LocationKind.RESEARCH_FACILITY
    )


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
    many scientists or operatives (whichever `category` picks) equal to the
    Academy's tier. Shares expand_into's once-a-day slot — fails closed if the
    corp's already made its move today, holds no Academy (it always does once
    its territory has been claimed at all), or can't afford it."""
    if corp_state.daily_action_used:
        return False
    academy = _owned_academy(corp_state, corp_map)
    if academy is None or ACADEMY_TRAINING_COST > corp_state.cash:
        return False
    corp_state.cash -= ACADEMY_TRAINING_COST
    gained = academy.academy_tier or 0
    if category is EmployeeCategory.SCIENTIST:
        corp_state.scientists += gained
    else:
        corp_state.operatives += gained
    corp_state.daily_action_used = True
    return True
