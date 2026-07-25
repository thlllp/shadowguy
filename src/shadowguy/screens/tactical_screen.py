from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.shops import CONSUMABLES_BY_ID, Consumable
from shadowguy.tactical import (
    GRENADE_RADIUS,
    Side,
    TacticalOutcome,
    Tile,
    available_grenades,
    begin_grenade_aim,
    best_shot,
    cancel_grenade_aim,
    confirm_grenade_aim,
    end_turn,
    grenade_needs_target,
    leave,
    legal_grenade_target,
    move_aim_cursor,
    move_player,
    player_attack,
    start_tactical,
    throw_grenade,
    visible_tiles,
)

from . import MENU_QUIT_BINDINGS, CharacterSheet, _boxed_text, _menu_css, _terrain_glyph

_TAC_END_TEXT = {
    TacticalOutcome.VICTORY: "You've cleared them out.",
    TacticalOutcome.ESCAPED: "You slip out.",
    TacticalOutcome.DEAD: "You're down.",
}
TACTICAL_LOG_LINES = 6


class GrenadePickScreen(ModalScreen):
    """Which carried grenade to throw, when there's more than one kind — the tactical
    counterpart of GangTollScreen's pay/refuse pick (corp_map_screen.py), same
    dismiss-a-value shape. Dismisses the chosen Character.consumables index, or None
    if cancelled. Skipped entirely when the runner carries exactly one kind (see
    TacticalScreen.action_throw_grenade) — no need to ask when there's nothing to ask."""

    BINDINGS = [("escape", "cancel", "Back")]
    CSS = _menu_css("GrenadePickScreen", "grenade_dialog")

    def __init__(self, grenades: list[tuple[int, Consumable]]) -> None:
        super().__init__()
        self._grenades = grenades

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Throw which grenade?"),
            ListView(
                *(
                    ListItem(Static(consumable.name), id=f"grenade_{i}")
                    for i, (_, consumable) in enumerate(self._grenades)
                ),
            ),
            id="grenade_dialog",
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        picked = int(event.item.id.removeprefix("grenade_"))
        self.dismiss(self._grenades[picked][0])


class TacticalScreen(Screen):
    BINDINGS = [
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("f", "fire", "Attack"),
        ("g", "throw_grenade", "Grenade"),
        ("e", "end_turn", "End turn"),
        ("l", "leave", "Leave (on exit)"),
        ("enter", "continue", "Continue / confirm throw"),
        ("escape", "cancel_aim", "Cancel throw"),
        *MENU_QUIT_BINDINGS,
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #tac_map { height: 1fr; padding: 0 1; }
    #tac_end, #tac_log { height: auto; padding: 0 1; }
    #tac_status { height: auto; padding: 0 1; }
    #tac_status .tac_box {
        border: round $accent;
        padding: 0 1;
        margin: 0 1 0 0;
        width: auto;
        height: auto;
    }
    """

    def __init__(self, stage) -> None:
        super().__init__()
        self.stage = stage
        self.state = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.stage.prompt, id="tac_prompt")
        yield Static(id="tac_end")
        yield Horizontal(
            Static(id="tac_box_move", classes="tac_box"),
            Static(id="tac_box_attack", classes="tac_box"),
            Static(id="tac_box_grenade", classes="tac_box"),
            Static(id="tac_box_end", classes="tac_box"),
            Static(id="tac_box_leave", classes="tac_box"),
            Static(id="tac_box_enemies", classes="tac_box"),
            id="tac_status",
        )
        yield Static(id="tac_map")
        yield Static(id="tac_log")
        yield Footer()

    def on_mount(self) -> None:
        self.state = start_tactical(
            self.app.character,
            self.stage.grid,
            self.stage.player_start,
            list(self.stage.enemies),
            self.stage.exits,
        )
        self._refresh()

    def action_move(self, direction: str) -> None:
        if self.state.is_over:
            return
        dx, dy = self.DIRECTIONS[direction]
        if self.state.aim_cursor is not None:
            move_aim_cursor(self.state, dx, dy)
            self._refresh()
            return
        px, py = self.state.player.coord
        move_player(self.state, (px + dx, py + dy))
        self._refresh()

    def action_fire(self) -> None:
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        shot = best_shot(self.state)
        if shot is None:
            self.notify(
                "You've already acted this turn." if self.state.acted else "No target in sight and range."
            )
            return
        weapon, target = shot
        player_attack(self.state, target, weapon, self.app.rng)
        self._refresh()

    def action_throw_grenade(self) -> None:
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        grenades = available_grenades(self.state)
        if not grenades:
            self.notify(
                "You've already acted this turn." if self.state.acted else "No grenades carried."
            )
            return
        if len(grenades) == 1:
            self._start_or_throw(*grenades[0])
            self._refresh()
            return
        self.app.push_screen(GrenadePickScreen(grenades), self._on_grenade_picked)

    def _on_grenade_picked(self, consumable_index: int | None) -> None:
        if consumable_index is not None:
            consumable = CONSUMABLES_BY_ID[self.app.character.consumables[consumable_index]]
            self._start_or_throw(consumable_index, consumable)
        self._refresh()

    def _start_or_throw(self, index: int, consumable: Consumable) -> None:
        """Either resolve the throw immediately (an untargeted effect, e.g. a smoke
        grenade — see grenade_needs_target) or enter tile-aiming mode for one that
        needs a landing spot. The caller refreshes after."""
        if grenade_needs_target(consumable):
            begin_grenade_aim(self.state, index)
        else:
            throw_grenade(self.state, index)

    def action_end_turn(self) -> None:
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        end_turn(self.state, self.app.rng)
        self._refresh()

    def action_leave(self) -> None:
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        if not leave(self.state):
            self.notify("You're not standing on an exit.")
        self._refresh()

    def action_continue(self) -> None:
        if self.state.aim_cursor is not None:
            if not confirm_grenade_aim(self.state):
                self.notify("Out of range or blocked — pick another tile.")
            self._refresh()
            return
        if self.state.is_over:
            self.dismiss(self.state.outcome)

    def action_cancel_aim(self) -> None:
        if self.state.aim_cursor is not None:
            cancel_grenade_aim(self.state)
            self._refresh()

    def _map_text(self) -> Text:
        state = self.state
        grid = state.grid
        terrain = [[_terrain_glyph(grid, x, y) for x in range(grid.width)] for y in range(grid.height)]
        glyphs = [[terrain[y][x][0] for x in range(grid.width)] for y in range(grid.height)]
        # Tiles outside the player's current FOV render dimmed, not hidden -- there's no
        # fog-of-war here (the whole map is always known), just a visual cue for "not
        # looking that way right now" that reads as depth.
        seen = visible_tiles(grid, state.player.coord)
        styles: dict[tuple[int, int], str] = {}
        for ex, ey in state.exits:
            if grid.tiles[ey][ex] is Tile.FLOOR:
                glyphs[ey][ex] = ">"
                styles[(ey, ex)] = "bold green"
        for unit in state.units:
            ux, uy = unit.coord
            if unit.side is Side.PLAYER:
                glyphs[uy][ux], styles[(uy, ux)] = "@", "bold cyan"
            elif unit.health > 0:
                glyphs[uy][ux], styles[(uy, ux)] = "E", "bold red"
            else:
                glyphs[uy][ux], styles[(uy, ux)] = "x", "grey37"

        # While aiming a grenade: shade the 3x3 blast around the cursor and mark the
        # cursor itself, in a color that says whether it's a legal_grenade_target right
        # now (green) or not (red) -- so the player sees the throw's actual reach and
        # radius before committing, not just where their cursor happens to be sitting.
        cursor: tuple[int, int] | None = None
        cursor_style = ""
        blast: frozenset[tuple[int, int]] = frozenset()
        if state.aim_cursor is not None:
            cx, cy = state.aim_cursor
            cursor = (cy, cx)
            blast = frozenset(
                (by, bx)
                for by in range(max(0, cy - GRENADE_RADIUS), min(grid.height, cy + GRENADE_RADIUS + 1))
                for bx in range(max(0, cx - GRENADE_RADIUS), min(grid.width, cx + GRENADE_RADIUS + 1))
            )
            cursor_style = "bold green underline" if legal_grenade_target(state, state.aim_cursor) else "bold red underline"

        text = Text()
        for y in range(grid.height):
            for x in range(grid.width):
                ch = glyphs[y][x]
                default = terrain[y][x][1]
                if (y, x) not in styles and not seen[y, x]:
                    default = f"{default} dim"
                style = styles.get((y, x), default)
                if (y, x) == cursor:
                    style = cursor_style
                elif (y, x) in blast:
                    style = f"{style} on grey15"
                text.append(ch, style=style)
            text.append("\n")
        return text

    def _refresh(self) -> None:
        state = self.state
        self.query_one(CharacterSheet).refresh()
        self.query_one("#tac_map", Static).update(self._map_text())
        self.query_one("#tac_log", Static).update(Text("\n".join(state.log[-TACTICAL_LOG_LINES:])))

        status = self.query_one("#tac_status", Horizontal)
        if state.is_over:
            status.display = False
            self.query_one("#tac_end", Static).update(
                f"{_TAC_END_TEXT[state.outcome]}  —  press Enter to continue."
            )
            return
        status.display = True

        aiming = state.aim_cursor is not None
        self.query_one("#tac_end", Static).update(
            "Aiming — arrows move the target, Enter to throw, Esc to cancel." if aiming else ""
        )

        on_exit = state.player.coord in state.exits
        move_detail = "aiming target" if aiming else f"{state.moves_left}/{state.player.speed} left"
        attack_detail = "used" if state.acted else ("ready" if best_shot(state) is not None else "no shot")
        grenades = available_grenades(state)
        if aiming:
            grenade_detail = "aiming — enter/esc"
        else:
            grenade_detail = "used" if state.acted else (f"{len(grenades)} ready" if grenades else "none carried")
        self.query_one("#tac_box_move", Static).update(_boxed_text("Move (arrows)", move_detail))
        self.query_one("#tac_box_attack", Static).update(_boxed_text("Attack (f)", attack_detail))
        self.query_one("#tac_box_grenade", Static).update(_boxed_text("Grenade (g)", grenade_detail))
        self.query_one("#tac_box_end", Static).update(_boxed_text("End turn (e)", "advance the round"))
        self.query_one("#tac_box_leave", Static).update(
            _boxed_text("Leave (l)", "on exit" if on_exit else "not here")
        )
        self.query_one("#tac_box_enemies", Static).update(_boxed_text("Enemies", f"{len(state.enemies)} left"))
