"""Remote support: the hacker on the far end of a comm link, during a tactical fight.

The other half of a crew (see Character.crew_on_site / crew_support). An on-site hire is
a body on the board with a combat.Enemy stat block; a support hire never appears on the
map at all and instead does things to the *building* on the player's order.

**They act on their own turn, not yours.** Directing them costs no move and no action,
which is the whole reason to pay for a second person rather than a better gun. What stops
that being free is the trace: every task rolls, a miss climbs the meter, and at TRACE_CAP
the building's ICE has them -- they drop offline for the rest of the job and the noise
brings the alarm with them. So the pressure is "how hard do you push them", the same shape
as matrix.MatrixState.security, rather than a resource you count down.

**The layering here points one way: support imports tactical, never the reverse.** This
module reads the board and pushes results into it (raise_alarm, state.explored,
Unit.stunned_rounds); the fight itself only ever *carries* a Support and touches two of
its plain attributes (`acted` in _begin_player_turn, `blinded_cameras` in
check_detection), which is why TacticalState's `support` field is a string annotation and
tactical.py imports nothing from here. Every entry point below is called by the screen or
by scene_screen, never by the engine mid-turn -- that's what keeps the arrow clean.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.buildings import ROOM_LABELS
from shadowguy.checks import resolve_check, resolve_rng
from shadowguy.grid import Coord, chebyshev
from shadowguy.runners import SUPPORT_PROGRAMS, SupportProgram, live_runner, support_programs_for
from shadowguy.tactical import TacticalState, raise_alarm

TRACE_CAP = 10
# How many enemy phases a guard sits out when their own chrome is turned against them.
# Reuses Unit.stunned_rounds, the same field a Flash grenade sets -- a guard convulsing
# on the floor and a guard blinded by a flashbang are the same thing to the AI phase.
CYBERWARE_STUN_ROUNDS = 2


class SupportTaskKind(StrEnum):
    """What a support program does when run. The string values are what
    runners.SupportProgram.task holds -- runners.py is a leaf and can't import this."""

    SCOUT = "scout"  # read the building's sensors back at it: reveal a room you haven't seen
    DEVICE = "device"  # talk a lock open, or a camera into looking elsewhere
    CYBERWARE = "cyberware"  # reach through a guard's own implants and drop them


if {kind.value for kind in SupportTaskKind} < {p.task for p in SUPPORT_PROGRAMS}:
    raise ValueError("a SupportProgram names a task with no SupportTaskKind")


@dataclass
class Support:
    """The remote hacker as the fight sees them: what they can run, how hot they are.

    Not a Unit and deliberately not a combat.Enemy -- they have no position, no health
    and nothing can shoot them. The only thing that can hurt them is the trace, and the
    only thing that can spend it is the player choosing to push.
    """

    name: str
    rating: int
    programs: tuple[SupportProgram, ...]
    trace: int = 0
    # Spent for this player turn; cleared in tactical._begin_player_turn. One task per
    # turn, so directing them is a real choice between the three rather than a shopping
    # list.
    acted: bool = False
    # Traced and burned for the rest of the job. Terminal -- nothing clears it.
    offline: bool = False
    # (level, cell) of every camera talked out of looking. check_detection skips these.
    # Held here rather than mutating Building.cameras because the building is pickled
    # inside the accepted job and a camera should come back when the job is re-walked.
    blinded_cameras: set[tuple[int, Coord]] = field(default_factory=set)

    @property
    def can_act(self) -> bool:
        return not self.offline and not self.acted


def support_for(hires, roster=None) -> "Support | None":
    """Build the Support a fight opens with from this job's support hires
    (Character.crew_support), or None if nobody is backing it.

    Takes the *best* one rather than stacking several: two hackers in the same system is
    a second feature (whose trace? whose turn?), and only one of them can be told what to
    do on a turn anyway. Anyone whose archetype can't work support has no programs at all
    (runners.support_programs_for), so they can't be the pick.

    `roster` is the run's own runner list (ShadowguyApp.runners), and it matters: both
    numbers read here move in play. A hacker's rating is earned and their deck is bought,
    so resolving a hire against runners.RUNNERS_BY_ID would staff the comm link with the
    person they were on day one.
    """
    candidates = [
        (runner, support_programs_for(runner))
        for runner in (live_runner(hire.runner_id, roster) for hire in hires)
        if runner is not None
    ]
    staffed = [(runner, programs) for runner, programs in candidates if programs]
    if not staffed:
        return None
    runner, programs = max(staffed, key=lambda pair: pair[0].rating)
    return Support(name=runner.name, rating=runner.rating, programs=programs)


def support_tasks(state: TacticalState) -> list[SupportProgram]:
    """The programs the hacker could run *this instant* -- rating already filtered them
    at hire time, so what this adds is whether there's anything on this level to point
    them at. An empty list is why the side menu greys out rather than lying."""
    support = state.support
    if support is None or not support.can_act or state.is_over:
        return []
    return [p for p in support.programs if _support_target(state, p) is not None]


def _support_target(state: TacticalState, program: SupportProgram):
    """What this program would act on right now, or None if there's nothing. One place
    that answers it, so the menu and the resolution can't disagree about what's legal."""
    kind = SupportTaskKind(program.task)
    if kind is SupportTaskKind.SCOUT:
        return _nearest_unseen_room(state)
    if kind is SupportTaskKind.DEVICE:
        return _nearest_device(state)
    return _nearest_guard(state)


def _by_distance(state: TacticalState, items, coord_of):
    return min(items, key=lambda item: chebyshev(state.player.coord, coord_of(item)), default=None)


def _nearest_unseen_room(state: TacticalState):
    """The closest room on this level with anything in it the player hasn't laid eyes on.
    Rooms rather than tiles because a hacker pulling a floor plan reads a *room*."""
    if state.building is None:
        return None
    explored = state.explored.get(state.level_index, set())
    unseen = [
        room
        for room in state.building.levels[state.level_index].rooms
        if any(cell not in explored for cell in _room_interior(room))
    ]
    return _by_distance(state, unseen, lambda room: room.center)


def _room_interior(room) -> list[Coord]:
    """Every interior cell of a buildings.Room."""
    return [(x, y) for x in range(room.x, room.x + room.width) for y in range(room.y, room.y + room.height)]


def _nearest_device(state: TacticalState):
    """A locked door or a live camera on this level, whichever is closer -- "device" is
    one task rather than two because from the far end of a link they're the same job."""
    if state.building is None:
        return None
    here = state.level_index
    devices = [(level, cell) for (level, cell) in state.building.locks if level == here]
    devices += [
        (level, cell)
        for (level, cell) in state.building.cameras
        if level == here and (level, cell) not in state.support.blinded_cameras
    ]
    return _by_distance(state, devices, lambda device: device[1])


def _nearest_guard(state: TacticalState):
    """Any standing enemy on this level. Deliberately not gated on line of sight: the
    hacker isn't looking through the player's eyes, they're in the building's network."""
    return _by_distance(state, [e for e in state.enemies if e.health > 0], lambda unit: unit.coord)


def run_support_task(
    state: TacticalState, program: SupportProgram, rng: random.Random | None = None
) -> None:
    """Direct the hacker at one task. Costs the player nothing but the hacker's own turn.

    Rolls their `rating` as the pool (runners.RivalRunner.rating is an effective
    skill_value, which is what that field was always for) against the program's
    difficulty. A miss climbs the trace and nothing else happens; reaching TRACE_CAP
    burns them for the job and brings the alarm down at the same time.
    """
    rng = resolve_rng(rng)
    support = state.support
    if support is None or not support.can_act or program not in support_tasks(state):
        return
    target = _support_target(state, program)
    support.acted = True

    roll = resolve_check(stat_value=support.rating, difficulty=program.difficulty, rng=rng)
    if not roll.result.passed:
        support.trace += program.trace_on_failure
        state.log.append(f"{support.name}: {program.name} glances off. Trace {support.trace}/{TRACE_CAP}.")
        if support.trace >= TRACE_CAP:
            support.offline = True
            state.log.append(f"{support.name} is traced and drops the link.")
            raise_alarm(state, "The trace runs both ways. The building knows.")
        return

    _apply_support_success(state, program, target)


def _apply_support_success(state: TacticalState, program: SupportProgram, target) -> None:
    support = state.support
    kind = SupportTaskKind(program.task)
    if kind is SupportTaskKind.SCOUT:
        explored = state.explored.setdefault(state.level_index, set())
        explored.update(_room_interior(target))
        watchers = sum(
            1 for level, cell in state.building.cameras
            if level == state.level_index and cell in set(_room_interior(target))
        )
        room_name = ROOM_LABELS.get(target.kind, "room")
        note = f" {watchers} camera(s) on it." if watchers else " Nothing watching it."
        state.log.append(f"{support.name}: {room_name.lower()} mapped.{note}")
    elif kind is SupportTaskKind.DEVICE:
        if target in state.building.locks:
            del state.building.locks[target]
            state.log.append(f"{support.name}: that lock just opened itself.")
        else:
            support.blinded_cameras.add(target)
            state.log.append(f"{support.name}: camera's looking somewhere else now.")
    else:
        target.stunned_rounds += CYBERWARE_STUN_ROUNDS
        state.log.append(f"{support.name}: {target.name}'s own chrome puts them down.")
