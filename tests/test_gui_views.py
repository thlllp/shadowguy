"""Headless checks for the GUI views' state logic.

Views only touch the window for `session`, `push`/`pop` and (during draw) the
renderer, so a small fake window exercises the input/state paths without a
display. Skipped when the optional `gui` group is not installed.
"""

import random
import types

import pytest

pytest.importorskip("arcade")

import arcade  # noqa: E402

from shadowguy.abstract_combat import CombatOutcome  # noqa: E402
from shadowguy.combat import Drop  # noqa: E402
from shadowguy.corpmap import (  # noqa: E402
    LocationKind,
    SHOP_KINDS,
    expansion_candidates,
    unowned_territory_ids,
)
from shadowguy.encounters import (  # noqa: E402
    ARREST_CASH_PCT,
    ARREST_HOURS,
    CorpEncounter,
    GangEncounter,
    gang_attack,
)
from shadowguy.factions import FACTIONS  # noqa: E402
from shadowguy.gangs import GANGS  # noqa: E402
from shadowguy.gui.session import GameSession, HopEncounter  # noqa: E402
from shadowguy.gui.views import (  # noqa: E402
    CombatView,
    CorpActionsView,
    EntranceView,
    FactionPickView,
    FixerView,
    ForcePickView,
    GameOverView,
    InventoryView,
    JobsView,
    MapView,
    ResearchTreeView,
    SceneView,
    ShopView,
    StartView,
    TacticalView,
    TollView,
)
from shadowguy.jobs import generate_job  # noqa: E402
from shadowguy.scene import Outcome  # noqa: E402
from helpers import AlwaysOne, AlwaysSix, force_outcome  # noqa: E402


class FakeWindow:
    def __init__(self, seed=1):
        self.session = GameSession(seed=seed)
        self.width = 1280
        self.height = 860
        self.seed = self.session.seed
        self.views = []
        self.map_view = None
        self.selected_id = self.session.character.location_id

    def push(self, view):
        self.views.append(view)

    def pop(self):
        if self.views:
            self.views.pop()

    def show_map(self):
        self.seed = self.session.seed
        self.selected_id = self.session.character.location_id
        self.map_view = MapView(self)
        self.views = [self.map_view]

    def start_runner(self):
        self.show_map()

    def start_corp(self, faction_id):
        self.session.start_corp(faction_id)
        self.show_map()

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


def _job_starting_with(kind: str):
    window = FakeWindow()
    for seed in range(200):
        scene, _timing = generate_job(
            1, window.session.corp_map, fixer_id="test", rng=random.Random(seed)
        )
        if getattr(scene.stages[scene.start_stage], kind) is not None:
            return window, scene
    pytest.skip(f"no generated job started with a {kind} stage")


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


def _forced_combat(window, callback):
    """A CombatView whose outcome has been pinned, for testing what the callback
    does without rolling a fight. Mirrors the real stack: [map, combat]."""
    fight = gang_attack(GANGS[0], random.Random(0))
    view = CombatView(window, fight, Drop.ENEMY, callback)
    window.views.append(view)
    return view


def _finish(window, combat, outcome):
    force_outcome(combat.state, outcome)
    combat.on_key(arcade.key.ENTER, 0)


def test_toll_fight_death_ends_the_run():
    window = FakeWindow()
    map_view = MapView(window)
    window.views = [map_view]
    toll = TollView(window, HopEncounter("gang", GangEncounter(GANGS[0], -1, 50)), map_view)
    combat = _forced_combat(window, toll._after_fight)

    _finish(window, combat, CombatOutcome.DEAD)

    assert isinstance(window.views[-1], GameOverView)
    # A dead run has no escape back to the map.
    window.views[-1].cancel()
    assert isinstance(window.views[-1], GameOverView)


def test_toll_fight_knockout_mugs_a_gang_fight_instead_of_ending_it():
    window = FakeWindow()
    window.session.app.rng = AlwaysSix()  # every wake-up roll is 6: you come to
    map_view = MapView(window)
    window.views = [map_view]
    toll = TollView(window, HopEncounter("gang", GangEncounter(GANGS[0], -1, 50)), map_view)
    combat = _forced_combat(window, toll._after_fight)
    character = window.session.character
    cash = character.cash

    _finish(window, combat, CombatOutcome.KNOCKED_OUT)

    assert window.views[-1] is map_view
    assert character.cash == cash // 2
    assert character.health == 1
    assert "alley" in map_view.message


def test_toll_fight_knockout_is_an_arrest_for_corp_security():
    window = FakeWindow()
    window.session.app.rng = AlwaysSix()
    map_view = MapView(window)
    faction = FACTIONS[0]
    encounter = CorpEncounter(faction, -1, "Test District", fine=50)
    toll = TollView(window, HopEncounter("corp", encounter), map_view)
    combat = _forced_combat(window, toll._after_fight)
    character = window.session.character
    cash = character.cash
    hours = character.elapsed_hours

    _finish(window, combat, CombatOutcome.KNOCKED_OUT)

    unowned = unowned_territory_ids(window.session.corp_map)
    assert character.cash == cash - int(cash * ARREST_CASH_PCT)
    assert character.elapsed_hours == hours + ARREST_HOURS
    assert character.location_id == (unowned[0] if unowned else window.session.corp_map.player_start_id)
    assert character.standing_with(faction.id) < 0
    assert "holds you for hours" in map_view.message


def test_toll_fight_knockout_can_end_the_run():
    window = FakeWindow()
    window.session.app.rng = AlwaysOne()  # every wake-up roll is 1: you don't
    map_view = MapView(window)
    window.views = [map_view]
    toll = TollView(window, HopEncounter("gang", GangEncounter(GANGS[0], -1, 50)), map_view)
    combat = _forced_combat(window, toll._after_fight)

    _finish(window, combat, CombatOutcome.KNOCKED_OUT)

    assert isinstance(window.views[-1], GameOverView)


def test_scene_fight_knockout_pays_the_loss_not_the_victory():
    window = FakeWindow()
    window.session.app.rng = AlwaysSix()
    scene, _timing = generate_job(
        1, window.session.corp_map, fixer_id="test", rng=random.Random(0)
    )
    map_view = MapView(window)
    window.views = [map_view]
    view = SceneView(window, scene, window.pop)
    window.views = [map_view, view]
    victory = Outcome(text="WIN", cash_delta=999, experience_delta=99)
    view._stage = lambda: types.SimpleNamespace(
        tactical=types.SimpleNamespace(victory=victory), combat=None
    )
    combat = _forced_combat(window, view._after_fight)
    character = window.session.character
    character.accepted_jobs.append(types.SimpleNamespace(scene=scene))
    cash = character.cash

    _finish(window, combat, CombatOutcome.KNOCKED_OUT)

    assert window.views == [map_view]
    assert character.cash == cash // 2
    assert character.health == 1
    assert scene.id not in [job.scene.id for job in character.accepted_jobs]


def test_jobs_view_opens_the_entrance_picker_for_a_burglary_start():
    window, scene = _job_starting_with("burglary")
    map_view = MapView(window)
    window.views = [map_view]
    jobs_view = JobsView(window, [types.SimpleNamespace(id="job_test", scene=scene)])
    window.views.append(jobs_view)

    jobs_view.pick("job_test")

    assert isinstance(window.views[-2], SceneView)
    assert isinstance(window.views[-1], EntranceView)
    assert window.views[-1].rows


def test_burglary_ambush_routes_straight_to_the_fight():
    from shadowguy.gui.views import TacticalView

    window, scene = _job_starting_with("burglary")
    map_view = MapView(window)
    window.views = [map_view]
    jobs_view = JobsView(window, [types.SimpleNamespace(id="job_test", scene=scene)])
    window.views.append(jobs_view)
    jobs_view.pick("job_test")
    scene_view = window.views[-2]
    entrance_view = window.views[-1]
    entrance_view.pick(f"entrance:{len(entrance_view.rows) - 1}")  # the ambush

    assert window.views[-1] is scene_view
    assert scene_view.awaiting

    scene_view.pick("continue")

    assert isinstance(window.views[-1], TacticalView)


def test_fixer_view_marks_a_rival_taken_offer_and_refuses_it():
    window = FakeWindow()
    fixer = next(f for f in window.session.app.fixers if f.offers)
    offer = fixer.offers[0]
    offer.taken_by = window.session.app.runners[0].id

    view = FixerView(window, fixer)
    label = dict(view.rows)[f"offer:{offer.id}"]
    assert "TAKEN" in label

    view.pick(f"offer:{offer.id}")

    assert offer not in window.session.character.accepted_jobs
    assert offer in fixer.offers
    assert view.message == "Someone else already took that one."


def test_shop_view_reports_an_unaffordable_program():
    window = FakeWindow()
    shop = next(
        location
        for territory in window.session.corp_map.territories.values()
        for location in territory.locations
        if location.kind is LocationKind.COMPUTER_STORE
    )
    view = ShopView(window, shop)
    row_id = next(row_id for row_id, _ in view.rows if row_id.startswith("program:"))
    program_id = row_id.removeprefix("program:")
    cash = window.session.character.cash

    view.pick(row_id)

    assert program_id not in window.session.character.owned_programs
    assert window.session.character.cash == cash
    assert view.message.startswith("Can't afford")


def test_tactical_aim_arrows_move_the_cursor():
    window = FakeWindow()
    grid = types.SimpleNamespace(
        in_bounds=lambda coord: 0 <= coord[0] < 10 and 0 <= coord[1] < 10
    )
    state = types.SimpleNamespace(aim_cursor=(5, 4), is_over=False, grid=grid)
    view = TacticalView(window, state, lambda outcome: None)
    view.aiming = True

    view.on_key(arcade.key.LEFT, 0)
    view.on_key(arcade.key.UP, 0)

    assert state.aim_cursor == (4, 3)
    assert view.aiming


def test_map_view_saves_and_loads_the_latest_run(tmp_path, monkeypatch):
    from shadowguy import saves

    monkeypatch.setattr(saves, "SAVE_DIR", tmp_path)

    window = FakeWindow()
    view = MapView(window)
    window.views = [view]
    window.session.character.cash = 777

    view.on_key(arcade.key.F5, 0)

    assert "Saved" in view.message
    assert len(saves.list_saves()) == 1

    window.session.character.cash = 1
    window.session.character.location_id = "mutated"
    view.on_key(arcade.key.F9, 0)

    assert window.session.character.cash == 777
    assert window.session.app.screen_stack == []
    assert window.views[-1] is not view
    assert window.views[-1].selected_id == window.session.character.location_id
    assert window.views[-1].message == "Loaded the newest save."


def test_map_view_load_with_no_saves_reports_it(tmp_path, monkeypatch):
    from shadowguy import saves

    monkeypatch.setattr(saves, "SAVE_DIR", tmp_path)

    window = FakeWindow()
    view = MapView(window)
    window.views = [view]

    view.on_key(arcade.key.F9, 0)

    assert view.message == "No save files."
    assert window.views == [view]


def test_start_view_offers_a_corp_run():
    window = FakeWindow()
    view = StartView(window)

    assert [row_id for row_id, _ in view.rows] == ["runner", "corp"]

    view.pick("corp")

    assert isinstance(window.views[-1], FactionPickView)


def test_faction_pick_starts_a_corp_only_run():
    window = FakeWindow()
    view = FactionPickView(window)
    faction_id = FACTIONS[0].id

    view.pick(f"faction:{faction_id}")

    assert window.session.app.corp_only
    assert window.session.app.corp_state.faction_id == faction_id
    assert isinstance(window.views[-1], MapView)
    assert "corp actions" in window.views[-1].message.lower()


def test_corp_map_c_opens_the_actions_list():
    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    view = window.map_view
    window.views = [view]

    view.on_key(arcade.key.C, 0)

    assert isinstance(window.views[-1], CorpActionsView)


def test_corp_map_relocation_is_free():
    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    view = window.map_view
    start = view.selected_id
    target = window.session.territory(start).connections[0]
    hours = window.session.character.elapsed_hours

    view.selected_id = target
    view._travel()

    assert window.session.character.location_id == target
    assert window.session.character.elapsed_hours == hours


def test_corp_actions_expand_claims_neutral_ground():
    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    state = window.session.app.corp_state
    corp_map = window.session.app.corp_map
    target = expansion_candidates(corp_map, state.faction_id)[0]
    window.map_view.selected_id = target
    view = CorpActionsView(window, window.map_view)
    row = f"expand:{target}"
    assert row in [row_id for row_id, _ in view.rows]

    view.pick(row)

    assert corp_map.territories[target].owner == state.faction_id
    assert state.action_points == 1


def test_corp_actions_rest_ends_the_day():
    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    view = CorpActionsView(window, window.map_view)
    day = window.session.character.day

    view.pick("rest")

    assert window.session.character.day == day + 1


def test_corp_rest_shows_game_over_when_the_last_district_is_lost():
    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    state = window.session.app.corp_state
    corp_map = window.session.app.corp_map
    other = next(faction.id for faction in FACTIONS if faction.id != state.faction_id)
    for territory in corp_map.territories.values():
        if territory.owner == state.faction_id:
            territory.owner = other
    view = CorpActionsView(window, window.map_view)

    view.pick("rest")

    assert isinstance(window.views[-1], GameOverView)


def test_force_pick_returns_the_chosen_count():
    window = FakeWindow()
    picked = []
    view = ForcePickView(window, "Send?", 4, picked.append)
    window.views = [view]

    view.pick("force:2")

    assert picked == [2]
    assert window.views == []


def test_research_tree_buys_a_root_technology():
    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    state = window.session.app.corp_state
    state.research_points = 10
    view = ResearchTreeView(window)

    view.pick("tech:worker_surveillance")

    assert "worker_surveillance" in state.researched


def test_corp_run_saves_and_loads(tmp_path, monkeypatch):
    from shadowguy import saves

    monkeypatch.setattr(saves, "SAVE_DIR", tmp_path)

    window = FakeWindow()
    window.start_corp(FACTIONS[0].id)
    view = window.map_view
    window.session.app.corp_state.cash = 1234

    view.on_key(arcade.key.F5, 0)
    window.session.app.corp_state.cash = 1
    view.on_key(arcade.key.F9, 0)

    assert window.session.app.corp_only
    assert window.session.app.corp_state.cash == 1234
    assert isinstance(window.views[-1], MapView)
