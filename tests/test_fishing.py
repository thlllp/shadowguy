"""Invariant tests for fishing.py's cast/wait/reel Scene generation."""

from shadowguy.fishing import (
    CAST_SKILLS,
    FISH_CASH,
    FISH_CRITICAL_CASH,
    REEL_SKILLS,
    generate_fishing_trip,
)
from shadowguy.scene import SceneKind


def test_fishing_trip_is_a_three_stage_cast_wait_reel_scene():
    scene = generate_fishing_trip()
    assert scene.kind is SceneKind.FISHING
    assert scene.start_stage == "cast"
    assert set(scene.stages) == {"cast", "wait", "reel"}
    assert scene.stages["wait"].narration.next_stage == "reel"


def test_fishing_cast_and_reel_offer_every_skill_in_their_pool():
    scene = generate_fishing_trip()
    assert {c.skill for c in scene.stages["cast"].choices} == {skill for skill, _label in CAST_SKILLS}
    assert {c.skill for c in scene.stages["reel"].choices} == {skill for skill, _label in REEL_SKILLS}


def test_a_botched_cast_or_reel_ends_the_trip_with_no_next_stage():
    scene = generate_fishing_trip()
    for choice in scene.stages["cast"].choices:
        assert choice.failure.next_stage is None
        assert choice.critical_failure.next_stage is None
    for choice in scene.stages["reel"].choices:
        assert choice.failure.next_stage is None
        assert choice.critical_failure.next_stage is None


def test_a_made_cast_advances_to_wait_and_never_pays_cash():
    scene = generate_fishing_trip()
    for choice in scene.stages["cast"].choices:
        assert choice.success.next_stage == "wait"
        assert choice.critical_success.next_stage == "wait"
        assert choice.success.cash_delta == 0
        assert choice.critical_success.cash_delta == 0


def test_reel_success_pays_cash_and_critical_pays_more():
    scene = generate_fishing_trip()
    for choice in scene.stages["reel"].choices:
        assert choice.success.cash_delta == FISH_CASH
        assert choice.critical_success.cash_delta == FISH_CRITICAL_CASH
        assert choice.critical_success.cash_delta > choice.success.cash_delta
        assert choice.failure.cash_delta == 0
        assert choice.critical_failure.cash_delta == 0


def test_fishing_never_touches_health_rep_or_any_standing():
    """Cash + flavor only -- a botched cast/reel costs nothing but the trip's own
    hours_cost, same as a missed scavenge or gig failure without its crit-fail
    health hit."""
    scene = generate_fishing_trip()
    for stage in scene.stages.values():
        outcomes = [stage.narration] if stage.narration is not None else []
        for choice in stage.choices:
            outcomes += [choice.success, choice.failure, choice.critical_success, choice.critical_failure]
        for outcome in outcomes:
            assert outcome.health_delta == 0
            assert outcome.rep_delta == 0
            assert outcome.standing_delta == 0
            assert outcome.fixer_trust_delta == 0
            assert outcome.local_standing_delta == 0
            assert outcome.experience_delta == 0
            assert outcome.security_delta == 0
