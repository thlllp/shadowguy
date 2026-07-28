from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import HOURS_PER_DAY, Character
from shadowguy.corpmap import (
    Location,
    LocationKind,
    add_safehouse,
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
from shadowguy.fixer import Fixer, JobOffer
from shadowguy.gangs import Gang
from shadowguy.jobs import generate_smuggling_job
from shadowguy.rivals import RunnerActivity
from shadowguy.security import SecurityContract
from shadowguy.runners import RUNNERS_BY_ID, recruit_cut, recruit_wage
from shadowguy.skills import skill_value
from shadowguy.shops import (
    CATALOG,
    CONSUMABLE_CATALOG,
    CONSUMABLES_BY_ID,
    HOSPITAL_STAY_COST,
    ITEMS_BY_ID,
    PROGRAM_CATALOG,
    SCAVENGE_HOURS_COST,
    bonus_text,
    buy_consumable,
    buy_item,
    buy_price,
    buy_program,
    hospital_stay,
    scavenge,
    sell_item,
    sell_price,
)

from . import (
    MENU_BACK_BINDINGS,
    PANEL_NAV_BINDINGS,
    BackScreen,
    CharacterSheet,
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
        offers = self.query_one("#offers", ListView)
        await _replace_items(offers, items)
        first_id = items[0].id if items else None
        if items:
            offers.index = 0
        self._show_roles(first_id)

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
        offer = next(offer for offer in self.fixer.offers if offer.id == item_id)
        if offer.taken_by is not None:
            self.notify(
                f"{RUNNERS_BY_ID[offer.taken_by].name} already took that one.", severity="warning"
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
            runner = RUNNERS_BY_ID[self.chosen_runner]
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
            and state.activity is RunnerActivity.DRINKING
        )

    def _roster_items(self) -> list[ListItem]:
        character = self.app.character
        items = []
        for runner in self.app.runners:
            # A runner who died on one of your jobs is gone for the run, and one who was
            # picked up at a scene is inside until their day comes round — neither is at
            # the bar to hire (Character.runner_available).
            if not character.runner_available(runner.id):
                continue
            tag = f"{runner.name} ({runner.archetype}, rating {runner.rating})"
            if character.on_crew(runner.id):
                label = f"{tag} — on your crew"
                item_id = f"runner_{runner.id}"
            elif character.knows_runner(runner.id):
                label = f"Recruit {tag}"
                item_id = f"runner_{runner.id}"
            elif self._runner_here(runner.id):
                label = f"{runner.name} is here, nursing a drink — exchange numbers?"
                item_id = f"meet_{runner.id}"
            else:
                label = f"{tag} — no way to reach them yet"
                item_id = f"locked_{runner.id}"
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
            if item_id.startswith("locked_"):
                return
            if item_id.startswith("meet_"):
                runner = RUNNERS_BY_ID[item_id.removeprefix("meet_")]
                character.meet_runner(runner.id)
                self.notify(f"You exchange numbers with {runner.name}.")
                await self._refresh()
                return
            runner = RUNNERS_BY_ID[item_id.removeprefix("runner_")]
            if not character.on_crew(runner.id):
                self.chosen_runner = runner.id
                await self._refresh()
            return

        runner = RUNNERS_BY_ID[self.chosen_runner]
        if item_id == "opt_indef":
            character.hire_indefinite(runner.id)
            self.notify(f"{runner.name} is on the crew ({runner.daily_cost}eb/day).")
        elif item_id.startswith("opt_job_onsite_"):
            job_scene_id = item_id.removeprefix("opt_job_onsite_")
            if character.hire_for_job(runner.id, job_scene_id, on_site=True):
                self.notify(f"{runner.name} signed on for the job, on-site.")
        elif item_id.startswith("opt_job_support_"):
            job_scene_id = item_id.removeprefix("opt_job_support_")
            if character.hire_for_job(runner.id, job_scene_id, on_site=False):
                self.notify(f"{runner.name} signed on for the job, in support.")
        self.chosen_runner = None
        await self._refresh()
        await self._refresh()


class SafehouseScreen(BackScreen):
    BINDINGS = MENU_BACK_BINDINGS

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.location.name)
        yield Static("Your place. Nothing to do here yet.")
        yield Footer()


class RealEstateScreen(BackScreen):
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


class HospitalScreen(BackScreen):
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


class JunkyardScreen(BackScreen):
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

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

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


class GangDenScreen(BackScreen):
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

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

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
