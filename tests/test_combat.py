"""Tests for combat.py: drop_for_result, attack_verbs, resolve_hit, defense and soak,
and the Enemy stat block that derives its combat numbers the way the player does."""

import random


from shadowguy.character import Character
from shadowguy.checks import CheckResult
from shadowguy.combat import (
    DEFENSE_BASE,
    ENEMIES,
    ENEMIES_BY_ID,
    ENEMY_TIERS,
    NPC_WEAPONS,
    SMARTLINK_ATTACK_BONUS,
    _DEFAULT_ATTACK_VERBS,
    Drop,
    UNARMED,
    attack_verbs,
    crew_stats,
    drop_for_result,
    player_defense,
    player_soak,
    resolve_hit,
    melee_damage_bonus,
    roll_enemies,
    smartlink_bonus,
    weapon_range,
)
from shadowguy.cybernetics import CyberSlot, install_cyberware
from shadowguy.runners import RIVAL_RUNNERS
from shadowguy.shops import ITEMS_BY_ID, MIN_WEAPON_DAMAGE, Slot
from shadowguy.skills import skill_value

import pytest

from helpers import npc_weapon, synthetic_enemy


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


# --- Enemy: the stat block derives what a fight reads ---


@pytest.mark.parametrize("enemy", ENEMIES, ids=lambda e: e.id)
def test_every_enemy_derives_its_combat_numbers_from_its_own_sheet(enemy):
    """The whole point of the rewrite: none of these seven is a stored number any more,
    so each has to equal the formula behind it rather than whatever a table once said."""
    assert enemy.health == enemy.body * 5
    assert enemy.toughness == enemy.body + enemy.armor
    assert enemy.attack == skill_value(enemy, enemy.weapon.skill)
    assert enemy.defense == DEFENSE_BASE + skill_value(enemy, "dodge")
    assert enemy.stun_damage == enemy.weapon.stun_damage
    assert enemy.reach == weapon_range(enemy.weapon)


@pytest.mark.parametrize("enemy", ENEMIES, ids=lambda e: e.id)
def test_every_enemy_is_a_coherent_combatant(enemy):
    """Invariants rather than exact values: whatever the roster is tuned to, an entry
    that can't hurt anyone or can't be hurt is a bug, not a design."""
    assert enemy.health > 0
    assert enemy.attack > 0
    assert enemy.damage > 0 or enemy.stun_damage > 0
    assert all(rank > 0 for rank in enemy.ranks.values())


def test_enemy_and_player_defense_agree_when_the_sheets_do():
    """Same Dodge value on either side of the table, same defense — the symmetry the
    derived stat block exists for. player_defense and Enemy.defense are the same
    formula, so nothing but the sheet can make them differ."""
    character = Character(name="t", agility=3)
    enemy = synthetic_enemy(npc_weapon("clubs", damage=1), agility=3)
    # The player starts every skill at rank 1 and the enemy at 0, so match them up.
    enemy_matched = synthetic_enemy(npc_weapon("clubs", damage=1), agility=3, ranks={"dodge": 1})
    assert skill_value(character, "dodge") == skill_value(enemy_matched, "dodge")
    assert player_defense(character) == enemy_matched.defense
    assert enemy.defense == enemy_matched.defense - 1


def test_strength_reaches_an_enemy_melee_hit_the_way_it_reaches_the_players():
    """melee_damage_bonus used to be player-only. An Enemy has a Strength now, so the
    same rule applies to it — and still doesn't reach a gun."""
    melee = synthetic_enemy(npc_weapon("clubs", damage=2), strength=4)
    shooter = synthetic_enemy(npc_weapon("pistols", damage=2), strength=4)
    assert melee.damage == 2 + 4
    assert shooter.damage == 2


def test_enemy_stat_rejects_a_name_that_is_not_a_core_stat():
    """Same guard Character.stat has — an Enemy is only the six CORE_STATS, so a typo'd
    skill table entry fails loudly rather than reading as 0."""
    enemy = synthetic_enemy(npc_weapon("clubs", damage=1))
    with pytest.raises(ValueError):
        enemy.stat("cash")


# --- the NPC weapon table ---


def test_npc_weapons_are_not_for_sale_and_reach_under_the_catalog_floor():
    """Why they can't just be catalog items: most sit below MIN_WEAPON_DAMAGE, which the
    catalog rejects at import. Enemy damage picks up Strength now, so arming the roster
    off the player's shelf would double what it was tuned to. Nothing here is buyable
    either way — the three long guns do reach catalog-grade damage."""
    assert NPC_WEAPONS
    assert any(weapon.damage < MIN_WEAPON_DAMAGE for weapon in NPC_WEAPONS.values())
    for weapon in NPC_WEAPONS.values():
        assert weapon.id not in ITEMS_BY_ID  # not for sale, never in a shop listing
        assert weapon.damage or weapon.stun_damage


def test_no_npc_weapon_declares_a_cooldown_no_surface_would_honour():
    """weapon_cooldowns is player-only state on both fight surfaces, so an NPC weapon
    with recharge_rounds would fire every round anyway and the number would be a lie."""
    assert all(weapon.recharge_rounds == 0 for weapon in NPC_WEAPONS.values())


# --- the roster and its tiers ---


def test_every_tier_pool_names_a_real_enemy_and_rolls_within_its_count():
    rng = random.Random(0)
    for tier, (pool, (low, high)) in ENEMY_TIERS.items():
        assert pool and all(enemy_id in ENEMIES_BY_ID for enemy_id in pool)
        for _ in range(50):
            squad = roll_enemies(tier, rng)
            assert low <= len(squad) <= high
            assert all(enemy.id in pool for enemy in squad)


def test_the_roster_spreads_shape_not_just_power():
    """The diversity the roster is for: it is not one ladder of the same creature. At
    least one of each must exist somewhere in it."""
    assert any(enemy.reach > 1 for enemy in ENEMIES)   # something to kite
    assert any(enemy.reach == 1 for enemy in ENEMIES)  # something that must close
    assert any(enemy.stun_damage for enemy in ENEMIES)  # threatens a KO, not a death
    assert any(enemy.armor for enemy in ENEMIES)       # soaks
    # and no two entries are the same creature under a different name
    profiles = {
        (e.health, e.attack, e.defense, e.damage, e.toughness, e.reach, e.stun_damage)
        for e in ENEMIES
    }
    assert len(profiles) >= len(ENEMIES) - 2


# --- crew_stats: a hire is an Enemy pointed the other way ---


@pytest.mark.parametrize("runner", RIVAL_RUNNERS, ids=lambda r: r.id)
def test_crew_stats_builds_a_usable_combatant_for_every_runner(runner):
    """rating is no longer assigned to `attack` — it's bought as rank in their weapon's
    skill, so a better-rated hire is a better-trained one. Whatever the archetype, a hire
    has to come out able to act."""
    stats = crew_stats(runner)
    assert stats.id == runner.id and stats.name == runner.name
    assert stats.attack > 0 and stats.health > 0 and stats.damage > 0
    assert stats.attack <= runner.rating  # rating is a ceiling on the gun half, not a floor


def test_a_netrunner_shoots_worse_than_a_solo_of_the_same_rating():
    """RivalRunner.rating is a runner's standing on the street, not their marksmanship.
    Without _CREW_PROFILES' combat_gap the two were the same number, and a netrunner
    hired for their deck put rounds downrange exactly as well as the hire you pay to
    shoot people."""
    solo = next(r for r in RIVAL_RUNNERS if r.archetype == "Solo")
    netrunner = next(r for r in RIVAL_RUNNERS if r.archetype == "Netrunner")
    assert solo.rating == netrunner.rating, "fixture assumption: equal rating"
    assert crew_stats(netrunner).attack < crew_stats(solo).attack


def test_only_the_solo_converts_its_whole_rating_into_the_gun():
    """The Solo is the one you hire to shoot people, so it's the one archetype whose
    rating is entirely marksmanship. Everyone else gives some of it up."""
    for runner in RIVAL_RUNNERS:
        attack = crew_stats(runner).attack
        if runner.archetype == "Solo":
            assert attack == runner.rating
        else:
            assert attack < runner.rating
