"""Laying out one tactical fight map, run once per generated stage.

A job's tactical fight lands on one of these (see jobs.py). BSP rooms + corridors,
some scattered low cover, the player entering one end and the squad holding the other.
tcod does the partition (seeded off the caller's rng so a run stays reproducible); the
carving/placement/validation is ours.

Split out of tactical.py the same way corpmap_gen.py is split out of corpmap.py, and
for the same reason: generation runs once and the fight runs for the rest of the stage,
so the two have no business sharing a file. This module is a **leaf** — it imports
grid.py for the geometry and tcod for the partition, and nothing else from the package.
In particular it never imports tactical.py: a map knows nothing about units, turns or
whose turn it is, which is exactly what makes it generatable ahead of the fight and
picklable onto a scene.TacticalStage.

A burglary does *not* come through here — buildings.py carves those, with several
candidate entry points converging on one objective rather than one player_start with a
squad at the far end. See Burglary in DESIGN.md.
"""

import random
from dataclasses import dataclass

import tcod.bsp
import tcod.random

from shadowguy.grid import Coord, Grid, Tile, path_between

# Sized to sit inside the fight screen at 80x24 without scrolling (see app.TacticalScreen).
TAC_MAP_WIDTH = 30
TAC_MAP_HEIGHT = 10
_BSP_DEPTH = 3
_ROOM_MIN = 4
_MAP_GEN_ATTEMPTS = 60


@dataclass
class TacticalMap:
    """A generated fight map plus where everyone starts — what a TacticalStage is built
    from. The player enters at `player_start` (near the `exits`, the way back out); the
    squad holds `enemy_spawns` at the far end."""

    grid: Grid
    player_start: Coord
    enemy_spawns: list[Coord]
    exits: frozenset[Coord]


def _carve(tiles: list[list[Tile]], x: int, y: int, tile: Tile = Tile.FLOOR) -> None:
    """Set a cell if it's in bounds and not on the outer wall ring — the border stays
    solid so no room or tunnel ever opens onto the edge."""
    if 0 < x < len(tiles[0]) - 1 and 0 < y < len(tiles) - 1:
        tiles[y][x] = tile


def _carve_room(tiles: list[list[Tile]], x: int, y: int, w: int, h: int) -> None:
    for j in range(y, y + h):
        for i in range(x, x + w):
            _carve(tiles, i, j)


def _carve_tunnel(tiles: list[list[Tile]], a: Coord, b: Coord) -> None:
    """An L-shaped corridor between two room centers: horizontal, then vertical."""
    (x1, y1), (x2, y2) = a, b
    for x in range(min(x1, x2), max(x1, x2) + 1):
        _carve(tiles, x, y1)
    for y in range(min(y1, y2), max(y1, y2) + 1):
        _carve(tiles, x2, y)


def _room_cells(grid: Grid, rect: tuple[int, int, int, int]) -> list[Coord]:
    rx, ry, rw, rh = rect
    return [
        (x, y)
        for y in range(ry, ry + rh)
        for x in range(rx, rx + rw)
        if grid.in_bounds((x, y)) and grid.tile((x, y)) is Tile.FLOOR
    ]


def _bsp_rooms(
    tiles: list[list[Tile]], width: int, height: int, rng: random.Random, depth: int = _BSP_DEPTH
) -> list[tuple[Coord, tuple[int, int, int, int]]] | None:
    """Carve BSP rooms and corridors into tiles. Returns room list or None. `depth` is
    room granularity -- deeper splits the same footprint into more, smaller rooms."""
    bsp = tcod.bsp.BSP(x=1, y=1, width=width - 2, height=height - 2)
    bsp.split_recursive(
        depth=depth, min_width=_ROOM_MIN, min_height=_ROOM_MIN,
        max_horizontal_ratio=1.5, max_vertical_ratio=1.5,
        seed=tcod.random.Random(tcod.random.MERSENNE_TWISTER, seed=rng.getrandbits(31)),
    )
    rooms: list[tuple[Coord, tuple[int, int, int, int]]] = []
    for leaf in bsp.pre_order():
        if leaf.children:
            continue
        rx, ry = leaf.x + 1, leaf.y + 1
        rw, rh = max(2, leaf.width - 2), max(2, leaf.height - 2)
        _carve_room(tiles, rx, ry, rw, rh)
        rooms.append(((rx + rw // 2, ry + rh // 2), (rx, ry, rw, rh)))
    if len(rooms) < 2:
        return None
    for prev, cur in zip(rooms, rooms[1:]):
        _carve_tunnel(tiles, prev[0], cur[0])
    return rooms


def _pick_spawns(cells_by_room: list[list[Coord]], enemy_count: int, reserved: set[Coord], rng: random.Random) -> list[Coord] | None:
    """Pick enemy spawn cells away from the entry room; fall back to all rooms."""
    spawn_pool = [cell for cells in cells_by_room[1:] for cell in cells if cell not in reserved]
    if len(spawn_pool) < enemy_count:
        spawn_pool = [cell for cells in cells_by_room for cell in cells if cell not in reserved]
    if len(spawn_pool) < enemy_count:
        return None
    return rng.sample(spawn_pool, enemy_count)


def _scatter_cover(tiles: list[list[Tile]], cells_by_room: list[list[Coord]], keep_clear: set[Coord], rng: random.Random, density: float) -> None:
    for cells in cells_by_room:
        for cell in cells:
            if cell not in keep_clear and rng.random() < density:
                tiles[cell[1]][cell[0]] = Tile.LOW_COVER


def _verify_map(grid: Grid, player_start: Coord, enemy_spawns: list[Coord], exits: frozenset[Coord]) -> bool:
    return all(
        target == player_start or path_between(grid, player_start, target)
        for target in (*enemy_spawns, *exits)
    )


def generate_map(
    rng: random.Random,
    enemy_count: int,
    width: int = TAC_MAP_WIDTH,
    height: int = TAC_MAP_HEIGHT,
    cover_density: float = 0.08,
) -> TacticalMap:
    for _ in range(_MAP_GEN_ATTEMPTS):
        tiles = [[Tile.WALL] * width for _ in range(height)]
        rooms = _bsp_rooms(tiles, width, height, rng)
        if rooms is None:
            continue

        grid = Grid(width=width, height=height, tiles=tiles)
        rooms.sort(key=lambda room: room[0][0])
        cells_by_room = [_room_cells(grid, rect) for _center, rect in rooms]
        player_start = rooms[0][0]
        exits = frozenset(sorted(cells_by_room[0])[:2])

        reserved = {player_start, *exits}
        enemy_spawns = _pick_spawns(cells_by_room, enemy_count, reserved, rng)
        if enemy_spawns is None:
            continue

        keep_clear = {player_start, *exits, *enemy_spawns}
        _scatter_cover(tiles, cells_by_room, keep_clear, rng, cover_density)
        # `tiles` was just edited in place under an already-constructed Grid.
        grid._invalidate()

        if _verify_map(grid, player_start, enemy_spawns, exits):
            return TacticalMap(grid, player_start, enemy_spawns, exits)
    raise RuntimeError("could not generate a playable tactical map")
