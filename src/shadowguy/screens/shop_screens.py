from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import HOURS_PER_DAY, Character
from shadowguy.corpmap import (
    WORKSHOP_BUILD_COST,
    Location,
    LocationKind,
    add_safehouse,
    build_workshop,
    has_home,
    safehouse_price,
)
from shadowguy.corp_turn import CorpState
from shadowguy.factions import (
    EXECUTIVE_ROLE,
    FACTIONS_BY_ID,
    TAKEOVER_COST,
    Faction,
    can_take_over,
    officer_dialogue,
    officer_gate,
    officer_unlocked,
    takeover_gate,
)
from shadowguy.fixer import RUNNER_BROKER_FIXER_IDS, Fixer, JobOffer
from shadowguy.gangs import Gang
from shadowguy.jobs import generate_smuggling_job
from shadowguy.rivals import RunnerActivity
from shadowguy.security import SecurityContract
from shadowguy.runners import RUNNERS_BY_ID, intro_cost, recruit_cut, recruit_wage
from shadowguy.cybernetics import (
    CYBERWARE_BY_ID,
    CyberSlot,
    Cyberware,
    catalog_for_standing,
    free_humanity,
    install_cyberware,
    lost_to_cyberpsychosis,
    remove_cyberware,
)
from shadowguy.skills import skill_for, skill_value
from shadowguy.shops import (
    CATALOG,
    CONSUMABLE_CATALOG,
    CONSUMABLES_BY_ID,
    CRAFT_RECIPES,
    HOSPITAL_STAY_COST,
    ITEMS_BY_ID,
    MOD_CATALOG,
    MOD_SLOTS_PER_ITEM,
    MODS_BY_ID,
    PROGRAM_CATALOG,
    SCAVENGE_HOURS_COST,
    WORKSHOP_HOURS_COST,
    bonus_text,
    buy_consumable,
    buy_item,
    buy_price,
    buy_program,
    craft_consumable,
    hospital_stay,
    install_mod,
    remove_mod,
    scavenge,
    sell_item,
    sell_price,
)

from . import (
    MENU_BACK_BINDINGS,
    PANEL_NAV_BINDINGS,
    BackScreen,
    CharacterSheet,
    RefreshOnResume,
    PanelNav,
    _populate_list,
    _replace_items,
    matrix_warning,
)


def offer_label(character: Character, offer: JobOffer) -> str:
    # An offer a rival runner beat the player to (rivals.py) stays listed for
    # the day it was lost, so the board shows what went instead of quietly
    # having one row fewer. No square brackets — Static parses them as Rich
    # markup and eats them.
    if offer.taken_by is not None:
        runner = RUNNERS_BY_ID[offer.taken_by]
        return f"{offer.scene.title} — TAKEN, {runner.name} got there first"
    return (
        f"{offer.scene.title} ({offer.scene.hours_cost}h) — {offer.timing.label}"
        f"{matrix_warning(character, offer.scene)}"
    )


def _contact_label(runner) -> str:
    return f"Ask about {runner.name} ({runner.archetype}) — {intro_cost(runner)}eb, introduce"


class FixerOffersScreen(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

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

    async def on_mount(self) -> None:
        await self._refresh()

    def _security_label(self, contract: SecurityContract) -> str:
        corp_map = self.app.corp_map
        faction = FACTIONS_BY_ID[contract.faction_id]
        territory = corp_map.territories[contract.territory_id]
        location = next(loc for loc in territory.locations if loc.id == contract.location_id)
        return (
            f"Security — {faction.name} at {location.name} ({territory.name}) — "
            f"{contract.nights_total} nights, {contract.nightly_pay}eb/night "
            f"+ {contract.completion_bonus}eb bonus"
        )

    async def _refresh(self) -> None:
        character = self.app.character
        items = [
            ListItem(Static(offer_label(character, offer)), id=offer.id)
            for offer in self.fixer.offers
        ]
        items += [
            ListItem(Static(self._security_label(contract)), id=contract.id)
            for contract in self.fixer.security_offers
        ]
        if self.fixer.id in RUNNER_BROKER_FIXER_IDS:
            items += [
                ListItem(Static(_contact_label(runner)), id=f"intro_{runner.id}")
                for runner in self.app.runners
                if character.runner_available(runner.id)
                and not character.knows_runner(runner.id)
                and not character.on_crew(runner.id)
            ]
        await _replace_items(self.query_one("#offers", ListView), items)
        self._show_roles(items[0].id if items else None)

    def _show_roles(self, offer_id: str | None) -> None:
        panel = self.query_one("#offer_roles", Static)
        # Security contracts have no Scene, so no roles to show — clear the panel.
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
        item_id = event.item.id
        if item_id.startswith("security_"):
            contract = next(c for c in self.fixer.security_offers if c.id == item_id)
            self.app.character.accept_security_contract(contract)
            self.fixer.security_offers = [c for c in self.fixer.security_offers if c.id != item_id]
            await self._refresh()
            return
        if item_id.startswith("intro_"):
            character = self.app.character
            runner = self.app.runner(item_id.removeprefix("intro_"))
            cost = intro_cost(runner)
            if character.cash < cost:
                self.notify(f"Can't afford the introduction ({cost}eb).", severity="warning")
                return
            character.cash -= cost
            character.meet_runner(runner.id)
            self.notify(f"{self.fixer.name} makes a call. You've got {runner.name}'s number now.")
            await self._refresh()
            return
        offer = next(offer for offer in self.fixer.offers if offer.id == item_id)
        if offer.taken_by is not None:
            self.notify(
                f"{self.app.runner(offer.taken_by).name} already took that one.", severity="warning"
            )
            return
        self.app.character.accept_job(offer)
        self.fixer.offers = [o for o in self.fixer.offers if o.id != offer.id]
        await self._refresh()


class ShopScreen(PanelNav, BackScreen):
    PANEL_IDS = ("shop_items", "shop_programs")
    BINDINGS = [*MENU_BACK_BINDINGS, *PANEL_NAV_BINDINGS]

    CSS = """
    #shop_items_panel, #shop_programs_panel, #shop_items, #shop_programs {
        height: auto;
    }
    """

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="shop_info")
        yield Collapsible(ListView(id="shop_items"), title="Stock", collapsed=False, id="shop_items_panel")
        yield Collapsible(
            ListView(id="shop_programs"), title="Programs", collapsed=False, id="shop_programs_panel"
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def _owner(self):
        """Every SHOP_KINDS location is generated with exactly one character, so the
        first one is the owner whose standing prices this shelf."""
        return self.location.characters[0] if self.location.characters else None

    def _owner_standing(self) -> int:
        owner = self._owner()
        return self.app.character.local_standing_with(owner.id) if owner else 0

    async def _refresh(self) -> None:
        character = self.app.character
        owner = self._owner()
        standing = self._owner_standing()
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

        programs = []
        for program in PROGRAM_CATALOG.get(self.location.kind, []):
            if program.min_standing > standing:
                continue
            price = buy_price(program.price, standing)
            label = f"Buy {program.name} — {price}eb"
            if program.id in character.owned_programs:
                label += " — owned"
            elif character.cash < price:
                label += " — can't afford"
            programs.append(ListItem(Static(label), id=f"buyp_{program.id}"))
        if not programs:
            programs = [ListItem(Static("No programs available."), id="no_programs")]

        await _replace_items(self.query_one("#shop_programs", ListView), programs)

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
        elif item_id.startswith("buyp_"):
            self.notify(buy_program(character, item_id.removeprefix("buyp_"), standing))
        elif item_id.startswith("sell_"):
            sell_item(character, int(item_id.removeprefix("sell_")), standing)

        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class BarScreen(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

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
            runner = self.app.runner(self.chosen_runner)
            info.update(f"Bring {runner.name} on — on what terms?")
            items = self._terms_items(runner)
        await _replace_items(self.query_one("#bar_runners", ListView), items)

    def _runner_here(self, runner_id: str) -> bool:
        """Whether `runner_id` happens to be drinking at this bar right now
        (rivals.RunnerActivity.DRINKING in the runner's current territory) —
        the encounter that lets the player exchange numbers with them."""
        state = self.app.rival_runner_states.get(runner_id)
        return (
            state is not None
            and state.territory_id == self.app.character.location_id
            and state.current(self.app.character.hour_of_day) is RunnerActivity.DRINKING
        )

    def _roster_items(self) -> list[ListItem]:
        character = self.app.character
        items = []
        for runner in self.app.runners:
            if not character.runner_available(runner.id):
                continue
            if not self._runner_here(runner.id):
                continue
            tag = f"{runner.name} ({runner.archetype}, rating {runner.rating})"
            if character.on_crew(runner.id):
                label = f"{tag} — on your crew"
                item_id = f"runner_{runner.id}"
            elif character.knows_runner(runner.id):
                label = f"Recruit {tag}"
                item_id = f"runner_{runner.id}"
            else:
                label = f"{runner.name} is here, nursing a drink — exchange numbers?"
                item_id = f"meet_{runner.id}"
            items.append(ListItem(Static(label), id=item_id))
        return items

    def _terms_items(self, runner) -> list[ListItem]:
        leadership = skill_value(self.app.character, "leadership")
        wage = recruit_wage(runner, leadership)
        items = [
            ListItem(Static(f"Keep on indefinitely — {wage}eb/day"), id="opt_indef")
        ]
        pct = round(recruit_cut(runner, leadership) * 100)
        character = self.app.character
        for job in character.accepted_jobs:
            scene = job.scene
            if scene.max_on_site is None and scene.max_support is None:
                # Uncapped archetype: on-site vs support is meaningless here (nothing
                # reads CrewHire.on_site outside a roster-cap check), so keep the single
                # plain option rather than offering two identical-looking choices.
                items.append(
                    ListItem(
                        Static(f"For the job: {scene.title} — {pct}% cut of the payout"),
                        id=f"opt_job_onsite_{scene.id}",
                    )
                )
                continue
            for on_site, label in ((True, "on-site"), (False, "support")):
                if not character.job_roster_has_room(scene.id, on_site):
                    continue
                prefix = "opt_job_onsite_" if on_site else "opt_job_support_"
                items.append(
                    ListItem(
                        Static(f"For the job: {scene.title} ({label}) — {pct}% cut of the payout"),
                        id=f"{prefix}{scene.id}",
                    )
                )
        items.append(ListItem(Static("Back"), id="opt_back"))
        return items

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        item_id = event.item.id
        if self.chosen_runner is None:
            if item_id.startswith("meet_"):
                runner = self.app.runner(item_id.removeprefix("meet_"))
                character.meet_runner(runner.id)
                self.notify(f"You exchange numbers with {runner.name}.")
                await self._refresh()
                return
            runner = self.app.runner(item_id.removeprefix("runner_"))
            if not character.on_crew(runner.id):
                self.chosen_runner = runner.id
                await self._refresh()
            return

        runner = self.app.runner(self.chosen_runner)
        if item_id == "opt_indef":
            character.hire_indefinite(runner.id)
            self.notify(f"{runner.name} is on the crew ({runner.daily_cost}eb/day).")
        elif item_id.startswith("opt_job_onsite_"):
            job_scene_id = item_id.removeprefix("opt_job_onsite_")
            if character.hire_for_job(runner.id, job_scene_id, on_site=True, roster=self.app.runners):
                self.notify(f"{runner.name} signed on for the job, on-site.")
        elif item_id.startswith("opt_job_support_"):
            job_scene_id = item_id.removeprefix("opt_job_support_")
            if character.hire_for_job(runner.id, job_scene_id, on_site=False, roster=self.app.runners):
                self.notify(f"{runner.name} signed on for the job, in support.")
        self.chosen_runner = None
        await self._refresh()


class SafehouseScreen(RefreshOnResume, BackScreen):
    """The runner's own apartment or a bought safehouse (corpmap.PLAYER_OWNED_KINDS).
    Nothing to do here until it has a workshop (Location.workshop_built) — the
    apartment starts with one, a safehouse is built here for WORKSHOP_BUILD_COST.
    Once built: install/remove a Mod on an owned weapon or wearable (shops.MOD_CATALOG,
    rolls Armorer), or craft a Consumable from scavenged materials (shops.CRAFT_RECIPES,
    rolls Chemistry)."""

    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="workshop_info")
        yield ListView(id="workshop_actions")
        yield Footer()

    async def _refresh(self) -> None:
        character = self.app.character
        items: list[ListItem] = []
        if not self.location.workshop_built:
            label = f"Build a Workshop — {WORKSHOP_BUILD_COST}eb"
            if character.cash < WORKSHOP_BUILD_COST:
                label += " — can't afford"
            items.append(ListItem(Static(label), id="build_workshop"))
            await _replace_items(self.query_one("#workshop_actions", ListView), items)
            return

        for index, entry in enumerate(character.inventory):
            item = ITEMS_BY_ID[entry.item_id]
            for mod in MOD_CATALOG:
                if item.slot not in mod.applies_to or mod.id in entry.mods:
                    continue
                if len(entry.mods) >= MOD_SLOTS_PER_ITEM:
                    continue
                label = f"Install {mod.name} on {item.name} — {mod.price}eb"
                if character.cash < mod.price:
                    label += " — can't afford"
                items.append(ListItem(Static(label), id=f"mod_{index}_{mod.id}"))
            for mod_id in entry.mods:
                label = f"Remove {MODS_BY_ID[mod_id].name} from {item.name}"
                items.append(ListItem(Static(label), id=f"unmod_{index}_{mod_id}"))
        for consumable_id in CRAFT_RECIPES:
            consumable = CONSUMABLES_BY_ID[consumable_id]
            price = round(consumable.price * 0.5)
            label = f"Craft {consumable.name} — {price}eb"
            if character.cash < price:
                label += " — can't afford"
            items.append(ListItem(Static(label), id=f"craft_{consumable_id}"))
        if not items:
            items.append(ListItem(Static("Nothing to work on."), id="none"))
        await _replace_items(self.query_one("#workshop_actions", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        character = self.app.character
        if item_id == "build_workshop":
            if character.cash < WORKSHOP_BUILD_COST:
                return
            character.cash -= WORKSHOP_BUILD_COST
            build_workshop(self.location)
            self.notify("Workshop built.")
        elif item_id.startswith("mod_"):
            _, index, mod_id = item_id.split("_", 2)
            attempted, message = install_mod(character, int(index), mod_id, self.app.rng)
            if attempted:
                self.app.spend_time(WORKSHOP_HOURS_COST)
            self.notify(message)
        elif item_id.startswith("unmod_"):
            _, index, mod_id = item_id.split("_", 2)
            message = remove_mod(character, int(index), mod_id)
            self.notify(message)
        elif item_id.startswith("craft_"):
            consumable_id = item_id.removeprefix("craft_")
            attempted, message = craft_consumable(character, consumable_id, self.app.rng)
            if attempted:
                self.app.spend_time(WORKSHOP_HOURS_COST)
            self.notify(message)
        else:
            return
        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class RealEstateScreen(RefreshOnResume, BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="realestate_info")
        yield ListView(id="realestate_listings")
        yield Footer()

    async def _refresh(self) -> None:
        character = self.app.character
        territories = self.app.corp_map.territories
        items = []
        for territory_id in self.location.listings:
            territory = territories[territory_id]
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


class HospitalScreen(RefreshOnResume, BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="hospital_info")
        yield ListView(id="hospital_actions")
        yield Footer()

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
        character = self.app.character
        message = hospital_stay(character)
        if message is None:
            self.notify("Can't afford a night's care.", severity="warning")
            return
        self.app.spend_time(HOURS_PER_DAY, skip_night_effects=True)
        # A day in a hospital bed is still a night's sleep — counts as rest the same
        # as app.rest() (halves fatigue, doesn't clear it).
        character.mark_rested()
        self.notify(message)
        self.query_one(CharacterSheet).refresh()
        await self._refresh()


JUNKYARD_ART = r"""
.....................:..:.:............................:::-:::::::::::::::::.:::::::::...:..:...................:::
.:.:.:.:.:.:.:.:.:.:..:...:.:.:.:.:.:.:.:.:.::.:.:.:.:..::::-..:::::.::.:::::::::.::..::..:..:.:.:.:.:.:.:.::::::::
:..:..:..:..:..:..:.:..:.:..:..:..:..:..:..:.::..:..:::::::::....::::::::::.::.:::..:...:..:..:..:..:..:.:::::::::-
..:..:..:..:..:..:..:..:...:..:..:..:..:..:::::::.::::::...:..:..:.:::.:..::.:....:..:.:.:..:..:..:..::.:.:::::::::
.:..:..:..:..:..:..:..:..:...:..:.:::..::..:::::::::::::::::::.:..:...:..:::..::..:..:.:.:..:..:..:.....:..:.::::::
..:..:..:..:..:..:...:..:.:.:..:.:::::::::::::::::..:....:.::::::::::..:....:...:..:..:...:..:..:..::.:..:..:..:..:
..:..:..:..:..:..:.:..:...:..:.:..:::::::.::::....:..:..:..:::::::::::..:...:.:..:..:...:..:..:..:...:.:..:..:..:..
:..:..:..:..:..:..:.:..:.:..:...:.::::::......:.:..:..:.::....::::::::::..:..:.:..:..:.:.:..:..:..:..:..:..:..:..:.
.:..:..:..:..:..:...:..:...:..:..:..::...:.:..:..:..:..:..:.:.:.:::::.:::..:...:..:..:...:..:..:..:.:..:.:..:..:..:
..:..:..:..:..:..:.:..:..:..:..:..:..:::..:.:..:..:..:..:..:...:...:.::::..:.:..:..:..:.:..:..:..:..:..:..:..:..:..
:..:..:..:..:..:..:..:..:.:..:..:..::...:...:..:..:..:..:..:.:..:.:::::::.:..:..:..:..:...:..:..:..:..:..:.:..:..:.
.:..:..:..:..:..:..:..:...:..:..:..::..:.:.:..:..:..:..:..::..:..:..:::::...:..:..:..:..:..:..:..:...:..:..:..:..:.
.:..:..:..:..:..:..:..:.:..:..:..::..:..:.::.:..:..:..:..:.-#+::..:::.:..:.::::..:..:..:.:..:..:..:.:..:..:.::.::.:
..:..:..:..:..:..:..:..:.:..:..:...:..:...:..:.:..:..@.=.#=#=-#.::::::::::::...:..:..:...:..:..:..:..:..::.::.::.::
..:..:..:..:..:..:..:..:..:..:..:..:..:.:..:..:#:=-*:**.+**.++#+.*=+:........:..:..:..:.:..:..:..:.::.:::::::::.:::
.:..:..:..:..:..:..:..:..:.:..:..:..:..:.:..:*:.#%::-+=:=--+*=.=*-+###:..:.:..:..:..:..:..:..:..:.....:.:.::::::::.
.:..:..:..:..:..:..:..:..:..:..:..:..:...+.:-:::-.=:%=.-%.:::*##:#+.:##+:..:..:..:..:..:.:..:..:..:.:..::::::::...:
..:..:..:..:..:..:..:..:..:..:..:..:..-=+:#+#-.*+::--#**#%#==%@%*%*.*=#+%-:..:..:..:..:..:.:..:..:..:......::::.:..
:..:..:..:..:..:..:..:..:..:..:..:..:-:-*##.+-**=-:-*####+-:+=+%=*%%#%%+-=#.#@#:..:..:..:..:.:..:..:..:..:..:::::..
:..:..:..:..:..:..:..:..:..:..:..:....#=#++-%*.%-*:=*=-###+=:+=%*--+.-===+++*..:.:..:..:..:..:.:..:..:.::::...::::.
.:..:..::..:..:..:..:..:..:..:..:..:.:+===:#=+-.++=++.=#=##%**.-.---=#%-:.++=+=:*-.:..:..:..:..:.:..::::::::......:
..:..:....:..:..:..:..:..:..:..:..:+:-=#.#=%=--=.+**###%+#=.===-:--=---.=+#=.#+=+*#*.:..:..:..:..::::....:::::..:..
:.::..:.:..:..:..:..:..:..:..:..:.+--*.+-+=--*+-##=+=#-=#*#++#-:-...+:.::+-:=#-###%++%-++.:..:..:.....:....:::::.:.
.:..:..:.:..:..:..:..:..:..:..:.==#+*=:-:-#-*+-+#--++=#++.:*+--%.::::%:+==-:=%%#+*###@+%%#++:..:..:.:..::.::-:::..:
*-..:..:..:..:..:..:..:..:..:.:==+#%+.#:%##-:##%===++%.##+.-.:-+-*:+===-#=*#**%%#%+%+%=-+%%=+#.-.:..:..::::::--..:.
%*+*..:.::.:..:..:..:..:..::%++..--::%#+#=-+:+*---:-+=-+%+=:==*#..+=#::+%%#**%#-#%**@++#%%.:%#*+##=..:::::---::.:..
%##.#%#.+@..:..:--..:..+-.+:#%+#+####+%-%=+.#+#*=.-+%%#+:.-.-.:+::-=**--+##*=#%+%+-++*:.##%#-**#*##.:.:.::::..:..:.
=+=+=#+#**-+.:=%#=%##-#.-=:-%*#%@+:-=#%=.--=#-%.-..-+=:-.::*.=%+.++#-*+*.:.*%#+##===%+*+#:-=:#.+#%%@.:..:...:..:..:
@=#%%=:##.+*:-%%%+#.:-:.*-+.%-%%+##+..-#+#:+=-+#.::##%%.:=.==-+##*%::=*#++%%%+%*#=%#+==%#%%*%@###+%%@.:..:..:..:...
-=#=@%%=@=*-+-#=*..=-#+%-%%%%%*.+*=#+%.=++:++#%%%:-:==*:..-%-*++-%%%=#:-=#+###-%=#=+%%-#@#%%%%#%%#@@#%#..:.:..:..::
%*@#%=#@*.*===+--*=:=#*+@#@%=-:..@-@*#++%%%:+#%:-%..:.-*%=+==**=:%%+#.:+-%###-@@+*@#@%%%#-#%@@%*@%@#%##.+.%.:::::::
%%#%@-.##@#++:+#%%%#:=#-+%%+--##.%*#%#:+..-%..*@#+-..#*%::+%-+-=%-.--::###+-*=*#@#+*%##@-#.@@%@@%%#%#%*##%#+%..::::
##=#+*@.:..:=#++%#:=*.--%*@#*++-*+.#*:%.=:%%:**:--::#=:--%:#:#*.+=-=:-*@*@.%@%%%++.+##%*#+%@%@@@#=%@@%#@##%%%##::::
##:-#**.=+:-%#+=-:#+=:%%@+:++:.%*=*#=+-*#-+.+-=+%*#%@+**::#.=:++%@%#..:#-##+=-#+-%%@#*#%%%+==-@+%++@%%%@*%@##%%%.::
-+-@@+#--+====:-#+.+--%=*==*==#%#-*%++-+%=.:#+%:@-::.%%@+=.:-%%..#.-#.-=-*#:%*+=*%@@@%%@%%#=-*%%+#**=*#+#%##::#+@.:
=-..#=.:==*.-==.==-##-%=+.+#.#==##@*+#%=+:.=:*#=:#-:=%%%%*+:-:.::::#==-.-*=-##+@#+*###**@%@#%#.%=-#@%###++%###*@#%:
#*=..:-+=#+#.+#:+#%-#-=#--+-##+*#%@#+=+-..-%-----=%:.=*=+:-%=..+-+:*-=:-*-#%%#@@@%%%####@##%*@%%:%=++-#+*#*#%%%%#-%
#+=#-----+.+-+*--.::.#:**++-#:#%*+%-+.::.+#+--:-.+-:@.:*-:..=--=*=%++---:@+..%%%%@++:#+=%%%%*:#=#+*%##%#%%%#%%#%%+%
=*++=+=.+-#-%##++=..=.+###--%#*-####.:*%#.#-=+#+###-+=-:.-++:##-.=+.#%#=*.===+=%#+#++*++==%+=.-+:####%%#%###@%%%##%
@@#--=+#+@%###%#:..#**%+##-#%##=+-:=:.+*#...:**.*#==:-:..:#.-#+#-%+*=#.==##==+=-+%*%+*+%+#%-*%---#+#%@#%%%+%++@+#%%
-#*+:++-#@#%*-=#.+-+++++%#=---**+#@%%@+:=.:-.:.=#......+=++:+=*#@%==**+@%+@==+=*:+%%@+=*-=%:::-:*.*%+-##%##*##*#*+%
""".strip("\n")


class JunkyardScreen(RefreshOnResume, BackScreen):
    BINDINGS = MENU_BACK_BINDINGS
    CSS = """
    #junkyard_scene {
        layers: art overlay;
        height: 1fr;
    }

    #junkyard_art {
        layer: art;
        width: auto;
        height: auto;
    }

    #junkyard_panel {
        layer: overlay;
        width: auto;
        height: auto;
        max-width: 40;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #junkyard_panel ListView {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Container(
            Static(JUNKYARD_ART, id="junkyard_art", markup=False),
            Vertical(
                Static(self.location.name, id="junkyard_info"),
                ListView(id="junkyard_actions"),
                id="junkyard_panel",
            ),
            id="junkyard_scene",
        )
        yield Footer()

    async def _refresh(self) -> None:
        row = ListItem(Static(f"Scavenge the scrap ({SCAVENGE_HOURS_COST} hours)"), id="scavenge")
        await _replace_items(self.query_one("#junkyard_actions", ListView), [row])

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id != "scavenge":
            return
        message = scavenge(self.app.character, self.app.rng)
        self.app.spend_time(SCAVENGE_HOURS_COST)
        self.notify(message)
        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class GangDenScreen(RefreshOnResume, BackScreen):
    """A gang's den (corpmap.LocationKind.GANG_DEN) -- the source of a Smuggling
    delivery (jobs.SmugglingJob). One job at a time: the den just tells the runner
    to come back once they're clear."""

    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, location: Location, gang: Gang) -> None:
        super().__init__()
        self.location = location
        self.gang = gang

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="gang_info")
        yield ListView(id="gang_actions")
        yield Footer()

    async def _refresh(self) -> None:
        character = self.app.character
        standing = character.gang_standing_with(self.gang.id)
        self.query_one("#gang_info", Static).update(
            f"{self.gang.name} — {self.gang.description}  (standing {standing:+d})"
        )
        if character.smuggling_job is not None:
            row = ListItem(Static("Already carrying a job for them — deliver it or wait it out."), id="busy")
        else:
            row = ListItem(Static("Take a delivery job"), id="take_job")
        await _replace_items(self.query_one("#gang_actions", ListView), [row])

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id != "take_job":
            return
        character = self.app.character
        character.smuggling_job = generate_smuggling_job(
            self.gang.id, character.location_id, self.app.corp_map, character.day, self.app.rng
        )
        job = character.smuggling_job
        destination = self.app.corp_map.territories[job.destination_territory_id]
        self.notify(f"{self.gang.name} hands you {job.item}. Get it to {destination.name} by day {job.deadline_day}.")
        await self._refresh()


class CorpHQScreen(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

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

        # The takeover sits under the officers rather than beside them: it's what the
        # exec suite is *for*, and it only appears once the executive will actually see
        # you (there's nobody to make the offer to otherwise). Hidden outright while
        # already running a corp — you can't run two.
        officers = self.query_one("#hq_officers", ListView)
        if self.app.corp_state is not None:
            return
        if not officer_unlocked(character.rep, standing, EXECUTIVE_ROLE):
            return
        if can_take_over(character.rep, standing, character.cash):
            text = f"Move on the board — buy a controlling stake, {TAKEOVER_COST}eb"
        else:
            text = f"Move on the board — locked (needs {takeover_gate(character.rep, standing, character.cash)})"
        await officers.append(ListItem(Static(text), id="takeover"))

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        standing = character.standing_with(self.faction.id)
        if event.item.id == "takeover":
            await self._take_over(character, standing)
            return
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

    async def _take_over(self, character, standing: int) -> None:
        """Buy the corp. Fails closed (no charge, no corp_state) on every gate, the same
        shape corp_turn.py's daily actions use — the row can be selected while locked,
        so the check here is the real one, not the label's."""
        if self.app.corp_state is not None:
            self.notify("You're already running a corp.", severity="warning")
            return
        if not can_take_over(character.rep, standing, character.cash):
            self.query_one("#hq_dialogue", Static).update(
                f"The executive hears you out and passes — come back with "
                f"{takeover_gate(character.rep, standing, character.cash)}."
            )
            return
        character.cash -= TAKEOVER_COST
        self.app.corp_state = CorpState(faction_id=self.faction.id)
        self.notify(f"The board signs. {self.faction.name} is yours.")
        await self._refresh()
        self.query_one("#hq_dialogue", Static).update(
            f"The paperwork takes an afternoon. You walk out running {self.faction.name} — "
            f"and every rival on the map now knows exactly who to come for."
        )


def _cyberware_label(cyberware: Cyberware) -> str:
    """What a piece does, in one parenthesised clause — the cyberware counterpart to
    shops.bonus_text. Smartlink and Datajack carry no bonuses/skill_bonuses at all,
    so each names its own effect rather than rendering as a bare price."""
    parts = [f"+{value} {stat.title()}" for stat, value in cyberware.bonuses.items()]
    parts += [f"+{value} {skill_for(skill_id).name}" for skill_id, value in cyberware.skill_bonuses.items()]
    if cyberware.defense:
        parts.append(f"+{cyberware.defense} soak")
    if cyberware.matrix_action_bonus:
        parts.append(f"+{cyberware.matrix_action_bonus} matrix")
    if cyberware.grants_smartlink:
        parts.append("smartlink")
    return ", ".join(parts)


def _would_end_you(character: Character, cyberware: Cyberware) -> bool:
    """Whether installing this piece would leave nothing of the runner behind.

    install_cyberware only refuses a piece that doesn't *fit* (humanity_cost above
    free_humanity), so a piece costing exactly what's left is legal -- and then the
    operation's own SURGERY_SCARRING tips free Humanity below 0. That is
    cyberpsychosis, and it ends the run. Predicting it here is what lets the shelf
    warn instead of the player finding out by clicking."""
    from shadowguy.character import SURGERY_SCARRING

    return free_humanity(character) - cyberware.humanity_cost - SURGERY_SCARRING <= 0


class RipperdocScreen(PanelNav, RefreshOnResume, BackScreen):
    """A cyber clinic: buy and have cyberware installed, or have a piece cut back out.

    Two panels, the same shape ShopScreen uses: #ripper_stock is what this doc will
    sell you (cybernetics.catalog_for_standing — rows above your standing with the
    contact are hidden outright, exactly as ShopScreen hides them, not shown locked),
    and #ripper_installed is what's already in you, each row an extraction.

    Humanity is the second budget and the reason this isn't just another shop: the
    header carries free_humanity, and a piece that won't fit says so on its own row
    rather than failing silently on click.
    """

    PANEL_IDS = ("ripper_stock", "ripper_installed")
    BINDINGS = [*MENU_BACK_BINDINGS, *PANEL_NAV_BINDINGS]

    CSS = """
    #ripper_stock_panel, #ripper_installed_panel, #ripper_stock, #ripper_installed {
        height: auto;
    }
    """

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name, id="ripper_info")
        yield Collapsible(
            ListView(id="ripper_stock"), title="On the shelf", collapsed=False, id="ripper_stock_panel"
        )
        yield Collapsible(
            ListView(id="ripper_installed"), title="Installed", collapsed=False, id="ripper_installed_panel"
        )
        yield Footer()

    def _contact(self):
        """Whoever at this clinic vouches for you -- the character you stand best with,
        not simply characters[0] the way ShopScreen picks its owner.

        A clinic isn't in SHOP_KINDS, so corpmap_gen._make_characters gives it 1-2
        characters rather than exactly one, and gigs.refresh_gigs assigns a gig to a
        *random* one of them. Reading only the first would mean roughly half a clinic's
        gigs moved a standing the shelf never looked at. Best-of instead: knowing anyone
        at the clinic is what gets you served. Ties break on list order."""
        character = self.app.character
        if not self.location.characters:
            return None
        return max(
            self.location.characters, key=lambda c: character.local_standing_with(c.id)
        )

    def _owner_standing(self) -> int:
        contact = self._contact()
        return self.app.character.local_standing_with(contact.id) if contact else 0

    async def _refresh(self) -> None:
        character = self.app.character
        standing = self._owner_standing()
        owner = self._contact()
        header = self.location.name
        if owner:
            header += f" — {owner.name} ({owner.role}), standing {standing:+d}"
        header += f"\nHumanity: {free_humanity(character):g} of {character.humanity:g} free"
        self.query_one("#ripper_info", Static).update(header)

        stock = []
        for cyberware in catalog_for_standing(standing):
            effect = _cyberware_label(cyberware)
            label = f"{cyberware.name} — {cyberware.price}eb, {cyberware.humanity_cost:g} humanity"
            if effect:
                label += f" ({effect})"
            # Reasons in the order the player can act on them: a full slot is fixed by
            # an extraction below, short Humanity by a different piece, cash by a job.
            if cyberware.slot in character.installed_cyberware:
                label += f" — {cyberware.slot.value} slot full"
            elif cyberware.humanity_cost > free_humanity(character):
                label += " — not enough humanity left"
            elif cyberware.price > character.cash:
                label += " — can't afford"
            elif _would_end_you(character, cyberware):
                # Last, not first: a piece you can't pay for is a cash problem, and
                # saying so is the actionable answer -- warning that an implant you
                # cannot buy would kill you helps nobody. Deliberately still
                # selectable, though. This is the cyberpsychosis cliff and the player
                # is allowed to walk off it, just never by surprise.
                label += " — WOULD BE THE LAST OF YOU"
            stock.append(ListItem(Static(label), id=f"install_{cyberware.id}"))
        if not stock:
            stock = [ListItem(Static("Nothing here they'll sell you."), id="no_stock")]
        await _replace_items(self.query_one("#ripper_stock", ListView), stock)

        installed = [
            ListItem(
                Static(
                    f"Remove {CYBERWARE_BY_ID[cyberware_id].name} — no refund "
                    f"(frees {CYBERWARE_BY_ID[cyberware_id].humanity_cost:g} humanity)"
                ),
                id=f"remove_{slot.value}",
            )
            for slot, cyberware_id in character.installed_cyberware.items()
        ]
        if not installed:
            installed = [ListItem(Static("No chrome in you yet."), id="no_installed")]
        await _replace_items(self.query_one("#ripper_installed", ListView), installed)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        character = self.app.character
        item_id = event.item.id
        if item_id in ("no_stock", "no_installed"):
            return

        if item_id.startswith("install_"):
            cyberware = CYBERWARE_BY_ID[item_id.removeprefix("install_")]
            if install_cyberware(character, cyberware.id, self._owner_standing()):
                if lost_to_cyberpsychosis(character):
                    self.app.exit(
                        message=(
                            f"{character.name} never came off the table. "
                            f"There was nothing left to wake up. Game over."
                        )
                    )
                    return
                self.notify(f"{cyberware.name} installed. It aches, then it's yours.")
            elif cyberware.slot in character.installed_cyberware:
                self.notify(
                    f"Your {cyberware.slot.value} slot is already taken — have it out first.",
                    severity="warning",
                )
            elif cyberware.humanity_cost > free_humanity(character):
                self.notify("There isn't enough of you left to hang that off.", severity="warning")
            else:
                self.notify(f"Can't afford {cyberware.name}.", severity="warning")
        elif item_id.startswith("remove_"):
            slot = CyberSlot(item_id.removeprefix("remove_"))
            removed = remove_cyberware(character, slot)
            if removed is not None:
                self.notify(f"{CYBERWARE_BY_ID[removed].name} is out. No refund.")

        self.query_one(CharacterSheet).refresh()
        await self._refresh()
