"""Tests for grid.py: the geometry — walkability, LOS, pathing, distance.

Everything a fight does *with* this geometry (cover, range gating, movement budgets)
is test_tactical.py; a Level carved out of it is test_buildings.py.
"""

from shadowguy.grid import (
    Tile,
    chebyshev,
    has_line_of_sight,
    parse_grid,
    path_between,
    step_neighbors,
    visible_tiles,
)


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


def test_step_neighbors_offers_all_eight_directions():
    grid = parse_grid(["...", "...", "..."])
    assert set(step_neighbors(grid, (1, 1))) == {
        (1, 0),
        (1, 2),
        (0, 1),
        (2, 1),
        (0, 0),
        (2, 0),
        (0, 2),
        (2, 2),
    }


def test_path_between_takes_the_diagonal():
    """Three cells over and three down is three king moves, not six."""
    grid = parse_grid(["....", "....", "....", "...."])
    assert path_between(grid, (0, 0), (3, 3)) == [(1, 1), (2, 2), (3, 3)]
