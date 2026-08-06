"""The first slice of "the world keeps moving without the player": rival corp
Factions and independent Runners each get a daily action on day-advance.

A parallel resolution module, like security.py/encounters.py — not a Scene.
Called once per day from ShadowguyApp's day tick (app._apply_day_tick, fired by
app.spend_time whenever elapsed time crosses midnight), the same tick that pays
crew wages and refreshes gigs/offers.

A faction gets three separate rolls a day, not one choice between them:
  - expansion onto neutral ground bordering its own (claim_territory /
    corpmap.expansion_candidates),
  - reinforcement of its thinnest district (_reinforce), and
  - an attack on a bordering rival (_faction_attack), settled through
    corp_turn.resolve_attack — the same dice the player's own attack_territory
    rolls, so the AI is never fighting a different war than the player is.

That last one is the conflict layer this module used to defer ("taking a rival
faction's own territory is a bigger mechanic, left for later"). It is what makes
the map genuinely contested rather than a race to fill empty space, and it is how
a player-run corp can *lose* ground — the AI attacks the player's districts
through exactly the same path it attacks each other's, with no special case.

Target choice is where relations.py finally does something: a faction weights its
candidates by how badly it already gets on with each one's owner
(_pick_attack_target), so the seeded corp-vs-corp standing that had been pure
data now decides who moves on whom.

An AI faction keeps no operative pool — its attack force and garrison cap are
derived from its holdings (_attack_force / AI_GARRISON_CAP) rather than tracked,
so there is no shadow CorpState per faction.

Once the player takes over a Faction (corp_turn.py), that faction is excluded
from this AI loop via player_faction_id — its daily move becomes the player's
own decision instead. Note the exclusion is only on *acting*: the player's
territory is still ordinary attack_candidates ground for everyone else.

RivalRunners now make a real choice each day rather than only drifting, and
that day is split into three fixed RUNNER_BLOCK_HOURS blocks matching
Character.hour_of_day rather than one activity for the whole day: block 0 is
their productive time (a job, legwork, or shopping), block 1 is next (the
bar, or laying low), block 2 is always RESTING. What a runner is doing now
depends on when you look, via RunnerState.activities (one RunnerActivity per
block) and RunnerState.current(hour_of_day). Only block 0 can move them
(LEGWORK wanders one hop, WORKING relocates to the job site) or touch
anything outside this module; block 1 carries block 0's activity over
unchanged when it was WORKING — a job can run long — otherwise rolls the bar
against laying low. State lives in a caller-owned
`rival_runner_states` dict of RunnerState (persisted on ShadowguyApp, not
here — rivals.py stays leaf-ish), which is what gives RivalAction.territory_id
its content and lets surveillance.py's Surveillance checks have somewhere
real to catch them.

Two activities bite the player: WORKING takes a real JobOffer off a real
fixer's board (marking it fixer.JobOffer.taken_by), so a job you sat on is a
job you can lose. SHOPPING spends a runner's own cash via runners.buy_gear —
the same ladder a completed job already pays into, so the world's other
runners keep gearing up even on a day they didn't work. The rest — LEGWORK,
LAYING_LOW, DRINKING, RESTING, RECOVERING — are informational: they drive
movement and give PhoneScreen something true to show, but nothing rolls
against them yet. RECOVERING is the one with a cause, and the one activity
that still pre-empts the whole day (all three blocks alike): it only ever
follows a WORKING day that went badly.

WORKING is also how the city's runners get *better*: a job that doesn't hurt
them pays RUNNER_JOB_PAY and RUNNER_JOB_XP through runners.complete_job, which
is where experience becomes rating and eb becomes gear. Nothing about that
lives here — this module decides who worked today, runners.py decides what a
day's work is worth.

Each faction also gets a simplified, unstaffed research roll of its own
(RIVAL_RESEARCH_CHANCE) — no economy behind it, just enough to give
screens/info_screens.py's CorpWebsiteScreen (each corp's public "site")
something to report for a corp the player isn't running. Both that and a
territory claim get appended to a caller-owned `faction_events` blog log via
corp_turn.log_faction_event.

Leaf-ish: imports character/corpmap/corp_turn/factions/relations/runners, never
scene or app. The fixer board is reached through a TYPE_CHECKING-only import (the same
trick fixer.py itself uses for Character) — a runner only ever reads an
offer's timing and marks it taken, so nothing here needs fixer/scene at
runtime.
"""

import copy
import random
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from shadowguy.character import HOURS_PER_DAY, Character
from shadowguy.corp_turn import (
    TECHNOLOGIES,
    AttackResult,
    FactionEvent,
    log_faction_event,
    resolve_attack,
)
from shadowguy.corpmap import (
    CorpMap,
    LocationKind,
    attack_candidates,
    claim_territory,
    expansion_candidates,
)
from shadowguy.factions import FACTIONS
from shadowguy.relations import Relations, relation
from shadowguy.runners import RIVAL_RUNNERS, RivalRunner, buy_gear, complete_job

if TYPE_CHECKING:
    from shadowguy.fixer import Fixer, JobOffer

# Per faction, per day; only rolled when the faction has an eligible neutral
# neighbor at all. First-slice number, not balance-simulated.
EXPANSION_CHANCE = 0.2

# --- Conflict ---------------------------------------------------------------
# An AI faction keeps no operative pool the way CorpState does — modelling one
# would mean giving every faction a shadow CorpState purely so the player never
# sees it. Instead a faction's force is *derived* from what it holds: bigger corps
# field bigger attacks and garrison harder, which is the only property of a pool
# the player can actually observe from outside.
#
# Per faction, per day: the odds it reinforces one of its own districts by 1,
# rolled only where something is under AI_GARRISON_CAP. The cap is per district,
# so a faction's total standing force still scales with its holdings.
AI_GARRISON_CHANCE = 0.35
AI_GARRISON_CAP = 4
# Per faction, per day: the odds it attacks a bordering rival, rolled only when it
# has a candidate at all. Deliberately well under EXPANSION_CHANCE — free ground
# is always the cheaper move, so corps mostly grow outward and turn on each other
# once the neutral ground runs out, which is what gives a run its shape.
ATTACK_CHANCE = 0.12
# What a faction commits to an attack: one operative per this many districts held,
# floored at MIN_AI_ATTACK_FORCE. A four-district corp throws 2, a ten-district
# corp throws 5 — so a runaway leader presses its advantage rather than stalling.
AI_TERRITORIES_PER_ATTACKER = 2
MIN_AI_ATTACK_FORCE = 2
# How much relations.py's seeded corp-vs-corp standing sways target choice. Each
# candidate is weighted by (RELATION_TARGET_BIAS - relation), so the corp a faction
# already dislikes is the one it moves on first, and an ally is picked only when
# there's nobody else to hit. This is the first thing to read relations at all —
# it was seeded at generation and consumed by nothing.
RELATION_TARGET_BIAS = 3

# Per faction, per day; only rolled when a Technology is available to it (prereqs
# met, not already "researched" — see rival_researched below). Deliberately not
# wired to any real economy (no scientists/labs for a rival corp, unlike
# corp_turn.collect_research) — this exists purely to give CorpWebsiteScreen's
# blog something to report for corps the player isn't running. First-slice
# number, not balance-simulated.
RIVAL_RESEARCH_CHANCE = 0.15


class RunnerActivity(StrEnum):
    """What an independent runner is doing in one of their day's three
    blocks (see RUNNER_BLOCK_HOURS). Also the movement rule: LEGWORK is the
    only activity that wanders, WORKING relocates to the job site, and the
    rest stay put."""

    WORKING = "working"  # took a job off a fixer's board and is running it
    LEGWORK = "legwork"  # asking around, casing ground — the old blind wander
    SHOPPING = "shopping"  # spending their own cash up the runners.GEAR_LADDERS
    LAYING_LOW = "laying_low"  # off the grid for a block
    DRINKING = "drinking"  # at a bar in their current territory
    RESTING = "resting"  # block 2, always — the day's last block is always rest
    RECOVERING = "recovering"  # laid up after a job that went badly


# For display, the way corpmap.MODIFIER_LABELS is: the enum values are ids, not
# prose. WORKING's label is the fallback for a state whose job_title is gone —
# a surface with the title should name the job instead.
ACTIVITY_LABELS = {
    RunnerActivity.WORKING: "out on a job",
    RunnerActivity.LEGWORK: "asking around",
    RunnerActivity.SHOPPING: "out shopping",
    RunnerActivity.LAYING_LOW: "laying low",
    RunnerActivity.DRINKING: "drinking",
    RunnerActivity.RESTING: "resting",
    RunnerActivity.RECOVERING: "laid up, took a bad hit",
}

# A runner's day is three fixed blocks of Character.hour_of_day (0-23, the same
# literal clock AlarmClockScreen renders as HH:00): block 0 (00:00-08:00) is
# theirs to work with, block 1 (08:00-16:00) is next, block 2 (16:00-24:00) is
# always rest. Derived from HOURS_PER_DAY rather than a bare 8 so the three
# stay in lockstep with it.
RUNNER_BLOCK_HOURS = HOURS_PER_DAY // 3


@dataclass
class RunnerState:
    """One independent runner's persistent day-to-day state. Replaces the bare
    territory id the wander used to track, since an activity has to survive to
    the next turn (recovery) and to display time (job_title).

    `activities` holds today's three block outcomes — replaces the old single
    day-long `activity`, since a runner's block 1 can diverge from their
    block 0 (still on the job) while block 2 never does (always RESTING).
    `current` is how a caller reads "right now" (BarScreen's DRINKING check,
    ContactsScreen's status line) rather than "today"."""

    territory_id: str
    activities: tuple[RunnerActivity, RunnerActivity, RunnerActivity] = (
        RunnerActivity.LEGWORK, RunnerActivity.LAYING_LOW, RunnerActivity.RESTING,
    )
    # WORKING only: the Scene.title of the offer they pulled, so a surface can
    # name the job without holding a reference to the offer itself (which
    # expire_offers drops the day after).
    job_title: str | None = None
    # How many more days RECOVERING is forced. Only ever set by a bad WORKING
    # day; 0 means they roll freely next turn.
    recovery_days: int = 0

    def current(self, hour_of_day: int) -> RunnerActivity:
        """Which of today's three blocks is happening right now, from
        Character.hour_of_day."""
        return self.activities[hour_of_day // RUNNER_BLOCK_HOURS]


# Per independent runner, per free day: the odds they go looking for work
# rather than idle in block 0. Only reached when a takeable offer actually
# exists, so the real rate is lower early in a run.
RUNNER_WORK_CHANCE = 0.35

# Block 0's idle table, rolled when a runner doesn't work: legwork (the old
# blind wander, and still the majority share — the old IDLE_ACTIVITY_WEIGHTS'
# LEGWORK share was tuned to reproduce a pre-block RUNNER_MOVE_CHANCE that no
# longer applies once SHOPPING and the bar/laying-low roll are split across
# two separate blocks, so this is a fresh first-slice number, not a carried-
# over one) or spending their own cash (runners.buy_gear). Not
# balance-simulated.
BLOCK0_IDLE_WEIGHTS = {
    RunnerActivity.LEGWORK: 0.7,
    RunnerActivity.SHOPPING: 0.3,
}

# Block 1's idle table, rolled when block 0 wasn't WORKING (a working block 0
# carries straight through instead — see _runner_turn). DRINKING needs a bar
# in the territory and falls back to LAYING_LOW without one. First-slice
# numbers, not balance-simulated.
BLOCK1_IDLE_WEIGHTS = {
    RunnerActivity.LAYING_LOW: 0.65,
    RunnerActivity.DRINKING: 0.35,
}

# Per WORKING day: the odds the job went badly enough to lay them up, and for
# how many days if it did. This is the only thing that produces RECOVERING —
# a runner is never hurt by anything the player did.
JOB_INJURY_CHANCE = 0.25
RECOVERY_DAYS = (1, 2)

# What a job a runner ran pays them, in eb and experience — banked through
# runners.complete_job, which is where it turns into rating and gear.
#
# Flat, not scaled by day tier the way the player's own jobs are
# (jobs.REWARD_BASE/JOB_XP_BASE): the offer they took carries its payout only
# inside its final stage's Outcome, and digging a Scene apart to read it would
# cost rivals.py an import of scene/jobs it deliberately doesn't have. Sized off
# the middle tier of both instead, so a runner's day of work is worth roughly
# what one of yours is.
#
# **An injured day pays nothing.** JOB_INJURY_CHANCE isn't only a day off any
# more — it's the job that went wrong, so it costs them the fee and the
# experience as well as the recovery. First-slice numbers, not
# balance-simulated.
RUNNER_JOB_PAY = 300
RUNNER_JOB_XP = 15


@dataclass
class RivalAction:
    """One actor's turn on a given day. For a faction, territory_id is set only
    when it claimed neutral ground that day, and activities/job_title stay None.
    For a runner, territory_id is always set — their territory after today's
    turn, whether or not they moved — and activities holds all three of
    today's blocks (RunnerState.activities). job_title/fixer_id are set only
    on a day they took a job (always block 0), which is the one thing here a
    caller currently reports — app notifies on it, but only for a fixer the
    player has actually met, which is what fixer_id is for."""

    kind: Literal["faction", "runner"]
    actor_id: str
    day: int
    territory_id: str | None = None
    activities: tuple[RunnerActivity, RunnerActivity, RunnerActivity] | None = None
    job_title: str | None = None
    fixer_id: str | None = None
    # Faction only: the attack this faction made on a rival today, if it made one.
    # Independent of territory_id (which stays the *neutral* ground it expanded
    # onto) because expanding and attacking are separate rolls, not one choice —
    # a faction can do both on the same day.
    attack: AttackResult | None = None


def _has_bar(corp_map: CorpMap, territory_id: str) -> bool:
    return any(loc.kind is LocationKind.BAR for loc in corp_map.territories[territory_id].locations)


def _faction_territories(corp_map: CorpMap, faction_id: str) -> list:
    """Sorted by id, so a garrison roll picks the same district for the same seed."""
    return sorted(
        (t for t in corp_map.territories.values() if t.owner == faction_id), key=lambda t: t.id
    )


def _reinforce(corp_map: CorpMap, faction_id: str, rng: random.Random) -> None:
    """One AI faction's standing-force upkeep: with AI_GARRISON_CHANCE, add a
    single operative to whichever of its districts is currently thinnest (ties
    broken by id). Thinnest-first rather than random so a faction shores up its
    weak flank instead of stacking one fortress, which is what stops the player
    from finding a permanently free way in."""
    holdings = [t for t in _faction_territories(corp_map, faction_id) if t.garrison < AI_GARRISON_CAP]
    if not holdings or rng.random() >= AI_GARRISON_CHANCE:
        return
    min(holdings, key=lambda t: t.garrison).garrison += 1


def _attack_force(corp_map: CorpMap, faction_id: str) -> int:
    """How many operatives this faction throws at a rival, derived from its
    holdings (see AI_TERRITORIES_PER_ATTACKER) rather than drawn from a pool it
    doesn't have."""
    held = len(_faction_territories(corp_map, faction_id))
    return max(MIN_AI_ATTACK_FORCE, held // AI_TERRITORIES_PER_ATTACKER)


def _pick_attack_target(
    corp_map: CorpMap, faction_id: str, candidates: list[str], relations: Relations | None, rng: random.Random
) -> str:
    """Which bordering rival district a faction moves on. Weighted by how badly it
    already gets on with each district's owner (relations.relation, seeded per run
    by relations.generate_relations): a corp hits the rival it likes least first.

    Weights are (RELATION_TARGET_BIAS - relation), floored at 1 so even a
    well-liked rival stays possible — a corp with one hostile neighbor should
    usually, not always, go for that one. Falls back to a flat pick when the map
    carries no relations at all (hand-built test fixtures omit them)."""
    if relations is None:
        return rng.choice(candidates)
    weights = [
        max(1, RELATION_TARGET_BIAS - relation(relations, faction_id, corp_map.territories[t].owner))
        for t in candidates
    ]
    return rng.choices(candidates, weights=weights)[0]


def _faction_attack(
    corp_map: CorpMap, faction_id: str, relations: Relations | None, rng: random.Random
) -> AttackResult | None:
    """One AI faction's shot at taking ground off a rival. Rolls ATTACK_CHANCE
    only when it actually borders one, picks a target by relations, and settles it
    through corp_turn.resolve_attack — the same dice the player's own
    attack_territory rolls, so the AI can't be fighting a different war."""
    candidates = attack_candidates(corp_map, faction_id)
    if not candidates or rng.random() >= ATTACK_CHANCE:
        return None
    target_id = _pick_attack_target(corp_map, faction_id, candidates, relations, rng)
    return resolve_attack(corp_map.territories[target_id], faction_id, _attack_force(corp_map, faction_id), rng)


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


def _weighted_roll(rng: random.Random, weights: dict[RunnerActivity, float]) -> RunnerActivity:
    """One draw from a block's idle table (BLOCK0_IDLE_WEIGHTS / BLOCK1_IDLE_WEIGHTS)."""
    return rng.choices(list(weights), weights=list(weights.values()))[0]


def _runner_turn(
    runner: RivalRunner,
    state: RunnerState,
    corp_map: CorpMap,
    fixers: list["Fixer"],
    day: int,
    rng: random.Random,
) -> "JobOffer | None":
    """Resolve one runner's day across its three blocks, mutating `state` in
    place and returning the offer they took, if any — the caller needs the
    offer's fixer, which isn't worth persisting on the state past the day it
    was taken.

    Takes the RivalRunner itself, not just their id: a day's work or a day's
    shopping also spends/pays them (runners.complete_job / buy_gear), and that
    lands on the runner rather than on their RunnerState — a hire earns the
    same way from the player's jobs, where there is no RunnerState in sight.

    Recovery pre-empts everything (all three blocks, no choice). Otherwise
    block 0 tries for work first — the only branch besides SHOPPING that
    touches anything outside this module — and falls back to legwork or
    shopping, which is also what decides whether they move. Block 1 carries a
    WORKING block 0 straight through (a job can run long) or rolls the bar
    against laying low. Block 2 is always RESTING."""
    state.job_title = None
    if state.recovery_days > 0:
        state.recovery_days -= 1
        state.activities = (RunnerActivity.RECOVERING,) * 3
        return None

    offer = None
    offers = _takeable_offers(fixers, day)
    if offers and rng.random() < RUNNER_WORK_CHANCE:
        offer = rng.choice(offers)
        offer.taken_by = runner.id
        block0 = RunnerActivity.WORKING
        state.job_title = offer.scene.title
        # A job whose target territory isn't on this map (or isn't set at all)
        # leaves them where they are rather than guessing at a destination.
        if offer.scene.target_territory_id in corp_map.territories:
            state.territory_id = offer.scene.target_territory_id
        if rng.random() < JOB_INJURY_CHANCE:
            state.recovery_days = rng.randint(*RECOVERY_DAYS)
        else:
            complete_job(runner, RUNNER_JOB_PAY, RUNNER_JOB_XP)
    else:
        block0 = _weighted_roll(rng, BLOCK0_IDLE_WEIGHTS)
        if block0 is RunnerActivity.SHOPPING:
            buy_gear(runner)
        elif block0 is RunnerActivity.LEGWORK:
            connections = corp_map.territories[state.territory_id].connections
            if connections:
                state.territory_id = rng.choice(connections)

    if block0 is RunnerActivity.WORKING:
        block1 = RunnerActivity.WORKING
    else:
        block1 = _weighted_roll(rng, BLOCK1_IDLE_WEIGHTS)
        if block1 is RunnerActivity.DRINKING and not _has_bar(corp_map, state.territory_id):
            block1 = RunnerActivity.LAYING_LOW

    block2 = RunnerActivity.RESTING
    state.activities = (block0, block1, block2)
    return offer


def resolve_rival_day(
    character: Character,
    corp_map: CorpMap,
    day: int,
    rng: random.Random,
    player_faction_id: str | None = None,
    rival_runner_states: dict[str, RunnerState] | None = None,
    fixers: list["Fixer"] | None = None,
    rival_researched: dict[str, set[str]] | None = None,
    faction_events: dict[str, list[FactionEvent]] | None = None,
    runners: list[RivalRunner] | None = None,
) -> list[RivalAction]:
    """Every Faction gets a shot at expanding into bordering neutral ground. A
    RivalRunner acts only while independent — excluded the moment they're on the
    player's crew, indefinite or for-job alike, since either engagement means
    they're working for the player that day, not freelancing on their own — and
    otherwise takes one three-block turn via _runner_turn.

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
    find work — every runner falls through to the idle table.

    rival_researched is the caller's persistent faction_id -> {technology_id}
    map (ShadowguyApp.rival_researched in production): a per-day roll
    (RIVAL_RESEARCH_CHANCE) hands an eligible faction one Technology whose
    prereqs it already has, purely so its corp website has something to report
    — no scientists/labs/research points are simulated for a rival corp.
    Defaults to a fresh dict when omitted (discarded after the call, same as
    rival_runner_states' default).

    faction_events is the caller's persistent faction_id -> [FactionEvent] blog
    log (ShadowguyApp.faction_events in production), appended to via
    corp_turn.log_faction_event whenever a faction claims territory or gains a
    technology here. Omitted (the default) means no log is kept.

    runners is the run's actual independent-runner roster (ShadowguyApp.runners
    in production: the guaranteed RIVAL_RUNNERS plus that run's random pick from
    runners.RUNNER_POOL, see runners.select_active_runners). Defaults to a
    throwaway *copy* of the RIVAL_RUNNERS three, which is fine for callers
    (mostly tests) that don't care about the extras — the copy is because a day
    of work now mutates the runner who did it, and handing out the roster table
    itself would leave that progress sitting in a module constant for everything
    that reads one afterwards."""
    if rival_runner_states is None:
        rival_runner_states = {}
    if fixers is None:
        fixers = []
    if runners is None:
        runners = copy.deepcopy(RIVAL_RUNNERS)
    if rival_researched is None:
        rival_researched = {}
    actions = []
    for faction in FACTIONS:
        if faction.id == player_faction_id:
            continue
        target_id = None
        candidates = expansion_candidates(corp_map, faction.id)
        if candidates and rng.random() < EXPANSION_CHANCE:
            target_id = rng.choice(candidates)
            claim_territory(corp_map.territories[target_id], faction.id, rng)
            if faction_events is not None:
                log_faction_event(
                    faction_events, faction.id, FactionEvent(kind="territory", day=day, territory_id=target_id)
                )
        _reinforce(corp_map, faction.id, rng)
        attack = _faction_attack(corp_map, faction.id, corp_map.relations or None, rng)
        if attack is not None and attack.captured and faction_events is not None:
            log_faction_event(
                faction_events,
                faction.id,
                FactionEvent(
                    kind="seizure",
                    day=day,
                    territory_id=attack.territory_id,
                    from_faction_id=attack.defender_id,
                ),
            )
        actions.append(
            RivalAction(
                kind="faction", actor_id=faction.id, day=day, territory_id=target_id, attack=attack
            )
        )

        researched = rival_researched.setdefault(faction.id, set())
        available = [
            tech for tech in TECHNOLOGIES if tech.id not in researched and set(tech.prereqs) <= researched
        ]
        if available and rng.random() < RIVAL_RESEARCH_CHANCE:
            technology = rng.choice(available)
            researched.add(technology.id)
            if faction_events is not None:
                log_faction_event(
                    faction_events,
                    faction.id,
                    FactionEvent(kind="technology", day=day, technology_id=technology.id),
                )
    for runner in runners:
        # On your crew, dead, or in a cell: either way they aren't out working the city
        # today (Character.runner_available covers the last two).
        if character.on_crew(runner.id) or not character.runner_available(runner.id):
            continue
        state = rival_runner_states.get(runner.id)
        if state is None:
            state = RunnerState(territory_id=rng.choice(list(corp_map.territories)))
            rival_runner_states[runner.id] = state
        taken = _runner_turn(runner, state, corp_map, fixers, day, rng)
        actions.append(
            RivalAction(
                kind="runner",
                actor_id=runner.id,
                day=day,
                territory_id=state.territory_id,
                activities=state.activities,
                job_title=state.job_title,
                fixer_id=taken.fixer_id if taken else None,
            )
        )
    return actions
