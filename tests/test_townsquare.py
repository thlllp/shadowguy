"""The town square's pure geometry: seeded layout and unit placement.

The screen over this is exercised in test_app_flows.py; nothing here touches Textual.
"""

import random
from collections import Counter, deque

import pytest

from shadowguy.corpmap import Location, LocationKind, Territory
from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.townsquare import (
    MAX_BLOCK_HEIGHT,
    MAX_BLOCK_WIDTH,
    MIN_BLOCK_HEIGHT,
    MIN_BLOCK_WIDTH,
    KIND_GLYPHS,
    build_square,
    place_units,
)

# Same convention as the other map/layout sweeps: a wide seed range, invariants only.
SEEDS = range(40)


def _territory(count: int, *, territory_id: str = "t1") -> Territory:
    kinds = list(LocationKind)
    locations = [
        Location(id=f"loc_{index}", name=f"Place {index}", kind=kinds[index % len(kinds)])
        for index in range(count)
    ]
    return Territory(id=territory_id, name="Testville", x=0, y=0, locations=locations)


def _reachable_floor(square) -> set[tuple[int, int]]:
    start = square.start
    seen = {start}
    frontier = deque([start])
    while frontier:
        x, y = frontier.popleft()
        for tile in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if square.is_floor(*tile) and tile not in seen:
                seen.add(tile)
                frontier.append(tile)
    return seen


@pytest.mark.parametrize("count", [0, 1, 2, 3, 6, 10, 14])
def test_every_location_stands_in_the_square_exactly_once(count):
    territory = _territory(count)
    square = build_square(territory)

    placed = {location.id for location in square.locations}
    assert placed == {location.id for location in territory.locations}
    assert len(square.locations) == count
    # Every building tile resolves to the location whose block it belongs to, no
    # location owns two separate blocks, and no two blocks' tiles collide.
    blocks = Counter(location.id for location in square.buildings.values())
    assert set(blocks) == {location.id for location in square.locations}
    for location in square.locations:
        tiles = [tile for tile, owner in square.buildings.items() if owner.id == location.id]
        xs = [x for x, _ in tiles]
        ys = [y for _, y in tiles]
        # A block is one filled rectangle, not a scattering of tiles.
        assert len(tiles) == (max(xs) - min(xs) + 1) * (max(ys) - min(ys) + 1)
        assert MIN_BLOCK_WIDTH * MIN_BLOCK_HEIGHT <= len(tiles)
        assert len(tiles) <= MAX_BLOCK_WIDTH * MAX_BLOCK_HEIGHT


@pytest.mark.parametrize("count", [0, 1, 2, 5, 9, 14])
def test_the_runner_starts_on_an_open_floor_tile(count):
    square = build_square(_territory(count))
    assert square.in_bounds(*square.start)
    assert square.is_floor(*square.start)
    assert square.location_at(*square.start) is None
    assert not square.is_wall(*square.start)


@pytest.mark.parametrize("count", [0, 3, 8, 14])
def test_building_blocks_stay_inside_the_walls(count):
    square = build_square(_territory(count))
    for x, y in square.buildings:
        assert 0 < x < square.width - 1
        assert 0 < y < square.height - 1
        assert not square.is_wall(x, y)


@pytest.mark.parametrize("seed", range(15))
@pytest.mark.parametrize("count", [3, 6, 10, 14])
def test_all_floor_is_reachable_from_the_start(seed, count):
    square = build_square(_territory(count), seed=seed)
    assert _reachable_floor(square) == set(square.floor_tiles)


def test_layout_is_deterministic_from_the_territory_id():
    territory = _territory(9)
    first = build_square(territory)
    second = build_square(territory)

    assert first.start == second.start
    assert [location.id for location in first.locations] == [
        location.id for location in second.locations
    ]
    assert set(first.buildings) == set(second.buildings)
    assert {tile: location.id for tile, location in first.buildings.items()} == {
        tile: location.id for tile, location in second.buildings.items()
    }
    assert first.signs == second.signs


def test_layout_is_deterministic_from_the_run_seed():
    territory = _territory(9)
    first = build_square(territory, seed=12345)
    second = build_square(territory, seed=12345)

    assert (first.width, first.height, first.start) == (
        second.width,
        second.height,
        second.start,
    )
    assert {tile: location.id for tile, location in first.buildings.items()} == {
        tile: location.id for tile, location in second.buildings.items()
    }
    assert first.signs == second.signs


def test_different_run_seeds_relayout_the_same_district():
    """The run seed is what makes two playthroughs' districts differ; the place set
    must not change, only where the blocks stand."""
    territory = _territory(10)
    first = build_square(territory, seed=1)
    second = build_square(territory, seed=2)

    assert {location.id for location in first.locations} == {
        location.id for location in second.locations
    }
    assert (first.width, first.height, first.start) != (
        second.width,
        second.height,
        second.start,
    ) or {tile: location.id for tile, location in first.buildings.items()} != {
        tile: location.id for tile, location in second.buildings.items()
    }


def test_different_districts_can_order_the_same_locations_differently():
    """The shuffle is seeded by the district, so its purpose is visible: the same set of
    places need not stand in the same blocks across two districts."""
    locations = _territory(10).locations
    orderings = set()
    for index in range(20):
        territory = Territory(
            id=f"district_{index}", name="Testville", x=0, y=0, locations=list(locations)
        )
        orderings.add(tuple(location.id for location in build_square(territory).locations))
    assert len(orderings) > 1


def test_blocks_vary_in_size_between_districts():
    sizes = set()
    for index in range(20):
        square = build_square(_territory(8, territory_id=f"district_{index}"))
        for location in square.locations:
            sizes.add(sum(1 for owner in square.buildings.values() if owner.id == location.id))
    assert len(sizes) > 1


def test_place_units_is_deterministic_and_never_overlaps():
    square = build_square(_territory(8))
    keys = ["fixer:a", "fixer:b", "gig:loc_0", "gig:loc_3", "gig:loc_7"]
    first = place_units(square, keys)
    second = place_units(square, keys)

    assert first == second
    assert set(first) == set(keys)
    assert len(set(first.values())) == len(keys)
    assert all(square.is_floor(*tile) for tile in first.values())
    assert all(tile in square.plaza_tiles for tile in first.values())
    assert square.start not in first.values()


def test_place_units_keeps_other_people_put_when_one_leaves():
    """A completed gig's "!" disappearing must not reshuffle the whole square."""
    square = build_square(_territory(6))
    before = place_units(square, ["fixer:a", "fixer:b", "gig:loc_0", "gig:loc_1"])
    after = place_units(square, ["fixer:a", "fixer:b", "gig:loc_1"])

    assert after["fixer:a"] == before["fixer:a"]
    assert after["fixer:b"] == before["fixer:b"]
    assert after["gig:loc_1"] == before["gig:loc_1"]


def test_place_units_honours_blocked_tiles():
    square = build_square(_territory(6))
    plaza = square.plaza_tiles
    blocked = (plaza[0], plaza[1])
    placed = place_units(square, ["fixer:a", "gig:loc_0"], blocked=blocked)
    assert set(placed.values()).isdisjoint(blocked)


def test_glyphs_cover_every_location_kind():
    assert set(KIND_GLYPHS) == set(LocationKind)
    assert all(len(glyph) == 1 for glyph in KIND_GLYPHS.values())


@pytest.mark.parametrize("seed", SEEDS)
def test_a_generated_map_lays_out_every_district(seed):
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    for territory in corp_map.territories.values():
        square = build_square(territory, seed=seed)
        assert square.is_floor(*square.start), territory.id
        assert {location.id for location in square.locations} == {
            location.id for location in territory.locations
        }, territory.id
        assert all(square.is_floor(*tile) for tile in place_units(square, ["fixer:a"]).values())
