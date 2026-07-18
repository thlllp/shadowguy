from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.combat import Drop
from shadowguy.matrix import available_matrix_actions, start_matrix, take_matrix_turn
from shadowguy.scene import MatrixStage

from . import CharacterSheet, _replace_items

MATRIX_LOG_LINES = 8


class MatrixScreen(Screen):
    """A matrix fight against ICE — the netrunner's CombatScreen. Same shape (an action
    list, an enemy panel, a log), but the runner's integrity is a per-fight pool, not the
    Character.health the CharacterSheet shows, so it gets its own status line. Dismisses
    with a matrix.MatrixOutcome (SEIZED / EJECTED); SceneScreen maps those onto the
    MatrixStage's victory/escape Outcomes."""

    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self, stage: MatrixStage, drop: Drop) -> None:
        super().__init__()
        self.stage = stage
        self.drop = drop
        self.state = None
        self.actions = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static(self.stage.prompt, id="prompt"),
            Static(id="integrity"),
            Static(id="ice"),
            Static(id="matrix_log"),
            ListView(id="actions"),
            id="matrix_body",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.state = start_matrix(self.app.character, self.stage.ice, self.drop, self.app.rng)
        await self._refresh()

    def _ice_text(self) -> Text:
        lines = []
        for fighter in self.state.ices:
            if not fighter.is_standing:
                lines.append(f"  {fighter.ice.name}: dark")
            else:
                lines.append(f"  {fighter.ice.name}: {fighter.integrity}/{fighter.ice.integrity}")
        return Text("\n".join(lines))

    async def _refresh(self) -> None:
        state = self.state
        self.query_one(CharacterSheet).refresh()
        self.query_one("#integrity", Static).update(
            Text(f"Integrity: {state.integrity}/{state.max_integrity}")
        )
        self.query_one("#ice", Static).update(self._ice_text())
        self.query_one("#matrix_log", Static).update(Text("\n".join(state.log[-MATRIX_LOG_LINES:])))

        if state.is_over:
            self.actions = []
            await _replace_items(
                self.query_one("#actions", ListView), [ListItem(Static("Continue"), id="done")]
            )
            return

        self.actions = available_matrix_actions(self.app.character, state.program_uses)
        await _replace_items(
            self.query_one("#actions", ListView),
            [ListItem(Static(action.label), id=f"action_{i}") for i, action in enumerate(self.actions)],
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.state.is_over:
            self.dismiss(self.state.outcome)
            return

        action = self.actions[int(event.item.id.removeprefix("action_"))]
        take_matrix_turn(self.state, action, self.app.rng)
        await self._refresh()
