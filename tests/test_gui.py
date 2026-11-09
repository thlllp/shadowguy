"""Tests for the Arcade front-end's non-rendering pieces.

The window itself needs a display; these tests drive the shared session and the
travel formula headlessly. The Arcade import is skipped when the optional `gui`
group is not installed (CI's dev env).
"""

from shadowguy.app import ShadowguyApp
from shadowguy.travel import travel_hours


def test_travel_hours_is_the_shared_formula():
    app = ShadowguyApp(seed=1)
    assert travel_hours(app.character) > 0


def test_session_travel_hop_drives_the_shared_game_state():
    import pytest

    pytest.importorskip("arcade")
    from shadowguy.gui.session import GameSession

    session = GameSession(seed=1)
    start = session.character.location_id
    target_id = session.territory(start).connections[0]
    before = session.character.elapsed_hours

    session.travel_hop(target_id)

    assert session.character.location_id == target_id
    assert session.character.elapsed_hours > before
