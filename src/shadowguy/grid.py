"""Space itself: a tile map, and the geometry questions you can ask one.

The leaf under every part of the game that has *positions* in it. A `Grid` is a
rectangle of `Tile`s; the functions over it answer what you can stand on
(`is_walkable`, `step_neighbors`), what you can see (`_fov`, `has_line_of_sight`,
`visible_tiles`), how you get there (`path_between`) and how far away it is
(`chebyshev`). Nothing here knows about units, turns, weapons or a job — no game
state at all, which is what lets both users import it without dragging the other in:

    tactical.py    fight surface 2, and the BSP map generator that emits a Grid
    buildings.py   burglary targets: a Level is one Grid plus its rooms

That split is the point. `buildings.py` needs only this geometry, and `tactical.py`
needs a whole `Building` to walk one — so with the geometry down here, the arrow runs
grid -> buildings -> tactical and nothing has to reach backwards.

It imports tcod for field-of-view and pathfinding, and numpy for the boolean arrays
tcod wants. Coordinates are (x, y) everywhere in the public surface; tcod and numpy
index [row, col] = [y, x], and that flip is confined to `_yx()` and the array builders
below, so callers never deal in it.
"""

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import tcod

Coord = tuple[int, int]  # (x, y)

# Eight-way movement: a diagonal step costs the same one move as a cardinal one, which
# is exactly what chebyshev already says a diagonal is worth (distance 1 -- the metric
# melee reach, grenade blasts and cover adjacency have always used). Both sides of a
# fight step through this same table, so nobody outruns anybody by cutting corners.
_STEPS: tuple[Coord, ...] = (
    (0, -1),
    (0, 1),
    (-1, 0),
    (1, 0),
    (-1, -1),
    (1, -1),
    (-1, 1),
    (1, 1),
)


class Tile(StrEnum):
    """What occupies a cell. Walkability and transparency are derived from the kind
    (see _WALKABLE/_TRANSPARENT), never stored per-cell — one table, no drift."""

    FLOOR = "floor"  # open ground: you can stand and see through it
    WALL = "wall"  # blocks movement and line of sight — full cover to hide behind
    LOW_COVER = "low_cover"  # a crate/railing: blocks movement, but you see and shoot *over* it


# Standing *on* a tile. Only floor is stand-able; walls and low cover are objects you
# move around, not into. (Low cover's whole point is that a unit hugging it — adjacent,
# not on it — gets a defense bonus; that's a tactical.py increment-1 concern, computed
# from adjacency, not a property of the tile you occupy.)
_WALKABLE = frozenset({Tile.FLOOR})
# Seeing/shooting *through* a tile. Low cover is transparent (you shoot over the crate);
# only a full wall is opaque. This is the array tcod's FOV and our LOS check read.
_TRANSPARENT = frozenset({Tile.FLOOR, Tile.LOW_COVER})


@dataclass
class Grid:
    """A rectangular tile map. The numpy/tcod arrays it feeds are built once and cached:
    only the *units* move, so the terrain is fixed for the fight and there's nothing to
    invalidate. That matters because has_line_of_sight runs an unlimited-radius FOV per
    call and is hit hard — once per movement step per enemy in _enemy_phase, per guard
    per keypress while sneaking — and rebuilding the array from `tiles` was ~70% of
    each call. Generation mutates `tiles` in place while carving (see generate_map), so
    the cache is keyed on the tile data's identity-and-contents via _invalidate below;
    callers that edit tiles after construction must go through it."""

    width: int
    height: int
    tiles: list[list[Tile]]  # tiles[y][x]
    _arrays: dict[frozenset[Tile], np.ndarray] = field(default_factory=dict, repr=False, compare=False)

    def _invalidate(self) -> None:
        """Drop the cached arrays after an in-place edit to `tiles`."""
        self._arrays.clear()

    def in_bounds(self, coord: Coord) -> bool:
        x, y = coord
        return 0 <= x < self.width and 0 <= y < self.height

    def tile(self, coord: Coord) -> Tile:
        x, y = coord
        return self.tiles[y][x]

    def is_walkable(self, coord: Coord) -> bool:
        """Whether a unit may stand here — bounds and terrain only. Other units blocking
        a cell is a per-turn fact the caller supplies (see path_between/step_neighbors),
        not a property of the map."""
        return self.in_bounds(coord) and self.tile(coord) in _WALKABLE

    def _bool_array(self, kinds: frozenset[Tile]) -> np.ndarray:
        """A [y, x] boolean grid, True where the tile is in `kinds` — the shape tcod wants.
        Cached per `kinds` (see the class docstring); the returned array is shared, so
        treat it as read-only — tcod's FOV/A* only ever read it."""
        cached = self._arrays.get(kinds)
        if cached is None:
            cached = np.array(
                [[self.tiles[y][x] in kinds for x in range(self.width)] for y in range(self.height)],
                dtype=bool,
            )
            self._arrays[kinds] = cached
        return cached

    def transparency(self) -> np.ndarray:
        return self._bool_array(_TRANSPARENT)

    def walkable(self) -> np.ndarray:
        return self._bool_array(_WALKABLE)


def parse_grid(rows: list[str]) -> Grid:
    """Build a Grid from ASCII art — '#' wall, '%' low cover, anything else floor. The way
    tactical maps are written in tests and hand-authored fixtures; procedural generation
    (tcod BSP, keyed off the job's LocationKind) is a later increment that also emits a Grid."""
    glyphs = {"#": Tile.WALL, "%": Tile.LOW_COVER}
    width = max(len(row) for row in rows)
    tiles = [
        [glyphs.get(row[x] if x < len(row) else " ", Tile.FLOOR) for x in range(width)]
        for row in rows
    ]
    return Grid(width=width, height=len(rows), tiles=tiles)


def _yx(coord: Coord) -> tuple[int, int]:
    x, y = coord
    return (y, x)


def _fov(grid: Grid, origin: Coord) -> np.ndarray:
    """Unlimited symmetric-shadowcast FOV from `origin` as a [y, x] bool array. Symmetric
    so 'A sees B' iff 'B sees A' — the property a fair fight needs, since one array decides
    both who the player sees and who can shoot the player. Unlimited (radius 0) because
    reach is never read off FOV: a weapon's range is a separate explicit distance check
    (see has_line_of_sight / weapon_range), which also sidesteps tcod's Euclidean-radius
    off-by-one at the edge."""
    return tcod.map.compute_fov(
        grid.transparency(), _yx(origin), radius=0,
        algorithm=tcod.constants.FOV_SYMMETRIC_SHADOWCAST,
    )


def has_line_of_sight(grid: Grid, a: Coord, b: Coord) -> bool:
    """Whether the line from `a` to `b` is unobstructed by walls — can a shot connect,
    range aside. A pure obstruction test; a weapon's reach is a separate distance gate the
    caller applies."""
    if a == b:
        return True
    return bool(_fov(grid, a)[_yx(b)])


def visible_tiles(grid: Grid, origin: Coord) -> np.ndarray:
    """Every tile `origin` can currently see, as the same [y, x] bool array
    has_line_of_sight reads one cell from — exposed for renderers that want the whole
    picture at once (e.g. dimming what the player can't presently see) instead of one
    has_line_of_sight call per tile, which would recompute the FOV from scratch each time."""
    return _fov(grid, origin)


def path_between(
    grid: Grid, start: Coord, goal: Coord, blocked: frozenset[Coord] = frozenset()
) -> list[Coord]:
    """A* from `start` to `goal` over walkable floor, treating `blocked` cells (other units)
    as impassable. Returns the steps *after* start, ending on goal, or [] if unreachable.
    Eight-way, diagonals at the same cost as a cardinal step -- the AI closes distance the
    way the player does, and `chebyshev` stays the honest step count. `goal` itself is left walkable so a
    unit can path *up to* an occupied target and stop adjacent — the AI wants to reach the
    player's tile conceptually, then attack from range, not fail because the player stands on it."""
    cost = grid.walkable().astype(np.int8)
    for bx, by in blocked:
        if grid.in_bounds((bx, by)) and (bx, by) != goal:
            cost[by, bx] = 0
    finder = tcod.path.AStar(cost, diagonal=1.0)
    path = finder.get_path(*_yx(start), *_yx(goal))
    return [(x, y) for (y, x) in path]


def step_neighbors(grid: Grid, coord: Coord, blocked: frozenset[Coord] = frozenset()) -> list[Coord]:
    """The cells one step from `coord` a unit may move into -- any of the eight, in bounds,
    walkable, and not occupied. The move-legality counterpart to path_between's routing."""
    return [
        n
        for dx, dy in _STEPS
        if grid.is_walkable((n := (coord[0] + dx, coord[1] + dy))) and n not in blocked
    ]


def chebyshev(a: Coord, b: Coord) -> int:
    """King-move distance — the range metric. Movement is cardinal (see _STEPS), but a
    unit reaches/attacks the whole 8-cell ring around it, so distance is measured that
    way: a diagonal neighbour is 'adjacent' for a melee swing though it takes two steps
    to walk to. LOS/obstruction is separate (has_line_of_sight)."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))

