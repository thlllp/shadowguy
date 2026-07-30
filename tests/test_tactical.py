"""Tests for tactical.py: LOS/range gating, attack resolution, enemy-phase
movement/combat, and generated-map invariants.

The grid geometry underneath is grid.py — see test_grid.py."""

import random

import pytest

from shadowguy.buildings import BuildingKind, Lock, generate_building
from shadowguy.character import Character
from shadowguy.checks import pool_for_difficulty
from shadowguy.combat import ENEMIES_BY_ID, attack_verbs, player_defense
from shadowguy.grid import (
    Tile,
    chebyshev,
    has_line_of_sight,
    parse_grid,
    path_between,
    visible_tiles,
)
from shadowguy.shops import ITEMS_BY_ID, InventoryItem
from shadowguy.tactical import (
    ARREST_DAYS,
    ENEMY_SPEED,
    FIREARM_RANGE,
    FULL_COVER,
    GRENADE_RADIUS,
    GRENADE_RANGE,
    HALF_COVER,
    LOCK_FAILURE_ALARM_CHANCE,
    MELEE_RANGE,
    TAC_MAP_HEIGHT,
    TAC_MAP_WIDTH,
    AimKind,
    CrewFate,
    Side,
    TacticalOutcome,
    Unit,
    aim_is_legal,
    attack_targets,
    attempt_lock,
    available_grenades,
    begin_attack_aim,
    begin_grenade_aim,
    begin_look,
    best_shot,
    cancel_aim,
    confirm_aim,
    confirm_attack_aim,
    confirm_grenade_aim,
    _reveal,
    _settle,
    check_detection,
    cover_bonus,
    end_turn,
    enter_level,
    enemy_at,
    enemy_target,
    generate_map,
    leave,
    legal_attack_target,
    legal_grenade_target,
    legal_moves,
    lock_at,
    move_aim_cursor,
    move_player,
    player_attack,
    player_weapons,
    resolve_downed_crew,
    snap_aim_to_next_target,
    stabilize_ally,
    stairs_here,
    start_burglary,
    stabilize_targets,
    start_tactical,
    take_stairs,
    targets_for,
    throw_grenade,
    weapon_for_target,
    weapon_range,
)

from helpers import AlwaysOne, AlwaysSix, ForcedChance, crew_stats_for

SEEDS = range(80)


def test_weapon_range_firearms_outranges_melee():
    firearm = next(i for i in ITEMS_BY_ID.values() if i.skill == "firearms")
    melee = next(i for i in ITEMS_BY_ID.values() if i.skill and i.skill != "firearms" and i.damage)
    assert weapon_range(firearm) == FIREARM_RANGE
    assert weapon_range(melee) == MELEE_RANGE
    assert FIREARM_RANGE > MELEE_RANGE


# --- cover_bonus ---


def test_cover_bonus_is_zero_with_nothing_between():
    grid = parse_grid(["....."])
    assert cover_bonus(grid, defender=(2, 0), attacker=(4, 0)) == 0


def test_cover_bonus_is_zero_against_your_own_cell():
    grid = parse_grid(["....."])
    assert cover_bonus(grid, defender=(2, 0), attacker=(2, 0)) == 0


def test_cover_bonus_wall_grants_full_cover():
    grid = parse_grid(["...#."])
    assert cover_bonus(grid, defender=(2, 0), attacker=(4, 0)) == FULL_COVER


def test_cover_bonus_low_cover_grants_half_cover():
    grid = parse_grid(["...%."])
    assert cover_bonus(grid, defender=(2, 0), attacker=(4, 0)) == HALF_COVER


def test_cover_bonus_takes_the_best_of_both_cardinal_sides():
    # Defender (2,2), attacker diagonally at (4,4): the two cardinal cells checked are
    # (3,2) (low cover) and (2,3) (a wall) -- the wall should win.
    rows = [
        ".....",
        ".....",
        "...%.",
        "..#..",
        ".....",
    ]
    grid = parse_grid(rows)
    assert cover_bonus(grid, defender=(2, 2), attacker=(4, 4)) == FULL_COVER


# --- TacticalState: movement, leaving, flee-is-always-available ---


def _simple_state():
    grid = parse_grid(["......", "......", "......"])
    character = Character(name="t")
    enemy = ENEMIES_BY_ID["thug"]
    return start_tactical(character, grid, player_start=(0, 0), enemy_placements=[(enemy, (5, 2))], exits=frozenset({(0, 0)}))


def test_move_player_rejects_illegal_step():
    state = _simple_state()
    assert not move_player(state, (5, 5))  # not adjacent
    assert state.player.coord == (0, 0)


def test_move_player_accepts_legal_step_and_spends_a_move():
    state = _simple_state()
    before = state.moves_left
    assert move_player(state, (1, 0))
    assert state.player.coord == (1, 0)
    assert state.moves_left == before - 1


def test_move_player_accepts_a_diagonal_step_for_one_move():
    state = _simple_state()
    before = state.moves_left
    assert move_player(state, (1, 1))
    assert state.player.coord == (1, 1)
    assert state.moves_left == before - 1


def test_legal_moves_empty_once_moves_exhausted():
    state = _simple_state()
    state.moves_left = 0
    assert legal_moves(state) == []


# --- fog of war: TacticalState.explored ---


def test_fight_start_reveals_the_players_own_fov():
    state = _simple_state()
    seen = {(x, y) for y in range(state.grid.height) for x in range(state.grid.width) if visible_tiles(state.grid, (0, 0))[y, x]}
    assert seen  # sanity: an open room sees at least itself
    assert state.explored[0] == seen


def test_explored_keeps_tiles_that_drop_out_of_current_fov():
    """A tile once seen stays "known" (fog of war) even once a wall cuts off the current
    line of sight back to it -- explored is a memory, not a snapshot of visible_tiles."""
    grid = parse_grid(["..#..", "..#..", "....."])
    character = Character(name="t")
    enemy = ENEMIES_BY_ID["thug"]
    state = start_tactical(character, grid, player_start=(0, 0), enemy_placements=[(enemy, (4, 2))])
    start_explored = set(state.explored[0])
    assert start_explored  # sanity

    state.player.coord = (4, 0)
    _reveal(state)
    far_seen = {(x, y) for y in range(grid.height) for x in range(grid.width) if visible_tiles(grid, (4, 0))[y, x]}
    assert far_seen  # sanity

    left_room_only = start_explored - far_seen
    assert left_room_only  # sanity: the wall column actually blocked something
    # Nothing seen from the start position is forgotten once the far room's FOV
    # doesn't include it anymore.
    assert left_room_only <= state.explored[0]
    assert far_seen <= state.explored[0]


def test_leave_succeeds_from_an_exit_tile_with_no_roll():
    state = _simple_state()
    assert state.player.coord in state.exits
    assert leave(state)
    from shadowguy.tactical import TacticalOutcome
    assert state.outcome is TacticalOutcome.ESCAPED


def test_leave_fails_off_an_exit_tile():
    grid = parse_grid(["......"])
    character = Character(name="t")
    enemy = ENEMIES_BY_ID["thug"]
    state = start_tactical(character, grid, player_start=(2, 0), enemy_placements=[(enemy, (5, 0))], exits=frozenset({(0, 0)}))
    assert not leave(state)


# --- grenades (throw_grenade / available_grenades) ---


def test_available_grenades_excludes_non_combat_consumables():
    state = _simple_state()
    state.character.consumables.append("health_kit")
    assert available_grenades(state) == []


def test_available_grenades_empty_once_acted():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    state.acted = True
    assert available_grenades(state) == []


def test_throw_grenade_damage_all_hits_a_targeted_enemy_and_spends_the_turn():
    grid = parse_grid(["......", "......", "......"])
    character = Character(name="t", consumables=["grenade_frag"])
    enemy = ENEMIES_BY_ID["enforcer"]  # 11 health -- survives a 7-damage frag
    state = start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(enemy, (5, 2))], exits=frozenset({(0, 0)})
    )
    index, consumable = available_grenades(state)[0]
    health_before = state.enemies[0].health
    throw_grenade(state, index, target=(5, 2))
    assert state.acted
    assert character.consumables == []  # thrown, popped
    assert state.enemies[0].health == health_before - consumable.amount


def test_throw_grenade_stun_makes_the_targeted_enemy_skip_its_next_phase_entirely():
    grid = parse_grid(["......", "......", "......"])
    character = Character(name="t", consumables=["grenade_flash"])
    enemy = ENEMIES_BY_ID["thug"]  # reach 1, melee -- would otherwise land a hit at range 1
    state = start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(enemy, (1, 0))], exits=frozenset({(0, 0)})
    )
    index, consumable = available_grenades(state)[0]
    throw_grenade(state, index, target=(1, 0))
    assert state.enemies[0].stunned_rounds == consumable.amount

    health_before = character.health
    end_turn(state, random.Random(0))
    assert character.health == health_before  # stunned -- no attack landed
    assert state.enemies[0].stunned_rounds == consumable.amount - 1
    assert "reeling" in state.log[-1]


def test_throw_grenade_only_hits_enemies_within_the_blast_radius():
    grid = parse_grid(["......", "......", "......", "......", "......", "......"])
    character = Character(name="t", consumables=["grenade_flash"])
    thug = ENEMIES_BY_ID["thug"]
    state = start_tactical(
        character,
        grid,
        player_start=(0, 0),
        enemy_placements=[(thug, (3, 4)), (thug, (3, 5))],
        exits=frozenset({(0, 0)}),
    )
    index, consumable = available_grenades(state)[0]
    throw_grenade(state, index, target=(3, 3))
    near, far = state.units[1], state.units[2]
    assert chebyshev((3, 3), near.coord) == GRENADE_RADIUS  # inside the 3x3 blast
    assert chebyshev((3, 3), far.coord) == GRENADE_RADIUS + 1  # one step outside it
    assert near.stunned_rounds == consumable.amount
    assert far.stunned_rounds == 0


def test_throw_grenade_raises_without_a_target_for_an_area_effect():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    index, _ = available_grenades(state)[0]
    with pytest.raises(ValueError):
        throw_grenade(state, index)


def test_throw_grenade_escape_ends_the_fight_with_no_roll_and_no_target():
    state = _simple_state()
    state.character.consumables.append("grenade_smoke")
    index, _ = available_grenades(state)[0]
    throw_grenade(state, index)
    assert state.outcome is TacticalOutcome.ESCAPED


def test_throw_grenade_is_a_noop_once_already_acted():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    state.acted = True
    throw_grenade(state, 0, target=(5, 2))
    assert state.character.consumables == ["grenade_frag"]  # untouched, not popped


# --- grenade tile-targeting (begin_grenade_aim / move_aim_cursor / confirm|cancel) ---


def test_legal_grenade_target_range_gate():
    grid = parse_grid(["." * 10 for _ in range(10)])
    state = start_tactical(Character(name="t"), grid, player_start=(0, 0), enemy_placements=[], exits=frozenset())
    assert legal_grenade_target(state, (GRENADE_RANGE, GRENADE_RANGE))  # chebyshev == range: still legal
    assert not legal_grenade_target(state, (GRENADE_RANGE + 1, 0))


def test_legal_grenade_target_los_gate():
    grid = parse_grid(["...", "###", "..."])
    state = start_tactical(Character(name="t"), grid, player_start=(0, 0), enemy_placements=[], exits=frozenset())
    assert not legal_grenade_target(state, (0, 2))  # within range, but blocked by the wall row


def test_move_aim_cursor_clamps_to_grid_bounds():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    index, _ = available_grenades(state)[0]
    begin_grenade_aim(state, index)
    assert state.aim_cursor == (0, 0)
    move_aim_cursor(state, -1, 0)  # off the left edge -- stays put
    assert state.aim_cursor == (0, 0)
    move_aim_cursor(state, 1, 0)
    assert state.aim_cursor == (1, 0)


def test_begin_grenade_aim_is_a_noop_once_already_acted():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    state.acted = True
    begin_grenade_aim(state, 0)
    assert state.aim_cursor is None
    assert state.pending_grenade_index is None


def test_begin_look_starts_on_the_player_and_spends_nothing():
    state = _simple_state()
    assert begin_look(state)
    assert state.aim_cursor == state.player.coord
    assert state.aim_kind is AimKind.LOOK
    assert not state.acted
    assert state.moves_left == state.player.speed


def test_begin_look_refuses_once_the_fight_is_over():
    state = _simple_state()
    state.outcome = TacticalOutcome.VICTORY
    assert not begin_look(state)
    assert state.aim_cursor is None


def test_move_aim_cursor_pans_a_look_cursor_same_as_any_other():
    state = _simple_state()
    begin_look(state)
    move_aim_cursor(state, 1, 0)
    assert state.aim_cursor == (1, 0)


def test_cancel_aim_spends_nothing():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    index, _ = available_grenades(state)[0]
    begin_grenade_aim(state, index)
    cancel_aim(state)
    assert state.aim_cursor is None
    assert state.pending_grenade_index is None
    assert not state.acted
    assert state.character.consumables == ["grenade_frag"]  # not popped


def test_confirm_grenade_aim_refuses_an_out_of_range_target():
    grid = parse_grid(["." * 20 for _ in range(3)])
    character = Character(name="t", consumables=["grenade_frag"])
    enemy = ENEMIES_BY_ID["thug"]
    state = start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(enemy, (19, 0))], exits=frozenset({(0, 0)})
    )
    index, _ = available_grenades(state)[0]
    begin_grenade_aim(state, index)
    state.aim_cursor = (19, 0)  # far beyond GRENADE_RANGE
    assert not confirm_grenade_aim(state)
    assert state.aim_cursor == (19, 0)  # unchanged -- still aiming, nothing spent
    assert not state.acted
    assert character.consumables == ["grenade_frag"]


def test_grenade_aim_begin_move_confirm_resolves_the_throw():
    grid = parse_grid(["......", "......", "......"])
    character = Character(name="t", consumables=["grenade_frag"])
    enemy = ENEMIES_BY_ID["enforcer"]  # 11 health -- survives a 7-damage frag
    state = start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(enemy, (5, 2))], exits=frozenset({(0, 0)})
    )
    index, consumable = available_grenades(state)[0]
    begin_grenade_aim(state, index)
    assert state.aim_cursor == (0, 0)
    assert state.pending_grenade_index == index

    for _ in range(5):
        move_aim_cursor(state, 1, 0)
    move_aim_cursor(state, 0, 1)
    move_aim_cursor(state, 0, 1)
    assert state.aim_cursor == (5, 2)

    health_before = state.enemies[0].health
    assert confirm_grenade_aim(state)
    assert state.aim_cursor is None
    assert state.pending_grenade_index is None
    assert state.acted
    assert character.consumables == []
    assert state.enemies[0].health == health_before - consumable.amount


# --- targets_for (range + LOS gating on an actual weapon) ---


def _firearm_id():
    return next(i.id for i in ITEMS_BY_ID.values() if i.skill == "firearms")


def test_targets_for_excludes_an_out_of_range_enemy():
    grid = parse_grid(["." * 10])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (9, 0))], exits=frozenset(),
    )
    weapon = player_weapons(state)[0]  # bare hands, MELEE_RANGE
    assert targets_for(state, weapon) == []


def test_targets_for_excludes_an_out_of_los_enemy():
    grid = parse_grid(["...", "###", "..."])
    character = Character(name="t", inventory=[InventoryItem(item_id=_firearm_id(), equipped=True)])
    state = start_tactical(
        character, grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (0, 2))], exits=frozenset(),
    )
    weapon = player_weapons(state)[0]
    assert targets_for(state, weapon) == []  # in firearm range, but the wall row blocks LOS


def test_targets_for_includes_an_in_range_in_los_enemy():
    grid = parse_grid(["......"])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (1, 0))], exits=frozenset(),
    )
    weapon = player_weapons(state)[0]
    assert state.enemies[0] in targets_for(state, weapon)


# --- player_attack ---


def _adjacent_state(enemy_id="thug", enemy_coord=(1, 0)):
    grid = parse_grid(["......", "......", "......"])
    character = Character(name="t")
    enemy = ENEMIES_BY_ID[enemy_id]
    return start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(enemy, enemy_coord)], exits=frozenset({(0, 0)})
    )


def test_player_attack_guaranteed_miss_leaves_the_enemy_untouched():
    """thug's defense (9) converts to a 0-dice opposing pool (checks.pool_for_
    difficulty), so under AlwaysOne the attacker rolls 0 successes against an
    opposition that also rolls 0 -- margin 0 is a miss regardless of build."""
    state = _adjacent_state()
    weapon = player_weapons(state)[0]
    health_before = state.enemies[0].health
    player_attack(state, state.enemies[0], weapon, rng=AlwaysOne())
    assert state.enemies[0].health == health_before
    assert state.acted
    assert "miss" in state.log[-1]


def test_player_attack_guaranteed_hit_deals_the_exact_margin_damage():
    """Same 0-dice opposing pool as above, but under AlwaysSix every die the attacker
    rolls succeeds too, so margin == the attacker's whole pool (bare-hands grapple
    skill_value 2 for a fresh Character). Soak (thug toughness 1) is also fully
    saturated under AlwaysSix, so damage is UNARMED.damage (0) + 2 - 1 = 1, an exact,
    non-random number to pin instead of only asserting "some damage happened"."""
    state = _adjacent_state()
    weapon = player_weapons(state)[0]
    health_before = state.enemies[0].health
    player_attack(state, state.enemies[0], weapon, rng=AlwaysSix())
    assert state.enemies[0].health == health_before - 1
    assert state.acted
    # The log line reads off the weapon's own verb (combat.attack_verbs), so assert
    # against the table rather than a literal -- bare hands "wrestle", they don't "hit".
    assert attack_verbs(weapon)[1] in state.log[-1]


def test_player_attack_refuses_a_target_out_of_weapon_range():
    grid = parse_grid(["." * 10])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (9, 0))], exits=frozenset(),
    )
    weapon = player_weapons(state)[0]
    player_attack(state, state.enemies[0], weapon, rng=AlwaysSix())
    assert not state.acted
    assert state.enemies[0].health == ENEMIES_BY_ID["thug"].health


def test_player_attack_is_a_noop_once_already_acted():
    state = _adjacent_state()
    state.acted = True
    weapon = player_weapons(state)[0]
    health_before = state.enemies[0].health
    player_attack(state, state.enemies[0], weapon, rng=AlwaysSix())
    assert state.enemies[0].health == health_before


def test_player_attack_kills_the_last_enemy_and_ends_the_fight_in_victory():
    state = _adjacent_state()
    state.enemies[0].health = 1  # one guaranteed hit (see the exact-damage test) is lethal
    weapon = player_weapons(state)[0]
    player_attack(state, state.enemies[0], weapon, rng=AlwaysSix())
    assert state.outcome is TacticalOutcome.VICTORY
    assert "drop" in state.log[-1]


# --- best_shot (the attack policy: nearest in-range, in-sight target) ---


def test_best_shot_is_none_once_already_acted():
    state = _adjacent_state()
    state.acted = True
    assert best_shot(state) is None


def test_best_shot_is_none_with_nothing_in_range():
    grid = parse_grid(["." * 20])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (19, 0))], exits=frozenset(),
    )
    assert best_shot(state) is None  # bare hands, way outside MELEE_RANGE


def test_best_shot_picks_the_nearest_enemy_in_range():
    character = Character(name="t", inventory=[InventoryItem(item_id=_firearm_id(), equipped=True)])
    grid = parse_grid(["." * 10])
    near, far = (2, 0), (5, 0)
    state = start_tactical(
        character, grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], near), (ENEMIES_BY_ID["thug"], far)], exits=frozenset(),
    )
    target = best_shot(state)
    assert target.coord == near


# --- end_turn / _enemy_phase: movement policy and attack resolution ---


def test_ranged_enemy_holds_distance_when_it_already_has_a_shot():
    grid = parse_grid(["." * 10])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["corp_sec"], (5, 0))],  # reach 6: already in range+LOS
        exits=frozenset(),
    )
    end_turn(state, AlwaysOne())
    assert state.enemies[0].coord == (5, 0)  # never advanced -- no reason to give up the angle


def test_melee_enemy_closes_in_to_reach_via_pathfinding():
    grid = parse_grid(["." * 10])
    start_coord = (ENEMY_SPEED + 1, 0)  # just out of a 1-reach melee's attack this turn
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], start_coord)],  # reach 1
        exits=frozenset(),
    )
    end_turn(state, AlwaysOne())
    assert state.enemies[0].coord == (1, 0)  # closed the gap and stopped once adjacent


def test_enemy_phase_resolves_every_standing_enemy_not_just_the_first():
    grid = parse_grid(["......", "......", "......"])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (1, 0)), (ENEMIES_BY_ID["thug"], (0, 1))],
        exits=frozenset(),
    )
    end_turn(state, AlwaysOne())
    misses = [line for line in state.log if "swings wide" in line]
    assert len(misses) == 2  # both enemies were already adjacent and both attacked


def test_enemy_attack_guaranteed_hit_deals_the_exact_margin_damage():
    """A fresh Character's player_defense (14) converts to a 2-dice opposing pool;
    the enforcer's attack pool (4) is the strongest in the roster, so under AlwaysSix
    margin is 4-2=2. player_soak (1, also fully saturated under AlwaysSix) takes the
    rest: damage is enforcer.damage (4) + 2 - 1 = 5, exact and non-random."""
    character = Character(name="t")
    assert ENEMIES_BY_ID["enforcer"].attack > pool_for_difficulty(player_defense(character))
    grid = parse_grid(["......"])
    state = start_tactical(
        character, grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["enforcer"], (1, 0))], exits=frozenset(),
    )
    health_before = character.health
    end_turn(state, AlwaysSix())
    assert character.health == health_before - 5
    assert "hits you" in state.log[-1]


def test_enemy_attack_lethal_damage_ends_the_fight_dead():
    character = Character(name="t")
    character.health = 1  # below the guaranteed damage the test above pins at 5
    grid = parse_grid(["......"])
    state = start_tactical(
        character, grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["enforcer"], (1, 0))], exits=frozenset(),
    )
    end_turn(state, AlwaysSix())
    assert state.outcome is TacticalOutcome.DEAD


# --- attack targeting (begin_attack_aim / snap / confirm, weapon_for_target) ---


def _armed_state(enemy_coords, player_start=(0, 0), rows=None, weapons=("pipe_pistol", "combat_knife")):
    """A fight where the player carries a gun and a knife, so weapon_for_target has an
    actual choice to make at each range."""
    grid = parse_grid(rows or ["." * 12 for _ in range(3)])
    character = Character(
        name="t", inventory=[InventoryItem(item_id=wid, equipped=True) for wid in weapons]
    )
    thug = ENEMIES_BY_ID["thug"]
    return start_tactical(
        character,
        grid,
        player_start=player_start,
        enemy_placements=[(thug, coord) for coord in enemy_coords],
        exits=frozenset({player_start}),
    )


def test_weapon_for_target_takes_the_firearm_at_range_and_the_blade_up_close():
    state = _armed_state([(1, 0), (6, 0)])
    adjacent, distant = state.enemies
    # The knife out-damages the pistol (5 vs 5 base, blade wins on ties by catalog order
    # only if it's actually reaching) -- what matters is the gun is the *only* option at 6.
    assert weapon_for_target(state, distant).skill == "firearms"
    assert weapon_for_target(state, adjacent).damage >= weapon_for_target(state, distant).damage


def test_weapon_for_target_is_none_beyond_every_weapons_reach():
    state = _armed_state([(0, 0)], player_start=(0, 2), rows=["." * 20 for _ in range(3)])
    far = state.enemies[0]
    far.coord = (FIREARM_RANGE + 4, 2)
    assert weapon_for_target(state, far) is None
    assert attack_targets(state) == []


def test_attack_targets_excludes_an_enemy_behind_a_wall():
    state = _armed_state([(2, 0)], player_start=(0, 0), rows=["..#..", "....."])
    assert state.enemies[0].coord == (2, 0)
    state.enemies[0].coord = (4, 0)  # in range, but the wall at (2, 0) blocks the line
    assert not has_line_of_sight(state.grid, (0, 0), (4, 0))
    assert attack_targets(state) == []


def test_begin_attack_aim_starts_on_the_default_target_and_spends_nothing():
    state = _armed_state([(6, 0), (2, 0)])
    begin_attack_aim(state)
    assert state.aim_kind is AimKind.ATTACK
    assert state.aim_cursor == (2, 0)  # nearest of the two, best_shot's pick
    assert not state.acted


def test_begin_attack_aim_is_a_noop_once_already_acted():
    state = _armed_state([(2, 0)])
    state.acted = True
    begin_attack_aim(state)
    assert state.aim_cursor is None
    assert state.aim_kind is None


def test_snap_aim_to_next_target_cycles_nearest_first_and_wraps():
    state = _armed_state([(6, 0), (2, 0), (4, 0)])
    begin_attack_aim(state)
    assert state.aim_cursor == (2, 0)
    assert snap_aim_to_next_target(state)
    assert state.aim_cursor == (4, 0)
    assert snap_aim_to_next_target(state)
    assert state.aim_cursor == (6, 0)
    assert snap_aim_to_next_target(state)
    assert state.aim_cursor == (2, 0)  # wrapped


def test_snap_aim_skips_an_enemy_no_weapon_reaches():
    state = _armed_state([(2, 0)], rows=["." * 20 for _ in range(3)])
    state.units.append(Unit(
        name="far thug", side=Side.ENEMY, coord=(FIREARM_RANGE + 3, 0), speed=4,
        stats=ENEMIES_BY_ID["thug"], health=5,
    ))
    begin_attack_aim(state)
    # Only the in-reach enemy is in the cycle, so snapping stays put on it.
    assert snap_aim_to_next_target(state)
    assert state.aim_cursor == (2, 0)


def test_confirm_attack_aim_refuses_an_empty_tile_and_keeps_aiming():
    state = _armed_state([(2, 0)])
    begin_attack_aim(state)
    state.aim_cursor = (2, 1)  # empty floor beside the thug
    assert not confirm_attack_aim(state, AlwaysSix())
    assert state.aim_cursor == (2, 1)  # still aiming
    assert state.aim_kind is AimKind.ATTACK
    assert not state.acted


def test_confirm_attack_aim_hits_the_aimed_enemy_not_the_nearest():
    """The whole point of aiming: the player's pick beats best_shot's default."""
    state = _armed_state([(1, 0), (5, 0)])
    near, far = state.enemies
    begin_attack_aim(state)
    assert state.aim_cursor == near.coord
    snap_aim_to_next_target(state)
    assert state.aim_cursor == far.coord

    near_health, far_health = near.health, far.health
    assert confirm_attack_aim(state, AlwaysSix())  # forced hit
    assert state.acted
    assert state.aim_cursor is None and state.aim_kind is None
    assert near.health == near_health  # the nearer enemy was never the target
    assert far.health < far_health


def test_confirm_aim_dispatches_on_the_running_aim_kind():
    # Two fights rather than one: a forced hit can drop the only enemy and end the
    # fight, which would leave nothing to aim the second half at.
    attacking = _armed_state([(2, 0)])
    attacking.character.consumables.append("grenade_frag")
    begin_attack_aim(attacking)
    assert confirm_aim(attacking, AlwaysSix())
    assert attacking.acted
    assert attacking.character.consumables == ["grenade_frag"]  # attack branch, nothing thrown

    throwing = _armed_state([(2, 0)])
    throwing.character.consumables.append("grenade_frag")
    index, _ = available_grenades(throwing)[0]
    begin_grenade_aim(throwing, index)
    throwing.aim_cursor = (2, 0)
    assert confirm_aim(throwing, AlwaysSix())
    assert throwing.acted
    assert throwing.character.consumables == []  # grenade branch, thrown and popped


def test_aim_is_legal_follows_the_kind_for_the_same_cell():
    state = _armed_state([(2, 0)])
    state.character.consumables.append("grenade_frag")
    empty = (3, 1)  # in range of both, but nobody standing there
    begin_attack_aim(state)
    assert not aim_is_legal(state, empty)  # no unit to attack
    cancel_aim(state)
    index, _ = available_grenades(state)[0]
    begin_grenade_aim(state, index)
    assert aim_is_legal(state, empty)  # a tile is all a grenade needs


# --- allies and enemy target selection ---


def test_start_tactical_places_allies_around_the_player():
    grid = parse_grid(["." * 8 for _ in range(3)])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (7, 2))], allies=[crew_stats_for()],
    )
    ally = state.allies[0]
    assert ally.is_ally and not ally.is_enemy
    assert chebyshev(ally.coord, (0, 0)) == 1  # next to the player they came in with
    assert ally.coord != state.player.coord    # never stacked on the same tile


def test_allies_that_dont_fit_the_entry_room_sit_the_fight_out():
    grid = parse_grid(["#.#", "###"])  # exactly one open cell: the player's own
    state = start_tactical(
        Character(name="t"), grid, player_start=(1, 0), enemy_placements=[], allies=[crew_stats_for()],
    )
    assert state.allies == []


def test_enemy_targets_the_runner_it_can_hit_without_cover():
    """Cover redirects fire: the player tucked against a wall is passed over for the
    hire standing in the open, even though the player is nearer."""
    # Middle row: ally in the open at x=1, player at x=5 tucked behind the crate at x=6,
    # enemy at x=7. The player is three times closer -- and still not the one shot at.
    grid = parse_grid(["........", ".A...@%E", "........"])
    state = start_tactical(
        Character(name="t"), grid, player_start=(5, 1),
        enemy_placements=[(ENEMIES_BY_ID["corp_sec"], (7, 1))], allies=[crew_stats_for()],
    )
    state.allies[0].coord = (1, 1)
    enemy = state.enemies[0]
    assert cover_bonus(state.grid, state.player.coord, enemy.coord) > 0
    assert cover_bonus(state.grid, state.allies[0].coord, enemy.coord) == 0
    assert enemy_target(state, enemy) is state.allies[0]


def test_enemy_falls_back_to_the_nearest_when_cover_is_equal():
    grid = parse_grid(["." * 10 for _ in range(3)])  # open ground, nobody in cover
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 1),
        enemy_placements=[(ENEMIES_BY_ID["corp_sec"], (9, 1))], allies=[crew_stats_for()],
    )
    state.allies[0].coord = (7, 1)  # much closer to the enemy than the player is
    enemy = state.enemies[0]
    assert enemy_target(state, enemy) is state.allies[0]


def test_enemy_target_prefers_the_player_on_a_true_tie():
    grid = parse_grid(["." * 6 for _ in range(3)])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["corp_sec"], (5, 1))], allies=[crew_stats_for()],
    )
    state.player.coord, state.allies[0].coord = (0, 0), (0, 2)  # mirrored, equal distance
    enemy = state.enemies[0]
    assert chebyshev(enemy.coord, state.player.coord) == chebyshev(enemy.coord, state.allies[0].coord)
    assert enemy_target(state, enemy) is state.player


def test_enemy_target_is_none_with_nobody_left_standing():
    grid = parse_grid(["." * 6])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (5, 0))], allies=[crew_stats_for()],
    )
    state.character.health = 0
    state.allies[0].health = 0
    assert enemy_target(state, state.enemies[0]) is None


def test_an_enemy_attack_on_an_ally_costs_the_ally_health_not_the_player():
    grid = parse_grid(["....."])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["enforcer"], (4, 0))], allies=[crew_stats_for()],
    )
    ally = state.allies[0]
    ally.coord, state.player.coord = (3, 0), (0, 0)  # the hire is the only one in reach
    player_health, ally_health = state.character.health, ally.health
    end_turn(state, AlwaysSix())  # forced hits all round
    assert ally.health < ally_health
    assert state.character.health == player_health


def test_an_ally_attacks_on_its_own_phase_and_can_drop_an_enemy():
    grid = parse_grid(["....."])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (4, 0))], allies=[crew_stats_for()],
    )
    enemy_health = state.enemies[0].health
    end_turn(state, AlwaysSix())
    # The player did nothing this turn -- any damage on the thug came from the hire.
    assert not state.enemies or state.enemies[0].health < enemy_health
    assert any("Juncture" in line for line in state.log)


def test_a_downed_ally_does_not_end_the_fight():
    grid = parse_grid(["....."])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (4, 0))], allies=[crew_stats_for()],
    )
    state.allies[0].health = 0
    end_turn(state, random.Random(0))
    assert state.outcome is TacticalOutcome.ONGOING
    assert state.allies == []  # out of the fight, but the fight goes on


def test_the_player_can_never_target_their_own_crew():
    grid = parse_grid(["....."])
    character = Character(
        name="t", inventory=[InventoryItem(item_id="pipe_pistol", equipped=True)]
    )
    state = start_tactical(
        character, grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (4, 0))], allies=[crew_stats_for()],
    )
    ally = state.allies[0]
    assert enemy_at(state, ally.coord) is None       # no friendly fire, by construction
    assert ally not in attack_targets(state)
    assert not legal_attack_target(state, ally.coord)


# --- a downed hire: stabilizing, and what the fight's ending costs them ---


def _downed_ally_state(consumables=(), player_at=(1, 0)):
    """A finished-shape fight with one hire bleeding at (0, 0) and the player wherever
    the caller wants them relative to that."""
    grid = parse_grid(["." * 6, "." * 6])
    character = Character(name="t", consumables=list(consumables))
    state = start_tactical(
        character, grid, player_start=(0, 1),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (5, 1))], allies=[crew_stats_for()],
    )
    ally = state.downed_allies[0] if state.downed_allies else state.allies[0]
    ally.coord, ally.health = (0, 0), 0
    state.player.coord = player_at
    return state, ally


def test_stabilize_needs_a_kit_and_adjacency():
    state, ally = _downed_ally_state(player_at=(5, 0))  # kit-less and across the room
    assert stabilize_targets(state) == []

    state, ally = _downed_ally_state(consumables=["health_kit"], player_at=(5, 0))
    assert stabilize_targets(state) == []  # has the kit, too far to use it
    state.player.coord = (1, 0)
    assert stabilize_targets(state) == [ally]


def test_stabilize_spends_the_kit_and_the_action_but_leaves_them_down():
    state, ally = _downed_ally_state(consumables=["health_kit"])
    assert stabilize_ally(state) is None
    assert ally.stabilized
    assert ally.health == 0          # first aid, not a revival
    assert state.allies == []        # still out of this fight
    assert state.acted               # cost the turn
    assert state.character.consumables == []  # kit spent
    assert not state.character.health_kit_used_today  # patching someone else isn't your daily kit


def test_stabilize_refuses_once_the_action_is_spent_or_already_stable():
    state, ally = _downed_ally_state(consumables=["health_kit", "health_kit"])
    state.acted = True
    assert stabilize_ally(state) is not None
    assert state.character.consumables == ["health_kit", "health_kit"]  # nothing spent

    state.acted = False
    assert stabilize_ally(state) is None
    state.acted = False
    assert stabilize_ally(state) == "Nobody on your crew is down."  # stable already, no second kit
    assert state.character.consumables == ["health_kit"]


def test_a_stabilized_hire_always_walks_away_from_a_won_fight():
    state, ally = _downed_ally_state(consumables=["health_kit"])
    stabilize_ally(state)
    state.outcome = TacticalOutcome.VICTORY
    assert resolve_downed_crew(state, ForcedChance(0.99)) == [(ally.name, CrewFate.RECOVERED)]
    assert state.character.dead_runners == set()
    assert state.character.arrested_runners == {}


def test_an_unstabilized_hire_can_bleed_out_even_on_a_won_fight():
    state, ally = _downed_ally_state()
    state.outcome = TacticalOutcome.VICTORY
    assert resolve_downed_crew(state, ForcedChance(0.0)) == [(ally.name, CrewFate.KILLED)]
    assert ally.stats.id in state.character.dead_runners


def test_leaving_a_stabilized_hire_behind_gets_them_picked_up_not_killed():
    state, ally = _downed_ally_state(consumables=["health_kit"])
    stabilize_ally(state)
    state.outcome = TacticalOutcome.ESCAPED
    assert resolve_downed_crew(state, ForcedChance(0.0)) == [(ally.name, CrewFate.ARRESTED)]
    assert ally.stats.id not in state.character.dead_runners
    assert state.character.arrested_runners[ally.stats.id] == state.character.day + ARREST_DAYS


def test_a_killed_or_arrested_hire_comes_off_the_crew():
    for chance, expected in ((0.0, CrewFate.KILLED), (0.5, CrewFate.ARRESTED)):
        state, ally = _downed_ally_state()
        state.character.hire_for_job(ally.stats.id, "job_1")
        state.outcome = TacticalOutcome.ESCAPED
        assert resolve_downed_crew(state, ForcedChance(chance)) == [(ally.name, expected)]
        assert not state.character.on_crew(ally.stats.id)
        assert not state.character.runner_available(ally.stats.id)


def test_a_recovered_hire_stays_on_the_crew():
    state, ally = _downed_ally_state()
    state.character.hire_for_job(ally.stats.id, "job_1")
    state.outcome = TacticalOutcome.ESCAPED
    assert resolve_downed_crew(state, ForcedChance(0.99)) == [(ally.name, CrewFate.RECOVERED)]
    assert state.character.on_crew(ally.stats.id)
    assert state.character.runner_available(ally.stats.id)


def test_resolve_downed_crew_ignores_a_hire_who_stayed_up():
    grid = parse_grid(["." * 6])
    state = start_tactical(
        Character(name="t"), grid, player_start=(0, 0),
        enemy_placements=[(ENEMIES_BY_ID["thug"], (5, 0))], allies=[crew_stats_for()],
    )
    state.outcome = TacticalOutcome.ESCAPED
    assert resolve_downed_crew(state, ForcedChance(0.0)) == []


# --- generate_map invariants ---


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_map_is_the_configured_size(seed):
    tac = generate_map(random.Random(seed), enemy_count=2)
    assert tac.grid.width == TAC_MAP_WIDTH
    assert tac.grid.height == TAC_MAP_HEIGHT


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_map_places_every_requested_enemy(seed):
    tac = generate_map(random.Random(seed), enemy_count=3)
    assert len(tac.enemy_spawns) == 3


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_map_has_at_least_one_exit(seed):
    tac = generate_map(random.Random(seed), enemy_count=2)
    assert tac.exits


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_map_player_start_and_enemies_and_exits_are_all_walkable(seed):
    tac = generate_map(random.Random(seed), enemy_count=2)
    assert tac.grid.is_walkable(tac.player_start)
    for spawn in tac.enemy_spawns:
        assert tac.grid.is_walkable(spawn)
    for exit_cell in tac.exits:
        assert tac.grid.is_walkable(exit_cell)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_map_every_enemy_and_exit_reachable_from_player_start(seed):
    """The map generator retries until this holds -- verify it actually does."""
    tac = generate_map(random.Random(seed), enemy_count=2)
    for target in (*tac.enemy_spawns, *tac.exits):
        if target == tac.player_start:
            continue
        assert path_between(tac.grid, tac.player_start, target), (
            f"{target} unreachable from {tac.player_start} at seed {seed}"
        )


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_map_border_ring_stays_solid_wall(seed):
    tac = generate_map(random.Random(seed), enemy_count=2)
    grid = tac.grid
    for x in range(grid.width):
        assert grid.tile((x, 0)) is Tile.WALL
        assert grid.tile((x, grid.height - 1)) is Tile.WALL
    for y in range(grid.height):
        assert grid.tile((0, y)) is Tile.WALL
        assert grid.tile((grid.width - 1, y)) is Tile.WALL


# --- burglary: levels, standing a post, and what actually ends one ---


def _burglary(seed=7, guard_id="thug", entrances=2):
    """A real generated house with a live infiltration on its entry level."""
    building = generate_building(random.Random(seed), entrance_count=entrances)
    character = Character(name="t", inventory=[InventoryItem(item_id="pipe_pistol", equipped=True)])
    state = start_burglary(character, building, building.entrance_spawns[0], ENEMIES_BY_ID[guard_id])
    return building, state


def test_a_burglary_starts_quiet_with_its_guards_on_post():
    building, state = _burglary()
    assert not state.alarm
    assert state.outcome is TacticalOutcome.ONGOING
    everyone = [*state.units, *(u for units in state.off_level_units.values() for u in units)]
    assert [u.alerted for u in everyone if u.is_enemy] == [False] * len(building.guards)


def test_only_the_current_levels_units_are_on_the_board():
    building, state = _burglary()
    on_board = {(state.level_index, unit.coord) for unit in state.enemies}
    waiting = {(level, unit.coord) for level, units in state.off_level_units.items() for unit in units}
    assert on_board | waiting == set(building.guards)
    assert not on_board & waiting


def test_taking_the_stairs_swaps_the_level_and_costs_a_move():
    building, state = _burglary()
    stair = next(cell for link in building.links for level, cell in (link.a, link.b) if level == state.level_index)
    state.player.coord = stair
    destination = stairs_here(state)
    assert destination is not None
    moves = state.moves_left

    assert take_stairs(state)
    assert (state.level_index, state.player.coord) == destination
    assert state.grid is building.levels[state.level_index].grid
    assert state.moves_left == moves - 1


def test_explored_is_kept_separately_per_level():
    """A burglary's levels are separate grids (same DESIGN.md point buildings.py makes
    about Building not being a corpmap.Location) -- explored has to be keyed by level or
    a coordinate seen on one floor would wrongly read as "known" on another."""
    building, state = _burglary()
    entry_level = state.level_index
    entry_explored = set(state.explored[entry_level])
    assert entry_explored

    stair = next(cell for link in building.links for level, cell in (link.a, link.b) if level == entry_level)
    state.player.coord = stair
    destination_level, destination_coord = stairs_here(state)
    assert take_stairs(state)
    assert state.level_index == destination_level

    assert destination_level in state.explored
    assert state.explored[destination_level]
    # Walking upstairs didn't touch what was already known about the entry level, and
    # the two levels' explored sets aren't the same object or a match by coincidence.
    assert state.explored[entry_level] == entry_explored
    assert entry_level != destination_level


def test_a_guard_left_behind_is_still_there_when_you_come_back():
    """Levels are places, not a re-roll: who was where, and how they felt about it,
    survives you walking upstairs and back down."""
    building, state = _burglary()
    guard_level = next(level for level, _coord in building.guards)
    enter_level(state, guard_level, building.levels[guard_level].rooms[0].center)
    guard = state.enemies[0]
    guard.health -= 2
    hurt, where = guard.health, guard.coord

    other = next(i for i in range(len(building.levels)) if i != guard_level)
    enter_level(state, other, building.levels[other].rooms[0].center)
    assert not state.enemies  # they didn't come with you
    enter_level(state, guard_level, building.levels[guard_level].rooms[0].center)
    assert (state.enemies[0].health, state.enemies[0].coord) == (hurt, where)


def test_an_unalerted_guard_stands_its_post_instead_of_taking_a_turn():
    """The whole basis of sneaking: a guard who hasn't seen you doesn't act."""
    grid = parse_grid(["." * 12 for _ in range(3)])
    character = Character(name="t")
    state = start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(ENEMIES_BY_ID["thug"], (1, 0))]
    )
    guard = state.enemies[0]
    guard.alerted = False
    health, coord = character.health, guard.coord

    end_turn(state, AlwaysSix())  # forced hits, and adjacent -- they'd flatten you if they acted
    assert character.health == health
    assert guard.coord == coord


def test_walking_into_a_guards_sightline_raises_the_alarm_for_the_whole_building():
    building, state = _burglary()
    guard_level = next(level for level, _coord in building.guards)
    guard_coord = next(coord for level, coord in building.guards if level == guard_level)
    enter_level(state, guard_level, guard_coord)  # nose to nose with them
    assert check_detection(state)
    assert state.alarm
    everyone = [*state.units, *(u for units in state.off_level_units.values() for u in units)]
    assert all(unit.alerted for unit in everyone if unit.is_enemy)


def test_staying_out_of_sight_keeps_you_undetected():
    building, state = _burglary()
    # Standing where they aren't: nobody on this level, so nobody sees anything.
    assert not any(unit.is_enemy for unit in state.units) or not check_detection(state)
    assert not state.alarm


def test_taking_a_shot_gives_you_away_even_if_nobody_saw_you():
    grid = parse_grid(["." * 12 for _ in range(3)])
    character = Character(name="t", inventory=[InventoryItem(item_id="pipe_pistol", equipped=True)])
    state = start_tactical(
        character, grid, player_start=(0, 0), enemy_placements=[(ENEMIES_BY_ID["thug"], (4, 0))]
    )
    guard = state.enemies[0]
    guard.alerted = False
    begin_attack_aim(state)
    confirm_attack_aim(state, AlwaysSix())
    assert state.alarm
    assert guard.health <= 0 or guard.alerted


def test_reaching_the_score_ends_the_burglary_secured():
    building, state = _burglary()
    enter_level(state, *building.objective)
    _settle(state)
    assert state.outcome is TacticalOutcome.SECURED


def test_clearing_every_guard_does_not_end_a_burglary():
    """You came for the score, not the bodies -- an empty house is a quiet house, and
    the job is still unfinished."""
    building, state = _burglary()
    for units in state.off_level_units.values():
        for unit in units:
            unit.health = 0
    for unit in state.units:
        if unit.is_enemy:
            unit.health = 0
    _settle(state)
    assert state.outcome is TacticalOutcome.ONGOING


def test_the_way_you_came_in_is_a_way_out():
    building, state = _burglary()
    assert state.player.coord in state.exits
    assert leave(state)
    assert state.outcome is TacticalOutcome.ESCAPED


# --- burglary: locked doors and cameras ---


def test_a_camera_raises_the_alarm_without_any_guard_seeing_you():
    """Building.cameras isn't a Unit -- it's not gated on being unalerted, and there's no
    fighting or sneaking past one by knocking it out."""
    building = generate_building(random.Random(1), entrance_count=2, kind=BuildingKind.OFFICE)
    character = Character(name="t", inventory=[InventoryItem(item_id="pipe_pistol", equipped=True)])
    state = start_burglary(character, building, building.entrance_spawns[0], ENEMIES_BY_ID["thug"])
    assert building.cameras  # OFFICE always carries at least one
    for units in state.off_level_units.values():
        for unit in units:
            unit.health = 0
    for unit in state.units:
        if unit.is_enemy:
            unit.health = 0
    camera_level, camera_coord = building.cameras[0]
    enter_level(state, camera_level, camera_coord)  # nose to nose with the camera
    assert check_detection(state)
    assert state.alarm
    assert "camera" in state.log[-1].lower()


def test_stepping_into_a_pickable_locked_door_opens_it_and_moves_you_through():
    building, state = _burglary()
    dest = next(iter(legal_moves(state)))
    state.character.skill_ranks["hack"] = 0
    state.character.intelligence = 6  # comfortably clears a difficulty-9 lock (opposing pool 0)
    state.building.locks[(state.level_index, dest)] = Lock(skill="hack", difficulty=9)
    moves = state.moves_left

    assert lock_at(state, dest) is not None
    assert move_player(state, dest, AlwaysSix())
    assert state.player.coord == dest
    assert lock_at(state, dest) is None
    assert state.moves_left == moves - 1


def test_a_failed_lock_pick_leaves_it_locked_and_still_costs_the_move():
    building, state = _burglary()
    dest = next(iter(legal_moves(state)))
    state.character.skill_ranks["hack"] = 0
    state.character.intelligence = 0  # a difficulty-9 lock still has an opposing pool of 0,
    state.building.locks[(state.level_index, dest)] = Lock(skill="hack", difficulty=9)
    moves = state.moves_left

    # 0 successes either side is a plain FAILURE (not critical), so the alarm is only the
    # ordinary chance -- keep the roll just above it to isolate "failed, no alarm" here.
    assert move_player(state, dest, ForcedChance(LOCK_FAILURE_ALARM_CHANCE + 0.01))
    assert state.player.coord != dest
    assert lock_at(state, dest) is not None
    assert state.moves_left == moves - 1
    assert not state.alarm


def test_a_failed_lock_pick_can_still_trip_the_alarm():
    building, state = _burglary()
    dest = next(iter(legal_moves(state)))
    state.character.skill_ranks["hack"] = 0
    state.character.intelligence = 0
    state.building.locks[(state.level_index, dest)] = Lock(skill="hack", difficulty=9)

    assert move_player(state, dest, ForcedChance(LOCK_FAILURE_ALARM_CHANCE - 0.01))
    assert state.alarm


def test_a_critical_failure_lock_pick_always_trips_the_alarm():
    """A high enough difficulty against a 0-pool character is a guaranteed critical
    failure (AlwaysSix forces every opposing die to hit too) -- and that always goes
    loud, same as a burglary entrance's own critical failure, whatever the ordinary
    LOCK_FAILURE_ALARM_CHANCE roll would have said."""
    building, state = _burglary()
    dest = next(iter(legal_moves(state)))
    state.character.skill_ranks["hack"] = 0
    state.character.intelligence = 0
    state.building.locks[(state.level_index, dest)] = Lock(skill="hack", difficulty=21)

    assert move_player(state, dest, AlwaysSix())
    assert state.alarm
    assert lock_at(state, dest) is not None  # still locked -- only a passed check clears it


def test_attempt_lock_is_what_move_player_dispatches_to():
    """move_player's locked-door branch is attempt_lock, called directly here so a
    regression in the dispatch (e.g. move_player stops checking lock_at) shows up as a
    behavioral difference between the two, not just a passing test either way."""
    building, state = _burglary()
    dest = next(iter(legal_moves(state)))
    state.character.skill_ranks["hack"] = 0
    state.character.intelligence = 6
    lock = Lock(skill="hack", difficulty=9)
    state.building.locks[(state.level_index, dest)] = lock

    attempt_lock(state, dest, lock, AlwaysSix())
    assert state.player.coord == dest
    assert lock_at(state, dest) is None
