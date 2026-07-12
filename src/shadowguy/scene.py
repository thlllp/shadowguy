from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check
from shadowguy.factions import standing_shift
from shadowguy.skills import skill_for, skill_value


class SceneKind(StrEnum):
    JOB = "job"
    GIG = "gig"
    LEGWORK = "legwork"


@dataclass
class Outcome:
    text: str
    health_delta: int = 0
    cash_delta: int = 0
    rep_delta: int = 0
    advantage_delta: int = 0
    # Applied to the scene's target_faction_id; rivals move the other way.
    standing_delta: int = 0
    next_stage: str | None = None


@dataclass
class Choice:
    label: str
    # A skill id (skills.SKILLS_BY_ID), not a core stat: the roll is the skill's
    # tied stat plus the rank invested in it. Scene.__post_init__ rejects unknown ids.
    skill: str
    difficulty: int
    success: Outcome
    failure: Outcome
    critical_success: Outcome | None = None
    critical_failure: Outcome | None = None

    def outcome_for(self, result: CheckResult) -> Outcome:
        if result is CheckResult.CRITICAL_SUCCESS:
            return self.critical_success or self.success
        if result is CheckResult.CRITICAL_FAILURE:
            return self.critical_failure or self.failure
        if result is CheckResult.SUCCESS:
            return self.success
        return self.failure


@dataclass
class Stage:
    id: str
    prompt: str
    choices: list[Choice]


@dataclass
class Scene:
    id: str
    title: str
    stages: dict[str, Stage]
    start_stage: str = "start"
    kind: SceneKind = SceneKind.JOB
    prepares_for: str | None = None
    stamina_cost: int = 1
    # Which corp this scene is run against, and where. target_territory_id is the
    # anchor for scenes that should also move territory control, not just standing.
    # target_location_id is the specific place inside that territory being hit.
    target_faction_id: str | None = None
    target_territory_id: str | None = None
    target_location_id: str | None = None

    def __post_init__(self) -> None:
        if self.start_stage not in self.stages:
            raise ValueError(f"{self.id}: start_stage {self.start_stage!r} is not a known stage")
        for stage in self.stages.values():
            for choice in stage.choices:
                skill_for(choice.skill)  # unknown skill id: fail here, not mid-roll
                for outcome in (choice.success, choice.failure, choice.critical_success, choice.critical_failure):
                    if outcome is None:
                        continue
                    if outcome.next_stage is not None and outcome.next_stage not in self.stages:
                        raise ValueError(
                            f"{self.id}: stage {stage.id!r} references unknown next_stage {outcome.next_stage!r}"
                        )
                    if outcome.advantage_delta and (self.kind != SceneKind.LEGWORK or self.prepares_for is None):
                        raise ValueError(
                            f"{self.id}: stage {stage.id!r} banks advantage but the scene is not legwork prep"
                        )
                    if outcome.standing_delta and self.target_faction_id is None:
                        raise ValueError(
                            f"{self.id}: stage {stage.id!r} moves standing but the scene has no target faction"
                        )


def validate_scene_registry(scenes: Iterable[Scene]) -> None:
    job_ids = {scene.id for scene in scenes if scene.kind == SceneKind.JOB}
    for scene in scenes:
        if scene.kind == SceneKind.LEGWORK and scene.prepares_for not in job_ids:
            raise ValueError(
                f"{scene.id}: legwork prepares_for {scene.prepares_for!r} is not a known job"
            )


def apply_outcome(character: Character, outcome: Outcome, scene: Scene) -> None:
    character.adjust_health(outcome.health_delta)
    character.cash += outcome.cash_delta
    character.rep += outcome.rep_delta
    if outcome.advantage_delta:
        banks_advantage_for = scene.prepares_for if scene.kind == SceneKind.LEGWORK else None
        if not banks_advantage_for:
            raise ValueError("outcome has advantage_delta but no scene to bank it for")
        character.add_advantage(banks_advantage_for, outcome.advantage_delta)
    if outcome.standing_delta:
        shift = standing_shift(scene.target_faction_id, outcome.standing_delta)
        for faction_id, delta in shift.items():
            character.adjust_standing(faction_id, delta)


def resolve_choice(character: Character, scene: Scene, choice: Choice) -> tuple[CheckResult, Outcome]:
    advantage = character.consume_advantage(scene.id) if scene.kind == SceneKind.JOB else 0
    roll = resolve_check(
        stat_value=skill_value(character, choice.skill),
        difficulty=choice.difficulty,
        advantage=advantage,
    )
    outcome = choice.outcome_for(roll.result)
    apply_outcome(character, outcome, scene)
    return roll.result, outcome
