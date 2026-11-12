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
"""

import math
import random
import zlib
from collections import deque
from dataclasses import dataclass

from shadowguy.corpmap import Location, LocationKind, Territory

WALL_GLYPH = "#"
FLOOR_GLYPH = "·"
PLAYER_GLYPH = "@"
FIXER_GLYPH = "F"
GIG_GLYPH = "!"

# A building block is a small rectangle, not a single tile, so its sign reads as part of
# a building rather than the whole building. Sizes are rolled per block; the bounds also
# keep a district readable when several blocks sit close together.
MIN_BLOCK_WIDTH = 3
MAX_BLOCK_WIDTH = 5
MIN_BLOCK_HEIGHT = 2
MAX_BLOCK_HEIGHT = 3

# At least one walkable tile between blocks and between a block and the wall. This is
# what keeps the plaza connected: no block can seal off a pocket of floor.
BLOCK_GAP = 1

# The smallest square, and the size of an empty one. Wide enough that a lone shop still
# gets a plaza around it rather than a corridor.
MIN_SQUARE_WIDTH = 21
MIN_SQUARE_HEIGHT = 9

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

# A stable seed from a string. Python's own hash() is salted per process, which would
# make a district's square change between runs of the game -- the opposite of what a
# fixed layout is for.
def _seed(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def kind_label(kind: LocationKind) -> str:
    """How a kind reads in prose: "weapon_shop" -> "Weapon Shop"."""
    # Two kinds whose enum name isn't how a player would say them: an HQ is a corporate
    # one, and Amy's Place is the bar it is, not a second place called "Amys Place".
    overrides = {LocationKind.CORP_HQ: "Corporate HQ", LocationKind.AMYS_PLACE: "Bar"}
    return overrides.get(kind, kind.replace("_", " ").title())


@dataclass(frozen=True)
class Square:
    """A laid-out town square. Every tile either a wall, a plaza floor or a building."""

    territory_id: str
    width: int
    height: int
    start: tuple[int, int]
    locations: tuple[Location, ...]
    # Every tile of every building block -> the Location standing there.
    buildings: dict[tuple[int, int], Location]
    # Each Location's sign tile (where its glyph is drawn) -> location id.
    signs: dict[str, tuple[int, int]]

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_wall(self, x: int, y: int) -> bool:
        return not self.in_bounds(x, y) or x == 0 or y == 0 or x == self.width - 1 or y == self.height - 1

    def location_at(self, x: int, y: int) -> Location | None:
        return self.buildings.get((x, y))

    def is_floor(self, x: int, y: int) -> bool:
        return self.in_bounds(x, y) and not self.is_wall(x, y) and (x, y) not in self.buildings

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
        for tile in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
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
    count: int, width: int, height: int, rng: random.Random
) -> list[Block] | None:
    """Try to scatter `count` blocks over the square. None if they don't fit, which
    makes build_square give the square more room and try again."""
    blocks: list[Block] = []
    for _ in range(count):
        for _attempt in range(120):
            block_width = rng.randint(MIN_BLOCK_WIDTH, MAX_BLOCK_WIDTH)
            block_height = rng.randint(MIN_BLOCK_HEIGHT, MAX_BLOCK_HEIGHT)
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


def build_square(territory: Territory, *, seed: int | None = None) -> Square:
    """Lay out `territory`'s Locations as scattered blocks around an open plaza.

    Deterministic from the territory id *and* the run seed, so the same location looks
    the same on every visit and after a reload, while two runs get different squares.
    Nothing is stored on the Territory or in the save bundle; the run seed is already
    saved, so this costs no SAVE_VERSION bump.

    Locations are shuffled with the same seed so a district's blocks don't follow
    generation order. Block sizes and positions are rolled until every block fits with a
    walkable gap around it and all floor is connected.
    """
    locations = list(territory.locations)
    if not locations:
        # A park (or any empty district) still gets somewhere to stand: a small empty
        # plaza, rather than a screen with nothing in it.
        return Square(
            territory_id=territory.id,
            width=MIN_SQUARE_WIDTH,
            height=MIN_SQUARE_HEIGHT,
            start=(MIN_SQUARE_WIDTH // 2, MIN_SQUARE_HEIGHT // 2),
            locations=(),
            buildings={},
            signs={},
        )

    seed_text = f"{territory.id}:layout" if seed is None else f"{seed}:{territory.id}:layout"
    rng = random.Random(_seed(seed_text))
    rng.shuffle(locations)

    # Start compact and grow whichever dimension is currently tighter. Growth is rare
    # for a real district (at most a handful of Locations) and bounded for tests.
    side = max(1, math.ceil(math.sqrt(len(locations))))
    width = max(MIN_SQUARE_WIDTH, side * 7)
    height = max(MIN_SQUARE_HEIGHT, side * 3)
    blocks: list[Block] = []
    for _attempt in range(80):
        rolled = _roll_blocks(len(locations), width, height, rng)
        if rolled is not None:
            blocks = rolled
            break
        if width <= height * 1.6:
            width += 3
        else:
            height += 2
    else:
        raise RuntimeError(f"could not lay out {len(locations)} blocks for {territory.id!r}")

    buildings: dict[tuple[int, int], Location] = {}
    signs: dict[str, tuple[int, int]] = {}
    for location, (x, y, block_width, block_height) in zip(locations, blocks, strict=True):
        for dx in range(block_width):
            for dy in range(block_height):
                buildings[(x + dx, y + dy)] = location
        # The sign sits on the block's front row, somewhere along its width, so a wider
        # building reads as a frontage rather than a single glyph.
        signs[location.id] = (x + rng.randrange(block_width), y)

    floor = _floor_tiles(width, height, blocks)
    return Square(
        territory_id=territory.id,
        width=width,
        height=height,
        start=_starting_tile(width, height, floor, rng),
        locations=tuple(locations),
        buildings=buildings,
        signs=signs,
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
        start = _seed(f"{square.territory_id}:unit:{key}") % len(tiles)
        for offset in range(len(tiles)):
            tile = tiles[(start + offset) % len(tiles)]
            if tile not in taken:
                taken.add(tile)
                placed[key] = tile
                break
    return placed
