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
    MIN_START_DEGREE,
    TERRITORIES_PER_FACTION,
    TERRITORY_COUNT,
    generate_corp_map,
    has_home,
)
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID

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
