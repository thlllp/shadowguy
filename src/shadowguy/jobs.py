"""Procedural generation of job Scenes offered by Fixers."""

import random
import uuid
from dataclasses import dataclass

from shadowguy.scene import Choice, Outcome, Scene, SceneKind, Stage

CORPS = ["Arasaka", "Militech", "Biotechnica", "Kang Tao", "Petrochem"]
LOCATIONS = [
    "a data tower in City Center",
    "a warehouse on Autopia Row",
    "a clinic in Kabuki",
    "a server farm in Northside",
]
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


def generate_job(day: int, rng: random.Random | None = None) -> tuple[Scene, JobTiming]:
    rng = rng or random.Random()
    archetype = rng.choice(ARCHETYPES)
    corp = rng.choice(CORPS)
    location = rng.choice(LOCATIONS)
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
            f"You need to {archetype.verb} {corp}, to reach {target} hidden in {location}."
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
        title=f"{archetype.name}: {corp}",
        kind=SceneKind.JOB,
        stamina_cost=2 if tier == 0 else 3,
        start_stage=stage_ids[0],
        stages=stages,
    )
    return scene, _random_timing(day, rng)


def generate_legwork_for_job(job_id: str, job_title: str) -> Scene:
    return Scene(
        id=f"legwork_{job_id}",
        title=f"Case the job: {job_title}",
        kind=SceneKind.LEGWORK,
        prepares_for=job_id,
        start_stage="start",
        stages={
            "start": Stage(
                id="start",
                prompt=f"You scope out the details before running the {job_title} job.",
                choices=[
                    Choice(
                        label="Case the location (Cool)",
                        stat="cool",
                        difficulty=11,
                        success=Outcome(
                            text="You clock the pattern cold. You'll know exactly when to move.",
                            advantage_delta=3,
                        ),
                        failure=Outcome(text="Nothing solid turns up. Wasted time."),
                        critical_failure=Outcome(
                            text="Someone clocks you scoping the place. You bolt.",
                            health_delta=-2,
                        ),
                    ),
                ],
            ),
        },
    )
