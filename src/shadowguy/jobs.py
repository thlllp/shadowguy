"""Procedural generation of job Scenes offered by Fixers."""

import random
import uuid
from dataclasses import dataclass

from shadowguy.corpmap import LOCATION_STAT, CorpMap, LocationKind
from shadowguy.factions import FACTIONS_BY_ID
from shadowguy.scene import Choice, Outcome, Scene, SceneKind, Stage

TARGETS = [
    "a corp exec's private files",
    "a rival fixer's stash",
    "a defector's biochip",
    "a black-market weapons cache",
]

STAT_FLAVOR = {
    "skill": "hack the system",
    "cool": "talk or bluff your way through",
    "body": "force your way through",
}

DIFFICULTY_BASE = (10, 13, 16)
REWARD_BASE = (250, 450, 700)

# Standing lost with the corp you just robbed, on a completed job.
JOB_STANDING_HIT = -2


@dataclass
class JobArchetype:
    name: str
    verb: str
    stat_sequence: tuple[str, ...]


ARCHETYPES = [
    JobArchetype(name="Heist", verb="break into", stat_sequence=("skill", "cool")),
    JobArchetype(name="Extraction", verb="extract a target from", stat_sequence=("cool", "body")),
    JobArchetype(name="Sabotage", verb="sabotage", stat_sequence=("skill", "body")),
]


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
    reward_base = REWARD_BASE[tier]

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    stage_ids = [f"stage_{i}" for i in range(len(archetype.stat_sequence))]
    stages: dict[str, Stage] = {}

    for i, stat in enumerate(archetype.stat_sequence):
        is_last = i == len(stage_ids) - 1
        next_stage = None if is_last else stage_ids[i + 1]
        difficulty = difficulty_base + i + rng.randint(-1, 2)
        prompt = (
            f"You need to {archetype.verb} {faction.name} at {location.name}, in {territory.name}, "
            f"to reach {target}."
            if i == 0
            else f"You're in deep. One more push before you're clear with {target}."
        )
        stages[stage_ids[i]] = Stage(
            id=stage_ids[i],
            prompt=prompt,
            choices=[
                Choice(
                    label=f"{STAT_FLAVOR[stat].capitalize()} ({stat.capitalize()})",
                    stat=stat,
                    difficulty=difficulty,
                    success=Outcome(
                        text="It goes clean.",
                        next_stage=next_stage,
                        cash_delta=reward_base if is_last else 0,
                        rep_delta=1 if is_last else 0,
                        standing_delta=JOB_STANDING_HIT if is_last else 0,
                    ),
                    failure=Outcome(
                        text="It gets messy, but you push on.",
                        health_delta=-3,
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
                        health_delta=-8,
                        next_stage=next_stage,
                    ),
                )
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


# How each kind of place is scouted, in flavor text. The stat itself lives in
# corpmap.LOCATION_STAT — that's also what corpmap._location_kinds reads to
# keep a district's filler slot off its own specialty's stat, so there is one
# place that says "DATA is a skill check" rather than two that must agree.
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
        stat = LOCATION_STAT[location.kind]
        approach = LEGWORK_APPROACH_TEXT[location.kind]
        is_site = location.id == job.target_location_id
        label = f"Case {location.name} itself" if is_site else approach.format(name=location.name)
        choices.append(
            Choice(
                label=f"{label} ({stat.capitalize()})",
                stat=stat,
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
