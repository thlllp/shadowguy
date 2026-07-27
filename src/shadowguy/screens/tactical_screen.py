from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.shops import CONSUMABLES_BY_ID, Consumable
from shadowguy.tactical import (
    GRENADE_RADIUS,
    AimKind,
    CrewFate,
    Side,
    TacticalOutcome,
    Tile,
    aim_is_legal,
    available_grenades,
    begin_attack_aim,
    begin_grenade_aim,
    best_shot,
    cancel_aim,
    confirm_aim,
    enemy_at,
    end_turn,
    grenade_needs_target,
    leave,
    move_aim_cursor,
    move_player,
    snap_aim_to_next_target,
    stabilize_ally,
    stairs_here,
    start_burglary,
    start_tactical,
    take_stairs,
    throw_grenade,
    visible_tiles,
    weapon_for_target,
)

from . import MENU_QUIT_BINDINGS, CharacterSheet, _boxed_text, _menu_css, _terrain_glyph

_TAC_END_TEXT = {
    TacticalOutcome.VICTORY: "You've cleared them out.",
    TacticalOutcome.ESCAPED: "You slip out.",
    TacticalOutcome.DEAD: "You're down.",
    TacticalOutcome.SECURED: "You've got what you came for.",
}
TACTICAL_LOG_LINES = 6

# How a standing unit reads on the map, by side. The player is '@' and handled apart —
# they're the one unit whose health lives on the Character, not the Unit.
_SIDE_GLYPH = {Side.ALLY: ("A", "bold magenta"), Side.ENEMY: ("E", "bold red")}

# What the end-of-fight line says about a hire who went down (tactical.resolve_downed_crew).
_CREW_FATE_TEXT = {
    CrewFate.RECOVERED: "{name} is patched up and back on the street.",
    CrewFate.ARRESTED: "{name} was picked up at the scene.",
    CrewFate.KILLED: "{name} didn't make it.",
}


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
        ("f", "fire", "Aim attack"),
        ("g", "throw_grenade", "Grenade"),
        # Priority so the aim cursor's target-cycling wins over Screen's own default
        # tab -> focus_next binding, which would otherwise eat the key.
        Binding("tab", "next_target", "Next target", priority=True),
        ("s", "stabilize", "Stabilize crew"),
        ("e", "end_turn", "End turn"),
        ("l", "leave", "Leave (on exit)"),
        (">", "stairs", "Take stairs"),
        ("enter", "continue", "Continue / confirm"),
        ("escape", "cancel_aim", "Cancel aim"),
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

    def __init__(self, stage, allies=(), spawn=None) -> None:
        super().__init__()
        self.stage = stage
        # Set for a burglary (scene.BurglaryStage): which (level, cell) the entrance the
        # player picked drops them at. None for an ordinary fight, which has one grid and
        # a fixed player_start.
        self.spawn = spawn
        # combat.Enemy stat blocks for the hired runners on this job (combat.crew_stats),
        # supplied by whoever opened the fight — the crew is the Character's, not the
        # stage's, so a hire made after the job was generated still shows up.
        self.allies = list(allies)
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
            Static(id="tac_box_crew", classes="tac_box"),
            Static(id="tac_box_level", classes="tac_box"),
            id="tac_status",
        )
        yield Static(id="tac_map")
        yield Static(id="tac_log")
        yield Footer()

    def on_mount(self) -> None:
        # One screen, two setups: a burglary is a tactical fight you're trying not to
        # start, so it plays here rather than in a stripped-down walk of its own.
        if self.spawn is not None:
            self.state = start_burglary(
                self.app.character, self.stage.building, self.spawn, self.stage.guard, self.allies
            )
        else:
            self.state = start_tactical(
                self.app.character,
                self.stage.grid,
                self.stage.player_start,
                list(self.stage.enemies),
                self.stage.exits,
                allies=self.allies,
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
        move_player(self.state, (px + dx, py + dy), self.app.rng)
        self._refresh()

    def action_fire(self) -> None:
        """Open the aim cursor on the default target rather than firing outright — the
        player confirms with Enter (action_continue), after walking the cursor or tabbing
        to another enemy. Nothing is spent until then."""
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        if not begin_attack_aim(self.state):
            self.notify(
                "You've already acted this turn." if self.state.acted else "No target in sight and range."
            )
            return
        self._refresh()

    def action_next_target(self) -> None:
        if self.state.aim_cursor is None:
            return
        if not snap_aim_to_next_target(self.state):
            self.notify("Nothing in reach to snap to.")
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

    def action_stabilize(self) -> None:
        """Patch up a downed hire you're standing next to. Which body and whether it's
        allowed are both stabilize_ally's call — this only reports the refusal."""
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        refused = stabilize_ally(self.state)
        if refused is not None:
            self.notify(refused)
        self._refresh()

    def action_end_turn(self) -> None:
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        end_turn(self.state, self.app.rng)
        self._refresh()

    def action_stairs(self) -> None:
        """Take the stairs (or lift) under your feet, in a burglary. Costs a move like
        any other step -- another floor is somewhere you walk to."""
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        if not take_stairs(self.state):
            self.notify(
                "No stairs here." if stairs_here(self.state) is None else "No movement left this turn."
            )
        self._refresh()

    def action_leave(self) -> None:
        if self.state.is_over or self.state.aim_cursor is not None:
            return
        if not leave(self.state):
            self.notify("You're not standing on an exit.")
        self._refresh()

    def action_continue(self) -> None:
        if self.state.aim_cursor is not None:
            aiming_attack = self.state.aim_kind is AimKind.ATTACK
            if not confirm_aim(self.state, self.app.rng):
                self.notify(
                    "Nothing you can hit there — pick another target."
                    if aiming_attack
                    else "Out of range or blocked — pick another tile."
                )
            self._refresh()
            return
        if self.state.is_over:
            self.dismiss(self.state.outcome)

    def action_cancel_aim(self) -> None:
        if self.state.aim_cursor is not None:
            cancel_aim(self.state)
            self._refresh()

    def _map_text(self, cursor_legal: bool) -> Text:
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
                glyphs[ey][ex] = "<"
                styles[(ey, ex)] = "bold green"
        if state.building is not None:
            for link in state.building.links:
                for level, (lx, ly) in (link.a, link.b):
                    if level == state.level_index:
                        glyphs[ly][lx], styles[(ly, lx)] = ">", "bold yellow"
            if state.objective is not None and state.objective[0] == state.level_index:
                ox, oy = state.objective[1]
                glyphs[oy][ox], styles[(oy, ox)] = "$", "bold yellow"
            for level, (dx, dy) in state.building.locks:
                if level == state.level_index:
                    glyphs[dy][dx], styles[(dy, dx)] = "+", "bold red"
            for level, (mx, my) in state.building.cameras:
                if level == state.level_index:
                    glyphs[my][mx], styles[(my, mx)] = "o", "bold magenta"
        # A downed unit -- hire or hostile -- greys out rather than vanishing: they're
        # still a body on the floor you can walk over (and, for a hire, patch up).
        for unit in state.units:
            ux, uy = unit.coord
            if unit.side is Side.PLAYER:
                glyphs[uy][ux], styles[(uy, ux)] = "@", "bold cyan"
            elif unit.is_down:
                glyphs[uy][ux], styles[(uy, ux)] = "x", "grey37"
            else:
                glyphs[uy][ux], styles[(uy, ux)] = _SIDE_GLYPH[unit.side]

        # While aiming: mark the cursor in a color that says whether confirming there
        # would actually resolve (green) or not (red) -- so the player sees a weapon's
        # reach, or a throw's, before committing rather than after. A grenade also shades
        # the 3x3 blast it would land, since that's area the cursor cell alone doesn't
        # show; an attack hits exactly the unit under the cursor, so there's none.
        cursor: tuple[int, int] | None = None
        cursor_style = ""
        blast: frozenset[tuple[int, int]] = frozenset()
        if state.aim_cursor is not None:
            cx, cy = state.aim_cursor
            cursor = (cy, cx)
            if state.aim_kind is AimKind.GRENADE:
                blast = frozenset(
                    (by, bx)
                    for by in range(max(0, cy - GRENADE_RADIUS), min(grid.height, cy + GRENADE_RADIUS + 1))
                    for bx in range(max(0, cx - GRENADE_RADIUS), min(grid.width, cx + GRENADE_RADIUS + 1))
                )
            cursor_style = "bold green underline" if cursor_legal else "bold red underline"

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
        # Resolve what the cursor is pointing at once: the map colours the cursor by it
        # and the Attack tile names it, and each answer costs a line-of-sight pass.
        aiming_attack = state.aim_kind is AimKind.ATTACK
        aim_target = enemy_at(state, state.aim_cursor) if aiming_attack else None
        aim_weapon = None if aim_target is None else weapon_for_target(state, aim_target)
        cursor_legal = (
            aim_weapon is not None
            if aiming_attack
            else state.aim_cursor is not None and aim_is_legal(state, state.aim_cursor)
        )

        self.query_one(CharacterSheet).refresh()
        self.query_one("#tac_map", Static).update(self._map_text(cursor_legal))
        self.query_one("#tac_log", Static).update(Text("\n".join(state.log[-TACTICAL_LOG_LINES:])))

        status = self.query_one("#tac_status", Horizontal)
        if state.is_over:
            status.display = False
            aftermath = "  ".join(
                _CREW_FATE_TEXT[fate].format(name=name) for name, fate in state.crew_aftermath or []
            )
            self.query_one("#tac_end", Static).update(
                f"{_TAC_END_TEXT[state.outcome]}  {aftermath}  —  press Enter to continue."
            )
            return
        status.display = True

        aiming = state.aim_cursor is not None
        verb = "fire" if aiming_attack else "throw"
        self.query_one("#tac_end", Static).update(
            f"Aiming — arrows move the target, Tab for the next one, Enter to {verb}, Esc to cancel."
            if aiming
            else ""
        )

        on_exit = state.player.coord in state.exits
        move_detail = "aiming target" if aiming else f"{state.moves_left}/{state.player.speed} left"
        if aiming_attack:
            # Name the weapon that would fire and who it's pointed at: the weapon is
            # picked for you (weapon_for_target), so the readout is where the player
            # finds out a step back has swapped their knife for the gun.
            attack_detail = (
                f"{aim_weapon.name} → {aim_target.name}" if aim_weapon is not None else "no shot there"
            )
        else:
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
        # Burglary only: which floor you're on and whether the house knows about you.
        # An ordinary fight is one room where everyone already does, so it says nothing.
        level_box = self.query_one("#tac_box_level", Static)
        level_box.display = state.building is not None
        if level_box.display:
            level = state.building.levels[state.level_index]
            room = level.room_at(state.player.coord)
            where = f"{level.name}{' — ' + room.label() if room else ''}"
            level_box.update(_boxed_text("ALARM" if state.alarm else "Undetected", where))
        # Crew tile only exists when you brought someone — a fight you walked into alone
        # shouldn't carry an empty box saying so.
        crew = [unit for unit in state.units if unit.is_ally]
        crew_box = self.query_one("#tac_box_crew", Static)
        crew_box.display = bool(crew)
        if crew_box.display:
            crew_box.update(_boxed_text("Crew (s to stabilize)", ", ".join(map(self._crew_line, crew))))

    @staticmethod
    def _crew_line(unit) -> str:
        if not unit.is_down:
            return f"{unit.name} {unit.health}/{unit.stats.health}"
        return f"{unit.name} {'stable' if unit.stabilized else 'DOWN'}"
