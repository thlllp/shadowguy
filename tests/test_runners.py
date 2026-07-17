"""Tests for runners.py: Leadership-scaled recruiting terms.

Leadership is the skill that governs how easy runners are to recruit (issue #33 — it
replaced the dead `seduction` skill). recruit_wage/recruit_cut bend a runner's listed
daily_cost/job_cut by the recruiter's skill_value("leadership"), mirroring how
shops._standing_discount bends prices."""

from shadowguy.character import Character
from shadowguy.runners import (
    LEADERSHIP_BASE,
    LEADERSHIP_TERMS_CAP,
    RIVAL_RUNNERS,
    recruit_cut,
    recruit_wage,
)
from shadowguy.skills import skill_value

RUNNER = RIVAL_RUNNERS[0]  # Specter: daily_cost 60, job_cut 0.25


def test_terms_at_base_are_the_listed_values():
    assert recruit_wage(RUNNER, LEADERSHIP_BASE) == RUNNER.daily_cost
    assert recruit_cut(RUNNER, LEADERSHIP_BASE) == RUNNER.job_cut


def test_higher_leadership_is_cheaper():
    """Above base: a discount on both the wage and the cut."""
    assert recruit_wage(RUNNER, LEADERSHIP_BASE + 4) < RUNNER.daily_cost
    assert recruit_cut(RUNNER, LEADERSHIP_BASE + 4) < RUNNER.job_cut


def test_no_leadership_is_never_a_markup():
    """A recruiter with no Leadership pays the listed terms, never more — runners are
    looking for work too. Even a nominally sub-base value can't produce a markup."""
    assert recruit_wage(RUNNER, LEADERSHIP_BASE) == RUNNER.daily_cost
    assert recruit_wage(RUNNER, 0) == RUNNER.daily_cost
    assert recruit_cut(RUNNER, 0) == RUNNER.job_cut


def test_wage_is_monotonic_in_leadership():
    wages = [recruit_wage(RUNNER, lead) for lead in range(0, 20)]
    assert wages == sorted(wages, reverse=True)


def test_discount_is_capped():
    """Beyond the cap the discount stops growing, like shops.STANDING_PRICE_CAP."""
    floor_wage = round(RUNNER.daily_cost * (1 - LEADERSHIP_TERMS_CAP))
    assert recruit_wage(RUNNER, 100) == floor_wage


def test_leadership_is_a_real_skill_a_character_can_buy():
    """The point of issue #33: a point in leadership now has a live effect."""
    c = Character(name="t", cool=4)
    c.spend_skill_point("leadership")
    lead = skill_value(c, "leadership")
    assert recruit_wage(RUNNER, lead) < recruit_wage(RUNNER, 0)


def test_wages_scale_when_charged_on_a_crew():
    """A high-Leadership recruiter is charged less on payroll than a low-Leadership one."""
    strong = Character(name="lead", cool=6, cash=1000)
    for _ in range(8):
        strong.spend_skill_point("leadership")
    weak = Character(name="grunt", cool=1, cash=1000)
    for c in (strong, weak):
        c.hire_indefinite(RUNNER.id)
        c.pay_crew_wages()
    assert strong.cash > weak.cash
