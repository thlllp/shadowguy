"""Smoke-test the GUI draw paths without a display.

Arcade's draw calls are redirected to counters and `Text` to a no-op, so this
catches missing attributes/typos in the renderer. Skipped without the `gui` group.
"""

import pytest

pytest.importorskip("arcade")

import arcade  # noqa: E402

from shadowguy.gui.session import GameSession  # noqa: E402
from shadowguy.gui.views import FixerView, InventoryView, MapView, TacticalView  # noqa: E402
from shadowguy.gui.window import ShadowguyWindow  # noqa: E402


def test_map_and_menu_draw_paths(monkeypatch):
    calls = []

    for name in (
        "draw_lbwh_rectangle_filled",
        "draw_lbwh_rectangle_outline",
        "draw_line",
        "draw_circle_filled",
        "draw_circle_outline",
    ):
        monkeypatch.setattr(arcade, name, lambda *args, **kwargs: calls.append(name))

    class DummyText:
        def __init__(self, *args, **kwargs):
            pass

        def draw(self):
            calls.append("text")

    monkeypatch.setattr(arcade, "Text", DummyText)

    window = ShadowguyWindow.__new__(ShadowguyWindow)
    window.session = GameSession(seed=1)
    window._width = 1280
    window._height = 860
    window.selected_id = window.session.character.location_id
    window.seed = window.session.seed
    window.views = []

    window.draw_map_screen("smoke")
    MapView(window).draw()
    InventoryView(window).draw()

    fixer = next(f for f in window.session.app.fixers if f.offers)
    FixerView(window, fixer).draw()

    from shadowguy.combat import ENEMIES_BY_ID
    from shadowguy.grid import parse_grid
    from shadowguy.scene import Outcome, TacticalStage

    stage = TacticalStage(
        prompt="Smoke.",
        grid=parse_grid(["." * 9] * 9),
        player_start=(4, 4),
        enemies=((ENEMIES_BY_ID["thug"], (8, 0)),),
        victory=Outcome(text=""),
        escape=Outcome(text=""),
        exits=frozenset({(0, 8)}),
    )
    TacticalView.from_stage(window, stage, lambda outcome: None).draw()

    assert calls
