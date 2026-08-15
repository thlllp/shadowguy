from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Grid, ScrollableContainer, Vertical
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import CORE_STATS, HOURS_PER_DAY, MAX_SKILL_RANK, Character
from shadowguy.corp_turn import TECHNOLOGIES_BY_ID, FactionEvent
from shadowguy.corpmap import LocationKind
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID, Faction
from shadowguy.inventory import (
    active_deck_entry,
    effective_ram_max,
    free_passive_slots,
    free_program_slots,
    free_ram,
    install_program,
    installed_programs_for,
    reload_weapon,
    rounds_needed,
    toggle_equip,
    uninstall_program,
    use_consumable,
)
from shadowguy.rivals import ACTIVITY_LABELS, RunnerActivity
from shadowguy.runners import RivalRunner
from shadowguy.shops import (
    APP_STORE_CATALOG,
    AmmoKind,
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    PROGRAMS_BY_ID,
    Program,
    bonus_text,
    buy_app,
    effective_item,
    loaded_rounds,
    owned_app_bonus,
)
from shadowguy.skills import SKILLS, skill_for

from . import (
    MENU_BACK_BINDINGS,
    PANEL_NAV_BINDINGS,
    BackScreen,
    CharacterSheet,
    RefreshOnResume,
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


class EquipToggleMixin:
    """The `toggle_<index>` row both gear screens carry: a deck is an ordinary Item on
    InventoryScreen and the thing active_deck_entry reads on CyberdeckScreen, so both
    list it and both have to be able to equip it."""

    def _toggle_equip(self, index: int) -> None:
        character = self.app.character
        item = ITEMS_BY_ID[character.inventory[index].item_id]
        if not toggle_equip(character, index):
            self.notify(f"No free {item.slot.value} slot.", severity="warning")


class InventoryScreen(EquipToggleMixin, RefreshOnResume, BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="inventory_items")
        yield Footer()

    async def _refresh(self) -> None:
        items = []
        for index, entry in enumerate(self.app.character.inventory):
            item = effective_item(entry)
            state = "Equipped" if entry.equipped else "Stowed"
            parts = [p for p in (bonus_text(item), item.slot.value if item.slot else None) if p]
            if item.ammo is not None:
                parts.append(f"{loaded_rounds(self.app.character, item)}/{item.magazine} loaded")
            label = f"{state} — {item.name}" + (f" ({', '.join(parts)})" if parts else "")
            items.append(ListItem(Static(label), id=f"toggle_{index}"))
            # Free out of combat, and offered only when it would actually do something:
            # room in the magazine *and* rounds in the reserve to put there. The in-fight
            # reload is the one that costs a round.
            reserve = item.ammo and self.app.character.ammo.get(item.ammo.value, 0)
            if rounds_needed(self.app.character, item) and reserve:
                label = f"Reload {item.name} — {reserve} {item.ammo.label} held"
                items.append(ListItem(Static(label), id=f"reload_{index}"))

        for kind_value, count in sorted(self.app.character.ammo.items()):
            if count:
                label = f"{count} {AmmoKind(kind_value).label}"
                items.append(ListItem(Static(label), id=f"ammo_{kind_value}"))

        for index, item_id in enumerate(self.app.character.consumables):
            consumable = CONSUMABLES_BY_ID[item_id]
            items.append(ListItem(Static(f"Use {consumable.name}"), id=f"use_{index}"))

        await _replace_items(self.query_one("#inventory_items", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        item_id = event.item.id

        if item_id.startswith("toggle_"):
            self._toggle_equip(int(item_id.removeprefix("toggle_")))
        elif item_id.startswith("reload_"):
            self.notify(reload_weapon(character, int(item_id.removeprefix("reload_"))))
        elif item_id.startswith("use_"):
            index = int(item_id.removeprefix("use_"))
            self.notify(use_consumable(character, index))

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class CyberdeckScreen(EquipToggleMixin, RefreshOnResume, BackScreen):
    """Deck + Program management with visual slot display."""

    BINDINGS = MENU_BACK_BINDINGS

    CSS = """
    #deck_title {
        text-style: bold;
        color: $accent;
        margin: 1 0 0 0;
    }

    #deck_slot_display {
        margin: 0 0 1 0;
        min-height: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static("C Y B E R D E C K", id="deck_title")
        yield Static(id="deck_slot_display")
        yield ListView(id="cyberdeck_items")
        yield Footer()

    def _slot_display(self, character: Character) -> Text:
        active = active_deck_entry(character.inventory)
        result = Text()

        if not active:
            result.append("\n")
            result.append("  ░▒▓ NO DECK ACTIVE ▓▒░\n", style="bold dim")
            result.append("  No cyberdeck equipped.\n", style="dim")
            result.append("  Equip one below or visit a Computer Store.\n", style="dim")
            return result

        entry, item = active
        # Two pools, drawn as one row of boxes: program_slots for action programs,
        # then item.passive_slots for passive ones. installed_programs is a single
        # flat list, so split it by Program.is_passive rather than by position --
        # indexing it positionally would put a passive in an action box (and drop
        # whatever overflowed past program_slots off the display entirely).
        action_progs = [p for p in installed_programs_for(entry) if not p.is_passive]
        passive_progs = [p for p in installed_programs_for(entry) if p.is_passive]
        total = item.program_slots
        passive_total = item.passive_slots
        logic = item.bonuses.get("logic", 0)
        free_slots = free_program_slots(item, entry)
        free_passive = free_passive_slots(item, entry)
        ram_max = effective_ram_max(character, item)
        ram_free = free_ram(character, item, entry)

        result.append("  ")
        result.append(item.name, style="bold cyan")
        result.append(f"  \u2500\u2500  Logic +{logic}", style="dim")
        result.append(f"  \u2500\u2500  {free_slots}/{total} slots free", style="dim")
        result.append(f"  \u2500\u2500  {free_passive}/{passive_total} passive", style="dim")
        result.append(f"  \u2500\u2500  {ram_free}/{ram_max} RAM free\n\n", style="dim")

        SLOT_WIDTH = 26

        slots: list[tuple[str, Program | None, int]] = []
        for i in range(total):
            prog = action_progs[i] if i < len(action_progs) else None
            slots.append(("occupied" if prog else "empty", prog, i + 1))
        for i in range(passive_total):
            prog = passive_progs[i] if i < len(passive_progs) else None
            slots.append(("passive" if prog else "passive_empty", prog, total + i + 1))

        def _slot_color(kind: str) -> str:
            # Passive slots read cyan so the two pools are tellable apart at a glance
            # -- they are drawn in one row, and "why can't I put Sleaze there" is the
            # question the colour has to answer.
            if kind == "occupied":
                return "green"
            return "cyan" if kind.startswith("passive") else "dim"

        result.append("  ")
        for kind, _, _ in slots:
            result.append("\u250c" + "\u2500" * (SLOT_WIDTH - 2) + "\u2510  ", style=_slot_color(kind))
        result.append("\n")

        result.append("  ")
        for kind, _, num in slots:
            label = f"PASSIVE {num}" if kind.startswith("passive") else f"SLOT {num}"
            pad = SLOT_WIDTH - 3 - len(label)
            result.append(f"\u2502 {label}{' ' * pad}\u2502  ", style=_slot_color(kind))
        result.append("\n")

        result.append("  ")
        for kind, prog, _ in slots:
            if prog:
                name = prog.name[:SLOT_WIDTH - 4]
                pad = SLOT_WIDTH - 3 - len(name)
                style = "bold cyan" if kind.startswith("passive") else "bold green"
                result.append(f"\u2502 {name}{' ' * pad}\u2502  ", style=style)
            else:
                label = "--- EMPTY ---"
                pad = SLOT_WIDTH - 3 - len(label)
                result.append(f"\u2502 {label}{' ' * pad}\u2502  ", style=_slot_color(kind))
        result.append("\n")

        result.append("  ")
        for kind, prog, _ in slots:
            if prog:
                bonus_parts: list[str] = []
                if prog.integrity_bonus:
                    bonus_parts.append(f"+{prog.integrity_bonus} int")
                if prog.firewall_bonus:
                    bonus_parts.append(f"+{prog.firewall_bonus} fw")
                if prog.soak_bonus:
                    bonus_parts.append(f"+{prog.soak_bonus} soak")
                if prog.damage_bonus:
                    bonus_parts.append(f"+{prog.damage_bonus} dmg")
                if prog.action_damage:
                    bonus_parts.append(f"{prog.action_damage} dmg")
                if prog.action_sleaze:
                    bonus_parts.append("sleaze")
                if prog.action_extract:
                    bonus_parts.append("extract")
                if prog.action_analyze:
                    bonus_parts.append("analyze")
                if prog.action_skip_ice:
                    bonus_parts.append("skip")
                if prog.action_fade:
                    bonus_parts.append(f"-{prog.action_fade} sec")
                if bonus_parts:
                    detail = ", ".join(bonus_parts)
                elif prog.uses_per_fight == 0:
                    detail = prog.tag or "passive"
                else:
                    detail = prog.tag or f"{prog.uses_per_fight} uses"
                detail = detail[:SLOT_WIDTH - 4]
                pad = SLOT_WIDTH - 3 - len(detail)
                result.append(f"\u2502 {detail}{' ' * pad}\u2502  ", style="dim cyan")
            else:
                pad = SLOT_WIDTH - 5
                result.append(f"\u2502 {' ' * pad}\u2502  ", style="dim")
        result.append("\n")

        result.append("  ")
        for kind, _, _ in slots:
            result.append("\u2514" + "\u2500" * (SLOT_WIDTH - 2) + "\u2518  ", style=_slot_color(kind))

        return result

    async def _refresh(self) -> None:
        character = self.app.character

        self.query_one("#deck_slot_display", Static).update(self._slot_display(character))

        active_entry = active_deck_entry(character.inventory)
        active_index = character.inventory.index(active_entry[0]) if active_entry else None

        items: list[ListItem] = []
        for index, entry in enumerate(character.inventory):
            item = ITEMS_BY_ID[entry.item_id]
            if item.program_slots <= 0:
                continue
            state = "\u2726 Equipped" if entry.equipped else "\u25c7 Stowed"
            tag = " [active]" if index == active_index else ""
            items.append(
                ListItem(
                    Static(
                        f"{state} \u2014 {item.name}{tag} "
                        f"({item.program_slots} slots, {effective_ram_max(character, item)} RAM)"
                    ),
                    id=f"toggle_{index}",
                )
            )
            if index == active_index and active_entry:
                for program_id in entry.installed_programs:
                    program = PROGRAMS_BY_ID.get(program_id)
                    if program:
                        items.append(
                            ListItem(
                                Static(f"  \u2715 Uninstall {program.name}"),
                                id=f"uninstall_{index}_{program_id}",
                            )
                        )
                for program_id in sorted(character.owned_programs - set(entry.installed_programs)):
                    program = PROGRAMS_BY_ID.get(program_id)
                    if program:
                        items.append(
                            ListItem(
                                Static(f"  \uff0b Install {program.name} ({program.ram_cost} RAM)"),
                                id=f"install_{index}_{program_id}",
                            )
                        )

        if not items:
            items.append(
                ListItem(
                    Static("No cyberdeck owned \u2014 visit a Computer Store."),
                    id="no_deck",
                )
            )

        await _replace_items(self.query_one("#cyberdeck_items", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "no_deck":
            return

        character = self.app.character

        if item_id.startswith("toggle_"):
            self._toggle_equip(int(item_id.removeprefix("toggle_")))
        elif item_id.startswith("install_"):
            index_str, program_id = item_id.removeprefix("install_").split("_", 1)
            self.notify(install_program(character, int(index_str), program_id))
        elif item_id.startswith("uninstall_"):
            index_str, program_id = item_id.removeprefix("uninstall_").split("_", 1)
            self.notify(uninstall_program(character, int(index_str), program_id))

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class ContactsScreen(PanelNav, RefreshOnResume, BackScreen):
    PANEL_IDS = ("fixers_list", "locals_list", "runners_list")
    BINDINGS = [*MENU_BACK_BINDINGS, *PANEL_NAV_BINDINGS]

    CSS = """
    #fixers_panel, #locals_panel, #runners_panel {
        height: auto;
    }

    #fixers_list, #locals_list, #runners_list {
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
            ListView(id="locals_list"), title="Locals", collapsed=False, id="locals_panel"
        )
        yield Collapsible(
            ListView(id="runners_list"), title="Runners", collapsed=False, id="runners_panel"
        )
        yield Footer()

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
        known_runners = [
            r for r in self.app.runners
            if character.knows_runner(r.id) or character.on_crew(r.id)
        ]
        await _populate_list(
            self.query_one("#runners_list", ListView),
            known_runners,
            id_prefix="runner_",
            # Rating is here and not only on BarScreen because it *moves* now
            # (runners.gain_experience): a contact you met at rating 5 and haven't hired
            # since may be worth more than the price you remember.
            label=lambda runner: (
                f"{runner.name} — {runner.archetype}, rating {runner.rating}"
                + (" (on your crew)" if character.on_crew(runner.id) else f" — {self._status(runner)}")
                + f": {runner.description}"
            ),
            empty_label="No runner contacts yet — visit a bar to meet some.",
            empty_id="no_runners",
        )

    def _status(self, runner: RivalRunner) -> str:
        """What an independent runner is doing right now (RunnerState.current,
        rivals.py). A runner with no state yet hasn't had a day tick since the
        run started."""
        state = self.app.rival_runner_states.get(runner.id)
        if state is None:
            return "whereabouts unknown"
        territory = self.app.corp_map.territories[state.territory_id]
        current = state.current(self.app.character.hour_of_day)
        if current is RunnerActivity.WORKING and state.job_title:
            return f"{territory.name}, running {state.job_title}"
        if current is RunnerActivity.DRINKING:
            # rivals.py only picks DRINKING in a territory that has a bar, so
            # naming it here is safe — and much better flavor than "drinking".
            bar = next(loc for loc in territory.locations if loc.kind is LocationKind.BAR)
            return f"{territory.name}, drinking at {bar.name}"
        return f"{territory.name}, {ACTIVITY_LABELS[current]}"

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id

        if item_id.startswith("fixer_"):
            fixer_id = item_id.removeprefix("fixer_")
            fixer = next((fixer for fixer in self.app.fixers if fixer.id == fixer_id), None)
            if fixer is not None:
                self.app.push_screen(FixerOffersScreen(fixer))


class WebScreen(RefreshOnResume, BackScreen):
    """A browser: each megacorp's own site listed like an app shortcut (opens
    CorpWebsiteScreen), above a Search section that's the real cross-fixer job
    board — every open offer from every fixer established trust reaches,
    acceptable from anywhere, not just that fixer's own board
    (shop_screens.FixerOffersScreen)."""

    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="web_list")
        yield Footer()

    async def _refresh(self) -> None:
        character = self.app.character
        established = [fixer for fixer in self.app.fixers if character.trust_with(fixer.id) > 0]

        web_items = [
            ListItem(Static(f"{faction.name} — official site"), id=f"webapp_{faction.id}")
            for faction in FACTIONS
        ]
        web_items.append(ListItem(Static("── Search ──"), id="web_search_header"))
        web_offers = [(fixer, offer) for fixer in established for offer in fixer.open_offers]
        # An owned GigFeed app (shops.owned_app_bonus's "job_alert") appends each
        # open offer's best-case payout (Scene.max_cash_reward) to its Search row --
        # display only, no gate on which offers show up.
        job_alert = bool(owned_app_bonus(character, "job_alert"))
        web_items += (
            [
                ListItem(
                    Static(
                        f"{fixer.name} — {offer_label(character, offer)}"
                        + (
                            f" (~{offer.scene.max_cash_reward}eb)"
                            if job_alert and offer.taken_by is None
                            else ""
                        )
                    ),
                    id=f"weboffer_{offer.id}",
                )
                for fixer, offer in web_offers
            ]
            if web_offers
            else [ListItem(Static("No work posted online."), id="no_web_offers")]
        )
        await _replace_items(self.query_one("#web_list", ListView), web_items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        character = self.app.character

        if item_id.startswith("webapp_"):
            faction_id = item_id.removeprefix("webapp_")
            faction = next(f for f in FACTIONS if f.id == faction_id)
            self.app.push_screen(CorpWebsiteScreen(faction))
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


class CorpWebsiteScreen(RefreshOnResume, BackScreen):
    """One megacorp's own site, reached by tapping its row in WebScreen: a
    one-line masthead plus a blog of recent corp_turn.FactionEvent (territory
    claimed, technology researched) for that faction — most-recent-first,
    same source app.faction_events every faction's site reads from, whether
    the player runs that corp or not."""

    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, faction: Faction) -> None:
        super().__init__()
        self.faction = faction

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(f"{self.faction.name}.net — {self.faction.specialty.value}\n{self.faction.description}")
        yield ListView(id="blog_list")
        yield Footer()

    def _post_label(self, event: FactionEvent) -> str:
        if event.kind == "territory":
            name = self.app.corp_map.territories[event.territory_id].name
            return f"Day {event.day} — Expanded operations into {name}."
        if event.kind == "seizure":
            name = self.app.corp_map.territories[event.territory_id].name
            rival = FACTIONS_BY_ID[event.from_faction_id].name
            return f"Day {event.day} — Acquired {name} from {rival} in a hostile takeover."
        technology = TECHNOLOGIES_BY_ID[event.technology_id]
        return f"Day {event.day} — Unveiled new technology: {technology.name}."

    async def _refresh(self) -> None:
        events = self.app.faction_events.get(self.faction.id, [])
        blog_items = (
            [ListItem(Static(self._post_label(event)), id=f"blogpost_{i}") for i, event in enumerate(events)]
            if events
            else [ListItem(Static("No updates yet."), id="no_blog_posts")]
        )
        await _replace_items(self.query_one("#blog_list", ListView), blog_items)


class AlarmClockScreen(RefreshOnResume, BackScreen):
    """Sets Character.alarm_hour, read by app.rest() to cut a Rest short instead
    of the flat REST_HOURS_COST."""

    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="alarm_list")
        yield Footer()

    async def _refresh(self) -> None:
        character = self.app.character
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

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "alarm_status":
            return
        hour = int(item_id.removeprefix("alarm_"))
        character = self.app.character
        character.alarm_hour = None if character.alarm_hour == hour else hour
        await self._refresh()


class MessagesScreen(RefreshOnResume, BackScreen):
    """A read-only recap of what Contacts/Web already show, reframed as texts —
    no state of its own, so there's nothing here to persist or expire."""

    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="messages_list")
        yield Footer()

    def _messages(self, character: Character) -> list[str]:
        lines = []
        for fixer in self.app.fixers:
            if character.trust_with(fixer.id) <= 0:
                continue
            if fixer.open_offers:
                lines.append(f"{fixer.name}: got {len(fixer.open_offers)} job(s) if you're interested.")
            if fixer.security_offers:
                lines.append(f"{fixer.name}: security work up for grabs too, if that's more your speed.")
        for runner in self.app.runners:
            state = self.app.rival_runner_states.get(runner.id)
            # job_title is only ever set on a day a runner went WORKING (rivals._runner_turn).
            if state is not None and state.job_title:
                lines.append(f"{runner.name}: heads up, I'm out running {state.job_title} today.")
        return lines

    async def _refresh(self) -> None:
        lines = self._messages(self.app.character)
        message_items = (
            [ListItem(Static(line), id=f"message_{i}") for i, line in enumerate(lines)]
            if lines
            else [ListItem(Static("No new messages."), id="no_messages")]
        )
        await _replace_items(self.query_one("#messages_list", ListView), message_items)


class AppStoreScreen(RefreshOnResume, BackScreen):
    """Buy a one-time Phone app (shops.APP_STORE_CATALOG) into Character.owned_apps --
    no location, no owner, no standing gate, unlike every other catalog screen: it's
    reachable from the Phone itself, same as Contacts/Web/Messages."""

    BINDINGS = MENU_BACK_BINDINGS

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ListView(id="app_store_list")
        yield Footer()

    async def _refresh(self) -> None:
        character = self.app.character
        items = []
        for app in APP_STORE_CATALOG:
            if app.id in character.owned_apps:
                label = f"{app.name} — owned ({app.tag})"
                items.append(ListItem(Static(label), id=f"owned_{app.id}"))
                continue
            label = f"Buy {app.name} — {app.price}eb ({app.tag})"
            if character.cash < app.price:
                label += " — can't afford"
            items.append(ListItem(Static(label), id=f"buy_{app.id}"))
        await _replace_items(self.query_one("#app_store_list", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id.startswith("buy_"):
            self.notify(buy_app(self.app.character, item_id.removeprefix("buy_")))
            self.query_one(CharacterSheet).refresh()
            await self._refresh()


class PhoneScreen(BackScreen):
    """The runner's handheld — a phone's home screen: a 3-column grid of app
    shortcuts (ContactsScreen, WebScreen, AlarmClockScreen, MessagesScreen,
    AppStoreScreen), each opening as its own screen rather than expanding inline."""

    BINDINGS = MENU_BACK_BINDINGS

    APPS = [
        ("contacts", "Contacts", ContactsScreen),
        ("web", "Web", WebScreen),
        ("alarm", "Alarm Clock", AlarmClockScreen),
        ("messages", "Messages", MessagesScreen),
        ("app_store", "App Store", AppStoreScreen),
    ]

    CSS = """
    #phone_frame {
        border: round $accent;
        padding: 1 2;
    }

    #phone_banner {
        text-align: center;
        color: $accent;
    }

    #phone_apps {
        layout: grid;
        grid-size: 3;
        grid-gutter: 1 2;
        grid-rows: 5;
        height: auto;
    }

    #phone_apps > ListItem {
        border: round $accent;
        height: 100%;
    }

    #phone_apps > ListItem > Static {
        width: 100%;
        height: 100%;
        content-align: center middle;
        text-style: bold;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static("── SG-Phone ──", id="phone_banner"),
            ListView(
                *(ListItem(Static(label), id=f"app_{key}") for key, label, _screen in self.APPS),
                id="phone_apps",
            ),
            id="phone_frame",
        )
        yield Footer()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        key = event.item.id.removeprefix("app_")
        screen_cls = next(screen_cls for app_key, _label, screen_cls in self.APPS if app_key == key)
        self.app.push_screen(screen_cls())


class SkillsScreen(PanelNav, RefreshOnResume, BackScreen):
    """Read-only for skill *values* (gear bonuses included), but spendable for
    Character.experience: a "Raise <Stat>" row atop each column plus every skill
    row, both showing next-purchase cost the same way CharacterCreationScreen's
    build columns do, funded by spend_experience_on_stat/spend_experience_on_skill
    instead of the one-shot creation pools."""

    PANEL_IDS = tuple(f"skill_list_{stat}" for stat in CORE_STATS)
    BINDINGS = [*MENU_BACK_BINDINGS, *PANEL_NAV_BINDINGS]

    CSS = """
    /* The grid has to live inside a scroller, the same way CharacterCreationScreen's
       build columns do: a .skill_column ListView is height:auto, so it sizes to its
       content and clips inside the grid cell rather than scrolling itself. Logic
       carries 11 skills now, and without this the rows past Tactics were painted
       below the viewport on any ordinary terminal -- unreachable, not just off-screen,
       because nothing in the chain had anywhere to scroll to. */
    #skills_scroll {
        height: 1fr;
    }

    #skills_grid {
        grid-size: 3 2;
        grid-gutter: 1 2;
        height: auto;
    }

    .skill_column {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    .skill_column ListView {
        /* Bounded, not auto: a column taller than the viewport can't be brought into
           view by the outer scroller alone -- scrolling a widget into view only makes
           it visible inside *its* container, and an auto-height ListView's container
           is as tall as its content. Capping it means the list scrolls itself, so the
           highlighted row is always reachable no matter how many skills a stat carries. */
        height: auto;
        max-height: 12;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield ScrollableContainer(
            Grid(
                *(
                    Vertical(
                        Static(id=f"skill_head_{stat}"),
                        ListView(id=f"skill_list_{stat}"),
                        classes="skill_column",
                    )
                    for stat in CORE_STATS
                ),
                id="skills_grid",
            ),
            id="skills_scroll",
        )
        yield Footer()

    async def _refresh(self, stat: str | None = None, index: int = 0) -> None:
        character = self.app.character
        for s in CORE_STATS if stat is None else (stat,):
            self.query_one(f"#skill_head_{s}", Static).update(f"{s.capitalize()} — {character.stat(s)}")
            cost = character.next_stat_cost(s)
            items = [ListItem(Static(f"Raise {s.capitalize()}\n  {cost}xp"), id=f"stat_{s}")]
            items += [
                ListItem(Static(_compact_skill_label(character, skill)), id=f"skill_{skill.id}")
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
