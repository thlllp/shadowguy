from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check
from shadowguy.combat import Enemy
from shadowguy.factions import standing_shift
from shadowguy.matrix import Ice
from shadowguy.skills import skill_for, skill_value
from shadowguy.tactical import Coord, Grid


class SceneKind(StrEnum):
    JOB = "job"
    GIG = "gig"
    LEGWORK = "legwork"


class Posture(StrEnum):
    """Where a crew member works a beat from."""

    ON_SITE = "on-site"  # runs in with you — muscle on the exfil, an opener on the approach
    REMOTE = "remote"  # works it from afar — the netrunner in the car on the objective


@dataclass(frozen=True)
class Role:
    """A crew position on a job: a beat someone could cover, the kind of specialist who
    fits it, and whether they'd work it on-site or from afar.

    Plain data on purpose — it holds display strings, not jobs.StageType, so it can live
    here on the Scene without scene.py importing jobs (which imports scene). jobs.py owns
    the *derivation* (from each stage's beat and its lead approach's skill); this is just
    the record. Descriptive for now: recruiting a runner to fill a role comes later, and
    when it does, `filled_by` (a runner id) is the natural field to add here.
    """

    beat: str  # the stage's StageType value, as a label ("approach", "objective", ...)
    specialist: str  # runner archetype that fits ("Netrunner" / "Solo" / "Infiltrator")
    posture: Posture


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
    # Applied to the scene's target_character_id (a corpmap.LocalCharacter). Like fixer
    # trust: a direct, one-person relationship, no rival effect.
    local_standing_delta: int = 0
    next_stage: str | None = None


def _outcome_for_result(
    result: CheckResult,
    success: Outcome,
    failure: Outcome,
    critical_success: Outcome | None,
    critical_failure: Outcome | None,
) -> Outcome:
    """Shared by Choice.outcome_for and Entrance.outcome_for -- same four-way branch,
    so the two check-shaped types can't quietly drift apart on how a result picks
    an Outcome."""
    if result is CheckResult.CRITICAL_SUCCESS:
        return critical_success or success
    if result is CheckResult.CRITICAL_FAILURE:
        return critical_failure or failure
    if result is CheckResult.SUCCESS:
        return success
    return failure


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
        return _outcome_for_result(result, self.success, self.failure, self.critical_success, self.critical_failure)


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
class TacticalStage:
    """A tactical-combat fight, as the scene graph sees it — the grid analogue of Encounter.

    Same split, same reason: it lives here, not in tactical.py, because it holds Outcomes
    and tactical.py must not import scene. tactical.py owns *how the grid fight resolves*;
    this owns *what winning or slipping out is worth*, through the ordinary Outcome — so a
    tactical stage pays a job's cash/rep/standing on the same reward path as everything else.

    `escape` is the Outcome for leaving by an exit tile. Losing has no Outcome: you're at
    0 health, which is death everywhere else in the game.
    """

    prompt: str
    grid: Grid
    player_start: Coord
    # (enemy template, spawn cell). The live fight (tactical.TacticalState) is rebuilt
    # from these each time the screen opens, so this stays an immutable template.
    enemies: tuple[tuple[Enemy, Coord], ...]
    victory: Outcome
    escape: Outcome
    exits: frozenset[Coord] = frozenset()


@dataclass(frozen=True)
class Entrance:
    """One way into a Burglary stage's building -- Choice-shaped (same skill/
    difficulty/outcome shape as a normal Approach-derived Choice), plus where the
    interior walk begins in BurglaryStage.grid if this entrance is picked. The check
    resolves the instant it's picked (resolve_entrance); the walk that follows is
    spatial risk, not a second roll -- see BurglaryStage."""

    label: str
    skill: str
    difficulty: int
    spawn: Coord
    success: Outcome
    failure: Outcome
    critical_success: Outcome | None = None
    critical_failure: Outcome | None = None

    def outcome_for(self, result: CheckResult) -> Outcome:
        return _outcome_for_result(result, self.success, self.failure, self.critical_success, self.critical_failure)


@dataclass
class BurglaryStage:
    """A job's APPROACH stage, played out as: pick an Entrance (a Choice-shaped
    check, resolved immediately on pick) on a small diagram, then walk the interior
    grid from that entrance's spawn to the objective tile, avoiding guard sightlines.
    Reaching the objective carries the scene to whatever next_stage the entrance's
    Outcome already set; getting spotted by a guard fires `spotted` instead (which
    is why `spotted` isn't per-entrance -- it's a hazard of the walk itself, not of
    how you got in). This is the one place in the game a single stage attempt can
    apply two Outcomes in sequence (the entrance's, then maybe `spotted`'s) --
    deliberate, so keep `spotted`'s cost modest, since it stacks on whatever the
    entrance check already did."""

    prompt: str
    entrances: tuple[Entrance, ...]
    grid: Grid
    objective: Coord
    spotted: Outcome
    # Static watcher positions, not combat.Enemy -- nothing to fight while sneaking
    # past. Walking within one's line of sight ends the walk in `spotted` instead
    # of at the objective (see tactical.spotted()).
    guards: tuple[Coord, ...] = ()


@dataclass(frozen=True)
class MatrixStage:
    """A matrix fight, as the scene graph sees it — the ICE analogue of Encounter.

    Same split, same reason as Encounter/TacticalStage: it lives here, not in matrix.py,
    because it holds Outcomes and matrix.py must not import scene. matrix.py owns *how the
    ICE fight resolves*; this owns *what seizing the data or being ejected is worth*,
    through the ordinary Outcome — so a Data Heist's matrix stage pays its cash/rep/
    standing on the same reward path as every other stage.

    `victory` is seizing the data (every ICE down); `escape` is being ejected — integrity
    gone, or a voluntary jack-out. Unlike Encounter/TacticalStage, ejection is the *only*
    way to lose: a remote hack can't kill you, so there's no death branch to leave
    Outcome-less.
    """

    prompt: str
    ice: tuple[Ice, ...]
    victory: Outcome
    escape: Outcome


@dataclass
class Stage:
    id: str
    prompt: str
    choices: list[Choice]
    # A stage is a set of choices, a fight, a tactical map, a burglary, or a matrix
    # fight -- exactly one, never a mix (guarded in Scene.__post_init__). A
    # fight's/map's "choices" come from the runner's own gear and skills
    # (combat.available_actions / matrix.available_matrix_actions / the grid), not the
    # scene; a burglary's choices are its Entrances, picked on a diagram screen rather
    # than a text list.
    combat: Encounter | None = None
    tactical: TacticalStage | None = None
    burglary: BurglaryStage | None = None
    matrix: MatrixStage | None = None


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
    # Which LocalCharacter (corpmap.LocalCharacter.id) a gig's standing reward lands on.
    target_character_id: str | None = None
    # The crew positions this job offers, one per beat, derived at generation (jobs.py).
    # Empty for gigs/legwork. Descriptive for now — nothing fills them yet.
    roles: list[Role] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._validate_start_stage()
        for stage in self.stages.values():
            self._validate_stage(stage)
            for outcome in self._stage_outcomes(stage):
                self._validate_outcome(stage, outcome)

    def _validate_start_stage(self) -> None:
        if self.start_stage not in self.stages:
            raise ValueError(f"{self.id}: start_stage {self.start_stage!r} is not a known stage")
        start = self.stages[self.start_stage]
        if start.combat is not None or start.tactical is not None or start.matrix is not None:
            raise ValueError(
                f"{self.id}: start_stage {self.start_stage!r} cannot be a fight, tactical map, or matrix run"
            )

    def _validate_stage(self, stage: Stage) -> None:
        modes = sum(
            1 for mode in (stage.choices, stage.combat, stage.tactical, stage.burglary, stage.matrix) if mode
        )
        if modes > 1:
            raise ValueError(
                f"{self.id}: stage {stage.id!r} must be exactly one of choices, a fight, "
                "a tactical map, a burglary, or a matrix run"
            )
        if stage.combat is not None and not stage.combat.enemies:
            raise ValueError(f"{self.id}: stage {stage.id!r} is a fight with nobody in it")
        if stage.tactical is not None and not stage.tactical.enemies:
            raise ValueError(f"{self.id}: stage {stage.id!r} is a tactical map with nobody in it")
        if stage.burglary is not None and not stage.burglary.entrances:
            raise ValueError(f"{self.id}: stage {stage.id!r} is a burglary with no entrances")
        if stage.matrix is not None and not stage.matrix.ice:
            raise ValueError(f"{self.id}: stage {stage.id!r} is a matrix run with no ICE")
        for choice in stage.choices:
            skill_for(choice.skill)
        if stage.burglary is not None:
            for entrance in stage.burglary.entrances:
                skill_for(entrance.skill)

    def _validate_outcome(self, stage: Stage, outcome: Outcome) -> None:
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
        if outcome.local_standing_delta and self.target_character_id is None:
            raise ValueError(
                f"{self.id}: stage {stage.id!r} moves local standing but the scene has no target character"
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
        if stage.tactical is not None:
            yield from (stage.tactical.victory, stage.tactical.escape)
        if stage.matrix is not None:
            yield from (stage.matrix.victory, stage.matrix.escape)
        if stage.burglary is not None:
            for entrance in stage.burglary.entrances:
                for outcome in (
                    entrance.success,
                    entrance.failure,
                    entrance.critical_success,
                    entrance.critical_failure,
                ):
                    if outcome is not None:
                        yield outcome
            yield stage.burglary.spotted

    @property
    def has_matrix(self) -> bool:
        """Whether any stage is a matrix fight — a Data Heist (jobs.py). What the UI reads
        to decide a job needs the cyberdeck/Hack warning (see screens.matrix_warning)."""
        return any(stage.matrix is not None for stage in self.stages.values())

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


def apply_outcome(character: Character, outcome: Outcome, scene: Scene) -> None:
    character.adjust_health(outcome.health_delta)
    # Not floored, unlike health: the activity list refuses a scene the runner can't
    # cover (Scene.max_cash_loss), so a losing outcome always has the cash to take.
    # Flooring here instead would hand a broke runner every loss for free.
    character.cash += outcome.cash_delta
    # Floored, like health, but not at 0: a blown job or gig can now push rep
    # negative (see character.REP_FLOOR) rather than stopping at "nobody."
    character.adjust_rep(outcome.rep_delta)
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
    if outcome.local_standing_delta:
        character.adjust_local_standing(scene.target_character_id, outcome.local_standing_delta)


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


def resolve_entrance(character: Character, scene: Scene, entrance: Entrance) -> tuple[CheckResult, Outcome]:
    """Same shape as resolve_choice, for a BurglaryStage's Entrance -- the entrance
    check resolves (and applies its Outcome) the instant it's picked, before any
    interior walk. Only next_stage's actual effect (the scene advancing) waits on
    the walk; see screens/burglary_screens.py and SceneScreen._on_entrance_picked."""
    advantage = character.consume_advantage(scene.id) if scene.kind == SceneKind.JOB else 0
    roll = resolve_check(
        stat_value=skill_value(character, entrance.skill),
        difficulty=entrance.difficulty,
        advantage=advantage,
    )
    outcome = entrance.outcome_for(roll.result)
    apply_outcome(character, outcome, scene)
    return roll.result, outcome
