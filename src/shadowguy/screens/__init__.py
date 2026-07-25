import re

from rich.text import Text
from textual.screen import Screen
from textual.widgets import Collapsible, ListItem, ListView, Static

from shadowguy.character import MAX_SKILL_RANK, Character
from shadowguy.factions import FACTIONS
from shadowguy.matrix import matrix_readiness
from shadowguy.scene import Scene
from shadowguy.shops import ITEMS_BY_ID
from shadowguy.skills import skill_value
from shadowguy.tactical import Grid, Tile

# Terrain glyph/style, shared by TacticalScreen and BurglaryWalkScreen so the two grid
# renderers (same tactical.Grid/Tile underneath) look like one visual language. Wall and
# low cover previously rendered as flat "#"/"%" in the same grey -- indistinguishable at
# a glance. Solid vs. shaded blocks plus a distinct color for cover reads as "hide here",
# without changing any tactical.py data.

# Bitmask of which cardinal neighbors are also wall -- N/S/E/W as bits 1/2/4/8 -- mapped
# to the box-drawing glyph that connects in those directions, so a wall reads as an actual
# outlined floor plan instead of a flat field of the same block everywhere. The map's
# outer edge counts as wall too (see _is_wall), closing the boundary the same way an
# interior wall does.
_WALL_CONNECTORS: dict[int, str] = {
    0: "■",
    1: "│", 2: "│", 3: "│",
    4: "─", 8: "─", 12: "─",
    5: "└", 6: "┌", 9: "┘", 10: "┐",
    7: "├", 11: "┤", 13: "┴", 14: "┬",
    # 15 (wall on all four cardinal sides) isn't a real junction shape to draw -- it's a
    # wall tile with no floor/cover next to it in any of the four directions, i.e. solid
    # rock in the middle of a wall band. A plain block reads as "rock" there instead of
    # a busy field of crossings.
    15: "█",
}


def _is_wall(grid: Grid, x: int, y: int) -> bool:
    if not (0 <= x < grid.width and 0 <= y < grid.height):
        return True
    return grid.tiles[y][x] is Tile.WALL


def _wall_glyph(grid: Grid, x: int, y: int) -> str:
    mask = (
        (1 if _is_wall(grid, x, y - 1) else 0)
        | (2 if _is_wall(grid, x, y + 1) else 0)
        | (4 if _is_wall(grid, x + 1, y) else 0)
        | (8 if _is_wall(grid, x - 1, y) else 0)
    )
    return _WALL_CONNECTORS[mask]


def _terrain_glyph(grid: Grid, x: int, y: int) -> tuple[str, str]:
    tile = grid.tiles[y][x]
    if tile is Tile.WALL:
        return _wall_glyph(grid, x, y), "grey42"
    if tile is Tile.LOW_COVER:
        return "▒", "wheat4"
    return "·", "grey50"


# Combat/matrix Action labels follow "Title (detail)" — split it so a fight action can
# render as a boxed RPG-style button (bold title, dim detail on its own line) instead of
# a flat text row, shared by CombatScreen/MatrixScreen/TacticalScreen alike.
_ACTION_LABEL_RE = re.compile(r"^(.*) \(([^)]*)\)$")


def _boxed_text(title: str, detail: str | None = None) -> Text:
    if not detail:
        return Text.from_markup(f"[bold]{title}[/bold]")
    return Text.from_markup(f"[bold]{title}[/bold]\n[dim]{detail}[/dim]")


def _boxed_action_text(label: str) -> Text:
    match = _ACTION_LABEL_RE.match(label)
    if not match:
        return _boxed_text(label)
    title, detail = match.groups()
    return _boxed_text(title, detail)


def matrix_warning(character: Character, scene: Scene) -> str:
    """The heads-up a Data Heist offer/row shows a runner who isn't kitted for the matrix
    — "⚠ needs a cyberdeck and more Hack skill" — or "" when the scene has no matrix stage
    or the runner is ready. Advisory only: the job is still offered and acceptable (see
    matrix.matrix_readiness), this just warns before they bleed against ICE they can't touch."""
    if not scene.has_matrix:
        return ""
    missing = matrix_readiness(character)
    return f" ⚠ needs {' and '.join(missing)}" if missing else ""


async def _replace_items(list_view: ListView, items: list[ListItem], index: int = 0) -> None:
    await list_view.clear()
    for item in items:
        list_view.append(item)
    list_view.index = min(index, len(items) - 1) if items else 0


async def _populate_list(
    list_view: ListView,
    entries: list,
    *,
    id_prefix: str,
    label,
    empty_label: str | None = None,
    empty_id: str = "",
) -> None:
    if entries:
        items = [ListItem(Static(label(entry)), id=f"{id_prefix}{entry.id}") for entry in entries]
    else:
        items = [ListItem(Static(empty_label), id=empty_id)] if empty_label else []
    await _replace_items(list_view, items)


class CharacterSheet(Static):
    def __init__(self, character: Character) -> None:
        super().__init__()
        self.character = character

    def render(self) -> str:
        c = self.character
        standings = "  ".join(
            f"{f.name.split()[0]}: {c.standing_with(f.id):+d}" for f in FACTIONS
        )
        gear = ", ".join(
            ITEMS_BY_ID[entry.item_id].name if entry.equipped else f"{ITEMS_BY_ID[entry.item_id].name} (stowed)"
            for entry in c.inventory
        ) or "none"
        fatigue = "Rested" if c.fatigue == 0 else f"Fatigued: {c.fatigue} (-{c.fatigue_penalty} to stats)"
        return (
            f"{c.name}\n"
            f"Day {c.day}, {c.hour_of_day:02d}:00   Health: {c.health}/{c.max_health}   {fatigue}\n"
            f"Body: {c.stat('body')}  Strength: {c.stat('strength')}  Agility: {c.stat('agility')}\n"
            f"Perception: {c.stat('perception')}  Intelligence: {c.stat('intelligence')}  "
            f"Cool: {c.stat('cool')}\n"
            f"Cash: {c.cash}eb   Rep: {c.rep}   Experience: {c.experience}xp   "
            f"Humanity: {c.humanity}\n"
            f"Standing — {standings}\n"
            f"Gear: {gear}"
        )


PANEL_NAV_BINDINGS = [
    ("left", "focus_panel(-1)", "Prev panel"),
    ("right", "focus_panel(1)", "Next panel"),
]

# The two footer bindings almost every screen shares: "q" quits to the main menu
# (app.action_quit_menu), "escape" pops the screen (BackScreen.action_back).
MENU_QUIT_BINDINGS = [("q", "quit_menu", "Menu")]
MENU_BACK_BINDINGS = [*MENU_QUIT_BINDINGS, ("escape", "back", "Back")]


class BackScreen(Screen):
    """Screen whose back action pops itself off the stack — the default for any screen
    reachable via `escape`. Subclasses needing different back behavior override
    action_back (e.g. CorpMainMenu's no-op, BarScreen's async unwind)."""

    def action_back(self) -> None:
        self.app.pop_screen()


def _in_collapsed_section(widget) -> bool:
    """True if `widget` sits inside a collapsed Collapsible (so it's hidden and can't take
    focus). Screens with no Collapsible ancestor never report True, so PanelNav is unchanged
    for them."""
    node = widget.parent
    while node is not None:
        if isinstance(node, Collapsible):
            return node.collapsed
        node = node.parent
    return False


class PanelNav:
    PANEL_IDS: tuple[str, ...] = ()

    def action_focus_panel(self, step: int) -> None:
        panels = [self.query_one(f"#{pid}", ListView) for pid in self.PANEL_IDS]
        focused = self.focused
        current = next((i for i, panel in enumerate(panels) if panel is focused), 0)
        # Step over panels tucked inside a collapsed Collapsible — focusing a hidden list
        # would strand the cursor. Falls through to the plain single step when nothing's
        # collapsed (the offset-1 candidate is simply the next panel).
        for offset in range(1, len(panels) + 1):
            candidate = panels[(current + step * offset) % len(panels)]
            if not _in_collapsed_section(candidate):
                candidate.focus()
                return


def _compact_skill_label(character: Character, skill, show_cost: bool = False) -> str:
    rank = character.skill_rank(skill.id)
    value = skill_value(character, skill.id)
    detail = f"  rank {rank}/{MAX_SKILL_RANK}  value {value}"
    if show_cost:
        cost = character.next_rank_cost(skill.id)
        detail += "  MAX" if cost is None else f"  next +{cost}"
    return f"{skill.name}\n{detail}"


_MENU_CSS = """
__SCREEN__ {
    align: center middle;
}

#__DIALOG__ {
    width: auto;
    height: auto;
    border: round $accent;
    padding: 1 2;
}

#__DIALOG__ ListView {
    width: 28;
    height: auto;
}
"""


def _menu_css(screen: str, dialog: str) -> str:
    return _MENU_CSS.replace("__SCREEN__", screen).replace("__DIALOG__", dialog)
