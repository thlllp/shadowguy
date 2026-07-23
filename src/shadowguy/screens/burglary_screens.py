"""Screens for a Burglary job's two-phase APPROACH: pick an entrance on a small
diagram, then walk the interior grid it leads into. Neither screen owns any check
resolution or Outcome logic -- SceneScreen does that (resolve_entrance, apply_outcome),
the same separation every other screen in this package keeps between input/view and
game logic (see TacticalScreen/CombatScreen)."""

from enum import StrEnum

from rich.text import Text
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.scene import BurglaryStage
from shadowguy.tactical import (
    BurglaryWalkState,
    Coord,
    Tile,
    move_walker,
    reached_objective,
    spotted,
)

from . import MENU_QUIT_BINDINGS, CharacterSheet, _replace_items

# A fixed illustration, not a positional layout -- deliberately not corpmap.py's
# dynamic column/connector rendering, which is built for dozens of interconnected
# nodes; a burglary's entrance count is always small and the entrances have no
# connectivity to show between them.
_ENTRANCE_DIAGRAM = (
    "      .----------------------.\n"
    "      |                      |\n"
    "      |       BUILDING       |\n"
    "      |                      |\n"
    "      '----------------------'\n"
)

class EntrancePickScreen(Screen):
    """Phase A: pick a way in. Dismisses with the chosen Entrance's index into
    stage.entrances -- SceneScreen resolves the actual check (resolve_entrance)."""

    # No escape/back binding: this screen was pushed with a dismiss callback
    # (SceneScreen._on_entrance_picked), and Screen.pop_screen() bypasses that
    # callback entirely rather than invoking it with any value -- popping instead
    # of dismissing would strand SceneScreen with no way to resume the stage.
    # Same reason CombatScreen/TacticalScreen have no escape binding either.
    BINDINGS = MENU_QUIT_BINDINGS

    def __init__(self, stage: BurglaryStage) -> None:
        super().__init__()
        self.stage = stage

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.stage.prompt, id="entrance_prompt")
        yield Static(_ENTRANCE_DIAGRAM, id="entrance_diagram")
        yield ListView(id="entrances")
        yield Footer()

    async def on_mount(self) -> None:
        items = [
            ListItem(Static(entrance.label), id=f"entrance_{i}")
            for i, entrance in enumerate(self.stage.entrances)
        ]
        await _replace_items(self.query_one("#entrances", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        index = int(event.item.id.removeprefix("entrance_"))
        self.dismiss(index)


class BurglaryWalkResult(StrEnum):
    REACHED = "reached"
    SPOTTED = "spotted"


_WALK_TILE = {Tile.WALL: "#", Tile.LOW_COVER: "%", Tile.FLOOR: "."}
_WALK_END_TEXT = {
    BurglaryWalkResult.REACHED: "You've reached it.",
    BurglaryWalkResult.SPOTTED: "A guard's light sweeps toward you!",
}


class BurglaryWalkScreen(Screen):
    """Phase B: walk the interior from the chosen entrance's spawn to the objective,
    avoiding guard sightlines. Dismisses with a BurglaryWalkResult once either
    happens -- SceneScreen applies whichever Outcome that implies (stage.burglary's
    entrance-check Outcome on REACHED, stage.burglary.spotted on SPOTTED)."""

    BINDINGS = [
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("enter", "continue", "Continue"),
        *MENU_QUIT_BINDINGS,
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #walk_map { height: 1fr; padding: 0 1; }
    #walk_status { height: auto; padding: 0 1; }
    """

    def __init__(self, stage: BurglaryStage, spawn: Coord) -> None:
        super().__init__()
        self.stage = stage
        self.spawn = spawn
        self.state: BurglaryWalkState | None = None
        self.result: BurglaryWalkResult | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="walk_status")
        yield Static(id="walk_map")
        yield Footer()

    def on_mount(self) -> None:
        self.state = BurglaryWalkState(
            grid=self.stage.grid,
            position=self.spawn,
            objective=self.stage.objective,
            guards=self.stage.guards,
        )
        # An entrance can spawn the walker already inside a guard's sightline --
        # check immediately, don't wait for the first move to notice.
        if spotted(self.state):
            self.result = BurglaryWalkResult.SPOTTED
        elif reached_objective(self.state):
            self.result = BurglaryWalkResult.REACHED
        self._refresh()

    def action_move(self, direction: str) -> None:
        if self.result is not None:
            return
        dx, dy = self.DIRECTIONS[direction]
        px, py = self.state.position
        move_walker(self.state, (px + dx, py + dy))
        if spotted(self.state):
            self.result = BurglaryWalkResult.SPOTTED
        elif reached_objective(self.state):
            self.result = BurglaryWalkResult.REACHED
        self._refresh()

    def action_continue(self) -> None:
        if self.result is not None:
            self.dismiss(self.result)

    def _map_text(self) -> Text:
        state = self.state
        grid = state.grid
        glyphs = [[_WALK_TILE[grid.tiles[y][x]] for x in range(grid.width)] for y in range(grid.height)]
        styles: dict[tuple[int, int], str] = {}
        ox, oy = state.objective
        if grid.tiles[oy][ox] is Tile.FLOOR:
            glyphs[oy][ox], styles[(oy, ox)] = "$", "bold yellow"
        for gx, gy in state.guards:
            glyphs[gy][gx], styles[(gy, gx)] = "G", "bold red"
        px, py = state.position
        glyphs[py][px], styles[(py, px)] = "@", "bold cyan"
        text = Text()
        for y in range(grid.height):
            for x in range(grid.width):
                ch = glyphs[y][x]
                default = "grey30" if ch in ("#", "%") else "grey50"
                text.append(ch, style=styles.get((y, x), default))
            text.append("\n")
        return text

    def _refresh(self) -> None:
        self.query_one(CharacterSheet).refresh()
        self.query_one("#walk_map", Static).update(self._map_text())
        if self.result is not None:
            self.query_one("#walk_status", Static).update(
                f"{_WALK_END_TEXT[self.result]}  —  press Enter to continue."
            )
        else:
            self.query_one("#walk_status", Static).update(
                "Find your way in without being seen. (arrows move)"
            )
