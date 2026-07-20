from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.screen import Screen
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import CORE_STATS
from shadowguy.factions import FACTIONS
from shadowguy.runners import RIVAL_RUNNERS
from shadowguy.shops import (
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    PROGRAMS_BY_ID,
    bonus_text,
    free_program_slots,
    install_program,
    installed_programs_for,
    toggle_equip,
    uninstall_program,
    use_consumable,
)
from shadowguy.skills import SKILLS

from . import (
    PANEL_NAV_BINDINGS,
    CharacterSheet,
    PanelNav,
    _compact_skill_label,
    _populate_list,
    _replace_items,
)
from .shop_screens import FixerOffersScreen


class InventoryScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="inventory_items")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        items = []
        for index, entry in enumerate(self.app.character.inventory):
            item = ITEMS_BY_ID[entry.item_id]
            state = "Equipped" if entry.equipped else "Stowed"
            parts = [p for p in (bonus_text(item), item.slot.value if item.slot else None) if p]
            label = f"{state} — {item.name}" + (f" ({', '.join(parts)})" if parts else "")
            items.append(ListItem(Static(label), id=f"toggle_{index}"))

        for index, item_id in enumerate(self.app.character.consumables):
            consumable = CONSUMABLES_BY_ID[item_id]
            items.append(ListItem(Static(f"Use {consumable.name}"), id=f"use_{index}"))

        character = self.app.character
        for index, entry in enumerate(character.inventory):
            item = ITEMS_BY_ID[entry.item_id]
            if item.program_slots <= 0:
                continue
            installed = installed_programs_for(entry)
            names = ", ".join(p.name for p in installed) if installed else "none"
            used_ram = item.program_slots - free_program_slots(item, entry)
            items.append(
                ListItem(
                    Static(f"{item.name} — programs: {names} ({used_ram}/{item.program_slots} slots)"),
                    id=f"deck_info_{index}",
                )
            )
            for program_id in entry.installed_programs:
                program = PROGRAMS_BY_ID[program_id]
                items.append(
                    ListItem(Static(f"  Uninstall {program.name} from {item.name}"), id=f"uninstall_{index}_{program_id}")
                )
            for program_id in sorted(character.owned_programs - set(entry.installed_programs)):
                program = PROGRAMS_BY_ID[program_id]
                items.append(
                    ListItem(Static(f"  Install {program.name} on {item.name}"), id=f"install_{index}_{program_id}")
                )

        await _replace_items(self.query_one("#inventory_items", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        item_id = event.item.id

        if item_id.startswith("toggle_"):
            index = int(item_id.removeprefix("toggle_"))
            item = ITEMS_BY_ID[character.inventory[index].item_id]
            if not toggle_equip(character, index):
                self.notify(f"No free {item.slot.value} slot.", severity="warning")
        elif item_id.startswith("use_"):
            index = int(item_id.removeprefix("use_"))
            self.notify(use_consumable(character, index))
        elif item_id.startswith("install_"):
            index_str, program_id = item_id.removeprefix("install_").split("_", 1)
            self.notify(install_program(character, int(index_str), program_id))
        elif item_id.startswith("uninstall_"):
            index_str, program_id = item_id.removeprefix("uninstall_").split("_", 1)
            self.notify(uninstall_program(character, int(index_str), program_id))

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class ContactsScreen(PanelNav, Screen):
    PANEL_IDS = ("fixers_list", "corps_list", "locals_list", "runners_list")
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back"), *PANEL_NAV_BINDINGS]

    CSS = """
    #fixers_panel, #corps_panel, #locals_panel, #runners_panel {
        height: auto;
    }

    #fixers_list, #corps_list, #locals_list, #runners_list {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Collapsible(
            ListView(id="fixers_list"), title="Fixers", collapsed=False, id="fixers_panel"
        )
        yield Collapsible(
            ListView(id="corps_list"), title="Corps", collapsed=False, id="corps_panel"
        )
        yield Collapsible(
            ListView(id="locals_list"), title="Locals", collapsed=False, id="locals_panel"
        )
        yield Collapsible(
            ListView(id="runners_list"), title="Runners", collapsed=False, id="runners_panel"
        )
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        character = self.app.character

        established = [fixer for fixer in self.app.fixers if character.trust_with(fixer.id) > 0]
        await _populate_list(
            self.query_one("#fixers_list", ListView),
            established,
            id_prefix="fixer_",
            label=lambda fixer: (
                f"{fixer.name} — {fixer.specialty} "
                f"(trust {character.trust_with(fixer.id):+d}, {len(fixer.offers)} jobs, "
                f"{len(fixer.security_offers)} security available)"
            ),
            empty_label="No established contacts yet.",
            empty_id="no_fixers",
        )
        await _populate_list(
            self.query_one("#corps_list", ListView),
            FACTIONS,
            id_prefix="faction_",
            label=lambda faction: (
                f"{faction.name} — {faction.specialty.value} "
                f"(standing {character.standing_with(faction.id):+d})"
            ),
        )

        map_characters = self.app.corp_map.characters()
        loc_by_char = {char.id: loc for loc, char in map_characters}
        known_locals = [
            char for _loc, char in map_characters if character.local_standing_with(char.id) != 0
        ]
        await _populate_list(
            self.query_one("#locals_list", ListView),
            known_locals,
            id_prefix="local_",
            label=lambda char: (
                f"{char.name} ({char.role}) — {loc_by_char[char.id].name} "
                f"(standing {character.local_standing_with(char.id):+d})"
            ),
            empty_label="No locals know you yet.",
            empty_id="no_locals",
        )
        await _populate_list(
            self.query_one("#runners_list", ListView),
            RIVAL_RUNNERS,
            id_prefix="runner_",
            label=lambda runner: (
                f"{runner.name} — {runner.archetype}"
                + (" (on your crew)" if character.on_crew(runner.id) else "")
                + f": {runner.description}"
            ),
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if not event.item.id.startswith("fixer_"):
            return
        fixer_id = event.item.id.removeprefix("fixer_")
        fixer = next((fixer for fixer in self.app.fixers if fixer.id == fixer_id), None)
        if fixer is not None:
            self.app.push_screen(FixerOffersScreen(fixer))


class SkillsScreen(PanelNav, Screen):
    PANEL_IDS = tuple(f"skill_list_{stat}" for stat in CORE_STATS)
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back"), *PANEL_NAV_BINDINGS]

    CSS = """
    #skills_grid {
        grid-size: 3 2;
        grid-gutter: 1 2;
    }

    .skill_column {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    .skill_column ListView {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Grid(
            *(
                Vertical(
                    Static(stat.capitalize()),
                    ListView(id=f"skill_list_{stat}"),
                    classes="skill_column",
                )
                for stat in CORE_STATS
            ),
            id="skills_grid",
        )
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        character = self.app.character
        for stat in CORE_STATS:
            items = [
                ListItem(Static(_compact_skill_label(character, skill)), id=f"skill_{skill.id}")
                for skill in SKILLS
                if skill.stat == stat
            ]
            await _replace_items(self.query_one(f"#skill_list_{stat}", ListView), items)
