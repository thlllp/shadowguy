"""Invariant tests for corpmap_gen.generate_corp_map, run over many seeds.

Mirrors CLAUDE.md's own prescribed verification style for generators: a map that
merely *looks* plausible can still be unfair, disconnected, or violate a guard the
generator's docstrings promise. These assert the guarantees documented in
corpmap_gen.py hold across a broad seed sample, not just "it doesn't crash."
"""

import functools
import random
from collections import Counter, deque

import pytest

from shadowguy.corpmap import (
    MODIFIER_MAX,
    STARTING_ACADEMY_TIER,
    STARTING_RESEARCH_TIER,
    LocationKind,
    has_home,
)
from shadowguy.corpmap_gen import (
    AMYS_PLACE_ROLE,
    DOCKS_ROLE,
    FACTION_VALUE_SPREAD,
    GANG_TURF_MAX,
    GANG_TURF_MIN,
    JUNKYARD_ROLE,
    MIN_START_DEGREE,
    OUTSKIRTS_COUNT,
    PARK_COUNT,
    SLUM_COUNT,
    SPECIAL_BARS,
    TERRITORIES_PER_FACTION,
    TERRITORY_COUNT,
    TILES_PER_DOCKS,
    TILES_PER_JUNKYARD,
    generate_corp_map,
)
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.gangs import GANG_RANKS, GANGS, GANGS_BY_ID

SEEDS = range(200)


@functools.lru_cache(maxsize=None)
def _generated_map(seed: int):
    """One map per seed, shared by every test below.

    Kept at CLAUDE.md's documented 200 seeds even after the board went to
    TERRITORY_COUNT 260 (which made a map ~4x more expensive to build): 40+ tests
    assert against the same seed and none of them mutate the map, so generating it
    once per seed instead of once per test is what pays for the width.
    """
    return generate_corp_map(FACTIONS, random.Random(seed))


@pytest.mark.parametrize("seed", SEEDS)
def test_map_has_exactly_territory_count_territories(seed):
    corp_map = _generated_map(seed)
    assert len(corp_map.territories) == TERRITORY_COUNT


@pytest.mark.parametrize("seed", SEEDS)
def test_map_is_fully_connected(seed):
    """Every territory must be reachable from every other -- generate_corp_map's
    spanning-tree guarantee, checked by BFS rather than trusted."""
    corp_map = _generated_map(seed)
    territories = corp_map.territories
    start = next(iter(territories))
    seen = {start}
    queue = deque([start])
    while queue:
        tid = queue.popleft()
        for neighbor in territories[tid].connections:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    assert seen == set(territories)


@pytest.mark.parametrize("seed", SEEDS)
def test_connections_are_symmetric(seed):
    corp_map = _generated_map(seed)
    for tid, territory in corp_map.territories.items():
        for other in territory.connections:
            assert tid in corp_map.territories[other].connections


@pytest.mark.parametrize("seed", SEEDS)
def test_every_faction_holds_equal_territory_count(seed):
    corp_map = _generated_map(seed)
    counts = Counter(t.owner for t in corp_map.territories.values())
    for faction in FACTIONS:
        assert counts[faction.id] == TERRITORIES_PER_FACTION


@pytest.mark.parametrize("seed", SEEDS)
def test_every_faction_holds_equal_total_value(seed):
    corp_map = _generated_map(seed)
    totals = {faction.id: 0 for faction in FACTIONS}
    for territory in corp_map.territories.values():
        if territory.owner in totals:
            totals[territory.owner] += territory.value
    expected = sum(FACTION_VALUE_SPREAD)
    assert all(total == expected for total in totals.values())


@pytest.mark.parametrize("seed", SEEDS)
def test_every_gang_holds_turf_in_range(seed):
    corp_map = _generated_map(seed)
    counts = Counter(t.gang_id for t in corp_map.territories.values() if t.gang_id)
    for gang in GANGS:
        assert GANG_TURF_MIN <= counts[gang.id] <= GANG_TURF_MAX


@pytest.mark.parametrize("seed", SEEDS)
def test_gang_turf_is_unclaimed_and_never_the_start(seed):
    corp_map = _generated_map(seed)
    for territory in corp_map.territories.values():
        if territory.gang_id:
            assert territory.owner == "neutral"
            assert territory.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_is_neutral(seed):
    corp_map = _generated_map(seed)
    start = corp_map.territories[corp_map.player_start_id]
    assert start.owner == "neutral"
    assert start.owner not in FACTIONS_BY_ID


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_has_minimum_degree(seed):
    corp_map = _generated_map(seed)
    start = corp_map.territories[corp_map.player_start_id]
    assert len(start.connections) >= MIN_START_DEGREE


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_has_apartment(seed):
    corp_map = _generated_map(seed)
    start = corp_map.territories[corp_map.player_start_id]
    assert has_home(start)


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_apartment_has_a_workshop_already_built(seed):
    corp_map = _generated_map(seed)
    start = corp_map.territories[corp_map.player_start_id]
    apartment = next(loc for loc in start.locations if loc.kind == LocationKind.APARTMENT)
    assert apartment.workshop_built is True


@pytest.mark.parametrize("seed", SEEDS)
def test_each_gang_has_exactly_one_den_on_its_own_turf(seed):
    corp_map = _generated_map(seed)
    dens = {}
    for territory in corp_map.territories.values():
        for location in territory.locations:
            if location.kind == "gang_den":
                assert territory.gang_id is not None
                assert location.name == f"{GANGS_BY_ID[territory.gang_id].name} Safehouse"
                dens[territory.gang_id] = location
    assert set(dens) == {gang.id for gang in GANGS}


@pytest.mark.parametrize("seed", SEEDS)
def test_every_gang_den_is_staffed_with_both_ranks(seed):
    corp_map = _generated_map(seed)
    dens = [
        location
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == "gang_den"
    ]
    assert dens
    for den in dens:
        assert {member.role for member in den.characters} == set(GANG_RANKS)
        names = {member.name for member in den.characters}
        assert len(names) == len(den.characters)


def _junkyards(corp_map):
    return [
        (territory, location)
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.JUNKYARD
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_junkyards_are_neutral_and_never_the_start(seed):
    corp_map = _generated_map(seed)
    for territory, _location in _junkyards(corp_map):
        assert territory.owner == "neutral"
        assert territory.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_junkyard_count_matches_neutral_density(seed):
    """TILES_PER_JUNKYARD is a ratio of *unclaimed* districts, not TERRITORY_COUNT --
    checked against the map's own neutral, non-start territory count rather than a
    hardcoded number, so this stays correct if TERRITORY_COUNT/TERRITORIES_PER_FACTION
    or the faction count ever changes."""
    corp_map = _generated_map(seed)
    neutral_count = sum(
        1
        for t in corp_map.territories.values()
        if t.owner == "neutral" and t.id != corp_map.player_start_id
    )
    expected = max(1, round(neutral_count / TILES_PER_JUNKYARD))
    assert len(_junkyards(corp_map)) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_every_junkyard_has_exactly_one_scrapper(seed):
    corp_map = _generated_map(seed)
    junkyards = _junkyards(corp_map)
    assert junkyards
    for _territory, location in junkyards:
        assert [c.role for c in location.characters] == [JUNKYARD_ROLE]


def _docks(corp_map):
    return [
        (territory, location)
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.DOCKS
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_docks_are_neutral_and_never_the_start(seed):
    corp_map = _generated_map(seed)
    for territory, _location in _docks(corp_map):
        assert territory.owner == "neutral"
        assert territory.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_docks_count_matches_neutral_density(seed):
    """Same reasoning as test_junkyard_count_matches_neutral_density: checked against
    the map's own neutral, non-start territory count rather than a hardcoded number."""
    corp_map = _generated_map(seed)
    neutral_count = sum(
        1
        for t in corp_map.territories.values()
        if t.owner == "neutral" and t.id != corp_map.player_start_id
    )
    expected = max(1, round(neutral_count / TILES_PER_DOCKS))
    assert len(_docks(corp_map)) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_every_docks_has_exactly_one_angler(seed):
    corp_map = _generated_map(seed)
    docks = _docks(corp_map)
    assert docks
    for _territory, location in docks:
        assert [c.role for c in location.characters] == [DOCKS_ROLE]


@pytest.mark.parametrize("seed", SEEDS)
def test_docks_never_share_a_tile_with_a_junkyard_hospital_or_gang_den(seed):
    corp_map = _generated_map(seed)
    docks_ids = {territory.id for territory, _location in _docks(corp_map)}
    junkyard_ids = {territory.id for territory, _location in _junkyards(corp_map)}
    assert not docks_ids & junkyard_ids
    for territory_id in docks_ids:
        territory = corp_map.territories[territory_id]
        kinds = {loc.kind for loc in territory.locations}
        assert LocationKind.HOSPITAL not in kinds
        assert LocationKind.GANG_DEN not in kinds


def _amys_places(corp_map):
    return [
        (territory, location)
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.AMYS_PLACE
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_exactly_one_amys_place_on_neutral_non_start_ground(seed):
    corp_map = _generated_map(seed)
    amys_places = _amys_places(corp_map)
    assert len(amys_places) == 1
    territory, _location = amys_places[0]
    assert territory.owner == "neutral"
    assert territory.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_amys_place_has_exactly_one_fixer_character(seed):
    corp_map = _generated_map(seed)
    _territory, location = _amys_places(corp_map)[0]
    assert [c.role for c in location.characters] == [AMYS_PLACE_ROLE]


@pytest.mark.parametrize("seed", SEEDS)
def test_amys_place_never_shares_a_tile_with_a_junkyard_docks_hospital_or_gang_den(seed):
    corp_map = _generated_map(seed)
    amy_territory, _location = _amys_places(corp_map)[0]
    kinds = {loc.kind for loc in amy_territory.locations}
    assert LocationKind.JUNKYARD not in kinds
    assert LocationKind.DOCKS not in kinds
    assert LocationKind.HOSPITAL not in kinds
    assert LocationKind.GANG_DEN not in kinds


def _special_bars(corp_map):
    special_names = {name for _, name in SPECIAL_BARS}
    return [
        (territory, location)
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.BAR and location.name in special_names
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_exactly_one_of_each_special_bar_on_neutral_non_start_ground(seed):
    corp_map = _generated_map(seed)
    special_bars = _special_bars(corp_map)
    assert sorted(location.name for _territory, location in special_bars) == sorted(
        name for _, name in SPECIAL_BARS
    )
    for territory, _location in special_bars:
        assert territory.owner == "neutral"
        assert territory.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_special_bars_never_share_a_tile_with_amys_place_each_other_or_a_hospital(seed):
    corp_map = _generated_map(seed)
    amy_territory, _location = _amys_places(corp_map)[0]
    special_bars = _special_bars(corp_map)
    territory_ids = [territory.id for territory, _location in special_bars]
    assert len(set(territory_ids)) == len(territory_ids)
    for territory, _location in special_bars:
        assert territory.id != amy_territory.id
        kinds = {loc.kind for loc in territory.locations}
        assert LocationKind.HOSPITAL not in kinds
        assert LocationKind.GANG_DEN not in kinds


@pytest.mark.parametrize("seed", SEEDS)
def test_each_faction_has_exactly_one_hq(seed):
    corp_map = _generated_map(seed)
    hq_owners = [
        territory.owner
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == "corp_hq"
    ]
    assert Counter(hq_owners) == {faction.id: 1 for faction in FACTIONS}


@pytest.mark.parametrize("seed", SEEDS)
def test_each_faction_has_exactly_one_research_facility_at_starting_tier(seed):
    corp_map = _generated_map(seed)
    facilities = [
        (territory.owner, location)
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.RESEARCH_FACILITY
    ]
    assert Counter(owner for owner, _location in facilities) == {faction.id: 1 for faction in FACTIONS}
    assert all(location.research_tier == STARTING_RESEARCH_TIER for _owner, location in facilities)


@pytest.mark.parametrize("seed", SEEDS)
def test_each_faction_has_exactly_one_academy_at_starting_tier(seed):
    corp_map = _generated_map(seed)
    academies = [
        (territory.owner, location)
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == LocationKind.ACADEMY
    ]
    assert Counter(owner for owner, _location in academies) == {faction.id: 1 for faction in FACTIONS}
    assert all(location.academy_tier == STARTING_ACADEMY_TIER for _owner, location in academies)


@pytest.mark.parametrize("seed", SEEDS)
def test_hq_research_facility_and_academy_never_share_a_district(seed):
    corp_map = _generated_map(seed)
    special = {LocationKind.CORP_HQ, LocationKind.RESEARCH_FACILITY, LocationKind.ACADEMY}
    for territory in corp_map.territories.values():
        kinds = [location.kind for location in territory.locations if location.kind in special]
        assert len(kinds) <= 1


@pytest.mark.parametrize("attribute", ["id", "name"])
@pytest.mark.parametrize("seed", SEEDS)
def test_location_ids_and_names_are_unique_across_the_map(seed, attribute):
    corp_map = _generated_map(seed)
    values = [getattr(loc, attribute) for t in corp_map.territories.values() for loc in t.locations]
    assert len(values) == len(set(values))


@pytest.mark.parametrize("seed", SEEDS)
def test_local_character_ids_are_unique_across_the_map(seed):
    corp_map = _generated_map(seed)
    ids = [char.id for _loc, char in corp_map.characters()]
    assert len(ids) == len(set(ids))


def _slums(corp_map):
    return [t for t in corp_map.territories.values() if t.is_slum]


@pytest.mark.parametrize("seed", SEEDS)
def test_exactly_slum_count_slums(seed):
    corp_map = _generated_map(seed)
    assert len(_slums(corp_map)) == SLUM_COUNT


@pytest.mark.parametrize("seed", SEEDS)
def test_slums_are_neutral_non_start(seed):
    corp_map = _generated_map(seed)
    for t in _slums(corp_map):
        assert t.owner == "neutral"
        assert t.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_slums_have_no_gang_presence(seed):
    corp_map = _generated_map(seed)
    for t in _slums(corp_map):
        assert t.gang_id is None


@pytest.mark.parametrize("seed", SEEDS)
def test_slums_have_encampment_not_shops(seed):
    corp_map = _generated_map(seed)
    shop_kinds = {
        LocationKind.PAWN, LocationKind.WEAPON_SHOP, LocationKind.AUTO_DEALER,
        LocationKind.PHARMACY, LocationKind.COMPUTER_STORE, LocationKind.CYBER_CLINIC,
        LocationKind.BAR, LocationKind.REAL_ESTATE,
    }
    for t in _slums(corp_map):
        kinds = {loc.kind for loc in t.locations}
        assert LocationKind.ENCAMPMENT in kinds
        assert not kinds & shop_kinds
        assert LocationKind.HOSPITAL not in kinds
        assert LocationKind.GANG_DEN not in kinds


@pytest.mark.parametrize("seed", SEEDS)
def test_slums_never_share_with_junkyard_or_docks(seed):
    corp_map = _generated_map(seed)
    for t in _slums(corp_map):
        kinds = {loc.kind for loc in t.locations}
        assert LocationKind.JUNKYARD not in kinds
        assert LocationKind.DOCKS not in kinds
        assert LocationKind.AMYS_PLACE not in kinds


@pytest.mark.parametrize("seed", SEEDS)
def test_slums_have_zero_development_security_surveillance(seed):
    corp_map = _generated_map(seed)
    for t in _slums(corp_map):
        assert t.modifiers["development"] == 0
        assert t.modifiers["security"] == 0
        assert t.modifiers["surveillance"] == 0
        assert t.modifiers["unrest"] == MODIFIER_MAX


def _outskirts(corp_map):
    return [t for t in corp_map.territories.values() if t.is_outskirts]


@pytest.mark.parametrize("seed", SEEDS)
def test_exactly_outskirts_count_outskirts(seed):
    corp_map = _generated_map(seed)
    assert len(_outskirts(corp_map)) == OUTSKIRTS_COUNT


@pytest.mark.parametrize("seed", SEEDS)
def test_outskirts_are_neutral_non_start(seed):
    corp_map = _generated_map(seed)
    for t in _outskirts(corp_map):
        assert t.owner == "neutral"
        assert t.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_outskirts_are_on_grid_edge(seed):
    corp_map = _generated_map(seed)
    for t in _outskirts(corp_map):
        assert t.x == 0 or t.x == 13 or t.y == 0 or t.y == 23


@pytest.mark.parametrize("seed", SEEDS)
def test_outskirts_have_no_gang_presence(seed):
    corp_map = _generated_map(seed)
    for t in _outskirts(corp_map):
        assert t.gang_id is None


@pytest.mark.parametrize("seed", SEEDS)
def test_outskirts_have_zero_development_security_surveillance(seed):
    corp_map = _generated_map(seed)
    for t in _outskirts(corp_map):
        assert t.modifiers["development"] == 0
        assert t.modifiers["security"] == 0
        assert t.modifiers["surveillance"] == 0
        assert t.modifiers["unrest"] == MODIFIER_MAX


@pytest.mark.parametrize("seed", SEEDS)
def test_outskirts_never_share_with_junkyard_or_docks(seed):
    corp_map = _generated_map(seed)
    for t in _outskirts(corp_map):
        kinds = {loc.kind for loc in t.locations}
        assert LocationKind.JUNKYARD not in kinds
        assert LocationKind.DOCKS not in kinds
        assert LocationKind.AMYS_PLACE not in kinds


@pytest.mark.parametrize("seed", SEEDS)
def test_outskirts_and_slums_are_disjoint(seed):
    corp_map = _generated_map(seed)
    outskirts_ids = {t.id for t in _outskirts(corp_map)}
    slum_ids = {t.id for t in _slums(corp_map)}
    assert not outskirts_ids & slum_ids


def _parks(corp_map):
    return [t for t in corp_map.territories.values() if t.is_park]


@pytest.mark.parametrize("seed", SEEDS)
def test_exactly_park_count_parks(seed):
    corp_map = _generated_map(seed)
    assert len(_parks(corp_map)) == PARK_COUNT


@pytest.mark.parametrize("seed", SEEDS)
def test_parks_are_neutral_non_start(seed):
    corp_map = _generated_map(seed)
    for t in _parks(corp_map):
        assert t.owner == "neutral"
        assert t.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_parks_have_no_gang_presence_or_locations(seed):
    corp_map = _generated_map(seed)
    for t in _parks(corp_map):
        assert t.gang_id is None
        assert t.locations == []


@pytest.mark.parametrize("seed", SEEDS)
def test_parks_have_zero_modifiers_across_the_board(seed):
    corp_map = _generated_map(seed)
    for t in _parks(corp_map):
        assert t.modifiers["development"] == 0
        assert t.modifiers["security"] == 0
        assert t.modifiers["surveillance"] == 0
        assert t.modifiers["unrest"] == 0
        assert t.modifiers["restricted"] == 0


@pytest.mark.parametrize("seed", SEEDS)
def test_parks_never_share_with_slums_or_outskirts(seed):
    corp_map = _generated_map(seed)
    park_ids = {t.id for t in _parks(corp_map)}
    outskirts_ids = {t.id for t in _outskirts(corp_map)}
    slum_ids = {t.id for t in _slums(corp_map)}
    assert not park_ids & outskirts_ids
    assert not park_ids & slum_ids


def test_generate_corp_map_raises_if_factions_dont_fit():
    """generate_corp_map's own guard: too many factions for the territory count."""
    too_many = FACTIONS + [FACTIONS[0]] * 10
    with pytest.raises(ValueError):
        generate_corp_map(too_many, random.Random(0))
