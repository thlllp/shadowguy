"""Biased random.Random subclasses shared by tests that need to force a specific
check or probability outcome instead of seeding and hoping."""

import random

from shadowguy.character import Character


class AlwaysSix(random.Random):
    def randint(self, a, b):
        return 6


class AlwaysOne(random.Random):
    def randint(self, a, b):
        return 1


class ForcedChance(random.Random):
    """A Random whose random() always returns a fixed value; randint()/choice() still
    work normally, so the fixed value forces a probability roll to hit or miss on demand."""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._value = value

    def random(self) -> float:
        return self._value


def character_with_skill_value(skill_id: str, value: int) -> Character:
    """A fresh Character with the given skill forced to an exact skill_value, by
    zeroing its rank and setting perception/intelligence directly (bypasses spend_*,
    fine for a resolution test that only cares about the resulting pool size)."""
    character = Character(name="t")
    character.skill_ranks[skill_id] = 0
    character.perception = value
    character.intelligence = value
    return character
