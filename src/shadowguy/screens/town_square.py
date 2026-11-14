"""The walkable town square of the district the runner is standing in.

A roguelike counterpoint to `CorpMapScreen`'s Locals panel: one screen per territory,
arrows/WASD/HJKL move `@`, and walking into a building opens the same screen its "Enter"
row does. Fixers and the owners of local gigs stand in the plaza as `F`/`!`; walking into
one talks to them or takes their work.

Free to walk around: no step spends in-game time, and the square itself is generated from
the run seed plus the territory id (`shadowguy.townsquare`) rather than stored, so a
visit changes nothing and leaves nothing to save.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, Static

from shadowguy.corpmap import Location, Territory
from shadowguy.fixer import Fixer
from shadowguy.npcs import Npc, npcs_for_territory
from shadowguy.scene import Scene
from shadowguy.townsquare import (
    FIXER_GLYPH,
    FLOOR_GLYPH,
    GIG_GLYPH,
    KIND_GLYPHS,
    KIND_STYLES,
    PLAYER_GLYPH,
    WALL_GLYPH,
    Square,
    build_square,
    kind_label,
    place_units,
)

from . import MENU_QUIT_BINDINGS, BackScreen, CharacterSheet
from .scene_screen import SceneScreen
from .npc_screens import NpcDialogueScreen
from .shop_screens import FixerOffersScreen, push_location_screen


class TownSquareScreen(BackScreen):
    """Move around one territory's square; bump into places and people to engage."""

    BINDINGS = [
        *MENU_QUIT_BINDINGS,
        Binding("escape", "back", "Back"),
        # One movement entry, not eight: the arrow row is what the manual documents,
        # and WASD/HJKL are aliases a roguelike player expects but needn't advertise.
        ("up", "move('up')", "Move"),
        Binding("down", "move('down')", "Move", show=False),
        Binding("left", "move('left')", "Move", show=False),
        Binding("right", "move('right')", "Move", show=False),
        Binding("w", "move('up')", "Move", show=False),
        Binding("s", "move('down')", "Move", show=False),
        Binding("a", "move('left')", "Move", show=False),
        Binding("d", "move('right')", "Move", show=False),
        Binding("k", "move('up')", "Move", show=False),
        Binding("j", "move('down')", "Move", show=False),
        Binding("h", "move('left')", "Move", show=False),
        Binding("l", "move('right')", "Move", show=False),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #square_map {
        width: 1fr;
        height: auto;
        content-align: center middle;
    }

    #square_status {
        width: 1fr;
        height: auto;
        content-align: center middle;
    }

    #square_help {
        width: 1fr;
        height: auto;
        content-align: center middle;
        color: $text-muted;
    }
    """

    def __init__(self, territory: Territory) -> None:
        super().__init__()
        self.territory = territory
        self.square = self._build_square()
        self.player = self.square.start
        # (x, y) -> ("fixer", Fixer) | ("gig", Location) | ("npc", Npc). Rebuilt on
        # resume because a completed gig disappears from app.location_gigs while this
        # screen sits under the SceneScreen that ran it.
        self._units: dict[tuple[int, int], tuple[str, object]] = {}
        # Standard locals reposition every few steps so the plaza feels alive. A pure
        # function of the step count, so it never needs saving.
        self._steps = 0

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="square_map")
        yield Static(id="square_status")
        yield Static(
            Text("Arrows / WASD / HJKL move  ·  walk into a place or person to engage"),
            id="square_help",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._refresh_units()
        self._refresh()

    async def on_screen_resume(self) -> None:
        # A shop bought from, a fixer's job accepted, or a gig run and consumed can all
        # change what stands in the square. Re-place the people, then redraw.
        if {location.id for location in self.territory.locations} != {
            location.id for location in self.square.locations
        }:
            # A safehouse can be bought in this district from a RealEstateScreen the
            # square pushed, so the Location list itself can grow while we sit under it.
            # The layout moved with it; put the runner back on the square's starting tile
            # rather than risk standing inside a block.
            self.square = self._build_square()
            self.player = self.square.start
        self._refresh_units()
        self._refresh()

    def _build_square(self) -> Square:
        # The run seed is what makes each playthrough's districts different; mixing in
        # the territory id keeps one run's districts distinct from each other. The seed
        # is part of the save, so a reload rebuilds the exact same square.
        return build_square(self.territory, seed=self.app.seed)

    # ── people in the square ─────────────────────────────────────────────────

    def _refresh_units(self) -> None:
        character = self.app.character
        fixers = sorted(
            (
                fixer
                for fixer in self.app.fixers
                if fixer.location_id == self.territory.id
                and fixer.id in character.discovered_fixers
            ),
            key=lambda fixer: fixer.id,
        )
        locations = {location.id: location for location in self.territory.locations}
        gig_ids = sorted(location_id for location_id in self.app.location_gigs if location_id in locations)
        hour = character.hour_of_day
        npcs = [
            npc
            for npc in npcs_for_territory(self.territory, self.app.seed, self.app.corp_map)
            if npc.is_present(hour)
        ]
        bucket = self._steps // 3
        keys = [f"fixer:{fixer.id}" for fixer in fixers]
        keys += [f"gig:{location_id}" for location_id in gig_ids]
        keys += [f"npc:{npc.id}:{bucket}" for npc in npcs]
        positions = place_units(self.square, keys, blocked=(self.player,))

        self._units = {}
        for fixer in fixers:
            position = positions.get(f"fixer:{fixer.id}")
            if position is not None:
                self._units[position] = ("fixer", fixer)
        for location_id in gig_ids:
            position = positions.get(f"gig:{location_id}")
            if position is not None:
                self._units[position] = ("gig", locations[location_id])
        for npc in npcs:
            position = positions.get(f"npc:{npc.id}:{bucket}")
            if position is not None:
                self._units[position] = ("npc", npc)

    # ── movement ─────────────────────────────────────────────────────────────

    def action_move(self, direction: str) -> None:
        dx, dy = self.DIRECTIONS[direction]
        x, y = self.player
        target = (x + dx, y + dy)

        person = self._units.get(target)
        if person is not None:
            self._interact_person(*person)
            return

        location = self.square.location_at(*target)
        if location is not None:
            self._enter_location(location)
            return

        if self.square.is_floor(*target):
            self.player = target
            self._steps += 1
            self._refresh_units()
            self._refresh()

    def _enter_location(self, location: Location) -> None:
        if not push_location_screen(self.app, location, self.territory):
            self.notify(f"{location.name} has no door open to walk-ins.", severity="warning")

    def _interact_person(self, kind: str, target: object) -> None:
        if kind == "fixer":
            self.app.push_screen(FixerOffersScreen(target))
            return
        if kind == "npc":
            self.app.push_screen(NpcDialogueScreen(target))
            return

        location: Location = target  # a gig's owner stands in for the gig here
        gig: Scene | None = self.app.location_gigs.get(location.id)
        if gig is None:
            self._refresh_units()
            self._refresh()
            return
        character = self.app.character
        if character.cash < gig.max_cash_loss:
            self.notify(
                f"You can't cover the gig's {gig.max_cash_loss}eb stake.", severity="warning"
            )
            return
        self.app.spend_time(gig.hours_cost)
        self.app.push_screen(SceneScreen(gig))

    # ── rendering ────────────────────────────────────────────────────────────

    def _refresh(self) -> None:
        self.query_one(CharacterSheet).refresh()
        self.query_one("#square_map", Static).update(self._render_map())
        self.query_one("#square_status", Static).update(self._status_text())

    def _render_map(self) -> Text:
        text = Text()
        for y in range(self.square.height):
            for x in range(self.square.width):
                if (x, y) == self.player:
                    text.append(PLAYER_GLYPH, style="bold bright_green")
                    continue
                person = self._units.get((x, y))
                if person is not None:
                    if person[0] == "fixer":
                        glyph, style = FIXER_GLYPH, "bold bright_cyan"
                    elif person[0] == "gig":
                        glyph, style = GIG_GLYPH, "bold bright_yellow"
                    else:
                        npc: Npc = person[1]
                        glyph, style = npc.glyph, npc.style
                    text.append(glyph, style=style)
                    continue
                location = self.square.location_at(x, y)
                if location is not None:
                    if self.square.signs.get(location.id) == (x, y):
                        style = KIND_STYLES.get(location.kind, "white")
                        text.append(KIND_GLYPHS[location.kind], style=f"bold {style}")
                    else:
                        text.append("▒", style="grey30")
                    continue
                if self.square.is_wall(x, y):
                    text.append(WALL_GLYPH, style="grey35")
                else:
                    text.append(FLOOR_GLYPH, style="grey50")
            if y < self.square.height - 1:
                text.append("\n")
        return text

    def _status_text(self) -> Text:
        x, y = self.player
        adjacent = None
        for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            adjacent = self._describe(x + dx, y + dy)
            if adjacent is not None:
                break
        detail = adjacent or "Open plaza — nothing within reach."
        count = len(self.territory.locations)
        noun = "place" if count == 1 else "places"
        return Text(f"{self.territory.name}  ·  {count} {noun}\n{detail}")

    def _describe(self, x: int, y: int) -> str | None:
        person = self._units.get((x, y))
        if person is not None:
            kind, target = person
            if kind == "fixer":
                fixer: Fixer = target
                return f"{fixer.name} — {fixer.specialty}. Walk into them to talk."
            if kind == "npc":
                npc: Npc = target
                return f"{npc.name} — {npc.label}. Walk into them to talk."
            location: Location = target
            gig = self.app.location_gigs.get(location.id)
            if gig is None:
                return None
            owner = next(
                (c for c in location.characters if c.id == gig.target_character_id),
                None,
            )
            who = owner.name if owner else "Someone"
            return f"{who} has work — {gig.title} ({gig.hours_cost}h). Walk into them to take it."

        location = self.square.location_at(x, y)
        if location is not None:
            return f"{location.name} — {kind_label(location.kind)}. Walk into it to enter."
        return None
