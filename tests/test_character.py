"""Tests for character.py: stat/health math, rank costs/caps, rep floor, relationships."""

import pytest

from shadowguy.character import (
    BASE_HEALTH,
    CORE_STATS,
    HEALTH_PER_BODY,
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


def test_starting_health_and_stamina_default_to_max():
    c = Character(name="t")
    assert c.health == c.max_health
    assert c.stamina == c.max_stamina


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


def test_rest_advances_day_refills_stamina_and_clears_daily_flags():
    c = Character(name="t")
    c.spend_stamina(c.max_stamina)
    c.free_travel_used = 2
    c.health_kit_used_today = True
    c.temp_bonuses["strength"] = 3
    day_before = c.day
    c.rest()
    assert c.day == day_before + 1
    assert c.stamina == c.max_stamina
    assert c.free_travel_used == 0
    assert c.health_kit_used_today is False
    assert c.temp_bonuses == {}


def test_rest_does_not_heal():
    c = Character(name="t")
    c.adjust_health(-5)
    hurt = c.health
    c.rest()
    assert c.health == hurt


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


def test_can_afford_and_spend_stamina():
    c = Character(name="t")
    assert c.can_afford(c.max_stamina)
    assert not c.can_afford(c.max_stamina + 1)
    c.spend_stamina(2)
    assert c.stamina == c.max_stamina - 2


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
