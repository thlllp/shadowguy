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
    BASE_LAB_CAPACITY,
    DEVELOPMENT_BUMP_COST,
    DEVELOPMENT_MIN_SECURITY,
    DEVELOPMENT_MIN_SURVEILLANCE,
    EFFICIENCY_UPGRADE_COSTS,
    EXPANSION_COST_BASE,
    EXPANSION_COST_PER_VALUE,
    LAB_UPGRADE_COSTS,
    MAX_EFFICIENCY_UPGRADES,
    MAX_LABS_BUILT,
    RESEARCH_ASSISTANTS_PER_LAB,
    RESEARCH_PER_ASSISTANT,
    RESEARCH_PER_SCIENTIST,
    STARTING_CASH,
    SURVEILLANCE_BUMP_COST,
    TECHNOLOGIES_BY_ID,
    TERRITORY_INCOME_BASE,
    TERRITORY_INCOME_PER_VALUE,
    WORKER_SURVEILLANCE_ID,
    WORKER_SURVEILLANCE_INCOME_BONUS,
    CorpState,
    EmployeeCategory,
    assistant_capacity,
    build_efficiency_upgrade,
    build_lab,
    collect_income,
    collect_research,
    development_targets,
    expand_into,
    expansion_cost,
    has_technology,
    lab_capacity,
    next_efficiency_cost,
    next_lab_cost,
    owned_research_facility,
    raise_development,
    raise_surveillance,
    research_rate,
    research_technology,
    surveillance_targets,
    train_employees,
)
from shadowguy.corpmap import (
    MODIFIER_MAX,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    TerritoryModifier,
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


def test_collect_research_reads_the_facility_tier_on_owned_territory():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility One", kind=LocationKind.RESEARCH_FACILITY, research_tier=3)
    )
    corp_state = CorpState(faction_id=IRONCLAD)
    assert collect_research(corp_state, corp_map) == 3


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
    assert corp_state.research_assistants == 0


def test_train_employees_credits_research_assistants():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=2)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.RESEARCH_ASSISTANT) is True
    assert corp_state.research_assistants == 2
    assert corp_state.scientists == 0
    assert corp_state.operatives == 0


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


def test_lab_capacity_starts_at_base_with_no_labs_built():
    facility = Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    assert lab_capacity(facility) == BASE_LAB_CAPACITY


def test_next_lab_cost_matches_the_upgrade_table():
    facility = Location(
        id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=0
    )
    assert next_lab_cost(facility) == LAB_UPGRADE_COSTS[0]
    facility.labs_built = 1
    assert next_lab_cost(facility) == LAB_UPGRADE_COSTS[1]
    facility.labs_built = MAX_LABS_BUILT
    assert next_lab_cost(facility) is None


def test_collect_research_adds_working_scientists_capped_by_capacity():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=1
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, scientists=5)
    capacity = BASE_LAB_CAPACITY + 1
    expected = 1 + min(5, capacity) * RESEARCH_PER_SCIENTIST
    assert collect_research(corp_state, corp_map) == expected


def test_build_lab_succeeds_and_charges_cash():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=0)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert build_lab(corp_state, corp_map) is True
    facility = owned_research_facility(corp_state, corp_map)
    assert facility.labs_built == 1
    assert corp_state.cash == 10_000 - LAB_UPGRADE_COSTS[0]
    assert corp_state.daily_action_used is True


def test_build_lab_is_sequential():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=0)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=100_000)
    assert build_lab(corp_state, corp_map) is True
    corp_state.daily_action_used = False
    assert build_lab(corp_state, corp_map) is True
    facility = owned_research_facility(corp_state, corp_map)
    assert facility.labs_built == MAX_LABS_BUILT
    corp_state.daily_action_used = False
    assert build_lab(corp_state, corp_map) is False
    assert facility.labs_built == MAX_LABS_BUILT


def test_build_lab_fails_with_no_research_facility():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert build_lab(corp_state, corp_map) is False
    assert corp_state.cash == 10_000


def test_build_lab_fails_when_unaffordable():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=0)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=0)
    assert build_lab(corp_state, corp_map) is False


def test_build_lab_fails_when_already_used_today():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=0)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, daily_action_used=True)
    assert build_lab(corp_state, corp_map) is False


def test_research_rate_starts_at_base_with_no_efficiency_upgrades():
    facility = Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    assert research_rate(facility) == RESEARCH_PER_SCIENTIST


def test_next_efficiency_cost_matches_the_upgrade_table():
    facility = Location(
        id="rf1",
        name="Facility",
        kind=LocationKind.RESEARCH_FACILITY,
        research_tier=1,
        efficiency_upgrades=0,
    )
    assert next_efficiency_cost(facility) == EFFICIENCY_UPGRADE_COSTS[0]
    facility.efficiency_upgrades = 1
    assert next_efficiency_cost(facility) == EFFICIENCY_UPGRADE_COSTS[1]
    facility.efficiency_upgrades = MAX_EFFICIENCY_UPGRADES
    assert next_efficiency_cost(facility) is None


def test_collect_research_uses_the_boosted_rate():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1",
            name="Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=1,
            efficiency_upgrades=1,
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, scientists=5)
    capacity = BASE_LAB_CAPACITY
    rate = RESEARCH_PER_SCIENTIST + 1
    expected = 1 + min(5, capacity) * rate
    assert collect_research(corp_state, corp_map) == expected


def test_collect_research_reads_only_the_corps_own_facility():
    """A corp holds exactly one research facility (seeded per faction; expand_into
    only claims neutral ground, which carries none), so collect_research reads the
    single owned one -- another faction's is never counted. This replaces an earlier
    multi-facility fill-order test, dropped when collect_research collapsed to one."""
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf_own",
            name="Own Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=0,
            efficiency_upgrades=2,
        )
    )
    corp_map.territories["neutral_a"].locations.append(
        Location(
            id="rf_other",
            name="Unowned Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=9,
            efficiency_upgrades=9,
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, scientists=BASE_LAB_CAPACITY)
    expected = BASE_LAB_CAPACITY * (RESEARCH_PER_SCIENTIST + 2)
    assert collect_research(corp_state, corp_map) == expected


def test_build_efficiency_upgrade_succeeds_and_charges_cash():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1",
            name="Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=1,
            efficiency_upgrades=0,
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert build_efficiency_upgrade(corp_state, corp_map) is True
    facility = owned_research_facility(corp_state, corp_map)
    assert facility.efficiency_upgrades == 1
    assert corp_state.cash == 10_000 - EFFICIENCY_UPGRADE_COSTS[0]
    assert corp_state.daily_action_used is True


def test_build_efficiency_upgrade_is_sequential():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1",
            name="Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=1,
            efficiency_upgrades=0,
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=100_000)
    assert build_efficiency_upgrade(corp_state, corp_map) is True
    corp_state.daily_action_used = False
    assert build_efficiency_upgrade(corp_state, corp_map) is True
    facility = owned_research_facility(corp_state, corp_map)
    assert facility.efficiency_upgrades == MAX_EFFICIENCY_UPGRADES
    corp_state.daily_action_used = False
    assert build_efficiency_upgrade(corp_state, corp_map) is False
    assert facility.efficiency_upgrades == MAX_EFFICIENCY_UPGRADES


def test_build_efficiency_upgrade_fails_with_no_research_facility():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert build_efficiency_upgrade(corp_state, corp_map) is False
    assert corp_state.cash == 10_000


def test_build_efficiency_upgrade_fails_when_unaffordable():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1",
            name="Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=1,
            efficiency_upgrades=0,
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=0)
    assert build_efficiency_upgrade(corp_state, corp_map) is False


def test_build_efficiency_upgrade_fails_when_already_used_today():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1",
            name="Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=1,
            efficiency_upgrades=0,
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, daily_action_used=True)
    assert build_efficiency_upgrade(corp_state, corp_map) is False


def test_assistant_capacity_scales_with_labs_built():
    facility = Location(
        id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=1
    )
    assert assistant_capacity(facility) == (BASE_LAB_CAPACITY + 1) * RESEARCH_ASSISTANTS_PER_LAB


def test_collect_research_adds_working_assistants_capped_by_capacity():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, research_assistants=5)
    capacity = BASE_LAB_CAPACITY * RESEARCH_ASSISTANTS_PER_LAB
    expected = 1 + min(5, capacity) * RESEARCH_PER_ASSISTANT
    assert collect_research(corp_state, corp_map) == expected


def test_collect_research_combines_scientists_and_assistants():
    corp_map = _map()
    corp_map.territories["iron_home"].locations.append(
        Location(
            id="rf1", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=1, labs_built=1
        )
    )
    corp_state = CorpState(faction_id=IRONCLAD, scientists=2, research_assistants=4)
    lab_cap = BASE_LAB_CAPACITY + 1
    assist_cap = lab_cap * RESEARCH_ASSISTANTS_PER_LAB
    expected = (
        1
        + min(2, lab_cap) * RESEARCH_PER_SCIENTIST
        + min(4, assist_cap) * RESEARCH_PER_ASSISTANT
    )
    assert collect_research(corp_state, corp_map) == expected


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


# --- Technology: Worker Surveillance ------------------------------------------
# The first thing in the game that spends research points. Its two effects land in
# different places (collect_income for the per-territory bonus, raise_surveillance
# for the ability), so they're tested separately rather than through one call.


def _corp_territory(corp_map, territory_id, **modifiers):
    """Set a held territory's modifiers explicitly -- _map()'s fixtures carry none,
    and every gate below reads them."""
    territory = corp_map.territories[territory_id]
    territory.modifiers = {
        TerritoryModifier.SECURITY: modifiers.get("security", 0),
        TerritoryModifier.SURVEILLANCE: modifiers.get("surveillance", 0),
        TerritoryModifier.UNREST: modifiers.get("unrest", 0),
        TerritoryModifier.DEVELOPMENT: modifiers.get("development", 0),
        TerritoryModifier.RESTRICTED: modifiers.get("restricted", 0),
    }
    return territory


def test_research_technology_spends_research_points_once():
    corp_state = CorpState(faction_id=IRONCLAD, research_points=10)
    cost = TECHNOLOGIES_BY_ID[WORKER_SURVEILLANCE_ID].cost
    assert research_technology(corp_state, WORKER_SURVEILLANCE_ID) is True
    assert corp_state.research_points == 10 - cost
    assert has_technology(corp_state, WORKER_SURVEILLANCE_ID)
    # Researching again is refused and costs nothing further.
    assert research_technology(corp_state, WORKER_SURVEILLANCE_ID) is False
    assert corp_state.research_points == 10 - cost


def test_research_technology_fails_closed_when_short_on_points():
    cost = TECHNOLOGIES_BY_ID[WORKER_SURVEILLANCE_ID].cost
    corp_state = CorpState(faction_id=IRONCLAD, research_points=cost - 1)
    assert research_technology(corp_state, WORKER_SURVEILLANCE_ID) is False
    assert corp_state.research_points == cost - 1
    assert corp_state.researched == set()


def test_research_technology_does_not_consume_the_daily_action():
    """RP is its own pacing gate, so researching doesn't compete with expanding."""
    corp_state = CorpState(faction_id=IRONCLAD, research_points=10)
    assert research_technology(corp_state, WORKER_SURVEILLANCE_ID) is True
    assert corp_state.daily_action_used is False


def test_worker_surveillance_income_bonus_is_per_territory():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD)
    owned = [t for t in corp_map.territories.values() if t.owner == IRONCLAD]
    before = collect_income(corp_state, corp_map)
    corp_state.researched.add(WORKER_SURVEILLANCE_ID)
    after = collect_income(corp_state, corp_map)
    assert after - before == WORKER_SURVEILLANCE_INCOME_BONUS * len(owned)


def test_surveillance_targets_are_empty_until_researched():
    corp_map = _map()
    _corp_territory(corp_map, "iron_home", surveillance=1)
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert surveillance_targets(corp_state, corp_map) == []
    assert raise_surveillance(corp_state, corp_map, "iron_home") is False
    assert corp_state.cash == 10_000


def test_raise_surveillance_bumps_one_level_and_charges_cash():
    corp_map = _map()
    territory = _corp_territory(corp_map, "iron_home", surveillance=1)
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, researched={WORKER_SURVEILLANCE_ID})
    assert raise_surveillance(corp_state, corp_map, "iron_home") is True
    assert territory.modifiers[TerritoryModifier.SURVEILLANCE] == 2
    assert corp_state.cash == 10_000 - SURVEILLANCE_BUMP_COST
    # Repeatable within the same day -- cash is the only gate.
    assert corp_state.daily_action_used is False
    assert raise_surveillance(corp_state, corp_map, "iron_home") is True
    assert territory.modifiers[TerritoryModifier.SURVEILLANCE] == 3


def test_raise_surveillance_refuses_a_maxed_district():
    corp_map = _map()
    _corp_territory(corp_map, "iron_home", surveillance=MODIFIER_MAX)
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, researched={WORKER_SURVEILLANCE_ID})
    assert "iron_home" not in {t.id for t in surveillance_targets(corp_state, corp_map)}
    assert raise_surveillance(corp_state, corp_map, "iron_home") is False
    assert corp_state.cash == 10_000


def test_raise_surveillance_refuses_territory_the_corp_does_not_hold():
    corp_map = _map()
    _corp_territory(corp_map, "neutral_a", surveillance=1)
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, researched={WORKER_SURVEILLANCE_ID})
    assert raise_surveillance(corp_state, corp_map, "neutral_a") is False
    assert corp_map.territories["neutral_a"].modifiers[TerritoryModifier.SURVEILLANCE] == 1


def test_raise_surveillance_fails_closed_when_unaffordable():
    corp_map = _map()
    territory = _corp_territory(corp_map, "iron_home", surveillance=1)
    corp_state = CorpState(faction_id=IRONCLAD, cash=0, researched={WORKER_SURVEILLANCE_ID})
    assert raise_surveillance(corp_state, corp_map, "iron_home") is False
    assert territory.modifiers[TerritoryModifier.SURVEILLANCE] == 1


def test_raise_surveillance_leaves_development_alone():
    """Development is its own purchase (raise_development), not re-derived from the
    levers the way corpmap._development() does at generation time."""
    corp_map = _map()
    territory = _corp_territory(corp_map, "iron_home", security=4, surveillance=1, development=1)
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, researched={WORKER_SURVEILLANCE_ID})
    assert raise_surveillance(corp_state, corp_map, "iron_home") is True
    assert territory.modifiers[TerritoryModifier.DEVELOPMENT] == 1


# --- Development, gated on Security + Surveillance ----------------------------


def test_development_targets_require_both_thresholds():
    corp_map = _map()
    _corp_territory(
        corp_map, "iron_home", security=DEVELOPMENT_MIN_SECURITY, surveillance=DEVELOPMENT_MIN_SURVEILLANCE
    )
    # Watched enough, but not policed enough.
    _corp_territory(
        corp_map, "iron_second", security=DEVELOPMENT_MIN_SECURITY - 1, surveillance=MODIFIER_MAX
    )
    corp_state = CorpState(faction_id=IRONCLAD)
    assert {t.id for t in development_targets(corp_state, corp_map)} == {"iron_home"}


def test_raise_development_bumps_one_level_and_charges_cash():
    corp_map = _map()
    territory = _corp_territory(
        corp_map,
        "iron_home",
        security=DEVELOPMENT_MIN_SECURITY,
        surveillance=DEVELOPMENT_MIN_SURVEILLANCE,
        development=1,
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert raise_development(corp_state, corp_map, "iron_home") is True
    assert territory.modifiers[TerritoryModifier.DEVELOPMENT] == 2
    assert corp_state.cash == 10_000 - DEVELOPMENT_BUMP_COST
    assert corp_state.daily_action_used is False


def test_raise_development_needs_no_technology():
    """A district seeded well enough can be built up from day one -- Worker
    Surveillance is only the route for one that wasn't."""
    corp_map = _map()
    _corp_territory(
        corp_map, "iron_home", security=DEVELOPMENT_MIN_SECURITY, surveillance=DEVELOPMENT_MIN_SURVEILLANCE
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert corp_state.researched == set()
    assert raise_development(corp_state, corp_map, "iron_home") is True


def test_raise_development_refuses_below_threshold_and_when_maxed():
    corp_map = _map()
    _corp_territory(corp_map, "iron_home", security=0, surveillance=0, development=0)
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert raise_development(corp_state, corp_map, "iron_home") is False

    _corp_territory(
        corp_map,
        "iron_home",
        security=MODIFIER_MAX,
        surveillance=MODIFIER_MAX,
        development=MODIFIER_MAX,
    )
    assert raise_development(corp_state, corp_map, "iron_home") is False
    assert corp_state.cash == 10_000


def test_surveillance_unlocks_development_on_a_poorly_seeded_district():
    """The chain the tech exists for: a policed but unwatched district can't be
    developed until Worker Surveillance raises its Surveillance to the threshold."""
    corp_map = _map()
    territory = _corp_territory(
        corp_map,
        "iron_home",
        security=DEVELOPMENT_MIN_SECURITY,
        surveillance=DEVELOPMENT_MIN_SURVEILLANCE - 1,
        development=0,
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=100_000, researched={WORKER_SURVEILLANCE_ID})
    assert development_targets(corp_state, corp_map) == []

    assert raise_surveillance(corp_state, corp_map, "iron_home") is True
    assert {t.id for t in development_targets(corp_state, corp_map)} == {"iron_home"}
    assert raise_development(corp_state, corp_map, "iron_home") is True
    assert territory.modifiers[TerritoryModifier.DEVELOPMENT] == 1
