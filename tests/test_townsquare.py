"""The town square's pure geometry: deterministic layout and unit placement.

The screen over this is exercised in test_app_flows.py; nothing here touches Textual.
"""

import random
from collections import Counter

import pytest

from shadowguy.corpmap import Location, LocationKind, Territory
from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.townsquare import (
    BLOCK_HEIGHT,
    BLOCK_WIDTH,
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


@pytest.mark.parametrize("count", [0, 1, 2, 3, 6, 10, 14])
def test_every_location_stands_in_the_square_exactly_once(count):
    territory = _territory(count)
    square = build_square(territory)

    placed = {location.id for location in square.locations}
    assert placed == {location.id for location in territory.locations}
    assert len(square.locations) == count
    # Every building tile resolves to the location whose block it belongs to, and no
    # location holds two separate blocks (which would be one district place standing in
    # twice).
    blocks = Counter(location.id for location in square.buildings.values())
    assert set(blocks) == {location.id for location in square.locations}
    assert all(tiles == BLOCK_WIDTH * BLOCK_HEIGHT for tiles in blocks.values())


@pytest.mark.parametrize("count", [0, 1, 2, 5, 9])
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
        square = build_square(territory)
        assert square.is_floor(*square.start), territory.id
        assert {location.id for location in square.locations} == {
            location.id for location in territory.locations
        }, territory.id
        assert all(square.is_floor(*tile) for tile in place_units(square, ["fixer:a"]).values())
