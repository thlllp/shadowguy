"""Tests for tactical.py: grid primitives, LOS/range gating, attack resolution,
enemy-phase movement/combat, and generated-map invariants."""

import random

import pytest

from shadowguy.character import Character
from shadowguy.checks import pool_for_difficulty
from shadowguy.combat import ENEMIES_BY_ID, player_defense
from shadowguy.shops import ITEMS_BY_ID, InventoryItem
from shadowguy.tactical import (
    ENEMY_SPEED,
    FIREARM_RANGE,
    FULL_COVER,
    GRENADE_RADIUS,
    GRENADE_RANGE,
    HALF_COVER,
    MELEE_RANGE,
    TAC_MAP_HEIGHT,
    TAC_MAP_WIDTH,
    TacticalOutcome,
    Tile,
    available_grenades,
    begin_grenade_aim,
    best_shot,
    cancel_grenade_aim,
    chebyshev,
    confirm_grenade_aim,
    cover_bonus,
    end_turn,
    generate_map,
    has_line_of_sight,
    leave,
    legal_grenade_target,
    legal_moves,
    move_aim_cursor,
    move_player,
    parse_grid,
    path_between,
    player_attack,
    player_weapons,
    start_tactical,
    step_neighbors,
    targets_for,
    throw_grenade,
    visible_tiles,
    weapon_range,
)

from helpers import AlwaysOne, AlwaysSix

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


def test_visible_tiles_agrees_with_has_line_of_sight_per_cell():
    grid = parse_grid(["...", "###", "..."])
    vis = visible_tiles(grid, (0, 0))
    assert bool(vis[0][0]) == has_line_of_sight(grid, (0, 0), (0, 0))
    assert bool(vis[0][2]) == has_line_of_sight(grid, (0, 0), (2, 0))  # open, same row
    assert bool(vis[2][0]) == has_line_of_sight(grid, (0, 0), (0, 2))  # blocked by wall row


def test_step_neighbors_excludes_walls_and_out_of_bounds():
    grid = parse_grid(["###", "#.#", "###"])
    assert step_neighbors(grid, (1, 1)) == []  # boxed in on every side


def test_step_neighbors_excludes_blocked_cells():
    grid = parse_grid(["..."])
    neighbors = step_neighbors(grid, (1, 0), blocked=frozenset({(0, 0)}))
    assert (0, 0) not in neighbors
    assert (2, 0) in neighbors


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


def test_cancel_grenade_aim_spends_nothing():
    state = _simple_state()
    state.character.consumables.append("grenade_frag")
    index, _ = available_grenades(state)[0]
    begin_grenade_aim(state, index)
    cancel_grenade_aim(state)
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
    assert "hit" in state.log[-1]


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
    _weapon, target = best_shot(state)
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
