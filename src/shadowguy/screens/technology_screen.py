from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.corp_turn import TECHNOLOGIES, TECHNOLOGIES_BY_ID, has_technology, research_technology

from . import _replace_items


class TechnologyScreen(Screen):
    """Where a corp spends research points. Pulled out of CorpScreen's old
    inline Technology collapsible into its own pushed screen (like
    ContactsScreen/CorpMapScreen) so the tree has room to grow — today
    TECHNOLOGIES is a flat, prerequisite-free list (see corp_turn.py), so
    this renders as a plain researchable list rather than a literal graph."""

    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="tech_info")
        yield ListView(id="tech_list")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        corp_state = self.app.corp_state
        info = self.query_one("#tech_info", Static)
        info.update(f"Research points: {corp_state.research_points:g}rp")

        items = []
        for technology in TECHNOLOGIES:
            if has_technology(corp_state, technology.id):
                items.append(
                    ListItem(
                        Static(f"{technology.name} — researched\n  {technology.description}"),
                        id=f"tech_done_{technology.id}",
                    )
                )
                continue
            label = f"Research {technology.name} — {technology.cost}rp"
            if technology.cost > corp_state.research_points:
                short = technology.cost - corp_state.research_points
                label += f" (need {short:g}rp more)"
            items.append(
                ListItem(Static(f"{label}\n  {technology.description}"), id=f"tech_{technology.id}")
            )
        await _replace_items(self.query_one("#tech_list", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id.startswith("tech_done_"):
            return

        if item_id.startswith("tech_"):
            corp_state = self.app.corp_state
            technology_id = item_id.removeprefix("tech_")
            if research_technology(corp_state, technology_id):
                self.notify(f"Researched {TECHNOLOGIES_BY_ID[technology_id].name}.")
            else:
                self.notify("Not enough research points.", severity="warning")
            await self._refresh()
