from textual.app import ComposeResult
from textual.containers import Grid, Vertical
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import CORE_STATS, HOURS_PER_DAY, MAX_SKILL_RANK, Character
from shadowguy.corpmap import LocationKind
from shadowguy.factions import FACTIONS
from shadowguy.rivals import ACTIVITY_LABELS, RunnerActivity
from shadowguy.runners import RIVAL_RUNNERS, RivalRunner
from shadowguy.shops import (
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    PROGRAMS_BY_ID,
    active_deck_entry,
    bonus_text,
    free_program_slots,
    install_program,
    installed_programs_for,
    toggle_equip,
    uninstall_program,
    use_consumable,
)
from shadowguy.skills import SKILLS, skill_for

from . import (
    MENU_BACK_BINDINGS,
    PANEL_NAV_BINDINGS,
    BackScreen,
    CharacterSheet,
    PanelNav,
    _compact_skill_label,
    _populate_list,
    _replace_items,
)
from .shop_screens import FixerOffersScreen, offer_label

# Alarm Clock quick-set choices — every three hours around the clock, rather than a
# free-text hour field: nothing else in the UI takes typed input (every screen is
# ListView-driven), so this stays consistent instead of being the one exception.
ALARM_HOUR_CHOICES = tuple(range(0, HOURS_PER_DAY, 3))


class InventoryScreen(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="inventory_items")
        yield Footer()

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

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class CyberdeckScreen(BackScreen):
    """Deck + Program management, split out of InventoryScreen: a deck's
    installed_programs and which deck is Character.stat()'s and matrix.py's
    active one (shops.active_deck_entry -- the equipped deck with the best
    Intelligence bonus) are cyberdeck-specific concerns, not general gear.
    Equip/stow itself stays generic and lives on InventoryScreen too (a deck
    is still an Item there); a deck's equip toggle is repeated here only
    because it's what active_deck_entry actually reads."""

    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="cyberdeck_items")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        character = self.app.character
        active_entry = active_deck_entry(character.inventory)
        active_index = character.inventory.index(active_entry[0]) if active_entry else None

        items = []
        for index, entry in enumerate(character.inventory):
            item = ITEMS_BY_ID[entry.item_id]
            if item.program_slots <= 0:
                continue
            state = "Equipped" if entry.equipped else "Stowed"
            tag = " [active]" if index == active_index else ""
            installed = installed_programs_for(entry)
            names = ", ".join(p.name for p in installed) if installed else "none"
            used_ram = item.program_slots - free_program_slots(item, entry)
            items.append(
                ListItem(
                    Static(
                        f"{state} — {item.name}{tag} — programs: {names} "
                        f"({used_ram}/{item.program_slots} slots)"
                    ),
                    id=f"toggle_{index}",
                )
            )
            for program_id in entry.installed_programs:
                program = PROGRAMS_BY_ID[program_id]
                items.append(
                    ListItem(
                        Static(f"  Uninstall {program.name} from {item.name}"),
                        id=f"uninstall_{index}_{program_id}",
                    )
                )
            for program_id in sorted(character.owned_programs - set(entry.installed_programs)):
                program = PROGRAMS_BY_ID[program_id]
                items.append(
                    ListItem(
                        Static(f"  Install {program.name} on {item.name}"),
                        id=f"install_{index}_{program_id}",
                    )
                )

        if not items:
            items.append(ListItem(Static("No cyberdeck owned — visit a Computer Store."), id="no_deck"))

        await _replace_items(self.query_one("#cyberdeck_items", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "no_deck":
            return

        character = self.app.character

        if item_id.startswith("toggle_"):
            index = int(item_id.removeprefix("toggle_"))
            item = ITEMS_BY_ID[character.inventory[index].item_id]
            if not toggle_equip(character, index):
                self.notify(f"No free {item.slot.value} slot.", severity="warning")
        elif item_id.startswith("install_"):
            index_str, program_id = item_id.removeprefix("install_").split("_", 1)
            self.notify(install_program(character, int(index_str), program_id))
        elif item_id.startswith("uninstall_"):
            index_str, program_id = item_id.removeprefix("uninstall_").split("_", 1)
            self.notify(uninstall_program(character, int(index_str), program_id))

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class PhoneScreen(PanelNav, BackScreen):
    """The runner's handheld — Contacts is one tab among the phone's other apps:
    Web (a browser: each megacorp's own site listed like an app shortcut above a
    Search section, which is the cross-fixer job board — accept from anywhere
    established trust reaches), Alarm Clock (sets Character.alarm_hour, read by
    app.rest() to cut a Rest short), and Messages (a read-only recap built entirely
    from data the other tabs already show — fixer boards, rival-runner activity —
    not a new inbox)."""

    PANEL_IDS = (
        "fixers_list",
        "corps_list",
        "locals_list",
        "runners_list",
        "web_list",
        "alarm_list",
        "messages_list",
    )
    BINDINGS = [*MENU_BACK_BINDINGS, *PANEL_NAV_BINDINGS]

    CSS = """
    #phone_frame {
        border: round $accent;
        padding: 0 1;
    }

    #phone_banner {
        text-align: center;
        color: $accent;
    }

    #fixers_panel, #corps_panel, #locals_panel, #runners_panel,
    #web_panel, #alarm_panel, #messages_panel {
        height: auto;
    }

    #fixers_list, #corps_list, #locals_list, #runners_list,
    #web_list, #alarm_list, #messages_list {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static("── SG-Phone ──", id="phone_banner"),
            Collapsible(
                ListView(id="fixers_list"), title="Contacts — Fixers", collapsed=False, id="fixers_panel"
            ),
            Collapsible(
                ListView(id="corps_list"), title="Contacts — Corps", collapsed=False, id="corps_panel"
            ),
            Collapsible(
                ListView(id="locals_list"), title="Contacts — Locals", collapsed=False, id="locals_panel"
            ),
            Collapsible(
                ListView(id="runners_list"), title="Contacts — Runners", collapsed=False, id="runners_panel"
            ),
            Collapsible(ListView(id="web_list"), title="Web", collapsed=False, id="web_panel"),
            Collapsible(ListView(id="alarm_list"), title="Alarm Clock", collapsed=False, id="alarm_panel"),
            Collapsible(ListView(id="messages_list"), title="Messages", collapsed=False, id="messages_panel"),
            id="phone_frame",
        )
        yield Footer()

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
                f"(trust {character.trust_with(fixer.id):+d}, {len(fixer.open_offers)} jobs, "
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
                + (" (on your crew)" if character.on_crew(runner.id) else f" — {self._status(runner)}")
                + f": {runner.description}"
            ),
        )

        # A browser home screen: each megacorp's site listed like an app shortcut,
        # above a Search section that's the actual cross-fixer job board.
        web_items = [
            ListItem(Static(f"{faction.name} — official site"), id=f"webapp_{faction.id}")
            for faction in FACTIONS
        ]
        web_items.append(ListItem(Static("── Search ──"), id="web_search_header"))
        web_offers = [(fixer, offer) for fixer in established for offer in fixer.open_offers]
        web_items += (
            [
                ListItem(Static(f"{fixer.name} — {offer_label(character, offer)}"), id=f"weboffer_{offer.id}")
                for fixer, offer in web_offers
            ]
            if web_offers
            else [ListItem(Static("No work posted online."), id="no_web_offers")]
        )
        await _replace_items(self.query_one("#web_list", ListView), web_items)

        status = f"Alarm: {character.alarm_hour:02d}:00" if character.alarm_hour is not None else "Alarm: off"
        alarm_items = [ListItem(Static(status), id="alarm_status")]
        alarm_items += [
            ListItem(
                Static(f"Set {hour:02d}:00" + (" (current — tap to clear)" if character.alarm_hour == hour else "")),
                id=f"alarm_{hour}",
            )
            for hour in ALARM_HOUR_CHOICES
        ]
        await _replace_items(self.query_one("#alarm_list", ListView), alarm_items)

        lines = self._messages(character)
        message_items = (
            [ListItem(Static(line), id=f"message_{i}") for i, line in enumerate(lines)]
            if lines
            else [ListItem(Static("No new messages."), id="no_messages")]
        )
        await _replace_items(self.query_one("#messages_list", ListView), message_items)

    def _status(self, runner: RivalRunner) -> str:
        """What an independent runner was last seen doing (rivals.py). A runner
        with no state yet hasn't had a day tick since the run started."""
        state = self.app.rival_runner_states.get(runner.id)
        if state is None:
            return "whereabouts unknown"
        territory = self.app.corp_map.territories[state.territory_id]
        if state.activity is RunnerActivity.WORKING and state.job_title:
            return f"{territory.name}, running {state.job_title}"
        if state.activity is RunnerActivity.DRINKING:
            # rivals.py only picks DRINKING in a territory that has a bar, so
            # naming it here is safe — and much better flavor than "drinking".
            bar = next(loc for loc in territory.locations if loc.kind is LocationKind.BAR)
            return f"{territory.name}, drinking at {bar.name}"
        return f"{territory.name}, {ACTIVITY_LABELS[state.activity]}"

    def _messages(self, character: Character) -> list[str]:
        """A read-only recap of what Contacts/Web already show, reframed as texts —
        no state of its own, so there's nothing here to persist or expire."""
        lines = []
        for fixer in self.app.fixers:
            if character.trust_with(fixer.id) <= 0:
                continue
            if fixer.open_offers:
                lines.append(f"{fixer.name}: got {len(fixer.open_offers)} job(s) if you're interested.")
            if fixer.security_offers:
                lines.append(f"{fixer.name}: security work up for grabs too, if that's more your speed.")
        for runner in RIVAL_RUNNERS:
            state = self.app.rival_runner_states.get(runner.id)
            if state is not None and state.activity is RunnerActivity.WORKING and state.job_title:
                lines.append(f"{runner.name}: heads up, I'm out running {state.job_title} today.")
        return lines

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        character = self.app.character

        if item_id.startswith("alarm_") and item_id != "alarm_status":
            hour = int(item_id.removeprefix("alarm_"))
            character.alarm_hour = None if character.alarm_hour == hour else hour
            await self._refresh()
            return

        if item_id.startswith("webapp_"):
            faction_id = item_id.removeprefix("webapp_")
            faction = next(f for f in FACTIONS if f.id == faction_id)
            self.notify(f"{faction.name}.net — {faction.specialty.value}.")
            return

        if item_id.startswith("weboffer_"):
            offer_id = item_id.removeprefix("weboffer_")
            fixer = next((f for f in self.app.fixers if any(o.id == offer_id for o in f.offers)), None)
            if fixer is None:
                return
            offer = next(o for o in fixer.offers if o.id == offer_id)
            if offer.taken_by is not None:
                self.notify("Someone else already took that one.", severity="warning")
                return
            character.accept_job(offer)
            fixer.offers = [o for o in fixer.offers if o.id != offer.id]
            await self._refresh()
            return

        if item_id.startswith("fixer_"):
            fixer_id = item_id.removeprefix("fixer_")
            fixer = next((fixer for fixer in self.app.fixers if fixer.id == fixer_id), None)
            if fixer is not None:
                self.app.push_screen(FixerOffersScreen(fixer))


class SkillsScreen(PanelNav, BackScreen):
    """Read-only for skill *values* (gear bonuses included), but spendable for
    Character.experience: a "Raise <Stat>" row atop each column plus every skill
    row, both showing next-purchase cost the same way CharacterCreationScreen's
    build columns do, funded by spend_experience_on_stat/spend_experience_on_skill
    instead of the one-shot creation pools."""

    PANEL_IDS = tuple(f"skill_list_{stat}" for stat in CORE_STATS)
    BINDINGS = [*MENU_BACK_BINDINGS, *PANEL_NAV_BINDINGS]

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
                    Static(id=f"skill_head_{stat}"),
                    ListView(id=f"skill_list_{stat}"),
                    classes="skill_column",
                )
                for stat in CORE_STATS
            ),
            id="skills_grid",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self, stat: str | None = None, index: int = 0) -> None:
        character = self.app.character
        for s in CORE_STATS if stat is None else (stat,):
            self.query_one(f"#skill_head_{s}", Static).update(f"{s.capitalize()} — {character.stat(s)}")
            cost = character.next_stat_cost(s)
            items = [ListItem(Static(f"Raise {s.capitalize()}\n  {cost}xp"), id=f"stat_{s}")]
            items += [
                ListItem(Static(_compact_skill_label(character, skill, show_cost=True)), id=f"skill_{skill.id}")
                for skill in SKILLS
                if skill.stat == s
            ]
            await _replace_items(self.query_one(f"#skill_list_{s}", ListView), items, index)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        character = self.app.character
        index = event.list_view.index or 0

        if item_id.startswith("stat_"):
            stat = item_id.removeprefix("stat_")
            if not character.spend_experience_on_stat(stat):
                self.notify(
                    f"Raising {stat.capitalize()} costs {character.next_stat_cost(stat)}xp; "
                    f"you have {character.experience}.",
                    severity="warning",
                )
        elif item_id.startswith("skill_"):
            skill_id = item_id.removeprefix("skill_")
            stat = skill_for(skill_id).stat
            name = skill_for(skill_id).name
            cost = character.next_rank_cost(skill_id)
            if cost is None:
                self.notify(f"{name} is already at rank {MAX_SKILL_RANK}.", severity="warning")
                return
            if not character.spend_experience_on_skill(skill_id):
                self.notify(f"{name}'s next rank costs {cost}xp; you have {character.experience}.", severity="warning")
        else:
            return

        self.query_one(CharacterSheet).refresh()
        await self._refresh(stat, index)
