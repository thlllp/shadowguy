"""Tests for tactical.py: grid primitives, LOS/range gating, generated-map invariants."""

import random

import pytest

from shadowguy.character import Character
from shadowguy.combat import ENEMIES_BY_ID
from shadowguy.shops import ITEMS_BY_ID
from shadowguy.tactical import (
    FIREARM_RANGE,
    GRENADE_RADIUS,
    GRENADE_RANGE,
    MELEE_RANGE,
    TAC_MAP_HEIGHT,
    TAC_MAP_WIDTH,
    TacticalOutcome,
    Tile,
    available_grenades,
    begin_grenade_aim,
    cancel_grenade_aim,
    chebyshev,
    confirm_grenade_aim,
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
    start_tactical,
    throw_grenade,
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
