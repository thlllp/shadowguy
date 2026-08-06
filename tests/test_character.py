"""Tests for character.py: stat/health math, rank costs/caps, rep floor, relationships."""

import pytest

from shadowguy.shops import ITEMS_BY_ID, STOCK_MOD_IDS, WEAPON_MOD_SLOTS, InventoryItem
from shadowguy.character import (
    BASE_HEALTH,
    CORE_STATS,
    GEAR_EB_PER_POINT,
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


def test_adjust_stun_floors_at_zero():
    c = Character(name="t")
    c.adjust_stun(5)
    assert c.stun == 5
    c.adjust_stun(-10_000)
    assert c.stun == 0


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


def test_mark_rested_halves_fatigue_instead_of_clearing_it():
    """A burnout sticks a little: mark_rested() (called by app.rest() and a hospital
    stay alike) only halves the accumulated total, not a full reset."""
    c = Character(name="t")
    c.fatigue = 7
    c.mark_rested()
    assert c.fatigue == 3
    c.mark_rested()
    assert c.fatigue == 1
    c.mark_rested()
    assert c.fatigue == 0


def test_mark_rested_resets_last_rest_hour():
    c = Character(name="t")
    c.elapsed_hours = 40
    c.mark_rested()
    assert c.last_rest_hour == 40


def test_mark_rested_clears_stun_completely_unlike_fatigue():
    """Unlike fatigue's halving, stun is meant to be a real meter but not an
    extremely punishing one -- one rest walks it off entirely."""
    c = Character(name="t")
    c.stun = 12
    c.mark_rested()
    assert c.stun == 0


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


def test_crew_working_takes_this_job_s_hires_plus_everyone_on_retainer():
    """Who walks into the fight, as opposed to crew_for_job's "who takes a cut": an
    indefinite hire is on retainer and comes to all of it, someone else's job doesn't."""
    c = Character(name="t")
    c.hire_for_job("runner_a", "job_1")
    c.hire_for_job("runner_b", "job_2")
    c.hire_indefinite("runner_c")
    assert {h.runner_id for h in c.crew_working("job_1")} == {"runner_a", "runner_c"}
    assert {h.runner_id for h in c.crew_working("job_2")} == {"runner_b", "runner_c"}
    assert [h.runner_id for h in c.crew_for_job("job_1")] == ["runner_a"]


def test_crew_on_site_leaves_remote_support_off_the_map():
    """`on_site` was write-only until crew_on_site existed: set at hire time, capped per
    archetype, saved -- and then ignored when the fight opened, so a hire taken on as
    remote support turned up at the location as a body holding a gun. They're still on
    the job (crew_working) and still take a cut; they're just not standing there."""
    c = Character(name="t")
    c.accepted_jobs.append(_job("job_1", max_on_site=2, max_support=1))
    # Real roster ids: support is role-gated (only a Netrunner can work it), so a test
    # double can't fill the support slot any more.
    assert c.hire_for_job("runner_juncture", "job_1", on_site=True)  # Solo
    assert c.hire_for_job("runner_specter", "job_1", on_site=False)  # Netrunner

    assert {h.runner_id for h in c.crew_working("job_1")} == {"runner_juncture", "runner_specter"}
    assert [h.runner_id for h in c.crew_on_site("job_1")] == ["runner_juncture"]
    assert [h.runner_id for h in c.crew_support("job_1")] == ["runner_specter"]


def test_only_a_support_capable_archetype_can_take_the_support_slot():
    """Support is a role, not just a placement -- a solo standing across the street
    contributes nothing, so the posture is refused rather than sold. On-site is
    unaffected: everyone can stand in a room."""
    c = Character(name="t")
    c.accepted_jobs.append(_job("job_1", max_on_site=3, max_support=2))
    assert not c.hire_for_job("runner_juncture", "job_1", on_site=False)  # Solo
    assert not c.hire_for_job("runner_mireille", "job_1", on_site=False)  # Infiltrator
    assert c.hire_for_job("runner_juncture", "job_1", on_site=True)
    assert c.crew_support("job_1") == []


def test_crew_on_site_keeps_retained_hires_who_default_to_being_there():
    """An indefinite hire is on retainer with on_site defaulting True, so the new filter
    must not quietly empty the map for anyone who never chose a posture."""
    c = Character(name="t")
    c.hire_indefinite("runner_c")
    assert [h.runner_id for h in c.crew_on_site("job_1")] == ["runner_c"]


def test_record_runner_killed_takes_them_off_the_crew_and_the_roster_for_good():
    c = Character(name="t")
    c.hire_indefinite("runner_x")
    c.record_runner_killed("runner_x")
    assert not c.on_crew("runner_x")
    assert not c.runner_available("runner_x")
    c.elapsed_hours += HOURS_PER_DAY * 100  # no amount of time brings them back
    assert not c.runner_available("runner_x")


def test_an_arrested_runner_comes_back_once_their_day_arrives():
    c = Character(name="t")
    c.hire_for_job("runner_x", "job_1")
    c.record_runner_arrested("runner_x", days=3)
    assert not c.on_crew("runner_x")
    assert not c.runner_available("runner_x")

    c.elapsed_hours += HOURS_PER_DAY * 2
    assert not c.runner_available("runner_x")  # still inside
    c.elapsed_hours += HOURS_PER_DAY
    assert c.runner_available("runner_x")
    assert "runner_x" not in c.arrested_runners  # released, and the ledger swept itself


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


def _job(job_id="job_1", max_on_site=None, max_support=None):
    """The smallest accepted JobOffer that carries a roster cap -- see test_rivals.py's
    own _scene for the same minimal-Scene shape."""
    from shadowguy.fixer import JobOffer
    from shadowguy.jobs import JobTiming
    from shadowguy.scene import Scene, Stage

    scene = Scene(
        id=job_id,
        title="Job",
        stages={"start": Stage(id="start", prompt="p", choices=[])},
        max_on_site=max_on_site,
        max_support=max_support,
    )
    return JobOffer(id=f"offer_{job_id}", fixer_id="fx", scene=scene, timing=JobTiming(), offered_day=1)


def test_hire_for_job_uncapped_when_job_unknown_or_archetype_has_no_cap():
    c = Character(name="t")
    # job_id not among accepted_jobs at all -- treated as uncapped, matching the tests
    # elsewhere in this file that hire against a bare job_id string.
    assert c.hire_for_job("runner_a", "job_1")
    c.accepted_jobs.append(_job("job_2"))  # max_on_site/max_support both None
    assert c.hire_for_job("runner_b", "job_2", on_site=True)
    # Real Netrunner id: the support posture is role-gated even where the cap isn't.
    assert c.hire_for_job("runner_specter", "job_2", on_site=False)


def test_hire_for_job_respects_burglary_style_cap_one_on_site_one_support():
    c = Character(name="t")
    c.accepted_jobs.append(_job("job_1", max_on_site=1, max_support=1))
    # max_on_site=1 counts the player, so no additional on-site hire fits.
    assert not c.hire_for_job("runner_a", "job_1", on_site=True)
    assert not c.on_crew("runner_a")
    # One support hire fits; a second does not. Both are Netrunners, so what stops the
    # second is the cap rather than the role gate.
    assert c.hire_for_job("runner_specter", "job_1", on_site=False)
    assert not c.hire_for_job("runner_null", "job_1", on_site=False)
    assert c.on_crew("runner_specter")
    assert not c.on_crew("runner_null")


def test_hire_for_job_respects_data_heist_style_cap_solo_no_crew_at_all():
    c = Character(name="t")
    c.accepted_jobs.append(_job("job_1", max_on_site=1, max_support=0))
    assert not c.job_roster_has_room("job_1", on_site=True)
    assert not c.job_roster_has_room("job_1", on_site=False)
    assert not c.hire_for_job("runner_a", "job_1", on_site=True)
    assert not c.hire_for_job("runner_a", "job_1", on_site=False)
    assert c.crew_for_job("job_1") == []


def test_hire_for_job_respects_wetwork_style_cap_three_on_site_one_support():
    c = Character(name="t")
    c.accepted_jobs.append(_job("job_1", max_on_site=3, max_support=1))
    # Player fills one on-site slot, leaving room for two hired on-site runners.
    assert c.hire_for_job("runner_a", "job_1", on_site=True)
    assert c.hire_for_job("runner_b", "job_1", on_site=True)
    assert not c.hire_for_job("runner_c", "job_1", on_site=True)
    # Netrunner ids for the support slot -- see the role gate in hire_for_job.
    assert c.hire_for_job("runner_specter", "job_1", on_site=False)
    assert not c.hire_for_job("runner_null", "job_1", on_site=False)
    assert {h.runner_id for h in c.crew_for_job("job_1")} == {"runner_a", "runner_b", "runner_specter"}


# --- creation gear: skill points traded for the kit you walk in with ---


def test_converting_a_skill_point_buys_gear_budget_not_cash():
    c = Character(name="t")
    cash, points = c.cash, c.skill_points
    assert c.convert_skill_point_to_gear()
    assert c.skill_points == points - 1
    assert c.gear_budget == GEAR_EB_PER_POINT
    assert c.cash == cash  # budget is not, and never becomes, money


def test_the_whole_skill_pool_can_go_to_gear_and_then_nothing_is_left_to_convert():
    """Deliberately uncapped -- the trade caps itself, since gear with no ranks behind it
    swings at skill_value 2 and misses nearly everything."""
    c = Character(name="t")
    converted = 0
    while c.convert_skill_point_to_gear():
        converted += 1
    assert converted == STARTING_SKILL_POINTS
    assert c.gear_budget == STARTING_SKILL_POINTS * GEAR_EB_PER_POINT
    assert not c.convert_skill_point_to_gear()


def test_unspent_gear_budget_is_written_off_rather_than_banked_as_cash():
    """The rule that makes this a gear purchase instead of a way to print starting money."""
    c = Character(name="t")
    c.convert_skill_point_to_gear()
    cash = c.cash
    assert c.discard_gear_budget() == GEAR_EB_PER_POINT
    assert c.gear_budget == 0
    assert c.cash == cash
    assert c.discard_gear_budget() == 0  # idempotent


def test_buying_creation_gear_spends_budget_and_equips_what_fits():
    c = Character(name="t")
    c.convert_skill_point_to_gear()
    vest = ITEMS_BY_ID["kevlar_vest"]
    assert c.buy_creation_gear(vest)
    assert c.gear_budget == GEAR_EB_PER_POINT - vest.price
    assert c.creation_gear == ["kevlar_vest"]
    assert [e.item_id for e in c.inventory] == ["kevlar_vest"]
    assert c.inventory[0].equipped  # the torso slot was free


def test_buying_creation_gear_seeds_a_pistols_named_mod_slots_with_stock_parts():
    """Regression: buy_creation_gear used to build InventoryItem directly, bypassing
    shops.buy_item's stock-mod seeding -- a pistol picked as starting gear (several
    archetypes grant pipe_pistol) would end up with an empty mods list, and the first
    workshop mod install on it would IndexError against install_mod's positional
    named-slot lookup."""
    c = Character(name="t")
    c.convert_skill_point_to_gear()
    pistol = ITEMS_BY_ID["pipe_pistol"]
    assert c.buy_creation_gear(pistol)
    assert c.inventory[0].mods == [
        STOCK_MOD_IDS[slot_type] for slot_type in WEAPON_MOD_SLOTS["pistols"]
    ]


def test_creation_gear_refuses_what_the_budget_cannot_cover_or_standing_gates():
    c = Character(name="t")
    assert not c.buy_creation_gear(ITEMS_BY_ID["kevlar_vest"])  # no budget at all yet
    c.convert_skill_point_to_gear()
    gated = next(i for i in ITEMS_BY_ID.values() if i.min_standing)
    assert not c.buy_creation_gear(gated)  # nobody to have standing with pre-run
    assert c.creation_gear == []


def test_refunding_creation_gear_returns_the_full_price():
    c = Character(name="t")
    c.convert_skill_point_to_gear()
    c.buy_creation_gear(ITEMS_BY_ID["kevlar_vest"])
    assert c.refund_creation_gear("kevlar_vest")
    assert c.gear_budget == GEAR_EB_PER_POINT
    assert c.creation_gear == [] and c.inventory == []
    assert not c.refund_creation_gear("kevlar_vest")  # nothing left to give back


def test_refund_only_reaches_gear_bought_at_creation():
    """It must never become a way to sell starting kit at full price mid-run."""
    c = Character(name="t")
    c.inventory.append(InventoryItem("kevlar_vest"))
    assert not c.refund_creation_gear("kevlar_vest")
    assert len(c.inventory) == 1


def test_reset_build_takes_back_creation_gear_and_the_points_that_paid_for_it():
    c = Character(name="t")
    c.convert_skill_point_to_gear()
    c.buy_creation_gear(ITEMS_BY_ID["kevlar_vest"])
    c.reset_build()
    assert c.skill_points == STARTING_SKILL_POINTS
    assert c.gear_budget == 0
    assert c.creation_gear == []
    assert c.inventory == []
