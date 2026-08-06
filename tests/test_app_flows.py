"""End-to-end UI flow tests, driven headlessly via Textual's app.run_test(size=(80, 60))/pilot.

These exercise real screen wiring (imports, ids, event routing) rather than pure
logic -- the kind of regression a unit test on combat.py alone would miss (e.g. a
screen module importing a name from the wrong place, which only blows up the first
time that lazily-imported screen is actually reached at runtime).

No pytest-asyncio in this project's dev dependencies, so each test wraps its body
in asyncio.run() rather than using an async def test function directly.
"""

import asyncio
import random

import pytest

from shadowguy.app import ShadowguyApp
from shadowguy.buildings import BuildingKind, Lock
from shadowguy.character import (
    GEAR_EB_PER_POINT,
    SURGERY_SCARRING,
    HOURS_PER_DAY,
    REST_HOURS_COST,
    Character,
    InventoryItem,
)
from shadowguy.abstract_combat import ActionKind
from shadowguy.combat import ENEMIES_BY_ID, ENEMY_TIERS
from shadowguy.corpmap import (
    WORKSHOP_BUILD_COST,
    Location,
    LocationKind,
    STARTING_RESEARCH_TIER,
    TerritoryModifier,
    attack_candidates,
    capture_territory,
    expansion_candidates,
    has_home,
    lodging_cost,
)
from shadowguy.factions import (
    FACTIONS,
    FACTIONS_BY_ID,
    TAKEOVER_COST,
    TAKEOVER_MIN_REP,
    TAKEOVER_MIN_STANDING,
)
from shadowguy.fixer import JobOffer
from shadowguy.jobs import GANG_JOB_STANDING_GAIN, JobTiming, generate_job, generate_smuggling_job
from shadowguy.matrix import ICE_TIERS, MatrixOutcome
from shadowguy.screens import CharacterSheet
from shadowguy.screens.burglary_screens import EntrancePickScreen
from shadowguy.screens.combat_screen import CombatScreen
from shadowguy.screens.corp_map_screen import CorpMapScreen
from shadowguy.corp_turn import (
    ACADEMY_REBUILD_COST,
    RESEARCH_FACILITY_REBUILD_COST,
    TECHNOLOGIES,
    TECHNOLOGIES_BY_ID,
    CorpState,
    FactionEvent,
    WORKER_SURVEILLANCE_ID,
    WORKER_SURVEILLANCE_INCOME_BONUS,
    collect_income,
    collect_research,
    has_technology,
    owned_research_facility,
)
from shadowguy.cybernetics import (
    CYBERWARE_BY_ID,
    CYBERWARE_CATALOG,
    CyberSlot,
    free_humanity,
    install_cyberware,
)
from shadowguy.screens.corp_screen import CorpScreen, ForcePickScreen, ResearchTreeScreen
from shadowguy.screens.creation_screen import CharacterCreationScreen, GearScreen

from shadowguy.screens.matrix_screen import MatrixScreen
from shadowguy.screens.tactical_screen import HackerPickScreen, TacticalScreen
from shadowguy.grid import Tile, parse_grid, step_neighbors, visible_tiles
from shadowguy.support import TRACE_CAP
from shadowguy.tactical import (
    AimKind,
    CrewFate,
    Side,
    TacticalOutcome,
    Unit,
    enter_level,
)

# TestMenu is aliased -- an unaliased import would make pytest try (and fail, loudly
# in a warning) to collect it as a test class, since its name starts with "Test".
from shadowguy.screens.menu_screens import TestMenu as GameTestMenu
from shadowguy.screens.menu_screens import (
    ArchetypeSelectScreen,
    BuildSelectScreen,
    CorpSelectScreen,
    ModeSelectScreen,
    TitleMenu,
)
from shadowguy.gangs import GANGS, GANGS_BY_ID
from shadowguy.screens.corp_map_screen import GangTollScreen
from shadowguy.scene import BurglaryStage, Outcome, TacticalStage
from shadowguy.screens.info_screens import (
    AlarmClockScreen,
    ContactsScreen,
    CorpWebsiteScreen,
    CyberdeckScreen,
    InventoryScreen,
    MessagesScreen,
    PhoneScreen,
    SkillsScreen,
    WebScreen,
)
from shadowguy.screens.scene_screen import SceneScreen
from shadowguy.screens.shop_screens import (
    BarScreen,
    CorpHQScreen,
    GangDenScreen,
    HospitalScreen,
    JunkyardScreen,
    SafehouseScreen,
    RipperdocScreen,
    ShopScreen,
)
from shadowguy.shops import (
    CATALOG,
    CRAFT_RECIPES,
    HOSPITAL_STAY_COST,
    ITEMS_BY_ID,
    MOD_CATALOG,
    SCAVENGE_HOURS_COST,
    SCAVENGE_MATERIALS,
    Slot,
    buy_item,
)
from shadowguy.rivals import RunnerActivity, RunnerState
from shadowguy.runners import RIVAL_RUNNERS, RUNNERS_BY_ID, intro_cost
from shadowguy.screens.shop_screens import FixerOffersScreen
from textual.geometry import Offset
from textual.widgets import Collapsible, ListItem, ListView, Static

from helpers import AlwaysSix, ForcedChance, crew_stats_for


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


async def _settle(pilot) -> None:
    """Let a pending layout finish before anything is aimed by coordinate.

    A single pilot.pause() isn't enough to flush a layout-affecting change:
    pause() *ends* by calling screen._on_timer_update(), which is what actually
    runs the layout. Meanwhile pilot.click(selector) reads the target's .region
    once, up front, then pauses between the MouseDown/MouseUp/Click it posts --
    so a click can be aimed with a pre-layout region and delivered against the
    post-layout screen. The second pause runs the pending layout before any
    coordinates are taken.

    First found on the cold-boot screen: run_test() hands back a pilot while the
    first layout is still pending, which shifted TitleMenu's #new_game between
    y=18 and y=17 and put the click one row low, opening Load Game instead of
    New Game in ~2% of runs (measured: 3/150). The same race turned up again on
    an already-mounted screen after expanding a Collapsible and calling
    scroll_end() back to back -- one pause per mutation wasn't enough there
    either (test_shop_screen_buy_flow_spends_cash_and_adds_inventory /
    test_buy_deck_and_program_then_install_via_cyberdeck_screen, both flaky in
    CI). Use this instead of a bare pause() wherever a test mutates layout
    (boot, expanding/collapsing, scrolling) and then aims a coordinate-based
    click or hover at the result.
    """
    await pilot.pause()
    await pilot.pause()


async def _settle_map_boxes(pilot, screen) -> None:
    """CorpMapScreen builds #map_local_boxes (the Locals panel for the runner's own
    territory, including any "Enter" row a test wants to click) on a background asyncio
    task (_schedule_map_local_boxes starts _drain_map_local_boxes -- see the module's own
    comment on why: cancel()+immediate create_task() could race a still-mutating old
    task against a new one's remove_children/mount_all and crash). That task's own
    mount/layout work isn't bounded by a fixed number of pilot.pause()s the way a
    plain layout reflow is (_settle) -- awaiting it directly is the only way to know
    the boxes actually exist and are laid out, rather than reducing but not
    eliminating the odds a click lands before they are (measured: still ~1/45 with
    two _settle()s alone)."""
    if screen._map_locals_task is not None:
        await screen._map_locals_task
    await _settle(pilot)


def test_app_boots_to_title_menu():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            assert isinstance(app.screen, TitleMenu)

    run(body())


def test_unhandled_exception_under_test_fails_fast_instead_of_hanging():
    """ShadowguyApp._handle_exception shows a CrashScreen for real interactive
    play, but under run_test() (headless) it must fall back to Textual's own
    handling -- otherwise an unhandled exception leaves the pilot waiting on a
    CrashScreen nothing can click through, hanging the test instead of failing
    it (this is exactly what would have happened before the is_headless check:
    self._exception never gets set, so run_test()'s "re-raise so test frameworks
    are aware" never fires)."""

    def boom() -> None:
        raise RuntimeError("simulated bug")

    async def body():
        app = ShadowguyApp()
        async with app.run_test() as pilot:
            app.call_later(boom)
            await pilot.pause()

    with pytest.raises(RuntimeError, match="simulated bug"):
        run(asyncio.wait_for(body(), timeout=10))


def test_new_game_premade_archetype_and_begin_reaches_corp_map():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            assert isinstance(app.screen, ModeSelectScreen)
            await pilot.click("#runner")
            await pilot.pause()
            assert isinstance(app.screen, BuildSelectScreen)
            await pilot.click("#premade")
            await pilot.pause()
            assert isinstance(app.screen, ArchetypeSelectScreen)

            # Picking a preset spends every point and lands on the build screen,
            # where begin should then succeed. _settle, not one pause: creation's
            # async on_mount repopulates seven ListViews, and #begin is clicked by
            # coordinate right after (see CLAUDE.md's two-pause note).
            await pilot.click("#archetype_enforcer")
            await _settle(pilot)
            assert isinstance(app.screen, CharacterCreationScreen)
            character = app.character
            assert character.stat_points == 0
            assert character.skill_points == 0

            await pilot.click("#begin")
            await pilot.pause()
            # CorpMapScreen is the home screen -- MainMenu (gigs/jobs/legwork/...) is
            # one 'm' press away, not where "begin" lands directly.
            assert isinstance(app.screen, CorpMapScreen)

    run(body())


def test_every_archetype_row_is_on_screen_and_the_last_one_is_clickable():
    """The preset list is built from archetypes.ARCHETYPES, but the height it gets is
    CSS -- a sixth preset must not render off the dialog. Drives the *last* preset in
    the list, the one a too-tight cap would drop off the bottom. 80x24 because that is
    the smallest terminal this has to work at."""
    import shadowguy.archetypes as archetypes

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#runner")
            await pilot.pause()
            await pilot.click("#premade")
            await pilot.pause()
            assert isinstance(app.screen, ArchetypeSelectScreen)

            presets = archetypes.ARCHETYPES
            for preset in presets:
                row = app.screen.query_one(f"#archetype_{preset.id}")
                assert row.region.width > 0 and row.region.height > 0, preset.id

            # The list scrolls itself once the presets outgrow the cap, so scroll the
            # last row in before clicking it -- a coordinate click on a row below the
            # fold would land on whatever is painted there instead.
            last = app.screen.query_one(f"#archetype_{presets[-1].id}")
            last.scroll_visible()
            await _settle(pilot)
            assert last.region.y + last.region.height <= 24, last.region

            await pilot.click(f"#archetype_{presets[-1].id}")
            await pilot.pause()
            assert isinstance(app.screen, CharacterCreationScreen)
            assert app.character.stat_points == 0
            assert app.character.skill_points == 0

    run(body())


def test_creation_screen_keeps_begin_on_screen_on_a_small_terminal():
    """Adding a fourth and fifth preset once put a second row of auto-height cards on
    the creation screen, and on an 80x24 terminal that pushed #begin past the bottom
    edge and squeezed the build columns to a single row -- the run was unstartable
    without a taller window. The presets have their own screen now, but #begin still
    has to survive whatever else this screen grows. 80x24 because that is the classic
    default terminal, and the smallest this has to work at. Comes in by the preset
    route so the run is actually startable at the end of it."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#runner")
            await pilot.pause()
            await pilot.click("#premade")
            await pilot.pause()
            await pilot.click("#archetype_enforcer")
            await _settle(pilot)
            assert isinstance(app.screen, CharacterCreationScreen)

            begin = app.screen.query_one("#begin")
            assert begin.region.y + begin.region.height <= 24, begin.region
            # The build columns must keep usable room, not collapse to nothing.
            assert app.screen.query_one("#build_scroll").region.height >= 4

            await pilot.click("#begin")
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)

    run(body())


async def _walk_to_last_row(pilot, list_id):
    """Focus a skill column and arrow down to its final row, the way a player reaching
    for the bottom of a stat would. Returns that row's widget."""
    listview = pilot.app.screen.query_one(f"#{list_id}", ListView)
    listview.focus()
    await _settle(pilot)
    for _ in range(len(listview.children)):
        await pilot.press("down")
    await _settle(pilot)
    return listview.children[-1]


def test_creation_screen_can_reach_the_last_logic_skill_at_every_size():
    """Logic carries 11 skills now. A .build_column ListView is height:auto, so it is
    as tall as its content and scrolling a row "into view" only makes it visible inside
    that overlong list -- #build_scroll alone could not reach the bottom, and the last
    rows were painted below the terminal with nothing able to scroll to them. The player
    could still arrow onto them and spend points on rows they could not see."""
    async def body():
        for width, height in ((80, 24), (100, 40)):
            app = ShadowguyApp()
            async with app.run_test(size=(width, height)) as pilot:
                await _settle(pilot)
                await pilot.click("#new_game")
                await pilot.pause()
                await pilot.click("#runner")
                await pilot.pause()
                await pilot.click("#custom")
                await _settle(pilot)
                row = await _walk_to_last_row(pilot, "build_list_logic")
                region = row.region
                assert region.y >= 0 and region.y + region.height <= height, (
                    f"{width}x{height}: {row.id} at {region} is off a {height}-row screen"
                )

    run(body())


def test_skills_screen_can_reach_the_last_logic_skill_at_every_size():
    """Same failure on the XP-spending side, where it also had no scroller at all:
    #skills_grid sat straight on the Screen, so nothing in the chain could scroll and
    Tinkering through Demolitions were simply never drawn."""
    async def body():
        for width, height in ((80, 24), (100, 40)):
            app = ShadowguyApp()
            async with app.run_test(size=(width, height)) as pilot:
                await _settle(pilot)
                app.push_screen(SkillsScreen())
                await _settle(pilot)
                row = await _walk_to_last_row(pilot, "skill_list_logic")
                region = row.region
                assert region.y >= 0 and region.y + region.height <= height, (
                    f"{width}x{height}: {row.id} at {region} is off a {height}-row screen"
                )

    run(body())


def test_creation_screen_refuses_to_begin_with_unspent_points():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#runner")
            await pilot.pause()
            await pilot.click("#custom")
            await pilot.pause()
            # Nothing spent by hand and no preset applied -- points are still unspent.
            assert app.character.stat_points + app.character.skill_points > 0
            await pilot.click("#begin")
            await pilot.pause()
            assert isinstance(app.screen, CharacterCreationScreen)

    run(body())


def test_creation_screen_escape_goes_back_to_the_build_choice():
    """The presets have their own screen now, so a wrong turn into creation must not
    put them out of reach for the rest of the run: escape pops back to whichever
    screen pushed it, leaving the build alone (r is what blanks it)."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#runner")
            await pilot.pause()
            await pilot.click("#custom")
            await pilot.pause()
            assert isinstance(app.screen, CharacterCreationScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, BuildSelectScreen)

            # ...and back out of the preset route too, build still applied.
            await pilot.click("#premade")
            await pilot.pause()
            await pilot.click("#archetype_hacker")
            await _settle(pilot)
            assert app.character.skill_rank("cybercombat") == 6
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ArchetypeSelectScreen)
            assert app.character.skill_rank("cybercombat") == 6

    run(body())


def test_restart_run_reopens_the_build_choice():
    """restart_run used to reopen CharacterCreationScreen, which carried the presets
    itself. With them moved off it has to reopen the choice instead, or a restarted
    run could never take a preset."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            app.restart_run()
            await _settle(pilot)
            assert isinstance(app.screen, BuildSelectScreen)
            assert app.character.stat_points + app.character.skill_points > 0

    run(body())


def test_beginning_a_run_clears_the_menu_screens_out_from_under_the_map():
    """Nothing from setup may stay mounted under the home screen. It is unreachable
    while CorpMapScreen.action_back is a no-op, but a live ArchetypeSelectScreen one
    pop below home is a loaded gun: selecting a row on it calls reset_build() on the
    character mid-run (measured: a booted Enforcer's Body 4 goes back to 1). Both
    entry points -- runner and corp -- go through app.begin_run()."""
    async def body():
        for boot in (_boot_runner_game, _boot_corp_game):
            app = ShadowguyApp()
            async with app.run_test(size=(80, 60)) as pilot:
                await boot(pilot, app)
                # screen_stack[0] is Textual's own default screen, always there.
                assert [type(s).__name__ for s in app.screen_stack] == [
                    "Screen",
                    "CorpMapScreen",
                ], boot.__name__

    run(body())


def test_new_game_corp_mode_picks_faction_and_skips_creation():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            assert isinstance(app.screen, CorpSelectScreen)

            faction = FACTIONS[0]
            await pilot.click(f"#faction_{faction.id}")
            await pilot.pause()
            # Corp mode has no runner to build -- straight to CorpMapScreen (the home
            # screen, same as a runner game), no CharacterCreationScreen, and nothing
            # left in the build pools.
            assert isinstance(app.screen, CorpMapScreen)
            assert app.corp_state is not None
            assert app.corp_state.faction_id == faction.id
            assert app.character.stat_points == 0
            assert app.character.skill_points == 0
            assert app.corp_only is True

    run(body())


def test_corp_map_screen_has_sidebar_categories():
    """CorpMapScreen now carries the category sidebar natively -- no separate
    CorpMainMenu screen. 'corp' renders inline; 'phone'/'tech' push their own
    screens. escape returns to the map view from any inline category."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)

            categories = app.screen.query_one("#categories", ListView)
            assert [item.id for item in categories.children] == [
                "cat_corp",
                "cat_phone",
                "cat_tech",
            ]

            # Select the corp category to see its action list.
            await pilot.click("#cat_corp")
            await pilot.pause()
            assert any(item.id == "rest" for item in app.screen.query_one("#activities", ListView).children)

            await pilot.press("c")
            await pilot.pause()
            assert isinstance(app.screen, PhoneScreen)
            app.pop_screen()
            await pilot.pause()

            await pilot.click("#cat_tech")
            await pilot.pause()
            assert isinstance(app.screen, ResearchTreeScreen)
            app.pop_screen()
            await pilot.pause()

            # escape returns to map mode.
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)
            assert app.screen.selected_category is None

    run(body())


def test_job_ambush_choice_routes_into_an_abstract_fight_and_flee_ends_it():
    """Regression test for the Drop-import crash: selecting a job's guaranteed
    'Take them first' ambush choice must reach a live CombatScreen, and fleeing
    (which always works) must cleanly end the fight and return to the scene."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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


def test_test_menu_lists_one_row_per_tier_and_building_kind():
    """The Test menu is generated straight off ENEMY_TIERS/ICE_TIERS/BuildingKind
    rather than a hand-picked list -- assert against those same sources (not literal
    ids) so a future tier or BuildingKind addition is covered by construction, and
    this test never needs a manual update to match it."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)

            ids = [item.id for item in app.screen.query_one(ListView).children]
            expected = (
                [f"tactical_{tier}" for tier in ENEMY_TIERS]
                + [f"matrix_{tier}" for tier in ICE_TIERS]
                + [f"burglary_{kind.value}" for kind in BuildingKind]
                + ["wetwork"]
            )
            assert ids == expected

    run(body())


def test_test_menu_burglary_reaches_a_live_infiltration():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#burglary_{BuildingKind.OFFICE.value}")
            await pilot.pause()
            assert isinstance(app.screen, EntrancePickScreen)

            await pilot.click("ListView ListItem")  # pick the first entrance
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            assert not state.is_over

            # Positional escape always works, regardless of the entrance check's roll --
            # same trick the plain tactical test uses to end deterministically.
            state.player.coord = next(iter(state.exits))
            tac_screen.action_leave()
            await pilot.pause()
            assert state.is_over
            assert state.outcome is TacticalOutcome.ESCAPED

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)

    run(body())


def test_test_menu_wetwork_reaches_a_live_infiltration_of_a_compound():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click("#wetwork")
            await pilot.pause()
            assert isinstance(app.screen, EntrancePickScreen)

            await pilot.click("ListView ListItem")  # pick the first entrance
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            assert tac_screen.stage.building.kind is BuildingKind.COMPOUND

            state = tac_screen.state
            state.player.coord = next(iter(state.exits))
            tac_screen.action_leave()
            await pilot.pause()
            assert state.is_over
            assert state.outcome is TacticalOutcome.ESCAPED

            await pilot.press("enter")
            await pilot.pause()
            assert isinstance(app.screen, GameTestMenu)

    run(body())


def test_test_menu_matrix_combat_reaches_a_live_matrix_fight():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
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
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            assert not state.is_over

            # Content that doesn't depend on the randomly generated map/enemy roll.
            assert "Move (arrows/numpad)" in tac_screen.query_one("#tac_box_move").content.plain
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


def test_tactical_look_cursor_reads_the_map_without_spending_the_turn():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            moves_before = state.moves_left
            player_before = state.player.coord

            await pilot.press("x")
            assert state.aim_kind is AimKind.LOOK
            assert state.aim_cursor == player_before
            assert "You" in tac_screen.query_one("#tac_end", Static).content  # cursor opens on the player's own tile

            await pilot.press("right")
            assert state.aim_cursor == (player_before[0] + 1, player_before[1])
            assert state.player.coord == player_before  # only the cursor moved
            assert state.moves_left == moves_before  # looking costs no move or action
            assert not state.acted

            await pilot.press("escape")
            assert state.aim_cursor is None
            assert state.aim_kind is None

    run(body())


def _open_field_stage(player_start=(4, 4)):
    """A 9x9 room with one thug in the far corner: every direction off player_start is
    open floor, so a movement test doesn't depend on generate_map's RNG."""
    return TacticalStage(
        prompt="Numpad test.",
        grid=parse_grid(["." * 9] * 9),
        player_start=player_start,
        enemies=((ENEMIES_BY_ID["thug"], (8, 0)),),
        victory=Outcome(text="Cleared."),
        escape=Outcome(text="Out."),
        exits=frozenset({(0, 8)}),
    )


def test_numpad_keys_move_the_player_in_all_eight_directions():
    """1-9 (the numpad with NumLock on) walk the eight directions; home/end/pageup/pagedown
    (with it off) cover the four corners. A diagonal costs the same single move."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            app.push_screen(TacticalScreen(_open_field_stage()))
            await pilot.pause()
            state = app.screen.state

            for key, (dx, dy) in (
                ("8", (0, -1)),
                ("2", (0, 1)),
                ("4", (-1, 0)),
                ("6", (1, 0)),
                ("7", (-1, -1)),
                ("9", (1, -1)),
                ("1", (-1, 1)),
                ("3", (1, 1)),
                ("home", (-1, -1)),
                ("pageup", (1, -1)),
                ("end", (-1, 1)),
                ("pagedown", (1, 1)),
            ):
                state.player.coord = (4, 4)
                state.moves_left = 2
                await pilot.press(key)
                assert state.player.coord == (4 + dx, 4 + dy), key
                assert state.moves_left == 1, key  # a diagonal is one move, same as a cardinal

    run(body())


def test_numpad_diagonals_drive_the_aim_cursor_too():
    """While aiming, the move keys walk the cursor rather than the player -- the numpad
    diagonals are no exception."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            app.push_screen(TacticalScreen(_open_field_stage()))
            await pilot.pause()
            state = app.screen.state

            await pilot.press("x")  # look: opens the cursor on the player's own tile
            assert state.aim_cursor == (4, 4)

            await pilot.press("9")
            assert state.aim_cursor == (5, 3)
            assert state.player.coord == (4, 4)

    run(body())


def test_tactical_look_cursor_ignores_next_target_instead_of_reporting_nothing_in_reach():
    """Tab is bound to Next target (aim mode's own cursor-snap), and stays listed in the
    footer while looking -- but snap_aim_to_next_target's legality check has no branch for
    AimKind.LOOK, so it would always come back empty. action_next_target must ignore Tab
    outright while looking rather than surface that as a "nothing in reach" notification,
    which would be a lie whenever an enemy is standing right there."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            notified = []
            tac_screen.notify = lambda *args, **kwargs: notified.append(args)
            tac_screen.action_look()
            cursor_before = tac_screen.state.aim_cursor

            tac_screen.action_next_target()
            assert tac_screen.state.aim_kind is AimKind.LOOK
            assert tac_screen.state.aim_cursor == cursor_before
            assert notified == []

    run(body())


def test_tactical_map_hides_unexplored_tiles_and_renders_currently_seen_ones():
    """Fog of war, rendered: a tile the player has never had FOV on doesn't draw at all
    (blank), while one currently in FOV draws its terrain glyph -- not a space, since
    _terrain_glyph never returns one."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            grid = state.grid
            explored = state.explored[state.level_index]
            assert 0 < len(explored) < grid.width * grid.height  # sanity: fog hides something

            map_lines = tac_screen.query_one("#tac_map", Static).content.plain.split("\n")
            unexplored = next(
                (x, y) for y in range(grid.height) for x in range(grid.width) if (x, y) not in explored
            )
            assert map_lines[unexplored[1]][unexplored[0]] == " "

            seen = visible_tiles(grid, state.player.coord)
            visible_floor = next(
                (x, y)
                for y in range(grid.height)
                for x in range(grid.width)
                if seen[y, x] and grid.tile((x, y)) is Tile.FLOOR
            )
            assert map_lines[visible_floor[1]][visible_floor[0]] != " "

    run(body())


def test_tactical_look_reports_unknown_for_a_tile_never_seen():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            grid = state.grid
            explored = state.explored[state.level_index]
            unexplored = next(
                (x, y) for y in range(grid.height) for x in range(grid.width) if (x, y) not in explored
            )

            tac_screen.action_look()
            state.aim_cursor = unexplored
            tac_screen._refresh()
            assert tac_screen.query_one("#tac_end", Static).content.startswith("Unknown")

    run(body())


def test_burglary_look_cursor_names_a_locked_camera_watched_objective():
    """The look cursor's building-specific notes (camera/locked door/objective) are each
    gated on state.level_index -- force all three onto one cell on the level the player
    actually lands on, rather than hoping the random building put them there, so the
    assertion runs every time regardless of what generate_building rolled."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click("#wetwork")  # Compound: always has at least one camera
            await pilot.pause()
            await pilot.click("ListView ListItem")  # pick the first entrance
            await pilot.pause()
            assert isinstance(app.screen, TacticalScreen)

            tac_screen = app.screen
            state = tac_screen.state
            camera_level, coord = state.building.cameras[0]
            enter_level(state, camera_level, coord)
            state.building.locks[(state.level_index, coord)] = Lock(skill="hack", difficulty=12)
            state.objective = (state.level_index, coord)

            tac_screen.action_look()
            end_text = tac_screen.query_one("#tac_end", Static).content
            assert "camera" in end_text
            assert "locked door (hack)" in end_text
            assert "the objective" in end_text

            # Building.links_at (buildings.py) is the one place "is this cell a stair"
            # is answered elsewhere in the codebase -- the look cursor should agree with
            # it rather than carrying its own copy of the same check.
            link = state.building.links[0]
            link_level, link_coord = link.a
            enter_level(state, link_level, link_coord)
            state.aim_cursor = link_coord  # already looking; just move the cursor onto the link
            tac_screen._refresh()
            assert "stairs" in tac_screen.query_one("#tac_end", Static).content

    run(body())


def _open_strip(state, *, player, units):
    """Swap a live tactical fight onto a hand-placed board: an open 12x3 strip with the
    given units at the given coords. Both the generated map and the rolled enemy count are
    RNG-driven, so a test that needs a specific sightline builds its own."""
    state.grid = parse_grid(["." * 12 for _ in range(3)])
    state.units = [state.player, *units]
    state.player.coord = player
    state.exits = frozenset({(0, 2)})  # the generated map's exits are off this strip


def test_tactical_attack_aims_first_and_tab_cycles_targets_before_enter_fires():
    """Pressing f opens the aim cursor instead of firing outright; Tab walks it between
    the enemies in reach (the Screen's own tab->focus_next binding must not eat the key),
    Esc backs out spending nothing, and Enter resolves against whoever is under it."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            tac_screen = app.screen
            state = tac_screen.state

            # Two enemies in a straight line the player can see, at different ranges,
            # and a gun to reach them with.
            thug = ENEMIES_BY_ID["thug"]
            near = Unit(name="near thug", side=Side.ENEMY, coord=(2, 0), speed=4, stats=thug, health=thug.health)
            far = Unit(name="far thug", side=Side.ENEMY, coord=(5, 0), speed=4, stats=thug, health=thug.health)
            _open_strip(state, player=(0, 0), units=[near, far])
            app.character.inventory.append(InventoryItem(item_id="pipe_pistol", equipped=True))

            await pilot.press("f")
            await pilot.pause()
            assert state.aim_kind is AimKind.ATTACK
            assert state.aim_cursor == near.coord  # opens on the default target
            assert not state.acted  # aiming spends nothing

            await pilot.press("tab")
            await pilot.pause()
            assert state.aim_cursor == far.coord
            assert f"→ {far.name}" in tac_screen.query_one("#tac_box_attack").content.plain

            await pilot.press("escape")
            await pilot.pause()
            assert state.aim_cursor is None and state.aim_kind is None
            assert not state.acted

            # Aim again and fire: the shot resolves against the aimed enemy.
            await pilot.press("f")
            await pilot.press("tab")
            await pilot.pause()
            assert state.aim_cursor == far.coord
            await pilot.press("enter")
            await pilot.pause()
            assert state.acted
            assert state.aim_cursor is None
            assert state.log  # hit or miss, the shot was taken

    run(body())


def test_tactical_fight_with_a_hired_runner_renders_them_and_lets_them_fight():
    """A crew hire reaches the grid as a real unit: rendered as 'A', listed in the Crew
    HUD tile with their health, never targetable by the player's own aim cursor, and
    taking their own attack on end-turn."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            stage = app.screen.stage
            app.pop_screen()
            await pilot.pause()

            solo = crew_stats_for()
            app.push_screen(TacticalScreen(stage, allies=[solo]))
            await pilot.pause()
            tac_screen = app.screen
            state = tac_screen.state

            ally = state.allies[0]
            assert ally.name == solo.name
            assert f"{solo.name} {ally.health}/{solo.health}" in tac_screen.query_one("#tac_box_crew").content.plain
            assert tac_screen.query_one("#tac_box_crew").display is True
            assert "A" in tac_screen.query_one("#tac_map").content.plain

            # The player's own targeting never offers them (no friendly fire).
            tac_screen.action_fire()
            await pilot.pause()
            if state.aim_cursor is not None:
                assert state.aim_cursor != ally.coord

            # End the turn on a hand-placed board (the generated one's corridors decide
            # whether anyone has a shot): the hire is a Solo with reach 6, standing six
            # tiles off an enemy in the open, so their phase resolves into an attack.
            enemy = state.enemies[0]
            ally.coord, enemy.coord = (0, 1), (6, 1)
            _open_strip(state, player=(0, 0), units=[ally, enemy])
            await pilot.press("e")
            await pilot.pause()
            assert any(solo.name in line for line in state.log)

    run(body())


def test_tactical_stabilize_key_patches_a_downed_hire_and_the_fight_reports_their_fate():
    """'s' spends a carried health kit on a downed hire you're standing next to, the Crew
    tile shows them stable rather than DOWN, and the end-of-fight line says what became
    of them (a stabilized hire on a won fight always walks away)."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#test")
            await pilot.pause()
            await pilot.click(f"#tactical_{min(ENEMY_TIERS)}")
            await pilot.pause()
            stage = app.screen.stage
            app.pop_screen()
            await pilot.pause()

            solo = crew_stats_for()
            app.character.consumables.append("health_kit")
            app.character.hire_for_job(solo.id, "job_1")
            app.push_screen(TacticalScreen(stage, allies=[solo]))
            await pilot.pause()
            tac_screen = app.screen
            state = tac_screen.state

            ally = state.allies[0]
            ally.health = 0  # they went down
            ally.coord = next(iter(step_neighbors(state.grid, state.player.coord)))
            await pilot.press("s")
            await pilot.pause()
            assert ally.stabilized
            assert app.character.consumables == []  # kit spent
            assert "stable" in tac_screen.query_one("#tac_box_crew").content.plain

            # Clear the board and end the turn: the fight ending is what settles the
            # hire's fate (the engine's job), and the end line reports it.
            for enemy in state.enemies:
                enemy.health = 0
            await pilot.press("e")
            await pilot.pause()
            assert state.outcome is TacticalOutcome.VICTORY
            assert state.crew_aftermath == [(solo.name, CrewFate.RECOVERED)]
            assert "back on the street" in str(tac_screen.query_one("#tac_end").content)
            assert app.character.on_crew(solo.id)  # a recovered hire stays hired

    run(body())


def test_shop_screen_buy_flow_spends_cash_and_adds_inventory():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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
            # A live gig at this location adds an extra row to its box's ListView,
            # ahead of the "Enter" row this test clicks (corp_map_screen._location_box)
            # -- gigs.refresh_gigs spawns one probabilistically now, so without this the
            # "Enter" row's position (and thus this test's click) would vary by run.
            app.location_gigs.pop(shop_location.id, None)

            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)
            app.screen.query_one(f"#map_local_box_{shop_location.id}", Collapsible).collapsed = False
            await _settle(pilot)
            app.screen.query_one("#map_local_boxes_scroll").scroll_end(animate=False, immediate=True)
            await _settle(pilot)
            await pilot.click(f"#map_local_{shop_location.id}")
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


def test_buy_deck_and_program_then_install_via_cyberdeck_screen():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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
            # See test_shop_screen_buy_flow_spends_cash_and_adds_inventory: a live gig
            # here would shift the "Enter" row this test clicks.
            app.location_gigs.pop(store_location.id, None)

            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)
            app.screen.query_one(f"#map_local_box_{store_location.id}", Collapsible).collapsed = False
            await _settle(pilot)
            app.screen.query_one("#map_local_boxes_scroll").scroll_end(animate=False, immediate=True)
            await _settle(pilot)
            await pilot.click(f"#map_local_{store_location.id}")
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
            assert isinstance(app.screen, CorpMapScreen)

            await pilot.click("#cat_cyberdeck")
            await pilot.pause()
            assert isinstance(app.screen, CyberdeckScreen)

            await pilot.click(f"#install_{deck_index}_sleaze")
            await pilot.pause()
            assert app.character.inventory[deck_index].installed_programs == ["sleaze"]

    run(body())


def test_cyberdeck_menu_option_reaches_the_screen_with_no_decks_owned():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()

            categories = app.screen.query_one("#categories", ListView)
            assert "cat_cyberdeck" in [item.id for item in categories.children]

            await pilot.click("#cat_cyberdeck")
            await pilot.pause()
            assert isinstance(app.screen, CyberdeckScreen)

            items = app.screen.query_one("#cyberdeck_items", ListView)
            assert [item.id for item in items.children] == ["no_deck"]

    run(body())


def test_corp_map_screen_travel_moves_the_runner_to_a_bordering_territory():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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


def test_corp_only_travel_is_free_and_instant():
    """A corp-only game never builds a runner (ShadowguyApp.corp_only): every corp
    action already reads off CorpState, never character.location_id, so repositioning
    the map cursor shouldn't cost the time or gang-encounter risk a runner's real
    travel does."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_corp_game(pilot, app)
            app.push_screen(CorpMapScreen())
            await pilot.pause()

            start_id = app.character.location_id
            neighbor_id = app.corp_map.territories[start_id].connections[0]
            screen = app.screen
            screen.selected_id = neighbor_id
            screen._refresh()
            await pilot.pause()

            elapsed_before = app.character.elapsed_hours
            await pilot.press("enter")
            await pilot.pause()

            assert app.character.location_id == neighbor_id
            assert app.character.elapsed_hours == elapsed_before  # not a single hour spent
            assert isinstance(app.screen, CorpMapScreen)  # no gang encounter pushed a fight

    run(body())


def test_corp_only_rest_waives_lodging_regardless_of_location():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_corp_game(pilot, app)
            # Park on the priciest territory on the map to prove this isn't just
            # "this particular spot happens to be free."
            priciest = max(app.corp_map.territories.values(), key=lodging_cost)
            assert lodging_cost(priciest) > 0
            app.character.location_id = priciest.id

            assert app.rest_cost() == 0
            cash_before = app.character.cash
            app.rest()
            assert app.character.cash == cash_before

    run(body())

    run(body())


def test_spend_time_fires_the_day_tick_once_per_boundary_crossed():
    """spend_time's per-boundary loop only ever fires once with today's in-game costs
    (nothing spends >=2*HOURS_PER_DAY in one call) -- this proves the loop itself
    actually iterates more than once when a spend crosses more than one boundary,
    since no real call site exercises that path."""
    from shadowguy.corp_turn import CorpState, collect_income

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            app.corp_state = CorpState(faction_id=FACTIONS[0].id)
            one_day_income = collect_income(app.corp_state, app.corp_map)
            assert one_day_income > 0
            cash_before = app.corp_state.cash

            app.spend_time(HOURS_PER_DAY * 2 + 3)

            assert app.character.day == 3
            # Bounded, not exact: since the conflict layer landed, a rival can capture
            # one of the player's districts on either tick, and income is per-territory.
            # The corp can only *lose* ground here (resolve_rival_day skips the player's
            # own faction as an actor), so two ticks credit strictly more than zero and
            # at most two full days' worth. The rival_actions count below is what proves
            # the loop iterated twice; this just proves income was collected in it.
            assert cash_before < app.corp_state.cash <= cash_before + 2 * one_day_income
            # rival_actions must accumulate across both boundaries crossed in this one
            # spend, not just keep the last day's -- one action per non-player faction
            # plus one per not-on-crew rival runner, per day ticked.
            actions_per_day = (len(FACTIONS) - 1) + len(app.runners)
            assert len(app.rival_actions) == 2 * actions_per_day

    run(body())


def test_hospital_stay_advances_one_day_and_skips_the_lodging_charge():
    """A hospital stay spends exactly HOURS_PER_DAY hours with skip_night_effects=True
    -- it still ticks the day over (crew wages, offer refresh, etc.) but must not also
    charge lodging that night, since the stay already covers room and board."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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
            # A day in a hospital bed counts as rest, same as app.rest().
            assert app.character.last_rest_hour == app.character.elapsed_hours

    run(body())


def test_rest_charges_lodging_immediately_not_at_a_midnight_crossing():
    """Lodging used to be a side effect of whichever action crossed midnight; now it's
    charged by rest() itself, at whatever territory the runner is standing in when
    they take it -- even though REST_HOURS_COST (8) alone doesn't cross a day
    boundary from a fresh character's elapsed_hours=0."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            territory = next(
                t for t in app.corp_map.territories.values()
                if not has_home(t) and lodging_cost(t) > 0
            )
            app.character.location_id = territory.id
            cash_before = app.character.cash
            day_before = app.character.day

            app.rest()

            assert app.character.day == day_before  # no midnight crossed
            assert app.character.cash == cash_before - lodging_cost(territory)

    run(body())


def test_rest_is_free_at_home_and_resets_fatigue_by_half():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            # The start tile always carries the runner's free apartment.
            assert has_home(app.corp_map.territories[app.character.location_id])
            app.character.fatigue = 6
            cash_before = app.character.cash

            app.rest()

            assert app.character.cash == cash_before
            assert app.character.fatigue == 3
            assert app.character.last_rest_hour == app.character.elapsed_hours == REST_HOURS_COST

    run(body())


def test_rest_always_heals_one_point_regardless_of_health_kit_or_location():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            character = app.character
            character.health = character.max_health - 5
            assert not character.health_kit_used_today
            before = character.health

            app.rest()

            assert character.health == before + 1

    run(body())


def test_rest_healing_survives_a_rest_that_crosses_midnight():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            character = app.character
            character.health = character.max_health - 5
            character.elapsed_hours = HOURS_PER_DAY - 1  # one hour from midnight
            before = character.health

            app.rest()  # REST_HOURS_COST (8h) crosses the boundary

            assert character.day == 2
            assert character.health == before + 1

    run(body())


def test_rest_healing_does_not_exceed_max_health():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            character = app.character
            assert character.health == character.max_health

            app.rest()

            assert character.health == character.max_health

    run(body())


def test_scavenging_a_junkyard_spends_hours_not_a_day_and_grants_loot():
    """A Junkyard's one action costs SCAVENGE_HOURS_COST, not a full day like a hospital
    stay -- and a runner with a sky-high Tinkering value is certain to clear the check
    (opposing pool is a fixed handful of dice at SCAVENGE_DIFFICULTY), so the resulting
    inventory gain is a real assertion, not a coin flip."""

    class AlwaysSix(random.Random):
        def randint(self, a, b):
            return 6

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()

            app.rng = AlwaysSix()
            app.character.logic = 20
            hours_before = app.character.elapsed_hours

            junkyard_location = Location(id="test_junkyard", name="Test Yard", kind=LocationKind.JUNKYARD)
            app.push_screen(JunkyardScreen(junkyard_location))
            await pilot.pause()
            await pilot.click("#scavenge")
            await pilot.pause()

            assert app.character.elapsed_hours == hours_before + SCAVENGE_HOURS_COST
            assert app.character.inventory
            assert all(entry.item_id in SCAVENGE_MATERIALS for entry in app.character.inventory)

    run(body())


def test_workshop_builds_installs_a_mod_and_crafts_a_consumable():
    """SafehouseScreen end to end: build a workshop, install a Mod on an owned weapon
    (rolls Armorer), and craft a Consumable from scavenged materials (rolls Chemistry)
    -- exercising shops.effective_item through the real UI, not just its unit math."""

    class AlwaysSix(random.Random):
        def randint(self, a, b):
            return 6

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()

            app.rng = AlwaysSix()
            character = app.character
            character.logic = 20
            character.cash = 10_000
            weapon = next(item for items in CATALOG.values() for item in items if item.slot is Slot.WEAPON)
            buy_item(character, weapon)
            weapon_mod = next(m for m in MOD_CATALOG if m.applies_to == frozenset({Slot.WEAPON}))
            craftable_id = next(iter(CRAFT_RECIPES))
            for materials in (weapon_mod.materials, CRAFT_RECIPES[craftable_id]):
                for material_id, count in materials.items():
                    for _ in range(count):
                        character.inventory.append(InventoryItem(material_id, equipped=False))
            cash_before_workshop = character.cash

            location = Location(
                id="test_safehouse", name="Test Safehouse", kind=LocationKind.SAFEHOUSE, workshop_built=False
            )
            app.push_screen(SafehouseScreen(location))
            await pilot.pause()
            await pilot.click("#build_workshop")
            await pilot.pause()
            assert location.workshop_built is True
            assert character.cash == cash_before_workshop - WORKSHOP_BUILD_COST

            actions = app.screen.query_one("#workshop_actions", ListView)
            mod_item_id = next(item.id for item in actions.children if item.id and item.id.startswith("mod_"))
            await pilot.click(f"#{mod_item_id}")
            await pilot.pause()
            assert character.inventory[0].mods == [weapon_mod.id]

            await pilot.click(f"#craft_{craftable_id}")
            await pilot.pause()
            assert craftable_id in character.consumables

    run(body())


def test_workshop_declined_mod_install_spends_no_time():
    """A precondition failure (here: no materials on hand) must not cost the player
    WORKSHOP_HOURS_COST -- the row is still listed and clickable (SafehouseScreen only
    filters slot mismatch/duplicate/full-slots, not missing materials), so clicking it
    is a real path a player can hit, not just a theoretical one."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()

            character = app.character
            character.cash = 10_000
            weapon = next(item for items in CATALOG.values() for item in items if item.slot is Slot.WEAPON)
            buy_item(character, weapon)  # no materials granted

            location = Location(
                id="test_safehouse", name="Test Safehouse", kind=LocationKind.SAFEHOUSE, workshop_built=True
            )
            app.push_screen(SafehouseScreen(location))
            await pilot.pause()

            actions = app.screen.query_one("#workshop_actions", ListView)
            mod_item_id = next(item.id for item in actions.children if item.id and item.id.startswith("mod_"))
            hours_before = character.elapsed_hours
            await pilot.click(f"#{mod_item_id}")
            await pilot.pause()

            assert character.inventory[0].mods == []
            assert character.elapsed_hours == hours_before

    run(body())


def test_inventory_screen_shows_a_mods_damage_bonus():
    """The workshop's whole point, seen from the Inventory screen: a mod attached at
    the bench must change what the player sees they're carrying, not just what
    combat actually rolls."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()

            character = app.character
            character.cash = 10_000
            weapon = ITEMS_BY_ID["combat_knife"]
            buy_item(character, weapon)
            weapon_mod = next(m for m in MOD_CATALOG if m.applies_to == frozenset({Slot.WEAPON}))
            character.inventory[0].mods = [weapon_mod.id]

            app.push_screen(InventoryScreen())
            await pilot.pause()

            row = app.screen.query_one("#toggle_0", ListItem)
            label = row.query_one(Static).content
            assert f"{weapon.damage + weapon_mod.damage} dmg" in label

    run(body())


def _find_gang_den(app):
    """The first real gang den on this run's generated map, and the territory it's
    seated in -- corpmap_gen._make_gang_den only builds one per gang (den_ids), so a
    hand-set Territory.gang_id (see _stage_gang_turf) wouldn't have a Location to
    click on."""
    return next(
        (t, loc)
        for t in app.corp_map.territories.values()
        for loc in t.locations
        if loc.kind == LocationKind.GANG_DEN
    )


def test_taking_a_smuggling_job_sets_it_and_den_refuses_a_second():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            den_territory, den_location = _find_gang_den(app)
            app.character.location_id = den_territory.id
            gang = GANGS_BY_ID[den_territory.gang_id]

            app.push_screen(GangDenScreen(den_location, gang))
            await pilot.pause()
            await pilot.click("#take_job")
            await pilot.pause()

            job = app.character.smuggling_job
            assert job is not None
            assert job.gang_id == gang.id
            assert job.destination_territory_id != den_territory.id

            # A second den visit while one's already running offers no new job.
            await pilot.click("#busy")
            await pilot.pause()
            assert app.character.smuggling_job is job

    run(body())


def test_delivering_a_smuggling_job_only_pays_out_on_site():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            den_territory, den_location = _find_gang_den(app)
            gang = GANGS_BY_ID[den_territory.gang_id]
            app.character.location_id = den_territory.id

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            await pilot.click("#cat_job")
            await pilot.pause()
            app.push_screen(GangDenScreen(den_location, gang))
            await pilot.pause()
            await pilot.click("#take_job")
            await pilot.pause()
            job = app.character.smuggling_job

            await pilot.press("escape")
            await pilot.pause()

            # Not at the destination yet -- clicking must not pay out. The Jobs tab is
            # still selected after the escape, so the activities list (with
            # #deliver_package) is already the visible content.
            cash_before = app.character.cash
            standing_before = app.character.gang_standing_with(gang.id)
            await pilot.click("#deliver_package")
            await pilot.pause()
            assert app.character.smuggling_job is job
            assert app.character.cash == cash_before

            app.character.location_id = job.destination_territory_id
            await app.screen._refresh_activities()
            await pilot.pause()
            await pilot.click("#deliver_package")
            await pilot.pause()

            assert app.character.smuggling_job is None
            assert app.character.cash == cash_before + job.reward_cash
            assert app.character.gang_standing_with(gang.id) == standing_before + GANG_JOB_STANDING_GAIN

    run(body())


def test_missing_a_smuggling_deadline_clears_it_and_costs_gang_standing():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)):
            den_territory, _den_location = _find_gang_den(app)
            gang_id = den_territory.gang_id
            job = generate_smuggling_job(gang_id, den_territory.id, app.corp_map, app.character.day, app.rng)
            app.character.smuggling_job = job
            standing_before = app.character.gang_standing_with(gang_id)

            days_to_pass = job.deadline_day - app.character.day + 1
            app.spend_time(HOURS_PER_DAY * days_to_pass)

            assert app.character.smuggling_job is None
            assert app.character.gang_standing_with(gang_id) == standing_before - GANG_JOB_STANDING_GAIN

    run(body())


def test_deliver_package_with_no_active_job_is_a_safe_no_op():
    """A #deliver_package row rendered while a job was active can go stale if the
    job clears out from under it (e.g. a missed deadline) before the next refresh
    -- clicking it must not crash on character.smuggling_job being None."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            den_territory, den_location = _find_gang_den(app)
            gang = GANGS_BY_ID[den_territory.gang_id]
            app.character.location_id = den_territory.id

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            await pilot.click("#cat_job")
            await pilot.pause()
            app.push_screen(GangDenScreen(den_location, gang))
            await pilot.pause()
            await pilot.click("#take_job")
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()

            # The row is on screen from the click above; clear the job out from under
            # it without refreshing, the way a missed deadline mid-render would.
            app.character.smuggling_job = None
            await pilot.click("#deliver_package")
            await pilot.pause()

            assert app.character.smuggling_job is None

    run(body())


def test_running_a_job_that_crosses_midnight_does_not_expire_itself_or_drop_its_crew():
    """Regression test: the job-run handler used to call spend_time() (charging the
    job's own hours_cost) before pushing its Scene, and if that spend crossed
    midnight, the resulting day tick would prune the very job -- and discharge any
    crew hired for it -- out from under itself. protect_job_id (threaded through
    spend_time) exists to stop exactly this."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            await pilot.click("#cat_job")
            # Selecting Jobs hides the sidebar -- a layout-affecting mutation, so two
            # pauses before the coordinate click on the job row.
            await _settle(pilot)
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
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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


def test_corp_screen_expand_and_rest():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
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

            # A corp is taken at its HQ now (CorpHQScreen, gated on rep/standing/cash);
            # this test is about what you do once you have one, so stage it directly.
            app.corp_state = CorpState(faction_id=faction_id)
            await app.screen._refresh()
            await pilot.pause()
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
            # Rest is 8 hours now, not a full day -- click it enough times (3 * 8 =
            # HOURS_PER_DAY) to actually cross a day boundary and fire the tick.
            for _ in range(HOURS_PER_DAY // REST_HOURS_COST):
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
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            await pilot.click("#cat_corp")
            await pilot.pause()

            faction = FACTIONS[0]
            # A corp is taken at its HQ now (CorpHQScreen, gated on rep/standing/cash);
            # this test is about what you do once you have one, so stage it directly.
            app.corp_state = CorpState(faction_id=faction.id)
            await app.screen._refresh()
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
            # Training is queued, not instant: the batch sits in pending_recruit and
            # the pool doesn't grow until it completes on a later day tick.
            assert app.corp_state.scientists == scientists_before
            assert app.corp_state.pending_recruit is not None
            assert app.corp_state.daily_action_used is True
            # While a batch trains, the Academy shows its progress row, not new offers.
            academy_ids = {item.id for item in academy_list.children}
            assert academy_ids == {"pending_recruit"}

    run(body())


def test_corp_map_screen_corp_sections_stack_top_to_bottom():
    """Regression test for layout: the corp's action list, academy_panel, and
    research_panel must stack top-to-bottom in the main panel without overlapping.
    Height: auto overrides prevent ListView's default 1fr from squashing them."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)
            await pilot.click("#cat_corp")
            await pilot.pause()

            sidebar = app.screen.query_one("#sidebar")
            main_panel = app.screen.query_one("#main_panel")
            activities = app.screen.query_one("#activities", ListView)
            academy_panel = app.screen.query_one("#academy_panel")
            research_panel = app.screen.query_one("#research_panel")

            # The sidebar and main panel are side by side.
            assert sidebar.region.x + sidebar.region.width <= main_panel.region.x

            # Each section starts at or after the previous one's bottom edge -- top to
            # bottom, never overlapping.
            assert activities.region.y + activities.region.height <= academy_panel.region.y
            assert academy_panel.region.y + academy_panel.region.height <= research_panel.region.y

    run(body())


def test_phone_screen_lists_four_apps_in_a_grid():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(PhoneScreen())
            await pilot.pause()

            apps = app.screen.query_one("#phone_apps", ListView)
            assert [item.id for item in apps.children] == [
                "app_contacts",
                "app_web",
                "app_alarm",
                "app_messages",
            ]

    run(body())


def test_phone_app_tiles_open_their_own_screens():
    """Tapping an app tile opens a dedicated screen -- the phone's home grid,
    not an inline expansion."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            for key, screen_cls in (
                ("app_contacts", ContactsScreen),
                ("app_web", WebScreen),
                ("app_alarm", AlarmClockScreen),
                ("app_messages", MessagesScreen),
            ):
                app.push_screen(PhoneScreen())
                await pilot.pause()
                apps = app.screen.query_one("#phone_apps", ListView)
                apps.focus()
                apps.index = next(i for i, item in enumerate(apps.children) if item.id == key)
                await pilot.pause()
                await pilot.press("enter")
                await pilot.pause()
                assert isinstance(app.screen, screen_cls)
                app.pop_screen()
                await pilot.pause()
                app.pop_screen()
                await pilot.pause()

    run(body())


def test_contacts_screen_panels_are_collapsibles_expanded_by_default():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(ContactsScreen())
            await pilot.pause()

            panels = {
                pid: app.screen.query_one(f"#{pid}", Collapsible)
                for pid in (
                    "fixers_panel",
                    "locals_panel",
                    "runners_panel",
                )
            }
            assert len(panels) == 3
            assert all(not panel.collapsed for panel in panels.values())

    run(body())


def test_contacts_panel_nav_skips_a_collapsed_section():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(ContactsScreen())
            await pilot.pause()

            screen = app.screen
            screen.query_one("#locals_panel", Collapsible).collapsed = True
            await pilot.pause()
            screen.query_one("#fixers_list", ListView).focus()
            await pilot.pause()
            screen.action_focus_panel(1)
            await pilot.pause()
            assert screen.focused is screen.query_one("#runners_list", ListView)

    run(body())


def test_local_boxes_collapsed_by_default_and_accordion_to_one_open():
    """The Local category's #local_boxes is one Collapsible per Location (plus a
    Fixers box), phone-tile style -- all start collapsed, and opening one closes
    any other that was open (CorpMapScreen.on_collapsible_expanded)."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)

            boxes = list(app.screen.query("#map_local_boxes Collapsible"))
            assert len(boxes) >= 2  # at least one Location plus the Fixers box
            assert all(box.collapsed for box in boxes)

            boxes[0].collapsed = False
            await pilot.pause()
            assert boxes[0].collapsed is False

            boxes[1].collapsed = False
            await pilot.pause()
            assert boxes[0].collapsed is True, "opening box1 should have closed box0"
            assert boxes[1].collapsed is False

    run(body())


def test_map_hover_boxes_visible_in_map_mode_and_hidden_otherwise():
    """#map_local_boxes_scroll is the hover-preview panel below the map, shown
    only in map mode and hidden on every sidebar category tab."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            screen = app.screen

            assert screen.query_one("#map_local_boxes_scroll").display is True

            await pilot.click("#cat_job")
            await pilot.pause()
            assert screen.query_one("#map_local_boxes_scroll").display is False

    run(body())


def test_jobs_and_legwork_tabs_hide_the_sidebar_and_escape_brings_it_back():
    """The Jobs and Legwork activity lists take the full width: the category sidebar
    is hidden while either is selected, and 'escape' restores it with the map."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            screen = app.screen
            sidebar = screen.query_one("#sidebar")
            activities = screen.query_one("#activities", ListView)

            assert sidebar.display is True

            for category in ("job", "legwork"):
                screen.selected_category = category
                await screen._refresh_activities()
                await _settle(pilot)
                assert sidebar.display is False
                # ... and the activity list really does get the whole width.
                assert activities.region.x == 0
                assert activities.region.width == 80

                await pilot.press("escape")
                await _settle(pilot)
                assert screen.selected_category is None
                assert sidebar.display is True

            # A tab that isn't full-width keeps the sidebar.
            screen.selected_category = "corp"
            await screen._refresh_activities()
            await pilot.pause()
            assert sidebar.display is True

    run(body())


def _hover_territory(screen, territory_id: str) -> None:
    """Drive CorpMapScreen.on_mouse_move onto a territory's map label.

    Goes through the real handler (and its own hit-test) rather than a coordinate
    pilot.hover(): the map is a ~240-column ASCII blob inside a horizontally scrolled
    container, so deriving true screen coordinates for one label would be fragile in a
    way that reading its NodeSpan is not. on_mouse_move only ever asks the event for a
    content offset into #map, which is all this stands in for."""
    span = next(s for s in screen.rendered.spans if s.territory_id == territory_id)

    class _MouseMove:
        def get_content_offset(self, widget):
            return Offset(span.start, span.line)

    screen.on_mouse_move(_MouseMove())


def test_hovering_a_territory_leaves_the_locals_panel_on_the_current_location():
    """The Locals panel below the map shows where the runner is *standing*. Mousing over
    another territory retargets the summary bar between the two and nothing else -- the
    panel used to follow the cursor and preview whatever was under it."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)
            screen = app.screen

            here = app.corp_map.territories[app.character.location_id]
            neighbor = app.corp_map.territories[here.connections[0]]
            expected = {f"{loc.name} ({loc.kind})" for loc in here.locations} | {"Fixers", "Rest"}
            assert {box.title for box in screen.query("#map_local_boxes Collapsible")} == expected

            _hover_territory(screen, neighbor.id)
            await _settle_map_boxes(pilot, screen)

            # The panel is untouched...
            assert {box.title for box in screen.query("#map_local_boxes Collapsible")} == expected, (
                "hovering another territory must not repoint the Locals panel"
            )
            # ...while the bar between map and panel is exactly what may follow the mouse.
            summary = screen.query_one("#territory_summary", Static)
            assert neighbor.name in str(summary.content)

    run(body())


def test_hovering_does_not_collapse_an_open_locals_box():
    """The other half of "the panel doesn't change on hover", and the reason the hover
    path must not rebuild it at all: a rebuild replaces those widgets outright, so
    refreshing on mouse-move would snap shut whichever box the player had open."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)
            screen = app.screen

            here = app.corp_map.territories[app.character.location_id]
            box = screen.query_one(f"#map_local_box_{here.locations[0].id}", Collapsible)
            box.collapsed = False
            await _settle(pilot)
            assert box.collapsed is False

            _hover_territory(screen, here.connections[0])
            await _settle_map_boxes(pilot, screen)

            assert box.collapsed is False, "a mouse-move must not close an open box"
            assert box.parent is not None, "the box should be the same widget, not a rebuild"

    run(body())


def test_a_locals_box_is_openable_and_enterable_while_hovering_elsewhere():
    """Every box in the panel is somewhere the runner can act, so opening one needs no
    travel gate any more (on_collapsible_expanded used to veto it and say "Travel to X
    first"). The "Enter" row also has to resolve against the runner's own territory
    rather than the cursor's -- looking the id up in a hovered territory finds nothing."""
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            shop_location = shop_territory_id = None
            for territory in app.corp_map.territories.values():
                for location in territory.locations:
                    if location.kind == LocationKind.WEAPON_SHOP:
                        shop_location, shop_territory_id = location, territory.id
                        break
                if shop_location:
                    break
            assert shop_location is not None
            app.character.location_id = shop_territory_id
            # Same reason as the buy-flow test: a live gig would add a row ahead of "Enter".
            app.location_gigs.pop(shop_location.id, None)

            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)
            screen = app.screen

            here = app.corp_map.territories[shop_territory_id]
            _hover_territory(screen, here.connections[0])
            await _settle_map_boxes(pilot, screen)

            box = screen.query_one(f"#map_local_box_{shop_location.id}", Collapsible)
            box.collapsed = False
            await _settle(pilot)
            assert box.collapsed is False, "opening a box in your own territory is never vetoed"

            screen.query_one("#map_local_boxes_scroll").scroll_end(animate=False, immediate=True)
            await _settle(pilot)
            await pilot.click(f"#map_local_{shop_location.id}")
            await pilot.pause()
            assert isinstance(app.screen, ShopScreen)

    run(body())


def test_entering_gang_turf_at_minor_negative_prompts_a_toll_and_paying_deducts_cash():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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
        async with app.run_test(size=(80, 60)) as pilot:
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
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            await pilot.click("#cat_corp")
            await pilot.pause()

            faction = FACTIONS[0]
            # A corp is taken at its HQ now (CorpHQScreen, gated on rep/standing/cash);
            # this test is about what you do once you have one, so stage it directly.
            app.corp_state = CorpState(faction_id=faction.id)
            await app.screen._refresh()
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


def _boot_runner_game(pilot, app):
    """Title -> Runner -> premade archetype -> CorpMapScreen (the home screen, which
    now carries the sidebar natively — no 'm' press needed any more)."""

    async def go():
        await _settle(pilot)
        await pilot.click("#new_game")
        await pilot.pause()
        await pilot.click("#runner")
        await pilot.pause()
        await pilot.click("#premade")
        await pilot.pause()
        await pilot.click("#archetype_enforcer")
        # Two pauses: creation's async on_mount rebuilds seven ListViews between this
        # push and the coordinate click on #begin below.
        await _settle(pilot)
        await pilot.click("#begin")
        await pilot.pause()
        assert isinstance(app.screen, CorpMapScreen)

    return go()


def _boot_corp_game(pilot, app):
    """Title -> Corp -> pick a faction -> CorpMapScreen (the home screen, which now
    carries the sidebar natively — no 'm' press needed any more)."""

    async def go():
        await _settle(pilot)
        await pilot.click("#new_game")
        await pilot.pause()
        await pilot.click("#corp")
        await pilot.pause()
        await pilot.click(f"#faction_{FACTIONS[0].id}")
        await pilot.pause()
        assert isinstance(app.screen, CorpMapScreen)

    return go()


def test_character_sheet_panel_shows_stun_ampm_time_and_stats():
    """The always-visible panel (CharacterSheet) must carry Health, Fatigue,
    Stun, Cash, Reputation, Experience, Humanity, and all six stats."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            character = app.character
            character.stun = 5
            character.elapsed_hours = 13  # 1:00 PM

            content = app.screen.query_one(CharacterSheet).render()
            assert "Stun: 5" in content
            assert "1:00 PM" in content
            assert "Health:" in content
            assert "Cash:" in content
            assert "Rep:" in content
            assert "Body:" in content
            assert "Cool:" in content

    run(body())


def test_character_sheet_panel_shows_stun_none_and_midnight_as_12_am():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)

            content = app.screen.query_one(CharacterSheet).render()  # elapsed_hours starts at 0
            assert "Stun: None" in content
            assert "12:00 AM" in content

    run(body())


def test_corp_map_screen_shows_the_character_sheet_panel():
    """CorpMapScreen was the one runner-mode screen not yielding CharacterSheet
    -- the always-visible panel must reach it too."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            assert isinstance(app.screen, CorpMapScreen)
            assert app.screen.query_one(CharacterSheet) is not None

    run(body())


def test_offer_taken_by_a_rival_runner_is_shown_and_cannot_be_accepted():
    """A job an independent runner beat the player to (rivals.py) stays on the
    fixer's board for the day, labelled with who took it, and selecting it must
    not accept it."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            fixer = app.fixers[0]
            offer = fixer.offers[0]
            offer.taken_by = RIVAL_RUNNERS[0].id

            app.push_screen(FixerOffersScreen(fixer))
            await pilot.pause()
            rows = app.screen.query_one("#offers", ListView)
            label = str(rows.children[0].query_one(Static).content)
            assert "TAKEN" in label
            assert RUNNERS_BY_ID[offer.taken_by].name in label

            rows.index = 0
            await pilot.press("enter")
            await pilot.pause()
            assert app.character.accepted_jobs == []
            assert offer in fixer.offers

    run(body())


def test_contacts_runner_panel_reports_what_each_runner_is_doing():
    """The Runners panel reads rivals.RunnerState, so a runner with a state
    shows where they are and what they're up to, and one without says so —
    but only for runners whose number you actually have."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            character = app.character
            territory = app.corp_map.territories[character.location_id]
            character.meet_runner(RIVAL_RUNNERS[0].id)
            character.meet_runner(RIVAL_RUNNERS[1].id)
            app.rival_runner_states[RIVAL_RUNNERS[0].id] = RunnerState(
                territory_id=territory.id,
                activity=RunnerActivity.WORKING,
                job_title="Server Pull",
            )

            app.push_screen(ContactsScreen())
            await pilot.pause()
            rows = app.screen.query_one("#runners_list", ListView).children
            labels = [str(row.query_one(Static).content) for row in rows]

            working = next(label for label in labels if RIVAL_RUNNERS[0].name in label)
            assert f"{territory.name}, running Server Pull" in working
            unknown = next(label for label in labels if RIVAL_RUNNERS[1].name in label)
            assert "whereabouts unknown" in unknown

    run(body())


def test_bar_gates_recruiting_on_meeting_the_runner_first():
    """A runner can't be recruited sight-unseen. BarScreen only shows runners
    who are actually drinking at the bar (rivals.RunnerActivity.DRINKING in
    the player's current territory). Before that, the bar is empty."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            runner = RIVAL_RUNNERS[0]
            territory = app.corp_map.territories[app.character.location_id]
            bar = Location(id="test_bar", name="The Sprawl", kind=LocationKind.BAR)

            app.push_screen(BarScreen(bar))
            await pilot.pause()
            rows = app.screen.query_one("#bar_runners", ListView)
            assert not any(item.id.startswith("meet_") or item.id.startswith("runner_")
                           for item in rows.children)
            app.pop_screen()
            await pilot.pause()

            app.rival_runner_states[runner.id] = RunnerState(
                territory_id=territory.id, activity=RunnerActivity.DRINKING
            )
            app.push_screen(BarScreen(bar))
            await pilot.pause()
            rows = app.screen.query_one("#bar_runners", ListView)
            rows.focus()
            rows.index = next(i for i, item in enumerate(rows.children) if item.id == f"meet_{runner.id}")
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert app.character.knows_runner(runner.id)
            rows = app.screen.query_one("#bar_runners", ListView)
            assert any(item.id == f"runner_{runner.id}" for item in rows.children)

    run(body())


def test_neon_choir_can_introduce_a_runner_for_a_fee():
    """An info-broker fixer (fixer.RUNNER_BROKER_FIXER_IDS) can vouch for an
    independent runner directly from FixerOffersScreen, skipping the bar-luck
    encounter -- costs cash and immediately makes the runner knows_runner()."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            character = app.character
            fixer = next(f for f in app.fixers if f.id == "fixer_neon_choir")
            runner = RIVAL_RUNNERS[0]
            cost = intro_cost(runner)
            character.cash = cost

            app.push_screen(FixerOffersScreen(fixer))
            await pilot.pause()
            rows = app.screen.query_one("#offers", ListView)
            rows.focus()
            rows.index = next(
                i for i, item in enumerate(rows.children) if item.id == f"intro_{runner.id}"
            )
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert character.knows_runner(runner.id)
            assert character.cash == 0

    run(body())


def test_web_screen_lists_cross_fixer_offers_and_accepting_one_takes_it():
    """Web aggregates open offers from every established (trust>0) fixer into one
    list; selecting one should behave exactly like FixerOffersScreen's own accept
    flow -- added to accepted_jobs and pulled off that fixer's own board."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            fixer = next(f for f in app.fixers if f.offers)
            app.character.adjust_fixer_trust(fixer.id, 1)
            offer = fixer.offers[0]

            app.push_screen(WebScreen())
            await pilot.pause()
            web_list = app.screen.query_one("#web_list", ListView)
            web_list.focus()
            web_list.index = next(
                i for i, item in enumerate(web_list.children) if item.id == f"weboffer_{offer.id}"
            )
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()

            assert offer in app.character.accepted_jobs
            assert offer not in fixer.offers

    run(body())


def test_web_screen_omits_fixers_without_established_trust():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            assert all(app.character.trust_with(fixer.id) <= 0 for fixer in app.fixers)

            app.push_screen(WebScreen())
            await pilot.pause()
            web_list = app.screen.query_one("#web_list", ListView)
            ids = [item.id for item in web_list.children]
            assert not any(item_id.startswith("weboffer_") for item_id in ids)
            assert ids[-1] == "no_web_offers"

    run(body())


def test_web_screen_lists_megacorp_apps_before_the_search_section():
    """A browser home screen: every megacorp's site listed like an app shortcut,
    all of them ahead of the Search section (the actual job board)."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)

            app.push_screen(WebScreen())
            await pilot.pause()
            web_list = app.screen.query_one("#web_list", ListView)
            ids = [item.id for item in web_list.children]
            app_ids = [f"webapp_{faction.id}" for faction in FACTIONS]
            assert ids[: len(app_ids)] == app_ids
            assert ids[len(app_ids)] == "web_search_header"

            web_list.focus()
            web_list.index = 0
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.character.accepted_jobs == []  # an app shortcut, not a job accept
            assert isinstance(app.screen, CorpWebsiteScreen)
            assert app.screen.faction is FACTIONS[0]

    run(body())


def test_corp_website_screen_shows_no_updates_yet_with_an_empty_blog():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)

            app.push_screen(CorpWebsiteScreen(FACTIONS[0]))
            await pilot.pause()
            blog_list = app.screen.query_one("#blog_list", ListView)
            ids = [item.id for item in blog_list.children]
            assert ids == ["no_blog_posts"]

    run(body())


def test_corp_website_screen_renders_faction_events_most_recent_first():
    """Blog posts come straight from ShadowguyApp.faction_events, which
    rivals.resolve_rival_day/CorpScreen/ResearchTreeScreen append to
    most-recent-first — this screen just renders that order, it doesn't sort."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            faction = FACTIONS[0]
            territory_id = next(iter(app.corp_map.territories))
            technology_id = TECHNOLOGIES[0].id
            app.faction_events[faction.id] = [
                FactionEvent(kind="technology", day=7, technology_id=technology_id),
                FactionEvent(kind="territory", day=3, territory_id=territory_id),
            ]

            app.push_screen(CorpWebsiteScreen(faction))
            await pilot.pause()
            blog_list = app.screen.query_one("#blog_list", ListView)
            labels = [item.query_one(Static).content for item in blog_list.children]
            assert f"Day 7 — Unveiled new technology: {TECHNOLOGIES[0].name}." in str(labels[0])
            territory_name = app.corp_map.territories[territory_id].name
            assert f"Day 3 — Expanded operations into {territory_name}." in str(labels[1])

    run(body())


def test_alarm_clock_screen_sets_and_shortens_the_next_rest():
    """Setting an alarm hour cuts the next Rest short (instead of the flat
    REST_HOURS_COST) and clears itself so it doesn't fire again on the Rest after."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            assert app.character.elapsed_hours == 0.0  # hour_of_day 0, fresh character

            app.push_screen(AlarmClockScreen())
            await pilot.pause()
            alarm_list = app.screen.query_one("#alarm_list", ListView)
            alarm_list.focus()
            alarm_list.index = next(
                i for i, item in enumerate(alarm_list.children) if item.id == "alarm_6"
            )
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.character.alarm_hour == 6

            app.pop_screen()
            await pilot.pause()
            app.rest()
            assert app.character.elapsed_hours == 6.0  # not REST_HOURS_COST (8)
            assert app.character.alarm_hour is None

    run(body())


def test_alarm_clock_screen_selecting_the_same_hour_again_clears_it():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            app.character.alarm_hour = 6

            app.push_screen(AlarmClockScreen())
            await pilot.pause()
            alarm_list = app.screen.query_one("#alarm_list", ListView)
            alarm_list.focus()
            alarm_list.index = next(
                i for i, item in enumerate(alarm_list.children) if item.id == "alarm_6"
            )
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert app.character.alarm_hour is None

    run(body())


def test_messages_screen_recaps_an_established_fixers_open_work():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _boot_runner_game(pilot, app)
            fixer = next(f for f in app.fixers if f.offers)
            app.character.adjust_fixer_trust(fixer.id, 1)

            app.push_screen(MessagesScreen())
            await pilot.pause()
            rows = app.screen.query_one("#messages_list", ListView).children
            labels = [str(row.query_one(Static).content) for row in rows]
            assert any(fixer.name in label for label in labels)

    run(body())


def _find_corp_hq(app):
    """The first CORP_HQ location on this run's map, with its owning faction."""
    for territory in app.corp_map.territories.values():
        for location in territory.locations:
            if location.kind is LocationKind.CORP_HQ:
                return location, FACTIONS_BY_ID[territory.owner]
    raise AssertionError("no corp HQ found on the map")


def test_entering_corp_hq_with_no_territory_owner_is_a_safe_no_op():
    """No code currently nulls out a CORP_HQ territory's owner (corp-vs-corp
    takeover isn't implemented yet -- see corp_turn.py), but _push_location_screen
    guards against it anyway. Corrupt the value only after the screen's initial
    (valid-state) mount/render, so this exercises just that guard rather than
    render_ascii_map's separate, unrelated assumption that owner is never None."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            hq_location, _faction = _find_corp_hq(app)
            territory = next(
                t for t in app.corp_map.territories.values() if hq_location in t.locations
            )

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            screen = app.screen

            territory.owner = None
            screen._push_location_screen(hq_location, territory)
            await pilot.pause()

            assert app.screen is screen

    run(body())


def test_entering_gang_den_with_no_territory_gang_is_a_safe_no_op():
    """No code currently nulls out a gang den's territory's gang_id once the den
    exists there. See test_entering_corp_hq_with_no_territory_owner_is_a_safe_no_op
    for why the corruption happens after the initial mount/render, not before."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            den_territory, den_location = _find_gang_den(app)

            app.push_screen(CorpMapScreen())
            await pilot.pause()
            screen = app.screen

            den_territory.gang_id = None
            screen._push_location_screen(den_location, den_territory)
            await pilot.pause()

            assert app.screen is screen

    run(body())


def test_corp_screen_no_longer_hands_a_runner_a_corp():
    """The free menu pick is gone: with no corp, CorpScreen lists the corps as
    read-only standing readouts and selecting one only points at its HQ."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            app.push_screen(CorpMapScreen())
            await pilot.pause()
            await pilot.click("#cat_corp")
            await pilot.pause()
            assert isinstance(app.screen, CorpScreen)

            rows = app.screen.query_one("#corp_list", ListView)
            assert [row.id for row in rows.children] == [
                f"corpinfo_{faction.id}" for faction in FACTIONS
            ]
            rows.index = 0
            await pilot.press("enter")
            await pilot.pause()
            assert app.corp_state is None  # selecting a corp must not hand it over

    run(body())


def test_hq_takeover_is_locked_until_rep_standing_and_cash_are_all_met():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            location, faction = _find_corp_hq(app)
            character = app.character

            # Below the executive's own gate there's no offer on the table at all.
            app.push_screen(CorpHQScreen(location, faction))
            await pilot.pause()
            assert len(app.screen.query("#takeover")) == 0
            app.pop_screen()
            await pilot.pause()

            # Executive-visible but short on every takeover gate: shown, locked.
            character.rep = 12
            character.standing[faction.id] = 8
            character.cash = 0
            app.push_screen(CorpHQScreen(location, faction))
            await pilot.pause()
            label = str(app.screen.query_one("#takeover").query_one(Static).content)
            assert "locked" in label
            assert f"rep {TAKEOVER_MIN_REP}" in label
            assert f"{TAKEOVER_COST}eb" in label

            # Selecting it while locked must not hand over the corp.
            rows = app.screen.query_one("#hq_officers", ListView)
            rows.index = len(rows.children) - 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.corp_state is None
            assert character.cash == 0

    run(body())


def test_hq_takeover_buys_the_corp_and_charges_for_it():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            location, faction = _find_corp_hq(app)
            character = app.character
            character.rep = TAKEOVER_MIN_REP
            character.standing[faction.id] = TAKEOVER_MIN_STANDING
            character.cash = TAKEOVER_COST + 500

            app.push_screen(CorpHQScreen(location, faction))
            await pilot.pause()
            rows = app.screen.query_one("#hq_officers", ListView)
            assert str(rows.children[-1].query_one(Static).content).startswith("Move on the board")

            rows.index = len(rows.children) - 1
            await pilot.press("enter")
            await pilot.pause()

            assert app.corp_state is not None
            assert app.corp_state.faction_id == faction.id
            assert character.cash == 500  # charged exactly the stake
            # The offer is gone once taken -- you can't run two corps.
            assert len(app.screen.query("#takeover")) == 0

    run(body())


# --- directing the remote hacker from the tactical screen ---


def _supported_burglary_stage(seed=3):
    """A burglary stage plus the Support a netrunner hire produces for it."""
    from shadowguy.buildings import BuildingKind, generate_building
    from shadowguy.combat import ENEMIES_BY_ID
    from shadowguy.support import support_for

    building = generate_building(random.Random(seed), entrance_count=2, kind=BuildingKind.OFFICE)
    character = Character(name="t")
    character.hire_for_job("runner_specter", "j1", on_site=False)
    stage = BurglaryStage(
        prompt="Get in.",
        entrances=(),
        building=building,
        bailed=Outcome(text="bailed"),
        guard=ENEMIES_BY_ID["corp_sec"],
    )
    return stage, building.entrance_spawns[0], support_for(character.crew_support("j1"))


def test_the_hacker_tile_only_appears_when_somebody_is_backing_the_job():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await _settle(pilot)
            app.push_screen(TacticalScreen(_open_field_stage()))
            await _settle(pilot)
            assert not app.screen.query_one("#tac_box_hacker", Static).display

    run(body())


def test_h_directs_the_hacker_without_spending_the_players_turn():
    """The whole economy of remote support: their action, not yours. Pressing h resolves
    a task (or opens the picker when there's more than one) and leaves moves/acted alone."""

    async def body():
        stage, spawn, support = _supported_burglary_stage()
        app = ShadowguyApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await _settle(pilot)
            app.push_screen(TacticalScreen(stage, spawn=spawn, support=support))
            await _settle(pilot)
            state = app.screen.state
            assert app.screen.query_one("#tac_box_hacker", Static).display
            moves, acted = state.moves_left, state.acted

            await pilot.press("h")
            await _settle(pilot)
            if isinstance(app.screen, HackerPickScreen):
                await pilot.press("enter")  # more than one task offered; take the first
                await _settle(pilot)

            assert state.support.acted  # the hacker spent their own turn
            assert state.moves_left == moves and state.acted == acted  # yours is untouched

    run(body())


def test_a_traced_hacker_drops_off_the_line_and_the_tile_says_so():
    async def body():
        stage, spawn, support = _supported_burglary_stage()
        support.trace = TRACE_CAP
        support.offline = True
        app = ShadowguyApp()
        async with app.run_test(size=(120, 60)) as pilot:
            await _settle(pilot)
            app.push_screen(TacticalScreen(stage, spawn=spawn, support=support))
            await _settle(pilot)
            tile = app.screen.query_one("#tac_box_hacker", Static)
            assert tile.display
            assert "traced" in str(tile.content)

    run(body())


# --- creation gear: buying the kit you walk in with ---


def test_g_opens_the_gear_screen_and_converting_a_point_shows_up_back_on_creation():
    """The pools line on the creation screen has to follow a point spent on the gear
    screen -- a stale one reads as a lost point (hence on_screen_resume)."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await _settle(pilot)
            app.push_screen(CharacterCreationScreen())
            await _settle(pilot)
            character = app.character
            before = character.skill_points

            await pilot.press("g")
            await _settle(pilot)
            assert isinstance(app.screen, GearScreen)

            await pilot.press("enter")  # top row converts a skill point
            await _settle(pilot)
            assert character.skill_points == before - 1
            assert character.gear_budget == GEAR_EB_PER_POINT

            await pilot.press("escape")
            await _settle(pilot)
            assert isinstance(app.screen, CharacterCreationScreen)
            pools = str(app.screen.query_one("#pools", Static).content)
            assert f"Skill points: {before - 1}" in pools
            assert f"{GEAR_EB_PER_POINT}eb" in pools

    run(body())


def test_beginning_the_run_writes_off_unspent_gear_budget_instead_of_banking_it():
    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(100, 40)) as pilot:
            await _settle(pilot)
            app.push_screen(CharacterCreationScreen())
            await _settle(pilot)
            character = app.character
            character.convert_skill_point_to_gear()
            cash = character.cash
            while character.stat_points:
                character.spend_stat_point("body")
            while character.skill_points:
                if not any(character.spend_skill_point(s) for s in ("toughness", "sturdy", "running", "fortitude")):
                    break

            await pilot.press("b")
            await _settle(pilot)
            assert character.gear_budget == 0
            assert character.cash == cash  # written off, never banked

    run(body())


def test_corp_defeat_ends_the_run_on_the_day_tick():
    """The one loss condition Corp mode has: hold nothing at the day boundary and
    the run is over. Driven through the real tick rather than calling corp_defeated
    directly, so the wiring in _apply_day_tick is what's under test."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()
            assert app.corp_state is not None

            # Everything the corp held is taken off it overnight.
            ours = app.corp_state.faction_id
            other = next(f.id for f in FACTIONS if f.id != ours)
            for territory in app.corp_map.territories.values():
                if territory.owner == ours:
                    territory.owner = other
            app.spend_time(HOURS_PER_DAY)
            await pilot.pause()
            assert app.return_value is None
            assert "broken up" in (app._exit_renderables[0] if app._exit_renderables else "")

    run(body())


def test_corp_screen_operations_panel_lists_reinforce_and_attack_rows():
    """The Operations panel offers a row per district held (reinforce) and per
    bordering rival district (attack). Asserted on ids rather than prose."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()

            app.push_screen(CorpScreen())
            await _settle(pilot)
            screen = app.screen
            assert isinstance(screen, CorpScreen)

            ours = app.corp_state.faction_id
            held = {t.id for t in app.corp_map.territories.values() if t.owner == ours}
            rows = [item.id for item in screen.query_one("#operations_list", ListView).children]
            for territory_id in held:
                assert f"deploy_{territory_id}" in rows
            # A seeded map always puts at least one rival bloc against another.
            assert [r for r in rows if r.startswith("attack_")] == [
                f"attack_{t}" for t in attack_candidates(app.corp_map, ours)
            ]

    run(body())


def test_corp_screen_reinforce_flow_moves_operatives_onto_the_district():
    """Full UI path: pick a district, pick a force size on ForcePickScreen, and the
    operatives land on Territory.garrison."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()

            app.corp_state.operatives = 4
            app.push_screen(CorpScreen())
            await _settle(pilot)
            screen = app.screen

            ours = app.corp_state.faction_id
            target = sorted(t.id for t in app.corp_map.territories.values() if t.owner == ours)[0]
            await pilot.click(f"#deploy_{target}")
            await _settle(pilot)
            assert isinstance(app.screen, ForcePickScreen)

            await pilot.click("#force_4")
            await _settle(pilot)
            assert app.corp_map.territories[target].garrison == 4
            assert app.corp_state.operatives == 0
            assert app.corp_state.daily_action_used is True

    run(body())


def test_corp_screen_attack_flow_resolves_against_a_rival():
    """The other half of the Operations panel, end to end. Force is pinned high and
    the rng to sixes, so both contest dice match and the capture is deterministic."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()

            ours = app.corp_state.faction_id
            defender = next(f.id for f in FACTIONS if f.id != ours)
            # A generated map doesn't guarantee the player's corp borders a rival, so
            # hand a neighbouring district to one rather than skipping the test on the
            # maps where it doesn't.
            # Search the whole bloc, not just its first district — an interior one can
            # be surrounded entirely by our own ground.
            target = next(
                c
                for t in app.corp_map.territories.values()
                if t.owner == ours
                for c in t.connections
                if app.corp_map.territories[c].owner != ours
            )
            territory = app.corp_map.territories[target]
            territory.owner = defender
            territory.garrison = 0
            territory.modifiers[TerritoryModifier.SECURITY] = 1
            app.corp_state.operatives = 4
            app.rng = AlwaysSix()

            app.push_screen(CorpScreen())
            await _settle(pilot)

            await pilot.click(f"#attack_{target}")
            await _settle(pilot)
            assert isinstance(app.screen, ForcePickScreen)
            await pilot.click("#force_4")
            await _settle(pilot)

            # 4 committed vs defense 1: taken, one lost grinding through Security,
            # three survivors left holding it.
            assert app.corp_map.territories[target].owner == ours
            assert app.corp_map.territories[target].garrison == 3
            assert app.corp_state.operatives == 0
            assert app.corp_state.daily_action_used is True
            # The capture shows up on the corp's own public website.
            seizures = [e for e in app.faction_events[ours] if e.kind == "seizure"]
            assert seizures[0].territory_id == target
            assert seizures[0].from_faction_id == defender

    run(body())


def test_corp_screen_offers_a_rebuild_once_the_facility_is_captured():
    """Losing the labs to a rival swaps the Research Facility panel's upgrade rows for
    rebuild rows — one per district held — and building one restores research."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()

            ours = app.corp_state.faction_id
            rival = next(f.id for f in FACTIONS if f.id != ours)
            # A rival takes the district the labs stand on.
            lab_territory = next(
                t
                for t in app.corp_map.territories.values()
                if t.owner == ours
                and any(loc.kind is LocationKind.RESEARCH_FACILITY for loc in t.locations)
            )
            capture_territory(lab_territory, rival)
            app.corp_state.cash = RESEARCH_FACILITY_REBUILD_COST

            app.push_screen(CorpScreen())
            await _settle(pilot)
            screen = app.screen

            rows = [item.id for item in screen.query_one("#research_list", ListView).children]
            assert rows and all(r.startswith("rebuild_") for r in rows)
            assert "build_lab" not in rows

            target = rows[0].removeprefix("rebuild_")
            await pilot.click(f"#rebuild_{target}")
            await _settle(pilot)

            facility = owned_research_facility(app.corp_state, app.corp_map)
            assert facility is not None
            assert facility.research_tier == STARTING_RESEARCH_TIER
            assert app.corp_state.cash == 0
            assert collect_research(app.corp_state, app.corp_map) > 0
            # Back to ordinary upgrade rows now that a facility stands again.
            rows = [item.id for item in screen.query_one("#research_list", ListView).children]
            assert "build_lab" in rows

    run(body())


def test_corp_screen_offers_an_academy_rebuild_once_it_is_captured():
    """The Academy's mirror of the facility rebuild. Losing it is the harsher trap —
    no training means no operatives, so no attacking and no garrisoning."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await _settle(pilot)
            await pilot.click("#new_game")
            await pilot.pause()
            await pilot.click("#corp")
            await pilot.pause()
            await pilot.click(f"#faction_{FACTIONS[0].id}")
            await pilot.pause()

            ours = app.corp_state.faction_id
            rival = next(f.id for f in FACTIONS if f.id != ours)
            academy_territory = next(
                t
                for t in app.corp_map.territories.values()
                if t.owner == ours and any(loc.kind is LocationKind.ACADEMY for loc in t.locations)
            )
            capture_territory(academy_territory, rival)
            app.corp_state.cash = ACADEMY_REBUILD_COST

            app.push_screen(CorpScreen())
            await _settle(pilot)
            screen = app.screen

            rows = [item.id for item in screen.query_one("#academy_list", ListView).children]
            assert rows and all(r.startswith("newacademy_") for r in rows)
            assert not any(r.startswith("train_") for r in rows)

            target = rows[0].removeprefix("newacademy_")
            await pilot.click(f"#newacademy_{target}")
            await _settle(pilot)

            assert app.corp_state.cash == 0
            assert app.corp_state.daily_action_used is True
            # Training rows are back now that an Academy stands again.
            app.corp_state.daily_action_used = False
            await screen._refresh()
            await _settle(pilot)
            rows = [item.id for item in screen.query_one("#academy_list", ListView).children]
            assert any(r.startswith("train_") for r in rows)

    run(body())


def test_ripperdoc_flow_installs_cyberware_from_a_clinic_on_the_map():
    """Walking into a CYBER_CLINIC and buying chrome — the acquisition path
    cybernetics.py had a full catalog for and no way to reach."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()

            clinic = None
            clinic_territory_id = None
            for territory in app.corp_map.territories.values():
                for location in territory.locations:
                    if location.kind == LocationKind.CYBER_CLINIC:
                        clinic, clinic_territory_id = location, territory.id
                        break
                if clinic:
                    break
            assert clinic is not None, "every generated map carries at least one clinic"
            app.character.location_id = clinic_territory_id
            app.character.cash = 1_000_000
            # Same reason the shop flow above does it: a live gig adds a row ahead of
            # the "Enter" row this test clicks.
            app.location_gigs.pop(clinic.id, None)

            app.push_screen(CorpMapScreen())
            await _settle_map_boxes(pilot, app.screen)
            app.screen.query_one(f"#map_local_box_{clinic.id}", Collapsible).collapsed = False
            await _settle(pilot)
            app.screen.query_one("#map_local_boxes_scroll").scroll_end(animate=False, immediate=True)
            await _settle(pilot)
            await pilot.click(f"#map_local_{clinic.id}")
            await pilot.pause()
            assert isinstance(app.screen, RipperdocScreen)

            before_cash = app.character.cash
            before_humanity = free_humanity(app.character)
            # The first row is always an installable piece — Deltaware is min_standing 0.
            await pilot.click("#ripper_stock ListItem")
            await pilot.pause()
            assert len(app.character.installed_cyberware) == 1
            assert app.character.cash < before_cash
            assert free_humanity(app.character) < before_humanity

            # It's load-bearing immediately: the installed piece now shows as an
            # extraction row rather than the "no chrome" placeholder.
            rows = [i.id for i in app.screen.query_one("#ripper_installed", ListView).children]
            assert rows and all(r.startswith("remove_") for r in rows)

            await pilot.click("#ripper_installed ListItem")
            await pilot.pause()
            assert app.character.installed_cyberware == {}
            # No cash refund, and the rebound isn't a clean undo: the implant's whole
            # humanity_cost comes back, but both operations left a permanent scar.
            assert app.character.cash < before_cash
            assert free_humanity(app.character) == before_humanity - 2 * SURGERY_SCARRING

    run(body())


def test_ripperdoc_hides_stock_above_the_players_standing():
    """Rows above standing are hidden outright, the same as ShopScreen — never shown
    locked. Deltaware stays visible to a stranger, so no effect is ever unreachable."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            clinic = next(
                loc
                for t in app.corp_map.territories.values()
                for loc in t.locations
                if loc.kind == LocationKind.CYBER_CLINIC
            )
            app.push_screen(RipperdocScreen(clinic))
            await _settle(pilot)

            shown = {
                i.id.removeprefix("install_")
                for i in app.screen.query_one("#ripper_stock", ListView).children
            }
            gated = {c.id for c in CYBERWARE_CATALOG if c.min_standing > 0}
            assert shown & gated == set()
            assert {c.id for c in CYBERWARE_CATALOG if c.tier == "deltaware"} <= shown

    run(body())


def test_ripperdoc_install_that_takes_the_last_of_you_ends_the_run():
    """Cyberpsychosis. install_cyberware only refuses a piece that doesn't *fit*, so
    one costing exactly what's left goes in — and the operation's own scar takes free
    Humanity under 0. The shelf warns first; the player is allowed to do it anyway."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            clinic = next(
                loc
                for t in app.corp_map.territories.values()
                for loc in t.locations
                if loc.kind == LocationKind.CYBER_CLINIC
            )
            character = app.character
            character.cash = 1_000_000
            piece = CYBERWARE_BY_ID["reflex_coprocessor"]
            character.humanity = piece.humanity_cost  # nothing to spare

            app.push_screen(RipperdocScreen(clinic))
            await _settle(pilot)

            # The row says so before it's clicked.
            row = app.screen.query_one(f"#install_{piece.id}", ListItem)
            assert "LAST OF YOU" in row.query_one(Static).content

            await pilot.click(f"#install_{piece.id}")
            await pilot.pause()
            assert free_humanity(character) < 0
            assert "nothing left to wake up" in (
                app._exit_renderables[0] if app._exit_renderables else ""
            )

    run(body())


def test_ripperdoc_removal_rebounds_humanity_and_lifts_the_penalty():
    """The way back out of the spiral: pulling chrome returns its whole cost and
    charges only the scar."""

    async def body():
        app = ShadowguyApp()
        async with app.run_test(size=(80, 60)) as pilot:
            await pilot.pause()
            clinic = next(
                loc
                for t in app.corp_map.territories.values()
                for loc in t.locations
                if loc.kind == LocationKind.CYBER_CLINIC
            )
            character = app.character
            character.cash = 1_000_000
            for piece in ("reflex_coprocessor", "hydraulic_cyberarm"):
                install_cyberware(character, piece)
            assert character.humanity_penalty > 0
            sunk = free_humanity(character)

            app.push_screen(RipperdocScreen(clinic))
            await _settle(pilot)
            await pilot.click("#remove_arms")
            await pilot.pause()

            assert free_humanity(character) > sunk
            assert character.humanity_penalty == 0
            assert CyberSlot.ARMS not in character.installed_cyberware

    run(body())
