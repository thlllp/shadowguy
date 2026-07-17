"""Tests for tactical.py: grid primitives, LOS/range gating, generated-map invariants."""

import random

import pytest

from shadowguy.character import Character
from shadowguy.combat import ENEMIES_BY_ID
from shadowguy.shops import ITEMS_BY_ID
from shadowguy.tactical import (
    FIREARM_RANGE,
    MELEE_RANGE,
    TAC_MAP_HEIGHT,
    TAC_MAP_WIDTH,
    Tile,
    chebyshev,
    generate_map,
    has_line_of_sight,
    leave,
    legal_moves,
    move_player,
    parse_grid,
    path_between,
    start_tactical,
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
