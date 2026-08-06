import re

from rich.text import Text
from textual.screen import Screen
from textual.widgets import Collapsible, ListItem, ListView, Static

from shadowguy.character import MAX_SKILL_RANK, Character
from shadowguy.cybernetics import free_humanity
from shadowguy.grid import Grid, Tile
from shadowguy.matrix import matrix_readiness
from shadowguy.scene import Scene
from shadowguy.skills import skill_value

# Terrain glyph/style for anything drawn over a grid.Grid. Solid vs. shaded blocks plus
# a distinct color for cover reads as "hide here" at a glance, without changing any
# grid.py data.

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


# Matrix Action labels follow "Title (detail)" — split it so a fight action can render
# as a boxed RPG-style button (bold title, dim detail on its own line) instead of a flat
# text row. CombatScreen boxes only its highlighted row and keeps the raw label;
# TacticalScreen builds its status tiles from _boxed_text directly.
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


def _format_hour_ampm(hour: int) -> str:
    """CharacterSheet's clock display -- Character.hour_of_day (0-23) rendered
    12-hour, e.g. 0 -> "12:00 AM", 13 -> "1:00 PM". Scoped to this one panel;
    every other screen that shows the hour still uses the raw 24-hour value."""
    period = "AM" if hour < 12 else "PM"
    display_hour = hour % 12 or 12
    return f"{display_hour}:00 {period}"


class CharacterSheet(Static):
    """The always-visible status panel: every runner-mode screen (combat,
    tactical, matrix, burglary, scene, shop, main menu, creation, the corp
    map) yields one of these near the top, so it's never a deliberate choice
    to check your own condition mid-run."""

    def __init__(self, character: Character) -> None:
        super().__init__()
        self.character = character

    def render(self) -> str:
        c = self.character
        fatigue = "Rested" if c.fatigue == 0 else f"Fatigued: {c.fatigue} (-{c.fatigue_penalty} to stats)"
        # What's left of the runner (ceiling minus installed chrome), not the ceiling --
        # the ceiling alone says nothing about how chromed-up they currently are. The
        # penalty is spelled out for the same reason fatigue's is: so the player isn't
        # tracking a stat drain blind.
        humanity = f"{free_humanity(c):g}"
        if c.humanity_penalty:
            humanity += f" (-{c.humanity_penalty} to stats)"
        stun = "None" if c.stun == 0 else str(c.stun)
        return (
            f"{c.name}   Day {c.day}, {_format_hour_ampm(c.hour_of_day)}\n"
            f"Health: {c.health}/{c.max_health}   {fatigue}   Stun: {stun}\n"
            f"Cash: {c.cash}eb   Rep: {c.rep}   Experience: {c.experience}xp   "
            f"Humanity: {humanity}\n"
            f"Body: {c.stat('body')}  Strength: {c.stat('strength')}  Agility: {c.stat('agility')}\n"
            f"Perception: {c.stat('perception')}  Logic: {c.stat('logic')}  "
            f"Cool: {c.stat('cool')}"
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
    action_back (e.g. CorpMapScreen's no-op, BarScreen's async unwind)."""

    def action_back(self) -> None:
        self.app.pop_screen()


class RefreshOnResume:
    """Mount and re-entry both redraw, for a screen whose content can go stale while a
    pushed screen sits on top of it (buying at a shop, spending xp, a day ticking over).

    Mixed in rather than folded into BackScreen: the pair is only correct for a screen
    whose whole content comes from one `_refresh()`, which is most of them but not
    CorpMapScreen (map mode and a category tab redraw differently) or FixerOffersScreen
    (nothing pushes over it)."""

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()


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


def _compact_skill_label(character: Character, skill) -> str:
    """One skill row, shared by CharacterCreationScreen's build columns and
    SkillsScreen's: current rank and value, plus what the next rank costs. The two
    screens fund that cost from different pools, but the row reads the same."""
    rank = character.skill_rank(skill.id)
    value = skill_value(character, skill.id)
    cost = character.next_rank_cost(skill.id)
    next_up = "  MAX" if cost is None else f"  next +{cost}"
    return f"{skill.name}\n  rank {rank}/{MAX_SKILL_RANK}  value {value}{next_up}"


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
