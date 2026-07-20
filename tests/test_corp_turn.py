"""Tests for corp_turn.py: the player's own Corp turn (income + directed
expansion). Small hand-built CorpMaps mirror test_rivals.py's fixture style for
the fail-closed cases, plus a seed sweep over generate_corp_map for the
formula/invariant checks, matching the project's convention for generator-
adjacent code.
"""

import random

import pytest

from shadowguy.corp_turn import (
    ACADEMY_TRAINING_COST,
    EXPANSION_COST_BASE,
    EXPANSION_COST_PER_VALUE,
    STARTING_CASH,
    TERRITORY_INCOME_BASE,
    TERRITORY_INCOME_PER_VALUE,
    CorpState,
    EmployeeCategory,
    collect_income,
    collect_research,
    expand_into,
    expansion_cost,
    train_employees,
)
from shadowguy.corpmap import (
    CorpMap,
    Location,
    LocationKind,
    Territory,
    expansion_candidates,
    generate_corp_map,
)
from shadowguy.factions import FACTIONS

IRONCLAD, GHOSTWIRE, MERIDIAN = (f.id for f in FACTIONS)

SEEDS = range(150)


def _territory(id, owner="neutral", value=1, connections=(), gang_id=None):
    return Territory(
        id=id, name=id, x=0, y=0, owner=owner, value=value, connections=list(connections), gang_id=gang_id
    )


def _map():
    """start -- iron_home(value=2) -- neutral_a(value=3)
    iron_home also owns iron_second(value=1); neutral_gang is gang turf."""
    return CorpMap(
        territories={
            "start": _territory("start", connections=["iron_home"]),
            "iron_home": _territory(
                "iron_home",
                owner=IRONCLAD,
                value=2,
                connections=["start", "neutral_a", "neutral_gang", "iron_second"],
            ),
            "iron_second": _territory("iron_second", owner=IRONCLAD, value=1, connections=["iron_home"]),
            "neutral_a": _territory("neutral_a", value=3, connections=["iron_home"]),
            "neutral_gang": _territory("neutral_gang", connections=["iron_home"], gang_id="gang_x"),
        },
        player_start_id="start",
    )


def test_collect_income_sums_only_owned_territories():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD)
    expected = (TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * 2) + (
        TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * 1
    )
    assert collect_income(corp_state, corp_map) == expected


def test_collect_research_sums_facility_tiers_on_owned_territory():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility One", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    )
    corp_map.territories["iron_second"].locations.append(
        Location(id="rf2", name="Facility Two", kind=LocationKind.RESEARCH_FACILITY, research_tier=3)
    )
    corp_state = CorpState(faction_id=IRONCLAD)
    assert collect_research(corp_state, corp_map) == 1 + 3


def test_collect_research_ignores_facilities_on_unowned_territory():
    corp_map = _map()
    corp_map.territories["neutral_a"].locations.append(
        Location(id="rf3", name="Someone Else's Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=5)
    )
    corp_state = CorpState(faction_id=IRONCLAD)
    assert collect_research(corp_state, corp_map) == 0


def test_default_research_points_is_zero():
    assert CorpState(faction_id=IRONCLAD).research_points == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_collect_research_matches_the_generated_facility_s_tier(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    faction = FACTIONS[0]
    corp_state = CorpState(faction_id=faction.id)
    facility = next(
        location
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.RESEARCH_FACILITY and territory.owner == faction.id
    )
    assert collect_research(corp_state, corp_map) == facility.research_tier


def test_expansion_cost_scales_with_value():
    corp_map = _map()
    territory = corp_map.territories["neutral_a"]
    assert expansion_cost(territory) == EXPANSION_COST_BASE + EXPANSION_COST_PER_VALUE * 3


def test_expand_into_succeeds_and_charges_cash():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    rng = random.Random(0)
    cost = expansion_cost(corp_map.territories["neutral_a"])
    assert expand_into(corp_state, corp_map, "neutral_a", rng) is True
    assert corp_map.territories["neutral_a"].owner == IRONCLAD
    assert corp_state.cash == 10_000 - cost
    assert corp_state.daily_action_used is True


def test_expand_into_fails_when_already_used_today():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, daily_action_used=True)
    rng = random.Random(0)
    assert expand_into(corp_state, corp_map, "neutral_a", rng) is False
    assert corp_map.territories["neutral_a"].owner == "neutral"
    assert corp_state.cash == 10_000


def test_expand_into_fails_when_unaffordable():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=0)
    rng = random.Random(0)
    assert expand_into(corp_state, corp_map, "neutral_a", rng) is False
    assert corp_map.territories["neutral_a"].owner == "neutral"
    assert corp_state.daily_action_used is False


def test_expand_into_fails_for_gang_turf():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    rng = random.Random(0)
    assert expand_into(corp_state, corp_map, "neutral_gang", rng) is False
    assert corp_map.territories["neutral_gang"].owner == "neutral"


def test_expand_into_fails_for_the_player_start_territory():
    corp_map = _map()
    corp_map.territories["iron_home"].connections.append("start")
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    rng = random.Random(0)
    assert expand_into(corp_state, corp_map, "start", rng) is False


def test_default_starting_cash():
    assert CorpState(faction_id=IRONCLAD).cash == STARTING_CASH


@pytest.mark.parametrize("seed", SEEDS)
def test_collect_income_matches_formula_on_generated_maps(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    faction_id = FACTIONS[0].id
    corp_state = CorpState(faction_id=faction_id)
    owned = [t for t in corp_map.territories.values() if t.owner == faction_id]
    expected = sum(TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * t.value for t in owned)
    assert collect_income(corp_state, corp_map) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_expand_into_only_mutates_the_claimed_territory(seed):
    rng = random.Random(seed)
    corp_map = generate_corp_map(FACTIONS, rng)
    faction_id = FACTIONS[0].id
    corp_state = CorpState(faction_id=faction_id, cash=100_000)
    before = {tid: t.owner for tid, t in corp_map.territories.items()}
    candidates = expansion_candidates(corp_map, faction_id)
    if not candidates:
        pytest.skip("no eligible neutral neighbor on this seed")
    target = candidates[0]
    assert expand_into(corp_state, corp_map, target, rng) is True
    for tid, territory in corp_map.territories.items():
        if tid == target:
            assert territory.owner == faction_id
        else:
            assert territory.owner == before[tid]
    assert corp_state.daily_action_used is True
    # A second attempt the same day must not touch anything further.
    other_candidates = expansion_candidates(corp_map, faction_id)
    if other_candidates:
        assert expand_into(corp_state, corp_map, other_candidates[0], rng) is False


def test_train_employees_succeeds_and_charges_cash():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=2)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST) is True
    assert corp_state.cash == 10_000 - ACADEMY_TRAINING_COST
    assert corp_state.scientists == 2
    assert corp_state.operatives == 0
    assert corp_state.daily_action_used is True


def test_train_employees_credits_the_right_category():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE) is True
    assert corp_state.operatives == 1
    assert corp_state.scientists == 0


def test_train_employees_fails_with_no_academy():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST) is False
    assert corp_state.cash == 10_000
    assert corp_state.scientists == 0
    assert corp_state.operatives == 0


def test_train_employees_fails_when_unaffordable():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=0)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST) is False
    assert corp_state.scientists == 0


def test_train_employees_fails_when_already_used_today():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, daily_action_used=True)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST) is False
    assert corp_state.scientists == 0


def test_expand_and_train_share_the_same_daily_slot():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=100_000)
    rng = random.Random(0)
    assert expand_into(corp_state, corp_map, "neutral_a", rng) is True
    # Training the same day is refused -- the day's one move is already spent.
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST) is False
    assert corp_state.scientists == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_train_employees_matches_the_generated_academy_s_tier(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    faction = FACTIONS[0]
    corp_state = CorpState(faction_id=faction.id, cash=100_000)
    academy = next(
        location
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.ACADEMY and territory.owner == faction.id
    )
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE) is True
    assert corp_state.operatives == academy.academy_tier
    assert corp_state.scientists == 0
