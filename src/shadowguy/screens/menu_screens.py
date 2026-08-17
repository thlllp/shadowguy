from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

import shadowguy.archetypes as archetypes
from shadowguy.buildings import BuildingKind, generate_building
from shadowguy.checks import day_tier, resolve_check
from shadowguy.combat import ENEMY_TIERS, Drop, roll_enemies
from shadowguy.corp_turn import CorpState
from shadowguy.factions import FACTIONS
from shadowguy.fishing import generate_fishing_trip
from shadowguy.gangs import GANGS
from shadowguy.job_archetypes import ARCHETYPES, Approach, StageType
from shadowguy.jobs import DIFFICULTY_BASE, WETWORK_STRUCTURE, generate_job, generate_smuggling_job
from shadowguy.matrix import ICE_TIERS, MatrixOutcome, generate_matrix_network
from shadowguy.saves import SaveSlot, list_saves, load_game
from shadowguy.scene import BurglaryStage, Entrance, MatrixStage, Outcome, TacticalStage
from shadowguy.security import generate_security_contract
from shadowguy.skills import skill_for, skill_value
from shadowguy.tactical import TacticalOutcome
from shadowguy.tactical_gen import generate_map

from . import MENU_BACK_BINDINGS, MENU_QUIT_BINDINGS, BackScreen, _menu_css
from .burglary_screens import EntrancePickScreen
from .creation_screen import CharacterCreationScreen
from .matrix_screen import MatrixScreen
from .scene_screen import SceneScreen
from .tactical_screen import TacticalScreen


def _approach_pool(archetype_name: str) -> tuple[Approach, ...]:
    """An archetype's APPROACH-stage pool, read straight from jobs.ARCHETYPES so the
    Test menu's burglary/wetwork entrances always match what a real job generates --
    no separately hand-copied table here to fall out of sync when jobs.py's rows
    change."""
    archetype = next(a for a in ARCHETYPES if a.name == archetype_name)
    stage = next(s for s in archetype.stages if s.type is StageType.APPROACH)
    return stage.approaches


def _day_for_tier(tier: int) -> int:
    """The lowest day number that lands on `tier` per checks.day_tier -- Test menu
    scenarios have no run clock to read a day off of, so this picks one backwards
    from the tier the player actually chose rather than hard-coding day_tier's
    own day-per-tier cadence a second time here."""
    day = 1
    while day_tier(day, len(DIFFICULTY_BASE)) < tier:
        day += 1
    return day


class TierPickScreen(ModalScreen):
    """Which day-tier to generate a job/contract/delivery test scenario at -- the
    same dismiss-a-value modal shape GrenadePickScreen/HackerPickScreen/
    ForcePickScreen use. Dismisses the chosen tier, or None if cancelled."""

    BINDINGS = [("escape", "cancel", "Back")]
    CSS = _menu_css("TierPickScreen", "tier_dialog")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Tier"),
            ListView(
                *(
                    ListItem(Static(f"Tier {tier}"), id=f"tier_{tier}")
                    for tier in range(len(DIFFICULTY_BASE))
                )
            ),
            id="tier_dialog",
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(int(event.item.id.removeprefix("tier_")))


class QuitMenu(ModalScreen):
    BINDINGS = [("escape", "close", "Back"), ("q", "close", "Back")]
    CSS = _menu_css("QuitMenu", "quit_dialog")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Menu"),
            ListView(
                ListItem(Static("Save Game"), id="save"),
                ListItem(Static("Load Game"), id="load"),
                ListItem(Static("Quit Game"), id="quit"),
                ListItem(Static("Restart Game"), id="restart"),
            ),
            id="quit_dialog",
        )

    def action_close(self) -> None:
        self.dismiss()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "save":
            try:
                slot = self.app.save_run()
            except Exception as exc:
                self.app.notify(f"Couldn't save: {exc}", severity="error")
                return
            self.app.notify(f"Saved: {slot.label}")
            self.dismiss()
        elif event.item.id == "load":
            slots = list_saves()
            if not slots:
                self.app.notify("No saved games found.", severity="warning")
                return
            self.app.push_screen(LoadMenu(slots))
        elif event.item.id == "quit":
            self.app.exit()
        elif event.item.id == "restart":
            self.app.restart_run()


class LoadMenu(ModalScreen):
    BINDINGS = [("escape", "close", "Back"), ("q", "close", "Back")]
    CSS = _menu_css("LoadMenu", "load_dialog")

    def __init__(self, slots: list[SaveSlot]) -> None:
        super().__init__()
        self._slots = slots

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Load Game"),
            ListView(
                *(
                    ListItem(Static(slot.label), id=f"slot_{i}")
                    for i, slot in enumerate(self._slots)
                ),
            ),
            id="load_dialog",
        )

    def action_close(self) -> None:
        self.dismiss()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        slot = self._slots[int(event.item.id.removeprefix("slot_"))]
        try:
            state = load_game(slot.path)
        except Exception:
            self.app.notify(f"Couldn't load {slot.label}.", severity="error")
            return
        self.app.load_state(state)


BANNER = r"""
         ███             ███             ███             ███
       ███░            ███░            ███░            ███░
     ███░            ███░            ███░            ███░
   ███░            ███░            ███░            ███░
 ███░            ███░            ███░            ███░            █
██░            ███░            ███░            ███░            ███
░            ███░            ███░            ███░            ███░
            ░░░             ░░░             ░░░             ░░░
     ██____  _  _   __██ ____   __   _██_   ___  _  _██_  _
   ███/ ___)/ )( \ / _\ (    \ /  \█/ )( \ / __)/ )( \( \/ )
 ███░ \___ \) __ (/    \ ) D ((  O )\ /\ /( (_ \) \/ ( )  /      █
██░   (____/\_)(_/\_/\_/(____/ \__/ (_/\_) \___/\____/(__/     ███
░            ███░            ███░            ███░            ███░
           ███░            ███░            ███░            ███░
         ███░            ███░            ███░            ███░
        ░░░             ░░░             ░░░             ░░░
 ███             ███             ███             ███             █
██░            ███░            ███░            ███░            ███
░            ███░            ███░            ███░            ███░
           ███░            ███░            ███░            ███░
""".strip("\n")


class TitleMenu(Screen):
    BINDINGS = MENU_QUIT_BINDINGS
    CSS = """
TitleMenu {
    align: center middle;
}

#title_dialog {
    width: auto;
    height: auto;
    margin-top: 3;
}

#banner_box {
    width: auto;
    height: auto;
    border: round $accent;
    padding: 1 2;
}

#banner {
    width: auto;
    text-wrap: nowrap;
}

#menu_box {
    width: auto;
    height: auto;
    border: round $accent;
    padding: 1 2;
    margin-top: 2;
}

#menu_box ListView {
    width: 28;
    height: auto;
}
"""

    OPTIONS = [
        ("new_game", "New Game"),
        ("load_game", "Load Game"),
        ("test", "Test"),
        ("settings", "Settings"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Vertical(Static(BANNER, id="banner"), id="banner_box"),
            Vertical(
                ListView(*(ListItem(Static(label), id=option_id) for option_id, label in self.OPTIONS)),
                id="menu_box",
            ),
            id="title_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "new_game":
            self.app.push_screen(ModeSelectScreen())
        elif event.item.id == "load_game":
            slots = list_saves()
            if not slots:
                self.notify("No saved games found.", severity="warning")
                return
            self.app.push_screen(LoadMenu(slots))
        elif event.item.id == "test":
            self.app.push_screen(TestMenu())
        elif event.item.id == "settings":
            self.notify("Settings aren't implemented yet.")


class ModeSelectScreen(BackScreen):
    """New Game's first choice: build a Runner (BuildSelectScreen picks preset or
    hand-built from there), or set up as a Corp instead by picking one of the 4
    seeded Factions -- Corp mode has no runner to build, so that path skips
    CharacterCreationScreen entirely and drops straight into CorpMapScreen."""

    BINDINGS = MENU_BACK_BINDINGS
    CSS = _menu_css("ModeSelectScreen", "mode_dialog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("New Game"),
            ListView(
                ListItem(Static("Runner"), id="runner"),
                ListItem(Static("Corp"), id="corp"),
            ),
            id="mode_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "runner":
            self.app.push_screen(BuildSelectScreen())
        elif event.item.id == "corp":
            self.app.push_screen(CorpSelectScreen())


class BuildSelectScreen(BackScreen):
    """The Runner branch's own first choice: take a preset build, or spend the 26
    points by hand. Both paths end on CharacterCreationScreen -- a preset just
    arrives there with everything already spent, so it stays editable (r resets it)
    rather than being a one-way shortcut past the screen."""

    BINDINGS = MENU_BACK_BINDINGS
    CSS = _menu_css("BuildSelectScreen", "build_select_dialog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("New Runner"),
            ListView(
                ListItem(Static("Premade archetype"), id="premade"),
                ListItem(Static("Custom character"), id="custom"),
            ),
            id="build_select_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "premade":
            self.app.push_screen(ArchetypeSelectScreen())
        elif event.item.id == "custom":
            self.app.push_screen(CharacterCreationScreen())


class ArchetypeSelectScreen(BackScreen):
    """The preset builds. Picking one resets the build first -- a preset is the
    *whole* build, not a top-up -- then opens creation with both pools already at
    zero, so it stays editable rather than being a one-way shortcut.

    archetypes.ARCHETYPES is read in compose(), not in the class body: the table is
    lazily validated on first access (see archetypes.py), and a class body runs at
    module import time, which would defeat that."""

    BINDINGS = MENU_BACK_BINDINGS
    # Not _menu_css: that template's 28-column ListView is sized for one-line rows,
    # and a preset row carries its description under the name.
    CSS = """
ArchetypeSelectScreen {
    align: center middle;
}

#archetype_dialog {
    width: 60;
    max-width: 100%;
    height: auto;
    border: round $accent;
    padding: 1 2;
}

#archetype_dialog ListView {
    /* Capped so five three-line rows still fit an 80x24 terminal -- past the cap
       the list scrolls itself rather than pushing rows off the screen. */
    height: auto;
    max-height: 16;
}
"""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Pick an archetype"),
            ListView(
                *(
                    ListItem(Static(f"{a.name}\n  {a.description}"), id=f"archetype_{a.id}")
                    for a in archetypes.ARCHETYPES
                )
            ),
            id="archetype_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        archetype = archetypes.ARCHETYPES_BY_ID[event.item.id.removeprefix("archetype_")]
        character = self.app.character
        character.reset_build()
        archetype.apply(character)
        self.app.push_screen(CharacterCreationScreen())


class CorpSelectScreen(BackScreen):
    """Pick which Faction to run. Corp mode has no runner to build -- picking
    a Faction assigns app.corp_state, sets app.corp_only so save/load knows to
    reopen the same screen, and calls app.begin_run() to open the map, skipping
    character creation entirely. The stat/skill pools that creation would
    normally spend are zeroed here instead, so there's nothing left unspent to
    (pointlessly) force creation back open on a later save/load."""

    BINDINGS = MENU_BACK_BINDINGS
    CSS = _menu_css("CorpSelectScreen", "corp_select_dialog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Pick a Corp to run"),
            ListView(
                *(
                    ListItem(Static(f"{faction.name} ({faction.specialty})"), id=f"faction_{faction.id}")
                    for faction in FACTIONS
                )
            ),
            id="corp_select_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        faction_id = event.item.id.removeprefix("faction_")
        self.app.corp_state = CorpState(faction_id=faction_id)
        self.app.corp_only = True
        self.app.character.stat_points = 0
        self.app.character.skill_points = 0
        self.app.begin_run()


class TestMenu(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS
    CSS = _menu_css("TestMenu", "test_dialog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Test"),
            ListView(
                *(
                    ListItem(Static(f"Tactical Combat — Tier {tier}"), id=f"tactical_{tier}")
                    for tier in ENEMY_TIERS
                ),
                *(
                    ListItem(Static(f"Matrix Combat — Tier {tier}"), id=f"matrix_{tier}")
                    for tier in ICE_TIERS
                ),
                # One row per BuildingKind -- a new kind gets a row here for free, no
                # hand-wiring. Burglary can hit any of them (BURGLARY_STRUCTURE maps every
                # generated LocationKind onto one); Wetwork always targets WETWORK_STRUCTURE
                # specifically, so it stays its own single row below rather than a fourth
                # BuildingKind entry.
                *(
                    ListItem(
                        Static(f"Burglary — {kind.value.title()}"), id=f"burglary_{kind.value}"
                    )
                    for kind in BuildingKind
                ),
                ListItem(Static(f"Wetwork — {WETWORK_STRUCTURE.value.title()}"), id="wetwork"),
                ListItem(Static("Bodyguard Job"), id="bodyguard"),
                ListItem(Static("Smuggling Delivery"), id="smuggling"),
                ListItem(Static("Security Contract"), id="security"),
                ListItem(Static("Fishing"), id="fishing"),
            ),
            id="test_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id.startswith("tactical_"):
            self._start_tactical(int(item_id.removeprefix("tactical_")))
        elif item_id.startswith("matrix_"):
            self._start_matrix(int(item_id.removeprefix("matrix_")))
        elif item_id.startswith("burglary_"):
            kind = BuildingKind(item_id.removeprefix("burglary_"))
            self._start_burglary(kind, _approach_pool("Burglary"))
        elif item_id == "wetwork":
            self._start_burglary(WETWORK_STRUCTURE, _approach_pool("Wetwork"))
        elif item_id == "bodyguard":
            self._start_bodyguard()
        elif item_id == "smuggling":
            self._start_smuggling()
        elif item_id == "security":
            self._start_security()
        elif item_id == "fishing":
            self.app.push_screen(SceneScreen(generate_fishing_trip()))

    def _start_tactical(self, tier: int) -> None:
        rng = self.app.rng
        enemies = roll_enemies(tier, rng)
        tac = generate_map(rng, len(enemies))
        stage = TacticalStage(
            prompt=f"Test fight — tier {tier}.",
            grid=tac.grid,
            player_start=tac.player_start,
            enemies=tuple(zip(enemies, tac.enemy_spawns, strict=True)),
            victory=Outcome(text="Cleared."),
            escape=Outcome(text="You slip out."),
            exits=tac.exits,
        )
        self.app.push_screen(TacticalScreen(stage), self._on_tactical_end)

    def _on_tactical_end(self, result: TacticalOutcome) -> None:
        self.app.character.health = self.app.character.max_health
        self.notify(f"Test fight ended: {result.name.title()}.")

    def _start_matrix(self, tier: int) -> None:
        network = generate_matrix_network(tier, self.app.rng)
        stage = MatrixStage(
            prompt=f"Test breach — tier {tier}.",
            network=network,
            victory=Outcome(text="You seize the data."),
            escape=Outcome(text="You're ejected."),
        )
        self.app.push_screen(MatrixScreen(stage, Drop.NONE), self._on_matrix_end)

    def _on_matrix_end(self, result: MatrixOutcome) -> None:
        self.notify(f"Test breach ended: {result.name.title()}.")

    def _start_burglary(self, kind: BuildingKind, approaches: tuple[Approach, ...]) -> None:
        rng = self.app.rng
        tier = min(ENEMY_TIERS)
        difficulty = DIFFICULTY_BASE[tier]
        building = generate_building(rng, entrance_count=len(approaches), kind=kind)
        entrances = tuple(
            Entrance(
                label=f"{approach.flavor} ({skill_for(approach.skill).name})",
                skill=approach.skill,
                difficulty=difficulty + approach.difficulty_delta,
                spawn=spawn,
                success=Outcome(text="It goes clean."),
                failure=Outcome(text="It gets messy, but you're in."),
            )
            for approach, spawn in zip(approaches, building.entrance_spawns, strict=True)
        )
        stage = BurglaryStage(
            prompt=f"Test infiltration — {kind.value.title()}.",
            entrances=entrances,
            building=building,
            bailed=Outcome(text="You back out empty-handed."),
            guard=rng.choice(roll_enemies(tier, rng)),
        )
        self.app.push_screen(EntrancePickScreen(stage), lambda index: self._on_entrance_picked(stage, index))

    def _on_entrance_picked(self, stage: BurglaryStage, index: int) -> None:
        character = self.app.character
        entrance = stage.entrances[index]
        roll = resolve_check(skill_value(character, entrance.skill), entrance.difficulty, rng=self.app.rng)
        outcome = entrance.outcome_for(roll.result)
        self.notify(f"{roll.result.name}: {outcome.text}")
        self.app.push_screen(TacticalScreen(stage, spawn=entrance.spawn), self._on_burglary_end)

    def _on_burglary_end(self, result: TacticalOutcome) -> None:
        self.app.character.health = self.app.character.max_health
        self.notify(f"Test infiltration ended: {result.name.title()}.")

    def _start_bodyguard(self) -> None:
        self.app.push_screen(TierPickScreen(), self._bodyguard_tier_picked)

    def _bodyguard_tier_picked(self, tier: int | None) -> None:
        if tier is None:
            return
        archetype = next(a for a in ARCHETYPES if a.name == "Bodyguard")
        scene, _timing = generate_job(
            _day_for_tier(tier), self.app.corp_map, fixer_id="test", rng=self.app.rng, archetype=archetype
        )
        self.app.push_screen(SceneScreen(scene))

    def _start_smuggling(self) -> None:
        if self.app.character.smuggling_job is not None:
            self.notify("Already carrying a delivery job.", severity="warning")
            return
        self.app.push_screen(TierPickScreen(), self._smuggling_tier_picked)

    def _smuggling_tier_picked(self, tier: int | None) -> None:
        if tier is None:
            return
        character = self.app.character
        gang = self.app.rng.choice(GANGS)
        character.smuggling_job = generate_smuggling_job(
            gang.id, character.location_id, self.app.corp_map, _day_for_tier(tier), self.app.rng
        )
        destination = self.app.corp_map.territories[character.smuggling_job.destination_territory_id]
        self.notify(f"Test delivery for {gang.name}: carry it to {destination.name}.")

    def _start_security(self) -> None:
        self.app.push_screen(TierPickScreen(), self._security_tier_picked)

    def _security_tier_picked(self, tier: int | None) -> None:
        if tier is None:
            return
        character = self.app.character
        contract = generate_security_contract(
            _day_for_tier(tier), self.app.corp_map, fixer_id="test", rng=self.app.rng
        )
        character.accept_security_contract(contract)
        territory = self.app.corp_map.territories[contract.territory_id]
        self.notify(f"Test contract: guard {territory.name} for {contract.nights_total} nights.")
