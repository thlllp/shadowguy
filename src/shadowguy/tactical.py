"""Grid primitives for tactical-combat job stages — the leaf that owns *space*.

A tactical stage is combat played out on a grid: position, line of sight and cover
decide which of combat.py's existing attacks are legal and how hard they land, but the
dice underneath are still checks.resolve_check (see CLAUDE.md's Combat section). This
module is that spatial layer and nothing else. It imports tcod for field-of-view and
pathfinding, but — like combat.py — it imports no scene: it owns *how position works*,
not what a job is worth. scene.py holds the Outcome-bearing wrapper (TacticalStage),
importing this the same way it imports combat for Enemy.

Coordinates are (x, y) everywhere in this module's public surface. tcod and numpy index
[row, col] = [y, x]; that flip is confined to _yx() and the array builders below, so
callers never deal in it.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
import tcod
import tcod.bsp
import tcod.random

from shadowguy.character import Character
from shadowguy.combat import (
    Enemy,
    equipped_weapons,
    player_defense,
    player_soak,
    resolve_hit,
)
from shadowguy.shops import Item
from shadowguy.skills import skill_value

Coord = tuple[int, int]  # (x, y)

# Cardinal moves only, for now: a clean grid to reason about and render, and it keeps
# "distance" and "adjacent to cover" unambiguous. Diagonal movement is a lever (tcod's
# A* takes a diagonal cost) to revisit once the base game feels right, not day one.
_STEPS: tuple[Coord, ...] = ((0, -1), (0, 1), (-1, 0), (1, 0))


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
_WALKABLE = {Tile.FLOOR}
# Seeing/shooting *through* a tile. Low cover is transparent (you shoot over the crate);
# only a full wall is opaque. This is the array tcod's FOV and our LOS check read.
_TRANSPARENT = {Tile.FLOOR, Tile.LOW_COVER}


@dataclass
class Grid:
    """A rectangular tile map. The numpy/tcod arrays it feeds are rebuilt on demand from
    `tiles` rather than cached — a tactical map is small (tens of cells a side) and only
    the *units* move; the terrain is fixed for the fight, so there's nothing to invalidate."""

    width: int
    height: int
    tiles: list[list[Tile]]  # tiles[y][x]

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

    def _bool_array(self, kinds: set[Tile]) -> np.ndarray:
        """A [y, x] boolean grid, True where the tile is in `kinds` — the shape tcod wants."""
        return np.array(
            [[self.tiles[y][x] in kinds for x in range(self.width)] for y in range(self.height)],
            dtype=bool,
        )

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


def path_between(
    grid: Grid, start: Coord, goal: Coord, blocked: frozenset[Coord] = frozenset()
) -> list[Coord]:
    """A* from `start` to `goal` over walkable floor, treating `blocked` cells (other units)
    as impassable. Returns the steps *after* start, ending on goal, or [] if unreachable.
    Cardinal moves only (diagonal cost 0 disables them). `goal` itself is left walkable so a
    unit can path *up to* an occupied target and stop adjacent — the AI wants to reach the
    player's tile conceptually, then attack from range, not fail because the player stands on it."""
    cost = grid.walkable().astype(np.int8)
    for bx, by in blocked:
        if grid.in_bounds((bx, by)) and (bx, by) != goal:
            cost[by, bx] = 0
    finder = tcod.path.AStar(cost, diagonal=0.0)
    path = finder.get_path(*_yx(start), *_yx(goal))
    return [(x, y) for (y, x) in path]


def step_neighbors(grid: Grid, coord: Coord, blocked: frozenset[Coord] = frozenset()) -> list[Coord]:
    """The cells one cardinal step from `coord` a unit may move into: in bounds, walkable,
    and not occupied. The move-legality counterpart to path_between's routing."""
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


# ---------------------------------------------------------------------------
# The tactical fight. This is combat.py's resolution *given positions*: every
# attack is combat.resolve_hit (one hit formula, two surfaces — see its docstring),
# and cover is nothing more than a raised to-hit difficulty. This layer owns space,
# turn order and movement; it does not own what winning is worth (that's the
# Outcome on scene.TacticalStage, wired in a later increment).
# ---------------------------------------------------------------------------

# A weapon's reach, derived from its skill rather than a new Item field: Firearms is
# the ranged skill (see CLAUDE.md's Combat section), everything else is arm's length.
MELEE_RANGE = 1
FIREARM_RANGE = 8

# How far an enemy can attack is per-enemy, on combat.Enemy.reach (a guard shoots, street
# muscle closes) — read by _enemy_phase. There is deliberately no global enemy range.

# Move budget per turn. A constant for now; Agility (or a future ability) raising the
# player's is the obvious hook, which is why it's a field on the unit, not a global.
PLAYER_SPEED = 4
ENEMY_SPEED = 4

# Cover raises the to-hit difficulty against a unit hugging it, on the side facing the
# shooter: a full wall is worth more than a low crate you can shoot over. Added straight
# to the resolve_hit difficulty (which pool_for_difficulty turns into a bigger dodge
# pool), so cover is "harder to hit me" in the exact same formula, no special case.
FULL_COVER = 4
HALF_COVER = 2


class Side(StrEnum):
    PLAYER = "player"
    ENEMY = "enemy"


class TacticalOutcome(StrEnum):
    ONGOING = "ongoing"
    VICTORY = "victory"  # every enemy down
    ESCAPED = "escaped"  # player left by an exit tile
    DEAD = "dead"  # player at 0 health


@dataclass
class Unit:
    """One combatant on the grid. Enemy units carry their combat template (combat.Enemy)
    and current `health` here; the player's health stays on the Character — the single
    source of truth combat.py already mutates — so a player Unit's `enemy` is None and its
    `health` field is unused. `speed` is the per-turn move budget (see PLAYER_SPEED)."""

    name: str
    side: Side
    coord: Coord
    speed: int
    enemy: Enemy | None = None
    health: int = 0

    @property
    def is_enemy(self) -> bool:
        return self.side is Side.ENEMY


@dataclass
class TacticalState:
    """A tactical fight in progress. The screen renders this; the functions below advance
    it. One player turn (move up to `speed`, then one action) then the enemy phase."""

    character: Character
    grid: Grid
    units: list[Unit]
    exits: frozenset[Coord]
    outcome: TacticalOutcome = TacticalOutcome.ONGOING
    log: list[str] = field(default_factory=list)
    moves_left: int = 0
    acted: bool = False

    @property
    def player(self) -> Unit:
        return next(u for u in self.units if u.side is Side.PLAYER)

    @property
    def enemies(self) -> list[Unit]:
        """Every enemy still standing."""
        return [u for u in self.units if u.is_enemy and u.health > 0]

    @property
    def is_over(self) -> bool:
        return self.outcome is not TacticalOutcome.ONGOING

    def occupied(self, *, exclude: Unit | None = None) -> frozenset[Coord]:
        """Cells a unit stands on — what blocks movement and pathing this instant. Living
        units only: a downed enemy is a corpse you can walk over, not a wall."""
        return frozenset(
            u.coord
            for u in self.units
            if u is not exclude and (u.side is Side.PLAYER or u.health > 0)
        )


def _sign(n: int) -> int:
    return (n > 0) - (n < 0)


def cover_bonus(grid: Grid, defender: Coord, attacker: Coord) -> int:
    """How much cover shields `defender` from a shot coming from `attacker`: the to-hit
    difficulty bonus for a wall (full) or low-cover object (half) sitting in the cell
    next to the defender on the side facing the attacker. Checks the cardinal steps
    toward the attacker and takes the best — a unit tucked into a corner gets the wall,
    not the empty diagonal."""
    best = 0
    dx, dy = _sign(attacker[0] - defender[0]), _sign(attacker[1] - defender[1])
    for step in ((dx, 0), (0, dy)):
        if step == (0, 0):
            continue
        cell = (defender[0] + step[0], defender[1] + step[1])
        if not grid.in_bounds(cell):
            continue
        tile = grid.tile(cell)
        if tile is Tile.WALL:
            best = max(best, FULL_COVER)
        elif tile is Tile.LOW_COVER:
            best = max(best, HALF_COVER)
    return best


def weapon_range(weapon: Item) -> int:
    return FIREARM_RANGE if weapon.skill == "firearms" else MELEE_RANGE


def start_tactical(
    character: Character,
    grid: Grid,
    player_start: Coord,
    enemy_placements: list[tuple[Enemy, Coord]],
    exits: frozenset[Coord] = frozenset(),
) -> TacticalState:
    """Set up a fight: place the player and each enemy, then open the player's turn."""
    units = [Unit(name=character.name, side=Side.PLAYER, coord=player_start, speed=PLAYER_SPEED)]
    for enemy, coord in enemy_placements:
        units.append(
            Unit(
                name=enemy.name,
                side=Side.ENEMY,
                coord=coord,
                speed=ENEMY_SPEED,
                enemy=enemy,
                health=enemy.health,
            )
        )
    state = TacticalState(character=character, grid=grid, units=units, exits=frozenset(exits))
    _begin_player_turn(state)
    return state


def _begin_player_turn(state: TacticalState) -> None:
    state.moves_left = state.player.speed
    state.acted = False


def legal_moves(state: TacticalState) -> list[Coord]:
    """Where the player may step this instant: one cardinal move into open, unoccupied
    floor, if they have moves left."""
    if state.moves_left <= 0 or state.is_over:
        return []
    return step_neighbors(state.grid, state.player.coord, blocked=state.occupied(exclude=state.player))


def move_player(state: TacticalState, dest: Coord) -> bool:
    """Take one step. Returns False (spending nothing) if the step isn't legal."""
    if dest not in legal_moves(state):
        return False
    state.player.coord = dest
    state.moves_left -= 1
    return True


def targets_for(state: TacticalState, weapon: Item) -> list[Unit]:
    """Enemies the player could hit with this weapon right now: standing, within the
    weapon's range, and in line of sight."""
    origin = state.player.coord
    reach = weapon_range(weapon)
    return [
        enemy
        for enemy in state.enemies
        if chebyshev(origin, enemy.coord) <= reach and has_line_of_sight(state.grid, origin, enemy.coord)
    ]


def player_attack(state: TacticalState, target: Unit, weapon: Item, rng: random.Random | None = None) -> None:
    """Resolve the player's one action: an attack, through combat.resolve_hit, with the
    target's cover folded into the to-hit difficulty. Spends the action for the turn."""
    rng = rng or random
    if state.acted or state.is_over or target not in targets_for(state, weapon):
        return
    state.acted = True
    difficulty = target.enemy.defense + cover_bonus(state.grid, target.coord, state.player.coord)
    roll, damage = resolve_hit(
        rng,
        skill_value(state.character, weapon.skill),
        0,
        difficulty,
        weapon.damage,
        target.enemy.toughness,
    )
    if not roll.result.passed:
        state.log.append(f"You fire on {target.name} and miss.")
        return
    target.health = max(0, target.health - damage)
    if target.health <= 0:
        state.log.append(f"You drop {target.name}.")
    else:
        state.log.append(f"You hit {target.name} for {damage}.")
    _settle(state)


def leave(state: TacticalState) -> bool:
    """Walk out — but only from an exit tile. Positional escape: getting to the door *is*
    the flee, so there's no roll and no parting shot; the risk was crossing the room to
    reach it. Returns False if the player isn't standing on an exit."""
    if state.is_over or state.player.coord not in state.exits:
        return False
    state.outcome = TacticalOutcome.ESCAPED
    state.log.append("You slip out.")
    return True


def end_turn(state: TacticalState, rng: random.Random | None = None) -> None:
    """End the player's turn and run the enemy phase, then open the next player turn."""
    rng = rng or random
    if state.is_over:
        return
    _enemy_phase(state, rng)
    _settle(state)
    if not state.is_over:
        _begin_player_turn(state)


def _can_hit_player(state: TacticalState, enemy: Unit) -> bool:
    """Whether this enemy has a shot at the player right now: within its `reach` and with a
    clear line. Melee (reach 1) needs to be adjacent; a guard's gun only needs the sightline."""
    player = state.player.coord
    return chebyshev(enemy.coord, player) <= enemy.enemy.reach and has_line_of_sight(
        state.grid, enemy.coord, player
    )


def _enemy_phase(state: TacticalState, rng: random.Random) -> None:
    """Each enemy that can't already hit the player closes via A* (up to its speed) until it
    can, then attacks. A ranged enemy therefore holds its distance — it only advances when it
    has no shot — while melee has to reach arm's length."""
    for enemy in state.enemies:
        if state.is_over:
            return
        if not _can_hit_player(state, enemy):
            path = path_between(
                state.grid, enemy.coord, state.player.coord, blocked=state.occupied(exclude=enemy)
            )
            # Path ends on the player's own tile; don't step onto it — stop the step before.
            for step in path[: enemy.speed]:
                if step == state.player.coord:
                    break
                enemy.coord = step
                if _can_hit_player(state, enemy):
                    break
        if _can_hit_player(state, enemy):
            _enemy_attack(state, enemy, rng)
            _settle(state)


def _enemy_attack(state: TacticalState, enemy: Unit, rng: random.Random) -> None:
    difficulty = player_defense(state.character) + cover_bonus(
        state.grid, state.player.coord, enemy.coord
    )
    roll, damage = resolve_hit(
        rng, enemy.enemy.attack, 0, difficulty, enemy.enemy.damage, player_soak(state.character)
    )
    if not roll.result.passed:
        state.log.append(f"{enemy.name} swings wide.")
        return
    state.character.adjust_health(-damage)
    state.log.append(f"{enemy.name} hits you for {damage}." if damage else f"{enemy.name} connects, but your armor holds.")


def _settle(state: TacticalState) -> None:
    """Read the board. Death first: a mutual kill still kills you."""
    if not state.character.is_alive:
        state.outcome = TacticalOutcome.DEAD
    elif not state.enemies:
        state.outcome = TacticalOutcome.VICTORY


def player_weapons(state: TacticalState) -> list[Item]:
    """The weapons the player can attack with this fight — their equipped gear, or fists."""
    return equipped_weapons(state.character)


def best_shot(state: TacticalState) -> tuple[Item, Unit] | None:
    """The attack the player takes by default: the nearest in-sight, in-range enemy, with
    the best-reaching weapon (nearer first, more damage breaks ties). None if there's no
    shot — already acted, or nothing in sight and range. This is fight *policy*, not view,
    so it lives here beside targets_for; the screen and any headless driver share it."""
    if state.acted:
        return None
    origin = state.player.coord
    best = None  # (sort_key, weapon, target)
    for weapon in player_weapons(state):
        for target in targets_for(state, weapon):
            key = (chebyshev(origin, target.coord), -weapon.damage)
            if best is None or key < best[0]:
                best = (key, weapon, target)
    return None if best is None else (best[1], best[2])


# ---------------------------------------------------------------------------
# Procedural maps. A job's tactical fight lands on one of these (see jobs.py). BSP
# rooms + corridors, some scattered low cover, the player entering one end and the
# squad holding the other. tcod does the partition (seeded off the caller's rng so a
# run stays reproducible); the carving/placement/validation is ours.
# ---------------------------------------------------------------------------

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


def generate_map(
    rng: random.Random,
    enemy_count: int,
    width: int = TAC_MAP_WIDTH,
    height: int = TAC_MAP_HEIGHT,
    cover_density: float = 0.08,
) -> TacticalMap:
    """A connected BSP map with room for `enemy_count` enemies away from the player and at
    least one exit. Retries until every enemy spawn and exit is reachable from the player
    start (scattered cover can't wall part of the map off), raising only if it never lands
    a playable one — the caller can't recover from an unplayable fight, so this must not
    hand one back."""
    for _ in range(_MAP_GEN_ATTEMPTS):
        tiles = [[Tile.WALL] * width for _ in range(height)]
        bsp = tcod.bsp.BSP(x=1, y=1, width=width - 2, height=height - 2)
        bsp.split_recursive(
            depth=_BSP_DEPTH,
            min_width=_ROOM_MIN,
            min_height=_ROOM_MIN,
            max_horizontal_ratio=1.5,
            max_vertical_ratio=1.5,
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
            continue
        for prev, cur in zip(rooms, rooms[1:]):
            _carve_tunnel(tiles, prev[0], cur[0])

        grid = Grid(width=width, height=height, tiles=tiles)
        rooms.sort(key=lambda room: room[0][0])  # left to right
        cells_by_room = [_room_cells(grid, rect) for _center, rect in rooms]
        player_start = rooms[0][0]
        # Exits: the leftmost floor cells of the entry room — the way the runner came in.
        # The entry room always has floor (its centre is player_start), so this is non-empty.
        exits = frozenset(sorted(cells_by_room[0])[:2])

        # Enemies hold the rooms away from the entry; fall back to any far floor if a
        # small map didn't leave enough.
        reserved = {player_start, *exits}
        spawn_pool = [cell for cells in cells_by_room[1:] for cell in cells if cell not in reserved]
        if len(spawn_pool) < enemy_count:
            spawn_pool = [cell for cells in cells_by_room for cell in cells if cell not in reserved]
        if len(spawn_pool) < enemy_count:
            continue
        enemy_spawns = rng.sample(spawn_pool, enemy_count)

        # Scatter low cover in room interiors, never on anyone's start or an exit, then
        # prove it didn't sever the map before handing it back.
        keep_clear = {player_start, *exits, *enemy_spawns}
        for cells in cells_by_room:
            for cell in cells:
                if cell not in keep_clear and rng.random() < cover_density:
                    tiles[cell[1]][cell[0]] = Tile.LOW_COVER
        if all(
            target == player_start or path_between(grid, player_start, target)
            for target in (*enemy_spawns, *exits)
        ):
            return TacticalMap(grid, player_start, enemy_spawns, exits)
    raise RuntimeError("could not generate a playable tactical map")
