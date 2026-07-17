from textual.widgets import ListItem, ListView, Static

from shadowguy.character import MAX_SKILL_RANK, Character
from shadowguy.factions import FACTIONS
from shadowguy.matrix import matrix_readiness
from shadowguy.scene import Scene
from shadowguy.shops import ITEMS_BY_ID
from shadowguy.skills import skill_value


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
        return (
            f"{c.name}\n"
            f"Day {c.day}   Stamina: {c.stamina}/{c.max_stamina}   Health: {c.health}/{c.max_health}\n"
            f"Body: {c.stat('body')}  Strength: {c.stat('strength')}  Agility: {c.stat('agility')}\n"
            f"Perception: {c.stat('perception')}  Intelligence: {c.stat('intelligence')}  "
            f"Cool: {c.stat('cool')}\n"
            f"Cash: {c.cash}eb   Rep: {c.rep}\n"
            f"Standing — {standings}\n"
            f"Gear: {gear}"
        )


PANEL_NAV_BINDINGS = [
    ("left", "focus_panel(-1)", "Prev panel"),
    ("right", "focus_panel(1)", "Next panel"),
]


class PanelNav:
    PANEL_IDS: tuple[str, ...] = ()

    def action_focus_panel(self, step: int) -> None:
        panels = [self.query_one(f"#{pid}", ListView) for pid in self.PANEL_IDS]
        focused = self.focused
        current = next((i for i, panel in enumerate(panels) if panel is focused), 0)
        panels[(current + step) % len(panels)].focus()


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
