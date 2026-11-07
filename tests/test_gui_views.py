"""Headless checks for the GUI views' state logic.

Views only touch the window for `session`, `push`/`pop` and (during draw) the
renderer, so a small fake window exercises the input/state paths without a
display. Skipped when the optional `gui` group is not installed.
"""

import pytest

pytest.importorskip("arcade")

import arcade  # noqa: E402

from shadowguy.corpmap import SHOP_KINDS  # noqa: E402
from shadowguy.gui.session import GameSession  # noqa: E402
from shadowguy.gui.views import (  # noqa: E402
    FixerView,
    InventoryView,
    MapView,
    SceneView,
    ShopView,
)
from shadowguy.jobs import generate_job  # noqa: E402


class FakeWindow:
    def __init__(self, seed=1):
        self.session = GameSession(seed=seed)
        self.width = 1280
        self.height = 860
        self.views = []
        self.selected_id = self.session.character.location_id

    def push(self, view):
        self.views.append(view)

    def pop(self):
        if self.views:
            self.views.pop()

    def clock(self):
        return "Day 1"


def test_map_view_travel_reaches_a_connected_territory():
    window = FakeWindow()
    view = MapView(window)
    start = view.selected_id
    target = window.session.territory(start).connections[0]

    view.selected_id = target
    view._travel()

    assert window.session.character.location_id == target


def test_fixer_view_lists_offers():
    window = FakeWindow()
    fixer = next(f for f in window.session.app.fixers if f.offers)
    view = FixerView(window, fixer)
    assert view.rows
    assert all(row_id.startswith(("offer:", "sec:")) for row_id, _ in view.rows)


def test_inventory_view_builds_for_the_starting_character():
    window = FakeWindow()
    view = InventoryView(window)
    assert isinstance(view.rows, list)


def test_shop_view_builds_at_a_real_shop():
    window = FakeWindow()
    shop = next(
        location
        for territory in window.session.corp_map.territories.values()
        for location in territory.locations
        if location.kind in SHOP_KINDS
    )
    view = ShopView(window, shop)
    assert view.rows


def test_scene_view_resolves_a_choice_and_can_advance():
    window = FakeWindow()
    scene, _timing = generate_job(1, window.session.corp_map, fixer_id="test", rng=window.session.rng)
    view = SceneView(window, scene, lambda: None)
    assert view.rows
    view.pick(view.rows[0][0])
    assert view.awaiting
    view.pick("continue")


def _job_with_stage(kind: str):
    import random

    from shadowguy.jobs import generate_job

    window = FakeWindow()
    for seed in range(60):
        scene, _timing = generate_job(1, window.session.corp_map, fixer_id="test", rng=random.Random(seed))
        if any(getattr(stage, kind) is not None for stage in scene.stages.values()):
            return window, scene
    pytest.skip(f"no generated job had a {kind} stage")


def test_scene_view_pushes_a_tactical_view_for_a_tactics_choice():
    from shadowguy.gui.views import TacticalView

    window, scene = _job_with_stage("tactical")
    view = SceneView(window, scene, lambda: None)
    stage = view._stage()
    tactics = next(
        (i for i, choice in enumerate(stage.choices) if "Tactics" in choice.label), None
    )
    if tactics is None:
        pytest.skip("first stage has no Tactics choice")
    view.pick(f"choice:{tactics}")
    view.pick("continue")
    assert isinstance(window.views[-1], TacticalView)
    # A turn can be taken without a display.
    window.views[-1].on_key(arcade.key.E, 0)


def test_scene_view_pushes_a_matrix_view_for_a_matrix_stage():
    from shadowguy.gui.views import MatrixView

    window, scene = _job_with_stage("matrix")
    view = SceneView(window, scene, lambda: None)
    stage = view._stage()
    tactics = next(
        (i for i, choice in enumerate(stage.choices) if "Tactics" in choice.label), None
    )
    if tactics is None:
        pytest.skip("first stage has no Tactics choice")
    view.pick(f"choice:{tactics}")
    view.pick("continue")
    assert isinstance(window.views[-1], MatrixView)
