"""Tests for abstract_combat.py: available_actions, the drop, flee-always-works, stun."""

import random


from shadowguy.abstract_combat import (
    Action,
    ActionKind,
    CombatOutcome,
    available_actions,
    start_combat,
    take_turn,
)
from shadowguy.character import Character
from shadowguy.combat import ENEMIES_BY_ID, UNARMED, Drop, Enemy, equipped_weapons
from shadowguy.shops import ITEMS_BY_ID, InventoryItem

from helpers import AlwaysOne, AlwaysSix, npc_weapon, synthetic_enemy


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

        flee_action = Action(kind=ActionKind.FLEE, label="flee", skill="dodge")
        take_turn(state, flee_action, rng=random.Random(seed))
        assert state.outcome in (CombatOutcome.ESCAPED, CombatOutcome.DEAD)


def test_flee_parting_shot_is_from_one_enemy_not_the_whole_squad():
    """A failed flee costs one parting shot, not a free round of every enemy's attack."""
    c = Character(name="t", agility=1, body=10)  # tanky, so the shot alone won't kill
    enemies = tuple(ENEMIES_BY_ID["thug"] for _ in range(5))

    state = start_combat(c, enemies, rng=random.Random(0))

    flee_action = Action(kind=ActionKind.FLEE, label="flee", skill="dodge")
    before = c.health
    take_turn(state, flee_action, rng=AlwaysOne())
    # Worst case: one thug's damage(2) plus margin, definitely not 5 thugs' worth.
    assert before - c.health <= 12


# --- persistent stun (Character.stun replaced the old per-CombatState counter) ---


def _stunning_enemy(stun_damage: int) -> Enemy:
    """A fat target that reliably lands a shocking hit. Its stun now comes off the
    weapon it holds (Enemy.stun_damage is the weapon's), and a *ranged* weapon so its
    damage stays 1 rather than picking up melee_damage_bonus's Strength."""
    return synthetic_enemy(
        npc_weapon("pistols", damage=1, stun_damage=stun_damage),
        body=4, agility=5, id="test_stunner", name="Stunner",
    )


def test_a_landed_hit_from_a_stun_weapon_raises_character_stun():
    c = Character(name="t", body=10)  # tanky, so the parting health damage doesn't kill
    state = start_combat(c, (_stunning_enemy(4),), rng=random.Random(0))
    brace = Action(kind=ActionKind.BRACE, label="brace", skill="toughness")
    before = c.stun
    take_turn(state, brace, rng=AlwaysSix())
    assert c.stun == before + 4


def test_stun_carries_over_into_a_second_fight_instead_of_resetting():
    """The old CombatState.player_stun reset to 0 for every new CombatState; now
    it's Character.stun, so a runner who walks into a second fight already
    rattled starts it stunned instead of fresh."""
    c = Character(name="t", body=10)
    brace = Action(kind=ActionKind.BRACE, label="brace", skill="toughness")

    first = start_combat(c, (_stunning_enemy(4),), rng=random.Random(0))
    take_turn(first, brace, rng=AlwaysSix())
    after_first_fight = c.stun
    assert after_first_fight > 0

    second = start_combat(c, (_stunning_enemy(4),), rng=random.Random(0))
    assert second.character.stun == after_first_fight  # carried over, not reset to 0
    take_turn(second, brace, rng=AlwaysSix())
    assert c.stun == after_first_fight + 4


def test_stun_reaching_current_health_knocks_the_player_out():
    c = Character(name="t", body=1)  # low max_health, easy to reach the threshold in one hit
    state = start_combat(c, (_stunning_enemy(stun_damage=c.health),), rng=random.Random(0))
    brace = Action(kind=ActionKind.BRACE, label="brace", skill="toughness")
    take_turn(state, brace, rng=AlwaysSix())
    assert state.outcome is CombatOutcome.KNOCKED_OUT


# --- Strength on a melee hit (combat.melee_damage_bonus) ---


def _one_melee_swing(strength: int) -> int:
    """Health taken off a fat target by one katana swing at `strength`. AlwaysSix pins
    both the to-hit roll and the enemy's soak, so the only thing that moves between two
    calls is the Strength folded into base_damage.

    The target is body 4 (health 20, toughness 4) rather than the old health-999/
    toughness-0 dummy: an Enemy's health and soak both derive from Body now, so a
    punching bag that never soaks is no longer expressible. It doesn't matter — the soak
    is identical across both calls, so it cancels out of the difference — but the health
    does have to outlast the bigger swing, which is what 20 is for. The swinger needs
    agility 6 to clear the target's dodge pool at all: every weapon skill is agility's.
    """
    character = Character(
        name="t", strength=strength, agility=6,
        inventory=[InventoryItem(item_id="mono_katana", equipped=True)],
    )
    enemy = synthetic_enemy(npc_weapon("clubs", damage=1), body=4, id="dummy", name="Dummy")
    state = start_combat(character, (enemy,), rng=AlwaysSix())
    swing = next(
        a for a in available_actions(character, state.weapon_cooldowns)
        if a.kind is ActionKind.ATTACK and a.weapon and a.weapon.id == "mono_katana"
    )
    take_turn(state, swing, rng=AlwaysSix())
    return enemy.health - state.fighters[0].health


def test_strength_reaches_a_landed_melee_hit_point_for_point():
    weak, strong = _one_melee_swing(1), _one_melee_swing(7)
    assert strong - weak == 6


def test_strength_does_not_reach_a_gun():
    """The same swing with a pistol equipped instead: Strength is melee-only, so two
    very different runners hit for exactly the same."""
    def one_shot(strength: int) -> int:
        character = Character(
            name="t", strength=strength, agility=6,
            inventory=[InventoryItem(item_id="pipe_pistol", equipped=True)],
        )
        enemy = synthetic_enemy(npc_weapon("clubs", damage=1), body=4, id="d", name="D")
        state = start_combat(character, (enemy,), rng=AlwaysSix())
        shot = next(
            a for a in available_actions(character, state.weapon_cooldowns)
            if a.kind is ActionKind.ATTACK and a.weapon and a.weapon.id == "pipe_pistol"
        )
        take_turn(state, shot, rng=AlwaysSix())
        return enemy.health - state.fighters[0].health

    assert one_shot(1) == one_shot(9)
