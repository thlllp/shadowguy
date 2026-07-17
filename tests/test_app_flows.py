"""End-to-end UI flow tests, driven headlessly via Textual's app.run_test()/pilot.

These exercise real screen wiring (imports, ids, event routing) rather than pure
logic -- the kind of regression a unit test on combat.py alone would miss (e.g. a
screen module importing a name from the wrong place, which only blows up the first
time that lazily-imported screen is actually reached at runtime).

No pytest-asyncio in this project's dev dependencies, so each test wraps its body
in asyncio.run() rather than using an async def test function directly.
"""

import asyncio
import random

from shadowguy.app import ShadowguyApp
from shadowguy.combat import ActionKind
from shadowguy.corpmap import LocationKind
from shadowguy.jobs import generate_job
from shadowguy.screens.combat_screen import CombatScreen
from shadowguy.screens.corp_map_screen import CorpMapScreen
from shadowguy.screens.creation_screen import CharacterCreationScreen
from shadowguy.screens.main_menu import MainMenu
from shadowguy.screens.menu_screens import TitleMenu
from shadowguy.screens.scene_screen import SceneScreen
from shadowguy.screens.shop_screens import ShopScreen


def run(coro):
    return asyncio.run(coro)


def test_app_boots_to_title_menu():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, TitleMenu)

    run(body())


def test_new_game_creation_screen_apply_archetype_and_begin_reaches_main_menu():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#new_game")
            await pilot.pause()
            assert isinstance(app.screen, CharacterCreationScreen)

            # Applying an archetype spends every point; begin should then succeed.
            await pilot.click("#arch_enforcer")
            await pilot.pause()
            character = app.character
            assert character.stat_points == 0
            assert character.skill_points == 0

            await pilot.click("#begin")
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)

    run(body())


def test_creation_screen_refuses_to_begin_with_unspent_points():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#new_game")
            await pilot.pause()
            # No archetype applied -- points are still unspent.
            assert app.character.stat_points + app.character.skill_points > 0
            await pilot.click("#begin")
            await pilot.pause()
            assert isinstance(app.screen, CharacterCreationScreen)

    run(body())


def test_job_ambush_choice_routes_into_an_abstract_fight_and_flee_ends_it():
    """Regression test for the Drop-import crash: selecting a job's guaranteed
    'Take them first' ambush choice must reach a live CombatScreen, and fleeing
    (which always works) must cleanly end the fight and return to the scene."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            # Find a generated job whose first fight is abstract (not tactical) --
            # is_tactical is a per-job coin flip, so try a few seeds.
            scene = None
            for seed in range(30):
                candidate, _timing = generate_job(
                    day=1, corp_map=app.corp_map, fixer_id="fx", rng=random.Random(seed)
                )
                fight_id = f"{candidate.start_stage}_fight"
                if candidate.stages[fight_id].combat is not None:
                    scene = candidate
                    break
            assert scene is not None, "no abstract-combat job turned up in 30 seeds"

            app.push_screen(SceneScreen(scene))
            await pilot.pause()

            stage = scene.stages[scene.start_stage]
            ambush_index = len(stage.choices) - 1  # the ambush is always appended last
            await pilot.click(f"#choice_{ambush_index}")
            await pilot.pause()
            # Picking a choice shows its outcome text and waits for "Continue" before
            # actually advancing to the next stage -- click through it.
            await pilot.click("#choices ListItem")
            await pilot.pause()
            # Any result of the ambush choice routes to the fight -- win, lose, or
            # draw the check, we should now be looking at a live CombatScreen.
            assert isinstance(app.screen, CombatScreen)

            combat_screen = app.screen
            flee_index = next(
                i for i, action in enumerate(combat_screen.actions) if action.kind is ActionKind.FLEE
            )
            await pilot.click(f"#action_{flee_index}")
            await pilot.pause()
            # Flee always ends the fight (escaped, or dead from a parting shot) --
            # never left ongoing -- and the "Continue" row replaces the action list.
            assert combat_screen.state.is_over

    run(body())


def test_shop_screen_buy_flow_spends_cash_and_adds_inventory():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            # Stand the runner in a district with a weapon shop specifically -- its
            # catalog always has real Items first (unlike e.g. PHARMACY, whose Item
            # catalog is empty and would put a consumable-buy row first instead).
            shop_location = None
            shop_territory_id = None
            for territory in app.corp_map.territories.values():
                for location in territory.locations:
                    if location.kind == LocationKind.WEAPON_SHOP:
                        shop_location = location
                        shop_territory_id = territory.id
                        break
                if shop_location:
                    break
            assert shop_location is not None
            app.character.location_id = shop_territory_id
            app.character.cash = 1_000_000

            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_local")
            await pilot.pause()
            await pilot.click(f"#local_{shop_location.id}")
            await pilot.pause()
            assert isinstance(app.screen, ShopScreen)

            before_cash = app.character.cash
            before_items = len(app.character.inventory)
            # The first row in a shop is always a "Buy <item>" row.
            await pilot.click("#shop_items ListItem")
            await pilot.pause()
            assert len(app.character.inventory) == before_items + 1
            assert app.character.cash < before_cash

    run(body())


def test_corp_map_screen_travel_moves_the_runner_to_a_bordering_territory():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)

            start_id = app.character.location_id
            neighbor_id = app.corp_map.territories[start_id].connections[0]
            screen = app.screen
            screen.selected_id = neighbor_id
            screen._refresh()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.character.location_id == neighbor_id

    run(body())
