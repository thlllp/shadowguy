"""The walkable town square of the district the runner is standing in.

A roguelike counterpoint to `CorpMapScreen`'s Locals panel: one screen per territory,
arrows/WASD/HJKL move `@`, and walking into a building opens the same screen its "Enter"
row does. Fixers and the owners of local gigs stand in the plaza as `F`/`!`; walking into
one talks to them or takes their work.

Free to walk around: no step spends in-game time, and the square itself is generated from
the run seed plus the territory id (`shadowguy.townsquare`) rather than stored, so a
visit changes nothing and leaves nothing to save. The square is also fitted to the
terminal at mount: a short screen builds a shorter street rather than let the frame push
its own status line off the bottom.
"""

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.widgets import Footer, Header, Static

from shadowguy.corpmap import OWNER_COLORS, Location, Territory
from shadowguy.fixer import Fixer
from shadowguy.npcs import Npc, npcs_for_territory
from shadowguy.scene import Scene
from shadowguy.townsquare import (
    CAR_GLYPH,
    EMPTY_HOUSE_GLYPH,
    FIXER_GLYPH,
    FLOOR_GLYPH,
    GIG_GLYPH,
    KIND_GLYPHS,
    KIND_STYLES,
    PLAYER_GLYPH,
    SMALLEST_SQUARE_HEIGHT,
    SMALLEST_SQUARE_WIDTH,
    WALL_GLYPH,
    Square,
    build_square,
    kind_label,
    place_units,
)

from . import MENU_QUIT_BINDINGS, BackScreen, CharacterSheet, DayTicked
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
    #square_frame {
        width: 1fr;
        height: auto;
        /* The border colour is set in code from the district's owner (see
        _refresh_frame); $accent is only what unclaimed ground shows. */
        border: round $accent;
    }

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
        # Provisional: the full-size square. on_mount (and any resize) refits it to the
        # terminal, once the character sheet's height is actually known.
        self.square = build_square(self.territory, seed=self.app.seed)
        self.player = self.square.start
        # (x, y) -> ("fixer", Fixer) | ("gig", Location) | ("npc", Npc). Rebuilt on
        # resume because a completed gig disappears from app.location_gigs while this
        # screen sits under the SceneScreen that ran it.
        self._units: dict[tuple[int, int], tuple[str, object]] = {}
        # Standard locals reposition every few steps so the plaza feels alive. A pure
        # function of the step count, so it never needs saving.
        self._steps = 0
        # The owner colour currently on #square_frame, so the border is rewritten only
        # when the district changes hands rather than on every step.
        self._frame_color: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(Static(id="square_map"), id="square_frame")
        yield Static(id="square_status")
        yield Static(
            Text("Arrows / WASD / HJKL move  ·  walk into a place or person to engage"),
            id="square_help",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self._fit_square()
        self._refresh_units()
        self._refresh()

    def on_resize(self) -> None:
        # A terminal that shrinks under the square must not push the status line (the
        # only description of what is next to the runner) and the help row off-screen.
        if self.is_mounted and self._fit_square():
            self._refresh_units()
            self._refresh()

    def on_day_ticked(self, event: DayTicked) -> None:
        # Ownership can flip overnight: a rival claims neutral ground or a corp contest
        # resolves. The frame reads the territory's owner, so recolour it now rather
        # than waiting for the runner's next step (offers and gigs refresh too).
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

    def _square_height_budget(self) -> int:
        """Rows the square may use before it pushes the rest of the screen off.

        Header + footer (2), the two status rows, the help row and the frame's two
        border rows are fixed (7); the character sheet is as tall as it renders. At the
        documented 80x24 minimum that leaves 12 rows for the map, which is why the
        square builds a shorter street there instead of a 17-row one that hides its own
        description.
        """
        sheet_rows = len(self.query_one(CharacterSheet).render().splitlines())
        return self.app.size.height - 7 - sheet_rows

    def _fit_square(self) -> bool:
        """Shrink the square to the terminal, keeping the runner's view whole.

        Returns whether it rebuilt. Only shrinking triggers a rebuild: a terminal that
        grew again keeps the smaller square rather than moving the runner mid-walk.
        """
        height_budget = max(SMALLEST_SQUARE_HEIGHT, self._square_height_budget())
        width_budget = max(SMALLEST_SQUARE_WIDTH, self.app.size.width - 2)
        if self.square.height <= height_budget and self.square.width <= width_budget:
            return False
        self.square = self._build_square()
        self.player = self.square.start
        return True

    def _build_square(self) -> Square:
        # The run seed is what makes each playthrough's districts different; mixing in
        # the territory id keeps one run's districts distinct from each other. The seed
        # is part of the save, so a reload rebuilds the exact same square. The viewport
        # caps are the exception: a short terminal builds a shorter street (see
        # build_square), which is why this shouldn't be called before mount.
        return build_square(
            self.territory,
            seed=self.app.seed,
            max_height=max(SMALLEST_SQUARE_HEIGHT, self._square_height_budget()),
            max_width=max(SMALLEST_SQUARE_WIDTH, self.app.size.width - 2),
        )

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
        self._refresh_frame()
        self.query_one("#square_map", Static).update(self._render_map())
        self.query_one("#square_status", Static).update(self._status_text())

    def _refresh_frame(self) -> None:
        """Border the map in the colour the district's owner wears on the corp map.

        Same `corpmap.OWNER_COLORS` table as `CorpMapScreen`'s territory tags, so a
        district reads as the same holding in both views. Unclaimed ground has no entry
        there -- it falls back to the theme accent, like every other bordered panel.
        """
        color = OWNER_COLORS.get(self.territory.owner) or self.app.current_theme.accent
        if color == self._frame_color:
            return
        self._frame_color = color
        self.query_one("#square_frame").styles.border = ("round", color)

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
                if self.square.is_empty_house(x, y):
                    text.append(EMPTY_HOUSE_GLYPH, style="grey30")
                    continue
                if self.square.is_car(x, y):
                    text.append(CAR_GLYPH, style="grey62")
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
        if self.square.is_empty_house(x, y):
            return "A boarded-up house. Nobody's home."
        if self.square.is_car(x, y):
            return "A parked car. Doors locked."
        return None
