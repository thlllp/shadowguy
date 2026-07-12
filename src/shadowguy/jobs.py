"""Procedural generation of job Scenes offered by Fixers."""

import random
import uuid
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.corpmap import LOCATION_SKILL, CorpMap, LocationKind
from shadowguy.factions import FACTIONS_BY_ID
from shadowguy.scene import Choice, Outcome, Scene, SceneKind, Stage
from shadowguy.skills import skill_for

TARGETS = [
    "a corp exec's private files",
    "a rival fixer's stash",
    "a defector's biochip",
    "a black-market weapons cache",
]

DIFFICULTY_BASE = (10, 13, 16)
REWARD_BASE = (250, 450, 700)

# How much harder the last stage of a job is than the first. Spread across however
# many stages the job turned out to have, so the arc is the same shape whether it
# runs 3 stages or 4 — a longer job is more *checks*, not a steeper climb. (A flat
# +1 per stage index, which is what this replaces, quietly made 4-stage jobs harder
# to finish than 3-stage ones for the same money.)
STAGE_DIFFICULTY_RAMP = 2

# A critical failure hurts this much more than a plain one.
CRITICAL_FAILURE_MULTIPLIER = 2

# A stage offers a subset of its pool, not the whole thing: how many ways in this
# particular job happens to have is part of what makes one offer better than another.
# FULL_POOL_CHANCE of the time you get every approach; otherwise you get exactly
# PARTIAL_POOL_SIZE of them — an exact count, not a floor, so widening a pool adds
# approaches the full-pool roll can reach but does not make the partial draw any
# wider. It doubles as the minimum pool size, since a pool must have at least this
# many to draw from (guarded at import).
PARTIAL_POOL_SIZE = 2
FULL_POOL_CHANCE = 0.35

# Standing lost with the corp you just robbed, on a completed job.
JOB_STANDING_HIT = -2


class StageType(StrEnum):
    """What a stage *is*, not just what it rolls.

    Every archetype walks the same arc — get there, do the thing, get out — with
    its own pools and prose for each beat. The type is the semantic handle on a
    stage: it's what lets a job say "this one has a nasty exfil" rather than
    "this one has a stage_2", and it is the intended hook for hired support later
    (a netrunner covers your OBJECTIVE, muscle covers your EXFIL). Nothing reads
    it that way yet — today it carries the prompt and marks which stages are
    optional.
    """

    APPROACH = "approach"  # get to the job
    OBJECTIVE = "objective"  # do the thing you came to do
    COMPLICATION = "complication"  # it stops going to plan
    EXFIL = "exfil"  # be somewhere else


# Stage type -> the chance it shows up at all. A type in here is optional and rolled
# for at generation, so a job runs 3 or 4 stages; a type absent from it is mandatory.
# The chance lives *with* the type rather than beside it as a lone COMPLICATION_CHANCE:
# a second optional type would otherwise silently inherit the complication's odds.
# Membership is the "is this optional?" test, so there is one table here, not two that
# have to agree. The last stage of an archetype must be mandatory (the payout rides on
# the final stage) — guarded below.
OPTIONAL_STAGE_CHANCE = {StageType.COMPLICATION: 0.4}

# REWARD_BASE prices a job with no complication. One that has one is a longer job
# with an extra check's worth of blood in it, so it pays this much more per extra
# stage — otherwise the fixer board would quietly price identical-looking offers the
# same while one of them is strictly worse.
REWARD_PER_EXTRA_STAGE = 0.3


# The risk curve: how much health a failed approach costs, by how much easier than
# the stage's base difficulty it was. This is the *only* place job damage is set —
# an Approach's damage is derived from its difficulty_delta, never written next to
# it, so "the easy way in is the one that hurts" is structural and a row physically
# cannot be tuned out of the gradient.
#
# Calibrated against job *length*: a job runs 3-4 stages, and an off-stat specialist
# takes the bloody route on most of them, so a body-1 runner's 15 health is the
# budget these numbers spend. Doubling them is how you get a 13% death rate on a
# routine job — re-run the balance sim if you touch this.
DAMAGE_FOR_DELTA = {
    1: 1,  # hard and clean
    0: 2,
    -1: 3,
    -2: 4,  # easy and bloody
}

if sorted(DAMAGE_FOR_DELTA, reverse=True) != sorted(
    DAMAGE_FOR_DELTA, key=lambda d: DAMAGE_FOR_DELTA[d]
):
    raise ValueError("DAMAGE_FOR_DELTA must hurt more the easier the check gets")


@dataclass(frozen=True)
class Approach:
    """One way through a job stage: a skill, and how hard/bloody that way is.

    difficulty_delta shifts the stage's rolled difficulty, and it alone fixes the
    health cost (failure_damage, x CRITICAL_FAILURE_MULTIPLIER on a critical): the
    cheap check is always the one that hurts. A stage rolls its base difficulty
    *once* and every approach is offset from it, so a delta means the same thing on
    every job.
    """

    skill: str  # a skill id (skills.SKILLS_BY_ID)
    difficulty_delta: int
    flavor: str

    @property
    def failure_damage(self) -> int:
        return DAMAGE_FOR_DELTA[self.difficulty_delta]


@dataclass(frozen=True)
class JobStage:
    """One beat of a job: what kind of beat it is, how it reads, and the ways through.

    `approaches` is a *pool*, not the offer — generate_job draws a subset of it.
    `prompt` is a format string over verb/faction/territory/location/target; the
    fields are checked at import so a bad one can't KeyError mid-generation.
    """

    type: StageType
    prompt: str
    approaches: tuple[Approach, ...]


@dataclass
class JobArchetype:
    name: str
    verb: str
    stages: tuple[JobStage, ...]


# name, verb, then one row per stage: (StageType, prompt, approach pool).
# Approach row: (skill id, difficulty delta, flavor). The damage is not written here
# — it falls out of the delta via DAMAGE_FOR_DELTA.
#
# Each pool holds a hard/clean, a middling, and an easy/bloody way through, sitting
# on three *different* core stats — checked at import below. That is the whole point
# of the table: a stage whose approaches share a stat is not a choice, it's a
# formality that one build passes twice and the rest fail once. It also means no
# build walks every stage of every job, and a runner who is wrong for a stage can
# still buy their way past it with health.
#
# A generated job offers a *subset* of each pool (see PARTIAL_POOL_SIZE), so two
# Heists are not the same Heist: one may leave the door open for your build and the
# next may not. Pools therefore want to stay wider than PARTIAL_POOL_SIZE — a pool of
# exactly two never varies.
#
# Every archetype walks APPROACH -> OBJECTIVE -> (COMPLICATION) -> EXFIL. The
# complication is optional (OPTIONAL_STAGE_CHANCE), so a job is 3 or 4 stages, and it
# is where the job turns on you rather than merely resisting you.
_ARCHETYPE_ROWS = (
    (
        "Heist",
        "break into",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("stealth", 1, "Slip past the perimeter unseen"),
                    ("forgery", 0, "Badge in on credentials you wrote yourself"),
                    ("toughness", -2, "Go straight through the fence and eat the hits"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're inside {location}. {target} sits behind the last real lock.",
                (
                    ("hack", 1, "Crack the ice around the prize"),
                    ("infiltration", 0, "Work the vault's locks by hand"),
                    ("blunt", -2, "Put the case through with a wrecking bar"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A patrol that shouldn't be on the roster doubles back down the corridor.",
                (
                    ("listening", 1, "Track them by sound and stay a room ahead"),
                    ("intimidation", 0, "Freeze the one who sees you"),
                    ("grapple", -2, "Put them on the floor before they can call it in"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have {target}. Now you have to be somewhere else.",
                (
                    ("dodge", 1, "Slip the cordon before it closes"),
                    ("deception", 0, "Walk out past the response team like you belong there"),
                    ("lift", -2, "Force the loading shutter and go"),
                ),
            ),
        ),
    ),
    (
        "Extraction",
        "extract a target from",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("deception", 1, "Walk in as staff nobody thinks to question"),
                    ("tactics", 0, "Time your approach to the shift change"),
                    ("acrobatics", -1, "Come in over the roofline"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You've found them. Getting them to move is a separate problem.",
                (
                    ("grapple", 1, "Put the target down and carry them out"),
                    ("intimidation", 0, "Make it very clear they are leaving with you"),
                    ("toughness", -2, "Take what the room does to you and keep hold of them"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "The target panics, and panic is loud.",
                (
                    ("read_face", 1, "See it coming in their eyes and get ahead of it"),
                    ("negotiations", 0, "Cut them a deal on the spot"),
                    ("center_of_gravity", -2, "Take them off their feet and keep moving"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have them. Now get them off {faction}'s ground.",
                (
                    ("forgery", 1, "Badge the two of you through the checkpoint"),
                    ("lung_capacity", 0, "Carry them, and don't stop"),
                    ("short_blade", -2, "Cut through the cordon"),
                ),
            ),
        ),
    ),
    (
        "Sabotage",
        "sabotage",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("stealth", 1, "Come in through the service ducts"),
                    ("infer", 0, "Read the plant's layout and walk straight to it"),
                    ("toughness", -2, "Come through the loading door and dare them to stop you"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "The machinery is in front of you. It has to fail, and not while you're stood here.",
                (
                    ("tinkering", 1, "Rig the hardware to fail hours from now"),
                    ("sleight_of_hand", 0, "Palm the charge onto it as you walk past"),
                    ("lift", -2, "Wreck the machinery by hand"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A coolant line lets go, and a tech comes to find out why.",
                (
                    ("pattern_seeking", 1, "Spot the cascade before it reaches you"),
                    ("resist_poison", 0, "Ride out the chemical wash and keep working"),
                    ("grapple", -2, "Put the tech in a locker"),
                ),
            ),
            (
                StageType.EXFIL,
                "It's going to go, and you are still inside {location}.",
                (
                    ("lung_capacity", 1, "Run, and keep running until the sirens fade"),
                    ("dodge", 0, "Slip the response team in the stairwell"),
                    ("short_blade", -2, "Cut your way out through whoever is closest"),
                ),
            ),
        ),
    ),
)

ARCHETYPES = [
    JobArchetype(
        name=name,
        verb=verb,
        stages=tuple(
            JobStage(
                type=stage_type,
                prompt=prompt,
                approaches=tuple(Approach(*approach) for approach in approaches),
            )
            for stage_type, prompt, approaches in stages
        ),
    )
    for name, verb, stages in _ARCHETYPE_ROWS
]

# Everything the table can get wrong, caught at import rather than mid-generation.
#
# A typo'd skill id fails here, not mid-roll, and so does a difficulty_delta that
# DAMAGE_FOR_DELTA doesn't price — the risk curve is the only source of job damage, so
# a delta off the end of it has no damage at all. A pool too small to draw
# PARTIAL_POOL_SIZE from would make rng.sample raise, and a one-approach stage is
# not a choice at all — it's the regression this table exists to prevent. Neither is
# a stage whose approaches share a core stat: a job stage is a gate every build has
# to pass, so two approaches on one stat hand that stat's runner a second bite and
# everyone else nothing. Checking the stat rule across the whole *pool* means it
# holds for every subset the generator can draw. (Gigs are optional and
# self-selected, so they're allowed to be themed on one stat; see
# content.GIG_CHEM_TRIAL.)
_PROMPT_FIELDS = {
    "verb": "",
    "faction": "",
    "territory": "",
    "location": "",
    "target": "",
}

for _archetype in ARCHETYPES:
    if not _archetype.stages:
        raise ValueError(f"{_archetype.name}: a job needs at least one stage")
    # The cash, rep and standing all ride on whichever stage ends up last. If that
    # stage could be dropped as optional, the payout would silently move with it.
    if _archetype.stages[-1].type in OPTIONAL_STAGE_CHANCE:
        raise ValueError(
            f"{_archetype.name}: the last stage carries the payout and cannot be optional, "
            f"got {_archetype.stages[-1].type}"
        )
    for _stage in _archetype.stages:
        _stage.prompt.format(**_PROMPT_FIELDS)  # unknown field: fail here, not mid-job
        if len(_stage.approaches) < PARTIAL_POOL_SIZE:
            raise ValueError(
                f"{_archetype.name}/{_stage.type}: a stage pool needs at least "
                f"{PARTIAL_POOL_SIZE} approaches to draw from, got {len(_stage.approaches)}"
            )
        for _approach in _stage.approaches:
            # Approach.failure_damage only reads DAMAGE_FOR_DELTA when a job is being
            # generated, so an off-curve delta would KeyError at a fixer refresh.
            if _approach.difficulty_delta not in DAMAGE_FOR_DELTA:
                raise ValueError(
                    f"{_archetype.name}/{_stage.type}: {_approach.skill!r} has no damage on the "
                    f"risk curve for difficulty_delta {_approach.difficulty_delta}, "
                    f"which must be one of {sorted(DAMAGE_FOR_DELTA, reverse=True)}"
                )
        _stats = [skill_for(approach.skill).stat for approach in _stage.approaches]
        if len(set(_stats)) != len(_stats):
            raise ValueError(
                f"{_archetype.name}/{_stage.type}: a job stage's approaches must sit on "
                f"different core stats, got {_stats}"
            )


@dataclass
class JobTiming:
    deadline_day: int | None = None
    scheduled_day: int | None = None

    @property
    def label(self) -> str:
        if self.scheduled_day is not None:
            return f"must run on day {self.scheduled_day}"
        if self.deadline_day is not None:
            return f"expires after day {self.deadline_day}"
        return "no deadline"

    def is_available(self, day: int) -> bool:
        if self.scheduled_day is not None:
            return day == self.scheduled_day
        return True

    def is_expired(self, day: int) -> bool:
        if self.scheduled_day is not None:
            return day > self.scheduled_day
        if self.deadline_day is not None:
            return day > self.deadline_day
        return False


def _tier_for_day(day: int) -> int:
    return min(2, (day - 1) // 3)


def _random_timing(day: int, rng: random.Random) -> JobTiming:
    kind = rng.choices(["none", "deadline", "scheduled"], weights=[0.4, 0.35, 0.25])[0]
    if kind == "deadline":
        return JobTiming(deadline_day=day + rng.randint(2, 5))
    if kind == "scheduled":
        return JobTiming(scheduled_day=day + rng.randint(1, 4))
    return JobTiming()


def generate_job(
    day: int, corp_map: CorpMap, rng: random.Random | None = None
) -> tuple[Scene, JobTiming]:
    rng = rng or random.Random()
    archetype = rng.choice(ARCHETYPES)
    # The mark is a real corp, hit in a district it actually holds on this run's map.
    held = sorted(
        (t for t in corp_map.territories.values() if t.owner in FACTIONS_BY_ID),
        key=lambda t: t.id,
    )
    territory = rng.choice(held)
    faction = FACTIONS_BY_ID[territory.owner]
    location = rng.choice(territory.locations)
    target = rng.choice(TARGETS)
    tier = _tier_for_day(day)
    difficulty_base = DIFFICULTY_BASE[tier]

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    # Which beats this job actually has. An optional stage that doesn't make the cut
    # is gone before any ids are handed out, so stage_0..n stay contiguous.
    job_stages = [
        stage
        for stage in archetype.stages
        if stage.type not in OPTIONAL_STAGE_CHANCE
        or rng.random() < OPTIONAL_STAGE_CHANCE[stage.type]
    ]
    mandatory = sum(1 for s in archetype.stages if s.type not in OPTIONAL_STAGE_CHANCE)
    extra_stages = len(job_stages) - mandatory
    reward_base = int(REWARD_BASE[tier] * (1 + REWARD_PER_EXTRA_STAGE * extra_stages))
    stage_ids = [f"stage_{i}" for i in range(len(job_stages))]
    stages: dict[str, Stage] = {}

    for i, job_stage in enumerate(job_stages):
        is_last = i == len(stage_ids) - 1
        next_stage = None if is_last else stage_ids[i + 1]
        # Which ways through this job happens to leave open. Kept in pool order so
        # the clean approach still reads before the bloody one.
        pool = job_stage.approaches
        if rng.random() < FULL_POOL_CHANCE:
            approaches = list(pool)
        else:
            approaches = sorted(rng.sample(pool, PARTIAL_POOL_SIZE), key=pool.index)
        # Rolled once for the stage: every approach is offset from the same number,
        # so an Approach's difficulty_delta means the same thing on every job.
        ramp = round(STAGE_DIFFICULTY_RAMP * i / (len(job_stages) - 1)) if i else 0
        difficulty = difficulty_base + ramp + rng.randint(-1, 2)
        stages[stage_ids[i]] = Stage(
            id=stage_ids[i],
            prompt=job_stage.prompt.format(
                verb=archetype.verb,
                faction=faction.name,
                territory=territory.name,
                location=location.name,
                target=target,
            ),
            choices=[
                Choice(
                    label=f"{approach.flavor} ({skill_for(approach.skill).name})",
                    skill=approach.skill,
                    difficulty=difficulty + approach.difficulty_delta,
                    success=Outcome(
                        text="It goes clean.",
                        next_stage=next_stage,
                        cash_delta=reward_base if is_last else 0,
                        rep_delta=1 if is_last else 0,
                        standing_delta=JOB_STANDING_HIT if is_last else 0,
                    ),
                    failure=Outcome(
                        text="It gets messy, but you push on.",
                        health_delta=-approach.failure_damage,
                        next_stage=next_stage,
                    ),
                    critical_success=Outcome(
                        text="Flawless. You walk out with more than you bargained for.",
                        next_stage=next_stage,
                        cash_delta=int(reward_base * 1.5) if is_last else 0,
                        rep_delta=2 if is_last else 0,
                        standing_delta=JOB_STANDING_HIT if is_last else 0,
                    ),
                    critical_failure=Outcome(
                        text="It goes bad, fast.",
                        health_delta=-approach.failure_damage * CRITICAL_FAILURE_MULTIPLIER,
                        next_stage=next_stage,
                    ),
                )
                for approach in approaches
            ],
        )

    scene = Scene(
        id=job_id,
        title=f"{archetype.name}: {faction.name} ({territory.name})",
        kind=SceneKind.JOB,
        stamina_cost=2 if tier == 0 else 3,
        start_stage=stage_ids[0],
        stages=stages,
        target_faction_id=faction.id,
        target_territory_id=territory.id,
        target_location_id=location.id,
    )
    return scene, _random_timing(day, rng)


# How each kind of place is scouted, in flavor text. The skill itself lives in
# corpmap.LOCATION_SKILL — that's also what corpmap._location_kinds reads to
# keep a district's filler slot off its own specialty's stat, so there is one
# place that says "DATA is a Hack check" rather than two that must agree.
LEGWORK_APPROACH_TEXT = {
    LocationKind.DATA: "Sift the traffic in and out of {name}",
    LocationKind.LAB: "Pull the intake records at {name}",
    LocationKind.DEPOT: "Tail a shift worker out of {name}",
    LocationKind.SOCIAL: "Work the crowd at {name}",
    LocationKind.PAWN: "Work the counter for gossip at {name}",
    LocationKind.WEAPON_SHOP: "Tail a shipment out of {name}",
    LocationKind.AUTO_DEALER: "Chat up the lot staff at {name}",
    LocationKind.PHARMACY: "Pull the register logs at {name}",
    LocationKind.COMPUTER_STORE: "Sift the sales records at {name}",
}
if set(LEGWORK_APPROACH_TEXT) != set(LocationKind):
    raise ValueError("LEGWORK_APPROACH_TEXT must have exactly one entry per LocationKind")

# Casing the target itself is the hardest read to get, and the best one.
SITE_DIFFICULTY = 14
SITE_ADVANTAGE = 4
NEARBY_DIFFICULTY = 11
NEARBY_ADVANTAGE = 2


def generate_legwork_for_job(job: Scene, corp_map: CorpMap) -> Scene:
    territory = corp_map.territories[job.target_territory_id]
    faction = FACTIONS_BY_ID[job.target_faction_id]

    choices = []
    for location in territory.locations:
        skill = skill_for(LOCATION_SKILL[location.kind])
        approach = LEGWORK_APPROACH_TEXT[location.kind]
        is_site = location.id == job.target_location_id
        label = f"Case {location.name} itself" if is_site else approach.format(name=location.name)
        choices.append(
            Choice(
                label=f"{label} ({skill.name})",
                skill=skill.id,
                difficulty=SITE_DIFFICULTY if is_site else NEARBY_DIFFICULTY,
                success=Outcome(
                    text=(
                        "You clock the pattern cold. You'll know exactly when to move."
                        if is_site
                        else "A shift roster, a few loose words. It adds up."
                    ),
                    advantage_delta=SITE_ADVANTAGE if is_site else NEARBY_ADVANTAGE,
                ),
                failure=Outcome(text="Nothing solid turns up. Wasted time."),
                critical_failure=Outcome(
                    text="Someone clocks you scoping the place. You bolt.",
                    health_delta=-2,
                ),
            )
        )

    return Scene(
        id=f"legwork_{job.id}",
        title=f"Case the job: {job.title}",
        kind=SceneKind.LEGWORK,
        prepares_for=job.id,
        start_stage="start",
        stages={
            "start": Stage(
                id="start",
                prompt=(
                    f"You have time to work {territory.name} before the job. "
                    f"{faction.name} holds the district through a handful of places."
                ),
                choices=choices,
            ),
        },
    )
