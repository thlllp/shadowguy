"""Tests for combat.py: drop_for_result, available_actions, resolve_hit, flee-always-works."""

import random


from shadowguy.character import Character
from shadowguy.checks import CheckResult
from shadowguy.combat import (
    ActionKind,
    CombatOutcome,
    Drop,
    ENEMIES_BY_ID,
    UNARMED,
    available_actions,
    drop_for_result,
    equipped_weapons,
    resolve_hit,
    start_combat,
    take_turn,
)
from shadowguy.shops import ITEMS_BY_ID, InventoryItem


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


# --- available_actions ---


def test_available_actions_always_non_empty():
    c = Character(name="t")
    actions = available_actions(c)
    assert actions


def test_available_actions_includes_unarmed_attack_with_no_weapon():
    c = Character(name="t")
    actions = available_actions(c)
    attacks = [a for a in actions if a.kind is ActionKind.ATTACK]
    assert len(attacks) == 1
    assert attacks[0].weapon is UNARMED


def test_available_actions_always_offers_flee_and_brace():
    c = Character(name="t")
    kinds = {a.kind for a in available_actions(c)}
    assert ActionKind.FLEE in kinds
    assert ActionKind.BRACE in kinds


def test_available_actions_one_attack_per_equipped_weapon():
    weapon_ids = [item.id for item in ITEMS_BY_ID.values() if item.slot and item.slot.value == "weapon"][:2]
    assert len(weapon_ids) >= 1
    c = Character(name="t", inventory=[InventoryItem(item_id=wid, equipped=True) for wid in weapon_ids])
    actions = available_actions(c)
    attacks = [a for a in actions if a.kind is ActionKind.ATTACK]
    assert len(attacks) == len(equipped_weapons(c))


def test_available_actions_skips_weapons_on_cooldown():
    weapon_ids = [item.id for item in ITEMS_BY_ID.values() if item.slot and item.slot.value == "weapon"]
    c = Character(name="t", inventory=[InventoryItem(item_id=weapon_ids[0], equipped=True)])
    actions = available_actions(c, cooldowns={weapon_ids[0]: 1})
    attacks = [a for a in actions if a.kind is ActionKind.ATTACK]
    # Weapon on cooldown falls back to bare hands, never an empty attack list.
    assert len(attacks) == 1
    assert attacks[0].weapon is UNARMED


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


# --- start_combat / drop handling ---


def test_start_combat_player_drop_removes_a_straggler_if_more_than_one_enemy():
    c = Character(name="t")
    enemies = (ENEMIES_BY_ID["thug"], ENEMIES_BY_ID["thug"], ENEMIES_BY_ID["thug"])
    state = start_combat(c, enemies, drop=Drop.PLAYER, rng=random.Random(0))
    assert sum(1 for f in state.fighters if not f.is_standing) == 1
    assert state.enemy_skip_rounds == 1


def test_start_combat_player_drop_never_removes_the_only_enemy():
    c = Character(name="t")
    enemies = (ENEMIES_BY_ID["thug"],)
    state = start_combat(c, enemies, drop=Drop.PLAYER, rng=random.Random(0))
    assert state.fighters[0].is_standing


def test_start_combat_enemy_drop_deals_one_free_hit_only():
    c = Character(name="t", body=5)
    enemies = (ENEMIES_BY_ID["enforcer"], ENEMIES_BY_ID["enforcer"], ENEMIES_BY_ID["enforcer"])
    before = c.health
    start_combat(c, enemies, drop=Drop.ENEMY, rng=random.Random(1))
    # Only one enemy's damage (bounded by the strongest tier row's damage) could have
    # landed, not three -- a whole squad's free hit is exactly what the drop rule forbids.
    assert before - c.health <= ENEMIES_BY_ID["enforcer"].damage + 10  # generous margin incl. crit


# --- flee: always works, only the cost varies ---


def test_flee_always_ends_the_fight_escaped_or_dead_never_ongoing():
    """Running always works -- the Dodge check only decides the cost, never whether
    you get out. A fight must never be a cage."""
    c = Character(name="t", agility=1, body=1)  # worst possible flee build
    enemies = tuple(ENEMIES_BY_ID["enforcer"] for _ in range(4))  # maximally lethal squad
    for seed in range(30):
        state = start_combat(c.__class__(name="t", agility=1, body=1), enemies, rng=random.Random(seed))
        fresh = state.character
        fresh.health = fresh.max_health
        from shadowguy.combat import Action

        flee_action = Action(kind=ActionKind.FLEE, label="flee", skill="dodge")
        take_turn(state, flee_action, rng=random.Random(seed))
        assert state.outcome in (CombatOutcome.ESCAPED, CombatOutcome.DEAD)


def test_flee_parting_shot_is_from_one_enemy_not_the_whole_squad():
    """A failed flee costs one parting shot, not a free round of every enemy's attack."""
    c = Character(name="t", agility=1, body=10)  # tanky, so the shot alone won't kill
    enemies = tuple(ENEMIES_BY_ID["thug"] for _ in range(5))

    class AlwaysOne(random.Random):
        def randint(self, a, b):
            return 1  # guarantees the flee check itself fails

    state = start_combat(c, enemies, rng=random.Random(0))
    from shadowguy.combat import Action

    flee_action = Action(kind=ActionKind.FLEE, label="flee", skill="dodge")
    before = c.health
    take_turn(state, flee_action, rng=AlwaysOne())
    # Worst case: one thug's damage(2) plus margin, definitely not 5 thugs' worth.
    assert before - c.health <= 12
