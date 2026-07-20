from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.corp_turn import (
    ACADEMY_TRAINING_COST,
    CorpState,
    EmployeeCategory,
    expand_into,
    expansion_cost,
    train_employees,
)
from shadowguy.corpmap import expansion_candidates
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID

from . import _replace_items


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
        info.update(
            f"{faction.name} — {corp_state.cash}eb — {corp_state.research_points}rp — "
            f"{corp_state.scientists} scientists — {corp_state.operatives} operatives — "
            f"Day {self.app.character.day}\n"
            f"Territories ({len(owned)}): {territory_names}"
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
            label = f"Train {category}s at the Academy — {ACADEMY_TRAINING_COST}eb"
            if corp_state.daily_action_used:
                label += " (already acted today)"
            elif ACADEMY_TRAINING_COST > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"train_{category}"))

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
                self.notify(f"Trained a new batch of {category}s at the Academy.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
