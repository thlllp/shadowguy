"""Invariant tests for jobs.py's procedural job/legwork generation, over many seeds."""

import random

import pytest

from shadowguy.character import Character
from shadowguy.corpmap import (
    GENERATED_KINDS,
    PLAYER_OWNED_KINDS,
    TerritoryModifier,
    territory_distance,
)
from shadowguy.factions import FACTIONS_BY_ID
from shadowguy.gangs import GANGS
from shadowguy.jobs import (
    AMBUSH_LABEL,
    ARCHETYPES,
    DAMAGE_FOR_DELTA,
    JOB_SECURITY_HIT,
    JOB_STANDING_HIT,
    JOB_XP_BASE,
    LEGWORK_FIGHT_STAGE,
    NEARBY_DIFFICULTY,
    SITE_DIFFICULTY,
    SMUGGLING_BASE_DEADLINE_DAYS,
    SMUGGLING_DEADLINE_DAYS_PER_HOP,
    SPECIALIST_FOR_STAT,
    WETWORK_STRUCTURE,
    JobTiming,
    archetype_specialist,
    generate_job,
    generate_legwork_for_job,
    generate_smuggling_job,
)
from shadowguy.scene import Outcome, Scene, Stage, apply_outcome
from shadowguy.skills import skill_for

SEEDS = range(150)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_runs_three_or_four_stages(corp_map, seed):
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    fight_stages = {sid for sid in scene.stages if sid.endswith("_fight")}
    non_fight = len(scene.stages) - len(fight_stages)
    assert non_fight in (3, 4)
    # A narration stage (see JobStage.vigilance) never rolls, so nothing can ever
    # route into a fight beside it -- generate_job doesn't build one. Every other
    # non-fight stage still has exactly one.
    checkable = sum(1 for sid, s in scene.stages.items() if not sid.endswith("_fight") and s.narration is None)
    assert len(fight_stages) == checkable


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_last_non_fight_stage_carries_the_payout(corp_map, seed):
    scene, _timing = generate_job(day=10, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    stage_ids = sorted(
        (sid for sid in scene.stages if not sid.endswith("_fight")),
        key=lambda sid: int(sid.removeprefix("stage_")),
    )
    last = scene.stages[stage_ids[-1]]
    if last.narration is not None:
        # A quiet vigilance beat (see JobStage.vigilance) still has to pay out if it
        # lands on the last stage -- nothing went wrong, so the job still completes.
        assert last.narration.cash_delta > 0
        assert last.narration.rep_delta > 0
        assert last.narration.standing_delta == JOB_STANDING_HIT
        assert last.narration.experience_delta > 0
        return
    # The last stage's success outcome must actually pay cash/rep/standing.
    non_ambush = [c for c in last.choices if c.label != f"{AMBUSH_LABEL} ({skill_for('tactics').name})"]
    assert non_ambush, "last stage must have at least one non-ambush choice"
    for choice in non_ambush:
        assert choice.success.cash_delta > 0
        assert choice.success.rep_delta > 0
        assert choice.success.standing_delta == JOB_STANDING_HIT
        assert choice.success.experience_delta > 0


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_critical_success_pays_more_experience_than_plain_success(corp_map, seed):
    """critical_success uses the same 1.5x multiplier experience_delta shares with
    cash_delta, so a clean critical always pays more XP than a plain success."""
    scene, _timing = generate_job(day=10, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    stage_ids = sorted(
        (sid for sid in scene.stages if not sid.endswith("_fight")),
        key=lambda sid: int(sid.removeprefix("stage_")),
    )
    last = scene.stages[stage_ids[-1]]
    non_ambush = [c for c in last.choices if c.label != f"{AMBUSH_LABEL} ({skill_for('tactics').name})"]
    for choice in non_ambush:
        assert choice.critical_success.experience_delta > choice.success.experience_delta


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_plain_success_pays_exactly_tier_zero_xp_on_day_one(corp_map, seed):
    """day=1 is tier 0, and a plain success uses multiplier 1.0, so the payout
    should be exactly JOB_XP_BASE[0] with no rounding surprises."""
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    stage_ids = sorted(
        (sid for sid in scene.stages if not sid.endswith("_fight")),
        key=lambda sid: int(sid.removeprefix("stage_")),
    )
    last = scene.stages[stage_ids[-1]]
    non_ambush = [c for c in last.choices if c.label != f"{AMBUSH_LABEL} ({skill_for('tactics').name})"]
    for choice in non_ambush:
        assert choice.success.experience_delta == JOB_XP_BASE[0]


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_non_last_stage_pays_no_experience(corp_map, seed):
    """Only the final stage's payout carries XP — an earlier stage's success just
    advances the job, same as it does for cash/rep/standing."""
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    stage_ids = sorted(
        (sid for sid in scene.stages if not sid.endswith("_fight")),
        key=lambda sid: int(sid.removeprefix("stage_")),
    )
    if len(stage_ids) < 2:
        return
    first = scene.stages[stage_ids[0]]
    for choice in first.choices:
        assert choice.success.experience_delta == 0


def _stage_options(stage):
    """A stage's real approaches, whichever mode it's in: a plain Choice list, or a
    BurglaryStage's Entrances. Choice and Entrance share label/skill/failure, so
    callers can treat the two uniformly rather than branching per test."""
    return list(stage.burglary.entrances) if stage.burglary is not None else list(stage.choices)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_stage_approaches_have_distinct_stats(corp_map, seed):
    """Every stage's drawn approach pool must sit on different core stats -- the
    'a stage is a gate every build has to pass' rule."""
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    for sid, stage in scene.stages.items():
        if sid.endswith("_fight"):
            continue
        non_ambush = [o for o in _stage_options(stage) if not o.label.startswith(AMBUSH_LABEL)]
        stats = [skill_for(o.skill).stat for o in non_ambush]
        assert len(set(stats)) == len(stats)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_ambush_choice_present_on_every_non_fight_stage(corp_map, seed):
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    for sid, stage in scene.stages.items():
        if sid.endswith("_fight") or stage.narration is not None:
            # A narration beat (see JobStage.vigilance) never rolls, so there's
            # nothing to force into a fight -- no ambush choice to offer.
            continue
        labels = [o.label for o in _stage_options(stage)]
        assert any(label.startswith(AMBUSH_LABEL) for label in labels)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_approach_damage_matches_damage_for_delta_curve(corp_map, seed):
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    for sid, stage in scene.stages.items():
        if sid.endswith("_fight"):
            continue
        for option in _stage_options(stage):
            if option.label.startswith(AMBUSH_LABEL):
                continue
            # failure.health_delta is negative failure_damage from DAMAGE_FOR_DELTA.
            assert -option.failure.health_delta in DAMAGE_FOR_DELTA.values()


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_targets_a_real_held_territory_and_location(corp_map, seed):
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    territory = corp_map.territories[scene.target_territory_id]
    assert territory.owner in FACTIONS_BY_ID
    assert scene.target_faction_id == territory.owner
    location = next(loc for loc in territory.locations if loc.id == scene.target_location_id)
    assert location.kind in GENERATED_KINDS
    assert location.kind not in PLAYER_OWNED_KINDS


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_roles_match_non_fight_stage_count(corp_map, seed):
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    non_fight_count = sum(1 for sid in scene.stages if not sid.endswith("_fight"))
    assert len(scene.roles) == non_fight_count


@pytest.mark.parametrize("seed", SEEDS)
def test_specialist_job_keeps_its_lead_approach_through_the_partial_draw(corp_map, seed):
    """A Netrunner/Solo-specialist job (Intrusion/Wetwork) must never withdraw the
    lead approach that makes it that specialist's contract -- generate_job pins it.

    Checked positionally-independent of which template stage maps to which generated
    stage (the optional complication may or may not survive, shifting indices): every
    non-fight stage of a specialist job must offer at least one choice for that
    specialist, i.e. the specialist always has a way through every beat of their job.
    """
    rng = random.Random(seed)
    archetype = rng.choice(ARCHETYPES)
    specialist = archetype_specialist(archetype)
    if specialist is None:
        pytest.skip("generic archetype, no lead to pin")
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    for sid, stage in scene.stages.items():
        if sid.endswith("_fight") or stage.narration is not None:
            # A quiet vigilance beat (see JobStage.vigilance) has no choices at all --
            # the specialist's guaranteed way through only means something where
            # there's a check to withhold it from.
            continue
        # Wetwork's APPROACH is a burglary stage (see JobStage.burglary): the pool
        # lives on its Entrances, not stage.choices, but they're Choice-shaped too.
        options = stage.burglary.entrances if stage.burglary is not None else stage.choices
        non_ambush = [c for c in options if not c.label.startswith(AMBUSH_LABEL)]
        stats = {skill_for(c.skill).stat for c in non_ambush}
        specialists = {SPECIALIST_FOR_STAT[stat] for stat in stats}
        assert specialist in specialists


@pytest.mark.parametrize("seed", SEEDS)
def test_burglary_job_approach_is_a_burglary_stage_and_every_other_stage_is_not(corp_map, seed):
    rng = random.Random(seed)
    archetype = rng.choice(ARCHETYPES)
    if archetype.name not in ("Burglary", "Wetwork"):
        pytest.skip("not a burglary-stage job this seed")
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    # APPROACH is always the first stage kept (only COMPLICATION can be dropped),
    # so it's always stage_0.
    approach = scene.stages["stage_0"]
    assert approach.burglary is not None
    assert approach.choices == []
    assert len(approach.burglary.entrances) >= 3  # drawn approaches (>=2) + the ambush entry
    # Wetwork always breaks into a private COMPOUND, wherever the job site itself
    # is (WETWORK_STRUCTURE); Burglary's structure instead follows the site's own
    # kind (BURGLARY_STRUCTURE).
    if archetype.name == "Wetwork":
        assert approach.burglary.building.kind == WETWORK_STRUCTURE
    for sid, stage in scene.stages.items():
        if sid in ("stage_0", "stage_0_fight") or sid.endswith("_fight"):
            continue
        assert stage.burglary is None
        assert stage.choices


@pytest.mark.parametrize("seed", SEEDS)
def test_data_heist_fights_are_all_matrix_and_it_reads_as_a_netrunner_job(corp_map, seed):
    rng = random.Random(seed)
    archetype = rng.choice(ARCHETYPES)
    if archetype.name != "Data Heist":
        pytest.skip("not a Data Heist this seed")
    scene, _timing = generate_job(day=7, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    # A remote hack reads as the Netrunner's contract, worked entirely from afar.
    assert archetype_specialist(archetype) == "Netrunner"
    assert scene.has_matrix
    assert {role.specialist for role in scene.roles} == {"Netrunner"}
    assert {role.posture.value for role in scene.roles} == {"remote"}
    # Every fight beside a stage is ICE; no gunmen, no grid, and the non-fight stages
    # stay ordinary Choice stages.
    fights = [s for sid, s in scene.stages.items() if sid.endswith("_fight")]
    assert fights
    for fight in fights:
        assert fight.matrix is not None and fight.matrix.network.nodes
        assert fight.combat is None and fight.tactical is None
    for sid, stage in scene.stages.items():
        if sid.endswith("_fight"):
            continue
        assert stage.matrix is None
        assert stage.choices


def test_job_timing_no_deadline_never_expires_and_always_available():
    timing = JobTiming()
    assert timing.is_available(1)
    assert timing.is_available(9999)
    assert not timing.is_expired(9999)
    assert timing.label == "no deadline"


def test_job_timing_deadline_expires_strictly_after_the_day():
    timing = JobTiming(deadline_day=5)
    assert not timing.is_expired(5)
    assert timing.is_expired(6)
    assert timing.is_available(5)  # deadline doesn't restrict *availability*, only expiry


def test_job_timing_scheduled_only_available_on_that_exact_day():
    timing = JobTiming(scheduled_day=5)
    assert not timing.is_available(4)
    assert timing.is_available(5)
    assert not timing.is_available(6)
    assert not timing.is_expired(5)
    assert timing.is_expired(6)


# --- Legwork ---


@pytest.mark.parametrize("seed", SEEDS)
def test_legwork_offers_one_choice_per_generated_location_in_target_territory(corp_map, seed):
    rng = random.Random(seed)
    job_scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=rng)
    legwork = generate_legwork_for_job(job_scene, corp_map, rng=random.Random(seed))
    territory = corp_map.territories[job_scene.target_territory_id]
    generated_locations = [loc for loc in territory.locations if loc.kind in GENERATED_KINDS]
    start = legwork.stages["start"]
    assert len(start.choices) == len(generated_locations)


@pytest.mark.parametrize("seed", SEEDS)
def test_legwork_site_choice_is_hardest_and_pays_most_advantage(corp_map, seed):
    rng = random.Random(seed)
    job_scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=rng)
    legwork = generate_legwork_for_job(job_scene, corp_map, rng=random.Random(seed))
    start = legwork.stages["start"]
    site_choices = [c for c in start.choices if c.difficulty == SITE_DIFFICULTY]
    nearby_choices = [c for c in start.choices if c.difficulty == NEARBY_DIFFICULTY]
    assert len(site_choices) == 1
    assert site_choices[0].success.advantage_delta > nearby_choices[0].success.advantage_delta if nearby_choices else True


@pytest.mark.parametrize("seed", SEEDS)
def test_legwork_critical_failure_routes_to_a_real_combat_stage(corp_map, seed):
    rng = random.Random(seed)
    job_scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=rng)
    legwork = generate_legwork_for_job(job_scene, corp_map, rng=random.Random(seed))
    start = legwork.stages["start"]
    for choice in start.choices:
        assert choice.critical_failure.next_stage == LEGWORK_FIGHT_STAGE
    fight_stage = legwork.stages[LEGWORK_FIGHT_STAGE]
    assert fight_stage.combat is not None
    assert fight_stage.combat.enemies


# --- Smuggling (jobs.SmugglingJob -- a gang delivery, not a Scene) ---


@pytest.mark.parametrize("seed", SEEDS)
def test_generate_smuggling_job_destination_is_never_the_pickup(corp_map, seed):
    pickup_id = corp_map.player_start_id
    job = generate_smuggling_job(GANGS[0].id, pickup_id, corp_map, day=1, rng=random.Random(seed))
    assert job.destination_territory_id != pickup_id


@pytest.mark.parametrize("seed", SEEDS)
def test_generate_smuggling_job_deadline_scales_with_distance(corp_map, seed):
    pickup_id = corp_map.player_start_id
    job = generate_smuggling_job(GANGS[0].id, pickup_id, corp_map, day=1, rng=random.Random(seed))
    hops = territory_distance(corp_map, pickup_id, job.destination_territory_id)
    assert job.deadline_day == 1 + SMUGGLING_BASE_DEADLINE_DAYS + SMUGGLING_DEADLINE_DAYS_PER_HOP * hops


def test_generate_smuggling_job_carries_the_gang_that_gave_it(corp_map):
    job = generate_smuggling_job(GANGS[0].id, corp_map.player_start_id, corp_map, day=1, rng=random.Random(0))
    assert job.gang_id == GANGS[0].id


# --- Outcome.security_delta -------------------------------------------------
# The corp-mode consequence of runner work: a completed job knocks Security off the
# district it hit, which is half of corp_turn.defense_strength.


@pytest.mark.parametrize("seed", SEEDS)
def test_only_the_final_stage_of_a_job_carries_a_security_hit(corp_map, seed):
    """Same rule the cash/rep/standing payouts follow — a job softens a district
    when it's *finished*, not once per stage walked through."""
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    terminal = [
        outcome
        for stage in scene.stages.values()
        for choice in stage.choices
        for outcome in (choice.success, choice.critical_success)
        if outcome is not None and outcome.next_stage is None
    ]
    non_terminal = [
        outcome
        for stage in scene.stages.values()
        for choice in stage.choices
        for outcome in (choice.success, choice.critical_success)
        if outcome is not None and outcome.next_stage is not None
    ]
    assert all(o.security_delta == JOB_SECURITY_HIT for o in terminal)
    assert all(o.security_delta == 0 for o in non_terminal)


def test_apply_outcome_lowers_security_on_the_target_district(corp_map):
    territory = corp_map.territories[corp_map.player_start_id]
    territory.modifiers[TerritoryModifier.SECURITY] = 3
    scene = Scene(
        id="s",
        title="t",
        stages={"start": Stage(id="start", prompt="p", choices=[])},
        target_territory_id=territory.id,
    )
    apply_outcome(Character(name="t"), Outcome(text="", security_delta=-1), scene, corp_map)
    assert territory.modifiers[TerritoryModifier.SECURITY] == 2


def test_apply_outcome_clamps_security_at_zero(corp_map):
    """Grinding the same block forever bottoms out rather than going negative —
    defense_strength reads this number directly."""
    territory = corp_map.territories[corp_map.player_start_id]
    territory.modifiers[TerritoryModifier.SECURITY] = 0
    scene = Scene(
        id="s",
        title="t",
        stages={"start": Stage(id="start", prompt="p", choices=[])},
        target_territory_id=territory.id,
    )
    apply_outcome(Character(name="t"), Outcome(text="", security_delta=-1), scene, corp_map)
    assert territory.modifiers[TerritoryModifier.SECURITY] == 0


def test_apply_outcome_ignores_a_security_delta_with_no_target_territory(corp_map):
    """A gig, or a job on ground that isn't on this map: nothing to soften, and no
    raise for the missing key."""
    scene = Scene(
        id="s",
        title="t",
        stages={"start": Stage(id="start", prompt="p", choices=[])},
        target_territory_id=None,
    )
    apply_outcome(Character(name="t"), Outcome(text="", security_delta=-1), scene, corp_map)
