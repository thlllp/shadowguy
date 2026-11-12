"""The generated town-square population: deterministic rosters, schedule, named set."""

import random

from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.npcs import (
    NAMED_TEMPLATES,
    NpcKind,
    named_assignment,
    named_npcs,
    npcs_for_territory,
    standard_npcs,
)


def _map(seed=0):
    return generate_corp_map(FACTIONS, random.Random(seed))


def test_standard_roster_is_deterministic_and_uniform():
    corp_map = _map()
    territory = next(iter(corp_map.territories.values()))
    first = standard_npcs(territory, seed=7)
    second = standard_npcs(territory, seed=7)
    assert first == second
    assert 2 <= len(first) <= 4
    assert all(npc.id.startswith(f"npc:{territory.id}:") for npc in first)
    assert all(npc.kind is not NpcKind.NAMED for npc in first)


def test_named_assignment_is_unique_and_includes_the_start_district():
    corp_map = _map()
    assignment = named_assignment(corp_map, seed=7)
    assert len(assignment) == len(NAMED_TEMPLATES)
    names = [template.name for template in assignment.values()]
    assert len(set(names)) == len(names)
    assert corp_map.player_start_id in assignment
    start = corp_map.territories[corp_map.player_start_id]
    named = named_npcs(start, seed=7, corp_map=corp_map)
    assert len(named) == 1
    assert named[0].kind is NpcKind.NAMED
    assert named[0].special


def test_named_npcs_are_only_at_assigned_districts():
    corp_map = _map()
    assignment = named_assignment(corp_map, seed=7)
    for territory in list(corp_map.territories.values())[:20]:
        expected = 1 if territory.id in assignment else 0
        assert len(named_npcs(territory, seed=7, corp_map=corp_map)) == expected


def test_standard_npcs_keep_a_day_and_night_schedule():
    corp_map = _map()
    territory = next(iter(corp_map.territories.values()))
    roster = standard_npcs(territory, seed=7)
    for npc in roster:
        if npc.kind in (NpcKind.PUNK, NpcKind.DRUNK):
            assert npc.is_present(23)
            assert not npc.is_present(12)
        else:
            assert npc.is_present(12)
            assert not npc.is_present(23)


def test_npcs_for_territory_returns_standard_plus_named_at_the_start():
    corp_map = _map()
    start = corp_map.territories[corp_map.player_start_id]
    roster = npcs_for_territory(start, seed=7, corp_map=corp_map)
    kinds = {npc.kind for npc in roster}
    assert NpcKind.NAMED in kinds
    assert any(kind is not NpcKind.NAMED for kind in kinds)
