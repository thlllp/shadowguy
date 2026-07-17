from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.corpmap import (
    PLAYER_OWNED_KINDS,
    SHOP_KINDS,
    LocationKind,
    lodging_cost,
    owner_label,
)
from shadowguy.factions import FACTIONS_BY_ID
from shadowguy.fixer import discover_fixers_here
from shadowguy.gangs import GANGS_BY_ID
from shadowguy.jobs import generate_legwork_for_job
from shadowguy.scene import Scene
from shadowguy.security import resolve_security_night

from . import PANEL_NAV_BINDINGS, CharacterSheet, PanelNav, _replace_items, matrix_warning
from .corp_map_screen import CorpMapScreen
from .info_screens import ContactsScreen, InventoryScreen, SkillsScreen
from .scene_screen import SceneScreen
from .shop_screens import (
    BarScreen,
    CorpHQScreen,
    FixerOffersScreen,
    HospitalScreen,
    RealEstateScreen,
    SafehouseScreen,
    ShopScreen,
)


class MainMenu(PanelNav, Screen):
    PANEL_IDS = ("categories", "activities")
    BINDINGS = [
        ("q", "quit_menu", "Menu"),
        ("m", "corp_map", "Corp Map (preview)"),
        ("i", "inventory", "Gear"),
        ("k", "skills", "Skills"),
        ("c", "contacts", "Contacts"),
        *PANEL_NAV_BINDINGS,
    ]

    CSS = """
    #stats_panel {
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
    """

    CATEGORIES = [
        ("gig", "Gigs"),
        ("job", "Jobs"),
        ("legwork", "Legwork"),
        ("local", "Local"),
        ("gear", "Gear"),
        ("skills", "Skills"),
        ("contacts", "Contacts"),
        ("map", "Corp Map"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.selected_category = self.CATEGORIES[0][0]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(CharacterSheet(self.app.character), id="stats_panel")
        yield Horizontal(
            Vertical(ListView(id="categories"), id="sidebar"),
            Vertical(ListView(id="activities"), id="main_panel"),
        )
        yield Footer()

    def action_corp_map(self) -> None:
        self.app.push_screen(CorpMapScreen())

    def action_inventory(self) -> None:
        self.app.push_screen(InventoryScreen())

    def action_skills(self) -> None:
        self.app.push_screen(SkillsScreen())

    def action_contacts(self) -> None:
        self.app.push_screen(ContactsScreen())

    async def on_mount(self) -> None:
        await self._refresh_categories()
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh_categories(self) -> None:
        items = [ListItem(Static(label), id=f"cat_{key}") for key, label in self.CATEGORIES]
        await _replace_items(self.query_one("#categories", ListView), items)

    def _on_site(self, scene: Scene) -> bool:
        return self.app.character.location_id == scene.target_territory_id

    def _district(self, scene: Scene) -> str:
        return self.app.corp_map.territories[scene.target_territory_id].name

    async def _refresh(self) -> None:

        self.query_one(CharacterSheet).refresh()
        character = self.app.character
        items = []

        if self.selected_category == "gig":
            here = self.app.corp_map.territories[character.location_id]
            for location in here.locations:
                gig = self.app.location_gigs.get(location.id)
                if gig is None:
                    continue
                owner = next((c for c in location.characters if c.id == gig.target_character_id), None)
                who = f" — {owner.name}" if owner else ""
                label = f"Gig — {gig.title} @ {location.name}{who} ({gig.stamina_cost} stamina)"
                if not character.can_afford(gig.stamina_cost):
                    label += " — too tired"
                elif character.cash < gig.max_cash_loss:
                    label += f" — can't cover the stake ({gig.max_cash_loss} cash)"
                items.append(ListItem(Static(label), id=f"gig_{location.id}"))

        if self.selected_category == "job":
            for job in character.accepted_jobs:
                label = f"Job — {job.scene.title} ({job.scene.stamina_cost} stamina) — {job.timing.label}"
                if not self._on_site(job.scene):
                    label += f" — travel to {self._district(job.scene)}"
                elif not job.timing.is_available(character.day):
                    label += " — not yet"
                elif not character.can_afford(job.scene.stamina_cost):
                    label += " — too tired"
                label += matrix_warning(character, job.scene)
                items.append(ListItem(Static(label), id=f"job_{job.id}"))
            # Display-only: a security contract isn't "run" like a job — it progresses
            # by ending the day on-site (see the end_day branch below), so it carries
            # no stamina cost or travel-gated action here.
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

        if self.selected_category == "legwork":
            for job in character.accepted_jobs:
                advantage = character.advantage_for(job.scene.id)
                legwork_label = f"Legwork — Case the job: {job.scene.title}"
                if advantage:
                    legwork_label += f" (advantage +{advantage} banked)"
                if not self._on_site(job.scene):
                    legwork_label += f" — travel to {self._district(job.scene)}"
                items.append(ListItem(Static(legwork_label), id=f"legwork_{job.id}"))

        if self.selected_category == "local":
            corp_map = self.app.corp_map
            here = corp_map.territories[character.location_id]
            gang_suffix = f", gang: {GANGS_BY_ID[here.gang_id].name}" if here.gang_id else ""
            items.append(
                ListItem(
                    Static(f"{here.name} — {owner_label(here.owner)}{gang_suffix}"),
                    id="local_district",
                )
            )
            for location in here.locations:
                items.append(
                    ListItem(
                        Static(f"  {location.name} ({location.kind})"),
                        id=f"local_{location.id}",
                    )
                )
            discover_fixers_here(self.app.fixers, character)
            for fixer in self.app.fixers:
                if fixer.location_id == character.location_id:
                    items.append(
                        ListItem(
                            Static(
                                f"  {fixer.name} — {fixer.specialty} "
                                f"({len(fixer.offers)} jobs, {len(fixer.security_offers)} security available)"
                            ),
                            id=f"local_fixer_{fixer.id}",
                        )
                    )

        items.append(ListItem(Static("End the day (rest)"), id="end_day"))
        await _replace_items(self.query_one("#activities", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.list_view.id == "categories":
            await self._select_category(event.item.id.removeprefix("cat_"))
            return

        item_id = event.item.id
        character = self.app.character

        if item_id.startswith("security_info_"):
            return

        if item_id == "end_day":
            here_id = character.location_id
            here = self.app.corp_map.territories[here_id]
            # Computed before any resolution/removal below: a contract that completes
            # tonight must still count toward tonight's free lodging, since it was
            # active here when the night started.
            active_here = [c for c in character.security_contracts if c.territory_id == here_id]
            for contract in active_here:
                result = resolve_security_night(character, contract, self.app.rng)
                if result.blown:
                    self.notify(
                        f"Security contract at {here.name} blown — you're compromised.",
                        severity="error",
                    )
                    character.remove_security_contract(contract.id)
                elif result.completed:
                    self.notify(
                        f"Security contract at {here.name} complete — {result.pay + result.bonus}eb paid."
                    )
                    character.remove_security_contract(contract.id)
                elif result.pay:
                    self.notify(
                        f"Stood watch at {here.name} — paid {result.pay}eb "
                        f"({contract.nights_completed}/{contract.nights_total} nights)."
                    )
                else:
                    self.notify(
                        f"Rough night at {here.name} — no pay "
                        f"({contract.nights_completed}/{contract.nights_total} nights)."
                    )
            for contract in character.security_contracts:
                if contract.territory_id != here_id:
                    elsewhere = self.app.corp_map.territories[contract.territory_id]
                    self.notify(f"Not on-site for the {elsewhere.name} contract tonight — no progress.")

            cost = 0 if active_here else lodging_cost(here)
            if cost:
                paid = min(cost, character.cash)
                character.cash -= paid
                self.notify(f"Paid {paid}eb for lodging in {here.name}.")
            self.app.advance_day()
            await self._refresh()
            return

        if item_id.startswith("gig_"):
            location_id = item_id.removeprefix("gig_")
            gig = self.app.location_gigs.get(location_id)
            if gig is None:
                return
            if not character.can_afford(gig.stamina_cost):
                return
            if character.cash < gig.max_cash_loss:
                return
            character.spend_stamina(gig.stamina_cost)
            self.app.push_screen(SceneScreen(gig))
            return

        if item_id.startswith("legwork_"):
            offer_id = item_id.removeprefix("legwork_")
            job = next(job for job in character.accepted_jobs if job.id == offer_id)
            if not self._on_site(job.scene):
                return
            legwork_scene = generate_legwork_for_job(job.scene, self.app.corp_map, self.app.rng)
            if not character.can_afford(legwork_scene.stamina_cost):
                return
            character.spend_stamina(legwork_scene.stamina_cost)
            self.app.push_screen(SceneScreen(legwork_scene))
            return

        if item_id.startswith("job_"):
            offer_id = item_id.removeprefix("job_")
            job = next(job for job in character.accepted_jobs if job.id == offer_id)
            if not self._on_site(job.scene):
                return
            if not job.timing.is_available(character.day) or not character.can_afford(job.scene.stamina_cost):
                return
            character.spend_stamina(job.scene.stamina_cost)
            self.app.push_screen(SceneScreen(job.scene))
            return

        if item_id.startswith("local_fixer_"):
            fixer_id = item_id.removeprefix("local_fixer_")
            fixer = next(fixer for fixer in self.app.fixers if fixer.id == fixer_id)
            self.app.push_screen(FixerOffersScreen(fixer))
            return

        if item_id.startswith("local_") and item_id != "local_district":
            location_id = item_id.removeprefix("local_")
            here = self.app.corp_map.territories[character.location_id]
            location = next((loc for loc in here.locations if loc.id == location_id), None)
            if location is None:
                return
            if location.kind in SHOP_KINDS:
                self.app.push_screen(ShopScreen(location))
            elif location.kind == LocationKind.HOSPITAL:
                self.app.push_screen(HospitalScreen(location))
            elif location.kind == LocationKind.REAL_ESTATE:
                self.app.push_screen(RealEstateScreen(location))
            elif location.kind == LocationKind.CORP_HQ:
                self.app.push_screen(CorpHQScreen(location, FACTIONS_BY_ID[here.owner]))
            elif location.kind == LocationKind.BAR:
                self.app.push_screen(BarScreen(location))
            elif location.kind in PLAYER_OWNED_KINDS:
                self.app.push_screen(SafehouseScreen(location))
            return

    async def _select_category(self, key: str) -> None:
        if key == "contacts":
            self.app.push_screen(ContactsScreen())
        elif key == "map":
            self.app.push_screen(CorpMapScreen())
        elif key == "gear":
            self.app.push_screen(InventoryScreen())
        elif key == "skills":
            self.app.push_screen(SkillsScreen())
        self.selected_category = key
        await self._refresh()
