from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.corp_turn import (
    ACADEMY_TRAINING_COST,
    DEVELOPMENT_BUMP_COST,
    SURVEILLANCE_BUMP_COST,
    TECHNOLOGIES,
    TECHNOLOGIES_BY_ID,
    CorpState,
    EmployeeCategory,
    assistant_capacity,
    assistant_rate,
    build_efficiency_upgrade,
    build_lab,
    development_targets,
    expand_into,
    expansion_cost,
    has_technology,
    lab_capacity,
    next_efficiency_cost,
    next_lab_cost,
    owned_research_facility,
    raise_development,
    raise_surveillance,
    research_rate,
    research_technology,
    surveillance_targets,
    train_employees,
)
from shadowguy.corpmap import TerritoryModifier, expansion_candidates
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.runners import RUNNERS_BY_ID

from . import PANEL_NAV_BINDINGS, PanelNav, _replace_items
from .corp_map_screen import CorpMapScreen
from .info_screens import ContactsScreen


def _plural(category: EmployeeCategory) -> str:
    """research_assistant -> "research assistants"; scientist/operative have
    no underscore to begin with, so this just adds the s."""
    return f"{category.replace('_', ' ')}s"


def _sighting_label(sighting, corp_map) -> str:
    who = "You" if sighting.kind == "player" else RUNNERS_BY_ID[sighting.actor_id].name
    territory_name = corp_map.territories[sighting.territory_id].name
    return f"Day {sighting.day} — {who} spotted in {territory_name}"


class CorpScreen(Screen):
    """Play as a corp instead of the runner: pick one of the 3 seeded Factions
    to run (a plain menu choice for now — there's no in-fiction takeover yet,
    see corp_turn.py), then spend one directed move a day on either the same
    neutral-ground expansion rivals.py's AI factions make, or training up
    employees at the corp's Academy.

    Actions are grouped by the thing they're attached to, not left in one flat
    list: territory expansion + end-day stay in #corp_list, Academy training
    goes in the #academy_list collapsible, and Research Facility upgrades go in
    the #research_list collapsible — both always present once a corp is picked,
    since every faction's territory carries one guaranteed Academy and one
    guaranteed Research Facility from the start (corp_turn.py)."""

    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    # ListView defaults to height: 1fr, which -- with three of them stacked as
    # siblings (corp_list plus the two Collapsible-wrapped ones) -- squashes each
    # to a sliver and lets the Collapsibles overlap on top of it. height: auto
    # (the same fix MainMenu applies to its own Collapsible-wrapped lists) sizes
    # each to its actual item count instead.
    CSS = """
    #corp_list, #academy_list, #research_list, #tech_list, #surveillance_list {
        height: auto;
    }

    #academy_panel, #research_panel, #tech_panel, #surveillance_panel {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="corp_info")
        yield ListView(id="corp_list")
        yield Collapsible(ListView(id="academy_list"), title="Academy", collapsed=False, id="academy_panel")
        yield Collapsible(
            ListView(id="research_list"), title="Research Facility", collapsed=False, id="research_panel"
        )
        yield Collapsible(ListView(id="tech_list"), title="Technology", collapsed=False, id="tech_panel")
        yield Collapsible(
            ListView(id="surveillance_list"), title="Surveillance Log", collapsed=True, id="surveillance_panel"
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _refresh(self) -> None:
        corp_state = self.app.corp_state
        info = self.query_one("#corp_info", Static)
        list_view = self.query_one("#corp_list", ListView)
        academy_list = self.query_one("#academy_list", ListView)
        research_list = self.query_one("#research_list", ListView)

        if corp_state is None:
            info.update("Pick a corp to run.")
            items = [
                ListItem(Static(f"{faction.name} ({faction.specialty})"), id=f"faction_{faction.id}")
                for faction in FACTIONS
            ]
            await _replace_items(list_view, items)
            await _replace_items(academy_list, [])
            await _replace_items(research_list, [])
            await _replace_items(self.query_one("#tech_list", ListView), [])
            await _replace_items(self.query_one("#surveillance_list", ListView), [])
            self.query_one("#academy_panel").display = False
            self.query_one("#research_panel").display = False
            self.query_one("#tech_panel").display = False
            self.query_one("#surveillance_panel").display = False
            return

        self.query_one("#academy_panel").display = True
        self.query_one("#research_panel").display = True
        self.query_one("#tech_panel").display = True
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

        # The two modifier bumps are cash-gated and repeatable, so they never
        # carry the "already acted today" note the expansion rows above do.
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

        items.append(ListItem(Static("Rest"), id="rest"))
        await _replace_items(list_view, items)

        tech_items = []
        for technology in TECHNOLOGIES:
            if has_technology(corp_state, technology.id):
                tech_items.append(
                    ListItem(Static(f"{technology.name} — researched"), id=f"tech_done_{technology.id}")
                )
                continue
            label = f"Research {technology.name} — {technology.cost}rp"
            if technology.cost > corp_state.research_points:
                short = technology.cost - corp_state.research_points
                label += f" (need {short:g}rp more)"
            tech_items.append(ListItem(Static(label), id=f"tech_{technology.id}"))
        await _replace_items(self.query_one("#tech_list", ListView), tech_items)

        academy_items = []
        for category in EmployeeCategory:
            label = f"Train {_plural(category)} — {ACADEMY_TRAINING_COST}eb"
            if corp_state.daily_action_used:
                label += " (already acted today)"
            elif ACADEMY_TRAINING_COST > corp_state.cash:
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
        await _replace_items(self.query_one("#surveillance_list", ListView), sighting_items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id.startswith("faction_"):
            faction_id = item_id.removeprefix("faction_")
            self.app.corp_state = CorpState(faction_id=faction_id)
            self.notify(f"You're now running {FACTIONS_BY_ID[faction_id].name}.")
            await self._refresh()
            return

        if item_id == "rest":
            self.app.rest()
            await self._refresh()
            return

        if item_id.startswith("expand_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("expand_")
            territory = self.app.corp_map.territories[territory_id]
            if expand_into(corp_state, self.app.corp_map, territory_id, self.app.rng):
                self.notify(f"Claimed {territory.name}.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("tech_done_") or item_id.startswith("sighting_") or item_id == "no_sightings":
            return

        if item_id.startswith("tech_"):
            corp_state = self.app.corp_state
            technology_id = item_id.removeprefix("tech_")
            if research_technology(corp_state, technology_id):
                self.notify(f"Researched {TECHNOLOGIES_BY_ID[technology_id].name}.")
            else:
                self.notify("Not enough research points.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("surveil_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("surveil_")
            territory = self.app.corp_map.territories[territory_id]
            if raise_surveillance(corp_state, self.app.corp_map, territory_id):
                self.notify(f"Surveillance raised in {territory.name}.")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("develop_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("develop_")
            territory = self.app.corp_map.territories[territory_id]
            if raise_development(corp_state, self.app.corp_map, territory_id):
                self.notify(f"{territory.name} builds up. Development raised.")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("train_"):
            corp_state = self.app.corp_state
            category = EmployeeCategory(item_id.removeprefix("train_"))
            if train_employees(corp_state, self.app.corp_map, category):
                self.notify(f"Trained a new batch of {_plural(category)} at the Academy.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id == "build_lab":
            corp_state = self.app.corp_state
            if build_lab(corp_state, self.app.corp_map):
                self.notify("Built a new lab at the Research Facility.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id == "build_efficiency":
            corp_state = self.app.corp_state
            if build_efficiency_upgrade(corp_state, self.app.corp_map):
                self.notify("Upgraded the Research Facility's efficiency.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()


class CorpMainMenu(PanelNav, CorpScreen):
    """Home screen for a game started fresh as a Corp (New Game -> Corp): there's no
    runner in this kind of game, so none of the runner activities (gigs, jobs,
    legwork) apply. Laid out like MainMenu -- a left-hand category sidebar next to
    the main panel -- rather than dropping the player straight into the corp's
    action list. "Corp" renders inline (CorpScreen's own info/action list, grouped
    into Academy/Research Facility collapsibles, inherited unchanged); "Corp Map"/
    "Contacts" push their own screens, same as MainMenu's equivalent categories."""

    # CorpScreen's escape->back is redeclared here (not just omitted) with show=False:
    # Textual merges BINDINGS across the class hierarchy, so leaving it out would still
    # leave the inherited binding live -- this is the top-level screen for a pure-corp
    # game, with nothing below it worth popping back to (see action_back's override).
    BINDINGS = [
        ("q", "quit_menu", "Menu"),
        ("m", "corp_map", "Corp Map"),
        ("c", "contacts", "Contacts"),
        Binding("escape", "back", "Back", show=False),
        *PANEL_NAV_BINDINGS,
    ]
    PANEL_IDS = ("categories", "corp_list", "academy_list", "research_list", "tech_list", "surveillance_list")

    CATEGORIES = [("corp", "Corp"), ("map", "Corp Map"), ("contacts", "Contacts")]

    # The #corp_list/#academy_list/#research_list/#academy_panel/#research_panel rules
    # are already on CorpScreen.CSS, but Textual's cross-hierarchy CSS merge silently
    # drops them once PanelNav sits in the MRO (a confirmed Textual quirk: a plain
    # mixin between a subclass and its CSS-defining base breaks the ID-selector scoping,
    # leaving ListView's own default height: 1fr in effect instead). Re-declaring the
    # same rules directly here routes around it -- without this, academy_panel/
    # research_panel each claim a tall fixed box regardless of content and overlap
    # corp_list and each other instead of stacking top to bottom.
    CSS = """
    #corp_stats_panel {
        height: auto;
        border: solid $accent;
        padding: 0 1;
    }

    #sidebar {
        width: 20;
        border: solid $accent;
        padding: 1;
    }

    #main_panel {
        width: 1fr;
        border: solid $accent;
        padding: 0 1;
    }

    #corp_list, #academy_list, #research_list, #tech_list, #surveillance_list {
        height: auto;
    }

    #academy_panel, #research_panel, #tech_panel, #surveillance_panel {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(Static(id="corp_info"), id="corp_stats_panel")
        yield Horizontal(
            Vertical(ListView(id="categories"), id="sidebar"),
            Vertical(
                ListView(id="corp_list"),
                Collapsible(
                    ListView(id="academy_list"), title="Academy", collapsed=False, id="academy_panel"
                ),
                Collapsible(
                    ListView(id="research_list"),
                    title="Research Facility",
                    collapsed=False,
                    id="research_panel",
                ),
                Collapsible(
                    ListView(id="tech_list"), title="Technology", collapsed=False, id="tech_panel"
                ),
                Collapsible(
                    ListView(id="surveillance_list"),
                    title="Surveillance Log",
                    collapsed=True,
                    id="surveillance_panel",
                ),
                id="main_panel",
            ),
        )
        yield Footer()

    async def on_mount(self) -> None:
        items = [ListItem(Static(label), id=f"cat_{key}") for key, label in self.CATEGORIES]
        await _replace_items(self.query_one("#categories", ListView), items)
        await super().on_mount()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    def action_back(self) -> None:
        # No-op override of CorpScreen.action_back: escape is rebound above with
        # show=False rather than removed, so it still resolves to this action.
        pass

    def action_corp_map(self) -> None:
        self.app.push_screen(CorpMapScreen())

    def action_contacts(self) -> None:
        self.app.push_screen(ContactsScreen())

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "categories":
            key = event.item.id.removeprefix("cat_")
            if key == "map":
                self.app.push_screen(CorpMapScreen())
            elif key == "contacts":
                self.app.push_screen(ContactsScreen())
            return
        await super().on_list_view_selected(event)
