"""The walkable town square of one Territory: where its Locations stand.

Pure geometry and glyphs — no Textual and no game state beyond the `Territory` handed in.
`build_square` is deterministic from the territory id and the run seed, so a district's
square looks the same on every visit and after a reload without anything about it being
saved. `screens/town_square.py` is the UI over this.

A district's Locations are drawn as rectangular building blocks scattered around an open,
fully-connected plaza. Block sizes vary and each district's arrangement is rolled fresh
from the run seed: one territory can have a tight two-row market, another a sparse
courtyard with oversized blocks. Every tile of a block resolves to the same Location, so
walking into any of it enters the business. The glyph on the block's sign tile
(`KIND_GLYPHS`) says what kind of place it is.

The plaza is dressed as a street rather than left as empty floor: boarded-up houses
(`EMPTY_HOUSE_GLYPH`) stand between the real places as blocks with no Location behind
the door, and a handful of parked cars (`CAR_GLYPH`) sit on the road against the kerb.
Cars are obstacles, so the generator only takes a tile when the remaining floor stays
connected and every building keeps a door onto it.
"""

import math
import random
from collections import deque
from dataclasses import dataclass

from shadowguy.corpmap import Location, LocationKind, Territory
from shadowguy.seeding import seed_from

WALL_GLYPH = "#"
FLOOR_GLYPH = "·"
PLAYER_GLYPH = "@"
FIXER_GLYPH = "F"
GIG_GLYPH = "!"
# Boarded-up shells and parked cars: scenery, not destinations. They draw apart from
# real building blocks and from the open road so the plaza reads as a street.
EMPTY_HOUSE_GLYPH = "░"
CAR_GLYPH = "="

# A building block is a small rectangle, not a single tile, so its sign reads as part of
# a building rather than the whole building. Sizes are rolled per block; the bounds also
# keep a district readable when several blocks sit close together.
MIN_BLOCK_WIDTH = 3
MAX_BLOCK_WIDTH = 5
MIN_BLOCK_HEIGHT = 2
MAX_BLOCK_HEIGHT = 3

# Empty houses are smaller than the places you can walk into -- a row of narrow facades
# between the shops, not another row of businesses. They are rolled per district from the
# run seed, so a district's skyline is as fixed as its layout.
EMPTY_HOUSE_MIN = 3
EMPTY_HOUSE_MAX = 6
MIN_HOUSE_WIDTH = 2
MAX_HOUSE_WIDTH = 4
MIN_HOUSE_HEIGHT = 2
MAX_HOUSE_HEIGHT = 3

# Parked cars per district, and how far apart they keep: one clear tile between any two,
# so they read as parked traffic rather than a barricade across the street.
CAR_COUNT_MIN = 4
CAR_COUNT_MAX = 8

# At least one walkable tile between blocks and between a block and the wall. This is
# what keeps the plaza connected: no block can seal off a pocket of floor.
BLOCK_GAP = 1

# The smallest square. A real district is typically larger than this (see build_square);
# it is the floor for an empty one -- a park is still a street to walk.
MIN_SQUARE_WIDTH = 41
MIN_SQUARE_HEIGHT = 17

# The floor when the terminal itself is the limit (see build_square's max_width/max_height):
# one full-size block row plus the wall and the one-tile gap around it.
SMALLEST_SQUARE_WIDTH = MAX_BLOCK_WIDTH + 2
SMALLEST_SQUARE_HEIGHT = MAX_BLOCK_HEIGHT + 2

# One glyph per LocationKind. The stores are upper-case letters, services and the runner's
# own places get their own marks. Deliberately ASCII: this is read in a terminal first.
KIND_GLYPHS = {
    LocationKind.DATA: "D",
    LocationKind.LAB: "L",
    LocationKind.DEPOT: "T",
    LocationKind.CYBER_CLINIC: "R",
    LocationKind.BAR: "B",
    LocationKind.PAWN: "P",
    LocationKind.WEAPON_SHOP: "W",
    LocationKind.AUTO_DEALER: "V",
    LocationKind.PHARMACY: "+",
    LocationKind.COMPUTER_STORE: "C",
    LocationKind.HOSPITAL: "H",
    LocationKind.APARTMENT: "A",
    LocationKind.SAFEHOUSE: "S",
    LocationKind.REAL_ESTATE: "E",
    LocationKind.CORP_HQ: "Q",
    LocationKind.GANG_DEN: "G",
    LocationKind.RESEARCH_FACILITY: "X",
    LocationKind.ACADEMY: "Y",
    LocationKind.JUNKYARD: "J",
    LocationKind.DOCKS: "~",
    LocationKind.AMYS_PLACE: "B",
    LocationKind.ENCAMPMENT: "n",
}
if set(KIND_GLYPHS) != set(LocationKind):
    raise ValueError("KIND_GLYPHS must have exactly one entry per LocationKind")

# A broad colour per kind, so a row of shops reads apart from a row of clinics. The
# renderer treats this as a hint: a kind missing from here simply draws uncoloured.
KIND_STYLES = {
    LocationKind.PAWN: "yellow",
    LocationKind.WEAPON_SHOP: "yellow",
    LocationKind.AUTO_DEALER: "yellow",
    LocationKind.PHARMACY: "cyan",
    LocationKind.COMPUTER_STORE: "cyan",
    LocationKind.BAR: "bright_yellow",
    LocationKind.AMYS_PLACE: "bright_yellow",
    LocationKind.HOSPITAL: "cyan",
    LocationKind.CYBER_CLINIC: "magenta",
    LocationKind.REAL_ESTATE: "green",
    LocationKind.APARTMENT: "green",
    LocationKind.SAFEHOUSE: "green",
    LocationKind.CORP_HQ: "bright_magenta",
    LocationKind.GANG_DEN: "red",
    LocationKind.DATA: "bright_blue",
    LocationKind.LAB: "bright_blue",
    LocationKind.RESEARCH_FACILITY: "bright_blue",
    LocationKind.ACADEMY: "bright_blue",
    LocationKind.DEPOT: "white",
    LocationKind.JUNKYARD: "bright_black",
    LocationKind.DOCKS: "bright_cyan",
    LocationKind.ENCAMPMENT: "bright_black",
}


def kind_label(kind: LocationKind) -> str:
    """How a kind reads in prose: "weapon_shop" -> "Weapon Shop"."""
    # Two kinds whose enum name isn't how a player would say them: an HQ is a corporate
    # one, and Amy's Place is the bar it is, not a second place called "Amys Place".
    overrides = {LocationKind.CORP_HQ: "Corporate HQ", LocationKind.AMYS_PLACE: "Bar"}
    return overrides.get(kind, kind.replace("_", " ").title())


@dataclass(frozen=True)
class Square:
    """A laid-out town square. Every tile is a wall, a plaza floor, a building or scenery."""

    territory_id: str
    width: int
    height: int
    start: tuple[int, int]
    locations: tuple[Location, ...]
    # Every tile of every building block -> the Location standing there.
    buildings: dict[tuple[int, int], Location]
    # Each Location's sign tile (where its glyph is drawn) -> location id.
    signs: dict[str, tuple[int, int]]
    # Boarded-up houses: blocks with nothing behind the door, drawn as scenery.
    empty_houses: frozenset[tuple[int, int]]
    # Parked cars: impassable scenery tiles on the road. Never a tile the generators
    # would otherwise use, so they don't churn the save or the unit placement.
    cars: frozenset[tuple[int, int]]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_wall(self, x: int, y: int) -> bool:
        return not self.in_bounds(x, y) or x == 0 or y == 0 or x == self.width - 1 or y == self.height - 1

    def location_at(self, x: int, y: int) -> Location | None:
        return self.buildings.get((x, y))

    def is_empty_house(self, x: int, y: int) -> bool:
        return (x, y) in self.empty_houses

    def is_car(self, x: int, y: int) -> bool:
        return (x, y) in self.cars

    def is_floor(self, x: int, y: int) -> bool:
        """Whether the runner can stand there: not a wall, building, or parked car."""
        return (
            self.in_bounds(x, y)
            and not self.is_wall(x, y)
            and (x, y) not in self.buildings
            and (x, y) not in self.empty_houses
            and (x, y) not in self.cars
        )

    @property
    def floor_tiles(self) -> list[tuple[int, int]]:
        """Every walkable tile, row-major. Deterministic by construction."""
        return [
            (x, y)
            for y in range(self.height)
            for x in range(self.width)
            if self.is_floor(x, y)
        ]

    @property
    def plaza_tiles(self) -> list[tuple[int, int]]:
        """Every walkable tile. The whole floor is plaza now that blocks are scattered
        rather than confined to rows, and roaming people should be able to use all of it."""
        return self.floor_tiles


Block = tuple[int, int, int, int]  # x, y, width, height
# A size request for one block: min width, max width, min height, max height.
BlockSpec = tuple[int, int, int, int]

LOCATION_BLOCK_SPEC = (MIN_BLOCK_WIDTH, MAX_BLOCK_WIDTH, MIN_BLOCK_HEIGHT, MAX_BLOCK_HEIGHT)
HOUSE_BLOCK_SPEC = (MIN_HOUSE_WIDTH, MAX_HOUSE_WIDTH, MIN_HOUSE_HEIGHT, MAX_HOUSE_HEIGHT)


def _neighbors(tile: tuple[int, int]) -> tuple[tuple[int, int], ...]:
    x, y = tile
    return ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))



def _overlaps(a: Block, b: Block) -> bool:
    """Whether two blocks are closer than BLOCK_GAP tiles. The gap is Chebyshev, so
    diagonal neighbours can't pinch a one-tile passage closed either."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (
        ax + aw + BLOCK_GAP <= bx
        or bx + bw + BLOCK_GAP <= ax
        or ay + ah + BLOCK_GAP <= by
        or by + bh + BLOCK_GAP <= ay
    )


def _floor_tiles(width: int, height: int, blocks: list[Block]) -> list[tuple[int, int]]:
    """Every walkable tile inside the walls, row-major."""
    blocked = {
        (x + dx, y + dy)
        for x, y, block_width, block_height in blocks
        for dx in range(block_width)
        for dy in range(block_height)
    }
    return [
        (x, y)
        for y in range(1, height - 1)
        for x in range(1, width - 1)
        if (x, y) not in blocked
    ]


def _floor_is_connected(floor: list[tuple[int, int]]) -> bool:
    """All walkable floor in one 4-connected region, so the runner can reach every
    building from the starting tile."""
    remaining = set(floor)
    if not remaining:
        return False
    start = min(remaining)
    seen = {start}
    frontier = deque([start])
    while frontier:
        x, y = frontier.popleft()
        for tile in _neighbors((x, y)):
            if tile in remaining and tile not in seen:
                seen.add(tile)
                frontier.append(tile)
    return seen == remaining


def _starting_tile(
    width: int, height: int, floor: list[tuple[int, int]], rng: random.Random
) -> tuple[int, int]:
    """A random floor tile as close to the middle as possible, so the runner starts in
    the plaza rather than in a corner."""
    center_x, center_y = width // 2, height // 2
    nearest = min(abs(x - center_x) + abs(y - center_y) for x, y in floor)
    candidates = sorted(
        (x, y) for x, y in floor if abs(x - center_x) + abs(y - center_y) == nearest
    )
    return rng.choice(candidates)


def _roll_blocks(
    specs: list[BlockSpec], width: int, height: int, rng: random.Random
) -> list[Block] | None:
    """Try to scatter one block per spec over the square. None if they don't fit, which
    makes build_square give the square more room and try again."""
    blocks: list[Block] = []
    for min_width, max_width, min_height, max_height in specs:
        for _attempt in range(120):
            block_width = rng.randint(min_width, max_width)
            block_height = rng.randint(min_height, max_height)
            x = rng.randint(1, width - block_width - 1)
            y = rng.randint(1, height - block_height - 1)
            candidate = (x, y, block_width, block_height)
            if not any(_overlaps(candidate, other) for other in blocks):
                blocks.append(candidate)
                break
        else:
            return None
    if not _floor_is_connected(_floor_tiles(width, height, blocks)):
        return None
    return blocks


def _pick_cars(
    floor: list[tuple[int, int]], structures: set[tuple[int, int]], rng: random.Random
) -> set[tuple[int, int]]:
    """Park a few cars on the road, against the kerb (tiles touching a building).

    A car is an obstacle, so a candidate is only taken when what is left of the floor is
    still one connected region and every building still has at least one reachable floor
    tile to walk into it from -- a car must not close a passage or wall a shop in. Cars
    keep a clear tile between them, so a street full of them still reads as traffic
    rather than a barricade.
    """
    count = rng.randint(CAR_COUNT_MIN, CAR_COUNT_MAX)
    remaining = set(floor)
    curb = [
        tile
        for tile in floor
        if any(neighbor in structures for neighbor in _neighbors(tile))
    ]
    rng.shuffle(curb)
    # A "door" is a structure tile with road beside it; every one of them has to keep a
    # walkable neighbour or the place becomes unreachable even while the road connects.
    doors = [tile for tile in structures if any(n in remaining for n in _neighbors(tile))]
    cars: set[tuple[int, int]] = set()
    for tile in curb:
        if len(cars) >= count:
            break
        if any(max(abs(tile[0] - car[0]), abs(tile[1] - car[1])) <= 1 for car in cars):
            continue
        remaining.discard(tile)
        if not _floor_is_connected(sorted(remaining)) or not all(
            any(n in remaining for n in _neighbors(door)) for door in doors
        ):
            remaining.add(tile)
            continue
        cars.add(tile)
    return cars


def build_square(
    territory: Territory,
    *,
    seed: int | None = None,
    max_height: int | None = None,
    max_width: int | None = None,
) -> Square:
    """Lay out `territory`'s Locations as scattered blocks around an open plaza.

    Deterministic from the territory id *and* the run seed, so the same location looks
    the same on every visit and after a reload, while two runs get different squares.
    Nothing is stored on the Territory or in the save bundle; the run seed is already
    saved, so this costs no SAVE_VERSION bump.

    Locations are shuffled with the same seed so a district's blocks don't follow
    generation order. Block sizes and positions are rolled until every block fits with a
    walkable gap around it and all floor is connected. Empty houses and parked cars are
    rolled from the same stream afterwards, so they are as fixed as the real layouts.

    `max_height`/`max_width` cap the square to a terminal it has to fit (the screen
    passes its viewport). A capped square can't buy room by growing, so it drops
    boarded-up houses -- the decoration -- before it gives up: a short terminal loses
    skyline, not the places you can walk into.
    """
    locations = list(territory.locations)
    seed_text = f"{territory.id}:layout" if seed is None else f"{seed}:{territory.id}:layout"
    rng = random.Random(seed_from(seed_text))
    rng.shuffle(locations)

    # A park is greenspace, so it gets no boarded-up shells; every other district gets
    # a few, and an empty district still gets a street (cars and open floor) to stand in.
    house_count = 0 if territory.is_park else rng.randint(EMPTY_HOUSE_MIN, EMPTY_HOUSE_MAX)
    specs = [LOCATION_BLOCK_SPEC] * len(locations) + [HOUSE_BLOCK_SPEC] * house_count

    # Start compact and grow whichever dimension is currently tighter. A real district is
    # a handful of Locations, so this normally lands on MIN_SQUARE_WIDTH x MIN_SQUARE_HEIGHT
    # (or a bit larger) -- big enough to read as a street rather than a corridor.
    side = max(1, math.ceil(math.sqrt(len(locations))))
    width = max(MIN_SQUARE_WIDTH, side * 9)
    height = max(MIN_SQUARE_HEIGHT, side * 4)
    if max_width is not None:
        width = max(SMALLEST_SQUARE_WIDTH, min(width, max_width))
    if max_height is not None:
        height = max(SMALLEST_SQUARE_HEIGHT, min(height, max_height))

    blocks: list[Block] = []
    relaxed = False
    while True:
        rolled = None
        for _attempt in range(80):
            rolled = _roll_blocks(specs, width, height, rng)
            if rolled is not None:
                break
            at_max_height = max_height is not None and height >= max_height
            at_max_width = max_width is not None and width >= max_width
            if at_max_height and at_max_width:
                break
            if at_max_height:
                width = width + 3 if max_width is None else min(max_width, width + 3)
            elif at_max_width:
                height = height + 2 if max_height is None else min(max_height, height + 2)
            elif width <= height * 1.6:
                width += 3
            else:
                height += 2
        if rolled is not None:
            blocks = rolled
            break
        if house_count:
            # Boxed in on both axes: shed one shell and try again before giving up.
            house_count -= 1
            specs = [LOCATION_BLOCK_SPEC] * len(locations) + [HOUSE_BLOCK_SPEC] * house_count
            continue
        if relaxed:
            raise RuntimeError(f"could not lay out {len(specs)} blocks for {territory.id!r}")
        # Room for the places themselves is non-negotiable: a clipped square is better
        # than a crash on a terminal smaller than SMALLEST_SQUARE_HEIGHT allows for.
        max_height = max_width = None
        relaxed = True

    location_blocks = blocks[: len(locations)]
    house_blocks = blocks[len(locations) :]

    buildings: dict[tuple[int, int], Location] = {}
    signs: dict[str, tuple[int, int]] = {}
    for location, (x, y, block_width, block_height) in zip(locations, location_blocks, strict=True):
        for dx in range(block_width):
            for dy in range(block_height):
                buildings[(x + dx, y + dy)] = location
        # The sign sits on the block's front row, somewhere along its width, so a wider
        # building reads as a frontage rather than a single glyph.
        signs[location.id] = (x + rng.randrange(block_width), y)

    empty_houses = {
        (x + dx, y + dy)
        for x, y, block_width, block_height in house_blocks
        for dx in range(block_width)
        for dy in range(block_height)
    }

    floor = _floor_tiles(width, height, blocks)
    cars = _pick_cars(floor, set(buildings) | empty_houses, rng)
    open_floor = [tile for tile in floor if tile not in cars]
    return Square(
        territory_id=territory.id,
        width=width,
        height=height,
        start=_starting_tile(width, height, open_floor, rng),
        locations=tuple(locations),
        buildings=buildings,
        signs=signs,
        empty_houses=frozenset(empty_houses),
        cars=frozenset(cars),
    )


def place_units(
    square: Square, keys: list[str], *, blocked: tuple[tuple[int, int], ...] = ()
) -> dict[str, tuple[int, int]]:
    """Put one unit per key on a free plaza tile.

    Each key hashes to its own starting tile and then probes forward, so adding or
    removing a unit moves as few of the others as possible -- a completed gig's "!"
    disappearing does not reshuffle every fixer in the square. `blocked` is for tiles a
    unit must not stand on (the runner's own tile).
    """
    tiles = [tile for tile in square.plaza_tiles if tile not in blocked]
    if not tiles:
        return {}
    taken: set[tuple[int, int]] = set()
    placed: dict[str, tuple[int, int]] = {}
    for key in sorted(keys):
        start = seed_from(f"{square.territory_id}:unit:{key}") % len(tiles)
        for offset in range(len(tiles)):
            tile = tiles[(start + offset) % len(tiles)]
            if tile not in taken:
                taken.add(tile)
                placed[key] = tile
                break
    return placed
