"""Smoke-test the GUI draw paths without a display.

Arcade's draw calls are redirected to counters and `Text` to a no-op, so this
catches missing attributes/typos in the renderer. Skipped without the `gui` group.
"""

import pytest

pytest.importorskip("arcade")

import arcade  # noqa: E402

from shadowguy.gui.session import GameSession  # noqa: E402
from shadowguy.gui.views import (  # noqa: E402
    FactionPickView,
    FixerView,
    InventoryView,
    MapView,
    StartView,
    TacticalView,
)
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

    from shadowguy.gangs import GANGS

    window.session.take_smuggling_job(GANGS[0].id)

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


def test_map_footer_does_not_promise_wasd_movement(monkeypatch):
    """S opens shops on the map, so a footer advertising "Arrows/WASD move" is
    contradictory. The arrow keys are the documented movement path."""
    texts = []

    monkeypatch.setattr(arcade, "draw_line", lambda *args, **kwargs: None)

    class RecordingText:
        def __init__(self, value, *args, **kwargs):
            texts.append(value)

        def draw(self):
            pass

    monkeypatch.setattr(arcade, "Text", RecordingText)

    window = ShadowguyWindow.__new__(ShadowguyWindow)
    window._width = 1280
    window._height = 860
    window.session = GameSession(seed=1)
    window._draw_footer(56, None)

    footer = next(text for text in texts if "Enter travel" in text)
    assert "WASD" not in footer
    assert "S shops" in footer


def test_start_and_faction_views_draw(monkeypatch):
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

    StartView(window).draw()
    FactionPickView(window).draw()

    assert calls


def test_corp_map_and_actions_draw_paths(monkeypatch):
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

    from shadowguy.factions import FACTIONS
    from shadowguy.gui.views import CorpActionsView

    window = ShadowguyWindow.__new__(ShadowguyWindow)
    window.session = GameSession(seed=1)
    window.session.start_corp(FACTIONS[0].id)
    window._width = 1280
    window._height = 860
    window.selected_id = window.session.character.location_id
    window.seed = window.session.seed
    window.views = []
    window.map_view = MapView(window)

    window.draw_map_screen("smoke")
    window.map_view.draw()
    CorpActionsView(window, window.map_view).draw()

    assert calls
