"""The first slice of "the world keeps moving without the player": rival corp
Factions and independent Runners each get a daily action on day-advance.

A parallel resolution module, like security.py/encounters.py — not a Scene.
Called once per day from ShadowguyApp's day tick (app._apply_day_tick, fired by
app.spend_time whenever elapsed time crosses midnight), the same tick that pays
crew wages and refreshes gigs/offers.

Factions do something real: a faction can push onto neutral ground bordering
its own territory (see claim_territory / corpmap.expansion_candidates) — the
4X-style area-control mechanic CLAUDE.md flags as still missing. Deliberately
scoped to neutral ground only: taking a rival faction's own territory is a
bigger mechanic (contest resolution, standing/rep fallout) left for later.

Once the player takes over a Faction (corp_turn.py), that faction is excluded
from this AI loop via player_faction_id — its daily move becomes the player's
own decision instead.

RivalRunners now make a real choice each day rather than only drifting. An
independent (not-hired) runner picks one RunnerActivity per turn, and the
activity is what decides whether they move: only LEGWORK wanders, WORKING
relocates them to the job site, and the rest keep them where they are. State
lives in a caller-owned `rival_runner_states` dict of RunnerState (persisted on
ShadowguyApp, not here — rivals.py stays leaf-ish), which is what gives
RivalAction.territory_id its content and lets surveillance.py's Surveillance
checks have somewhere real to catch them.

Exactly one activity bites the player: WORKING takes a real JobOffer off a
real fixer's board (marking it fixer.JobOffer.taken_by), so a job you sat on is
a job you can lose. The others — LEGWORK, LAYING_LOW, DRINKING, RECOVERING —
are informational: they drive movement and give PhoneScreen something true
to show, but nothing rolls against them yet. RECOVERING is the one with a
cause: it only ever follows a WORKING day that went badly.

Leaf-ish: imports character/corpmap/factions/runners, never scene or app. The
fixer board is reached through a TYPE_CHECKING-only import (the same trick
fixer.py itself uses for Character) — a runner only ever reads an offer's
timing and marks it taken, so nothing here needs fixer/scene at runtime.
"""

import random
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from shadowguy.character import Character
from shadowguy.corpmap import CorpMap, LocationKind, claim_territory, expansion_candidates
from shadowguy.factions import FACTIONS
from shadowguy.runners import RIVAL_RUNNERS

if TYPE_CHECKING:
    from shadowguy.fixer import Fixer, JobOffer

# Per faction, per day; only rolled when the faction has an eligible neutral
# neighbor at all. First-slice number, not balance-simulated.
EXPANSION_CHANCE = 0.2


class RunnerActivity(StrEnum):
    """How an independent runner spent a day. Also the movement rule: LEGWORK
    is the only activity that wanders, WORKING relocates to the job site, and
    the rest stay put."""

    WORKING = "working"  # took a job off a fixer's board and is running it
    LEGWORK = "legwork"  # asking around, casing ground — the old blind wander
    LAYING_LOW = "laying_low"  # off the grid for a day
    DRINKING = "drinking"  # at a bar in their current territory
    RECOVERING = "recovering"  # laid up after a job that went badly


# For display, the way corpmap.MODIFIER_LABELS is: the enum values are ids, not
# prose. WORKING's label is the fallback for a state whose job_title is gone —
# a surface with the title should name the job instead.
ACTIVITY_LABELS = {
    RunnerActivity.WORKING: "out on a job",
    RunnerActivity.LEGWORK: "asking around",
    RunnerActivity.LAYING_LOW: "laying low",
    RunnerActivity.DRINKING: "drinking",
    RunnerActivity.RECOVERING: "laid up, took a bad hit",
}


@dataclass
class RunnerState:
    """One independent runner's persistent day-to-day state. Replaces the bare
    territory id the wander used to track, since an activity has to survive to
    the next turn (recovery) and to display time (job_title)."""

    territory_id: str
    activity: RunnerActivity = RunnerActivity.LEGWORK
    # WORKING only: the Scene.title of the offer they pulled, so a surface can
    # name the job without holding a reference to the offer itself (which
    # expire_offers drops the day after).
    job_title: str | None = None
    # How many more days RECOVERING is forced. Only ever set by a bad WORKING
    # day; 0 means they roll freely next turn.
    recovery_days: int = 0


# Per independent runner, per free day: the odds they go looking for work
# rather than idle. Only reached when a takeable offer actually exists, so the
# real rate is lower early in a run.
RUNNER_WORK_CHANCE = 0.35

# The idle table, rolled when a runner doesn't work. LEGWORK's share is what
# replaces the old flat RUNNER_MOVE_CHANCE (0.3): (1 - RUNNER_WORK_CHANCE) *
# 0.45 ≈ 0.29, so a runner still wanders at roughly the old rate on top of
# whatever job-site relocation adds. DRINKING needs a bar in the territory and
# falls back to LAYING_LOW without one. First-slice numbers, not
# balance-simulated.
IDLE_ACTIVITY_WEIGHTS = {
    RunnerActivity.LEGWORK: 0.45,
    RunnerActivity.LAYING_LOW: 0.35,
    RunnerActivity.DRINKING: 0.20,
}

# Per WORKING day: the odds the job went badly enough to lay them up, and for
# how many days if it did. This is the only thing that produces RECOVERING —
# a runner is never hurt by anything the player did.
JOB_INJURY_CHANCE = 0.25
RECOVERY_DAYS = (1, 2)


@dataclass
class RivalAction:
    """One actor's turn on a given day. For a faction, territory_id is set only
    when it claimed neutral ground that day, and activity/job_title stay None.
    For a runner, territory_id is always set — their territory after today's
    turn, whether or not they moved — and activity is what they did with the
    day. job_title/fixer_id are set only on the turn they took a job, which is
    the one thing here a caller currently reports — app notifies on it, but only
    for a fixer the player has actually met, which is what fixer_id is for."""

    kind: Literal["faction", "runner"]
    actor_id: str
    day: int
    territory_id: str | None = None
    activity: RunnerActivity | None = None
    job_title: str | None = None
    fixer_id: str | None = None


def _has_bar(corp_map: CorpMap, territory_id: str) -> bool:
    return any(loc.kind is LocationKind.BAR for loc in corp_map.territories[territory_id].locations)


def _takeable_offers(fixers: list["Fixer"], day: int) -> list["JobOffer"]:
    """Every open job on every fixer's board a runner could pick up today: not
    already taken by another runner, runnable today by its own timing (a job
    scheduled for day 9 isn't work anyone can do on day 4), and **already on
    the board before today**. Offers the player has accepted are already gone
    from the board — FixerOffersScreen removes them on accept — so there's
    nothing to exclude for that.

    That last rule is what makes losing a job fair. The day tick generates the
    day's new offers (fixer.refresh_offers) before it resolves runner turns, so
    without it a runner could take a job in the same tick that created it —
    one the player never had a chance to see, let alone sit on. Measured at 10%
    of all steals before the guard went in."""
    return [
        offer
        for fixer in fixers
        for offer in fixer.offers
        if offer.taken_by is None
        and offer.offered_day < day
        and offer.timing.is_available(day)
        and not offer.timing.is_expired(day)
    ]


def _runner_turn(
    runner_id: str,
    state: RunnerState,
    corp_map: CorpMap,
    fixers: list["Fixer"],
    day: int,
    rng: random.Random,
) -> "JobOffer | None":
    """Resolve one runner's day, mutating `state` in place and returning the
    offer they took, if any — the caller needs the offer's fixer, which isn't
    worth persisting on the state past the day it was taken.

    Recovery pre-empts everything (they don't get a choice). Otherwise they try
    for work first — the only branch that touches anything outside this module
    — and fall back to the idle table, which is also what decides whether they
    move."""
    state.job_title = None
    if state.recovery_days > 0:
        state.recovery_days -= 1
        state.activity = RunnerActivity.RECOVERING
        return None

    offers = _takeable_offers(fixers, day)
    if offers and rng.random() < RUNNER_WORK_CHANCE:
        offer = rng.choice(offers)
        offer.taken_by = runner_id
        state.activity = RunnerActivity.WORKING
        state.job_title = offer.scene.title
        # A job whose target territory isn't on this map (or isn't set at all)
        # leaves them where they are rather than guessing at a destination.
        if offer.scene.target_territory_id in corp_map.territories:
            state.territory_id = offer.scene.target_territory_id
        if rng.random() < JOB_INJURY_CHANCE:
            state.recovery_days = rng.randint(*RECOVERY_DAYS)
        return offer

    activity = rng.choices(
        list(IDLE_ACTIVITY_WEIGHTS), weights=list(IDLE_ACTIVITY_WEIGHTS.values())
    )[0]
    if activity is RunnerActivity.DRINKING and not _has_bar(corp_map, state.territory_id):
        activity = RunnerActivity.LAYING_LOW
    if activity is RunnerActivity.LEGWORK:
        connections = corp_map.territories[state.territory_id].connections
        if connections:
            state.territory_id = rng.choice(connections)
    state.activity = activity
    return None


def resolve_rival_day(
    character: Character,
    corp_map: CorpMap,
    day: int,
    rng: random.Random,
    player_faction_id: str | None = None,
    rival_runner_states: dict[str, RunnerState] | None = None,
    fixers: list["Fixer"] | None = None,
) -> list[RivalAction]:
    """Every Faction gets a shot at expanding into bordering neutral ground. A
    RivalRunner acts only while independent — excluded the moment they're on the
    player's crew, indefinite or for-job alike, since either engagement means
    they're working for the player that day, not freelancing on their own — and
    otherwise takes one RunnerActivity turn via _runner_turn.

    player_faction_id skips that faction entirely (no RivalAction recorded) once
    the player has taken it over via corp_turn.py — its move is the player's own
    decision, reported from the Corp screen instead of rolled here.

    rival_runner_states is the caller's persistent runner_id -> RunnerState map
    (ShadowguyApp.rival_runner_states in production), mutated in place so a
    runner's position and activity carry over day to day. A runner not in it yet
    is dropped onto a random territory and takes their turn from there the same
    day. Defaults to a fresh dict when omitted, which is fine for callers
    (mostly tests) that don't care where a runner ends up, only that they acted.

    fixers is the live fixer roster, the one thing here that reaches outside
    rivals.py: a runner who goes to work marks a real JobOffer taken, removing
    it from the player's options. Omitting it (the default) means nobody can
    find work — every runner falls through to the idle table."""
    if rival_runner_states is None:
        rival_runner_states = {}
    if fixers is None:
        fixers = []
    actions = []
    for faction in FACTIONS:
        if faction.id == player_faction_id:
            continue
        target_id = None
        candidates = expansion_candidates(corp_map, faction.id)
        if candidates and rng.random() < EXPANSION_CHANCE:
            target_id = rng.choice(candidates)
            claim_territory(corp_map.territories[target_id], faction.id, rng)
        actions.append(RivalAction(kind="faction", actor_id=faction.id, day=day, territory_id=target_id))
    for runner in RIVAL_RUNNERS:
        if character.on_crew(runner.id):
            continue
        state = rival_runner_states.get(runner.id)
        if state is None:
            state = RunnerState(territory_id=rng.choice(list(corp_map.territories)))
            rival_runner_states[runner.id] = state
        taken = _runner_turn(runner.id, state, corp_map, fixers, day, rng)
        actions.append(
            RivalAction(
                kind="runner",
                actor_id=runner.id,
                day=day,
                territory_id=state.territory_id,
                activity=state.activity,
                job_title=state.job_title,
                fixer_id=taken.fixer_id if taken else None,
            )
        )
    return actions
