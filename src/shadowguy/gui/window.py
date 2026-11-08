"""Arcade window for the graphical Shadowguy front-end.

The window owns the view stack and the shared `GameSession`; each view draws
itself and handles its own keys. The slice keeps the TUI's rules and state and
replaces the presentation only.
"""

from __future__ import annotations

import random

import arcade

from shadowguy.corpmap import MODIFIER_LABELS, owner_label, travel_path
from shadowguy.corpmap_gen import GRID_COLS, GRID_ROWS
from shadowguy.corp_turn import operative_max
from shadowguy.cybernetics import free_humanity
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID

from .session import GameSession
from .views import MapView, StartView

BG = (8, 12, 18)
PANEL = (16, 23, 32)
PANEL_EDGE = (38, 62, 78)
TEXT = (205, 218, 228)
MUTED = (110, 130, 145)
ACCENT = (39, 224, 232)
ORANGE = (255, 138, 61)
GREEN = (94, 242, 160)
YELLOW = (232, 206, 92)
RED = (255, 77, 109)

OWNER_RGB = dict(
    zip(
        (faction.id for faction in FACTIONS),
        [(214, 72, 86), (80, 200, 220), (96, 200, 120), (220, 190, 90), (200, 110, 220)],
        strict=True,
    )
)
NEUTRAL = (70, 84, 96)
PARK = (60, 130, 80)
SLUM = (110, 80, 55)
OUTSKIRTS = (90, 80, 110)


def _owner_color(territory) -> tuple[int, int, int]:
    if territory.owner != "neutral":
        return OWNER_RGB.get(territory.owner, TEXT)
    if territory.is_park:
        return PARK
    if territory.is_slum:
        return SLUM
    if territory.is_outskirts:
        return OUTSKIRTS
    return NEUTRAL


class ShadowguyWindow(arcade.Window):
    TEXT = TEXT
    MUTED = MUTED
    ACCENT = ACCENT
    ORANGE = ORANGE
    GREEN = GREEN
    YELLOW = YELLOW
    RED = RED

    def __init__(self, *, seed: int | None = None, width: int = 1280, height: int = 860) -> None:
        super().__init__(width, height, "SHADOWGUY - engine slice", resizable=True)
        self.set_minimum_size(900, 600)
        arcade.set_background_color(BG)
        self.seed = seed if seed is not None else random.SystemRandom().randrange(1, 1_000_000_000)
        self.session = GameSession(seed=self.seed)
        self.selected_id = self.session.character.location_id
        self.map_view = None
        self.views = [StartView(self)]

    # ── run start ───────────────────────────────────────────────────────────

    def start_runner(self) -> None:
        self.show_map()

    def start_corp(self, faction_id: str) -> None:
        self.session.start_corp(faction_id)
        self.show_map()

    @property
    def top_view(self):
        return self.views[-1]

    def push(self, view) -> None:
        self.views.append(view)

    def pop(self) -> None:
        if len(self.views) > 1:
            self.views.pop()

    def show_map(self) -> None:
        """Reset the view stack to a fresh map after loading a run."""
        self.seed = self.session.seed
        self.selected_id = self.session.character.location_id
        self.map_view = MapView(self)
        self.views = [self.map_view]

    def clock(self) -> str:
        c = self.session.character
        minutes = int((c.elapsed_hours % 1) * 60)
        return f"Day {c.day}, {c.hour_of_day:02d}:{minutes:02d}"

    def move_cursor(self, map_view, dx: int, dy: int) -> None:
        current = self.session.corp_map.territories[map_view.selected_id]
        for conn_id in current.connections:
            candidate = self.session.corp_map.territories[conn_id]
            if (candidate.x - current.x, candidate.y - current.y) == (dx, dy):
                map_view.selected_id = conn_id
                self.selected_id = conn_id
                return

    def text(self, value, x, y, *, size=12, color=TEXT, bold=False, anchor_x="left", anchor_y="top"):
        arcade.Text(
            value, x, y, color=color, font_size=size, bold=bold,
            anchor_x=anchor_x, anchor_y=anchor_y,
        ).draw()

    def panel(self, rect, title: str) -> float:
        x, y, w, h = rect
        arcade.draw_lbwh_rectangle_filled(x, y, w, h, PANEL)
        arcade.draw_lbwh_rectangle_outline(x, y, w, h, PANEL_EDGE, 1)
        self.text(title, x + 14, y + h - 10, size=13, color=ACCENT, bold=True)
        arcade.draw_line(x + 14, y + h - 30, x + w - 14, y + h - 30, PANEL_EDGE, 1)
        return y + h - 42

    # ── input / drawing ─────────────────────────────────────────────────────

    def on_key_press(self, symbol: int, modifiers: int) -> None:
        self.top_view.on_key(symbol, modifiers)

    def on_draw(self) -> None:
        self.clear()
        self.top_view.draw()

    def draw_map_screen(self, message: str | None = None) -> None:
        header_h = 64
        footer_h = 56
        margin = 16
        content_bottom = footer_h + margin
        content_top = self.height - header_h - margin
        left_w = 300 if self.width >= 1150 else max(240, int(self.width * 0.26))
        right_w = 290 if self.width >= 1150 else max(220, int(self.width * 0.24))

        self._draw_header(header_h)
        self._draw_footer(footer_h, message)
        self._draw_character((margin, content_bottom, left_w, content_top - content_bottom))
        self._draw_selected(
            (self.width - margin - right_w, content_bottom, right_w, content_top - content_bottom)
        )
        map_rect = (
            margin + left_w + margin,
            content_bottom,
            self.width - (margin * 3) - left_w - right_w,
            content_top - content_bottom,
        )
        self._draw_map(map_rect)

    def _draw_header(self, header_h: int) -> None:
        self.text("SHADOWGUY", 20, self.height - 14, size=26, color=ACCENT, bold=True)
        self.text("ENGINE SLICE", 206, self.height - 24, size=11, color=MUTED, bold=True)
        right = f"SEED {self.seed}   |   {self.clock()}"
        self.text(right, self.width - 20, self.height - 22, size=12, color=TEXT, anchor_x="right")
        arcade.draw_line(16, self.height - header_h, self.width - 16, self.height - header_h, PANEL_EDGE, 1)

    def _draw_footer(self, footer_h: int, message: str | None) -> None:
        arcade.draw_line(16, footer_h, self.width - 16, footer_h, PANEL_EDGE, 1)
        if message:
            self.text(message, 20, footer_h - 14, size=12, color=ACCENT)
        if self.session.app.corp_only:
            controls = (
                "Arrows/WASD move   Enter relocate   C actions   R end day   "
                "F5 save   F9 load   Q quit"
            )
        else:
            controls = (
                "Arrows move   Enter travel   R rest   F fixers   J jobs   S shops   "
                "I gear   G den   V deliver   F5 save   F9 load   Q quit"
            )
        self.text(controls, 20, footer_h - 34, size=11, color=MUTED)

    def _draw_corp_status(self, rect) -> None:
        corp_state = self.session.app.corp_state
        faction = FACTIONS_BY_ID[corp_state.faction_id]
        held = sum(
            1
            for territory in self.session.corp_map.territories.values()
            if territory.owner == corp_state.faction_id
        )
        y = self.panel(rect, "CORP")
        x = rect[0] + 16
        self.text(faction.name, x, y, size=17, color=TEXT, bold=True)
        y -= 24
        self.text(self.clock(), x, y, size=11, color=MUTED)
        y -= 24
        for label, value in [
            ("Cash", f"{corp_state.cash}eb"),
            ("Research", f"{corp_state.research_points:g}rp"),
            ("Action pts", f"{corp_state.action_points}"),
            ("Districts", f"{held}"),
            ("Operatives", f"{corp_state.operatives}/{operative_max(corp_state)}"),
            ("Tasking", f"{corp_state.tasking_operatives}"),
            ("Scientists", f"{corp_state.scientists}"),
            ("Assistants", f"{corp_state.research_assistants}"),
        ]:
            self.text(label, x, y, size=11, color=MUTED)
            self.text(value, x + 118, y, size=11, color=TEXT)
            y -= 18
        y -= 10
        self.text("RESEARCH", x, y, size=10, color=MUTED, bold=True)
        y -= 18
        self.text(f"Logistics level {corp_state.logistics_research}", x, y, size=11, color=TEXT)

    def _draw_character(self, rect) -> None:
        if self.session.app.corp_only and self.session.app.corp_state is not None:
            self._draw_corp_status(rect)
            return
        character = self.session.character
        territory = self.session.corp_map.territories[character.location_id]
        y = self.panel(rect, "RUNNER")
        x = rect[0] + 16
        self.text(character.name, x, y, size=18, color=TEXT, bold=True)
        y -= 24
        self.text(self.clock(), x, y, size=11, color=MUTED)
        y -= 18
        self.text(territory.name, x, y, size=12, color=ACCENT)
        y -= 28
        self.text("HEALTH", x, y, size=10, color=MUTED, bold=True)
        self.text(f"{character.health}/{character.max_health}", x + rect[2] - 42, y, size=10, color=TEXT)
        y -= 14
        bar_w = rect[2] - 32
        arcade.draw_lbwh_rectangle_filled(x, y - 6, bar_w, 6, (30, 40, 48))
        ratio = max(0.0, min(1.0, character.health / max(1, character.max_health)))
        color = GREEN if ratio > 0.5 else YELLOW if ratio > 0.25 else RED
        arcade.draw_lbwh_rectangle_filled(x, y - 6, bar_w * ratio, 6, color)
        y -= 26
        for label, value in [
            ("Cash", f"{character.cash}eb"),
            ("Rep", f"{character.rep}"),
            ("Experience", f"{character.experience}xp"),
            ("Humanity", f"{free_humanity(character):g}"),
            ("Fatigue", f"{character.fatigue}"),
        ]:
            self.text(label, x, y, size=11, color=MUTED)
            self.text(value, x + 94, y, size=11, color=TEXT)
            y -= 18
        y -= 10
        self.text("STATS", x, y, size=10, color=MUTED, bold=True)
        y -= 18
        stats = [
            ("BODY", character.stat("body")),
            ("STR", character.stat("strength")),
            ("AGI", character.stat("agility")),
            ("PER", character.stat("perception")),
            ("LOG", character.stat("logic")),
            ("COOL", character.stat("cool")),
        ]
        for i, (label, value) in enumerate(stats):
            sx = x + (i % 2) * (rect[2] // 2 - 8)
            sy = y - (i // 2) * 18
            self.text(label, sx, sy, size=10, color=MUTED)
            self.text(str(value), sx + 52, sy, size=11, color=TEXT, bold=True)
        y -= 3 * 18 + 16
        self.text("JOBS ACCEPTED", x, y, size=10, color=MUTED, bold=True)
        self.text(str(len(character.accepted_jobs)), x + 120, y, size=11, color=TEXT)
        y -= 18
        self.text("CREW", x, y, size=10, color=MUTED, bold=True)
        self.text(str(len(character.crew)), x + 120, y, size=11, color=TEXT)

    def _draw_selected(self, rect) -> None:
        territory = self.session.corp_map.territories[self.selected_id]
        character = self.session.character
        y = self.panel(rect, "DISTRICT")
        x = rect[0] + 16
        self.text(territory.name, x, y, size=17, color=TEXT, bold=True)
        y -= 24
        self.text(owner_label(territory.owner), x, y, size=12, color=_owner_color(territory), bold=True)
        y -= 20
        if territory.id == character.location_id:
            self.text("YOU ARE HERE", x, y, size=10, color=ACCENT, bold=True)
            y -= 18
        else:
            hops = len(travel_path(self.session.corp_map, character.location_id, territory.id))
            self.text(f"{hops} hops away", x, y, size=11, color=MUTED)
            y -= 18
        self.text(f"Value {territory.value}", x, y, size=11, color=TEXT)
        y -= 24
        self.text("MODIFIERS", x, y, size=10, color=MUTED, bold=True)
        y -= 17
        for modifier, label in MODIFIER_LABELS.items():
            self.text(label, x, y, size=10, color=MUTED)
            self.text(str(territory.modifiers.get(modifier, 0)), x + 108, y, size=10, color=TEXT)
            y -= 16
        y -= 8
        self.text("LOCATIONS", x, y, size=10, color=MUTED, bold=True)
        y -= 17
        for location in territory.locations[:7]:
            self.text(f"- {location.name}", x, y, size=10, color=TEXT)
            y -= 15
        y -= 8
        self.text("CONNECTIONS", x, y, size=10, color=MUTED, bold=True)
        self.text(str(len(territory.connections)), x + 108, y, size=10, color=TEXT)

    def _draw_map(self, rect) -> None:
        map_x, map_y, map_w, map_h = rect
        if map_w < 40 or map_h < 40:
            return
        arcade.draw_lbwh_rectangle_filled(map_x, map_y, map_w, map_h, (10, 15, 21))
        arcade.draw_lbwh_rectangle_outline(map_x, map_y, map_w, map_h, PANEL_EDGE, 1)
        self.text("THE CITY", map_x + 12, map_y + map_h - 10, size=12, color=ACCENT, bold=True)
        cell_w = map_w / GRID_COLS
        cell_h = map_h / GRID_ROWS

        def pos(territory):
            return (map_x + (territory.x + 0.5) * cell_w, map_y + map_h - (territory.y + 0.5) * cell_h)

        territories = self.session.corp_map.territories
        for territory in territories.values():
            x1, y1 = pos(territory)
            for conn_id in territory.connections:
                if territory.id > conn_id:
                    continue
                x2, y2 = pos(territories[conn_id])
                arcade.draw_line(x1, y1, x2, y2, (30, 42, 52), 1)

        character = self.session.character
        if self.selected_id != character.location_id:
            path = travel_path(self.session.corp_map, character.location_id, self.selected_id)
            if path:
                points = [character.location_id, *path]
                for a, b in zip(points, points[1:]):
                    x1, y1 = pos(territories[a])
                    x2, y2 = pos(territories[b])
                    arcade.draw_line(x1, y1, x2, y2, ACCENT, 2)

        radius = max(3.0, min(cell_w, cell_h) * 0.18)
        for territory in territories.values():
            x, y = pos(territory)
            arcade.draw_circle_filled(x, y, radius, _owner_color(territory))
            if territory.id == character.location_id:
                arcade.draw_circle_outline(x, y, radius + 3, ACCENT, 2)
            if territory.id == self.selected_id:
                arcade.draw_circle_outline(x, y, radius + 5, TEXT, 1)

        selected = territories[self.selected_id]
        sx, sy = pos(selected)
        self.text(selected.name, sx + 9, sy + 4, size=10, color=TEXT, bold=True)
        player = territories[character.location_id]
        px, py = pos(player)
        self.text("YOU", px + 9, py - 6, size=9, color=ACCENT, bold=True)
        job = character.smuggling_job
        if job is not None:
            target = territories[job.destination_territory_id]
            tx, ty = pos(target)
            arcade.draw_circle_outline(tx, ty, radius + 7, YELLOW, 2)
            self.text("PKG", tx + 9, ty - 6, size=9, color=YELLOW, bold=True)

        lx = map_x + 12
        ly = map_y + 12
        for faction in FACTIONS:
            arcade.draw_circle_filled(lx, ly, 3, OWNER_RGB[faction.id])
            label = faction.name.split()[0]
            self.text(label, lx + 8, ly + 4, size=9, color=MUTED)
            lx += 9 + len(label) * 6 + 12


def run(*, seed: int | None = None, width: int = 1280, height: int = 860) -> None:
    ShadowguyWindow(seed=seed, width=width, height=height)
    arcade.run()
