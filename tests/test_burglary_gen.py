"""Tests for tactical.py's Burglary additions: generate_building invariants and the
BurglaryWalkState movement/detection primitives."""

import random

import pytest

from shadowguy.tactical import (
    BURGLARY_GUARD_COUNT,
    GUARD_SIGHT_RANGE,
    TAC_MAP_HEIGHT,
    TAC_MAP_WIDTH,
    BurglaryWalkState,
    Tile,
    generate_building,
    move_walker,
    parse_grid,
    path_between,
    reached_objective,
    spotted,
)

SEEDS = range(80)


# --- generate_building invariants ---


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_is_the_configured_size(seed):
    layout = generate_building(random.Random(seed), entrance_count=3)
    assert layout.grid.width == TAC_MAP_WIDTH
    assert layout.grid.height == TAC_MAP_HEIGHT


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_places_every_requested_entrance(seed):
    layout = generate_building(random.Random(seed), entrance_count=3)
    assert len(layout.entrance_spawns) == 3


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_entrances_and_objective_are_all_distinct(seed):
    layout = generate_building(random.Random(seed), entrance_count=3)
    cells = [*layout.entrance_spawns, layout.objective, *layout.guards]
    assert len(cells) == len(set(cells))


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_entrances_objective_and_guards_are_all_walkable(seed):
    layout = generate_building(random.Random(seed), entrance_count=3)
    assert layout.grid.is_walkable(layout.objective)
    for spawn in layout.entrance_spawns:
        assert layout.grid.is_walkable(spawn)
    for guard in layout.guards:
        assert layout.grid.is_walkable(guard)


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_every_entrance_reaches_the_objective(seed):
    """The generator retries until this holds -- verify it actually does."""
    layout = generate_building(random.Random(seed), entrance_count=3)
    for spawn in layout.entrance_spawns:
        assert path_between(layout.grid, spawn, layout.objective), (
            f"{spawn} cannot reach objective {layout.objective} at seed {seed}"
        )


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_guard_count_never_exceeds_the_configured_constant(seed):
    layout = generate_building(random.Random(seed), entrance_count=3)
    assert len(layout.guards) <= BURGLARY_GUARD_COUNT


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_building_border_ring_stays_solid_wall(seed):
    layout = generate_building(random.Random(seed), entrance_count=3)
    grid = layout.grid
    for x in range(grid.width):
        assert grid.tile((x, 0)) is Tile.WALL
        assert grid.tile((x, grid.height - 1)) is Tile.WALL
    for y in range(grid.height):
        assert grid.tile((0, y)) is Tile.WALL
        assert grid.tile((grid.width - 1, y)) is Tile.WALL


@pytest.mark.parametrize("seed", range(30))
def test_generated_building_handles_a_two_entrance_pool(seed):
    """Burglary's smallest realistic draw (PARTIAL_POOL_SIZE) -- fewer rooms needed,
    should never be harder to satisfy than the 3-entrance case above."""
    layout = generate_building(random.Random(seed), entrance_count=2)
    assert len(layout.entrance_spawns) == 2
    for spawn in layout.entrance_spawns:
        assert path_between(layout.grid, spawn, layout.objective)


# --- BurglaryWalkState: movement and guard detection ---


def _simple_state(guards=()):
    grid = parse_grid(["......", "......", "......"])
    return BurglaryWalkState(grid=grid, position=(0, 0), objective=(5, 2), guards=guards)


def test_move_walker_rejects_illegal_step():
    state = _simple_state()
    assert not move_walker(state, (5, 5))  # not adjacent
    assert state.position == (0, 0)


def test_move_walker_accepts_legal_step():
    state = _simple_state()
    assert move_walker(state, (1, 0))
    assert state.position == (1, 0)


def test_reached_objective_only_at_the_objective_cell():
    state = _simple_state()
    assert not reached_objective(state)
    state.position = state.objective
    assert reached_objective(state)


def test_spotted_false_with_no_guards():
    state = _simple_state()
    assert not spotted(state)


def test_spotted_true_within_range_and_sight():
    state = _simple_state(guards=((2, 0),))
    state.position = (3, 0)  # chebyshev distance 1, open floor
    assert spotted(state)


def test_spotted_false_beyond_guard_sight_range():
    grid = parse_grid(["." * (GUARD_SIGHT_RANGE * 2 + 2)])
    state = BurglaryWalkState(grid=grid, position=(GUARD_SIGHT_RANGE * 2, 0), objective=(0, 0), guards=((0, 0),))
    assert not spotted(state)


def test_spotted_false_when_a_wall_blocks_the_sightline():
    grid = parse_grid(["...", "###", "..."])
    state = BurglaryWalkState(grid=grid, position=(0, 2), objective=(0, 0), guards=((0, 0),))
    assert not spotted(state)
