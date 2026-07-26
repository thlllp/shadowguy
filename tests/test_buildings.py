"""Tests for buildings.py: floorplan invariants for the room/level generator.

Seed-swept invariants rather than exact layouts -- a generator is only worth testing on
the properties every roll must hold (every room reachable, every entrance able to get to
the objective, nothing stacked on anything else), since the specific rooms are the point
of it being generated.
"""

import random

import pytest

from shadowguy.buildings import (
    BUILDING_PROFILES,
    LEVEL_PROGRAMS,
    OBJECTIVE_ROOMS,
    RESIDENTIAL_BATHROOMS,
    RESIDENTIAL_BEDROOMS,
    ROOM_LABELS,
    BuildingKind,
    RoomKind,
    _connected,
    generate_building,
)
from shadowguy.tactical import Tile, step_neighbors

SEEDS = range(80)


@pytest.mark.parametrize("seed", SEEDS)
def test_every_level_is_its_profiles_size(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    profile = BUILDING_PROFILES[building.kind]
    for level in building.levels:
        assert level.grid.width == profile.width
        assert level.grid.height == profile.height


@pytest.mark.parametrize("seed", SEEDS)
def test_a_residential_building_is_a_basement_a_ground_floor_and_upstairs(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    assert [level.name for level in building.levels] == ["Basement", "Ground floor", "Upstairs"]


@pytest.mark.parametrize("seed", SEEDS)
def test_a_house_has_the_rooms_a_house_has(seed):
    """The program is what a residential building promises: bedrooms and a bathroom
    upstairs, the living half downstairs, and the counts inside their configured range."""
    building = generate_building(random.Random(seed), entrance_count=3)
    kinds = [room.kind for level in building.levels for room in level.rooms]
    low, high = RESIDENTIAL_BEDROOMS
    assert low <= kinds.count(RoomKind.BEDROOM) <= high
    low, high = RESIDENTIAL_BATHROOMS
    assert low <= kinds.count(RoomKind.BATHROOM) <= high
    for required in (RoomKind.KITCHEN, RoomKind.LIVING, RoomKind.DINING, RoomKind.BASEMENT):
        assert required in kinds


@pytest.mark.parametrize("seed", SEEDS)
def test_every_room_on_a_level_is_reachable_from_every_other(seed):
    """The doors actually connect the floorplan -- no room walled off behind another."""
    building = generate_building(random.Random(seed), entrance_count=3)
    for level in building.levels:
        first = level.rooms[0].center
        for room in level.rooms:
            assert _connected(level.grid, first, room.center), f"{level.name}: {room.label()} is sealed off"


@pytest.mark.parametrize("seed", SEEDS)
def test_rooms_never_overlap_and_sit_inside_the_walls(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    for level in building.levels:
        seen = set()
        for room in level.rooms:
            for cell in room.cells:
                assert cell not in seen, f"{level.name}: rooms overlap at {cell}"
                seen.add(cell)
                x, y = cell
                assert 0 < x < level.grid.width - 1 and 0 < y < level.grid.height - 1


@pytest.mark.parametrize("seed", SEEDS)
def test_every_placed_thing_is_on_walkable_floor_and_in_a_room(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    placed = [*building.entrance_spawns, building.objective, *building.guards]
    for level_index, coord in placed:
        level = building.level(level_index)
        assert level.grid.tile(coord) is Tile.FLOOR
        assert level.room_at(coord) is not None


@pytest.mark.parametrize("seed", SEEDS)
def test_nothing_is_placed_on_top_of_anything_else(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    placed = [*building.entrance_spawns, building.objective, *building.guards]
    assert len(placed) == len(set(placed))


@pytest.mark.parametrize("seed", SEEDS)
def test_entrances_are_all_on_the_ground_floor(seed):
    """You come in at street level and find your own way up or down."""
    building = generate_building(random.Random(seed), entrance_count=3)
    ground = [i for i, level in enumerate(building.levels) if level.name == "Ground floor"][0]
    assert {level for level, _coord in building.entrance_spawns} == {ground}


@pytest.mark.parametrize("seed", SEEDS)
def test_the_objective_sits_somewhere_valuables_plausibly_live(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    level_index, coord = building.objective
    room = building.level(level_index).room_at(coord)
    assert room.kind in OBJECTIVE_ROOMS, f"objective ended up in a {room.label()}"


@pytest.mark.parametrize("seed", SEEDS)
def test_levels_are_linked_in_a_chain_and_the_links_are_two_sided(seed):
    building = generate_building(random.Random(seed), entrance_count=3)
    assert len(building.links) == len(building.levels) - 1
    for link in building.links:
        assert building.links_at(*link.a[:1], link.a[1]) == link.b
        assert building.links_at(*link.b[:1], link.b[1]) == link.a


@pytest.mark.parametrize("seed", SEEDS)
def test_every_entrance_can_reach_the_objective_across_levels(seed):
    """The invariant the generator re-rolls to guarantee: a burglary is never handed a
    building whose objective is behind a floor you can't get to.

    Deliberately floods raw walkable cells rather than reusing the generator's own
    room-centre graph -- an independent check, so a bug in that graph can't hide here."""
    building = generate_building(random.Random(seed), entrance_count=3)
    for spawn in building.entrance_spawns:
        seen, frontier = set(), [spawn]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            level_index, coord = current
            grid = building.level(level_index).grid
            side = building.links_at(level_index, coord)
            if side is not None:
                frontier.append(side)
            frontier += [
                (level_index, step)
                for step in step_neighbors(grid, coord)
                if (level_index, step) not in seen
            ]
        assert building.objective in seen, f"{spawn} can't reach the objective"


def test_every_kind_has_a_profile_and_a_level_program():
    """The two tables a new BuildingKind has to be added to -- both are import-guarded,
    so this is really asserting the guards are still doing their job."""
    assert set(BUILDING_PROFILES) == set(BuildingKind)
    assert set(LEVEL_PROGRAMS) == set(BuildingKind)
    assert set(ROOM_LABELS) == set(RoomKind)


def test_generate_building_raises_rather_than_hand_back_an_unwalkable_house():
    """Asking for more entrances than the ground floor has rooms is unsatisfiable, and
    the generator says so instead of returning something half-built."""
    with pytest.raises(RuntimeError):
        generate_building(random.Random(0), entrance_count=99)
