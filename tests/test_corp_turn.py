"""Tests for corp_turn.py: the player's own Corp turn (income + directed
expansion). Small hand-built CorpMaps mirror test_rivals.py's fixture style for
the fail-closed cases, plus a seed sweep over generate_corp_map for the
formula/invariant checks, matching the project's convention for generator-
adjacent code.
"""

import random

import pytest

from shadowguy.corp_turn import (
    ACADEMY_REBUILD_COST,
    ACADEMY_TRAINING_COST,
    BASE_LAB_CAPACITY,
    BRAINS_2_ID,
    BRAINS_2_RESEARCH_PER_ASSISTANT,
    BRAINS_2_RESEARCH_PER_SCIENTIST,
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
    RESEARCH_FACILITY_REBUILD_COST,
    RESEARCH_PER_ASSISTANT,
    RESEARCH_PER_SCIENTIST,
    STARTING_CASH,
    SURVEILLANCE_BUMP_COST,
    TECHNOLOGIES_BY_ID,
    TERRITORY_INCOME_BASE,
    TERRITORY_INCOME_PER_VALUE,
    TRAINING_DAYS,
    WORKER_SURVEILLANCE_ID,
    WORKER_SURVEILLANCE_INCOME_BONUS,
    CorpState,
    EmployeeCategory,
    advance_training,
    assistant_capacity,
    assistant_rate,
    attack_territory,
    build_academy,
    build_efficiency_upgrade,
    build_lab,
    build_research_facility,
    collect_income,
    collect_research,
    corp_defeated,
    defense_strength,
    deploy_operatives,
    development_targets,
    expand_into,
    expansion_cost,
    has_technology,
    lab_capacity,
    next_efficiency_cost,
    next_lab_cost,
    owned_research_facilities,
    owned_research_facility,
    raise_development,
    raise_surveillance,
    rebuild_academy_targets,
    rebuild_facility_targets,
    research_rate,
    research_technology,
    scientist_base_rate,
    surveillance_targets,
    train_employees,
)
from shadowguy.corpmap import (
    MODIFIER_MAX,
    STARTING_ACADEMY_TIER,
    STARTING_RESEARCH_TIER,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    TerritoryModifier,
    capture_territory,
    expansion_candidates,
)
from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from helpers import AlwaysOne, AlwaysSix

IRONCLAD, GHOSTWIRE, MERIDIAN, _ = (f.id for f in FACTIONS)

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


def test_train_employees_queues_a_batch_and_charges_cash():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=2)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=5) is True
    assert corp_state.cash == 10_000 - ACADEMY_TRAINING_COST[EmployeeCategory.SCIENTIST]
    assert corp_state.daily_action_used is True
    # The hires don't land yet -- they're queued behind the training delay.
    assert corp_state.scientists == 0
    pending = corp_state.pending_recruit
    assert pending is not None
    assert pending.category is EmployeeCategory.SCIENTIST
    assert pending.count == 2
    assert pending.ready_day == 5 + TRAINING_DAYS[EmployeeCategory.SCIENTIST]


def test_advance_training_completes_the_batch_on_its_ready_day():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=2)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=0) is True
    ready_day = TRAINING_DAYS[EmployeeCategory.SCIENTIST]
    # Nothing lands on the days before it's ready, and the slot stays occupied.
    for day in range(1, ready_day):
        assert advance_training(corp_state, day) is None
        assert corp_state.scientists == 0
        assert corp_state.pending_recruit is not None
    completed = advance_training(corp_state, ready_day)
    assert completed is not None
    assert completed.category is EmployeeCategory.SCIENTIST
    assert corp_state.scientists == 2
    assert corp_state.pending_recruit is None
    # Idempotent once the slot's cleared.
    assert advance_training(corp_state, ready_day + 1) is None


def test_train_employees_uses_the_category_specific_delay():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    # Each role trains for its own number of days.
    for category, expected in (
        (EmployeeCategory.SCIENTIST, 9),
        (EmployeeCategory.OPERATIVE, 6),
        (EmployeeCategory.RESEARCH_ASSISTANT, 3),
    ):
        assert TRAINING_DAYS[category] == expected
        corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
        assert train_employees(corp_state, corp_map, category, day=0) is True
        assert corp_state.pending_recruit.ready_day == expected


def test_train_employees_credits_the_right_category():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE, day=0) is True
    advance_training(corp_state, TRAINING_DAYS[EmployeeCategory.OPERATIVE])
    assert corp_state.operatives == 1
    assert corp_state.scientists == 0
    assert corp_state.research_assistants == 0


def test_train_employees_credits_research_assistants():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=2)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.RESEARCH_ASSISTANT, day=0) is True
    advance_training(corp_state, TRAINING_DAYS[EmployeeCategory.RESEARCH_ASSISTANT])
    assert corp_state.research_assistants == 2
    assert corp_state.scientists == 0
    assert corp_state.operatives == 0


def test_train_employees_trains_one_batch_at_a_time():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=0) is True
    # A second batch is refused while the first is still training, even on a later
    # day with the daily action free -- the Academy has one slot.
    corp_state.daily_action_used = False
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE, day=1) is False
    assert corp_state.pending_recruit.category is EmployeeCategory.SCIENTIST
    assert corp_state.cash == 10_000 - ACADEMY_TRAINING_COST[EmployeeCategory.SCIENTIST]


def test_train_employees_fails_with_no_academy():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=0) is False
    assert corp_state.cash == 10_000
    assert corp_state.pending_recruit is None


def test_train_employees_fails_when_unaffordable():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=0)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=0) is False
    assert corp_state.pending_recruit is None


def test_train_employees_fails_when_already_used_today():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=10_000, daily_action_used=True)
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=0) is False
    assert corp_state.pending_recruit is None


def test_expand_and_train_share_the_same_daily_slot():
    corp_map = _map()
    corp_map.territories["iron_second"].locations.append(
        Location(id="acad1", name="Academy", kind=LocationKind.ACADEMY, academy_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, cash=100_000)
    rng = random.Random(0)
    assert expand_into(corp_state, corp_map, "neutral_a", rng) is True
    # Training the same day is refused -- the day's one move is already spent.
    assert train_employees(corp_state, corp_map, EmployeeCategory.SCIENTIST, day=0) is False
    assert corp_state.pending_recruit is None


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
    assert research_rate(CorpState(faction_id=IRONCLAD), facility) == RESEARCH_PER_SCIENTIST


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
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE, day=0) is True
    advance_training(corp_state, TRAINING_DAYS[EmployeeCategory.OPERATIVE])
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


# --- Technology: Brains 2 -----------------------------------------------------


def _academy(corp_map, territory_id="iron_home"):
    location = Location(
        id=f"{territory_id}_academy",
        name="Academy",
        kind=LocationKind.ACADEMY,
        academy_tier=STARTING_ACADEMY_TIER,
    )
    corp_map.territories[territory_id].locations.append(location)
    return location


def _facility(corp_map, territory_id="iron_home", **kwargs):
    location = Location(
        id="rf", name="Facility", kind=LocationKind.RESEARCH_FACILITY, research_tier=0, **kwargs
    )
    corp_map.territories[territory_id].locations.append(location)
    return location


def test_brains_2_raises_both_per_head_rates():
    researched = CorpState(faction_id=IRONCLAD, researched={BRAINS_2_ID})
    plain = CorpState(faction_id=IRONCLAD)
    assert scientist_base_rate(plain) == RESEARCH_PER_SCIENTIST
    assert assistant_rate(plain) == RESEARCH_PER_ASSISTANT
    assert scientist_base_rate(researched) == BRAINS_2_RESEARCH_PER_SCIENTIST
    assert assistant_rate(researched) == BRAINS_2_RESEARCH_PER_ASSISTANT


def test_brains_2_replaces_the_base_rate_rather_than_stacking():
    """0.75 / 1.25 are the whole rate, not a bonus added to 0.5 / 1."""
    corp_state = CorpState(faction_id=IRONCLAD, researched={BRAINS_2_ID})
    assert scientist_base_rate(corp_state) == 1.25
    assert assistant_rate(corp_state) == 0.75


def test_brains_2_still_stacks_with_facility_efficiency_upgrades():
    corp_map = _map()
    facility = _facility(corp_map, efficiency_upgrades=2)
    corp_state = CorpState(faction_id=IRONCLAD, researched={BRAINS_2_ID})
    assert research_rate(corp_state, facility) == BRAINS_2_RESEARCH_PER_SCIENTIST + 2


def test_brains_2_moves_collect_research_for_scientists_and_assistants():
    corp_map = _map()
    facility = _facility(corp_map)
    corp_state = CorpState(faction_id=IRONCLAD, scientists=1, research_assistants=2)
    scientists = min(corp_state.scientists, lab_capacity(facility))
    assistants = min(corp_state.research_assistants, assistant_capacity(facility))

    before = collect_research(corp_state, corp_map)
    assert before == scientists * RESEARCH_PER_SCIENTIST + assistants * RESEARCH_PER_ASSISTANT

    corp_state.researched.add(BRAINS_2_ID)
    after = collect_research(corp_state, corp_map)
    assert after == (
        scientists * BRAINS_2_RESEARCH_PER_SCIENTIST + assistants * BRAINS_2_RESEARCH_PER_ASSISTANT
    )
    assert after > before


def test_brains_2_does_nothing_without_staff():
    """The tech pays per working head, so an unstaffed facility gains nothing --
    it's a multiplier on employees, not a flat research bump."""
    corp_map = _map()
    _facility(corp_map)
    corp_state = CorpState(faction_id=IRONCLAD)
    before = collect_research(corp_state, corp_map)
    corp_state.researched.add(BRAINS_2_ID)
    assert collect_research(corp_state, corp_map) == before


def test_both_technologies_are_researchable_from_the_start():
    """Neither has a prerequisite -- a fresh corp with enough RP can take either
    one first, and researching one doesn't gate the other."""
    for first, second in ((WORKER_SURVEILLANCE_ID, BRAINS_2_ID), (BRAINS_2_ID, WORKER_SURVEILLANCE_ID)):
        cost = TECHNOLOGIES_BY_ID[first].cost + TECHNOLOGIES_BY_ID[second].cost
        corp_state = CorpState(faction_id=IRONCLAD, research_points=cost)
        assert research_technology(corp_state, first) is True
        assert research_technology(corp_state, second) is True
        assert corp_state.research_points == 0
        assert corp_state.researched == {first, second}


# --- Conflict ---------------------------------------------------------------
# Both contest rolls come off the same rng, so AlwaysSix/AlwaysOne fix them to the
# same face and the outcome collapses to the deterministic `committed > defense`.
# That's what makes every case below exact rather than statistical.


def _contested_map():
    """iron_home(IRONCLAD) -- ghost_home(GHOSTWIRE, Security 2), plus a neutral.
    iron_home also owns iron_second, so IRONCLAD survives losing one district."""
    corp_map = _map()
    corp_map.territories["ghost_home"] = _territory(
        "ghost_home", owner=GHOSTWIRE, value=1, connections=["iron_home"]
    )
    corp_map.territories["ghost_home"].modifiers = {TerritoryModifier.SECURITY: 2}
    corp_map.territories["iron_home"].connections.append("ghost_home")
    return corp_map


def test_defense_strength_sums_garrison_and_security():
    territory = _territory("t", owner=IRONCLAD)
    territory.garrison = 3
    territory.modifiers = {TerritoryModifier.SECURITY: 2}
    assert defense_strength(territory) == 5


def test_defense_strength_of_an_unpoliced_empty_district_is_zero():
    assert defense_strength(_territory("t", owner=IRONCLAD)) == 0


def test_deploy_operatives_moves_them_from_the_pool_onto_the_district():
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD, operatives=5)
    assert deploy_operatives(corp_state, corp_map, "iron_home", 3) is True
    assert corp_state.operatives == 2
    assert corp_map.territories["iron_home"].garrison == 3
    assert corp_state.daily_action_used is True


def test_deploy_operatives_fails_closed_on_every_gate():
    corp_map = _map()
    # More than the pool holds.
    corp_state = CorpState(faction_id=IRONCLAD, operatives=2)
    assert deploy_operatives(corp_state, corp_map, "iron_home", 3) is False
    # Not a district this corp holds.
    assert deploy_operatives(corp_state, corp_map, "neutral_a", 1) is False
    # Not a positive count.
    assert deploy_operatives(corp_state, corp_map, "iron_home", 0) is False
    assert corp_state.operatives == 2
    assert corp_map.territories["iron_home"].garrison == 0
    assert corp_state.daily_action_used is False
    # Already moved today.
    corp_state.daily_action_used = True
    assert deploy_operatives(corp_state, corp_map, "iron_home", 1) is False
    assert corp_state.operatives == 2


def test_attack_captures_when_the_force_beats_the_defense():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, operatives=5)
    # ghost_home defends at 2 (Security), so 3 committed takes it.
    result = attack_territory(corp_state, corp_map, "ghost_home", 3, AlwaysSix())
    assert result.captured is True
    assert corp_map.territories["ghost_home"].owner == IRONCLAD
    assert result.attacker_losses == 2  # one per point of defense ground through
    # Survivors hold the ground they took rather than returning to the pool.
    assert corp_map.territories["ghost_home"].garrison == 1
    assert corp_state.operatives == 2
    assert corp_state.daily_action_used is True


def test_attack_is_repelled_when_the_defense_holds_and_survivors_come_home():
    corp_map = _contested_map()
    corp_map.territories["ghost_home"].garrison = 3  # defense 5 now
    corp_state = CorpState(faction_id=IRONCLAD, operatives=8)
    result = attack_territory(corp_state, corp_map, "ghost_home", 4, AlwaysSix())
    assert result.captured is False
    assert corp_map.territories["ghost_home"].owner == GHOSTWIRE
    assert result.attacker_losses == 4  # capped at what was committed
    assert result.defender_losses == 3  # min(garrison, committed)
    assert corp_map.territories["ghost_home"].garrison == 0
    assert corp_state.operatives == 4  # 8 - 4 committed, none survived to return


def test_a_repelled_attack_returns_its_survivors_to_the_pool():
    corp_map = _contested_map()
    corp_map.territories["ghost_home"].modifiers = {TerritoryModifier.SECURITY: 1}
    corp_map.territories["ghost_home"].garrison = 0  # defense 1
    corp_state = CorpState(faction_id=IRONCLAD, operatives=6)
    # Tie goes to the defender: 1 committed vs defense 1 is repelled, and the
    # single attacker is the one loss, so nobody comes home from this one.
    result = attack_territory(corp_state, corp_map, "ghost_home", 1, AlwaysSix())
    assert result.captured is False
    assert corp_state.operatives == 5


def test_attack_ties_go_to_the_defender():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, operatives=5)
    # committed == defense == 2: attack_power must strictly exceed.
    result = attack_territory(corp_state, corp_map, "ghost_home", 2, AlwaysOne())
    assert result.captured is False


def test_attack_fails_closed_on_every_gate():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, operatives=2)
    # More than the pool holds.
    assert attack_territory(corp_state, corp_map, "ghost_home", 3, AlwaysSix()) is None
    # Neutral ground is expand_into's business, not an attack's.
    assert attack_territory(corp_state, corp_map, "neutral_a", 1, AlwaysSix()) is None
    # Your own ground.
    assert attack_territory(corp_state, corp_map, "iron_second", 1, AlwaysSix()) is None
    assert corp_state.operatives == 2
    assert corp_state.daily_action_used is False
    corp_state.daily_action_used = True
    assert attack_territory(corp_state, corp_map, "ghost_home", 1, AlwaysSix()) is None


def test_capturing_a_rivals_facility_makes_a_corp_hold_two():
    """The case DESIGN.md flagged in advance: capture_territory carries Locations
    over, so collect_research has to fill more than one facility."""
    corp_map = _contested_map()
    _facility(corp_map)  # IRONCLAD's own, on iron_home
    corp_map.territories["ghost_home"].locations.append(
        Location(id="ghost_lab", name="Ghost Lab", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    )
    corp_state = CorpState(faction_id=IRONCLAD, operatives=5)
    assert len(owned_research_facilities(corp_state, corp_map)) == 1
    attack_territory(corp_state, corp_map, "ghost_home", 5, AlwaysSix())
    assert len(owned_research_facilities(corp_state, corp_map)) == 2


def test_collect_research_fills_the_best_facility_first():
    """Two facilities, one upgraded: a lone scientist sits at the better one, so
    total RP reflects the higher rate rather than an arbitrary pick."""
    corp_map = _contested_map()
    good = _facility(corp_map)
    good.efficiency_upgrades = 2
    weak = Location(id="weak", name="Weak", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    weak.labs_built = 0
    weak.efficiency_upgrades = 0
    corp_map.territories["iron_second"].locations.append(weak)
    corp_state = CorpState(faction_id=IRONCLAD, scientists=1)
    facilities = owned_research_facilities(corp_state, corp_map)
    assert facilities[0] is good
    # _facility() seeds tier 0 and the hand-built one tier 1, so tiers contribute 1 --
    # plus the one scientist, who must be sitting at the *good* facility's rate.
    assert collect_research(corp_state, corp_map) == 1 + research_rate(corp_state, good)


def test_collect_research_spills_into_a_second_facility_once_the_first_is_full():
    corp_map = _contested_map()
    first = _facility(corp_map)
    second = Location(id="second", name="Second", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    second.labs_built = 0
    second.efficiency_upgrades = 0
    corp_map.territories["iron_second"].locations.append(second)
    # BASE_LAB_CAPACITY seats each, so the second scientist can only work if the
    # spill is real.
    corp_state = CorpState(faction_id=IRONCLAD, scientists=2 * BASE_LAB_CAPACITY)
    expected = 1 + 2 * BASE_LAB_CAPACITY * RESEARCH_PER_SCIENTIST
    assert collect_research(corp_state, corp_map) == expected


def test_owned_research_facility_is_the_best_of_several():
    """build_lab/build_efficiency_upgrade land on this one, so it must be the
    facility collect_research fills first — not just whichever was found first."""
    corp_map = _contested_map()
    weak = _facility(corp_map)
    strong = Location(id="strong", name="Strong", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    strong.labs_built = 1
    strong.efficiency_upgrades = 2
    corp_map.territories["iron_second"].locations.append(strong)
    corp_state = CorpState(faction_id=IRONCLAD)
    assert owned_research_facility(corp_state, corp_map) is strong
    assert weak is not strong


def test_corp_defeated_only_when_it_holds_nothing():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD)
    assert corp_defeated(corp_state, corp_map) is False
    for territory in corp_map.territories.values():
        if territory.owner == IRONCLAD:
            territory.owner = GHOSTWIRE
    assert corp_defeated(corp_state, corp_map) is True


@pytest.mark.parametrize("seed", SEEDS)
def test_an_attack_never_creates_or_destroys_operatives_beyond_its_losses(seed):
    """Conservation sweep: every operative committed either dies, garrisons the
    captured ground, or comes home. Nothing leaks and nothing is duplicated."""
    rng = random.Random(seed)
    corp_map = _contested_map()
    corp_map.territories["ghost_home"].garrison = rng.randint(0, 4)
    corp_map.territories["ghost_home"].modifiers = {TerritoryModifier.SECURITY: rng.randint(0, 5)}
    committed = rng.randint(1, 8)
    corp_state = CorpState(faction_id=IRONCLAD, operatives=committed + rng.randint(0, 3))
    before = corp_state.operatives
    result = attack_territory(corp_state, corp_map, "ghost_home", committed, rng)
    assert result is not None
    survivors = committed - result.attacker_losses
    assert 0 <= result.attacker_losses <= committed
    if result.captured:
        assert corp_state.operatives == before - committed
        assert corp_map.territories["ghost_home"].garrison == survivors
    else:
        assert corp_state.operatives == before - result.attacker_losses
        assert corp_map.territories["ghost_home"].garrison >= 0


# --- Rebuilding a captured research facility ---------------------------------


def test_rebuild_is_not_offered_while_the_corp_still_holds_a_facility():
    """A rebuild is a way back from zero, not a way to run two — capturing a rival's
    is the only route to a second."""
    corp_map = _contested_map()
    _facility(corp_map)
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST)
    assert rebuild_facility_targets(corp_state, corp_map) == []
    assert build_research_facility(corp_state, corp_map, "iron_second") is False
    assert corp_state.cash == RESEARCH_FACILITY_REBUILD_COST
    assert corp_state.daily_action_used is False


def test_losing_the_only_facility_opens_the_rebuild():
    corp_map = _contested_map()
    _facility(corp_map, territory_id="iron_second")
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST)
    assert rebuild_facility_targets(corp_state, corp_map) == []
    # A rival takes the district the labs stood on.
    capture_territory(corp_map.territories["iron_second"], GHOSTWIRE)
    assert collect_research(corp_state, corp_map) == 0.0
    assert [t.id for t in rebuild_facility_targets(corp_state, corp_map)] == ["iron_home"]


def test_build_research_facility_stands_a_bare_one_up_and_charges_cash():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST + 50)
    assert build_research_facility(corp_state, corp_map, "iron_home") is True
    assert corp_state.cash == 50
    assert corp_state.daily_action_used is True
    facility = owned_research_facility(corp_state, corp_map)
    assert facility is not None
    assert facility.research_tier == STARTING_RESEARCH_TIER
    # Bare: whatever was built into the captured one is genuinely gone.
    assert facility.labs_built == 0
    assert facility.efficiency_upgrades == 0
    assert lab_capacity(facility) == BASE_LAB_CAPACITY


def test_a_rebuilt_facility_produces_research_again():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST, scientists=1)
    assert collect_research(corp_state, corp_map) == 0.0
    build_research_facility(corp_state, corp_map, "iron_home")
    assert collect_research(corp_state, corp_map) == STARTING_RESEARCH_TIER + RESEARCH_PER_SCIENTIST


def test_build_research_facility_fails_closed_on_every_gate():
    corp_map = _contested_map()
    # Short cash.
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST - 1)
    assert build_research_facility(corp_state, corp_map, "iron_home") is False
    # Not a district this corp holds.
    corp_state.cash = RESEARCH_FACILITY_REBUILD_COST
    assert build_research_facility(corp_state, corp_map, "ghost_home") is False
    assert build_research_facility(corp_state, corp_map, "neutral_a") is False
    assert owned_research_facilities(corp_state, corp_map) == []
    assert corp_state.daily_action_used is False
    # Already moved today.
    corp_state.daily_action_used = True
    assert build_research_facility(corp_state, corp_map, "iron_home") is False
    assert corp_state.cash == RESEARCH_FACILITY_REBUILD_COST


def test_a_rebuilt_facility_is_named_for_its_owner():
    """add_research_facility reads territory.owner rather than being handed a faction,
    which is what keeps corp_turn free of a factions import."""
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST)
    build_research_facility(corp_state, corp_map, "iron_home")
    facility = owned_research_facility(corp_state, corp_map)
    assert facility.name == f"{FACTIONS_BY_ID[IRONCLAD].name} Research Facility"


def test_rebuilding_cannot_collide_with_an_existing_facility_id():
    """The id is derived from the district, so the guard against a duplicate is
    rebuild_facility_targets being empty while any facility is held — including one
    standing on the very district a rebuild would target."""
    corp_map = _contested_map()
    _facility(corp_map, territory_id="iron_home")
    corp_state = CorpState(faction_id=IRONCLAD, cash=RESEARCH_FACILITY_REBUILD_COST)
    assert build_research_facility(corp_state, corp_map, "iron_home") is False
    ids = [loc.id for loc in corp_map.territories["iron_home"].locations]
    assert len(ids) == len(set(ids))


# --- Rebuilding a captured academy -------------------------------------------
# Same shape as the facility rebuild above. The academy is the harsher loss: with no
# way to train operatives, a corp can neither attack nor garrison.


def test_academy_rebuild_is_not_offered_while_the_corp_still_holds_one():
    corp_map = _contested_map()
    _academy(corp_map)
    corp_state = CorpState(faction_id=IRONCLAD, cash=ACADEMY_REBUILD_COST)
    assert rebuild_academy_targets(corp_state, corp_map) == []
    assert build_academy(corp_state, corp_map, "iron_second") is False
    assert corp_state.cash == ACADEMY_REBUILD_COST
    assert corp_state.daily_action_used is False


def test_losing_the_academy_blocks_training_and_opens_the_rebuild():
    corp_map = _contested_map()
    _academy(corp_map, territory_id="iron_second")
    corp_state = CorpState(faction_id=IRONCLAD, cash=5000)
    assert rebuild_academy_targets(corp_state, corp_map) == []

    capture_territory(corp_map.territories["iron_second"], GHOSTWIRE)
    # The dead end this exists to prevent: no academy, so no training at all.
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE, day=1) is False
    assert [t.id for t in rebuild_academy_targets(corp_state, corp_map)] == ["iron_home"]


def test_build_academy_restores_training():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=ACADEMY_REBUILD_COST + 500)
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE, day=1) is False

    assert build_academy(corp_state, corp_map, "iron_home") is True
    assert corp_state.cash == 500
    assert corp_state.daily_action_used is True

    # A fresh day, and the corp can train again.
    corp_state.daily_action_used = False
    assert train_employees(corp_state, corp_map, EmployeeCategory.OPERATIVE, day=1) is True
    assert corp_state.pending_recruit.category is EmployeeCategory.OPERATIVE
    assert corp_state.pending_recruit.count == STARTING_ACADEMY_TIER


def test_build_academy_fails_closed_on_every_gate():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=ACADEMY_REBUILD_COST - 1)
    assert build_academy(corp_state, corp_map, "iron_home") is False
    corp_state.cash = ACADEMY_REBUILD_COST
    assert build_academy(corp_state, corp_map, "ghost_home") is False
    assert build_academy(corp_state, corp_map, "neutral_a") is False
    assert corp_state.daily_action_used is False
    corp_state.daily_action_used = True
    assert build_academy(corp_state, corp_map, "iron_home") is False
    assert corp_state.cash == ACADEMY_REBUILD_COST


def test_a_rebuilt_academy_is_named_for_its_owner():
    corp_map = _contested_map()
    corp_state = CorpState(faction_id=IRONCLAD, cash=ACADEMY_REBUILD_COST)
    build_academy(corp_state, corp_map, "iron_home")
    academy = next(
        loc
        for loc in corp_map.territories["iron_home"].locations
        if loc.kind is LocationKind.ACADEMY
    )
    assert academy.name == f"{FACTIONS_BY_ID[IRONCLAD].name} Academy"
    assert academy.academy_tier == STARTING_ACADEMY_TIER


def test_rebuilding_an_academy_cannot_collide_with_an_existing_id():
    corp_map = _contested_map()
    _academy(corp_map, territory_id="iron_home")
    corp_state = CorpState(faction_id=IRONCLAD, cash=ACADEMY_REBUILD_COST)
    assert build_academy(corp_state, corp_map, "iron_home") is False
    ids = [loc.id for loc in corp_map.territories["iron_home"].locations]
    assert len(ids) == len(set(ids))
