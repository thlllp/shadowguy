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
from typing import TYPE_CHECKING

import numpy as np
import tcod
import tcod.bsp
import tcod.random

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check, resolve_rng
from shadowguy.combat import (
    Enemy,
    attack_verbs,
    combat_consumables,
    consumables_with,
    equipped_weapons,
    player_defense,
    player_soak,
    resolve_hit,
    smartlink_bonus,
)
from shadowguy.shops import CONSUMABLES_BY_ID, Consumable, EffectKind, Item
from shadowguy.skills import skill_value

if TYPE_CHECKING:
    # buildings.py imports *this* module for its grid primitives, so the arrow only
    # points one way at runtime: a burglary hands its Building in, and everything this
    # module does with one (walk a level's grid, follow a link) is duck-typed. Same
    # trick, same reason, as rivals.py's Fixer import.
    from shadowguy.buildings import Building, Lock

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
# A hired runner moves like everyone else; they're a unit on the same board, and giving
# backup its own movement rule would be a second thing to reason about for no gain.
ALLY_SPEED = 4

# Cover raises the to-hit difficulty against a unit hugging it, on the side facing the
# shooter: a full wall is worth more than a low crate you can shoot over. Added straight
# to the resolve_hit difficulty (which pool_for_difficulty turns into a bigger dodge
# pool), so cover is "harder to hit me" in the exact same formula, no special case.
FULL_COVER = 4
HALF_COVER = 2

# A thrown grenade's reach and blast, both in chebyshev distance (see targets_for's use
# of the same metric for weapon range). RADIUS=1 is a literal 3x3 square centered on the
# target tile — every enemy within one step of where it lands, not just the one tile.
# First-slice tuning, like the grenade catalog itself (shops.py's _CONSUMABLE_ROWS
# comment) — not yet balance-simulated.
GRENADE_RANGE = 5
GRENADE_RADIUS = 1


class Side(StrEnum):
    PLAYER = "player"
    ALLY = "ally"  # a hired runner fighting on the player's side (combat.crew_stats)
    ENEMY = "enemy"


class AimKind(StrEnum):
    """What the aim cursor is currently pointing *for*, and so which confirm the screen's
    Enter resolves (see confirm_aim). All three drive the same cursor with the same keys;
    they differ only in what makes a cell legal and what lands there — an attack needs an
    enemy standing on the cell and a weapon that reaches it, a grenade needs a tile in
    throwing range and takes anything in the blast with it. LOOK confirms nothing (see
    begin_look) — it's read-only, so confirm_aim has no branch for it."""

    ATTACK = "attack"
    GRENADE = "grenade"
    LOOK = "look"


class TacticalOutcome(StrEnum):
    ONGOING = "ongoing"
    VICTORY = "victory"  # every enemy down
    ESCAPED = "escaped"  # player left by an exit tile
    DEAD = "dead"  # player at 0 health
    # Burglary only: reached what you came for. Clearing the guards is *not* how a
    # burglary ends -- the thing you came to steal is still upstairs.
    SECURED = "secured"


@dataclass
class Unit:
    """One combatant on the grid.

    Every unit but the player carries a `combat.Enemy` stat block in `stats` and its
    current `health` here — hostile squad and hired crew alike, since `Enemy` is really
    "a combatant who isn't the player" (see combat.crew_stats). `side` is the only thing
    that says which way a unit is pointing. The *player's* health stays on the Character —
    the single source of truth combat.py already mutates — so the player Unit is the one
    with `stats is None` and an unused `health` field. `speed` is the per-turn move
    budget (see PLAYER_SPEED)."""

    name: str
    side: Side
    coord: Coord
    speed: int
    stats: Enemy | None = None
    health: int = 0
    # Enemy-only, set by a thrown Webbing/Flash-family grenade (EffectKind.COMBAT_STUN):
    # this unit sits out its next `stunned_rounds` enemy phases entirely — no move, no
    # attack — same "a round they owe you" meaning as combat.Fighter.stunned_rounds.
    stunned_rounds: int = 0

    # Ally-only: a downed hire whose bleeding the player stopped with a health kit
    # (stabilize_ally). Doesn't put them back in the fight — it decides whether they
    # walk away from it (resolve_downed_crew).
    stabilized: bool = False
    # Whether this unit knows there's anyone to fight. True everywhere except a
    # burglary's guards, who stand their post until they see you (check_detection): an
    # unalerted unit sits out the AI phase entirely, which is what makes sneaking past
    # one possible at all.
    alerted: bool = True

    @property
    def is_enemy(self) -> bool:
        return self.side is Side.ENEMY

    @property
    def is_ally(self) -> bool:
        """A hired runner fighting beside the player."""
        return self.side is Side.ALLY

    @property
    def is_down(self) -> bool:
        return self.health <= 0


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
    # Targeting, in progress between begin_attack_aim/begin_grenade_aim and
    # confirm_aim/cancel_aim. A non-None cursor means the screen is in aim mode: arrow
    # keys move this cursor instead of the player (tactical_screen.action_move), and
    # `aim_kind` says what confirming it does. None of these fields costs the turn's
    # action on its own — only a resolved attack or throw sets `acted` — so backing out
    # of an aim is free. `pending_grenade_index` is set for AimKind.GRENADE only.
    aim_cursor: Coord | None = None
    aim_kind: AimKind | None = None
    pending_grenade_index: int | None = None
    # Burglary only (None for an ordinary fight, which is one room with nothing to
    # steal). `building` is the whole place; `grid` above is whichever level is on the
    # board right now, and `units` the people standing on it -- everyone else waits in
    # `off_level_units` until the player takes a stair to them. `objective` is where the
    # score is, as (level, cell), and reaching it is how a burglary ends.
    building: "Building | None" = None
    level_index: int = 0
    objective: tuple[int, Coord] | None = None
    off_level_units: dict[int, list[Unit]] = field(default_factory=dict, repr=False)
    # Set the moment anybody sees you. Guards that were standing their post start
    # fighting, and no amount of hiding puts it back.
    alarm: bool = False
    # What became of any hire who went down, filled by _end_fight when the fight ends
    # (None while it's still running). The screen reports it; it isn't the screen's to
    # decide — see resolve_downed_crew.
    crew_aftermath: list[tuple[str, "CrewFate"]] | None = None

    @property
    def player(self) -> Unit:
        return next(u for u in self.units if u.side is Side.PLAYER)

    @property
    def enemies(self) -> list[Unit]:
        """Every enemy still standing."""
        return [u for u in self.units if u.is_enemy and u.health > 0]

    @property
    def allies(self) -> list[Unit]:
        """Every hired runner still standing."""
        return [u for u in self.units if u.is_ally and u.health > 0]

    @property
    def downed_allies(self) -> list[Unit]:
        """Hires bleeding on the floor. Out of the fight either way — what's still open
        is whether they walk away from it (stabilize_ally / resolve_downed_crew)."""
        return [u for u in self.units if u.is_ally and u.is_down]

    @property
    def friendlies(self) -> list[Unit]:
        """Everyone an enemy could shoot at: the player (while alive) and any ally still
        up. The player comes first, so a tie in the AI's target policy falls on them —
        a hire is backup, not a meat shield that quietly soaks every round."""
        player = self.player
        return ([player] if self.character.is_alive else []) + self.allies

    @property
    def is_over(self) -> bool:
        return self.outcome is not TacticalOutcome.ONGOING

    def occupied(self, *, exclude: Unit | None = None) -> frozenset[Coord]:
        """Cells a unit stands on — what blocks movement and pathing this instant. Living
        units only: a downed enemy (or a downed hire) is a corpse you can walk over, not
        a wall. The player is always in the set; a dead player ends the fight anyway."""
        return frozenset(
            u.coord for u in self.units if u is not exclude and (u.health > 0 or u.side is Side.PLAYER)
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


def ally_spawns(grid: Grid, player_start: Coord, count: int, taken: frozenset[Coord]) -> list[Coord]:
    """Where `count` hired runners stand at the start of a fight: the open cells nearest
    the player they came in with, ringing outward. Fewer coords than asked for if the
    entry room is too cramped — the caller drops the hires that don't fit rather than
    stacking two units on a tile."""
    found: list[Coord] = []
    frontier, seen = [player_start], {player_start, *taken}
    while frontier and len(found) < count:
        cell = frontier.pop(0)
        for neighbor in step_neighbors(grid, cell, blocked=frozenset()):
            if neighbor in seen:
                continue
            seen.add(neighbor)
            frontier.append(neighbor)
            found.append(neighbor)
            if len(found) == count:
                break
    return found


def start_tactical(
    character: Character,
    grid: Grid,
    player_start: Coord,
    enemy_placements: list[tuple[Enemy, Coord]],
    exits: frozenset[Coord] = frozenset(),
    allies: list[Enemy] = (),
) -> TacticalState:
    """Set up a fight: place the player, each enemy, and any hired runner who came along
    (`allies`, stat blocks from combat.crew_stats — they spawn around the player via
    ally_spawns, and any that don't fit sit the fight out), then open the player's turn."""
    units = [Unit(name=character.name, side=Side.PLAYER, coord=player_start, speed=PLAYER_SPEED)]
    for enemy, coord in enemy_placements:
        units.append(
            Unit(
                name=enemy.name,
                side=Side.ENEMY,
                coord=coord,
                speed=ENEMY_SPEED,
                stats=enemy,
                health=enemy.health,
            )
        )
    spawns = ally_spawns(grid, player_start, len(allies), frozenset(u.coord for u in units))
    for ally, coord in zip(allies, spawns, strict=False):
        units.append(
            Unit(
                name=ally.name,
                side=Side.ALLY,
                coord=coord,
                speed=ALLY_SPEED,
                stats=ally,
                health=ally.health,
            )
        )
    state = TacticalState(character=character, grid=grid, units=units, exits=frozenset(exits))
    _begin_player_turn(state)
    return state


def start_burglary(
    character: Character,
    building: "Building",
    spawn: tuple[int, Coord],
    guard: Enemy,
    allies: list[Enemy] = (),
) -> TacticalState:
    """Set up a burglary: the same tactical fight every other stage plays, with three
    differences that make it a burglary rather than a shootout.

    The board is one *level* of a building and the player can take stairs to the others
    (take_stairs). The guards are real units but start unalerted, standing their post --
    they only act once somebody sees you (check_detection), which is what makes walking
    past one a thing you can do. And it ends when you reach what you came for
    (SECURED), not when the last guard falls: clearing the house is a way to make the
    rest of the walk quiet, never the win itself.

    Every entrance is an exit, so the way you came in is the way you bail."""
    exits = frozenset(coord for level, coord in building.entrance_spawns if level == spawn[0])
    state = TacticalState(
        character=character,
        grid=building.levels[spawn[0]].grid,
        units=[],
        exits=exits,
        building=building,
        level_index=spawn[0],
        objective=building.objective,
    )
    # Everybody, filed by the level they're standing on. The player joins whichever
    # level they entered by; _enter_level swaps the rest in as they're walked into.
    for level_index, coord in building.guards:
        state.off_level_units.setdefault(level_index, []).append(
            Unit(
                name=guard.name,
                side=Side.ENEMY,
                coord=coord,
                speed=ENEMY_SPEED,
                stats=guard,
                health=guard.health,
                alerted=False,
            )
        )
    player = Unit(name=character.name, side=Side.PLAYER, coord=spawn[1], speed=PLAYER_SPEED)
    spawns = ally_spawns(
        state.grid, spawn[1], len(allies), frozenset(u.coord for u in state.off_level_units.get(spawn[0], []))
    )
    crew = [
        Unit(name=ally.name, side=Side.ALLY, coord=coord, speed=ALLY_SPEED, stats=ally, health=ally.health)
        for ally, coord in zip(allies, spawns, strict=False)
    ]
    state.units = [player, *state.off_level_units.pop(spawn[0], []), *crew]
    _begin_player_turn(state)
    return state


def enter_level(state: TacticalState, index: int, coord: Coord) -> None:
    """Put the player on another level of the building, at `coord`. The board swaps
    wholesale: this level's units go into off_level_units, the target level's come out,
    and the player (with any crew still standing) comes along. A guard left behind stays
    exactly as they were -- alerted or not, hurt or not -- because they're still there."""
    if state.building is None:
        return
    player = state.player
    crew = [unit for unit in state.units if unit.is_ally]
    state.off_level_units[state.level_index] = [
        unit for unit in state.units if unit is not player and unit not in crew
    ]
    state.level_index = index
    state.grid = state.building.levels[index].grid
    player.coord = coord
    arriving = state.off_level_units.pop(index, [])
    for ally, spot in zip(crew, ally_spawns(state.grid, coord, len(crew), frozenset(u.coord for u in arriving)), strict=False):
        ally.coord = spot
    state.units = [player, *arriving, *crew]
    state.exits = frozenset(
        cell for level, cell in state.building.entrance_spawns if level == index
    )
    state.log.append(f"You move to the {state.building.levels[index].name.lower()}.")


def stairs_here(state: TacticalState) -> tuple[int, Coord] | None:
    """Where the cell the player is standing on leads, if it's a stair or a lift."""
    if state.building is None:
        return None
    return state.building.links_at(state.level_index, state.player.coord)


def take_stairs(state: TacticalState) -> bool:
    """Take the stairs under your feet. Costs a move, like any other step -- a floor is
    a place you walk to, not a free teleport. False when there's nothing to take or no
    movement left to spend."""
    destination = stairs_here(state)
    if destination is None or state.moves_left <= 0 or state.is_over:
        return False
    state.moves_left -= 1
    enter_level(state, *destination)
    check_detection(state)
    _settle(state)
    return True


def check_detection(state: TacticalState) -> bool:
    """Look for the player through every unalerted guard's eyes: within GUARD_SIGHT_RANGE
    and with a clear line is seen. Raises the alarm for the whole building the first time
    anyone does -- a shout carries -- and returns whether anyone just spotted them.

    A Building.cameras position gets the identical range+LOS test, but it's not a Unit --
    fixed, unalerted-forever, and (unlike a guard) nothing the player can fight or sneak
    past by knocking it out. Guards are checked first only because a guard's name makes
    the better log line when both would catch you the same turn.

    Called after each thing the player does that could give them away (moving, taking
    stairs, attacking), which is what makes position the whole game while sneaking."""
    if state.building is None or state.is_over:
        return False
    player = state.player.coord
    spotted_by = [
        unit
        for unit in state.enemies
        if not unit.alerted and in_reach(state.grid, unit.coord, player, GUARD_SIGHT_RANGE)
    ]
    if spotted_by:
        raise_alarm(state, f"{spotted_by[0].name} spots you.")
        return True
    seen_by_camera = any(
        level == state.level_index and in_reach(state.grid, coord, player, GUARD_SIGHT_RANGE)
        for level, coord in state.building.cameras
    )
    if not seen_by_camera:
        return False
    raise_alarm(state, "A camera catches you.")
    return True


def raise_alarm(state: TacticalState, reason: str) -> None:
    """It's gone loud. Every guard on this level drops their post and fights, and the
    ones elsewhere are alert by the time you reach them."""
    if state.alarm:
        return
    state.alarm = True
    state.log.append(reason)
    for unit in state.units:
        if unit.is_enemy:
            unit.alerted = True
    for waiting in state.off_level_units.values():
        for unit in waiting:
            unit.alerted = True


def reached_score(state: TacticalState) -> bool:
    """Whether the player is standing on what they came to steal."""
    return state.objective is not None and (state.level_index, state.player.coord) == state.objective


def _begin_player_turn(state: TacticalState) -> None:
    state.moves_left = state.player.speed
    state.acted = False


def legal_moves(state: TacticalState) -> list[Coord]:
    """Where the player may step this instant: one cardinal move into open, unoccupied
    floor, if they have moves left."""
    if state.moves_left <= 0 or state.is_over:
        return []
    return step_neighbors(state.grid, state.player.coord, blocked=state.occupied(exclude=state.player))


def move_player(state: TacticalState, dest: Coord, rng: random.Random | None = None) -> bool:
    """Spend the player's move on `dest`. Returns False (spending nothing) only if the
    step isn't legal at all -- a locked door resolves a pick-the-lock check instead of a
    plain step (see attempt_lock), and still returns True on a failed pick: the move was
    spent attempting it, even though state.player.coord didn't change. Check that
    directly if "did the player actually end up on dest" is what you need."""
    if dest not in legal_moves(state):
        return False
    lock = lock_at(state, dest)
    if lock is not None:
        attempt_lock(state, dest, lock, rng)
        return True
    state.player.coord = dest
    state.moves_left -= 1
    check_detection(state)
    _settle(state)
    return True


def lock_at(state: TacticalState, coord: Coord) -> "Lock | None":
    """The still-locked door on this cell of the level currently on the board, if any."""
    if state.building is None:
        return None
    return state.building.locks.get((state.level_index, coord))


def attempt_lock(state: TacticalState, dest: Coord, lock: "Lock", rng: random.Random | None = None) -> None:
    """Try the lock instead of just walking through it. Costs the move either way:
    success clears it for good and steps the player through; failure leaves it standing
    and risks the alarm -- an ordinary failure only sometimes (LOCK_FAILURE_ALARM_CHANCE),
    a critical failure always, same shape as a burglary entrance's own critical failure."""
    rng = resolve_rng(rng)
    roll = resolve_check(skill_value(state.character, lock.skill), lock.difficulty, rng=rng)
    state.moves_left -= 1
    if roll.result.passed:
        del state.building.locks[(state.level_index, dest)]
        state.player.coord = dest
        state.log.append("The lock gives. You're through.")
    elif roll.result is CheckResult.CRITICAL_FAILURE or rng.random() < LOCK_FAILURE_ALARM_CHANCE:
        state.log.append("The lock holds, and something trips.")
        raise_alarm(state, "An alarm you didn't see screams.")
    else:
        state.log.append("The lock holds.")
    check_detection(state)
    _settle(state)


def in_reach(grid: Grid, origin: Coord, target: Coord, reach: int) -> bool:
    """Range and line of sight, the two gates DESIGN.md keeps deliberately separate,
    asked together. Every attack in this module goes through here — the player's
    (can_hit, off weapon_range) and the AI's (_can_reach, off Enemy.reach) — so there is
    one place that spells out what "able to attack that" means."""
    return chebyshev(origin, target) <= reach and has_line_of_sight(grid, origin, target)


def can_hit(state: TacticalState, weapon: Item, target: Unit) -> bool:
    """Whether this weapon reaches this enemy right now: standing, in range and in sight.
    targets_for lists it per weapon, weapon_for_target inverts it per target."""
    return target.health > 0 and in_reach(
        state.grid, state.player.coord, target.coord, weapon_range(weapon)
    )


def targets_for(state: TacticalState, weapon: Item) -> list[Unit]:
    """Enemies the player could hit with this weapon right now."""
    return [enemy for enemy in state.enemies if can_hit(state, weapon, enemy)]


def weapon_for_target(state: TacticalState, target: Unit) -> Item | None:
    """Which weapon the player attacks `target` with — the hardest-hitting equipped one
    that reaches it, or None if nothing does. This is what makes aiming a *unit* enough
    to resolve an attack: the player picks who, the weapon follows from where they're
    standing (a knife only for someone at arm's length, the gun for anyone further out)."""
    reaching = [weapon for weapon in player_weapons(state) if can_hit(state, weapon, target)]
    return max(reaching, key=lambda weapon: weapon.damage, default=None)


def attack_targets(state: TacticalState) -> list[Unit]:
    """Every enemy some equipped weapon can reach right now — the set the aim cursor
    snaps between (snap_aim_to_next_target) and the one "no shot" is read off."""
    return [enemy for enemy in state.enemies if weapon_for_target(state, enemy) is not None]


def enemy_at(state: TacticalState, coord: Coord) -> Unit | None:
    """The standing enemy on this cell, if any — how a cursor position becomes a target."""
    return next((enemy for enemy in state.enemies if enemy.coord == coord), None)


def player_attack(state: TacticalState, target: Unit, weapon: Item, rng: random.Random | None = None) -> None:
    """Resolve the player's one action: an attack, through combat.resolve_hit, with the
    target's cover folded into the to-hit difficulty. Spends the action for the turn."""
    rng = resolve_rng(rng)
    if state.acted or state.is_over or not can_hit(state, weapon, target):
        return
    state.acted = True
    raise_alarm(state, "The noise carries. They know you're here.")
    difficulty = target.stats.defense + cover_bonus(state.grid, target.coord, state.player.coord)
    roll, damage = resolve_hit(
        rng,
        skill_value(state.character, weapon.skill),
        smartlink_bonus(state.character, weapon),
        difficulty,
        weapon.damage,
        target.stats.toughness,
    )
    miss_verb, hit_verb = attack_verbs(weapon)
    if not roll.result.passed:
        state.log.append(f"You {miss_verb} {target.name} and miss.")
        return
    target.health = max(0, target.health - damage)
    if target.health <= 0:
        state.log.append(f"You drop {target.name}.")  # a kill reads the same however it landed
    else:
        state.log.append(f"You {hit_verb} {target.name} for {damage}.")
    _settle(state)


def healing_kits(character: Character) -> list[tuple[int, Consumable]]:
    """The health kits the runner is carrying — the stabilize counterpart of
    combat.combat_consumables, off the same combat.consumables_with. Any HEAL consumable
    works, so an Advanced Health Kit stabilizes exactly like a basic one (nothing here
    reads `amount`: you're stopping the bleeding, not healing them)."""
    return consumables_with(character, {EffectKind.HEAL})


def stabilize_targets(state: TacticalState) -> list[Unit]:
    """Downed hires the player could stabilize this instant: bleeding, not already
    stabilized, and within arm's reach. Empty once the turn's action is spent, or with
    no kit left to spend on them."""
    if state.acted or state.is_over or not healing_kits(state.character):
        return []
    return [
        ally
        for ally in state.downed_allies
        if not ally.stabilized and chebyshev(state.player.coord, ally.coord) <= MELEE_RANGE
    ]


def stabilize_ally(state: TacticalState) -> str | None:
    """Spend the turn's action and one health kit to stop a downed hire bleeding out.

    They stay down — this is first aid under fire, not a revival: what it buys is the
    aftermath (resolve_downed_crew), where a stabilized runner walks away and an
    unstabilized one may not. Costing a whole turn is the point; standing over a body in
    a firefight is a real decision, and one an unstabilized-but-alive hire lets you
    refuse. Deliberately *not* gated on Character.health_kit_used_today: that cap is
    about a runner topping themselves up between fights, not about who they can patch.

    *Which* body gets the kit is policy, so it's decided here rather than by the screen:
    the nearest one you could reach. Returns None on success, or why not — spending
    nothing — so a refusal can't drift out of step with what stabilize_targets allows.
    """
    targets = stabilize_targets(state)
    if not targets:
        return _no_stabilize_reason(state)
    ally = min(targets, key=lambda unit: chebyshev(state.player.coord, unit.coord))
    index, kit = healing_kits(state.character)[0]
    state.character.consumables.pop(index)
    state.acted = True
    ally.stabilized = True
    state.log.append(f"You put {kit.name} into {ally.name}. They're stable, but out of this one.")
    return None


def _no_stabilize_reason(state: TacticalState) -> str:
    """Which of stabilize_targets' gates is the one shutting the player out — read in the
    same order it applies them, so the two can't disagree."""
    if state.acted:
        return "You've already acted this turn."
    if not [ally for ally in state.downed_allies if not ally.stabilized]:
        return "Nobody on your crew is down."
    if not healing_kits(state.character):
        return "No health kit to patch them with."
    return "Step next to them first."


def begin_attack_aim(state: TacticalState) -> bool:
    """Enter targeting mode for an attack: the same cursor a grenade throw aims with (see
    begin_grenade_aim), pointed at units instead of tiles. Opens on best_shot's default
    target, so the common case is Enter straight away. Spends nothing — only
    confirm_attack_aim's resolved attack sets `acted`.

    Returns False, having done nothing, when there's no shot to open on: already acted,
    fight over, or nothing in sight and range. The caller doesn't have to ask best_shot
    itself first — asking costs a line-of-sight pass per enemy."""
    if state.acted or state.is_over:
        return False
    shot = best_shot(state)
    if shot is None:
        return False
    state.aim_cursor = shot.coord
    state.aim_kind = AimKind.ATTACK
    return True


def legal_attack_target(state: TacticalState, coord: Coord) -> bool:
    """Whether the player could actually attack whatever is on this cell right now: a
    standing enemy with an equipped weapon that reaches it. The attack counterpart of
    legal_grenade_target, and what colours the cursor while aiming."""
    enemy = enemy_at(state, coord)
    return enemy is not None and weapon_for_target(state, enemy) is not None


def confirm_attack_aim(state: TacticalState, rng: random.Random | None = None) -> bool:
    """Resolve the aimed attack at the cursor with weapon_for_target's pick. Returns False
    and stays in aim mode when there's nothing hittable there, same as
    confirm_grenade_aim's bad-tile behaviour — a misaimed cursor costs the player a
    keypress, not their action."""
    if state.aim_kind is not AimKind.ATTACK or state.aim_cursor is None:
        return False
    target = enemy_at(state, state.aim_cursor)
    weapon = None if target is None else weapon_for_target(state, target)
    if weapon is None:
        return False
    cancel_aim(state)
    player_attack(state, target, weapon, rng)
    return True


def aim_is_legal(state: TacticalState, coord: Coord) -> bool:
    """Whether confirming on this cell would resolve, for whichever aim is running —
    the one call a renderer needs to colour the cursor without knowing the kind."""
    if state.aim_kind is AimKind.ATTACK:
        return legal_attack_target(state, coord)
    if state.aim_kind is AimKind.GRENADE:
        return legal_grenade_target(state, coord)
    return False


def snap_aim_to_next_target(state: TacticalState) -> bool:
    """Jump the cursor to the next enemy worth aiming at, wrapping — nearest first, so
    tapping through the ring goes outward from the player rather than in map order. What
    counts as "worth aiming at" is the running aim's own legality: an enemy some weapon
    reaches when attacking, an enemy standing on a throwable tile when aiming a grenade.
    Returns False if there's no aim running or nothing to snap to (the cursor stays put,
    so the player can still walk it somewhere by hand)."""
    if state.aim_cursor is None:
        return False
    order = sorted(
        (enemy.coord for enemy in state.enemies if aim_is_legal(state, enemy.coord)),
        key=lambda coord: (chebyshev(state.player.coord, coord), coord),
    )
    if not order:
        return False
    index = order.index(state.aim_cursor) + 1 if state.aim_cursor in order else 0
    state.aim_cursor = order[index % len(order)]
    return True


def cancel_aim(state: TacticalState) -> None:
    """Back out of targeting with nothing spent, whichever aim is running."""
    state.aim_cursor = None
    state.aim_kind = None
    state.pending_grenade_index = None


def confirm_aim(state: TacticalState, rng: random.Random | None = None) -> bool:
    """Resolve whatever the cursor is aiming — the screen's Enter, with the attack/throw
    split kept here rather than in the UI. False means "not legal there, still aiming"."""
    if state.aim_kind is AimKind.ATTACK:
        return confirm_attack_aim(state, rng)
    if state.aim_kind is AimKind.GRENADE:
        return confirm_grenade_aim(state)
    return False


# The two grenade effects a blast radius actually applies to. COMBAT_ESCAPE isn't aimed
# at anyone (walking out isn't a target), so it's the one grenade throw_grenade resolves
# with no tile at all — same three-way split as combat._throw, this is just the subset
# that also needs a place to land first.
_TARGETED_GRENADE_EFFECTS = frozenset({EffectKind.COMBAT_STUN, EffectKind.COMBAT_DAMAGE_ALL})


def grenade_needs_target(consumable: Consumable) -> bool:
    """Whether throwing this grenade means picking a tile first (begin_grenade_aim)
    rather than resolving immediately. Only the two area effects are targeted; a smoke
    grenade gets the runner out with nothing to aim at."""
    return consumable.effect in _TARGETED_GRENADE_EFFECTS


def legal_grenade_target(state: TacticalState, coord: Coord) -> bool:
    """Whether `coord` is a tile the player could actually land a grenade on right now:
    in bounds, within GRENADE_RANGE, and in sight — the same shape as targets_for's
    range+LOS gate on a weapon, just against a tile instead of a unit."""
    return (
        state.grid.in_bounds(coord)
        and chebyshev(state.player.coord, coord) <= GRENADE_RANGE
        and has_line_of_sight(state.grid, state.player.coord, coord)
    )


def begin_grenade_aim(state: TacticalState, consumable_index: int) -> None:
    """Enter tile-targeting mode for Character.consumables[consumable_index]: arrow keys
    move state.aim_cursor instead of the player (tactical_screen.action_move) until
    confirm_aim resolves the throw or cancel_aim backs out. Starts the cursor on the
    player's own tile — always a legal target (range 0), a safe default to nudge from.
    Doesn't touch Character.consumables or state.acted; only a resolved throw spends
    either."""
    if state.acted or state.is_over:
        return
    state.aim_cursor = state.player.coord
    state.aim_kind = AimKind.GRENADE
    state.pending_grenade_index = consumable_index


def begin_look(state: TacticalState) -> bool:
    """Enter the same cursor mode aim/throw use, pointed at nothing in particular — just a
    way to read the map before committing to a move. Starts on the player's own tile, the
    same safe default begin_grenade_aim opens on. Spends nothing and needs no target, so
    it only refuses when the fight's already over."""
    if state.is_over:
        return False
    state.aim_cursor = state.player.coord
    state.aim_kind = AimKind.LOOK
    return True


def move_aim_cursor(state: TacticalState, dx: int, dy: int) -> None:
    """Nudge the aim cursor one cell, clamped to the grid. Bounds-only — unlike
    legal_moves, the cursor isn't gated by range/LOS/occupancy while moving, only at
    confirm (legal_grenade_target); a "you're out of range, back it up" cursor is more
    useful mid-aim than one that refuses to go there at all."""
    if state.aim_cursor is None:
        return
    x, y = state.aim_cursor
    dest = (x + dx, y + dy)
    if state.grid.in_bounds(dest):
        state.aim_cursor = dest


def confirm_grenade_aim(state: TacticalState) -> bool:
    """Resolve the pending grenade at the aim cursor. Returns False and leaves aim mode
    running if the cursor isn't a legal_grenade_target right now, so the screen can
    prompt the player to adjust rather than lose the throw to a bad tile."""
    if state.aim_cursor is None or state.pending_grenade_index is None:
        return False
    if not legal_grenade_target(state, state.aim_cursor):
        return False
    index, target = state.pending_grenade_index, state.aim_cursor
    cancel_aim(state)
    throw_grenade(state, index, target)
    return True


def throw_grenade(state: TacticalState, consumable_index: int, target: Coord | None = None) -> None:
    """Resolve the player's one action as a grenade throw instead of an attack: pops
    Character.consumables[consumable_index] and applies it exactly the way combat.py's
    abstract fight does (see combat._throw), with one difference space adds — COMBAT_STUN
    and COMBAT_DAMAGE_ALL land as a blast centered on `target`, hitting every enemy within
    GRENADE_RADIUS of it (a 3x3 square) rather than everyone standing, while COMBAT_ESCAPE
    stays positionless, same as the abstract fight, since walking out isn't aimed at
    anyone. Call this directly (target=None) for an untargeted throw, or through
    confirm_grenade_aim once a tile is picked, for the other two. Spends the action for
    the turn, same as player_attack."""
    if state.acted or state.is_over:
        return
    consumable = CONSUMABLES_BY_ID[state.character.consumables[consumable_index]]
    if target is None and grenade_needs_target(consumable):
        raise ValueError(f"{consumable.id}: needs a target tile (see begin_grenade_aim)")
    state.acted = True
    state.character.consumables.pop(consumable_index)
    if consumable.effect is EffectKind.COMBAT_DAMAGE_ALL:
        hit = [enemy for enemy in state.enemies if chebyshev(target, enemy.coord) <= GRENADE_RADIUS]
        state.log.append(f"{consumable.name} — {consumable.amount} to everything in the blast.")
        for enemy in hit:
            enemy.health = max(0, enemy.health - consumable.amount)
        _settle(state)
    elif consumable.effect is EffectKind.COMBAT_STUN:
        hit = [enemy for enemy in state.enemies if chebyshev(target, enemy.coord) <= GRENADE_RADIUS]
        for enemy in hit:
            enemy.stunned_rounds = consumable.amount
        state.log.append(f"{consumable.name} — {len(hit)} pinned down for {consumable.amount}.")
    elif consumable.effect is EffectKind.COMBAT_ESCAPE:
        state.log.append(f"{consumable.name} — you slip out under cover.")
        _end_fight(state, TacticalOutcome.ESCAPED)
    else:
        # Same guard as combat._throw, from the other side: a new combat-only effect
        # with no branch here would otherwise be popped and silently do nothing.
        raise ValueError(f"consumable effect not handled in tactical combat: {consumable.effect}")


def leave(state: TacticalState, rng: random.Random | None = None) -> bool:
    """Walk out — but only from an exit tile. Positional escape: getting to the door *is*
    the flee, so there's no roll and no parting shot; the risk was crossing the room to
    reach it. Returns False if the player isn't standing on an exit."""
    if state.is_over or state.player.coord not in state.exits:
        return False
    state.log.append("You slip out.")
    _end_fight(state, TacticalOutcome.ESCAPED, rng)
    return True


def end_turn(state: TacticalState, rng: random.Random | None = None) -> None:
    """End the player's turn: the hired crew acts, then the enemy phase, then the next
    player turn opens. Allies go first because they're on your side of the round — the
    fire they draw and the enemies they drop are part of what your turn bought."""
    rng = resolve_rng(rng)
    if state.is_over:
        return
    # Allies go first because they're on your side of the round — the fire they draw and
    # the enemies they drop are part of what your turn bought.
    _ai_phase(state, allied=True, rng=rng)
    if not state.is_over:
        _ai_phase(state, allied=False, rng=rng)
    _settle(state, rng)
    if not state.is_over:
        _begin_player_turn(state)


def _can_reach(state: TacticalState, attacker: Unit, target: Unit) -> bool:
    """Whether this unit has a shot at that one right now. Melee (reach 1) needs to be
    adjacent; a guard's gun only needs the sightline. Side-agnostic — it's the same
    question for a Sec Heavy and for your Solo."""
    return in_reach(state.grid, attacker.coord, target.coord, attacker.stats.reach)


def pick_target(state: TacticalState, attacker: Unit, candidates: list[Unit]) -> Unit | None:
    """Who a unit goes after — **the one they can actually hit**, and the one ranking both
    sides use.

    Candidates are ordered by the cover between them and the attacker (`cover_bonus` —
    the same number that raises the to-hit difficulty), then by distance, with anyone the
    attacker has no line to sorting last. So ducking behind a wall genuinely redirects
    fire onto whoever is standing in the open: cover stops being "my rolls got better"
    and becomes a decision about who eats the round. `min` is stable, so the candidate
    list's own order breaks exact ties — which is why `friendlies` puts the player first,
    and a hire is never a quiet meat shield. Called before movement too, so a unit crosses
    the room toward the target it *wants*, not the nearest body.

    One FOV pass covers every candidate (see visible_tiles); None when the list is empty."""
    seen = visible_tiles(state.grid, attacker.coord)
    return min(
        candidates,
        key=lambda target: (
            not seen[target.coord[1], target.coord[0]],
            cover_bonus(state.grid, target.coord, attacker.coord),
            chebyshev(attacker.coord, target.coord),
        ),
        default=None,
    )


def enemy_target(state: TacticalState, enemy: Unit) -> Unit | None:
    """Who this enemy shoots at: the player or a hire, by pick_target's ranking."""
    return pick_target(state, enemy, state.friendlies)


def _advance_and_attack(state: TacticalState, attacker: Unit, target: Unit, rng: random.Random) -> None:
    """Close via A* (up to `speed`) until the target is in reach, then hit it. The shared
    body of both AI phases — a ranged unit holds its distance because it stops advancing
    the moment it has a shot, while melee has to walk all the way in."""
    reached = _can_reach(state, attacker, target)
    if not reached:
        path = path_between(
            state.grid, attacker.coord, target.coord, blocked=state.occupied(exclude=attacker)
        )
        # Path ends on the target's own tile; don't step onto it — stop the step before.
        for step in path[: attacker.speed]:
            if step == target.coord:
                break
            attacker.coord = step
            if (reached := _can_reach(state, attacker, target)):
                break
    if reached:
        _unit_attack(state, attacker, target, rng)
        _settle(state, rng)


def _ai_phase(state: TacticalState, *, allied: bool, rng: random.Random) -> None:
    """One side's turn: each unit picks a target (pick_target), closes until it can hit
    them, then does. Targets are re-picked per unit per round rather than locked in at the
    start of the fight, so a runner who steps out of cover draws the next one's fire.

    Both sides run this same body — a hire fights the way a Sec Heavy does, pointed the
    other way. They're AI-driven, not a second unit you steer: a hire you have to
    micromanage is a second character, not backup."""
    for unit in state.allies if allied else state.enemies:
        if state.is_over:
            return
        if not unit.alerted:
            continue  # standing their post, none the wiser -- see check_detection
        if unit.stunned_rounds > 0:
            unit.stunned_rounds -= 1
            state.log.append(f"{unit.name} is still reeling.")
            continue
        target = pick_target(state, unit, state.enemies if allied else state.friendlies)
        if target is None:
            return
        _advance_and_attack(state, unit, target, rng)


def _unit_attack(state: TacticalState, attacker: Unit, target: Unit, rng: random.Random) -> None:
    """One AI attack, either direction: an enemy shooting at the player or their crew, or
    a hired runner shooting back. Runs through combat.resolve_hit like every other attack
    in the game, with the target's cover on the difficulty. Whose numbers get used is the
    only fork — the player's defense/soak come off the Character, everyone else's off
    their `stats` block — and damage lands in the matching place."""
    is_player = target is state.player
    difficulty = (
        player_defense(state.character) if is_player else target.stats.defense
    ) + cover_bonus(state.grid, target.coord, attacker.coord)
    soak = player_soak(state.character) if is_player else target.stats.toughness
    roll, damage = resolve_hit(rng, attacker.stats.attack, 0, difficulty, attacker.stats.damage, soak)
    # Every line names who was shot at: with two runners on the board, who a shot was
    # aimed at is the whole point — it's how cover redirecting fire reads.
    who = "you" if is_player else target.name
    if not roll.result.passed:
        state.log.append(f"{attacker.name} swings wide at {who}.")
        return
    if is_player:
        state.character.adjust_health(-damage)
    else:
        target.health = max(0, target.health - damage)
    if not is_player and target.is_down:
        state.log.append(f"{attacker.name} puts {who} down.")
    elif damage:
        state.log.append(f"{attacker.name} hits {who} for {damage}.")
    else:
        state.log.append(f"{attacker.name} connects with {who}, but it doesn't get through.")


def _settle(state: TacticalState, rng: random.Random | None = None) -> None:
    """Read the board. Death first: a mutual kill still kills you.

    A burglary ends on the score, never on an empty board -- putting the last guard down
    makes the rest of the house quiet, but the thing you came for is still upstairs."""
    if not state.character.is_alive:
        _end_fight(state, TacticalOutcome.DEAD, rng)
    elif reached_score(state):
        _end_fight(state, TacticalOutcome.SECURED, rng)
    elif state.objective is None and not state.enemies:
        _end_fight(state, TacticalOutcome.VICTORY, rng)


def _end_fight(state: TacticalState, outcome: TacticalOutcome, rng: random.Random | None = None) -> None:
    """The one place a fight ends. Sets the outcome and settles anyone who went down with
    you (resolve_downed_crew) — so a hire's fate follows from the fight ending, not from
    whoever happens to render or dismiss the screen afterwards. A player death skips it:
    the run is over, and there is nobody left to carry anyone out."""
    state.outcome = outcome
    state.crew_aftermath = [] if outcome is TacticalOutcome.DEAD else resolve_downed_crew(state, rng)


class CrewFate(StrEnum):
    """What became of a hire who went down, once the shooting stopped."""

    RECOVERED = "recovered"  # patched up and back on the street
    ARRESTED = "arrested"  # picked up at the scene, off the roster for a stretch
    KILLED = "killed"  # gone for the run


# How long a runner is held after being picked up at a scene, in days. First-slice
# tuning like the rest of this block — long enough to hurt a crew plan, short enough
# that a three-runner roster isn't gutted by one bad night.
ARREST_DAYS = 7

# The odds a downed hire faces, as (killed, arrested) — the remainder is RECOVERED.
# Two things decide which row applies: whether you stopped their bleeding
# (stabilize_ally) and whether you *held the field*. Winning means you can carry them
# out; walking out an exit means leaving them where they fell, for whoever arrives next.
# So the fight's ending is what turns a downed hire into a dead one, which is exactly
# the pressure a body on the floor should put on the decision to run.
# Not balance-simulated (nothing involving crew is yet).
_CREW_FATES: dict[tuple[bool, bool], tuple[float, float]] = {
    # (stabilized, held_the_field): (killed, arrested)
    (True, True): (0.00, 0.00),  # stable, and you carried them out
    (False, True): (0.25, 0.00),  # you won, but they'd been bleeding the whole time
    (True, False): (0.00, 0.60),  # alive where you left them — someone else finds them
    (False, False): (0.40, 0.40),  # left bleeding on someone else's floor
}


def resolve_downed_crew(
    state: TacticalState, rng: random.Random | None = None
) -> list[tuple[str, CrewFate]]:
    """Settle every hire who went down, once the fight is over. Applies each fate to the
    Character (a killed or arrested runner is discharged and comes off the roster — see
    Character.record_runner_killed/record_runner_arrested) and returns (name, fate) pairs
    for the screen to report.

    Only meaningful on a finished fight; a player death doesn't reach here at all (the
    run is over). Idempotent by construction only in the sense that it clears nothing —
    call it once per fight, at the end (TacticalScreen does, when the outcome lands)."""
    rng = resolve_rng(rng)
    held_the_field = state.outcome is TacticalOutcome.VICTORY
    results: list[tuple[str, CrewFate]] = []
    for ally in state.downed_allies:
        killed_odds, arrested_odds = _CREW_FATES[(ally.stabilized, held_the_field)]
        roll = rng.random()
        if roll < killed_odds:
            fate = CrewFate.KILLED
            state.character.record_runner_killed(ally.stats.id)
        elif roll < killed_odds + arrested_odds:
            fate = CrewFate.ARRESTED
            state.character.record_runner_arrested(ally.stats.id, ARREST_DAYS)
        else:
            fate = CrewFate.RECOVERED
        results.append((ally.name, fate))
    return results


def player_weapons(state: TacticalState) -> list[Item]:
    """The weapons the player can attack with this fight — their equipped gear, or fists."""
    return equipped_weapons(state.character)


def best_shot(state: TacticalState) -> Unit | None:
    """The enemy the player shoots by default: the nearest one some equipped weapon
    reaches. None if there's no shot — already acted, or nothing in sight and range.
    Which weapon fires isn't part of the answer, because it follows from the target
    (weapon_for_target) rather than from this choice. Fight *policy*, not view, so it
    lives here; the screen (as the aim cursor's starting target, see begin_attack_aim)
    and any headless driver share it."""
    if state.acted:
        return None
    origin = state.player.coord
    return min(attack_targets(state), key=lambda enemy: chebyshev(origin, enemy.coord), default=None)


def available_grenades(state: TacticalState) -> list[tuple[int, Consumable]]:
    """The grenades throw_grenade is legal for right now: every combat-only consumable
    the runner carries (combat.combat_consumables — the same set combat.py's abstract
    fight offers), or none once the action for the turn is already spent. Same "fight
    policy lives here, not the screen" reasoning as best_shot."""
    if state.acted or state.is_over:
        return []
    return combat_consumables(state.character)


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


def _bsp_rooms(
    tiles: list[list[Tile]], width: int, height: int, rng: random.Random, depth: int = _BSP_DEPTH
) -> list[tuple[Coord, tuple[int, int, int, int]]] | None:
    """Carve BSP rooms and corridors into tiles. Returns room list or None. `depth` is
    room granularity -- deeper splits the same footprint into more, smaller rooms, which
    is how a residential block differs from a fight map carved at the same size."""
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


# ---------------------------------------------------------------------------
# Burglary buildings. A different shape of generated map from TacticalMap: not one
# player_start converging enemies onto it, but several candidate entry points (one
# per Entrance the runner could pick, see scene.BurglaryStage) converging on one
# objective, with static guards to avoid rather than a squad to fight. Reuses the
# same BSP room-carving as generate_map (_bsp_rooms already carves the connecting
# tunnels; nothing here assumes a single entry room the way generate_map's own
# player_start/exits selection does).
# ---------------------------------------------------------------------------

# A guard's static sightline is capped the same way combat.Enemy.reach caps an
# attack's: has_line_of_sight is unlimited-range by design (see _fov's docstring),
# so an uncapped guard would spot the walker from clear across an open room the
# instant a sightline cleared -- far harsher than anything else in the game. A
# Building.cameras position reuses this same range rather than getting its own --
# one fewer unbalanced knob, and there's no basis yet to tune it separately.
GUARD_SIGHT_RANGE = 4

# A failed lock pick (attempt_lock) doesn't raise the alarm outright -- this is the
# chance an ordinary failure still trips it; a critical failure always does, the same
# shape as a burglary entrance's own critical failure always going loud. First-slice
# number, not balance-simulated.
LOCK_FAILURE_ALARM_CHANCE = 0.3
