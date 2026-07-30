"""Tests for combat.py: drop_for_result, attack_verbs, resolve_hit, defense and soak."""

import random


from shadowguy.character import Character
from shadowguy.checks import CheckResult
from shadowguy.combat import (
    SMARTLINK_ATTACK_BONUS,
    _DEFAULT_ATTACK_VERBS,
    Drop,
    UNARMED,
    attack_verbs,
    drop_for_result,
    player_soak,
    resolve_hit,
    melee_damage_bonus,
    smartlink_bonus,
)
from shadowguy.cybernetics import CyberSlot, install_cyberware
from shadowguy.shops import ITEMS_BY_ID, Slot


# --- drop_for_result ---


def test_drop_for_result_none_when_no_check_routed_you_in():
    assert drop_for_result(None) is Drop.NONE


def test_drop_for_result_player_on_any_passing_result():
    assert drop_for_result(CheckResult.SUCCESS) is Drop.PLAYER
    assert drop_for_result(CheckResult.CRITICAL_SUCCESS) is Drop.PLAYER


def test_drop_for_result_enemy_only_on_critical_failure():
    assert drop_for_result(CheckResult.CRITICAL_FAILURE) is Drop.ENEMY


def test_drop_for_result_none_on_plain_failure():
    assert drop_for_result(CheckResult.FAILURE) is Drop.NONE


# --- attack_verbs ---


def test_attack_verbs_cover_every_weapon_skill_in_the_catalog():
    """Including UNARMED's grapple -- an uncovered skill silently falls back to the
    generic pair, which is the failure mode this table exists to avoid."""
    weapons = [item for item in ITEMS_BY_ID.values() if item.slot is Slot.WEAPON]
    for weapon in (*weapons, UNARMED):
        assert attack_verbs(weapon) is not _DEFAULT_ATTACK_VERBS, weapon.skill


def test_attack_verbs_read_differently_for_every_weapon_category():
    by_skill = {
        item.skill: attack_verbs(item)
        for item in ITEMS_BY_ID.values()
        if item.slot is Slot.WEAPON
    }
    assert by_skill["pistols"] == ("fire on", "shoot")
    # No two categories read alike, which is the whole point -- a knife shouldn't
    # "fire", a bow shouldn't read like a shotgun, and knuckles shouldn't read like
    # either. Every stocked category is covered, so this compares all of them.
    assert len(set(by_skill.values())) == len(by_skill)


def test_attack_verbs_fall_back_for_an_unknown_skill():
    made_up = ITEMS_BY_ID["combat_knife"].__class__(
        id="x", name="X", price=0, bonuses={}, slot=Slot.WEAPON, skill="not_a_skill", damage=1
    )
    assert attack_verbs(made_up) == _DEFAULT_ATTACK_VERBS


# --- resolve_hit ---


def test_resolve_hit_miss_deals_zero_damage():
    class AlwaysOne(random.Random):
        def randint(self, a, b):
            return 1

    roll, damage = resolve_hit(AlwaysOne(), attacker_stat_value=1, attacker_advantage=0,
                                to_hit_difficulty=21, base_damage=10, soak_pool=0)
    assert not roll.result.passed
    assert damage == 0


def test_resolve_hit_full_soak_reduces_damage_to_zero():
    class AlwaysSix(random.Random):
        def randint(self, a, b):
            return 6

    # attacker pool 1 (difficulty 9 -> 0 opposing dice) hits for margin 1, so total
    # damage before soak is base_damage(1) + margin(1) = 2; a 5-die soak (5 successes
    # with AlwaysSix) swallows that whole, floored at 0.
    roll, damage = resolve_hit(AlwaysSix(), attacker_stat_value=1, attacker_advantage=0,
                                to_hit_difficulty=9, base_damage=1, soak_pool=5)
    assert roll.result.passed
    assert damage == 0


def test_resolve_hit_landed_hit_adds_margin_to_base_damage_before_soak():
    class AlwaysSix(random.Random):
        def randint(self, a, b):
            return 6

    roll, damage = resolve_hit(AlwaysSix(), attacker_stat_value=5, attacker_advantage=0,
                                to_hit_difficulty=9, base_damage=3, soak_pool=0)
    assert roll.result.passed
    assert damage == 3 + roll.margin


# --- melee_damage_bonus ---


def test_melee_damage_bonus_is_full_strength_on_a_melee_weapon():
    character = Character(name="t", strength=6)
    for weapon_id in ("mono_katana", "brass_knuckles", "combat_knife"):
        assert melee_damage_bonus(character, ITEMS_BY_ID[weapon_id]) == 6


def test_melee_damage_bonus_applies_to_bare_hands_and_to_a_thrown_weapon():
    """Grapple and Throwing both put your body behind the hit -- neither is a
    RANGED_SKILLS weapon, which is the whole test."""
    character = Character(name="t", strength=4)
    assert melee_damage_bonus(character, UNARMED) == 4
    assert melee_damage_bonus(character, ITEMS_BY_ID["throwing_knives"]) == 4


def test_melee_damage_bonus_is_zero_on_anything_that_shoots():
    character = Character(name="t", strength=9)
    for weapon_id in ("pipe_pistol", "machine_pistol", "pump_shotgun", "compound_bow"):
        assert melee_damage_bonus(character, ITEMS_BY_ID[weapon_id]) == 0


def test_melee_damage_bonus_reads_the_gear_included_stat_not_the_raw_field():
    """character.stat() folds gear/cyberware/chems/fatigue in, so a Strength buff has
    to reach the swing -- the same number every other check sees."""
    character = Character(name="t", strength=3)
    character.temp_bonuses["strength"] = 2
    assert melee_damage_bonus(character, ITEMS_BY_ID["mono_katana"]) == 5


# --- smartlink_bonus ---


def test_smartlink_bonus_zero_with_no_implant():
    character = Character(name="t")
    pistol = ITEMS_BY_ID["pipe_pistol"]
    assert smartlink_bonus(character, pistol) == 0


def test_smartlink_bonus_zero_on_an_unlinked_weapon():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, "smartlink")
    assert smartlink_bonus(character, UNARMED) == 0


def test_smartlink_bonus_applies_with_implant_and_a_smartlinked_weapon():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, "smartlink")
    pistol = ITEMS_BY_ID["pipe_pistol"]
    assert pistol.smartlinked is True
    assert smartlink_bonus(character, pistol) == SMARTLINK_ATTACK_BONUS


def test_smartlink_bonus_zero_with_a_different_implant_in_the_slot():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, "cybereye_scanner")
    assert character.installed_cyberware[CyberSlot.OPTICS] == "cybereye_scanner"
    pistol = ITEMS_BY_ID["pipe_pistol"]
    assert smartlink_bonus(character, pistol) == 0


def test_player_soak_folds_in_installed_cyberware_defense():
    character = Character(name="t", cash=10_000)
    before = player_soak(character)
    install_cyberware(character, "titanium_bones")
    assert player_soak(character) == before + 2
