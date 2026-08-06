"""Fight surface 2: combat played out on a grid.

Position, line of sight and cover decide which of combat.py's existing attacks are
legal and how hard they land, but the dice underneath are still checks.resolve_check
(see CLAUDE.md's Combat section). Cover is nothing more fancy than a raised to-hit
difficulty, and every attack — either side's — is combat.resolve_hit, so the grid can
never quietly become a second set of combat rules.

Space itself is grid.py, one layer down: `Grid`/`Tile` and the field-of-view,
pathfinding and distance functions over them. buildings.py sits between the two — it
needs that geometry to carve a burglary target, and this module needs a whole
`Building` to walk one — so the arrow runs grid -> buildings -> tactical, one way.

Like combat.py it imports no scene: it owns space, turn order and movement, not what
winning is worth. scene.py holds the Outcome-bearing wrappers (TacticalStage,
BurglaryStage), importing this the same way it imports combat for Enemy.

Two things that used to sit in here now have modules of their own, both one-way:

- **tactical_gen.py** — the BSP map generator that emits the Grid an ordinary tactical
  stage is fought on. Generation runs once, the fight runs for the rest of the stage, and
  the generator needs nothing from this module at all (corpmap/corpmap_gen, same split).
- **support.py** — the remote hacker a burglary can be backed by. That one imports *this*
  module and never the reverse: the fight only ever carries a `Support` and touches two of
  its attributes, so the `support` fields below stay string annotations and nothing here
  imports it back.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

import numpy as np

from shadowguy.buildings import Building, Lock
from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check, resolve_rng
from shadowguy.combat import (
    MELEE_RANGE,
    QUICKNESS_BASE,
    Enemy,
    attack_verbs,
    combat_consumables,
    consumables_with,
    equipped_weapons,
    melee_damage_bonus,
    player_defense,
    player_quickness,
    player_soak,
    resolve_hit,
    smartlink_bonus,
    weapon_range,
)
from shadowguy.grid import (
    Coord,
    Grid,
    Tile,
    chebyshev,
    has_line_of_sight,
    path_between,
    step_neighbors,
    visible_tiles,
)
from shadowguy.shops import CONSUMABLES_BY_ID, Consumable, EffectKind, Item
from shadowguy.skills import skill_value

if TYPE_CHECKING:
    # Type-only, and deliberately the *only* mention of support.py in here: the runtime
    # arrow runs support -> tactical, never back. See the module docstring.
    from shadowguy.support import Support


# MELEE_RANGE/THROWN_RANGE/FIREARM_RANGE and weapon_range now live in combat.py — an
# Enemy's reach is weapon_range of the weapon it's holding, so the bands had to be
# readable from the side of the fight that has no Character behind it.
#
# How far an enemy can attack is still per-enemy, on combat.Enemy.reach (a guard shoots,
# street muscle closes) — read by _take_unit_turn. There is deliberately no global enemy
# range; it falls out of what each one is carrying.

# Move budget per turn -- how far a unit gets when it's their turn, deliberately
# separate from how *often* they get one (see Unit.quickness/combat.player_quickness,
# both off Agility). A flat constant for now; a future ability raising it is the
# obvious hook, which is why it's a field on the unit, not a global.
PLAYER_SPEED = 4
ENEMY_SPEED = 4
# A hired runner moves like everyone else; they're a unit on the same board, and giving
# backup its own movement rule would be a second thing to reason about for no gain.
ALLY_SPEED = 4

# tactical.py's turn-order scheduler: how many ticks a "work unit" (one tile moved, or
# the turn's one action) costs a unit at exactly QUICKNESS_BASE quickness -- see
# _time_cost and end_turn. A quicker unit's turns come around faster; a slower one's
# turns come around slower, both relative to this baseline. First-slice, not
# balance-simulated, like the rest of tactical.py's own numbers.
BASE_ACTION_TIME = 100


def _time_cost(quickness: int, work: int) -> int:
    """How many scheduler ticks `work` units of a turn cost this unit -- work is tiles
    moved plus one more if the turn's action was spent too (an AI unit's turn is always
    exactly 1: one combined close-and-attack action). Scaled by
    combat.QUICKNESS_BASE/quickness, so a quicker unit's turns come around faster."""
    return round(BASE_ACTION_TIME * work * QUICKNESS_BASE / quickness)

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

# A guard's static sightline is capped the same way combat.Enemy.reach caps an
# attack's: has_line_of_sight is unlimited-range by design (see grid._fov's docstring),
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
    budget (see PLAYER_SPEED); `quickness`/`next_turn` are the turn-order scheduler's
    (see _time_cost/end_turn) -- how often this unit's turn comes up, and the tick it's
    next due, both cached at construction like speed is."""

    name: str
    side: Side
    coord: Coord
    speed: int
    quickness: int = QUICKNESS_BASE
    next_turn: int = 0
    stats: Enemy | None = None
    health: int = 0
    # Enemy-only, set by a thrown Webbing/Flash-family grenade (EffectKind.COMBAT_STUN):
    # this unit sits out its next `stunned_rounds` enemy phases entirely — no move, no
    # attack — same "a round they owe you" meaning as abstract_combat.Fighter.stunned_rounds.
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
    it. One player turn (move up to `speed`, then one action), then a quickness-paced
    scheduler decides which allies and enemies get theirs before the player is up again
    (see end_turn)."""

    character: Character
    grid: Grid
    units: list[Unit]
    exits: frozenset[Coord]
    outcome: TacticalOutcome = TacticalOutcome.ONGOING
    log: list[str] = field(default_factory=list)
    moves_left: int = 0
    acted: bool = False
    # Unconditional tally of tiles moved this player turn -- unlike moves_left, this
    # keeps counting even while not TacticalState.in_combat (where moves_left never
    # decrements at all, per the free-movement-while-unseen rule). end_turn's scheduler
    # needs to know how much a turn actually did regardless of whether it was rationed,
    # so the two counters serve different purposes and both have to exist. Reset in
    # _begin_player_turn alongside moves_left/acted.
    moves_taken_this_turn: int = 0
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
    building: Building | None = None
    level_index: int = 0
    objective: tuple[int, Coord] | None = None
    off_level_units: dict[int, list[Unit]] = field(default_factory=dict, repr=False)
    # Tiles the player has ever had FOV on, by level index -- fog of war. The renderer
    # treats these as "known but not presently in sight" and everything else as hidden
    # outright. Keyed by level because a burglary's levels are separate grids.
    explored: dict[int, set[Coord]] = field(default_factory=dict, repr=False)
    # Set the moment anybody sees you. Guards that were standing their post start
    # fighting, and no amount of hiding puts it back.
    alarm: bool = False
    # weapon id -> turns remaining before it can fire again, exactly as
    # abstract_combat.CombatState carries it: set by player_attack when the weapon has
    # recharge_rounds, decremented at the end of the player's turn. A cooling weapon is
    # gated out in can_hit, which is the one place "can this weapon reach that enemy"
    # is answered, so it drops out of targets_for/weapon_for_target/attack_targets and
    # the Attack tile together. Without this the field was simply inert on the grid —
    # the tasers fired every round here while pacing themselves in an abstract fight.
    weapon_cooldowns: dict[str, int] = field(default_factory=dict)
    # The remote hacker, if one was hired as support for this job (Character.crew_support).
    # None for every fight nobody is backing: an ordinary shootout, a solo burglary, a
    # Test-menu fight. See support.py.
    support: "Support | None" = None
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

    @property
    def in_combat(self) -> bool:
        """Whether anything is actually hunting you right now -- movement is only
        rationed by moves_left while this is true. An ordinary tactical fight is this
        from the first turn (Unit.alerted defaults to True, and `alarm` plays no part
        in one -- it's never set outside a burglary). A burglary's guards start
        unalerted and stay that way until check_detection/raise_alarm catches you, so
        walking a quiet house costs no action points at all -- only the fight that
        follows does. `alarm` alone covers the case current floor holds no live guard
        (fled upstairs, or they're all off_level_units) since it flips building-wide.
        One-way, same as `alarm`: nobody un-notices you."""
        return self.alarm or any(u.alerted for u in self.enemies)

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
    units = [
        Unit(
            name=character.name,
            side=Side.PLAYER,
            coord=player_start,
            speed=PLAYER_SPEED,
            quickness=player_quickness(character),
        )
    ]
    for enemy, coord in enemy_placements:
        units.append(
            Unit(
                name=enemy.name,
                side=Side.ENEMY,
                coord=coord,
                speed=ENEMY_SPEED,
                quickness=enemy.quickness,
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
                quickness=ally.quickness,
                stats=ally,
                health=ally.health,
            )
        )
    state = TacticalState(character=character, grid=grid, units=units, exits=frozenset(exits))
    _begin_player_turn(state)
    return state


def start_burglary(
    character: Character,
    building: Building,
    spawn: tuple[int, Coord],
    guard: Enemy,
    allies: list[Enemy] = (),
    support: "Support | None" = None,
) -> TacticalState:
    """Set up a burglary: the same tactical fight every other stage plays, with three
    differences.

    The board is one *level* of a building, and stairs reach the others. The guards are
    real units but start unalerted at their posts, acting only once somebody sees you
    (check_detection) -- which is what makes walking past one possible. And it ends when
    you reach what you came for (SECURED), not when the last guard falls: clearing the
    house makes the rest of the walk quiet, it is never the win itself.

    `allies` are on-site hires and become units; `support` (support.py) is the hire who
    isn't there at all. Every entrance is an exit, so the way in is the way you bail."""
    exits = frozenset(coord for level, coord in building.entrance_spawns if level == spawn[0])
    state = TacticalState(
        character=character,
        grid=building.levels[spawn[0]].grid,
        units=[],
        exits=exits,
        building=building,
        level_index=spawn[0],
        objective=building.objective,
        support=support,
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
                quickness=guard.quickness,
                stats=guard,
                health=guard.health,
                alerted=False,
            )
        )
    player = Unit(
        name=character.name,
        side=Side.PLAYER,
        coord=spawn[1],
        speed=PLAYER_SPEED,
        quickness=player_quickness(character),
    )
    spawns = ally_spawns(
        state.grid, spawn[1], len(allies), frozenset(u.coord for u in state.off_level_units.get(spawn[0], []))
    )
    crew = [
        Unit(
            name=ally.name,
            side=Side.ALLY,
            coord=coord,
            speed=ALLY_SPEED,
            quickness=ally.quickness,
            stats=ally,
            health=ally.health,
        )
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
    _reveal(state)
    arriving = state.off_level_units.pop(index, [])
    for ally, spot in zip(crew, ally_spawns(state.grid, coord, len(crew), frozenset(u.coord for u in arriving)), strict=False):
        ally.coord = spot
    # raise_alarm catches an alerted unit's next_turn up to *now* the moment it fires,
    # but a guard can sit off-level for several more player turns after that before
    # actually joining the board -- catch it up again here (max, not overwrite, so an
    # already-current unit is never pushed backward) or it re-floods the scheduler with
    # catch-up turns on arrival, same bug one hop later.
    for unit in arriving:
        if unit.alerted:
            unit.next_turn = max(unit.next_turn, player.next_turn)
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


def _moves_exhausted(state: TacticalState, in_combat: bool) -> bool:
    """Whether nothing more can be spent right now: the fight's over, or movement is
    being rationed (in_combat) and the pool's empty. Takes the already-computed flag
    rather than reading TacticalState.in_combat itself -- every caller needs it for its
    own legality check anyway, so this doesn't ask the (cheap, but not free) question a
    second time."""
    return state.is_over or (in_combat and state.moves_left <= 0)


def _spend_move(state: TacticalState, in_combat: bool) -> None:
    """Count one step, always -- and ration one moves_left point off it, but only once
    something is actually hunting you. Shared by every action that spends a step
    (take_stairs, move_player, attempt_lock) so the combat gate can't drift between
    them. Same precomputed-flag reasoning as _moves_exhausted.

    moves_taken_this_turn is unconditional (unlike moves_left) so end_turn's scheduler
    still knows how far a turn actually went even on the rare manual End Turn taken
    while not in_combat, where moves_left never moved at all."""
    state.moves_taken_this_turn += 1
    if in_combat:
        state.moves_left -= 1


def take_stairs(state: TacticalState) -> bool:
    """Take the stairs under your feet. Costs a move like any other step once something
    is actually hunting you (see TacticalState.in_combat) -- a floor is a place you walk
    to, not a free teleport. False when there's nothing to take, or no movement left to
    spend while in combat."""
    destination = stairs_here(state)
    in_combat = state.in_combat
    if destination is None or _moves_exhausted(state, in_combat):
        return False
    _spend_move(state, in_combat)
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
    blinded = state.support.blinded_cameras if state.support else frozenset()
    seen_by_camera = any(
        level == state.level_index
        and (level, coord) not in blinded
        and in_reach(state.grid, coord, player, GUARD_SIGHT_RANGE)
        for level, coord in state.building.cameras
    )
    if not seen_by_camera:
        return False
    raise_alarm(state, "A camera catches you.")
    return True


def raise_alarm(state: TacticalState, reason: str) -> None:
    """It's gone loud. Every guard on this level drops their post and fights, and the
    ones elsewhere are alert by the time you reach them.

    Also catches every *newly*-alerted unit's next_turn up to now (state.player.
    next_turn) rather than leaving it at its construction-time 0: a sneak can run for
    many player turns before the alarm fires, and without this a guard who's been
    frozen at time-zero the whole while would flood the scheduler with catch-up turns
    the instant it's alerted -- see enter_level for the matching fix one hop later.

    The `not unit.alerted` guard matters beyond burglaries: an ordinary tactical fight
    starts every enemy already alerted (Unit.alerted defaults True), so raise_alarm
    still fires there too (player_attack calls it unconditionally, "going loud" applies
    regardless of whether anyone was hiding) but with `state.alarm` starting False all
    the same. Without the guard, that first call would snap an already-active enemy's
    legitimately-earned schedule lead back down to the player's, handing it a free
    bonus action it hadn't earned -- catching up is only ever correct for a unit that
    was actually just alerted this call, not one that already had a turn order."""
    if state.alarm:
        return
    state.alarm = True
    state.log.append(reason)
    for unit in state.units:
        if unit.is_enemy and not unit.alerted:
            unit.alerted = True
            unit.next_turn = state.player.next_turn
    for waiting in state.off_level_units.values():
        for unit in waiting:
            if not unit.alerted:
                unit.alerted = True
                unit.next_turn = state.player.next_turn


def reached_score(state: TacticalState) -> bool:
    """Whether the player is standing on what they came to steal."""
    return state.objective is not None and (state.level_index, state.player.coord) == state.objective


def _reveal(state: TacticalState) -> None:
    """Fold the player's current FOV into state.explored -- the fog-of-war memory the
    renderer falls back on for tiles outside present sight. Idempotent, so any call site
    that might have moved the player can just call it."""
    seen = visible_tiles(state.grid, state.player.coord)
    explored = state.explored.setdefault(state.level_index, set())
    for y, x in zip(*np.nonzero(seen), strict=True):
        explored.add((int(x), int(y)))


def _begin_player_turn(state: TacticalState) -> None:
    state.moves_left = state.player.speed
    state.acted = False
    state.moves_taken_this_turn = 0
    if state.support is not None:
        state.support.acted = False  # the hacker's turn is their own, refreshed with yours
    for weapon_id in list(state.weapon_cooldowns):
        state.weapon_cooldowns[weapon_id] -= 1
        if state.weapon_cooldowns[weapon_id] <= 0:
            del state.weapon_cooldowns[weapon_id]
    _reveal(state)


def legal_moves(state: TacticalState, *, in_combat: bool | None = None) -> list[Coord]:
    """Where the player may step this instant: one cardinal move into open, unoccupied
    floor, if they have moves left -- but moves are only a limited resource once
    something is actually hunting you (TacticalState.in_combat). Walking a quiet house
    before anyone's spotted you doesn't ration steps at all.

    `in_combat` lets a caller that already read TacticalState.in_combat for its own
    purposes (move_player) pass it straight through instead of asking again; every
    other caller (tests, a future renderer) omits it and gets the same answer read
    fresh off `state`."""
    if in_combat is None:
        in_combat = state.in_combat
    if _moves_exhausted(state, in_combat):
        return []
    return step_neighbors(state.grid, state.player.coord, blocked=state.occupied(exclude=state.player))


def move_player(state: TacticalState, dest: Coord, rng: random.Random | None = None) -> bool:
    """Spend the player's move on `dest`. Returns False (spending nothing) only if the
    step isn't legal at all -- a locked door resolves a pick-the-lock check instead of a
    plain step (see attempt_lock), and still returns True on a failed pick: the move was
    spent attempting it, even though state.player.coord didn't change. Check that
    directly if "did the player actually end up on dest" is what you need.

    Only actually spends a moves_left point while TacticalState.in_combat -- the step
    that gets you spotted is itself free, since nothing was hunting you when you took
    it; every step after check_detection flips it on starts costing again."""
    in_combat = state.in_combat
    if dest not in legal_moves(state, in_combat=in_combat):
        return False
    lock = lock_at(state, dest)
    if lock is not None:
        attempt_lock(state, dest, lock, rng, in_combat=in_combat)
        return True
    state.player.coord = dest
    _spend_move(state, in_combat)
    _reveal(state)
    check_detection(state)
    _settle(state)
    return True


def lock_at(state: TacticalState, coord: Coord) -> Lock | None:
    """The still-locked door on this cell of the level currently on the board, if any."""
    if state.building is None:
        return None
    return state.building.locks.get((state.level_index, coord))


def attempt_lock(
    state: TacticalState,
    dest: Coord,
    lock: Lock,
    rng: random.Random | None = None,
    *,
    in_combat: bool | None = None,
) -> None:
    """Try the lock instead of just walking through it. Costs the move either way, same
    as any other step (see TacticalState.in_combat): success clears it for good and
    steps the player through; failure leaves it standing and risks the alarm -- an
    ordinary failure only sometimes (LOCK_FAILURE_ALARM_CHANCE), a critical failure
    always, same shape as a burglary entrance's own critical failure.

    `in_combat` is the same pass-through-or-recompute knob legal_moves takes: move_player
    already read TacticalState.in_combat once and hands it straight through; a caller
    that hasn't (a test, `>` in a screen with no lock in play) omits it."""
    if in_combat is None:
        in_combat = state.in_combat
    rng = resolve_rng(rng)
    roll = resolve_check(skill_value(state.character, lock.skill), lock.difficulty, rng=rng)
    _spend_move(state, in_combat)
    if roll.result.passed:
        del state.building.locks[(state.level_index, dest)]
        state.player.coord = dest
        _reveal(state)
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
    """Whether this weapon reaches this enemy right now: standing, off cooldown, in range
    and in sight. targets_for lists it per weapon, weapon_for_target inverts it per
    target."""
    return (
        target.health > 0
        and not state.weapon_cooldowns.get(weapon.id)
        and in_reach(state.grid, state.player.coord, target.coord, weapon_range(weapon))
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
    if weapon.recharge_rounds:
        state.weapon_cooldowns[weapon.id] = weapon.recharge_rounds
    raise_alarm(state, "The noise carries. They know you're here.")
    difficulty = target.stats.defense + cover_bonus(state.grid, target.coord, state.player.coord)
    roll, damage = resolve_hit(
        rng,
        skill_value(state.character, weapon.skill),
        smartlink_bonus(state.character, weapon),
        difficulty,
        weapon.damage + melee_damage_bonus(state.character, weapon),
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
# with no tile at all — same three-way split as abstract_combat._throw, this is just the subset
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


def _blast_targets(state: TacticalState, target: Coord) -> list[Unit]:
    """Every standing enemy inside a grenade's blast — a GRENADE_RADIUS chebyshev square
    centered on where it landed, so radius 1 is a literal 3x3."""
    return [enemy for enemy in state.enemies if chebyshev(target, enemy.coord) <= GRENADE_RADIUS]


def throw_grenade(state: TacticalState, consumable_index: int, target: Coord | None = None) -> None:
    """Resolve the player's one action as a grenade throw instead of an attack: pops
    Character.consumables[consumable_index] and applies it exactly the way combat.py's
    abstract fight does (see abstract_combat._throw), with one difference space adds — COMBAT_STUN
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
        hit = _blast_targets(state, target)
        state.log.append(f"{consumable.name} — {consumable.amount} to everything in the blast.")
        for enemy in hit:
            enemy.health = max(0, enemy.health - consumable.amount)
        _settle(state)
    elif consumable.effect is EffectKind.COMBAT_STUN:
        hit = _blast_targets(state, target)
        for enemy in hit:
            enemy.stunned_rounds = consumable.amount
        state.log.append(f"{consumable.name} — {len(hit)} pinned down for {consumable.amount}.")
    elif consumable.effect is EffectKind.COMBAT_ESCAPE:
        state.log.append(f"{consumable.name} — you slip out under cover.")
        _end_fight(state, TacticalOutcome.ESCAPED)
    else:
        # Same guard as abstract_combat._throw, from the other side: a new combat-only effect
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
    """End the player's turn: charge the scheduler for what it cost -- tiles moved plus
    the action if one was spent, floored at 1 so an empty End Turn still passes real time
    rather than being a free way to skip everyone else forever -- then let allies and
    enemies take their scheduled turns in whatever order their accumulated time puts them
    in. A slow unit can sit several out; a quick one can act twice before the player is
    up again. Pre-alarm no enemy is alerted, so candidates only ever holds allies."""
    rng = resolve_rng(rng)
    if state.is_over:
        return
    work = max(1, state.moves_taken_this_turn + (1 if state.acted else 0))
    state.player.next_turn += _time_cost(state.player.quickness, work)
    while not state.is_over:
        candidates = [
            unit
            for unit in (*state.allies, *state.enemies)
            if unit.alerted and unit.next_turn < state.player.next_turn
        ]
        if not candidates:
            break
        # Strict < above, not <=: a unit whose per-action cost divides evenly into the
        # player's committed time would otherwise re-qualify the instant it lands
        # exactly on the boundary, silently doubling its actions in the equal-quickness
        # case -- the common one, since most of the roster shares QUICKNESS_BASE + 1.
        unit = min(candidates, key=lambda u: u.next_turn)  # earliest due; ties favor allies (list order)
        _take_unit_turn(state, unit, rng)
    _settle(state, rng)
    if not state.is_over:
        _begin_player_turn(state)


def _can_reach(state: TacticalState, attacker: Unit, target: Unit) -> bool:
    """Whether this unit has a shot at that one right now. Melee (reach 1) needs to be
    adjacent; a guard's gun only needs the sightline. Side-agnostic — it's the same
    question for a Sec Heavy and for your Solo."""
    return in_reach(state.grid, attacker.coord, target.coord, attacker.stats.reach)


def pick_target(state: TacticalState, attacker: Unit, candidates: list[Unit]) -> Unit | None:
    """Who a unit goes after — the one ranking both sides use.

    Ordered by the cover between target and attacker (`cover_bonus`, the same number that
    raises the to-hit difficulty), then distance, with anyone out of line sorting last. So
    ducking behind a wall genuinely redirects fire onto whoever is in the open: cover
    stops being "my rolls got better" and becomes a decision about who eats the round.
    `min` is stable, so the candidate list's order breaks exact ties — which is why
    `friendlies` puts the player first and a hire is never a quiet meat shield. Called
    before movement, so a unit crosses the room toward the target it *wants*.

    One FOV pass covers every candidate; None when the list is empty."""
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


def _take_unit_turn(state: TacticalState, unit: Unit, rng: random.Random) -> None:
    """One unit's scheduled turn -- ally or enemy, whichever end_turn decided is next
    due. Pick a target, close until it can be hit, then hit it. Targets are re-picked per
    turn, so a runner who steps out of cover draws the next one's fire. Both sides run
    this same body: a hire fights the way a Sec Heavy does, pointed the other way.

    **Every branch falls through to the same next_turn advance; none return early.** A
    stunned unit, or one with no target, must still spend scheduler time or it stays
    permanently eligible and end_turn's while loop spins on it forever."""
    if unit.stunned_rounds > 0:
        unit.stunned_rounds -= 1
        state.log.append(f"{unit.name} is still reeling.")
    else:
        candidates = state.enemies if unit.is_ally else state.friendlies
        target = pick_target(state, unit, candidates)
        if target is not None:
            _advance_and_attack(state, unit, target, rng)
    unit.next_turn += _time_cost(unit.quickness, 1)


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
    the runner carries (combat.combat_consumables — the same set abstract_combat's
    fight offers), or none once the action for the turn is already spent. Same "fight
    policy lives here, not the screen" reasoning as best_shot."""
    if state.acted or state.is_over:
        return []
    return combat_consumables(state.character)


