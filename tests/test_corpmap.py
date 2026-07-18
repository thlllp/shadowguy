"""Invariant tests for corpmap.generate_corp_map, run over many seeds.

Mirrors CLAUDE.md's own prescribed verification style for generators: a map that
merely *looks* plausible can still be unfair, disconnected, or violate a guard the
generator's docstrings promise. These assert the guarantees documented in corpmap.py
hold across a broad seed sample, not just "it doesn't crash."
"""

import random
from collections import Counter, deque

import pytest

from shadowguy.corpmap import (
    FACTION_VALUE_SPREAD,
    GANG_TURF_MAX,
    GANG_TURF_MIN,
    MIN_START_DEGREE,
    MODIFIER_MAX,
    TERRITORIES_PER_FACTION,
    TERRITORY_COUNT,
    Territory,
    TerritoryModifier,
    claim_territory,
    generate_corp_map,
    has_home,
)
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.gangs import GANG_RANKS, GANGS, GANGS_BY_ID

SEEDS = range(200)


def _maps():
    for seed in SEEDS:
        yield generate_corp_map(FACTIONS, random.Random(seed))


@pytest.mark.parametrize("seed", SEEDS)
def test_map_has_exactly_territory_count_territories(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    assert len(corp_map.territories) == TERRITORY_COUNT


@pytest.mark.parametrize("seed", SEEDS)
def test_map_is_fully_connected(seed):
    """Every territory must be reachable from every other -- generate_corp_map's
    spanning-tree guarantee, checked by BFS rather than trusted."""
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
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
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    for tid, territory in corp_map.territories.items():
        for other in territory.connections:
            assert tid in corp_map.territories[other].connections


@pytest.mark.parametrize("seed", SEEDS)
def test_every_faction_holds_equal_territory_count(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    counts = Counter(t.owner for t in corp_map.territories.values())
    for faction in FACTIONS:
        assert counts[faction.id] == TERRITORIES_PER_FACTION


@pytest.mark.parametrize("seed", SEEDS)
def test_every_faction_holds_equal_total_value(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    totals = {faction.id: 0 for faction in FACTIONS}
    for territory in corp_map.territories.values():
        if territory.owner in totals:
            totals[territory.owner] += territory.value
    expected = sum(FACTION_VALUE_SPREAD)
    assert all(total == expected for total in totals.values())


@pytest.mark.parametrize("seed", SEEDS)
def test_every_gang_holds_turf_in_range(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    counts = Counter(t.gang_id for t in corp_map.territories.values() if t.gang_id)
    for gang in GANGS:
        assert GANG_TURF_MIN <= counts[gang.id] <= GANG_TURF_MAX


@pytest.mark.parametrize("seed", SEEDS)
def test_gang_turf_is_unclaimed_and_never_the_start(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    for territory in corp_map.territories.values():
        if territory.gang_id:
            assert territory.owner == "neutral"
            assert territory.id != corp_map.player_start_id


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_is_neutral(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    start = corp_map.territories[corp_map.player_start_id]
    assert start.owner == "neutral"
    assert start.owner not in FACTIONS_BY_ID


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_has_minimum_degree(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    start = corp_map.territories[corp_map.player_start_id]
    assert len(start.connections) >= MIN_START_DEGREE


@pytest.mark.parametrize("seed", SEEDS)
def test_player_start_has_apartment(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    start = corp_map.territories[corp_map.player_start_id]
    assert has_home(start)


@pytest.mark.parametrize("seed", SEEDS)
def test_each_gang_has_exactly_one_den_on_its_own_turf(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
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
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
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


@pytest.mark.parametrize("seed", SEEDS)
def test_each_faction_has_exactly_one_hq(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    hq_owners = [
        territory.owner
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.kind == "corp_hq"
    ]
    assert Counter(hq_owners) == {faction.id: 1 for faction in FACTIONS}


@pytest.mark.parametrize("seed", SEEDS)
def test_location_ids_are_unique_across_the_map(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    ids = [loc.id for t in corp_map.territories.values() for loc in t.locations]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("seed", SEEDS)
def test_location_names_are_unique_across_the_map(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    names = [loc.name for t in corp_map.territories.values() for loc in t.locations]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("seed", SEEDS)
def test_local_character_ids_are_unique_across_the_map(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    ids = [char.id for _loc, char in corp_map.characters()]
    assert len(ids) == len(set(ids))


def test_generate_corp_map_raises_if_factions_dont_fit():
    """generate_corp_map's own guard: too many factions for the territory count."""
    too_many = FACTIONS + [FACTIONS[0]] * 10
    with pytest.raises(ValueError):
        generate_corp_map(too_many, random.Random(0))


def test_claim_territory_flips_owner_and_reseeds_modifiers():
    """claim_territory (rivals.py's expansion mutator) must overwrite the neutral
    modifier profile with a corp-shaped one, not just flip owner."""
    territory = Territory(
        id="t1",
        name="Testville",
        x=0,
        y=0,
        owner="neutral",
        value=2,
        modifiers={
            TerritoryModifier.SECURITY: 1,
            TerritoryModifier.SURVEILLANCE: 0,
            TerritoryModifier.UNREST: MODIFIER_MAX,
            TerritoryModifier.DEVELOPMENT: 1,
            TerritoryModifier.RESTRICTED: 0,
        },
        gang_id="gang_test",
    )
    claim_territory(territory, "faction_ironclad", random.Random(0))
    assert territory.owner == "faction_ironclad"
    assert territory.gang_id is None
    assert territory.value == 2  # left as-is
    # Corp-shaped modifiers: Restricted is squeezed (2..MODIFIER_MAX), unlike
    # neutral ground's flat 0 — the clearest tell the profile actually changed.
    assert territory.modifiers[TerritoryModifier.RESTRICTED] >= 2
