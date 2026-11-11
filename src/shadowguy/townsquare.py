"""The walkable town square of one Territory: where its Locations stand.

Pure geometry and glyphs — no Textual and no game state beyond the `Territory` handed in.
`build_square` is deterministic from the territory id, so a district's square looks the
same on every visit and nothing about it needs saving. `screens/town_square.py` is the
UI over this.

A district's Locations are drawn as building blocks around an open plaza: one row of
blocks along the top, another along the bottom when there are enough places to need it.
Each block is `BLOCK_WIDTH` wide and `BLOCK_HEIGHT` tall; every tile of a block resolves
to the same Location, so walking into any of it enters the business. The glyph on the
block's sign tile (`KIND_GLYPHS`) says what kind of place it is.
"""

import random
import zlib
from dataclasses import dataclass

from shadowguy.corpmap import Location, LocationKind, Territory

WALL_GLYPH = "#"
FLOOR_GLYPH = "·"
PLAYER_GLYPH = "@"
FIXER_GLYPH = "F"
GIG_GLYPH = "!"

# A building block: four columns wide so a sign tile reads as part of the building rather
# than the whole thing, two rows tall so it has a visible footprint. Two blank columns
# between blocks leave room to walk between them.
BLOCK_WIDTH = 4
BLOCK_HEIGHT = 2
BLOCK_GAP = 2

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
        """The open plaza proper — the floor between the building rows, not the narrow
        gaps between two blocks. People stand here rather than tucked between shops."""
        # The bottom row's sign sits on the block's own first row; its absence means a
        # one-row square, whose plaza runs right down to the bottom wall.
        two_rows = any(y == self.height - 1 - BLOCK_HEIGHT for _, y in self.signs.values())
        last_row = self.height - 4 if two_rows else self.height - 2
        return [
            (x, y)
            for y in range(3, last_row + 1)
            for x in range(1, self.width - 1)
            if self.is_floor(x, y)
        ]


def _block_origins(count: int) -> tuple[int, int]:
    """How many building blocks each row holds: top row gets the odd one when needed."""
    blocks = max(1, (count + 1) // 2)
    top = min(count, blocks)
    return top, count - top


def build_square(territory: Territory) -> Square:
    """Lay out `territory`'s Locations around an open plaza.

    Deterministic from the territory id alone: no rng state from the run, no stored
    layout. The Locations are shuffled with that seed so a district's shop row does not
    follow generation order, but the same district always yields the same square.
    """
    locations = list(territory.locations)
    if not locations:
        # A park (or any empty district) still gets somewhere to stand: a small empty
        # plaza, rather than a screen with nothing in it.
        return Square(
            territory_id=territory.id,
            width=21,
            height=9,
            start=(10, 4),
            locations=(),
            buildings={},
            signs={},
        )

    rng = random.Random(_seed(f"{territory.id}:layout"))
    rng.shuffle(locations)

    top_count, bottom_count = _block_origins(len(locations))
    blocks_per_row = max(top_count, bottom_count)
    width = blocks_per_row * (BLOCK_WIDTH + BLOCK_GAP) + 1
    height = 14 if bottom_count else 10
    start = (width // 2, (height - 1) // 2)

    buildings: dict[tuple[int, int], Location] = {}
    signs: dict[str, tuple[int, int]] = {}

    def place_row(row: list[Location], y: int) -> None:
        for index, location in enumerate(row):
            x = 1 + index * (BLOCK_WIDTH + BLOCK_GAP)
            for dx in range(BLOCK_WIDTH):
                for dy in range(BLOCK_HEIGHT):
                    buildings[(x + dx, y + dy)] = location
            # The sign sits one tile in from the block's left edge, so the building
            # reads as having a door and a frontage rather than being a single glyph.
            signs[location.id] = (x + 1, y)

    place_row(locations[:top_count], 1)
    if bottom_count:
        place_row(locations[top_count:], height - 1 - BLOCK_HEIGHT)

    return Square(
        territory_id=territory.id,
        width=width,
        height=height,
        start=start,
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
