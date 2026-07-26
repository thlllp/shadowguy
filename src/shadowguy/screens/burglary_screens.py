"""The first phase of a Burglary job's APPROACH: pick a way in, on a small diagram.

What happens *after* the door is an ordinary tactical fight the runner is trying not to
start, so it plays on TacticalScreen (see tactical.start_burglary) rather than in a
stripped-down walk of its own -- this module is only the choice of entrance. It owns no
check resolution or Outcome logic; SceneScreen does that (resolve_entrance,
apply_outcome), the same separation every other screen in this package keeps."""

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.scene import BurglaryStage

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
