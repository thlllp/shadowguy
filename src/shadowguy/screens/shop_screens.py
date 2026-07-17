from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.corpmap import Location, LocationKind
from shadowguy.factions import Faction, officer_dialogue, officer_gate, officer_unlocked
from shadowguy.fixer import Fixer
from shadowguy.runners import RUNNERS_BY_ID, RIVAL_RUNNERS
from shadowguy.shops import (
    CATALOG,
    CONSUMABLE_CATALOG,
    CONSUMABLES_BY_ID,
    HOSPITAL_STAY_COST,
    ITEMS_BY_ID,
    bonus_text,
    buy_consumable,
    buy_item,
    buy_price,
    hospital_stay,
    sell_item,
    sell_price,
)

from . import CharacterSheet, _populate_list, _replace_items


class FixerOffersScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    CSS = """
    #offer_roles {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }
    """

    def __init__(self, fixer: Fixer) -> None:
        super().__init__()
        self.fixer = fixer

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(f"{self.fixer.name} — {self.fixer.specialty}", id="fixer_info")
        yield ListView(id="offers")
        yield Static(id="offer_roles")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        items = [
            ListItem(
                Static(f"{offer.scene.title} ({offer.scene.stamina_cost} stamina) — {offer.timing.label}"),
                id=offer.id,
            )
            for offer in self.fixer.offers
        ]
        offers = self.query_one("#offers", ListView)
        await _replace_items(offers, items)
        if self.fixer.offers:
            offers.index = 0
        self._show_roles(self.fixer.offers[0].id if self.fixer.offers else None)

    def _show_roles(self, offer_id: str | None) -> None:
        panel = self.query_one("#offer_roles", Static)
        offer = next((o for o in self.fixer.offers if o.id == offer_id), None)
        if offer is None or not offer.scene.roles:
            panel.update("")
            return
        lines = ["Crew roles (open — no crew yet):"]
        lines += [
            f"  {role.beat.title():13}— {role.specialist}, {role.posture.value}"
            for role in offer.scene.roles
        ]
        panel.update("\n".join(lines))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        self._show_roles(event.item.id if event.item else None)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        offer = next(offer for offer in self.fixer.offers if offer.id == event.item.id)
        self.app.character.accept_job(offer)
        self.fixer.offers = [o for o in self.fixer.offers if o.id != offer.id]
        await self._refresh()


class ShopScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="shop_info")
        yield ListView(id="shop_items")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    def _owner_standing(self) -> int:
        character = self.app.character
        owner = self.location.characters[0] if self.location.characters else None
        return character.local_standing_with(owner.id) if owner else 0

    async def _refresh(self) -> None:
        character = self.app.character
        owner = self.location.characters[0] if self.location.characters else None
        standing = character.local_standing_with(owner.id) if owner else 0
        header = self.location.name
        if owner:
            header += f" — {owner.name} ({owner.role}), standing {standing:+d}"
        self.query_one("#shop_info", Static).update(header)
        items = []

        for item in CATALOG.get(self.location.kind, []):
            if item.min_standing > standing:
                continue
            price = buy_price(item.price, standing)
            bonus = bonus_text(item)
            label = f"Buy {item.name} — {price}eb" + (f" ({bonus})" if bonus else "")
            if character.cash < price:
                label += " — can't afford"
            items.append(ListItem(Static(label), id=f"buy_{item.id}"))

        for consumable in CONSUMABLE_CATALOG.get(self.location.kind, []):
            if consumable.min_standing > standing:
                continue
            price = buy_price(consumable.price, standing)
            label = f"Buy {consumable.name} — {price}eb"
            if character.cash < price:
                label += " — can't afford"
            items.append(ListItem(Static(label), id=f"buyc_{consumable.id}"))

        if self.location.kind == LocationKind.PAWN:
            for index, entry in enumerate(character.inventory):
                item = ITEMS_BY_ID[entry.item_id]
                proceeds = sell_price(item.price, standing)
                items.append(ListItem(Static(f"Sell {item.name} — {proceeds}eb"), id=f"sell_{index}"))

        await _replace_items(self.query_one("#shop_items", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        standing = self._owner_standing()
        item_id = event.item.id

        if item_id.startswith("buy_"):
            item = ITEMS_BY_ID[item_id.removeprefix("buy_")]
            if not buy_item(character, item, standing):
                self.notify(f"Can't afford {item.name}.", severity="warning")
        elif item_id.startswith("buyc_"):
            consumable = CONSUMABLES_BY_ID[item_id.removeprefix("buyc_")]
            if not buy_consumable(character, consumable, standing):
                self.notify(f"Can't afford {consumable.name}.", severity="warning")
        elif item_id.startswith("sell_"):
            sell_item(character, int(item_id.removeprefix("sell_")), standing)

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class BarScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location
        self.chosen_runner: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="bar_info")
        yield ListView(id="bar_runners")
        yield Footer()

    async def action_back(self) -> None:
        if self.chosen_runner is not None:
            self.chosen_runner = None
            await self._refresh()
        else:
            self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        info = self.query_one("#bar_info", Static)
        if self.chosen_runner is None:
            info.update(f"{self.location.name} — ask around for runners looking for work")
            items = self._roster_items()
        else:
            runner = RUNNERS_BY_ID[self.chosen_runner]
            info.update(f"Bring {runner.name} on — on what terms?")
            items = self._terms_items(runner)
        await _replace_items(self.query_one("#bar_runners", ListView), items)

    def _roster_items(self) -> list[ListItem]:
        character = self.app.character
        items = []
        for runner in RIVAL_RUNNERS:
            tag = f"{runner.name} ({runner.archetype}, rating {runner.rating})"
            label = f"{tag} — on your crew" if character.on_crew(runner.id) else f"Recruit {tag}"
            items.append(ListItem(Static(label), id=f"runner_{runner.id}"))
        return items

    def _terms_items(self, runner) -> list[ListItem]:
        items = [
            ListItem(Static(f"Keep on indefinitely — {runner.daily_cost}eb/day"), id="opt_indef")
        ]
        pct = round(runner.job_cut * 100)
        for job in self.app.character.accepted_jobs:
            items.append(
                ListItem(
                    Static(f"For the job: {job.scene.title} — {pct}% cut of the payout"),
                    id=f"opt_job_{job.scene.id}",
                )
            )
        items.append(ListItem(Static("Back"), id="opt_back"))
        return items

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        item_id = event.item.id
        if self.chosen_runner is None:
            runner = RUNNERS_BY_ID[item_id.removeprefix("runner_")]
            if not character.on_crew(runner.id):
                self.chosen_runner = runner.id
                await self._refresh()
            return

        runner = RUNNERS_BY_ID[self.chosen_runner]
        if item_id == "opt_indef":
            character.hire_indefinite(runner.id)
            self.notify(f"{runner.name} is on the crew ({runner.daily_cost}eb/day).")
        elif item_id.startswith("opt_job_"):
            job_scene_id = item_id.removeprefix("opt_job_")
            character.hire_for_job(runner.id, job_scene_id)
            self.notify(f"{runner.name} signed on for the job.")
        self.chosen_runner = None
        await self._refresh()
        await self._refresh()


class SafehouseScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name)
        yield Static("Your place. Nothing to do here yet.")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()


class RealEstateScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="realestate_info")
        yield ListView(id="realestate_listings")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        character = self.app.character
        territories = self.app.corp_map.territories
        items = []
        for territory_id in self.location.listings:
            territory = territories[territory_id]
            from shadowguy.corpmap import has_home, safehouse_price

            if has_home(territory):
                continue
            price = safehouse_price(territory)
            label = f"Safehouse in {territory.name} — {price}eb"
            if character.cash < price:
                label += " — can't afford"
            items.append(ListItem(Static(label), id=f"buy_{territory_id}"))
        if not items:
            items.append(ListItem(Static("No properties available."), id="none"))
        await _replace_items(self.query_one("#realestate_listings", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        from shadowguy.corpmap import add_safehouse, has_home, safehouse_price

        item_id = event.item.id
        if not item_id.startswith("buy_"):
            return
        territory = self.app.corp_map.territories[item_id.removeprefix("buy_")]
        character = self.app.character
        if has_home(territory):
            return
        price = safehouse_price(territory)
        if character.cash < price:
            return
        character.cash -= price
        add_safehouse(territory)
        self.notify(f"Bought a safehouse in {territory.name} for {price}eb.")
        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class HospitalScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="hospital_info")
        yield ListView(id="hospital_actions")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        character = self.app.character
        if character.health >= character.max_health:
            row = ListItem(Static("Fully patched up — nothing to treat."), id="none")
        else:
            label = f"Stay the night — heal 1d6+Body, {HOSPITAL_STAY_COST}eb"
            if character.cash < HOSPITAL_STAY_COST:
                label += " (can't afford)"
            row = ListItem(Static(label), id="stay")
        await _replace_items(self.query_one("#hospital_actions", ListView), [row])

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id != "stay":
            return
        message = hospital_stay(self.app.character)
        if message is None:
            self.notify("Can't afford a night's care.", severity="warning")
            return
        self.app.advance_day()
        self.notify(message)
        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class CorpHQScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location, faction: Faction) -> None:
        super().__init__()
        self.location = location
        self.faction = faction

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(f"{self.faction.name} — Corporate HQ", id="hq_info")
        yield ListView(id="hq_officers")
        yield Static("", id="hq_dialogue")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        character = self.app.character
        standing = character.standing_with(self.faction.id)
        self.query_one("#hq_info", Static).update(
            f"{self.faction.name} — Corporate HQ  "
            f"(your rep {character.rep}, standing {standing:+d})"
        )

        def label(officer) -> str:
            if officer_unlocked(character.rep, standing, officer.role):
                return f"{officer.name} ({officer.role}) — talk"
            return f"{officer.name} ({officer.role}) — locked (needs {officer_gate(officer.role)})"

        await _populate_list(
            self.query_one("#hq_officers", ListView),
            self.location.characters,
            id_prefix="officer_",
            label=label,
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        standing = character.standing_with(self.faction.id)
        officer_id = event.item.id.removeprefix("officer_")
        officer = next((char for char in self.location.characters if char.id == officer_id), None)
        if officer is None:
            return
        dialogue = self.query_one("#hq_dialogue", Static)
        if not officer_unlocked(character.rep, standing, officer.role):
            dialogue.update(
                f"{officer.name}'s people wave you off — come back with {officer_gate(officer.role)}."
            )
            return
        dialogue.update(officer_dialogue(self.faction, officer.role, standing))
