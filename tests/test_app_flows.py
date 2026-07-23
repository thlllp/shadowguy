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
from shadowguy.character import HOURS_PER_DAY
from shadowguy.combat import ENEMY_TIERS, ActionKind
from shadowguy.corpmap import (
    Location,
    LocationKind,
    TerritoryModifier,
    expansion_candidates,
    has_home,
    lodging_cost,
)
from shadowguy.factions import FACTIONS
from shadowguy.fixer import JobOffer
from shadowguy.jobs import JobTiming, generate_job
from shadowguy.matrix import ICE_TIERS, MatrixOutcome
from shadowguy.screens.combat_screen import CombatScreen
from shadowguy.screens.corp_map_screen import CorpMapScreen
from shadowguy.corp_turn import (
    TECHNOLOGIES_BY_ID,
    WORKER_SURVEILLANCE_ID,
    WORKER_SURVEILLANCE_INCOME_BONUS,
    collect_income,
    has_technology,
)
from shadowguy.screens.corp_screen import CorpMainMenu, CorpScreen, ResearchTreeScreen
from shadowguy.screens.creation_screen import CharacterCreationScreen
from shadowguy.screens.main_menu import MainMenu
from shadowguy.screens.matrix_screen import MatrixScreen
from shadowguy.screens.tactical_screen import TacticalScreen
from shadowguy.tactical import TacticalOutcome

# TestMenu is aliased -- an unaliased import would make pytest try (and fail, loudly
# in a warning) to collect it as a test class, since its name starts with "Test".
from shadowguy.screens.menu_screens import TestMenu as GameTestMenu
from shadowguy.screens.menu_screens import CorpSelectScreen, ModeSelectScreen, TitleMenu
from shadowguy.gangs import GANGS
from shadowguy.screens.corp_map_screen import GangTollScreen
from shadowguy.scene import Outcome
from shadowguy.screens.info_screens import ContactsScreen, InventoryScreen, SkillsScreen
from shadowguy.screens.scene_screen import SceneScreen
from shadowguy.screens.shop_screens import HospitalScreen, ShopScreen
from shadowguy.shops import HOSPITAL_STAY_COST
from textual.widgets import Collapsible, ListView


class ForcedChance(random.Random):
    """A Random whose random() is fixed, so the flat gang-encounter chance can be forced
    to fire; randint/choice still work for the enemy roll."""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._value = value

    def random(self) -> float:
        return self._value


def _stage_gang_turf(app, standing: int) -> str:
    """Put a gang on a territory bordering the runner, sour the runner's standing to
    `standing`, and return that territory id. Force the encounter chance to always fire."""
    start_id = app.character.location_id
    neighbor_id = app.corp_map.territories[start_id].connections[0]
    app.corp_map.territories[neighbor_id].gang_id = GANGS[0].id
    app.character.adjust_gang_standing(GANGS[0].id, standing)
    app.rng = ForcedChance(0.0)
    return neighbor_id


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
            assert isinstance(app.screen, ModeSelectScreen)
            await pilot.click("#runner")
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
            await pilot.click("#runner")
            await pilot.pause()
            # No archetype applied -- points are still unspent.
            assert app.character.stat_points + app.character.skill_points > 0
            await pilot.click("#begin")
            await pilot.pause()
            assert isinstance(app.screen, CharacterCreationScreen)

    run(body())


def test_new_game_corp_mode_picks_faction_and_skips_creation():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            assert isinstance(app.screen, CorpSelectScreen)

            faction = FACTIONS[0]
            await pilot.click(f"#faction_{faction.id}")
            await pilot.pause()
            # Corp mode has no runner to build -- straight to CorpMainMenu, no
            # CharacterCreationScreen, and nothing left in the build pools.
            assert isinstance(app.screen, CorpMainMenu)
            assert app.corp_state is not None
            assert app.corp_state.faction_id == faction.id
            assert app.character.stat_points == 0
            assert app.character.skill_points == 0
            assert app.corp_only is True

    run(body())


def test_corp_main_menu_has_sidebar_categories():
    """CorpMainMenu is laid out like MainMenu -- a left category sidebar next to the
    corp's own action list -- rather than dropping straight into the action list with
    nothing beside it. "Corp" renders inline; "Corp Map"/"Contacts"/"Technology" push
    their own screens, reachable both from the sidebar and from the same quick
    keybindings MainMenu uses."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()
            assert isinstance(app.screen, CorpMainMenu)

            categories = app.screen.query_one("#categories", ListView)
            assert [item.id for item in categories.children] == [
                "cat_corp",
                "cat_map",
                "cat_contacts",
                "cat_tech",
            ]
            # The corp's own action list (inherited from CorpScreen) is visible
            # inline beside the sidebar, not something you have to navigate to.
            assert any(item.id == "rest" for item in app.screen.query_one("#corp_list", ListView).children)

            await pilot.click("#cat_map")
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)
            app.pop_screen()
            await pilot.pause()

            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, ContactsScreen)
            app.pop_screen()
            await pilot.pause()

            await pilot.click("#cat_tech")
            await pilot.pause()
            assert isinstance(app.screen, ResearchTreeScreen)
            app.pop_screen()
            await pilot.pause()

            # escape is a deliberate no-op here -- there's no MainMenu underneath
            # to pop back to for a pure-corp game.
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, CorpMainMenu)

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
            # is_tactical is a per-job coin flip, so try a few seeds. Also skip a
            # Burglary job: its start stage has no `choices` at all (it's a
            # BurglaryStage, picked via EntrancePickScreen, not #choice_N rows) --
            # this test is specifically about the plain-Choice-list ambush door.
            scene = None
            for seed in range(30):
                candidate, _timing = generate_job(
                    day=1, corp_map=app.corp_map, fixer_id="fx", rng=random.Random(seed)
                )
                if candidate.stages[candidate.start_stage].burglary is not None:
                    continue
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
                i
                for i, action in enumerate(combat_screen.actions)
                if action.kind is ActionKind.FLEE
            )
            await pilot.click(f"#action_{flee_index}")
            await pilot.pause()
            # Flee always ends the fight (escaped, or dead from a parting shot) --
            # never left ongoing -- and the "Continue" row replaces the action list.
            assert combat_screen.state.is_over

    run(body())


def test_combat_action_list_boxes_only_the_highlighted_action():
    """Regression test for the RPG-style boxed action list: a combat round can offer
    far more actions than a matrix fight (per-weapon attacks, the four stat-spread
    options, one row per grenade), so boxing every row like MatrixScreen does would
    push the list past the screen's visible height -- only the highlighted action
    gets a border, the rest stay flat text (see combat_screen.py's CSS comment)."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            scene = None
            for seed in range(30):
                candidate, _timing = generate_job(
                    day=1, corp_map=app.corp_map, fixer_id="fx", rng=random.Random(seed)
                )
                if candidate.stages[candidate.start_stage].burglary is not None:
                    continue
                fight_id = f"{candidate.start_stage}_fight"
                if candidate.stages[fight_id].combat is not None:
                    scene = candidate
                    break
            assert scene is not None, "no abstract-combat job turned up in 30 seeds"

            app.push_screen(SceneScreen(scene))
            await pilot.pause()

            stage = scene.stages[scene.start_stage]
            ambush_index = len(stage.choices) - 1
            await pilot.click(f"#choice_{ambush_index}")
            await pilot.pause()
            await pilot.click("#choices ListItem")
            await pilot.pause()
            assert isinstance(app.screen, CombatScreen)

            combat_screen = app.screen
            actions_list = combat_screen.query_one("#actions", ListView)
            items = list(actions_list.children)
            assert len(items) > 1, "need at least two actions to tell boxed from flat apart"

            def highlighted():
                return [item for item in items if "-highlight" in item.classes]

            # The default cursor position (index 0) is the only bordered item.
            assert highlighted() == [items[0]]

            actions_list.index = 1
            await pilot.pause()
            assert highlighted() == [items[1]]

    run(body())


def test_data_heist_ambush_routes_into_a_matrix_fight_and_jack_out_ends_it():
    """A Data Heist's fights are ICE, not gunmen: the guaranteed 'Take them first'
    ambush on its (ordinary Choice) approach stage must reach a live MatrixScreen
    (starting in navigation mode, at the network's entry node), and jacking out
    (which always works, even before any node fight has opened) must cleanly end
    the run."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            scene = None
            for seed in range(80):
                candidate, _timing = generate_job(
                    day=7, corp_map=app.corp_map, fixer_id="fx", rng=random.Random(seed)
                )
                if candidate.title.startswith("Data Heist"):
                    scene = candidate
                    break
            assert scene is not None, "no Data Heist turned up in 80 seeds"
            # Its start stage is an ordinary approach Choice list (matrix only replaces
            # the fights), and its fight beside that stage is a matrix run.
            start = scene.stages[scene.start_stage]
            assert start.choices and start.matrix is None
            assert scene.stages[f"{scene.start_stage}_fight"].matrix is not None

            app.push_screen(SceneScreen(scene))
            await pilot.pause()

            ambush_index = len(start.choices) - 1  # the ambush is always appended last
            await pilot.click(f"#choice_{ambush_index}")
            await pilot.pause()
            await pilot.click("#choices ListItem")  # click through the "Continue" row
            await pilot.pause()
            assert isinstance(app.screen, MatrixScreen)

            matrix_screen = app.screen
            # The entry node is never guarded, so this opens in navigation mode --
            # "Jack out" is always one of its rows, fight or no fight. It's always
            # the last row, and a big network can push it below the viewport, so
            # navigate to it by keyboard (which scrolls it into view) rather than
            # clicking a raw screen offset.
            assert not matrix_screen.run.in_fight
            actions_list = matrix_screen.query_one("#actions", ListView)
            jack_index = next(i for i, item in enumerate(actions_list.children) if item.id == "jack_out")
            for _ in range(jack_index):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert matrix_screen.run.is_over
            assert matrix_screen.run.outcome is MatrixOutcome.EJECTED

    run(body())


def test_test_menu_lists_a_single_tier_of_each_test_fight():
    """The Test menu was trimmed down to one Tactical Combat and one Matrix Combat
    entry (the lowest tier of each) rather than one row per combat.ENEMY_TIERS /
    matrix.ICE_TIERS entry -- lock that in so a future tier addition doesn't quietly
    reopen the full list."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#test")
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)

            ids = [item.id for item in app.screen.query_one(ListView).children]
            assert ids == [f"tactical_{min(ENEMY_TIERS)}", f"matrix_{min(ICE_TIERS)}"]

    run(body())


def test_test_menu_matrix_combat_reaches_a_live_matrix_fight():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert isinstance(app.screen, TitleMenu)

            await pilot.click("#test")
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)
            await pilot.click("#matrix_0")
            await pilot.pause()
            assert isinstance(app.screen, MatrixScreen)

            matrix_screen = app.screen
            assert not matrix_screen.run.in_fight
            actions_list = matrix_screen.query_one("#actions", ListView)
            jack_index = next(i for i, item in enumerate(actions_list.children) if item.id == "jack_out")
            for _ in range(jack_index):
                await pilot.press("down")
            await pilot.press("enter")
            await pilot.pause()
            assert matrix_screen.run.is_over
            assert matrix_screen.run.outcome is MatrixOutcome.EJECTED
            await pilot.click("#actions ListItem")  # click through the "Continue" row
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)

    run(body())


def test_test_menu_tactical_combat_reaches_a_live_tactical_fight_with_boxed_status_tiles():
    """The Tactical Combat test-menu entry must reach a live TacticalScreen, and its
    status readout must render as the bordered RPG-style HUD tiles (tactical_screen.py's
    #tac_box_* rows) rather than the old single flat status line."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            assert not state.is_over

            # Content that doesn't depend on the randomly generated map/enemy roll.
            assert "Move (arrows)" in tac_screen.query_one("#tac_box_move").content.plain
            assert "Attack (f)" in tac_screen.query_one("#tac_box_attack").content.plain
            assert "End turn (e)" in tac_screen.query_one("#tac_box_end").content.plain
            assert "Leave (l)" in tac_screen.query_one("#tac_box_leave").content.plain
            assert f"{len(state.enemies)} left" in tac_screen.query_one("#tac_box_enemies").content.plain
            assert tac_screen.query_one("#tac_status").display is True

            # Force the player onto an exit tile and leave -- positional escape always
            # works (no roll), so this ends the fight deterministically regardless of
            # the map's RNG-driven layout.
            state.player.coord = next(iter(state.exits))
            tac_screen.action_leave()
            await pilot.pause()
            assert state.is_over
            assert state.outcome is TacticalOutcome.ESCAPED
            # The HUD hides and the end-of-fight message takes its place.
            assert tac_screen.query_one("#tac_status").display is False

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)

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


def test_buy_deck_and_program_then_install_via_inventory_screen():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            store_location = None
            store_territory_id = None
            for territory in app.corp_map.territories.values():
                for location in territory.locations:
                    if location.kind == LocationKind.COMPUTER_STORE:
                        store_location = location
                        store_territory_id = territory.id
                        break
                if store_location:
                    break
            assert store_location is not None
            app.character.location_id = store_territory_id
            app.character.cash = 1_000_000

            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_local")
            await pilot.pause()
            await pilot.click(f"#local_{store_location.id}")
            await pilot.pause()
            assert isinstance(app.screen, ShopScreen)

            await pilot.click("#buy_burner_deck")
            await pilot.pause()
            assert len(app.character.inventory) == 1
            deck_index = 0

            await pilot.click("#buyp_sleaze")
            await pilot.pause()
            assert "sleaze" in app.character.owned_programs
            assert app.character.inventory[deck_index].installed_programs == []

            app.screen.action_back()
            await pilot.pause()
            assert isinstance(app.screen, MainMenu)

            await pilot.click("#cat_gear")
            await pilot.pause()
            assert isinstance(app.screen, InventoryScreen)

            await pilot.click(f"#install_{deck_index}_sleaze")
            await pilot.pause()
            assert app.character.inventory[deck_index].installed_programs == ["sleaze"]

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


def test_travel_never_refused_regardless_of_hours_already_spent():
    """No exhaustion cap: chaining travel hops never gets refused for "being too
    tired" the way stamina used to block it -- time just keeps accumulating."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            screen = app.screen

            here_id = app.character.location_id
            for _ in range(15):
                neighbor_id = app.corp_map.territories[here_id].connections[0]
                screen.selected_id = neighbor_id
                screen._refresh()
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert app.character.location_id == neighbor_id
                here_id = neighbor_id

            # 15 hops well past a single day's worth of hours, with no refusal above.
            assert app.character.elapsed_hours > HOURS_PER_DAY

    run(body())


def test_spend_time_fires_the_day_tick_once_per_boundary_crossed():
    """spend_time's per-boundary loop only ever fires once with today's in-game costs
    (nothing spends >=2*HOURS_PER_DAY in one call) -- this proves the loop itself
    actually iterates more than once when a spend crosses more than one boundary,
    since no real call site exercises that path."""
    from shadowguy.corp_turn import CorpState, collect_income
    from shadowguy.runners import RIVAL_RUNNERS

    async def body():
        app = ShadowguyApp()
        async with app.run_test():
            app.corp_state = CorpState(faction_id=FACTIONS[0].id)
            one_day_income = collect_income(app.corp_state, app.corp_map)
            assert one_day_income > 0
            cash_before = app.corp_state.cash

            app.spend_time(HOURS_PER_DAY * 2 + 3)

            assert app.character.day == 3
            assert app.corp_state.cash == cash_before + 2 * one_day_income
            # rival_actions must accumulate across both boundaries crossed in this one
            # spend, not just keep the last day's -- one action per non-player faction
            # plus one per not-on-crew rival runner, per day ticked.
            actions_per_day = (len(FACTIONS) - 1) + len(RIVAL_RUNNERS)
            assert len(app.rival_actions) == 2 * actions_per_day

    run(body())


def test_hospital_stay_advances_one_day_and_skips_the_lodging_charge():
    """A hospital stay spends exactly HOURS_PER_DAY hours with skip_night_effects=True
    -- it still ticks the day over (crew wages, offer refresh, etc.) but must not also
    charge lodging that night, since the stay already covers room and board."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            # The start tile always carries the runner's free apartment (has_home), which
            # would make a lodging charge zero regardless -- move to any territory with
            # no home and a nonzero lodging cost, so a missed double-charge would
            # actually show up (a generated map's Development can be 0, especially on
            # neutral ground, so scan rather than assume the first non-home neighbor).
            territory = next(
                t for t in app.corp_map.territories.values()
                if not has_home(t) and lodging_cost(t) > 0
            )
            app.character.location_id = territory.id

            hospital_location = Location(id="test_hospital", name="Test Ward", kind=LocationKind.HOSPITAL)
            app.character.adjust_health(-10)
            hurt_health = app.character.health
            day_before = app.character.day
            cash_before = app.character.cash

            app.push_screen(HospitalScreen(hospital_location))
            await pilot.pause()
            await pilot.click("#stay")
            await pilot.pause()

            assert app.character.day == day_before + 1
            assert app.character.health > hurt_health
            # Only the flat hospital fee was charged -- no separate lodging on top.
            assert app.character.cash == cash_before - HOSPITAL_STAY_COST

    run(body())


def test_running_a_job_that_crosses_midnight_does_not_expire_itself_or_drop_its_crew():
    """Regression test: the job-run handler used to call spend_time() (charging the
    job's own hours_cost) before pushing its Scene, and if that spend crossed
    midnight, the resulting day tick would prune the very job -- and discharge any
    crew hired for it -- out from under itself. protect_job_id (threaded through
    spend_time) exists to stop exactly this."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            scene, _timing = generate_job(day=1, corp_map=app.corp_map, fixer_id="fx", rng=random.Random(0))
            # A hard scheduled-day job due exactly today, run late enough that its
            # own hours_cost (8 or 12) pushes elapsed_hours past midnight.
            offer = JobOffer(
                id="offer_1", fixer_id="fx", scene=scene, timing=JobTiming(scheduled_day=1), offered_day=1
            )
            app.character.accepted_jobs.append(offer)
            app.character.location_id = scene.target_territory_id
            app.character.hire_for_job("runner_specter", scene.id)
            app.character.elapsed_hours = HOURS_PER_DAY - 1

            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_job")
            await pilot.pause()
            await pilot.click(f"#job_{offer.id}")
            await pilot.pause()

            assert isinstance(app.screen, SceneScreen)
            assert app.character.day == 2  # the job's own hours_cost crossed midnight
            assert offer in app.character.accepted_jobs  # not pruned out from under itself
            assert app.character.on_crew("runner_specter")  # crew hire survived too


def test_completed_job_xp_is_not_split_with_crew_but_each_crew_member_earns_it_too():
    """The player's own XP (credited via scene.apply_outcome, ahead of
    _take_crew_cut) is never reduced by having crew along, and every crew member
    hired for that job separately earns the same full amount into their own
    Character.crew_experience -- not divided out of a shared pot."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()

            scene, _timing = generate_job(day=1, corp_map=app.corp_map, fixer_id="fx", rng=random.Random(0))
            app.character.hire_for_job("runner_specter", scene.id)
            app.character.hire_for_job("runner_juncture", scene.id)
            app.push_screen(SceneScreen(scene))
            await pilot.pause()

            outcome = Outcome(text="done", cash_delta=100, experience_delta=20, next_stage=None)
            app.character.gain_experience(outcome.experience_delta)
            app.screen._take_crew_cut(outcome)

            assert app.character.experience == 20
            assert app.character.crew_experience == {"runner_specter": 20, "runner_juncture": 20}


def test_skills_screen_spends_experience_on_a_stat_and_a_skill():
    """SkillsScreen is the post-creation spend surface: a 'Raise <Stat>' row and
    every skill row both draw on Character.experience rather than the one-shot
    creation pools."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.character.experience = 100
            body_before = app.character.body
            hack_rank_before = app.character.skill_rank("hack")

            app.push_screen(SkillsScreen())
            await pilot.pause()

            await pilot.click("#stat_body")
            await pilot.pause()
            assert app.character.body == body_before + 1
            xp_after_stat = app.character.experience
            assert xp_after_stat == 99  # first point on a fresh stat costs 1

            await pilot.click("#skill_hack")
            await pilot.pause()
            assert app.character.skill_rank("hack") == hack_rank_before + 1
            assert app.character.experience < xp_after_stat

    run(body())


def test_skills_screen_refuses_unaffordable_stat_without_charging():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.character.experience = 0
            body_before = app.character.body

            app.push_screen(SkillsScreen())
            await pilot.pause()
            await pilot.click("#stat_body")
            await pilot.pause()

            assert app.character.body == body_before
            assert app.character.experience == 0

    run(body())

    run(body())


def test_corp_screen_pick_faction_expand_and_rest():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(MainMenu())
            await pilot.pause()

            await pilot.click("#cat_corp")
            await pilot.pause()
            assert isinstance(app.screen, CorpScreen)
            assert app.corp_state is None

            # Real maps vary run to run (ShadowguyApp seeds its own rng) -- scan for
            # whichever faction actually has an eligible neutral neighbor right now,
            # the same tolerant-of-randomness approach the shop-finding tests use.
            faction_id, candidates = None, []
            for faction in FACTIONS:
                found = expansion_candidates(app.corp_map, faction.id)
                if found:
                    faction_id, candidates = faction.id, found
                    break
            assert faction_id is not None, "no faction had an eligible neutral neighbor"

            await pilot.click(f"#faction_{faction_id}")
            await pilot.pause()
            assert app.corp_state is not None
            assert app.corp_state.faction_id == faction_id

            # Give the corp room to afford the move regardless of the target's value.
            app.corp_state.cash = 1_000_000
            await app.screen._refresh()
            await pilot.pause()

            target_id = candidates[0]
            await pilot.click(f"#expand_{target_id}")
            await pilot.pause()
            assert app.corp_map.territories[target_id].owner == faction_id
            assert app.corp_state.daily_action_used is True

            day_before = app.character.day
            hours_before = app.character.elapsed_hours
            cash_before = app.corp_state.cash
            await pilot.click("#rest")
            await pilot.pause()
            assert app.character.day == day_before + 1
            assert app.character.elapsed_hours == hours_before + HOURS_PER_DAY
            assert app.corp_state.daily_action_used is False
            assert app.corp_state.cash >= cash_before  # territory income collected

    run(body())


def test_corp_screen_groups_actions_by_academy_and_research_facility():
    """Academy/Research Facility actions live in their own collapsibles, not one
    flat list with everything else -- and clicking a row inside either one still
    reaches the same corp_turn.py functions as before."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_corp")
            await pilot.pause()

            faction = FACTIONS[0]
            await pilot.click(f"#faction_{faction.id}")
            await pilot.pause()
            app.corp_state.cash = 1_000_000
            await app.screen._refresh()
            await pilot.pause()

            academy_list = app.screen.query_one("#academy_list", ListView)
            academy_ids = {item.id for item in academy_list.children}
            assert academy_ids == {"train_scientist", "train_operative", "train_research_assistant"}

            research_list = app.screen.query_one("#research_list", ListView)
            research_ids = {item.id for item in research_list.children}
            assert research_ids == {"build_lab", "build_efficiency"}

            # Neither set of ids leaked into the territory/rest list.
            corp_list_ids = {item.id for item in app.screen.query_one("#corp_list", ListView).children}
            assert "train_scientist" not in corp_list_ids
            assert "build_lab" not in corp_list_ids
            assert "rest" in corp_list_ids

            scientists_before = app.corp_state.scientists
            await pilot.click("#train_scientist")
            await pilot.pause()
            assert app.corp_state.scientists > scientists_before
            assert app.corp_state.daily_action_used is True

    run(body())


def test_corp_main_menu_stats_panel_and_sections_stack_top_to_bottom():
    """Regression test for a real layout bug: ListView defaults to height: 1fr, and a
    plain mixin (PanelNav) sitting in CorpMainMenu's MRO was silently defeating the
    height: auto override that fixes it, so academy_panel/research_panel each claimed
    a tall fixed box and rendered on top of corp_list and each other instead of
    stacking below it. Also checks the stat box (corp_info) got its own bordered panel
    above the sidebar, the same way MainMenu's CharacterSheet does."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()
            assert isinstance(app.screen, CorpMainMenu)

            stats_panel = app.screen.query_one("#corp_stats_panel")
            sidebar = app.screen.query_one("#sidebar")
            corp_list = app.screen.query_one("#corp_list", ListView)
            academy_panel = app.screen.query_one("#academy_panel")
            research_panel = app.screen.query_one("#research_panel")

            # The stat box sits in its own panel above the sidebar/main-panel split.
            assert stats_panel.region.y + stats_panel.region.height <= sidebar.region.y

            # Each section starts at or after the previous one's bottom edge -- top to
            # bottom, never overlapping.
            assert corp_list.region.y + corp_list.region.height <= academy_panel.region.y
            assert academy_panel.region.y + academy_panel.region.height <= research_panel.region.y

    run(body())


def test_contacts_screen_panels_are_collapsibles_expanded_by_default():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ContactsScreen())
            await pilot.pause()

            panels = {
                pid: app.screen.query_one(f"#{pid}", Collapsible)
                for pid in (
                    "fixers_panel",
                    "corps_panel",
                    "locals_panel",
                    "runners_panel",
                )
            }
            assert len(panels) == 4
            assert all(not panel.collapsed for panel in panels.values())

    run(body())


def test_contacts_panel_nav_skips_a_collapsed_section():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(ContactsScreen())
            await pilot.pause()

            screen = app.screen
            # Collapse the middle "Corps" panel; stepping right off Fixers should land on
            # Locals (skipping the hidden Corps list), not on the collapsed Corps list.
            screen.query_one("#corps_panel", Collapsible).collapsed = True
            await pilot.pause()
            screen.query_one("#fixers_list", ListView).focus()
            await pilot.pause()
            screen.action_focus_panel(1)
            await pilot.pause()
            assert screen.focused is screen.query_one("#locals_list", ListView)

    run(body())


def test_local_tab_locations_and_fixers_are_collapsibles_expanded_by_default():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_local")
            await pilot.pause()

            panels = {
                pid: app.screen.query_one(f"#{pid}", Collapsible)
                for pid in ("local_locations_panel", "local_fixers_panel")
            }
            assert len(panels) == 2
            assert all(not panel.collapsed for panel in panels.values())

    run(body())


def test_local_panels_are_only_visible_on_the_local_category():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(MainMenu())
            await pilot.pause()
            screen = app.screen

            # Default category is "gig" -- the local-only panels start hidden.
            assert screen.query_one("#local_locations_panel").display is False
            assert screen.query_one("#local_fixers_panel").display is False

            await pilot.click("#cat_local")
            await pilot.pause()
            assert screen.query_one("#local_locations_panel").display is True
            assert screen.query_one("#local_fixers_panel").display is True

            await pilot.click("#cat_job")
            await pilot.pause()
            assert screen.query_one("#local_locations_panel").display is False
            assert screen.query_one("#local_fixers_panel").display is False

    run(body())


def test_local_panel_nav_skips_a_collapsed_section():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_local")
            await pilot.pause()

            screen = app.screen
            screen.query_one("#local_locations_panel", Collapsible).collapsed = True
            await pilot.pause()
            screen.query_one("#categories", ListView).focus()
            await pilot.pause()
            screen.action_focus_panel(1)
            await pilot.pause()
            assert screen.focused is screen.query_one("#local_fixers", ListView)

    run(body())


def test_entering_gang_turf_at_minor_negative_prompts_a_toll_and_paying_deducts_cash():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            neighbor_id = _stage_gang_turf(app, standing=-2)  # toll band
            app.character.cash = 1000

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            app.screen.selected_id = neighbor_id
            app.screen._refresh()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            # Arrived on the turf and got stopped for a toll.
            assert app.character.location_id == neighbor_id
            assert isinstance(app.screen, GangTollScreen)

            await pilot.click("#pay")
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)
            assert app.character.cash == 1000 - 70  # toll_for(-2)

    run(body())


def test_toll_the_runner_cant_cover_falls_through_to_a_fight():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            neighbor_id = _stage_gang_turf(app, standing=-2)  # toll band, 70eb
            app.character.cash = 10  # can't cover the toll

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            app.screen.selected_id = neighbor_id
            app.screen._refresh()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, GangTollScreen)

            # Trying to pay what you can't cover drops you into the fight instead.
            await pilot.click("#pay")
            await pilot.pause()
            assert isinstance(app.screen, CombatScreen)
            assert app.character.cash == 10  # nothing taken

    run(body())


def test_entering_gang_turf_at_deep_negative_drops_straight_into_a_fight():
    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            neighbor_id = _stage_gang_turf(app, standing=-5)  # attack band

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            app.screen.selected_id = neighbor_id
            app.screen._refresh()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert app.character.location_id == neighbor_id
            assert isinstance(app.screen, CombatScreen)

    run(body())


def test_corp_screen_researches_worker_surveillance_then_raises_a_modifier():
    """The Research Tree screen ('t' from CorpScreen) spends research points, and
    the tech's effects show up where they land: income rises per territory,
    Surveillance rows appear in the territory list (they don't exist at all before
    researching), and researching unlocks the tier behind it (Panopticon Grid)."""

    async def body():
        app = ShadowguyApp()
        # Tall enough that no row needs scrolling to click. The map is generated off
        # an unseeded app.rng, so the number of expansion rows -- and with it every
        # widget's y position -- varies run to run; at 80x24 this screen's stacked
        # sections overflow and the click target moves. See CLAUDE.md's note on the
        # section stack's height.
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(MainMenu())
            await pilot.pause()
            await pilot.click("#cat_corp")
            await pilot.pause()

            faction = FACTIONS[0]
            await pilot.click(f"#faction_{faction.id}")
            await pilot.pause()
            app.corp_state.cash = 1_000_000
            await app.screen._refresh()
            await pilot.pause()

            # Nothing to raise yet.
            corp_ids = {item.id for item in app.screen.query_one("#corp_list", ListView).children}
            assert not any(i.startswith("surveil_") for i in corp_ids)

            # Technology now lives on its own pushed Research Tree screen. Worker
            # Surveillance and Brains 2 are the two roots (empty prereqs), so both
            # land in tier 0.
            await pilot.press("t")
            await pilot.pause()
            assert isinstance(app.screen, ResearchTreeScreen)
            tier0_ids = {item.id for item in app.screen.query_one("#tier_0_list", ListView).children}
            assert tier0_ids == {"tech_worker_surveillance", "tech_brains_2"}

            income_before = collect_income(app.corp_state, app.corp_map)
            app.corp_state.research_points = TECHNOLOGIES_BY_ID[WORKER_SURVEILLANCE_ID].cost
            await app.screen._refresh()
            await pilot.pause()

            await pilot.click("#tech_worker_surveillance")
            await pilot.pause()
            assert has_technology(app.corp_state, WORKER_SURVEILLANCE_ID)
            assert app.corp_state.research_points == 0
            # Researching is not the day's directed move.
            assert app.corp_state.daily_action_used is False

            owned = [t for t in app.corp_map.territories.values() if t.owner == faction.id]
            assert collect_income(app.corp_state, app.corp_map) - income_before == (
                WORKER_SURVEILLANCE_INCOME_BONUS * len(owned)
            )

            # The researched box flips state; Brains 2 stays offered untouched, and
            # Panopticon Grid (Tier 1, gated behind Worker Surveillance) is now on
            # the tree at all.
            tier0_item = next(
                item
                for item in app.screen.query_one("#tier_0_list", ListView).children
                if item.id == "tech_worker_surveillance"
            )
            assert tier0_item.has_class("-researched")
            tier1_ids = {item.id for item in app.screen.query_one("#tier_1_list", ListView).children}
            assert "tech_panopticon_grid" in tier1_ids

            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, CorpScreen)
            await app.screen._refresh()
            await pilot.pause()

            corp_list = app.screen.query_one("#corp_list", ListView)
            surveil_ids = [item.id for item in corp_list.children if item.id.startswith("surveil_")]
            assert surveil_ids
            target_id = surveil_ids[0].removeprefix("surveil_")
            territory = app.corp_map.territories[target_id]
            before = territory.modifiers[TerritoryModifier.SURVEILLANCE]

            await pilot.click(f"#{surveil_ids[0]}")
            await pilot.pause()
            assert territory.modifiers[TerritoryModifier.SURVEILLANCE] == before + 1
            assert app.corp_state.daily_action_used is False

    run(body())
