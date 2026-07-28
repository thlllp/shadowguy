import asyncio

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import Character
from shadowguy.combat import CombatOutcome, Drop
from shadowguy.corpmap import (
    MODIFIER_LABELS,
    OWNER_COLORS,
    PLAYER_OWNED_KINDS,
    SHOP_KINDS,
    LocationKind,
    Territory,
    TerritoryModifier,
    owner_label,
    render_ascii_map,
)
from shadowguy.corp_turn import (
    ACADEMY_TRAINING_COST,
    DEVELOPMENT_BUMP_COST,
    SURVEILLANCE_BUMP_COST,
    TRAINING_DAYS,
    EmployeeCategory,
    FactionEvent,
    assistant_capacity,
    assistant_rate,
    build_efficiency_upgrade,
    build_lab,
    development_targets,
    employee_plural,
    expand_into,
    expansion_cost,
    expansion_candidates,
    lab_capacity,
    log_faction_event,
    next_efficiency_cost,
    next_lab_cost,
    owned_research_facility,
    raise_development,
    raise_surveillance,
    research_rate,
    surveillance_targets,
    train_employees,
)
from shadowguy.encounters import GangEncounter, gang_attack, roll_gang_encounter
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.fixer import discover_fixers_here
from shadowguy.gangs import GANGS_BY_ID
from shadowguy.jobs import GANG_JOB_STANDING_GAIN, generate_legwork_for_job
from shadowguy.runners import RUNNERS_BY_ID
from shadowguy.scene import Scene
from shadowguy.shops import equipped_travel_reduction

from . import (
    MENU_QUIT_BINDINGS,
    BackScreen,
    CharacterSheet,
    _menu_css,
    _populate_list,
    _replace_items,
    matrix_warning,
)
from .combat_screen import CombatScreen
from .corp_screen import CorpScreen, ResearchTreeScreen
from .info_screens import CyberdeckScreen, InventoryScreen, PhoneScreen, SkillsScreen
from .scene_screen import SceneScreen
from .shop_screens import (
    BarScreen,
    CorpHQScreen,
    FixerOffersScreen,
    GangDenScreen,
    HospitalScreen,
    JunkyardScreen,
    RealEstateScreen,
    SafehouseScreen,
    ShopScreen,
)

TRAVEL_HOURS_COST = 2.0


def _travel_hours(character: Character) -> float:
    return TRAVEL_HOURS_COST * (1 - equipped_travel_reduction(character.inventory))


_RUNNER_CATEGORIES = [
    ("gig", "Gigs"),
    ("job", "Jobs"),
    ("legwork", "Legwork"),
    ("local", "Local"),
    ("gear", "Gear"),
    ("cyberdeck", "Cyberdeck"),
    ("skills", "Skills"),
    ("phone", "Phone"),
    ("corp", "Corp"),
]

_CORP_CATEGORIES = [
    ("corp", "Corp"),
    ("phone", "Phone"),
    ("tech", "Technology"),
]


def _sighting_label(sighting, corp_map) -> str:
    who = "You" if sighting.kind == "player" else RUNNERS_BY_ID[sighting.actor_id].name
    territory_name = corp_map.territories[sighting.territory_id].name
    return f"Day {sighting.day} — {who} spotted in {territory_name}"


class CorpMapScreen(BackScreen):
    """The home screen for both a runner and a corp-only run, with a left-side
    category sidebar always visible. The main content area shows the map by default;
    selecting an inline category (gigs, jobs, legwork, local, corp) replaces it with
    that category's activity list. 'escape' returns to the map. Categories that push
    their own screen (gear, cyberdeck, skills, phone, tech) do so on top of the map,
    same as before."""

    BINDINGS = [
        *MENU_QUIT_BINDINGS,
        Binding("escape", "back", "Back", show=False),
        ("r", "run_corp", "Run a Corp"),
        ("i", "inventory", "Gear"),
        ("d", "cyberdeck", "Cyberdeck"),
        ("k", "skills", "Skills"),
        ("c", "phone", "Phone"),
        ("t", "research_tree", "Research Tree"),
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("enter", "enter_action", "Select"),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #sidebar {
        width: 20;
        border: solid $accent;
        padding: 0 1;
    }

    #categories {
        border: none;
    }

    #categories:focus {
        border: none;
    }

    #main_panel {
        width: 1fr;
    }

    #map_scroll {
        height: 1fr;
        overflow-x: auto;
        padding: 0 1;
    }

    #territory_summary {
        /* Fixed height (not auto) -- _territory_summary_text always renders
        exactly 4 lines, so hovering a different territory never resizes this
        panel and shoves #map_scroll's scroll position around. */
        height: 4;
        border-top: solid $accent;
        padding: 0 1;
    }

    #activities {
        height: auto;
    }

    #local_locations, #local_fixers {
        height: auto;
    }

    #local_locations_panel, #local_fixers_panel {
        height: auto;
    }

    #academy_list, #research_list, #surveillance_list {
        height: auto;
    }

    #academy_panel, #research_panel, #surveillance_panel {
        height: auto;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.selected_id = ""
        self.hovered_id: str | None = None
        self.rendered = None
        self._render_key: tuple[str, str] | None = None
        self._map_locals_task: asyncio.Task | None = None
        # Restore the last-viewed category (app.main_menu_category) across a full
        # screen rebuild (load_state/restart_run) -- but only if it's one of this
        # game's own categories, since a runner game and a corp-only game don't
        # share the same category set and the app attribute is shared between them.
        remembered = self.app.main_menu_category
        self.selected_category = remembered if remembered in dict(self.categories) else None

    @property
    def categories(self) -> list[tuple[str, str]]:
        return _CORP_CATEGORIES if self.app.corp_only else _RUNNER_CATEGORIES

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield ListView(id="categories")
            with Vertical(id="main_panel"):
                yield ScrollableContainer(Static(markup=False, id="map"), id="map_scroll")
                yield Static(markup=False, id="territory_summary")
                yield ListView(id="activities")
                yield Collapsible(
                    ListView(id="local_locations"), title="Locations", collapsed=False, id="local_locations_panel"
                )
                yield Collapsible(
                    ListView(id="local_fixers"), title="Fixers", collapsed=False, id="local_fixers_panel"
                )
                yield Static(id="corp_info")
                yield Collapsible(
                    ListView(id="academy_list"), title="Academy", collapsed=False, id="academy_panel"
                )
                yield Collapsible(
                    ListView(id="research_list"), title="Research Facility", collapsed=False, id="research_panel"
                )
                yield Collapsible(
                    ListView(id="surveillance_list"), title="Surveillance Log", collapsed=True, id="surveillance_panel"
                )
        yield Footer()

    # ── life-cycle ──────────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        self.selected_id = self.app.character.location_id
        await self._refresh_categories()
        self.query_one("#categories", ListView).can_focus = False
        self.query_one(CharacterSheet).refresh()
        if self.selected_category is None:
            self._refresh_map()
        else:
            await self._refresh_activities()
            self.focus_next()

    def _refresh(self) -> None:
        """Public entry point for tests that manipulate selected_id/hovered_id
        directly and need to re-render the map."""
        self._do_refresh_map()

    async def on_screen_resume(self) -> None:
        if self.selected_category is None:
            self._refresh_map()
            self.set_focus(None)
        else:
            await self._refresh_activities()

    # ── back / map mode ─────────────────────────────────────────────────────

    def action_back(self) -> None:
        if self.selected_category is not None:
            self.selected_category = None
            self._refresh_map()
            self.set_focus(None)
        # else: no-op — nothing below this screen to pop back to

    # ── category navigation ─────────────────────────────────────────────────

    async def _refresh_categories(self) -> None:
        items = [ListItem(Static(label), id=f"cat_{key}") for key, label in self.categories]
        await _replace_items(self.query_one("#categories", ListView), items)

    async def _select_category(self, key: str) -> None:
        if key == "phone":
            self.app.push_screen(PhoneScreen())
            return
        if key == "gear":
            self.app.push_screen(InventoryScreen())
            return
        if key == "cyberdeck":
            self.app.push_screen(CyberdeckScreen())
            return
        if key == "skills":
            self.app.push_screen(SkillsScreen())
            return
        if key == "corp" and not self.app.corp_only:
            self.app.push_screen(CorpScreen())
            return
        if key == "tech":
            if self.app.corp_state is not None:
                self.app.push_screen(ResearchTreeScreen())
            return
        self.selected_category = key
        self.app.main_menu_category = key
        await self._refresh_activities()
        # #categories itself stays unfocusable (arrows drive the map while nothing's
        # selected) -- focus the category's own list so up/down/enter work it, same
        # as the old MainMenu/CorpMainMenu did via PanelNav. Tab/shift+tab (Textual's
        # own default bindings) cycle to any other visible list a category shows
        # (e.g. Local's location/fixer panels, Corp's academy/research/surveillance).
        self.focus_next()

    # ── quick keybindings ───────────────────────────────────────────────────

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        # A corp-only run never builds a runner, so the runner-only quick keys
        # (r/i/d/k) have nothing to act on -- keep them out of the Footer entirely
        # instead of advertising a keybinding that silently no-ops when pressed.
        if self.app.corp_only and action in ("run_corp", "inventory", "cyberdeck", "skills"):
            return False
        return True

    def action_inventory(self) -> None:
        if not self.app.corp_only:
            self.app.push_screen(InventoryScreen())

    def action_cyberdeck(self) -> None:
        if not self.app.corp_only:
            self.app.push_screen(CyberdeckScreen())

    def action_skills(self) -> None:
        if not self.app.corp_only:
            self.app.push_screen(SkillsScreen())

    def action_phone(self) -> None:
        self.app.push_screen(PhoneScreen())

    def action_run_corp(self) -> None:
        if not self.app.corp_only:
            self.app.push_screen(CorpScreen())

    def action_research_tree(self) -> None:
        if self.app.corp_state is not None:
            self.app.push_screen(ResearchTreeScreen())

    # ── list-view dispatch ──────────────────────────────────────────────────

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "categories":
            await self._select_category(event.item.id.removeprefix("cat_"))
            return

        item_id = event.item.id
        character = self.app.character

        if item_id.startswith("sighting_") or item_id == "no_sightings":
            return

        if item_id == "rest":
            self.app.rest()
            await self._refresh_activities()
            self.query_one(CharacterSheet).refresh()
            return

        if item_id == "deliver_package":
            if character.location_id != character.smuggling_job.destination_territory_id:
                return
            job = character.deliver_smuggling_job(GANG_JOB_STANDING_GAIN)
            self.notify(f"Delivered. +{job.reward_cash}eb.")
            self.query_one(CharacterSheet).refresh()
            await self._refresh_activities()
            return

        if item_id.startswith("security_info_"):
            return

        if item_id.startswith("gig_"):
            location_id = item_id.removeprefix("gig_")
            gig = self.app.location_gigs.get(location_id)
            if gig is None:
                return
            if character.cash < gig.max_cash_loss:
                return
            self.app.spend_time(gig.hours_cost)
            self.app.push_screen(SceneScreen(gig))
            return

        if item_id.startswith("legwork_"):
            offer_id = item_id.removeprefix("legwork_")
            job = next(job for job in character.accepted_jobs if job.id == offer_id)
            if not self._on_site(job.scene):
                return
            legwork_scene = generate_legwork_for_job(job.scene, self.app.corp_map, self.app.rng)
            self.app.spend_time(legwork_scene.hours_cost, protect_job_id=job.scene.id)
            self.app.push_screen(SceneScreen(legwork_scene))
            return

        if item_id.startswith("job_"):
            offer_id = item_id.removeprefix("job_")
            job = next(job for job in character.accepted_jobs if job.id == offer_id)
            if not self._on_site(job.scene):
                return
            if not job.timing.is_available(character.day):
                return
            self.app.spend_time(job.scene.hours_cost, protect_job_id=job.scene.id)
            self.app.push_screen(SceneScreen(job.scene))
            return

        if item_id.startswith("local_fixer_"):
            fixer_id = item_id.removeprefix("local_fixer_")
            fixer = next(fixer for fixer in self.app.fixers if fixer.id == fixer_id)
            self.app.push_screen(FixerOffersScreen(fixer))
            return

        if item_id.startswith("map_local_fixer_"):
            fixer_id = item_id.removeprefix("map_local_fixer_")
            fixer = next(fixer for fixer in self.app.fixers if fixer.id == fixer_id)
            self.app.push_screen(FixerOffersScreen(fixer))
            return

        if item_id.startswith("map_local_") and item_id != "map_local_district":
            location_id = item_id.removeprefix("map_local_")
            t = self.app.corp_map.territories[self.hovered_id or self.selected_id]
            location = next((loc for loc in t.locations if loc.id == location_id), None)
            if location is None:
                return
            if t.id != character.location_id:
                self.notify(f"Travel to {t.name} first.", severity="warning")
                return
            self._push_location_screen(location, t)
            return

        if item_id.startswith("local_") and item_id != "local_district":
            location_id = item_id.removeprefix("local_")
            here = self.app.corp_map.territories[character.location_id]
            location = next((loc for loc in here.locations if loc.id == location_id), None)
            if location is None:
                return
            self._push_location_screen(location, here)
            return

        if item_id.startswith("corpinfo_"):
            faction = FACTIONS_BY_ID[item_id.removeprefix("corpinfo_")]
            self.notify(f"Find {faction.name}'s HQ on the map and walk in.")
            return

        if item_id.startswith("expand_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("expand_")
            territory = self.app.corp_map.territories[territory_id]
            if expand_into(corp_state, self.app.corp_map, territory_id, self.app.rng):
                self.notify(f"Claimed {territory.name}.")
                log_faction_event(
                    self.app.faction_events,
                    corp_state.faction_id,
                    FactionEvent(kind="territory", day=self.app.character.day, territory_id=territory_id),
                )
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh_activities()
            return

        if item_id.startswith("surveil_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("surveil_")
            territory = self.app.corp_map.territories[territory_id]
            if raise_surveillance(corp_state, self.app.corp_map, territory_id):
                self.notify(f"Surveillance raised in {territory.name}.")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh_activities()
            return

        if item_id.startswith("develop_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("develop_")
            territory = self.app.corp_map.territories[territory_id]
            if raise_development(corp_state, self.app.corp_map, territory_id):
                self.notify(f"{territory.name} builds up. Development raised.")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh_activities()
            return

        if item_id.startswith("train_"):
            corp_state = self.app.corp_state
            category = EmployeeCategory(item_id.removeprefix("train_"))
            if train_employees(corp_state, self.app.corp_map, category, self.app.character.day):
                self.notify(
                    f"Training a batch of {employee_plural(category)} — "
                    f"ready in {TRAINING_DAYS[category]} days."
                )
            elif corp_state.pending_recruit is not None:
                self.notify("The Academy's already training a batch.", severity="warning")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh_activities()
            return

        if item_id == "build_lab":
            corp_state = self.app.corp_state
            if build_lab(corp_state, self.app.corp_map):
                self.notify("Built a new lab at the Research Facility.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh_activities()
            return

        if item_id == "build_efficiency":
            corp_state = self.app.corp_state
            if build_efficiency_upgrade(corp_state, self.app.corp_map):
                self.notify("Upgraded the Research Facility's efficiency.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh_activities()

    # ── content visibility ──────────────────────────────────────────────────

    def _set_content_visibility(self) -> None:
        """Show/hide widgets based on self.selected_category."""
        in_map = self.selected_category is None
        is_local = self.selected_category == "local"
        is_corp = self.selected_category == "corp"

        self.query_one("#map_scroll").display = in_map
        self.query_one("#territory_summary").display = in_map
        self.query_one("#activities").display = not in_map
        self.query_one("#local_locations_panel").display = is_local or in_map
        self.query_one("#local_fixers_panel").display = is_local or in_map
        self.query_one("#corp_info").display = is_corp
        self.query_one("#academy_panel").display = is_corp
        self.query_one("#research_panel").display = is_corp
        self.query_one("#surveillance_panel").display = is_corp

    # ── map ─────────────────────────────────────────────────────────────────

    def _refresh_map(self) -> None:
        self._set_content_visibility()
        self.query_one(CharacterSheet).refresh()
        self._do_refresh_map()

    def _do_refresh_map(self) -> None:
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
        self.query_one("#territory_summary", Static).update(self._territory_summary_text(t, here, character))
        self.query_one(CharacterSheet).refresh()
        # Cancel any still-running refresh for a previously hovered/selected territory
        # before starting this one -- otherwise two overlapping tasks race to
        # clear()/append() the same #local_locations/#local_fixers ListViews (rapid
        # mouse movement or repeated clicks spawn one task per move), which can raise
        # DuplicateIds or leave the panel showing a stale territory's rows.
        if self._map_locals_task is not None and not self._map_locals_task.done():
            self._map_locals_task.cancel()
        self._map_locals_task = asyncio.create_task(self._refresh_map_locals(t))

    async def _refresh_map_locals(self, t: Territory) -> None:
        """Populate the local locations and fixers panels for territory *t*
        (the one under the cursor). Called as a background task from _do_refresh_map
        so it never blocks mouse-move or keyboard movement."""
        if self.selected_category is not None:
            return
        character = self.app.character
        await _populate_list(
            self.query_one("#local_locations", ListView),
            t.locations,
            id_prefix="map_local_",
            label=lambda loc: f"{loc.name} ({loc.kind})",
        )
        fixers_here = [f for f in self.app.fixers if f.location_id == t.id
                       and f.id in character.discovered_fixers]
        await _populate_list(
            self.query_one("#local_fixers", ListView),
            fixers_here,
            id_prefix="map_local_fixer_",
            label=lambda f: (
                f"{f.name} — {f.specialty} "
                f"({len(f.open_offers)} jobs, {len(f.security_offers)} security available)"
            ),
            empty_label="No fixer seated here.",
            empty_id="no_map_local_fixers",
        )

    def _territory_summary_text(self, t: Territory, here: Territory, character: Character) -> str:
        """Always exactly 4 lines -- #territory_summary is a fixed-height panel (see
        its CSS) precisely so a different territory's summary never resizes it and
        shoves #map_scroll's scroll position around."""
        borders = ", ".join(self.app.corp_map.territories[c].name for c in t.connections)
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
        modifier_line = "  ".join(
            f"{MODIFIER_LABELS[modifier]}:{level}" for modifier, level in t.modifiers.items()
        )
        return (
            f"{t.name} — owner: {owner_label(t.owner)}, value: {t.value}{gang_suffix}{fixer_suffix}\n"
            f"Borders: {borders}\n"
            f"{modifier_line}\n"
            f"{self._travel_hint(t, here, character)}"
        )

    def _travel_hint(self, t: Territory, here: Territory, character: Character) -> str:
        if t.id == here.id:
            return f"You are here. Day {character.day}."
        if t.id not in here.connections:
            return f"No route from {here.name} — travel is only to a bordering district."
        return f"enter: travel here ({_travel_hours(character):.1f}h)"

    # ── map movement / travel ───────────────────────────────────────────────

    def action_move(self, direction: str) -> None:
        if self.selected_category is not None:
            return
        dx, dy = self.DIRECTIONS[direction]
        current = self.app.corp_map.territories[self.selected_id]
        for conn_id in current.connections:
            candidate = self.app.corp_map.territories[conn_id]
            if (candidate.x - current.x, candidate.y - current.y) == (dx, dy):
                self.selected_id = conn_id
                self._do_refresh_map()
                return

    def action_enter_action(self) -> None:
        if self.selected_category is None:
            self.action_travel()
        # When an activity list is focused, enter selects the highlighted item
        # (ListView handles this natively — no extra wiring needed here)

    def action_travel(self) -> None:
        character = self.app.character
        here = self.app.corp_map.territories[character.location_id]
        if self.selected_id not in here.connections:
            return
        if self.app.corp_only:
            character.location_id = self.selected_id
            self._do_refresh_map()
            return
        self.app.spend_time(_travel_hours(character))
        character.location_id = self.selected_id
        self._do_refresh_map()
        self._maybe_gang_encounter()

    def on_mouse_move(self, event: events.MouseMove) -> None:
        offset = event.get_content_offset(self.query_one("#map", Static))
        hovered = (
            self.rendered.territory_at(offset.y, offset.x)
            if offset is not None and self.rendered is not None
            else None
        )
        if hovered != self.hovered_id:
            self.hovered_id = hovered
            self._do_refresh_map()

    def on_click(self) -> None:
        if self.hovered_id is not None:
            self.selected_id = self.hovered_id
            self._do_refresh_map()

    # ── gang encounters ─────────────────────────────────────────────────────

    def _maybe_gang_encounter(self) -> None:
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

    def _push_location_screen(self, location, territory) -> None:
        if location.kind in SHOP_KINDS:
            self.app.push_screen(ShopScreen(location))
        elif location.kind == LocationKind.HOSPITAL:
            self.app.push_screen(HospitalScreen(location))
        elif location.kind == LocationKind.JUNKYARD:
            self.app.push_screen(JunkyardScreen(location))
        elif location.kind == LocationKind.REAL_ESTATE:
            self.app.push_screen(RealEstateScreen(location))
        elif location.kind == LocationKind.CORP_HQ:
            self.app.push_screen(CorpHQScreen(location, FACTIONS_BY_ID[territory.owner]))
        elif location.kind == LocationKind.BAR:
            self.app.push_screen(BarScreen(location))
        elif location.kind == LocationKind.GANG_DEN:
            self.app.push_screen(GangDenScreen(location, GANGS_BY_ID[territory.gang_id]))
        elif location.kind in PLAYER_OWNED_KINDS:
            self.app.push_screen(SafehouseScreen(location))

    # ── activity lists ──────────────────────────────────────────────────────

    def _on_site(self, scene: Scene) -> bool:
        return self.app.character.location_id == scene.target_territory_id

    def _district(self, scene: Scene) -> str:
        return self.app.corp_map.territories[scene.target_territory_id].name

    async def _refresh_activities(self) -> None:
        self._set_content_visibility()
        self.query_one(CharacterSheet).refresh()

        if self.selected_category == "corp":
            await self._refresh_corp()
        elif self.selected_category is not None:
            await self._refresh_runner_activities()
        elif self.app.corp_only:
            # Map mode in corp-only: hide corp panels
            pass

    async def _refresh_runner_activities(self) -> None:
        character = self.app.character
        items: list[ListItem] = []

        if self.selected_category == "gig":
            here = self.app.corp_map.territories[character.location_id]
            for location in here.locations:
                gig = self.app.location_gigs.get(location.id)
                if gig is None:
                    continue
                owner = next((c for c in location.characters if c.id == gig.target_character_id), None)
                who = f" — {owner.name}" if owner else ""
                label = f"Gig — {gig.title} @ {location.name}{who} ({gig.hours_cost}h)"
                if character.cash < gig.max_cash_loss:
                    label += f" — can't cover the stake ({gig.max_cash_loss} cash)"
                items.append(ListItem(Static(label), id=f"gig_{location.id}"))

        elif self.selected_category == "job":
            today = character.day
            for job in character.accepted_jobs:
                label = f"Job — {job.scene.title} ({job.scene.hours_cost}h) — {job.timing.label}"
                if not self._on_site(job.scene):
                    label += f" — travel to {self._district(job.scene)}"
                elif not job.timing.is_available(today):
                    label += " — not yet"
                label += matrix_warning(character, job.scene)
                items.append(ListItem(Static(label), id=f"job_{job.id}"))
            for contract in character.security_contracts:
                faction = FACTIONS_BY_ID[contract.faction_id]
                territory = self.app.corp_map.territories[contract.territory_id]
                label = (
                    f"Security contract — {faction.name} at {territory.name} "
                    f"({contract.nights_completed}/{contract.nights_total} nights, "
                    f"{contract.nightly_pay}eb/night)"
                )
                if character.location_id != contract.territory_id:
                    label += f" — travel to {territory.name} to stand watch"
                items.append(ListItem(Static(label), id=f"security_info_{contract.id}"))

        elif self.selected_category == "legwork":
            for job in character.accepted_jobs:
                advantage = character.advantage_for(job.scene.id)
                legwork_label = f"Legwork — Case the job: {job.scene.title}"
                if advantage:
                    legwork_label += f" (advantage +{advantage} banked)"
                if not self._on_site(job.scene):
                    legwork_label += f" — travel to {self._district(job.scene)}"
                items.append(ListItem(Static(legwork_label), id=f"legwork_{job.id}"))

        elif self.selected_category == "local":
            corp_map = self.app.corp_map
            here = corp_map.territories[character.location_id]
            gang_suffix = f", gang: {GANGS_BY_ID[here.gang_id].name}" if here.gang_id else ""
            items.append(
                ListItem(
                    Static(f"{here.name} — {owner_label(here.owner)}{gang_suffix}"),
                    id="local_district",
                )
            )
            await _populate_list(
                self.query_one("#local_locations", ListView),
                here.locations,
                id_prefix="local_",
                label=lambda location: f"{location.name} ({location.kind})",
            )
            discover_fixers_here(self.app.fixers, character)
            fixers_here = [f for f in self.app.fixers if f.location_id == character.location_id]
            await _populate_list(
                self.query_one("#local_fixers", ListView),
                fixers_here,
                id_prefix="local_fixer_",
                label=lambda fixer: (
                    f"{fixer.name} — {fixer.specialty} "
                    f"({len(fixer.open_offers)} jobs, {len(fixer.security_offers)} security available)"
                ),
                empty_label="No fixer seated here.",
                empty_id="no_local_fixers",
            )

        if character.smuggling_job is not None:
            job = character.smuggling_job
            destination = self.app.corp_map.territories[job.destination_territory_id]
            if character.location_id == job.destination_territory_id:
                label = f"Deliver the package (due by day {job.deadline_day})"
            else:
                label = f"Deliver the package — travel to {destination.name} (due by day {job.deadline_day})"
            items.append(ListItem(Static(label), id="deliver_package"))

        items.append(ListItem(Static(self.app.rest_label()), id="rest"))
        await _replace_items(self.query_one("#activities", ListView), items)

    async def _refresh_corp(self) -> None:
        corp_state = self.app.corp_state
        info = self.query_one("#corp_info", Static)
        activities = self.query_one("#activities", ListView)
        academy_list = self.query_one("#academy_list", ListView)
        research_list = self.query_one("#research_list", ListView)
        surveillance_list = self.query_one("#surveillance_list", ListView)

        if corp_state is None:
            character = self.app.character
            info.update(
                "You aren't running a corp.\n"
                "Corps are taken, not chosen — go see one in person, at its HQ."
            )
            items = [
                ListItem(
                    Static(
                        f"{faction.name} ({faction.specialty}) — "
                        f"standing {character.standing_with(faction.id):+d}"
                    ),
                    id=f"corpinfo_{faction.id}",
                )
                for faction in FACTIONS
            ]
            await _replace_items(activities, items)
            await _replace_items(academy_list, [])
            await _replace_items(research_list, [])
            await _replace_items(surveillance_list, [])
            self.query_one("#academy_panel").display = False
            self.query_one("#research_panel").display = False
            self.query_one("#surveillance_panel").display = False
            return

        self.query_one("#academy_panel").display = True
        self.query_one("#research_panel").display = True
        self.query_one("#surveillance_panel").display = True

        corp_map = self.app.corp_map
        faction = FACTIONS_BY_ID[corp_state.faction_id]
        owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
        candidates = expansion_candidates(corp_map, corp_state.faction_id)
        territory_names = ", ".join(t.name for t in owned) or "none"
        facility = owned_research_facility(corp_state, self.app.corp_map)
        facility_line = ""
        if facility is not None:
            capacity = lab_capacity(facility)
            working = min(corp_state.scientists, capacity)
            assist_capacity = assistant_capacity(facility)
            working_assistants = min(corp_state.research_assistants, assist_capacity)
            facility_line = (
                f"\nResearch Facility: tier {facility.research_tier}, "
                f"{working}/{capacity} scientists at work "
                f"({research_rate(corp_state, facility):g}rp/scientist), "
                f"{working_assistants}/{assist_capacity} assistants at work "
                f"({assistant_rate(corp_state):g}rp/assistant)"
            )
        info.update(
            f"{faction.name} — {corp_state.cash}eb — {corp_state.research_points}rp — "
            f"{corp_state.scientists} scientists — {corp_state.operatives} operatives — "
            f"{corp_state.research_assistants} research assistants — "
            f"Day {self.app.character.day}\n"
            f"Territories ({len(owned)}): {territory_names}"
            f"{facility_line}"
        )

        items = []
        for territory_id in candidates:
            territory = corp_map.territories[territory_id]
            cost = expansion_cost(territory)
            label = f"Expand into {territory.name} — {cost}eb"
            if corp_state.daily_action_used:
                label += " (already acted today)"
            elif cost > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"expand_{territory_id}"))
        if not candidates:
            items.append(ListItem(Static("No neutral ground borders your territory."), id="none"))

        for territory in surveillance_targets(corp_state, corp_map):
            level = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
            label = (
                f"Raise Surveillance in {territory.name} "
                f"({level}→{level + 1}) — {SURVEILLANCE_BUMP_COST}eb"
            )
            if SURVEILLANCE_BUMP_COST > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"surveil_{territory.id}"))

        for territory in development_targets(corp_state, corp_map):
            level = territory.modifiers.get(TerritoryModifier.DEVELOPMENT, 0)
            label = f"Develop {territory.name} ({level}→{level + 1}) — {DEVELOPMENT_BUMP_COST}eb"
            if DEVELOPMENT_BUMP_COST > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"develop_{territory.id}"))

        items.append(ListItem(Static(self.app.rest_label()), id="rest"))
        await _replace_items(activities, items)

        academy_items = []
        pending = corp_state.pending_recruit
        if pending is not None:
            days_left = pending.ready_day - self.app.character.day
            academy_items.append(
                ListItem(
                    Static(
                        f"Training {employee_plural(pending.category)} — "
                        f"ready in {days_left} day{'s' if days_left != 1 else ''}"
                    ),
                    id="pending_recruit",
                )
            )
        else:
            for category in EmployeeCategory:
                cost = ACADEMY_TRAINING_COST[category]
                label = f"Train {employee_plural(category)} ({TRAINING_DAYS[category]}d) — {cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif cost > corp_state.cash:
                    label += " (can't afford)"
                academy_items.append(ListItem(Static(label), id=f"train_{category}"))
        await _replace_items(academy_list, academy_items)

        research_items = []
        if facility is not None:
            cost = next_lab_cost(facility)
            if cost is None:
                research_items.append(ListItem(Static("Labs fully upgraded"), id="labs_maxed"))
            else:
                label = f"Build a lab — {cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif cost > corp_state.cash:
                    label += " (can't afford)"
                research_items.append(ListItem(Static(label), id="build_lab"))

            efficiency_cost = next_efficiency_cost(facility)
            if efficiency_cost is None:
                research_items.append(ListItem(Static("Efficiency fully upgraded"), id="efficiency_maxed"))
            else:
                label = f"Upgrade efficiency — {efficiency_cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif efficiency_cost > corp_state.cash:
                    label += " (can't afford)"
                research_items.append(ListItem(Static(label), id="build_efficiency"))
        await _replace_items(research_list, research_items)

        if corp_state.sightings:
            sighting_items = [
                ListItem(Static(_sighting_label(sighting, corp_map)), id=f"sighting_{i}")
                for i, sighting in enumerate(corp_state.sightings)
            ]
        else:
            sighting_items = [ListItem(Static("No sightings yet."), id="no_sightings")]
        await _replace_items(surveillance_list, sighting_items)


class GangTollScreen(ModalScreen):
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
