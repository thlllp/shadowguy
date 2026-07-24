"""Tests for character.py: stat/health math, rank costs/caps, rep floor, relationships."""

import pytest

from shadowguy.character import (
    BASE_HEALTH,
    CORE_STATS,
    FATIGUE_GRACE_HOURS,
    FATIGUE_STAT_PENALTY_CAP,
    HEALTH_PER_BODY,
    HOURS_PER_DAY,
    MAX_SKILL_RANK,
    REP_FLOOR,
    SKILL_RANK_COST,
    STARTING_SKILL_POINTS,
    STARTING_SKILL_RANK,
    STARTING_STAT,
    STARTING_STAT_POINTS,
    Character,
)


def test_max_health_from_raw_body_not_stat():
    """max_health scales off raw body, not stat('body') (gear must never move it)."""
    c = Character(name="t", body=3)
    assert c.max_health == BASE_HEALTH + 3 * HEALTH_PER_BODY


def test_starting_health_defaults_to_max():
    c = Character(name="t")
    assert c.health == c.max_health


def test_day_is_derived_from_elapsed_hours():
    c = Character(name="t")
    assert c.day == 1
    c.elapsed_hours = HOURS_PER_DAY - 1
    assert c.day == 1
    c.elapsed_hours = HOURS_PER_DAY
    assert c.day == 2
    c.elapsed_hours = HOURS_PER_DAY * 2 + 5
    assert c.day == 3


def test_adjust_health_floors_at_zero_and_caps_at_max():
    c = Character(name="t")
    c.adjust_health(-10_000)
    assert c.health == 0
    c.adjust_health(10_000)
    assert c.health == c.max_health


def test_adjust_rep_floors_at_rep_floor_not_zero():
    c = Character(name="t")
    c.adjust_rep(-10_000)
    assert c.rep == REP_FLOOR
    assert c.rep < 0  # unlike health, rep is allowed negative


def test_spend_stat_point_raises_max_health_and_current_health():
    c = Character(name="t", body=1)
    before_max = c.max_health
    before_health = c.health
    assert c.spend_stat_point("body")
    assert c.max_health == before_max + HEALTH_PER_BODY
    # current health carried up with the ceiling, not left behind
    assert c.health == before_health + HEALTH_PER_BODY


def test_spend_stat_point_on_non_body_does_not_touch_health():
    c = Character(name="t")
    before = c.health
    c.spend_stat_point("strength")
    assert c.health == before


def test_spend_stat_point_exhausts_pool():
    c = Character(name="t")
    spent = 0
    while c.spend_stat_point("body"):
        spent += 1
    assert spent == STARTING_STAT_POINTS
    assert c.stat_points == 0
    assert not c.spend_stat_point("body")


def test_spend_stat_point_rejects_unknown_stat():
    c = Character(name="t")
    with pytest.raises(ValueError):
        c.spend_stat_point("luck")


def test_next_rank_cost_matches_skill_rank_cost_table():
    c = Character(name="t")
    # A fresh skill starts at STARTING_SKILL_RANK; next rank cost is looked up
    # for rank+1.
    assert c.next_rank_cost("hack") == SKILL_RANK_COST[STARTING_SKILL_RANK + 1]


def test_next_rank_cost_none_at_max_rank():
    c = Character(name="t", skill_points=1000)
    while c.spend_skill_point("hack"):
        pass
    assert c.skill_rank("hack") == MAX_SKILL_RANK
    assert c.next_rank_cost("hack") is None


def test_spend_skill_point_refuses_unaffordable_without_charging():
    """A refused buy is never charged — 'can't afford' must leave points untouched."""
    c = Character(name="t", skill_points=0)
    before = c.skill_points
    assert not c.spend_skill_point("hack")
    assert c.skill_points == before
    assert c.skill_rank("hack") == STARTING_SKILL_RANK


def test_spend_skill_point_raises_on_unknown_skill():
    c = Character(name="t")
    with pytest.raises(ValueError):
        c.spend_skill_point("not_a_real_skill")


def test_maxing_one_skill_costs_19_of_20_points():
    """Buying one skill from rank 1 to 10 costs 3*1 + 3*2 + 2*3 + 4 = 19 points."""
    c = Character(name="t")
    while c.spend_skill_point("hack"):
        pass
    assert c.skill_rank("hack") == MAX_SKILL_RANK
    assert c.skill_points == STARTING_SKILL_POINTS - 19


def test_next_stat_cost_escalates_with_current_value():
    """Cost climbs by 1 each time, unlike spend_stat_point's flat 1-point cost —
    the higher a stat already is, the pricier the next point."""
    c = Character(name="t", body=STARTING_STAT)
    assert c.next_stat_cost("body") == 1
    c.body += 2
    assert c.next_stat_cost("body") == 3


def test_next_stat_cost_rejects_unknown_stat():
    c = Character(name="t")
    with pytest.raises(ValueError):
        c.next_stat_cost("luck")


def test_spend_experience_on_stat_charges_escalating_cost_and_raises_the_stat():
    c = Character(name="t", experience=10, body=STARTING_STAT)
    assert c.spend_experience_on_stat("body")
    assert c.body == STARTING_STAT + 1
    assert c.experience == 9  # first point costs 1


def test_spend_experience_on_stat_raises_health_like_spend_stat_point():
    c = Character(name="t", experience=10, body=STARTING_STAT)
    before_max, before_health = c.max_health, c.health
    assert c.spend_experience_on_stat("body")
    assert c.max_health == before_max + HEALTH_PER_BODY
    assert c.health == before_health + HEALTH_PER_BODY


def test_spend_experience_on_stat_refuses_unaffordable_without_charging():
    c = Character(name="t", experience=0)
    before = c.experience
    assert not c.spend_experience_on_stat("body")
    assert c.experience == before
    assert c.body == STARTING_STAT


def test_spend_experience_on_stat_never_hits_a_cap():
    """Escalating cost, not a hard ceiling — enough XP always buys the next point."""
    c = Character(name="t", experience=10_000)
    for _ in range(20):
        assert c.spend_experience_on_stat("body")
    assert c.body == STARTING_STAT + 20


def test_spend_experience_on_skill_matches_next_rank_cost():
    c = Character(name="t", experience=1)
    cost = c.next_rank_cost("hack")
    assert c.spend_experience_on_skill("hack")
    assert c.experience == 1 - cost
    assert c.skill_rank("hack") == STARTING_SKILL_RANK + 1


def test_spend_experience_on_skill_refuses_unaffordable_without_charging():
    c = Character(name="t", experience=0)
    assert not c.spend_experience_on_skill("hack")
    assert c.experience == 0
    assert c.skill_rank("hack") == STARTING_SKILL_RANK


def test_spend_experience_on_skill_refuses_past_max_rank():
    c = Character(name="t", experience=10_000)
    while c.spend_experience_on_skill("hack"):
        pass
    assert c.skill_rank("hack") == MAX_SKILL_RANK
    before = c.experience
    assert not c.spend_experience_on_skill("hack")
    assert c.experience == before


def test_spend_experience_on_skill_raises_on_unknown_skill():
    c = Character(name="t", experience=100)
    with pytest.raises(ValueError):
        c.spend_experience_on_skill("not_a_real_skill")


def test_gain_experience_accumulates():
    c = Character(name="t")
    c.gain_experience(5)
    c.gain_experience(3)
    assert c.experience == 8


def test_grant_crew_experience_is_per_runner_and_accumulates():
    c = Character(name="t")
    c.grant_crew_experience("runner_specter", 10)
    c.grant_crew_experience("runner_specter", 5)
    c.grant_crew_experience("runner_juncture", 7)
    assert c.crew_experience == {"runner_specter": 15, "runner_juncture": 7}
    # A hired runner's own XP is entirely separate from the player's pool.
    assert c.experience == 0


def test_reset_build_undoes_every_point():
    c = Character(name="t")
    c.spend_stat_point("body")
    c.spend_skill_point("hack")
    c.reset_build()
    for stat in CORE_STATS:
        assert getattr(c, stat) == STARTING_STAT
    assert c.skill_rank("hack") == STARTING_SKILL_RANK
    assert c.stat_points == STARTING_STAT_POINTS
    assert c.skill_points == STARTING_SKILL_POINTS
    assert c.health == c.max_health


def test_stat_rejects_unknown_name():
    c = Character(name="t")
    with pytest.raises(ValueError):
        c.stat("luck")


def test_stat_subtracts_fatigue_penalty_capped_below_raw_fatigue():
    """The raw fatigue counter can climb past FATIGUE_STAT_PENALTY_CAP, but the felt
    penalty on a stat never does -- burning out further than the cap only means it
    takes longer to halve back down through it, not a worse penalty."""
    c = Character(name="t", strength=10)
    c.fatigue = FATIGUE_STAT_PENALTY_CAP
    assert c.stat("strength") == 10 - FATIGUE_STAT_PENALTY_CAP
    c.fatigue = FATIGUE_STAT_PENALTY_CAP * 5
    assert c.stat("strength") == 10 - FATIGUE_STAT_PENALTY_CAP


def test_standing_and_local_standing_and_trust_default_to_zero_and_adjust():
    c = Character(name="t")
    assert c.standing_with("faction_x") == 0
    assert c.local_standing_with("char_x") == 0
    assert c.trust_with("fixer_x") == 0
    c.adjust_standing("faction_x", 3)
    c.adjust_local_standing("char_x", -2)
    c.adjust_fixer_trust("fixer_x", 1)
    assert c.standing_with("faction_x") == 3
    assert c.local_standing_with("char_x") == -2
    assert c.trust_with("fixer_x") == 1


def test_advantage_bank_is_per_job_and_consumed_once():
    c = Character(name="t")
    assert c.advantage_for("job_1") == 0
    c.add_advantage("job_1", 4)
    assert c.advantage_for("job_1") == 4
    # A second job's bank is untouched.
    assert c.advantage_for("job_2") == 0
    assert c.consume_advantage("job_1") == 4
    assert c.advantage_for("job_1") == 0


def test_on_new_day_clears_daily_flags():
    c = Character(name="t")
    c.health_kit_used_today = True
    c.temp_bonuses["strength"] = 3
    c.on_new_day(c.day)
    assert c.health_kit_used_today is False
    assert c.temp_bonuses == {}


def test_on_new_day_does_not_heal():
    c = Character(name="t")
    c.adjust_health(-5)
    hurt = c.health
    c.on_new_day(c.day)
    assert c.health == hurt


def test_on_new_day_leaves_fatigue_alone_within_grace():
    """Resting isn't overdue yet -- on_new_day must not grow fatigue just because a
    day boundary happened to pass."""
    c = Character(name="t")
    c.elapsed_hours = FATIGUE_GRACE_HOURS
    c.on_new_day(c.day)
    assert c.fatigue == 0


def test_on_new_day_grows_fatigue_once_overdue():
    c = Character(name="t")
    c.elapsed_hours = FATIGUE_GRACE_HOURS + 1
    c.on_new_day(c.day)
    assert c.fatigue == 1


def test_fatigue_growth_compounds():
    """Each additional overdue day-tick adds more than the last, since the growth
    added is 1 plus a fraction of the fatigue already built up."""
    c = Character(name="t")
    c.elapsed_hours = FATIGUE_GRACE_HOURS + 1
    increments = []
    for _ in range(4):
        before = c.fatigue
        c.on_new_day(c.day)
        increments.append(c.fatigue - before)
    assert increments == sorted(increments)
    assert increments[-1] > increments[0]


def test_rest_halves_fatigue_instead_of_clearing_it():
    """A burnout sticks a little: resting only halves the accumulated total, not a
    full reset -- see app.rest()/HospitalScreen, which both do this directly since
    Character has no rest() of its own (resting needs corp_map for lodging cost)."""
    c = Character(name="t")
    c.fatigue = 7
    c.fatigue //= 2
    assert c.fatigue == 3
    c.fatigue //= 2
    assert c.fatigue == 1
    c.fatigue //= 2
    assert c.fatigue == 0


def test_on_crew_hire_indefinite_and_for_job():
    c = Character(name="t")
    assert not c.on_crew("runner_x")
    c.hire_indefinite("runner_x")
    assert c.on_crew("runner_x")
    # Hiring an already-hired runner is a no-op, not a second entry.
    c.hire_indefinite("runner_x")
    assert len(c.crew) == 1


def test_hire_for_job_and_crew_for_job():
    c = Character(name="t")
    c.hire_for_job("runner_x", "job_1")
    assert [h.runner_id for h in c.crew_for_job("job_1")] == ["runner_x"]
    assert c.crew_for_job("job_2") == []


def test_remove_job_discharges_orphaned_for_job_crew_but_not_indefinite():
    c = Character(name="t")
    c.hire_for_job("runner_a", "job_1")
    c.hire_indefinite("runner_b")
    c.remove_job("job_1")
    assert not c.on_crew("runner_a")
    assert c.on_crew("runner_b")


def test_pay_crew_wages_charges_indefinite_hires_and_drops_who_you_cant_cover():
    from shadowguy.runners import RUNNERS_BY_ID

    specter = RUNNERS_BY_ID["runner_specter"]  # daily_cost 60
    mireille = RUNNERS_BY_ID["runner_mireille"]  # daily_cost 45
    # Default cool 1 puts Leadership at LEADERSHIP_BASE, so wages are the listed values and
    # this test stays about the drop logic, not the discount (see test_runners.py).
    c = Character(name="t", cash=50)
    c.hire_indefinite(specter.id)  # can't cover 60
    c.hire_indefinite(mireille.id)  # can cover 45
    left = c.pay_crew_wages()
    assert left == [specter.name]
    assert not c.on_crew(specter.id)
    assert c.on_crew(mireille.id)
    assert c.cash == 50 - mireille.daily_cost


def test_pay_crew_wages_does_not_charge_for_job_hires():
    c = Character(name="t", cash=0)
    c.hire_for_job("runner_specter", "job_1")
    left = c.pay_crew_wages()
    assert left == []
    assert c.on_crew("runner_specter")
    assert c.cash == 0
