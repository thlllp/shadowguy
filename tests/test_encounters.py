"""Tests for encounters.py: gang turf toll/attack resolution.

roll_gang_encounter is gated on a flat chance, so the roll is pinned with a
random.Random subclass whose random() is fixed (the AlwaysSix trick from
tests/test_checks.py, applied to the [0, 1) chance roll instead of a die)."""

import random
from types import SimpleNamespace

from shadowguy.character import Character
from shadowguy.encounters import (
    ATTACK_STANDING,
    GANG_ENCOUNTER_CHANCE,
    gang_attack,
    roll_gang_encounter,
    toll_for,
)
from shadowguy.gangs import GANGS

GANG_ID = GANGS[0].id


class ForcedChance(random.Random):
    """A Random whose random() always returns `value`; randint/choice still work (for the
    enemy roll), so a fixed value forces the encounter chance to hit or miss on demand."""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._value = value

    def random(self) -> float:
        return self._value


def _territory(gang_id):
    return SimpleNamespace(gang_id=gang_id)


HIT = ForcedChance(0.0)  # 0.0 < chance -> always triggers
MISS = ForcedChance(0.99)  # 0.99 >= chance -> never triggers


def test_toll_escalates_by_band():
    assert [toll_for(s) for s in (-1, -2, -3, -4)] == [40, 70, 100, 130]


def test_no_encounter_when_standing_is_non_negative():
    c = Character(name="t")  # gang_standing defaults to 0
    assert roll_gang_encounter(c, _territory(GANG_ID), HIT) is None


def test_no_encounter_on_ungoverned_turf():
    c = Character(name="t")
    c.adjust_gang_standing(GANG_ID, -3)
    assert roll_gang_encounter(c, _territory(None), HIT) is None


def test_chance_miss_yields_no_encounter():
    c = Character(name="t")
    c.adjust_gang_standing(GANG_ID, -3)
    assert roll_gang_encounter(c, _territory(GANG_ID), MISS) is None


def test_minor_negative_is_a_toll():
    c = Character(name="t")
    c.adjust_gang_standing(GANG_ID, -2)
    enc = roll_gang_encounter(c, _territory(GANG_ID), HIT)
    assert enc is not None
    assert enc.gang.id == GANG_ID
    assert enc.toll == toll_for(-2) == 70


def test_worst_toll_band_is_minus_four():
    c = Character(name="t")
    c.adjust_gang_standing(GANG_ID, ATTACK_STANDING + 1)  # -4
    enc = roll_gang_encounter(c, _territory(GANG_ID), HIT)
    assert enc.toll == toll_for(-4)


def test_attack_band_has_no_toll():
    for standing in (ATTACK_STANDING, ATTACK_STANDING - 1):  # -5, -6
        c = Character(name="t")
        c.adjust_gang_standing(GANG_ID, standing)
        enc = roll_gang_encounter(c, _territory(GANG_ID), HIT)
        assert enc is not None
        assert enc.toll is None


def test_gang_attack_builds_a_real_fight():
    enc = gang_attack(GANGS[0], random.Random(1))
    assert enc.enemies  # non-empty squad
    assert enc.victory.text and enc.escape.text


def test_chance_boundary_is_strict_less_than():
    """random() == chance must miss (>=), so the 0.25 constant means 'under a quarter'."""
    c = Character(name="t")
    c.adjust_gang_standing(GANG_ID, -1)
    assert roll_gang_encounter(c, _territory(GANG_ID), ForcedChance(GANG_ENCOUNTER_CHANCE)) is None
