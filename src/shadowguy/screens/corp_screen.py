from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.corp_turn import (
    ACADEMY_TRAINING_COST,
    RESEARCH_PER_ASSISTANT,
    CorpState,
    EmployeeCategory,
    assistant_capacity,
    build_efficiency_upgrade,
    build_lab,
    expand_into,
    expansion_cost,
    lab_capacity,
    next_efficiency_cost,
    next_lab_cost,
    owned_research_facility,
    research_rate,
    train_employees,
)
from shadowguy.corpmap import expansion_candidates
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID

from . import PANEL_NAV_BINDINGS, PanelNav, _replace_items
from .corp_map_screen import CorpMapScreen
from .info_screens import ContactsScreen


def _plural(category: EmployeeCategory) -> str:
    """research_assistant -> "research assistants"; scientist/operative have
    no underscore to begin with, so this just adds the s."""
    return f"{category.replace('_', ' ')}s"


class CorpScreen(Screen):
    """Play as a corp instead of the runner: pick one of the 3 seeded Factions
    to run (a plain menu choice for now — there's no in-fiction takeover yet,
    see corp_turn.py), then spend one directed move a day on either the same
    neutral-ground expansion rivals.py's AI factions make, or training up
    employees at the corp's Academy."""

    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="corp_info")
        yield ListView(id="corp_list")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def _refresh(self) -> None:
        corp_state = self.app.corp_state
        info = self.query_one("#corp_info", Static)
        list_view = self.query_one("#corp_list", ListView)

        if corp_state is None:
            info.update("Pick a corp to run.")
            items = [
                ListItem(Static(f"{faction.name} ({faction.specialty})"), id=f"faction_{faction.id}")
                for faction in FACTIONS
            ]
            await _replace_items(list_view, items)
            return

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
                f"{working}/{capacity} scientists at work ({research_rate(facility)}rp/scientist), "
                f"{working_assistants}/{assist_capacity} assistants at work ({RESEARCH_PER_ASSISTANT}rp/assistant)"
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

        for category in EmployeeCategory:
            label = f"Train {_plural(category)} at the Academy — {ACADEMY_TRAINING_COST}eb"
            if corp_state.daily_action_used:
                label += " (already acted today)"
            elif ACADEMY_TRAINING_COST > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"train_{category}"))

        if facility is not None:
            cost = next_lab_cost(facility)
            if cost is None:
                items.append(ListItem(Static("Research Facility labs fully upgraded"), id="labs_maxed"))
            else:
                label = f"Build a lab at the Research Facility — {cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif cost > corp_state.cash:
                    label += " (can't afford)"
                items.append(ListItem(Static(label), id="build_lab"))

            efficiency_cost = next_efficiency_cost(facility)
            if efficiency_cost is None:
                items.append(
                    ListItem(Static("Research Facility efficiency fully upgraded"), id="efficiency_maxed")
                )
            else:
                label = f"Upgrade Research Facility efficiency — {efficiency_cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif efficiency_cost > corp_state.cash:
                    label += " (can't afford)"
                items.append(ListItem(Static(label), id="build_efficiency"))

        items.append(ListItem(Static("End the day"), id="end_day"))
        await _replace_items(list_view, items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id.startswith("faction_"):
            faction_id = item_id.removeprefix("faction_")
            self.app.corp_state = CorpState(faction_id=faction_id)
            self.notify(f"You're now running {FACTIONS_BY_ID[faction_id].name}.")
            await self._refresh()
            return

        if item_id == "end_day":
            self.app.end_day()
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
    action list. "Corp" renders inline (CorpScreen's own info/action list, inherited
    unchanged); "Corp Map"/"Contacts" push their own screens, same as MainMenu's
    equivalent categories."""

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
    PANEL_IDS = ("categories", "corp_list")

    CATEGORIES = [("corp", "Corp"), ("map", "Corp Map"), ("contacts", "Contacts")]

    CSS = """
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
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Horizontal(
            Vertical(ListView(id="categories"), id="sidebar"),
            Vertical(Static(id="corp_info"), ListView(id="corp_list"), id="main_panel"),
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
