from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.combat import ENEMY_TIERS, Drop, roll_enemies
from shadowguy.corp_turn import CorpState
from shadowguy.factions import FACTIONS
from shadowguy.matrix import ICE_TIERS, MatrixOutcome, generate_matrix_network
from shadowguy.saves import SaveSlot, list_saves, load_game
from shadowguy.scene import MatrixStage, Outcome, TacticalStage
from shadowguy.tactical import TacticalOutcome, generate_map

from . import MENU_BACK_BINDINGS, MENU_QUIT_BINDINGS, BackScreen, _menu_css
from .corp_screen import CorpMainMenu
from .creation_screen import CharacterCreationScreen
from .matrix_screen import MatrixScreen
from .tactical_screen import TacticalScreen


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


class TitleMenu(Screen):
    BINDINGS = MENU_QUIT_BINDINGS
    CSS = _menu_css("TitleMenu", "title_dialog")

    OPTIONS = [
        ("new_game", "New Game"),
        ("load_game", "Load Game"),
        ("test", "Test"),
        ("settings", "Settings"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Shadowguy"),
            ListView(*(ListItem(Static(label), id=option_id) for option_id, label in self.OPTIONS)),
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
    """New Game's first choice: build a Runner the usual way, or set up as a
    Corp instead by picking one of the 3 seeded Factions -- Corp mode has no
    runner to build, so that path skips CharacterCreationScreen entirely and
    drops straight into CorpMainMenu."""

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
            self.app.push_screen(CharacterCreationScreen())
        elif event.item.id == "corp":
            self.app.push_screen(CorpSelectScreen())


class CorpSelectScreen(BackScreen):
    """Pick which Faction to run. Corp mode has no runner to build -- picking
    a Faction assigns app.corp_state, sets app.corp_only so save/load knows to
    reopen the same screen, and switches straight to CorpMainMenu, skipping
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
        self.app.switch_screen(CorpMainMenu())


class TestMenu(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS
    CSS = _menu_css("TestMenu", "test_dialog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Test"),
            ListView(
                ListItem(
                    Static(f"Tactical Combat — Tier {min(ENEMY_TIERS)}"),
                    id=f"tactical_{min(ENEMY_TIERS)}",
                ),
                ListItem(
                    Static(f"Matrix Combat — Tier {min(ICE_TIERS)}"),
                    id=f"matrix_{min(ICE_TIERS)}",
                ),
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
