from textual.app import ComposeResult
from textual.containers import Container, Vertical
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.character import HOURS_PER_DAY
from shadowguy.corpmap import (
    Location,
    LocationKind,
    add_safehouse,
    has_home,
    safehouse_price,
)
from shadowguy.factions import FACTIONS_BY_ID, Faction, officer_dialogue, officer_gate, officer_unlocked
from shadowguy.fixer import Fixer
from shadowguy.security import SecurityContract
from shadowguy.runners import RIVAL_RUNNERS, RUNNERS_BY_ID, recruit_cut, recruit_wage
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
            ListItem(
                Static(
                    f"{offer.scene.title} ({offer.scene.hours_cost}h) — {offer.timing.label}"
                    f"{matrix_warning(character, offer.scene)}"
                ),
                id=offer.id,
            )
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

    def _roster_items(self) -> list[ListItem]:
        character = self.app.character
        items = []
        for runner in RIVAL_RUNNERS:
            tag = f"{runner.name} ({runner.archetype}, rating {runner.rating})"
            label = f"{tag} — on your crew" if character.on_crew(runner.id) else f"Recruit {tag}"
            items.append(ListItem(Static(label), id=f"runner_{runner.id}"))
        return items

    def _terms_items(self, runner) -> list[ListItem]:
        leadership = skill_value(self.app.character, "leadership")
        wage = recruit_wage(runner, leadership)
        items = [
            ListItem(Static(f"Keep on indefinitely — {wage}eb/day"), id="opt_indef")
        ]
        pct = round(recruit_cut(runner, leadership) * 100)
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
        character.last_rest_hour = character.elapsed_hours
        character.fatigue //= 2
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
