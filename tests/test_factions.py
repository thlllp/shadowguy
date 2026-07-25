"""Tests for factions.py: the officer gating ladder, the corp-takeover gate a
runner has to clear inside an HQ, and standing_shift's rival effect."""

import pytest

from shadowguy.factions import (
    CORP_OFFICER_TIERS,
    FACTIONS,
    RIVAL_WEIGHT,
    TAKEOVER_COST,
    TAKEOVER_MIN_REP,
    TAKEOVER_MIN_STANDING,
    can_take_over,
    officer_gate,
    officer_unlocked,
    standing_shift,
    takeover_gate,
)


def test_receptionist_has_no_standing_floor_but_does_gate_on_rep():
    assert officer_unlocked(rep=0, standing=-100, role="receptionist")
    assert not officer_unlocked(rep=-1, standing=100, role="receptionist")


@pytest.mark.parametrize("role,min_rep,min_standing", CORP_OFFICER_TIERS)
def test_officer_unlocked_requires_both_rep_and_standing_thresholds(role, min_rep, min_standing):
    if min_standing is None:
        assert officer_unlocked(min_rep, -9999, role)
        assert not officer_unlocked(min_rep - 1, 9999, role)
        return
    assert officer_unlocked(min_rep, min_standing, role)
    assert not officer_unlocked(min_rep - 1, min_standing, role)
    assert not officer_unlocked(min_rep, min_standing - 1, role)


def test_officer_gate_describes_the_requirement():
    assert "rep 0" in officer_gate("receptionist")
    assert "standing" not in officer_gate("receptionist")
    text = officer_gate("executive")
    assert "rep 12" in text
    assert "standing +8" in text


def test_standing_shift_moves_target_by_full_delta():
    target = FACTIONS[0].id
    shift = standing_shift(target, 10)
    assert shift[target] == 10


def test_standing_shift_moves_rivals_the_opposite_way_at_half_weight():
    target = FACTIONS[0].id
    shift = standing_shift(target, 10)
    for faction in FACTIONS[1:]:
        assert shift[faction.id] == -10 // RIVAL_WEIGHT


def test_standing_shift_covers_every_faction():
    target = FACTIONS[0].id
    shift = standing_shift(target, 5)
    assert set(shift) == {f.id for f in FACTIONS}


# The takeover gate: what it costs a runner to be handed a corp from inside its HQ.


def _at_gate(**overrides):
    """Exactly-qualifying arguments, with named fields knocked below the bar."""
    args = {
        "rep": TAKEOVER_MIN_REP,
        "standing": TAKEOVER_MIN_STANDING,
        "cash": TAKEOVER_COST,
    }
    return {**args, **overrides}


def test_exactly_meeting_every_requirement_qualifies():
    """The bar is >=, not >: a runner who has saved precisely TAKEOVER_COST can buy."""
    assert can_take_over(**_at_gate())


@pytest.mark.parametrize("field", ["rep", "standing", "cash"])
def test_falling_one_short_on_any_single_requirement_disqualifies(field):
    """All three gates are required -- no amount of one covers a shortfall in another,
    so a runner who is rich but unknown (or famous but broke) is still turned away."""
    short = _at_gate(**{field: _at_gate()[field] - 1})
    assert not can_take_over(**short)


def test_the_takeover_bar_sits_above_the_executive_tier_that_opens_the_conversation():
    """Reaching the exec suite buys the meeting, not the company -- otherwise the
    takeover would unlock the instant the last officer did."""
    _role, exec_rep, exec_standing = CORP_OFFICER_TIERS[-1]
    assert TAKEOVER_MIN_REP > exec_rep
    assert TAKEOVER_MIN_STANDING > exec_standing


def test_gate_text_names_only_what_is_still_missing():
    text = takeover_gate(**_at_gate(cash=0))
    assert "eb" in text
    assert "rep" not in text and "standing" not in text


def test_gate_text_is_empty_once_nothing_is_missing():
    """Empty means qualified -- the label switches to the buy line at that point."""
    assert takeover_gate(**_at_gate()) == ""


def test_gate_text_reports_the_runner_s_own_numbers_back():
    text = takeover_gate(rep=3, standing=-2, cash=17)
    assert "you have 3" in text
    assert "you have -2" in text
    assert "you have 17" in text
