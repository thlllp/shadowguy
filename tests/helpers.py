"""Shared test fixtures: biased random.Random subclasses for tests that need to force a
specific check or probability outcome instead of seeding and hoping, plus a couple of
Character/roster builders several suites want."""

import random

from shadowguy.character import Character
from shadowguy.combat import Enemy, crew_stats
from shadowguy.runners import RIVAL_RUNNERS
from shadowguy.shops import Item, Slot


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
    zeroing its rank and setting perception/logic directly (bypasses spend_*,
    fine for a resolution test that only cares about the resulting pool size)."""
    character = Character(name="t")
    character.skill_ranks[skill_id] = 0
    character.perception = value
    character.logic = value
    return character


def npc_weapon(skill: str, damage: int = 0, stun_damage: int = 0) -> Item:
    """A one-off weapon for a synthetic Enemy. Built by hand rather than pulled from
    shops.CATALOG because a test usually wants a damage figure the catalog's
    MIN_WEAPON_DAMAGE floor forbids -- the same reason combat.NPC_WEAPONS is hand-built.
    """
    return Item(
        id=f"test_{skill}_{damage}_{stun_damage}",
        name="Test Weapon",
        price=0,
        bonuses={},
        slot=Slot.WEAPON,
        skill=skill,
        damage=damage,
        stun_damage=stun_damage,
        concealment=1,
    )


def synthetic_enemy(weapon: Item, *, body: int = 1, agility: int = 1, strength: int = 1,
                    ranks: dict[str, int] | None = None, armor: int = 0,
                    id: str = "test_enemy", name: str = "Test Enemy") -> Enemy:
    """An Enemy built for one test's arithmetic rather than picked off the roster.

    Everything a fight reads is derived (see combat.Enemy), so a test pins a *derived*
    number by choosing the stat behind it: `health` is body*HEALTH_PER_BODY, `toughness`
    is body+armor (so the two move together -- there is no zero-soak punching bag any
    more), `defense` is DEFENSE_BASE+agility+dodge rank, and `attack` is the weapon
    skill's stat plus its rank.
    """
    return Enemy(
        id=id, name=name, body=body, strength=strength, agility=agility,
        perception=1, logic=1, cool=1, weapon=weapon, ranks=ranks or {}, armor=armor,
    )


def crew_stats_for(archetype: str = "Solo"):
    """The combat stat block for the roster's runner of this archetype -- the Solo by
    default, being the one hire built to shoot people (most health, a gun's reach)."""
    return crew_stats(next(runner for runner in RIVAL_RUNNERS if runner.archetype == archetype))
