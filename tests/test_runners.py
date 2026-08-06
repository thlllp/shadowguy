"""Tests for runners.py: Leadership-scaled recruiting terms, the per-run random pick of
independent runners, and what a run's work does to them.

Leadership is the skill that governs how easy runners are to recruit (issue #33 — it
replaced the dead `seduction` skill). recruit_wage/recruit_cut bend a runner's listed
daily_cost/job_cut by the recruiter's skill_value("leadership"), mirroring how
shops._standing_discount bends prices.

The progression half (complete_job -> gain_experience/buy_gear) works on *copies*: the
roster tables are templates and a run mutates its own instances, so these tests build
their own RivalRunner or copy one rather than levelling a module constant out from under
every other test in the suite."""

import copy
import random

import pytest

from shadowguy.character import Character
from shadowguy.runners import (
    GEAR_LADDERS,
    LEADERSHIP_BASE,
    LEADERSHIP_TERMS_CAP,
    MAX_RATING,
    RANDOM_RUNNER_COUNT,
    RATING_XP_STEP,
    RIVAL_RUNNERS,
    RUNNER_POOL,
    RUNNERS_BY_ID,
    RivalRunner,
    best_weapon_id,
    buy_gear,
    can_work_support,
    complete_job,
    experience_for_next_rating,
    gain_experience,
    gear_defense,
    live_runner,
    next_purchase,
    recruit_cut,
    recruit_wage,
    select_active_runners,
    support_programs_for,
)
from shadowguy.shops import ITEMS_BY_ID
from shadowguy.skills import skill_value

RUNNER = RIVAL_RUNNERS[0]  # Specter: daily_cost 60, job_cut 0.25
SEEDS = range(150)


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
    wages = [recruit_wage(RUNNER, lead) for lead in range(20)]
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


@pytest.mark.parametrize("seed", SEEDS)
def test_select_active_runners_is_guaranteed_three_plus_a_random_six(seed):
    """Every run's roster is the fixed three plus RANDOM_RUNNER_COUNT distinct
    extras drawn from RUNNER_POOL -- no duplicates, no repeats of a guaranteed
    runner, and no id select_active_runners could hand back that RUNNERS_BY_ID
    can't resolve."""
    roster = select_active_runners(random.Random(seed))
    assert len(roster) == len(RIVAL_RUNNERS) + RANDOM_RUNNER_COUNT
    assert len(roster) == len(set(r.id for r in roster))
    assert all(r in roster for r in RIVAL_RUNNERS)
    extras = [r for r in roster if r not in RIVAL_RUNNERS]
    assert len(extras) == RANDOM_RUNNER_COUNT
    assert all(r in RUNNER_POOL for r in extras)
    assert all(r.id in RUNNERS_BY_ID for r in roster)


def test_select_active_runners_varies_between_seeds():
    """The whole point: which six extras show up isn't the same every run."""
    first = {r.id for r in select_active_runners(random.Random(1))}
    second = {r.id for r in select_active_runners(random.Random(2))}
    assert first != second


def test_runners_by_id_spans_the_whole_pool_not_just_one_runs_roster():
    """A saved CrewHire/JobOffer.taken_by id must resolve regardless of whether
    the run that made it happened to roll that pool runner in."""
    assert all(r.id in RUNNERS_BY_ID for r in RIVAL_RUNNERS)
    assert all(r.id in RUNNERS_BY_ID for r in RUNNER_POOL)


# --- a run's roster is its own: progress must not leak between runs ---


def test_a_runs_roster_is_copies_not_the_roster_tables_own_instances():
    """The guard the whole progression rests on. A runner levels up and buys gear in
    play, so a run that got RIVAL_RUNNERS itself would leave one run's rating sitting in
    a module constant for the next run started in the same process."""
    roster = select_active_runners(random.Random(0))
    assert all(runner is not RUNNERS_BY_ID[runner.id] for runner in roster)


def test_levelling_a_runs_runner_leaves_the_next_run_untouched():
    first = select_active_runners(random.Random(1))
    specter = next(r for r in first if r.id == "runner_specter")
    authored = RUNNERS_BY_ID["runner_specter"].rating
    gain_experience(specter, 10_000)
    assert specter.rating > authored
    second = select_active_runners(random.Random(2))
    assert next(r for r in second if r.id == "runner_specter").rating == authored


def test_live_runner_prefers_the_runs_roster_and_falls_back_to_the_template():
    roster = select_active_runners(random.Random(3))
    specter = next(r for r in roster if r.id == "runner_specter")
    gain_experience(specter, 10_000)
    assert live_runner("runner_specter", roster) is specter
    assert live_runner("runner_specter") is RUNNERS_BY_ID["runner_specter"]
    assert live_runner("runner_specter", roster).rating > live_runner("runner_specter").rating
    assert live_runner("nobody_at_all", roster) is None


# --- levelling up ---


def _runner(archetype="Solo", **kwargs) -> RivalRunner:
    return RivalRunner(
        id="runner_test",
        name="Test",
        archetype=archetype,
        description="",
        rating=kwargs.pop("rating", 5),
        daily_cost=30,
        job_cut=0.1,
        **kwargs,
    )


def test_experience_buys_a_rating_point_at_the_listed_price():
    runner = _runner(rating=5)
    cost = experience_for_next_rating(runner)
    assert cost == 5 * RATING_XP_STEP
    assert gain_experience(runner, cost - 1) == 0
    assert runner.rating == 5
    assert gain_experience(runner, 1) == 1
    assert runner.rating == 6


def test_the_next_point_costs_more_than_the_last():
    """Same escalating shape as Character.next_stat_cost: an elite runner improves
    slowly, so the roster doesn't converge on MAX_RATING."""
    costs = [experience_for_next_rating(_runner(rating=r)) for r in range(1, MAX_RATING)]
    assert costs == sorted(costs)
    assert len(set(costs)) == len(costs)


def test_rating_stops_at_the_cap_and_stops_charging_for_it():
    runner = _runner(rating=5)
    gain_experience(runner, 100_000)
    assert runner.rating == MAX_RATING
    assert experience_for_next_rating(runner) is None
    banked = runner.experience
    assert gain_experience(runner, 500) == 0
    assert runner.rating == MAX_RATING
    assert runner.experience == banked + 500  # still counted, just nothing left to buy


# --- buying equipment ---


def test_a_runner_saves_for_the_next_rung_instead_of_buying_down_the_list():
    """The ladder is an order, not a menu: a solo saving for the rifle doesn't spend the
    fund on the armor further down it."""
    runner = _runner("Solo")
    wanted = next_purchase(runner)
    assert wanted.id == GEAR_LADDERS["Solo"][0]
    runner.cash = wanted.price - 1
    assert buy_gear(runner) is None
    runner.cash += 1
    assert buy_gear(runner) is wanted
    assert runner.cash == 0
    assert runner.gear == [wanted.id]


def test_they_work_down_their_own_ladder_in_order():
    runner = _runner("Solo", cash=100_000)
    bought = []
    while (item := buy_gear(runner)) is not None:
        bought.append(item.id)
    assert bought == list(GEAR_LADDERS["Solo"])
    assert next_purchase(runner) is None


def test_a_bought_deck_replaces_the_old_one_and_never_downgrades():
    """Decks are measured, not merely owned (runners._wants): Specter is authored on the
    best deck in the catalog and must not be walked back down to a Burner."""
    specter = copy.deepcopy(RUNNERS_BY_ID["runner_specter"])
    assert specter.deck_id == "zetatech_rig"
    specter.cash = 100_000
    while buy_gear(specter) is not None:
        pass
    assert specter.deck_id == "zetatech_rig"

    null = copy.deepcopy(RUNNERS_BY_ID["runner_null"])
    assert null.deck_id == "burner_deck"
    null.cash = ITEMS_BY_ID["cracked_cyberdeck"].price
    assert buy_gear(null).id == "cracked_cyberdeck"
    assert null.deck_id == "cracked_cyberdeck"
    assert "cracked_cyberdeck" not in null.gear  # a deck isn't worn gear


def test_buying_a_deck_is_what_makes_a_runner_employable_as_support():
    """The one purchase that changes what someone can *do*: support is gated on owning a
    deck (can_work_support), and program capacity comes off the deck they own."""
    runner = _runner("Netrunner", rating=9)
    assert not can_work_support(runner)
    assert support_programs_for(runner) == ()
    runner.cash = ITEMS_BY_ID["burner_deck"].price
    assert buy_gear(runner).id == "burner_deck"
    assert can_work_support(runner)
    before = len(support_programs_for(runner))
    runner.cash = ITEMS_BY_ID["cracked_cyberdeck"].price
    buy_gear(runner)
    assert len(support_programs_for(runner)) > before


def test_bought_gear_is_readable_as_a_weapon_and_an_armor_number():
    """What combat.crew_stats consumes: one weapon (the hardest-hitting they own) and one
    defense number (the best, not the sum)."""
    runner = _runner("Solo")
    assert best_weapon_id(runner) is None and gear_defense(runner) == 0
    runner.gear = ["kevlar_vest", "assault_rifle", "hardsuit"]
    assert best_weapon_id(runner) == "assault_rifle"
    assert gear_defense(runner) == ITEMS_BY_ID["hardsuit"].defense


def test_complete_job_pays_them_teaches_them_and_kits_them_out():
    runner = _runner("Solo", rating=5)
    price = ITEMS_BY_ID[GEAR_LADDERS["Solo"][0]].price
    assert complete_job(runner, pay=price - 1, experience=1) is None
    assert runner.cash == price - 1 and runner.experience == 1
    bought = complete_job(runner, pay=1, experience=5 * RATING_XP_STEP)
    assert bought.id == GEAR_LADDERS["Solo"][0]
    assert runner.cash == 0
    assert runner.rating == 6


def test_every_ladder_item_is_a_real_catalog_item():
    """The import-time guard, restated as a test so the reason it exists is written down:
    a typo'd id would leave that runner silently saving forever."""
    for ladder in GEAR_LADDERS.values():
        assert all(item_id in ITEMS_BY_ID for item_id in ladder)
