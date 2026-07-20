from rich.text import Text
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.combat import Drop
from shadowguy.matrix import (
    analyze_node,
    available_matrix_actions,
    connected_nodes,
    extract,
    jack_out,
    move_to,
    player_integrity,
    render_matrix_network,
    start_matrix_run,
    take_run_turn,
    usable_analyze_program,
)
from shadowguy.scene import MatrixStage

from . import CharacterSheet, _boxed_action_text, _replace_items

MATRIX_LOG_LINES = 8
NAV_LOG_LINES = 3


class MatrixScreen(Screen):
    """A matrix run against a node network — the netrunner's CombatScreen/
    TacticalScreen. Two modes sharing one layout: navigating the network (move
    between connected nodes, extract once the data node is cleared, jack out any
    time) and fighting a node's guardian (the same action-list/log shape every
    other fight screen uses). Dismisses with a matrix.MatrixOutcome (SEIZED /
    EJECTED); SceneScreen maps those onto the MatrixStage's victory/escape
    Outcomes — unchanged from before this screen went node-based."""

    BINDINGS = [("q", "quit_menu", "Menu")]

    CSS = """
    #network_scroll {
        height: auto;
        max-height: 6;
        overflow-x: auto;
    }

    #network_scroll #ice {
        width: auto;
    }

    #actions ListItem.matrix_action_box {
        height: auto;
        border: round $accent;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    #actions ListItem.matrix_action_box.-highlight {
        border: round $secondary;
    }
    """

    def __init__(self, stage: MatrixStage, drop: Drop) -> None:
        super().__init__()
        self.stage = stage
        self.drop = drop
        self.run = None
        self.actions = []
        # One-shot: true for exactly one refresh after a node's guardian falls, so
        # the player sees the kill before the screen snaps back to navigation.
        self._show_cleared_continue = False

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static(self.stage.prompt, id="prompt"),
            Static(id="integrity"),
            ScrollableContainer(Static(id="ice"), id="network_scroll", can_focus=False),
            Static(id="matrix_log"),
            ListView(id="actions"),
            id="matrix_body",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.run = start_matrix_run(self.app.character, self.stage.network, self.drop, self.app.rng)
        await self._refresh()

    def _integrity_text(self) -> Text:
        run = self.run
        if run.fight is not None:
            return Text(f"Integrity: {run.fight.integrity}/{run.fight.max_integrity}")
        full = player_integrity(self.app.character)
        return Text(f"Integrity: {full}/{full}")

    def _ice_text(self) -> Text:
        lines = []
        for fighter in self.run.fight.ices:
            if not fighter.is_standing:
                lines.append(f"  {fighter.ice.name}: dark")
            else:
                lines.append(f"  {fighter.ice.name}: {fighter.integrity}/{fighter.ice.integrity}")
        return Text("\n".join(lines))

    def _network_text(self) -> Text:
        return Text(render_matrix_network(self.run))

    def _navigation_rows(self) -> list[ListItem]:
        run = self.run
        program = usable_analyze_program(run)
        rows = []
        for node in connected_nodes(run):
            revealed = node.id in run.revealed_node_ids
            label = f"Move to {node.id} ({node.role.value})" if revealed else f"Move to {node.id}"
            rows.append(ListItem(Static(label), id=f"move_{node.id}"))
            if program is not None and not revealed:
                rows.append(ListItem(Static(f"Analyze {node.id} ({program.name})"), id=f"analyze_{node.id}"))
        if run.can_extract:
            rows.append(ListItem(Static("Extract with the data"), id="extract"))
        rows.append(ListItem(Static("Jack out (blow the run)"), id="jack_out"))
        return rows

    async def _refresh(self) -> None:
        run = self.run
        self.query_one(CharacterSheet).refresh()
        self.query_one("#integrity", Static).update(self._integrity_text())

        in_fight_view = run.in_fight or self._show_cleared_continue
        if in_fight_view:
            self.query_one("#ice", Static).update(self._ice_text())
            self.query_one("#matrix_log", Static).update(Text("\n".join(run.fight.log[-MATRIX_LOG_LINES:])))
        else:
            self.query_one("#ice", Static).update(self._network_text())
            # Fewer lines than fight mode: the network map itself already eats rows
            # (up to ~10 for a big network), and navigation log lines are terse.
            self.query_one("#matrix_log", Static).update(Text("\n".join(run.run_log[-NAV_LOG_LINES:])))

        if run.is_over or self._show_cleared_continue:
            self.actions = []
            await _replace_items(
                self.query_one("#actions", ListView), [ListItem(Static("Continue"), id="done")]
            )
            return

        if run.in_fight:
            self.actions = available_matrix_actions(self.app.character, run.fight.program_uses)
            rows = [
                ListItem(
                    Static(_boxed_action_text(action.label)),
                    id=f"action_{i}",
                    classes="matrix_action_box",
                )
                for i, action in enumerate(self.actions)
            ]
        else:
            self.actions = []
            rows = self._navigation_rows()
        await _replace_items(self.query_one("#actions", ListView), rows)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        run = self.run

        if run.is_over:
            self.dismiss(run.outcome)
            return

        if self._show_cleared_continue:
            self._show_cleared_continue = False
            await self._refresh()
            return

        item_id = event.item.id

        if run.in_fight:
            action = self.actions[int(item_id.removeprefix("action_"))]
            take_run_turn(run, action, self.app.rng)
            if not run.is_over and not run.in_fight:
                self._show_cleared_continue = True
            await self._refresh()
            return

        if item_id == "jack_out":
            jack_out(run)
        elif item_id == "extract":
            extract(run)
        elif item_id.startswith("analyze_"):
            analyze_node(run, item_id.removeprefix("analyze_"), self.app.rng)
        elif item_id.startswith("move_"):
            move_to(run, item_id.removeprefix("move_"), self.app.rng)
        await self._refresh()
