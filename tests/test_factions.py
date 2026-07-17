"""Tests for factions.py: officer gating ladder and standing_shift's rival effect."""

import pytest

from shadowguy.factions import (
    CORP_OFFICER_TIERS,
    FACTIONS,
    RIVAL_WEIGHT,
    officer_gate,
    officer_unlocked,
    standing_shift,
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
