from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import ScrollableContainer
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from shadowguy.character import Character
from shadowguy.corpmap import (
    MODIFIER_LABELS,
    MODIFIER_MAX,
    OWNER_COLORS,
    Territory,
    owner_label,
    render_ascii_map,
)
from shadowguy.fixer import discover_fixers_here


TRAVEL_STAMINA_COST = 1
MODIFIER_COLUMN = 13


class CorpMapScreen(Screen):
    BINDINGS = [
        ("q", "quit_menu", "Menu"),
        ("escape", "back", "Back"),
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("enter", "travel", "Travel here"),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #map_scroll {
        height: 1fr;
        overflow-x: auto;
        padding: 0 1;
    }

    #territory_info {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    #modifiers {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.selected_id = ""
        self.hovered_id: str | None = None
        self.rendered = None
        self._render_key: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield ScrollableContainer(Static(markup=False, id="map"), id="map_scroll")
        yield Static(id="territory_info")
        yield Static(markup=False, id="modifiers")
        yield Footer()

    def on_mount(self) -> None:
        self.selected_id = self.app.character.location_id
        self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_travel(self) -> None:
        character = self.app.character
        here = self.app.corp_map.territories[character.location_id]
        if self.selected_id not in here.connections:
            return
        if character.free_travel_remaining() > 0:
            character.spend_free_travel()
        elif character.can_afford(TRAVEL_STAMINA_COST):
            character.spend_stamina(TRAVEL_STAMINA_COST)
        else:
            return
        character.location_id = self.selected_id
        self._refresh()

    def action_move(self, direction: str) -> None:
        dx, dy = self.DIRECTIONS[direction]
        current = self.app.corp_map.territories[self.selected_id]
        for conn_id in current.connections:
            candidate = self.app.corp_map.territories[conn_id]
            if (candidate.x - current.x, candidate.y - current.y) == (dx, dy):
                self.selected_id = conn_id
                self._refresh()
                return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        offset = event.get_content_offset(self.query_one("#map", Static))
        hovered = (
            self.rendered.territory_at(offset.y, offset.x)
            if offset is not None and self.rendered is not None
            else None
        )
        if hovered != self.hovered_id:
            self.hovered_id = hovered
            self._refresh()

    def on_click(self) -> None:
        if self.hovered_id is not None:
            self.selected_id = self.hovered_id
            self._refresh()

    def _refresh(self) -> None:
        corp_map = self.app.corp_map
        character = self.app.character
        discover_fixers_here(self.app.fixers, character)

        key = (self.selected_id, character.location_id)
        if self.rendered is None or key != self._render_key:
            self.rendered = render_ascii_map(corp_map, self.selected_id, character.location_id)
            self._render_key = key
        text = Text(self.rendered.text)
        for span in self.rendered.spans:
            color = OWNER_COLORS.get(corp_map.territories[span.territory_id].owner)
            if color:
                text.stylize(color, span.offset, span.offset + span.end - span.start)
            if span.territory_id == self.hovered_id:
                text.stylize("reverse", span.offset, span.offset + span.end - span.start)
        self.query_one("#map", Static).update(text)

        t = corp_map.territories[self.hovered_id or self.selected_id]
        here = corp_map.territories[character.location_id]
        borders = ", ".join(corp_map.territories[c].name for c in t.connections)
        locations = ", ".join(f"{loc.name} ({loc.kind})" for loc in t.locations)
        fixer_here = next(
            (
                fixer
                for fixer in self.app.fixers
                if fixer.location_id == t.id and fixer.id in character.discovered_fixers
            ),
            None,
        )
        fixer_suffix = f", fixer: {fixer_here.name}" if fixer_here else ""
        self.query_one("#territory_info", Static).update(
            f"{t.name} — owner: {owner_label(t.owner)}, value: {t.value}{fixer_suffix}\n"
            f"Borders: {borders}\n"
            f"Locations: {locations}\n"
            f"{self._travel_hint(t, here, character)}"
        )
        self.query_one("#modifiers", Static).update(self._modifier_panel(t))

    def _modifier_panel(self, t: Territory) -> str:
        labels, levels = [], []
        for modifier, level in t.modifiers.items():
            labels.append(MODIFIER_LABELS[modifier].ljust(MODIFIER_COLUMN))
            levels.append(f"{level}/{MODIFIER_MAX}".ljust(MODIFIER_COLUMN))
        return f"{''.join(labels).rstrip()}\n{''.join(levels).rstrip()}"

    def _travel_hint(self, t: Territory, here: Territory, character: Character) -> str:
        stamina = f"{character.stamina}/{character.max_stamina}"
        if t.id == here.id:
            return f"You are here. Stamina: {stamina}"
        if t.id not in here.connections:
            return f"No route from {here.name} — travel is only to a bordering district."
        free = character.free_travel_remaining()
        if free > 0:
            return f"enter: travel here (free — {free} left today) — Stamina: {stamina}"
        if not character.can_afford(TRAVEL_STAMINA_COST):
            return f"Too tired to travel ({TRAVEL_STAMINA_COST} stamina). Rest to move."
        return f"enter: travel here ({TRAVEL_STAMINA_COST} stamina) — you have {stamina}"
