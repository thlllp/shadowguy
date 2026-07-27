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
# The invariants below hold for any sort of building, so every kind gets swept through
# them -- a new BUILDING_PROFILES row is covered the day it's added, not the day someone
# remembers to write it a test.
KINDS = list(BuildingKind)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_level_is_its_profiles_size(seed, kind):
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
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
def test_an_office_stacks_2_to_4_storeys_with_reception_at_street_level(seed):
    """The office program's promise: no basement -- the prize is on a desk or in a server
    closet, not under the building -- and the reception you'd have to walk past is on the
    ground floor rather than somewhere upstairs."""
    building = generate_building(random.Random(seed), entrance_count=3, kind=BuildingKind.OFFICE)
    names = [level.name for level in building.levels]
    assert 2 <= len(names) <= 4
    assert names[0] == "Ground floor"
    assert len(set(names)) == len(names), f"two levels share a name: {names}"
    ground = building.levels[0]
    assert RoomKind.RECEPTION in [room.kind for room in ground.rooms]
    kinds = [room.kind for level in building.levels for room in level.rooms]
    assert RoomKind.BASEMENT not in kinds
    assert kinds.count(RoomKind.OFFICE) >= 2


@pytest.mark.parametrize("seed", SEEDS)
def test_a_compound_is_two_storeys_with_an_optional_basement(seed):
    """The compound program's promise: always a ground floor and an upper floor -- never
    OFFICE's stack of up to four -- with a basement underneath being the only thing that's
    a coin flip."""
    building = generate_building(random.Random(seed), entrance_count=3, kind=BuildingKind.COMPOUND)
    names = [level.name for level in building.levels]
    assert names[-2:] == ["Ground floor", "Upper floor"]
    assert len(names) in (2, 3)
    if len(names) == 3:
        assert names[0] == "Basement"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_room_on_a_level_is_reachable_from_every_other(seed, kind):
    """The doors actually connect the floorplan -- no room walled off behind another."""
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    for level in building.levels:
        first = level.rooms[0].center
        for room in level.rooms:
            assert _connected(level.grid, first, room.center), f"{level.name}: {room.label()} is sealed off"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_rooms_never_overlap_and_sit_inside_the_walls(seed, kind):
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    for level in building.levels:
        seen = set()
        for room in level.rooms:
            for cell in room.cells:
                assert cell not in seen, f"{level.name}: rooms overlap at {cell}"
                seen.add(cell)
                x, y = cell
                assert 0 < x < level.grid.width - 1 and 0 < y < level.grid.height - 1


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_placed_thing_is_on_walkable_floor_and_in_a_room(seed, kind):
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    placed = [*building.entrance_spawns, building.objective, *building.guards]
    for level_index, coord in placed:
        level = building.level(level_index)
        assert level.grid.tile(coord) is Tile.FLOOR
        assert level.room_at(coord) is not None


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_nothing_is_placed_on_top_of_anything_else(seed, kind):
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    placed = [*building.entrance_spawns, building.objective, *building.guards]
    assert len(placed) == len(set(placed))


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_entrances_are_all_on_the_ground_floor(seed, kind):
    """You come in at street level and find your own way up or down."""
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    ground = [i for i, level in enumerate(building.levels) if level.name == "Ground floor"][0]
    assert {level for level, _coord in building.entrance_spawns} == {ground}


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_the_objective_sits_somewhere_valuables_plausibly_live(seed, kind):
    """Against *this* kind's own objective_rooms, not the union across every kind -- the
    union would happily pass an office whose prize turned up in somebody's bedroom."""
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    level_index, coord = building.objective
    room = building.level(level_index).room_at(coord)
    wanted = BUILDING_PROFILES[kind].objective_rooms
    assert room.kind in wanted, f"{kind} objective ended up in a {room.label()}"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_guards_stand_in_rooms_people_actually_stand_in(seed, kind):
    """The other half of what the profile's room preferences buy: a watcher posted in the
    circulation you have to cross, not sat in a bathroom.

    Placement is allowed to fall back to any free room, so this is strictly stronger than
    the generator's contract -- deliberately. It asserts every profile is wide enough to
    seat its own guard count without the safety valve ever firing, which is exactly the
    bug it caught: two guards against three sometimes-absent rooms put ~9% of office
    guards in a bathroom or a storage closet."""
    profile = BUILDING_PROFILES[kind]
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    assert len(building.guards) == profile.guards
    for level_index, coord in building.guards:
        room = building.level(level_index).room_at(coord)
        assert room.kind in profile.guard_rooms, f"{kind} guard posted in a {room.label()}"


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_levels_are_linked_in_a_chain_and_the_links_are_two_sided(seed, kind):
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
    assert len(building.links) == len(building.levels) - 1
    for link in building.links:
        assert building.links_at(*link.a[:1], link.a[1]) == link.b
        assert building.links_at(*link.b[:1], link.b[1]) == link.a


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("seed", SEEDS)
def test_every_entrance_can_reach_the_objective_across_levels(seed, kind):
    """The invariant the generator re-rolls to guarantee: a burglary is never handed a
    building whose objective is behind a floor you can't get to.

    Deliberately floods raw walkable cells rather than reusing the generator's own
    room-centre graph -- an independent check, so a bug in that graph can't hide here."""
    building = generate_building(random.Random(seed), entrance_count=3, kind=kind)
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


@pytest.mark.parametrize("kind", KINDS)
def test_generate_building_raises_rather_than_hand_back_an_unwalkable_house(kind):
    """Asking for more entrances than the ground floor has rooms is unsatisfiable, and
    the generator says so instead of returning something half-built."""
    with pytest.raises(RuntimeError):
        generate_building(random.Random(0), entrance_count=99, kind=kind)
