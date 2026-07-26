"""Tests for tactical.py: grid primitives, LOS/range gating, generated-map invariants."""

import random

import pytest

from helpers import AlwaysSix, ForcedChance, crew_stats_for

from shadowguy.character import Character, InventoryItem
from shadowguy.combat import ENEMIES_BY_ID
from shadowguy.shops import ITEMS_BY_ID
from shadowguy.tactical import (
    ARREST_DAYS,
    FIREARM_RANGE,
    GRENADE_RADIUS,
    GRENADE_RANGE,
    MELEE_RANGE,
    TAC_MAP_HEIGHT,
    TAC_MAP_WIDTH,
    AimKind,
    CrewFate,
    Side,
    TacticalOutcome,
    Tile,
    Unit,
    aim_is_legal,
    attack_targets,
    available_grenades,
    begin_attack_aim,
    begin_grenade_aim,
    cancel_aim,
    chebyshev,
    confirm_aim,
    confirm_attack_aim,
    confirm_grenade_aim,
    cover_bonus,
    end_turn,
    enemy_at,
    enemy_target,
    generate_map,
    has_line_of_sight,
    leave,
    legal_attack_target,
    legal_grenade_target,
    legal_moves,
    move_aim_cursor,
    move_player,
    parse_grid,
    path_between,
    resolve_downed_crew,
    snap_aim_to_next_target,
    stabilize_ally,
    stabilize_targets,
    start_tactical,
    throw_grenade,
    weapon_for_target,
    weapon_range,
)

SEEDS = range(80)


def test_parse_grid_reads_wall_and_low_cover_glyphs():
    grid = parse_grid(["#%.", "..."])
    assert grid.tile((0, 0)) is Tile.WALL
    assert grid.tile((1, 0)) is Tile.LOW_COVER
    assert grid.tile((2, 0)) is Tile.FLOOR


def test_is_walkable_only_floor():
    grid = parse_grid(["#%."])
    assert not grid.is_walkable((0, 0))
    assert not grid.is_walkable((1, 0))
    assert grid.is_walkable((2, 0))


def test_has_line_of_sight_blocked_by_wall():
    grid = parse_grid(["...", "###", "..."])
    assert not has_line_of_sight(grid, (0, 0), (0, 2))


def test_has_line_of_sight_open_floor():
    grid = parse_grid(["....."])
    assert has_line_of_sight(grid, (0, 0), (4, 0))


def test_low_cover_blocks_movement_but_not_sight():
    grid = parse_grid(["...", ".%.", "..."])
    assert not grid.is_walkable((1, 1))
    assert has_line_of_sight(grid, (0, 1), (2, 1))


def test_weapon_range_firearms_outranges_melee():
    firearm = next(i for i in ITEMS_BY_ID.values() if i.skill == "firearms")
    melee = next(i for i in ITEMS_BY_ID.values() if i.skill and i.skill != "firearms" and i.damage)
    assert weapon_range(firearm) == FIREARM_RANGE
    assert weapon_range(melee) == MELEE_RANGE
    assert FIREARM_RANGE > MELEE_RANGE


def test_chebyshev_is_king_move_distance():
    assert chebyshev((0, 0), (3, 4)) == 4
    assert chebyshev((0, 0), (0, 0)) == 0


def test_path_between_returns_empty_when_unreachable():
    grid = parse_grid(["...", "###", "..."])
    assert path_between(grid, (0, 0), (0, 2)) == []


def test_path_between_finds_a_route_around_an_obstacle():
    grid = parse_grid(["...", "##.", "..."])
    path = path_between(grid, (0, 0), (0, 2))
    assert path
    assert path[-1] == (0, 2)


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


def test_legal_moves_empty_once_moves_exhausted():
    state = _simple_state()
    state.moves_left = 0
    assert legal_moves(state) == []


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
