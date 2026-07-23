from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.character import Character
from shadowguy.combat import CombatOutcome, Drop
from shadowguy.corpmap import (
    MODIFIER_LABELS,
    MODIFIER_MAX,
    OWNER_COLORS,
    Territory,
    owner_label,
    render_ascii_map,
)
from shadowguy.encounters import GangEncounter, gang_attack, roll_gang_encounter
from shadowguy.fixer import discover_fixers_here
from shadowguy.gangs import GANGS_BY_ID
from shadowguy.shops import equipped_travel_reduction

from . import MENU_BACK_BINDINGS, BackScreen, _menu_css
from .combat_screen import CombatScreen

TRAVEL_HOURS_COST = 2.0
MODIFIER_COLUMN = 13


def _travel_hours(character: Character) -> float:
    """What one hop costs this character right now -- the one place this is computed,
    so the hint text shown before travelling can never disagree with what's charged."""
    return TRAVEL_HOURS_COST * (1 - equipped_travel_reduction(character.inventory))


class CorpMapScreen(BackScreen):
    BINDINGS = [
        *MENU_BACK_BINDINGS,
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

    def action_travel(self) -> None:
        character = self.app.character
        here = self.app.corp_map.territories[character.location_id]
        if self.selected_id not in here.connections:
            return
        # Spend before the move lands: a day boundary crossed mid-hop resolves
        # tonight's lodging/security at the origin, not the destination.
        self.app.spend_time(_travel_hours(character))
        character.location_id = self.selected_id
        self._refresh()
        self._maybe_gang_encounter()

    def _maybe_gang_encounter(self) -> None:
        """On arrival, a gang you're crosswise with may stop you here — a toll to pass, or
        a fight if you're deep enough in the red (or you refuse). See encounters.py."""
        character = self.app.character
        territory = self.app.corp_map.territories[character.location_id]
        encounter = roll_gang_encounter(character, territory, self.app.rng)
        if encounter is None:
            return
        self._pending_gang = encounter.gang
        if encounter.toll is None:
            self._start_gang_fight(encounter.gang)
        else:
            self.app.push_screen(GangTollScreen(encounter), self._on_toll)

    def _on_toll(self, paid: bool) -> None:
        if paid:
            self.notify(f"You pay off {self._pending_gang.name} and move on.")
        else:
            self._start_gang_fight(self._pending_gang)

    def _start_gang_fight(self, gang) -> None:
        self._gang_encounter = gang_attack(gang, self.app.rng)
        self.app.push_screen(
            CombatScreen(self._gang_encounter, Drop.ENEMY), self._on_gang_combat_end
        )

    def _on_gang_combat_end(self, result: CombatOutcome) -> None:
        character = self.app.character
        if result is CombatOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return
        if result is CombatOutcome.KNOCKED_OUT:
            roll = self.app.rng.randint(1, 6)
            if roll <= 2:
                self.app.exit(message=f"{character.name} didn't wake up. Game over.")
                return
            character.cash //= 2
            character.health = 1
            self.notify("You came to in an alley, lighter a few creds.")
            return
        outcome = (
            self._gang_encounter.victory
            if result is CombatOutcome.VICTORY
            else self._gang_encounter.escape
        )
        self.notify(outcome.text)

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
        gang_suffix = f", gang: {GANGS_BY_ID[t.gang_id].name}" if t.gang_id else ""
        self.query_one("#territory_info", Static).update(
            f"{t.name} — owner: {owner_label(t.owner)}, value: {t.value}{gang_suffix}{fixer_suffix}\n"
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
        if t.id == here.id:
            return f"You are here. Day {character.day}."
        if t.id not in here.connections:
            return f"No route from {here.name} — travel is only to a bordering district."
        return f"enter: travel here ({_travel_hours(character):.1f}h)"


class GangTollScreen(ModalScreen):
    """The shakedown at a gang's turf border: pay to pass, or refuse into a fight.
    Dismisses True if paid (cash already deducted here), False if refused or the runner
    can't cover it — the caller (CorpMapScreen._on_toll) turns a False into the fight."""

    BINDINGS = [("escape", "refuse", "Refuse")]
    CSS = _menu_css("GangTollScreen", "toll_dialog")

    def __init__(self, encounter: GangEncounter) -> None:
        super().__init__()
        self.encounter = encounter

    def compose(self) -> ComposeResult:
        enc = self.encounter
        can_pay = self.app.character.cash >= enc.toll
        pay_label = f"Pay {enc.toll}eb" if can_pay else f"Pay {enc.toll}eb — can't cover it"
        yield Vertical(
            Static(f"{enc.gang.name} block your way — {enc.toll}eb to pass."),
            ListView(
                ListItem(Static(pay_label), id="pay"),
                ListItem(Static("Refuse — they'll come at you"), id="refuse"),
            ),
            id="toll_dialog",
        )

    def action_refuse(self) -> None:
        self.dismiss(False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        if event.item.id == "pay" and character.cash >= self.encounter.toll:
            character.cash -= self.encounter.toll
            self.dismiss(True)
        else:
            self.dismiss(False)
