"""Invariant tests for jobs.py's procedural job/legwork generation, over many seeds."""

import random

import pytest

from shadowguy.corpmap import GENERATED_KINDS, PLAYER_OWNED_KINDS, generate_corp_map
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.jobs import (
    AMBUSH_LABEL,
    ARCHETYPES,
    DAMAGE_FOR_DELTA,
    JOB_STANDING_HIT,
    LEGWORK_FIGHT_STAGE,
    NEARBY_DIFFICULTY,
    SITE_DIFFICULTY,
    SPECIALIST_FOR_STAT,
    JobTiming,
    archetype_specialist,
    generate_job,
    generate_legwork_for_job,
)
from shadowguy.skills import skill_for

SEEDS = range(150)


@pytest.fixture(scope="module")
def corp_map():
    return generate_corp_map(FACTIONS, random.Random(0))


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_runs_three_or_four_stages(corp_map, seed):
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    fight_stages = {sid for sid in scene.stages if sid.endswith("_fight")}
    non_fight = len(scene.stages) - len(fight_stages)
    assert non_fight in (3, 4)
    # Every non-fight stage has exactly one fight beside it.
    assert len(fight_stages) == non_fight


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_job_last_non_fight_stage_carries_the_payout(corp_map, seed):
    scene, _timing = generate_job(day=10, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    stage_ids = sorted(
        (sid for sid in scene.stages if not sid.endswith("_fight")),
        key=lambda sid: int(sid.removeprefix("stage_")),
    )
    last = scene.stages[stage_ids[-1]]
    # The last stage's success outcome must actually pay cash/rep/standing.
    non_ambush = [c for c in last.choices if c.label != f"{AMBUSH_LABEL} ({skill_for('tactics').name})"]
    assert non_ambush, "last stage must have at least one non-ambush choice"
    for choice in non_ambush:
        assert choice.success.cash_delta > 0
        assert choice.success.rep_delta > 0
        assert choice.success.standing_delta == JOB_STANDING_HIT


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
        if sid.endswith("_fight"):
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
        if sid.endswith("_fight"):
            continue
        non_ambush = [c for c in stage.choices if not c.label.startswith(AMBUSH_LABEL)]
        stats = {skill_for(c.skill).stat for c in non_ambush}
        specialists = {SPECIALIST_FOR_STAT[stat] for stat in stats}
        assert specialist in specialists


@pytest.mark.parametrize("seed", SEEDS)
def test_burglary_job_approach_is_a_burglary_stage_and_every_other_stage_is_not(corp_map, seed):
    rng = random.Random(seed)
    archetype = rng.choice(ARCHETYPES)
    if archetype.name != "Burglary":
        pytest.skip("not a Burglary job this seed")
    scene, _timing = generate_job(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    # APPROACH is always the first stage kept (only COMPLICATION can be dropped),
    # so it's always stage_0.
    approach = scene.stages["stage_0"]
    assert approach.burglary is not None
    assert approach.choices == []
    assert len(approach.burglary.entrances) >= 3  # drawn approaches (>=2) + the ambush entry
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
        assert fight.matrix is not None and fight.matrix.ice
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
