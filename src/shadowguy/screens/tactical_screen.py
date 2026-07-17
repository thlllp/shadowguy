from rich.text import Text
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from shadowguy.tactical import (
    Side,
    TacticalOutcome,
    Tile,
    best_shot,
    end_turn,
    leave,
    move_player,
    player_attack,
    start_tactical,
)

from . import CharacterSheet

_TAC_TILE = {Tile.WALL: "#", Tile.LOW_COVER: "%", Tile.FLOOR: "."}
_TAC_END_TEXT = {
    TacticalOutcome.VICTORY: "You've cleared them out.",
    TacticalOutcome.ESCAPED: "You slip out.",
    TacticalOutcome.DEAD: "You're down.",
}
TACTICAL_LOG_LINES = 6


class TacticalScreen(Screen):
    BINDINGS = [
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("f", "fire", "Attack"),
        ("e", "end_turn", "End turn"),
        ("l", "leave", "Leave (on exit)"),
        ("enter", "continue", "Continue"),
        ("q", "quit_menu", "Menu"),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #tac_map { height: 1fr; padding: 0 1; }
    #tac_status, #tac_log { height: auto; padding: 0 1; }
    """

    def __init__(self, stage) -> None:
        super().__init__()
        self.stage = stage
        self.state = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.stage.prompt, id="tac_prompt")
        yield Static(id="tac_status")
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
        px, py = self.state.player.coord
        move_player(self.state, (px + dx, py + dy))
        self._refresh()

    def action_fire(self) -> None:
        if self.state.is_over:
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

    def action_end_turn(self) -> None:
        if self.state.is_over:
            return
        end_turn(self.state, self.app.rng)
        self._refresh()

    def action_leave(self) -> None:
        if self.state.is_over:
            return
        if not leave(self.state):
            self.notify("You're not standing on an exit.")
        self._refresh()

    def action_continue(self) -> None:
        if self.state.is_over:
            self.dismiss(self.state.outcome)

    def _map_text(self) -> Text:
        state = self.state
        grid = state.grid
        glyphs = [[_TAC_TILE[grid.tiles[y][x]] for x in range(grid.width)] for y in range(grid.height)]
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
        text = Text()
        for y in range(grid.height):
            for x in range(grid.width):
                ch = glyphs[y][x]
                default = "grey30" if ch in ("#", "%") else "grey50"
                text.append(ch, style=styles.get((y, x), default))
            text.append("\n")
        return text

    def _refresh(self) -> None:
        state = self.state
        self.query_one(CharacterSheet).refresh()
        self.query_one("#tac_map", Static).update(self._map_text())
        self.query_one("#tac_log", Static).update(Text("\n".join(state.log[-TACTICAL_LOG_LINES:])))
        if state.is_over:
            self.query_one("#tac_status", Static).update(
                f"{_TAC_END_TEXT[state.outcome]}  —  press Enter to continue."
            )
            return
        character = self.app.character
        self.query_one("#tac_status", Static).update(
            f"HP {character.health}/{character.max_health}   "
            f"Moves {state.moves_left}/{state.player.speed}   "
            f"Action {'used' if state.acted else 'ready'}   "
            f"Enemies left {len(state.enemies)}   (arrows move, f attack, e end turn)"
        )
