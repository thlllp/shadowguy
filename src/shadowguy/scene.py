from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check
from shadowguy.combat import Enemy
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
    # Applied to the scene's target_fixer_id. No rival effect — trust is a direct,
    # one-fixer relationship, unlike standing_shift's corp-vs-corp competition.
    fixer_trust_delta: int = 0
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


@dataclass(frozen=True)
class Encounter:
    """A fight, as the scene graph sees it.

    It lives here rather than in combat.py on purpose: it holds Outcomes, and
    combat.py must not import scene (scene imports combat for Enemy). So combat
    owns *how a fight resolves* and knows nothing about jobs; Encounter owns *what
    winning or running is worth*, and reuses the ordinary Outcome to say so — which
    is why fighting through the last stage of a job can pay the job's cash, rep and
    standing without a second reward path.

    `escape` is the Outcome for walking out (a made Dodge check, or a smoke
    grenade). Losing has no Outcome: you are at 0 health, and that is already death
    everywhere else in the game.
    """

    prompt: str
    enemies: tuple[Enemy, ...]
    victory: Outcome
    escape: Outcome


@dataclass
class Stage:
    id: str
    prompt: str
    choices: list[Choice]
    # A stage is either a set of choices or a fight, never both — a combat stage's
    # "choices" are combat.available_actions, which come from the runner's own gear
    # and skills rather than from the scene.
    combat: Encounter | None = None


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
    # Which Fixer issued this job (fixer.Fixer.id). Set by fixer.refresh_offers at
    # generation time, same as the other target_* fields — not by JobOffer, which
    # already carries fixer_id but only wraps the scene rather than being part of it.
    target_fixer_id: str | None = None

    def __post_init__(self) -> None:
        if self.start_stage not in self.stages:
            raise ValueError(f"{self.id}: start_stage {self.start_stage!r} is not a known stage")
        if self.stages[self.start_stage].combat is not None:
            # A fight is only ever routed to by a resolved check (that's what decides
            # the drop), so a scene may not *open* on one — the screen would have no
            # check to read and no choices to render.
            raise ValueError(f"{self.id}: start_stage {self.start_stage!r} cannot be a fight")
        for stage in self.stages.values():
            if stage.combat is not None:
                if stage.choices:
                    raise ValueError(
                        f"{self.id}: stage {stage.id!r} is a fight, so it cannot also offer choices"
                    )
                if not stage.combat.enemies:
                    raise ValueError(f"{self.id}: stage {stage.id!r} is a fight with nobody in it")
            for choice in stage.choices:
                skill_for(choice.skill)  # unknown skill id: fail here, not mid-roll
            for outcome in self._stage_outcomes(stage):
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
                if outcome.fixer_trust_delta and self.target_fixer_id is None:
                    raise ValueError(
                        f"{self.id}: stage {stage.id!r} moves fixer trust but the scene has no target fixer"
                    )

    @staticmethod
    def _stage_outcomes(stage: Stage) -> Iterable[Outcome]:
        """Every Outcome a stage can produce, whether it's a choice or a fight.

        The single place that enumerates them, so a rule (next_stage resolves,
        standing needs a target) can't hold for choices and quietly not for fights.
        """
        for choice in stage.choices:
            for outcome in (
                choice.success,
                choice.failure,
                choice.critical_success,
                choice.critical_failure,
            ):
                if outcome is not None:
                    yield outcome
        if stage.combat is not None:
            yield from (stage.combat.victory, stage.combat.escape)

    @property
    def max_cash_loss(self) -> int:
        """The most cash any path through this scene can charge — the stake.

        Derived from the outcomes rather than written beside them, so a scene can't
        claim a stake it doesn't risk. This is what the activity list gates on: you
        may not sit down at a table you cannot cover. That gate is the only reason
        apply_outcome can add cash_delta straight onto Character.cash — without it, a
        broke runner would ride every losing outcome for free (cash floored at 0) and
        the card table would be a money pump. If a scene ever charges cash outside the
        gig list, gate it there too.
        """
        worst = min(
            (
                outcome.cash_delta
                for stage in self.stages.values()
                for outcome in self._stage_outcomes(stage)
            ),
            default=0,
        )
        return max(0, -worst)


def validate_scene_registry(scenes: Iterable[Scene]) -> None:
    job_ids = {scene.id for scene in scenes if scene.kind == SceneKind.JOB}
    for scene in scenes:
        if scene.kind == SceneKind.LEGWORK and scene.prepares_for not in job_ids:
            raise ValueError(
                f"{scene.id}: legwork prepares_for {scene.prepares_for!r} is not a known job"
            )


def apply_outcome(character: Character, outcome: Outcome, scene: Scene) -> None:
    character.adjust_health(outcome.health_delta)
    # Not floored, unlike health: the activity list refuses a scene the runner can't
    # cover (Scene.max_cash_loss), so a losing outcome always has the cash to take.
    # Flooring here instead would hand a broke runner every loss for free.
    character.cash += outcome.cash_delta
    # Floored like health. A botched gig can burn rep you earned, but 0 is being a
    # nobody and there is nothing below that — the street can't owe you a bad name.
    character.rep = max(0, character.rep + outcome.rep_delta)
    if outcome.advantage_delta:
        banks_advantage_for = scene.prepares_for if scene.kind == SceneKind.LEGWORK else None
        if not banks_advantage_for:
            raise ValueError("outcome has advantage_delta but no scene to bank it for")
        character.add_advantage(banks_advantage_for, outcome.advantage_delta)
    if outcome.standing_delta:
        shift = standing_shift(scene.target_faction_id, outcome.standing_delta)
        for faction_id, delta in shift.items():
            character.adjust_standing(faction_id, delta)
    if outcome.fixer_trust_delta:
        character.adjust_fixer_trust(scene.target_fixer_id, outcome.fixer_trust_delta)


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
