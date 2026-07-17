import random

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.color import Color
from textual.containers import Grid, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, ListItem, ListView, Static

from shadowguy.archetypes import ARCHETYPES, ARCHETYPES_BY_ID
from shadowguy.character import CORE_STATS, MAX_SKILL_RANK, Character
from shadowguy.checks import CheckResult
from shadowguy.combat import (
    Action,
    CombatOutcome,
    CombatState,
    Drop,
    ENEMY_TIERS,
    available_actions,
    drop_for_result,
    roll_enemies,
    start_combat,
    take_turn,
)
from shadowguy.corpmap import (
    MODIFIER_LABELS,
    MODIFIER_MAX,
    OWNER_COLORS,
    PLAYER_OWNED_KINDS,
    SHOP_KINDS,
    LocalCharacter,
    Location,
    LocationKind,
    RenderedMap,
    Territory,
    add_safehouse,
    generate_corp_map,
    has_home,
    lodging_cost,
    owner_label,
    render_ascii_map,
    safehouse_price,
)
from shadowguy.factions import (
    FACTIONS,
    FACTIONS_BY_ID,
    Faction,
    officer_dialogue,
    officer_gate,
    officer_unlocked,
)
from shadowguy.fixer import Fixer, create_fixers, discover_fixers_here, expire_offers, refresh_offers
from shadowguy.gigs import GIG_FAIL_REP_HIT, GIG_FAIL_STANDING_HIT, refresh_gigs
from shadowguy.jobs import JOB_FAILURE_REP_HIT, JOB_FAILURE_TRUST_HIT, generate_legwork_for_job
from shadowguy.runners import RIVAL_RUNNERS, RUNNERS_BY_ID
from shadowguy.saves import SaveSlot, list_saves, load_game, save_game
from shadowguy.scene import (
    Encounter,
    Outcome,
    Scene,
    SceneKind,
    TacticalStage,
    apply_outcome,
    resolve_choice,
)
from shadowguy.tactical import (
    Side,
    TacticalOutcome,
    TacticalState,
    Tile,
    best_shot,
    end_turn,
    generate_map,
    leave,
    move_player,
    player_attack,
    start_tactical,
)
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
    toggle_equip,
    use_consumable,
)
from shadowguy.skills import SKILLS, skill_for, skill_value


async def _replace_items(list_view: ListView, items: list[ListItem], index: int = 0) -> None:
    await list_view.clear()
    for item in items:
        list_view.append(item)
    # Callers that repopulate under the player's cursor (rather than switching to a
    # fresh list) pass the row to keep highlighted — otherwise the cursor snaps to
    # the top and the next `enter` acts on a row the player never selected.
    list_view.index = min(index, len(items) - 1) if items else 0


async def _populate_list(
    list_view: ListView,
    entries: list,
    *,
    id_prefix: str,
    label,
    empty_label: str | None = None,
    empty_id: str = "",
) -> None:
    """Render `entries` as ListItems (id `id_prefix + entry.id`, text from `label(entry)`),
    or a single empty-state row when there are none."""
    if entries:
        items = [ListItem(Static(label(entry)), id=f"{id_prefix}{entry.id}") for entry in entries]
    else:
        items = [ListItem(Static(empty_label), id=empty_id)] if empty_label else []
    await _replace_items(list_view, items)


class CharacterSheet(Static):
    def __init__(self, character: Character) -> None:
        super().__init__()
        self.character = character

    def render(self) -> str:
        c = self.character
        standings = "  ".join(
            f"{f.name.split()[0]}: {c.standing_with(f.id):+d}" for f in FACTIONS
        )
        gear = ", ".join(
            ITEMS_BY_ID[entry.item_id].name if entry.equipped else f"{ITEMS_BY_ID[entry.item_id].name} (stowed)"
            for entry in c.inventory
        ) or "none"
        return (
            f"{c.name}\n"
            f"Day {c.day}   Stamina: {c.stamina}/{c.max_stamina}   Health: {c.health}/{c.max_health}\n"
            # Two lines, not one: six stats overflow the 60 columns this widget gets
            # beside MainMenu's sidebar, and a wrapped line silently eats a row of
            # the activity list under it.
            f"Body: {c.stat('body')}  Strength: {c.stat('strength')}  Agility: {c.stat('agility')}\n"
            f"Perception: {c.stat('perception')}  Intelligence: {c.stat('intelligence')}  "
            f"Cool: {c.stat('cool')}\n"
            f"Cash: {c.cash}eb   Rep: {c.rep}\n"
            f"Standing — {standings}\n"
            f"Gear: {gear}"
        )



# Shared left/right panel navigation for the multi-ListView screens. A ListView
# only binds up/down/enter, so left/right bubble to the screen unused — bind them
# there to move focus between panels and the whole screen is keyboard-navigable
# without Tab or the mouse. up/down still move the highlight within the focused
# list. The bindings can't live on the mixin (Textual doesn't merge BINDINGS from
# a plain mixin's MRO), so each screen splices PANEL_NAV_BINDINGS into its own
# BINDINGS; the mixin only supplies the action, found by ordinary attribute lookup.
PANEL_NAV_BINDINGS = [
    ("left", "focus_panel(-1)", "Prev panel"),
    ("right", "focus_panel(1)", "Next panel"),
]


class PanelNav:
    """Mixin: cycle focus through PANEL_IDS (in visual order) with left/right."""

    PANEL_IDS: tuple[str, ...] = ()

    def action_focus_panel(self, step: int) -> None:
        panels = [self.query_one(f"#{pid}", ListView) for pid in self.PANEL_IDS]
        focused = self.focused
        current = next((i for i, panel in enumerate(panels) if panel is focused), 0)
        panels[(current + step) % len(panels)].focus()


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
    #sidebar {
        width: 20;
        border: solid $accent;
        padding: 1;
    }

    #main_panel {
        width: 1fr;
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
        yield Horizontal(
            Vertical(ListView(id="categories"), id="sidebar"),
            Vertical(CharacterSheet(self.app.character), ListView(id="activities"), id="main_panel"),
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
        """A job (and its legwork) can only be run in the district it targets."""
        return self.app.character.location_id == scene.target_territory_id

    def _district(self, scene: Scene) -> str:
        return self.app.corp_map.territories[scene.target_territory_id].name

    async def _refresh(self) -> None:
        self.query_one(CharacterSheet).refresh()
        character = self.app.character
        items = []

        if self.selected_category == "gig":
            # One gig per local location, each owned by one of that location's
            # characters — street work you self-select into, gated only by being here.
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
                items.append(ListItem(Static(label), id=f"job_{job.id}"))

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
            items.append(
                ListItem(
                    Static(f"{here.name} — {owner_label(here.owner)}"),
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
                            Static(f"  {fixer.name} — {fixer.specialty} ({len(fixer.offers)} jobs available)"),
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

        if item_id == "end_day":
            here = self.app.corp_map.territories[character.location_id]
            # Lodging for the night, unless the runner owns a place here (their home or
            # a safehouse — then it's free). Pay what they can: resting must never be
            # blocked, and cash is kept off negative like everywhere else.
            cost = lodging_cost(here)
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
            # apply_outcome subtracts a losing bet straight off Character.cash, so a
            # scene the runner can't cover is refused here, not floored on the way out.
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
                # The HQ sits in its owner's district, so here.owner is the corp whose
                # officers (and rep/standing gates) this screen reads.
                self.app.push_screen(CorpHQScreen(location, FACTIONS_BY_ID[here.owner]))
            elif location.kind == LocationKind.BAR:
                self.app.push_screen(BarScreen(location))
            elif location.kind in PLAYER_OWNED_KINDS:
                self.app.push_screen(SafehouseScreen(location))
            return

    async def _select_category(self, key: str) -> None:
        if key == "contacts":
            self.app.push_screen(ContactsScreen())
            return
        if key == "map":
            self.app.push_screen(CorpMapScreen())
            return
        if key == "gear":
            self.app.push_screen(InventoryScreen())
            return
        if key == "skills":
            self.app.push_screen(SkillsScreen())
            return
        self.selected_category = key
        await self._refresh()


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
        # The highlighted offer's crew roles — the beats it's made of and who fits each.
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
        # ListView.index goes None after a clear; re-seat it so the roles panel and the
        # keyboard both have something highlighted to act on (see Known Textual gotchas).
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
        """Standing with the shop's owner (its single character). Bends every price
        here (shops.buy_price/sell_price). Defaults to 0 if the shop somehow has no
        owner, which is the neutral, no-effect case."""
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
    """A bar (LocationKind.BAR): where you hire runners onto your crew. Two-level menu —
    pick a runner (runners.RIVAL_RUNNERS), then the terms:

    - **Indefinitely** — they stay on the crew and draw `daily_cost` every rest (miss
      payroll and they walk; see app.advance_day).
    - **For a job** — signed to one accepted job for `job_cut` of *that job's* payout,
      taken when it pays (SceneScreen._take_crew_cut); the engagement ends with the job.

    Neither costs anything upfront — the price is the wage or the cut, paid later. Assigning
    a hire to a specific role (with the one-remote-support cap) is still a later increment.

    First-slice simplification: the whole roster is hireable at any bar. Seating runners at
    particular bars (the way fixers are seated to territories) is the natural next step."""

    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]

    def __init__(self, location: Location) -> None:
        super().__init__()
        self.location = location
        # None = showing the roster; a runner id = showing that runner's engagement terms.
        self.chosen_runner: str | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="bar_info")
        yield ListView(id="bar_runners")
        yield Footer()

    async def action_back(self) -> None:
        # Escape backs out of the terms menu first, then out of the bar.
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
    """A place the runner owns — their starting apartment, or a safehouse they bought.
    A stub for now: functions (rest, stash, ...) land here in later steps."""

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
    """A real estate office's cross-map listing of safehouses for sale. Buying one adds
    a safehouse to that district (corpmap.add_safehouse) — a place the runner owns, so
    lodging there goes free (lodging_cost). Districts the runner already owns a place in
    drop off the listing (has_home)."""

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
            if has_home(territory):
                continue  # already the runner's — not for sale
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


class HospitalScreen(Screen):
    """A hospital heals over time: each day you stay in the ward you pay HOSPITAL_STAY_COST
    and heal 1d6 + Body (shops.hospital_stay). A stay *is* a day, so it turns the run over
    like any other rest (app.advance_day). It's the main way health comes back — resting
    elsewhere doesn't heal, and a Health Kit is only a small one-off top-up."""

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
        # A stay is a day — turn the run over around the care, same as any rest.
        self.app.advance_day()
        self.notify(message)
        self.query_one(CharacterSheet).refresh()
        await self._refresh()


class CorpHQScreen(Screen):
    """A corp's headquarters: talk to its officers, if your reputation opens the door.

    Each officer's role is a rung on factions.CORP_OFFICER_TIERS (factions.officer_unlocked
    gates by role, not list position); reaching one needs both the street rep and the
    standing with this corp that its role demands. Talking is flavor only for now — a
    locked officer shows what you'd need, an unlocked one gives you a line themed on the
    corp and how it feels about you. Nothing here changes the run yet; it's the hook the
    corp-side game hangs on.
    """

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

        def label(officer: LocalCharacter) -> str:
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


def _compact_skill_label(character: Character, skill, show_cost: bool = False) -> str:
    """Two-line row text for the stat-column grids — the read-only SkillsScreen and
    the interactive creation screen both use it. The column header already names the
    stat and there's no room for the flavor description; two explicit lines rather
    than one long one because the longest skill names don't fit an 80-column
    terminal's 3-wide columns, and a deliberate break reads better than whatever
    mid-word point Rich's auto-wrap would pick. `show_cost` appends the next-rank
    price for the creation screen, where a point is about to be spent; SkillsScreen
    is read-only and leaves it off."""
    rank = character.skill_rank(skill.id)
    value = skill_value(character, skill.id)
    detail = f"  rank {rank}/{MAX_SKILL_RANK}  value {value}"
    if show_cost:
        cost = character.next_rank_cost(skill.id)
        detail += "  MAX" if cost is None else f"  next +{cost}"
    return f"{skill.name}\n{detail}"


class CharacterCreationScreen(PanelNav, Screen):
    """Build the runner. Both point pools are spent here and never refill.

    Laid out like SkillsScreen: a 3x2 grid of stat columns, each column raising its
    core stat (top row) and then that stat's skills. The archetype fast-path sits
    above as a row of bordered cards. left/right walks the cards, then the columns,
    then the Begin bar (PanelNav); up/down and enter spend within the focused panel.
    """

    PANEL_IDS = (
        *(f"arch_card_{archetype.id}" for archetype in ARCHETYPES),
        *(f"build_list_{stat}" for stat in CORE_STATS),
        "begin_row",
    )

    BINDINGS = [
        ("q", "quit_menu", "Menu"),
        ("r", "reset", "Reset build"),
        ("b", "begin", "Begin run"),
        *PANEL_NAV_BINDINGS,
    ]

    CSS = """
    #pools {
        padding: 0 1;
    }

    #arch_grid {
        grid-size: 3 1;
        grid-gutter: 0 1;
        height: auto;
    }

    .arch_card {
        height: auto;
        border: round $accent;
        padding: 0 1;
    }

    .arch_card:focus {
        border: round $secondary;
    }

    #build_scroll {
        height: 1fr;
    }

    #build_grid {
        grid-size: 3 2;
        grid-gutter: 1 2;
        height: auto;
    }

    .build_column {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    .build_column ListView {
        height: auto;
    }

    .build_column ListView:focus {
        background: $boost;
    }

    #begin_row {
        height: auto;
        border: round $success;
    }
    """

    def _arch_card(self, archetype) -> ListView:
        """One archetype as a bordered card: the border-title names it, the single
        selectable row is its pitch. Selecting it applies the preset."""
        card = ListView(
            ListItem(Static(archetype.description), id=f"arch_{archetype.id}"),
            id=f"arch_card_{archetype.id}",
            classes="arch_card",
        )
        card.border_title = archetype.name
        return card

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(id="pools")
        yield Grid(*(self._arch_card(archetype) for archetype in ARCHETYPES), id="arch_grid")
        yield ScrollableContainer(
            Grid(
                *(
                    Vertical(
                        Static(id=f"build_head_{stat}"),
                        ListView(id=f"build_list_{stat}"),
                        classes="build_column",
                    )
                    for stat in CORE_STATS
                ),
                id="build_grid",
            ),
            id="build_scroll",
        )
        yield ListView(id="begin_row")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def _unspent(self) -> int:
        character = self.app.character
        return character.stat_points + character.skill_points

    async def action_reset(self) -> None:
        self.app.character.reset_build()
        self.query_one(CharacterSheet).refresh()
        await self._refresh()

    def action_begin(self) -> None:
        if self._unspent():
            self.notify("Spend every point before the run starts.", severity="warning")
            return
        # switch, not push: the build is locked once the run begins, so there is
        # no screen to come back to.
        self.app.switch_screen(MainMenu())

    def _update_pools(self) -> None:
        character = self.app.character
        self.query_one("#pools", Static).update(
            f"Stat points: {character.stat_points}   Skill points: {character.skill_points}"
            "   —   enter spends · left/right change panel · r resets · b begins"
        )

    async def _refresh_column(self, stat: str, index: int = 0) -> None:
        """Rebuild one stat column: its header, the stat-raise row, then its skills.
        A single spend only touches one column (a stat change moves that stat's skill
        values, a skill change moves only its own row), so callers refresh just the
        affected column and leave the other five — and their cursors — alone."""
        character = self.app.character
        self.query_one(f"#build_head_{stat}", Static).update(f"{stat.capitalize()} — {character.stat(stat)}")
        items = [ListItem(Static(f"Raise {stat.capitalize()}\n  1 stat point"), id=f"stat_{stat}")]
        items += [
            ListItem(Static(_compact_skill_label(character, skill, show_cost=True)), id=f"skill_{skill.id}")
            for skill in SKILLS
            if skill.stat == stat
        ]
        await _replace_items(self.query_one(f"#build_list_{stat}", ListView), items, index)

    async def _refresh_begin(self) -> None:
        unspent = self._unspent()
        label = "Begin run" if not unspent else f"Begin run — {unspent} points unspent"
        await _replace_items(self.query_one("#begin_row", ListView), [ListItem(Static(label), id="begin")])

    async def _refresh(self) -> None:
        self._update_pools()
        for stat in CORE_STATS:
            await self._refresh_column(stat)
        await self._refresh_begin()

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id == "begin":
            self.action_begin()
            return

        character = self.app.character

        if item_id.startswith("arch_"):
            archetype = ARCHETYPES_BY_ID[item_id.removeprefix("arch_")]
            # Reset first: a preset is the whole build, not a top-up on whatever the
            # player already spent — otherwise picking one twice, or after hand-spending,
            # would run the pools dry part-way through and leave a half-applied runner.
            character.reset_build()
            archetype.apply(character)
            self.notify(f"{archetype.name} build applied. Press b to begin, r to start over.")
            self.query_one(CharacterSheet).refresh()
            await self._refresh()
            return
        # Keep the cursor on the row the player is spending into: the column is rebuilt
        # after every point, and a snap back to the top would sink the next `enter`
        # into the stat-raise row instead of whatever they were actually looking at.
        index = event.list_view.index or 0
        if item_id.startswith("stat_"):
            stat = item_id.removeprefix("stat_")
            if not character.spend_stat_point(stat):
                self.notify("No stat points left.", severity="warning")
        else:
            skill_id = item_id.removeprefix("skill_")
            stat = skill_for(skill_id).stat
            # Three different refusals. Read the cost before spending so a maxed skill
            # and a merely unaffordable one don't both report "no points left" —
            # ranks 8+ cost 3-4 points, so "can't afford" happens with points in hand.
            name = skill_for(skill_id).name
            cost = character.next_rank_cost(skill_id)
            if cost is None:
                self.notify(f"{name} is already at rank {MAX_SKILL_RANK}.", severity="warning")
            elif not character.spend_skill_point(skill_id):
                self.notify(
                    f"{name} rank {character.skill_rank(skill_id) + 1} costs {cost} points; "
                    f"you have {character.skill_points}.",
                    severity="warning",
                )
        # Only the spent-into stat's column changes (its header value and/or one skill
        # row); the pools and Begin bar track the shrinking point total.
        self.query_one(CharacterSheet).refresh()
        self._update_pools()
        await self._refresh_column(stat, index)
        await self._refresh_begin()


class ContactsScreen(PanelNav, Screen):
    """Read-only: who you know and how they feel about you, split into the three
    kinds of NPC the game tracks — Fixers (trust), Corps (standing), and Runners
    (identity only; see runners.py for why there's no relationship value there yet).
    Three panels rather than one mixed list, so each kind reads as its own thing.
    """

    PANEL_IDS = ("fixers_list", "corps_list", "locals_list", "runners_list")
    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back"), *PANEL_NAV_BINDINGS]

    CSS = """
    #fixers_panel, #corps_panel, #locals_panel, #runners_panel {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    #fixers_list, #corps_list, #locals_list, #runners_list {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static("Fixers"),
            ListView(id="fixers_list"),
            id="fixers_panel",
        )
        yield Vertical(
            Static("Corps"),
            ListView(id="corps_list"),
            id="corps_panel",
        )
        yield Vertical(
            Static("Locals"),
            ListView(id="locals_list"),
            id="locals_panel",
        )
        yield Vertical(
            Static("Runners"),
            ListView(id="runners_list"),
            id="runners_panel",
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

        # Only fixers you've actually worked for — a fixer you've never done a job
        # for isn't a contact yet, just someone you could look up in person (see the
        # Local tab, which is location-gated instead of trust-gated).
        established = [fixer for fixer in self.app.fixers if character.trust_with(fixer.id) > 0]
        await _populate_list(
            self.query_one("#fixers_list", ListView),
            established,
            id_prefix="fixer_",
            label=lambda fixer: (
                f"{fixer.name} — {fixer.specialty} "
                f"(trust {character.trust_with(fixer.id):+d}, {len(fixer.offers)} jobs available)"
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

        # Only locals whose regard you've actually moved — same rule as fixers. A
        # character you've never done a gig for isn't a contact, just someone behind
        # a counter. Location captured per-id for the label.
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
    """Read-only once the run starts: skill ranks are bought at creation, not in play.

    Laid out as a 3x2 grid, one column per core stat, so the six faculties read
    as six groups instead of one 31-row list.
    """

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


class SceneScreen(Screen):
    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self, scene: Scene) -> None:
        super().__init__()
        self.scene = scene
        self.stage_id = scene.start_stage
        self.awaiting_continue = False
        self._pending_next_stage: str | None = None
        # The result of the check that routed us at the next stage. Only read when
        # that stage turns out to be a fight, where it decides who got the drop
        # (combat.drop_for_result) — a made ambush opens with a free round, a nat-1
        # hands one to them.
        self._pending_result: CheckResult | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static(self._current_stage().prompt, id="prompt"),
            ListView(id="choices"),
            id="scene_body",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._show_stage()

    def _current_stage(self):
        return self.scene.stages[self.stage_id]

    async def _show_stage(self) -> None:
        self.awaiting_continue = False
        stage = self._current_stage()
        self.query_one("#prompt", Static).update(stage.prompt)
        items = [ListItem(Static(choice.label), id=f"choice_{i}") for i, choice in enumerate(stage.choices)]
        await _replace_items(self.query_one("#choices", ListView), items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.awaiting_continue:
            await self._advance()
            return

        stage = self._current_stage()
        index = int(event.item.id.removeprefix("choice_"))
        choice = stage.choices[index]

        character = self.app.character
        result, outcome = resolve_choice(character, self.scene, choice)
        self._take_crew_cut(outcome)
        self.query_one(CharacterSheet).refresh()

        prompt = self.query_one("#prompt", Static)
        prompt.update(f"{result.name}: {outcome.text}")

        if result in (CheckResult.CRITICAL_SUCCESS, CheckResult.CRITICAL_FAILURE):
            flash = "green" if result is CheckResult.CRITICAL_SUCCESS else "red"
            prompt.styles.background = Color.parse(flash)
            prompt.styles.animate("background", value=Color(0, 0, 0, 0), duration=0.6)

        if not character.is_alive:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return

        await self._await_continue(outcome.next_stage, result)

    async def _await_continue(self, next_stage: str | None, result: CheckResult | None) -> None:
        """Arm the Continue row that carries the scene to next_stage on select."""
        self._pending_next_stage = next_stage
        self._pending_result = result
        await _replace_items(self.query_one("#choices", ListView), [ListItem(Static("Continue"), id="continue")])
        self.awaiting_continue = True

    async def _show_combat(self, stage) -> None:
        """Hand the stage over to CombatScreen and pick the scene back up after."""
        self.stage_id = stage.id
        self.app.push_screen(
            CombatScreen(stage.combat, drop_for_result(self._pending_result)),
            self._on_combat_end,
        )

    async def _show_tactical(self, stage) -> None:
        """Hand a tactical-map stage over to TacticalScreen and resume the scene after.
        The same handoff as combat — the drop doesn't apply (a tactical fight opens on
        the player's turn), so unlike _show_combat it reads no _pending_result."""
        self.stage_id = stage.id
        self.app.push_screen(TacticalScreen(stage.tactical), self._on_tactical_end)

    async def _on_tactical_end(self, result: TacticalOutcome) -> None:
        character = self.app.character
        if result is TacticalOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return
        stage = self._current_stage()
        # Winning clears the stage (victory.next_stage rejoins the job / carries the last
        # stage's payout); slipping out by an exit ends the scene there, like a fled fight.
        outcome = stage.tactical.victory if result is TacticalOutcome.VICTORY else stage.tactical.escape
        await self._finish_fight(outcome)

    async def _on_combat_end(self, result: CombatOutcome) -> None:
        character = self.app.character
        if result is CombatOutcome.DEAD:
            self.app.exit(message=f"{character.name} has died. Game over.")
            return

        if result is CombatOutcome.KNOCKED_OUT:
            roll = self.app.rng.randint(1, 6)
            if roll <= 2:
                self.app.exit(message=f"{character.name} didn't wake up. Game over.")
                return
            # Wake up alive but badly off: lose half your cash, 1 health.
            character.cash //= 2
            character.health = 1
            msg = "Most of your creds are gone." if roll <= 4 else "At least you're alive."
            self.notify("You came to in an alley. " + msg)
            # The job/gig is blown — pop back to MainMenu. This bypasses apply_outcome
            # (there's no Outcome for waking up in an alley), so the same failure
            # penalty jobs.py/gigs.py apply on a blown scene has to be repeated here.
            if self.scene.kind == SceneKind.JOB:
                character.adjust_fixer_trust(self.scene.target_fixer_id, JOB_FAILURE_TRUST_HIT)
                character.adjust_rep(JOB_FAILURE_REP_HIT)
                character.remove_job(self.scene.id)
            elif self.scene.kind == SceneKind.GIG:
                character.adjust_local_standing(self.scene.target_character_id, GIG_FAIL_STANDING_HIT)
                character.adjust_rep(GIG_FAIL_REP_HIT)
                self.app.location_gigs.pop(self.scene.target_location_id, None)
            self.app.pop_screen()
            return

        stage = self._current_stage()
        # Winning is a way *past* the stage (victory.next_stage rejoins the job, and on
        # the last stage it carries the payout); running out ends the scene there.
        outcome = stage.combat.victory if result is CombatOutcome.VICTORY else stage.combat.escape
        await self._finish_fight(outcome)

    async def _finish_fight(self, outcome) -> None:
        """Apply a fight's winning/slipping-out Outcome and resume the scene — the shared
        tail of _on_combat_end and _on_tactical_end. result=None on the continue: no check
        routed us out of a fight, so a chained fight opens even (Drop.NONE) rather than
        inheriting the drop of the check that opened this one."""
        apply_outcome(self.app.character, outcome, self.scene)
        self._take_crew_cut(outcome)
        self.query_one(CharacterSheet).refresh()
        self.query_one("#prompt", Static).update(outcome.text)
        await self._await_continue(outcome.next_stage, None)

    def _take_crew_cut(self, outcome) -> None:
        """When a job's final payout lands, each runner signed *for this job* takes their cut
        of it off the top. Fires only on a JOB's paying end (an outcome that ends the scene
        with cash) — nothing for gigs, legwork, or a blown/failed finish. The for-job hires
        are discharged later, when _advance calls Character.remove_job."""
        if self.scene.kind is not SceneKind.JOB or outcome.next_stage is not None or outcome.cash_delta <= 0:
            return
        character = self.app.character
        for hire in character.crew_for_job(self.scene.id):
            runner = RUNNERS_BY_ID[hire.runner_id]
            cut = min(int(runner.job_cut * outcome.cash_delta), character.cash)
            character.cash -= cut
            self.notify(f"{runner.name} takes {cut}eb — their cut of the job.")

    async def _advance(self) -> None:
        if self._pending_next_stage is None:
            # Includes fleeing a fight: the job is over, and over is over — a blown
            # contract leaves accepted_jobs the same way a finished one does.
            if self.scene.kind == SceneKind.JOB:
                self.app.character.remove_job(self.scene.id)
            elif self.scene.kind == SceneKind.GIG:
                # A gig is one-shot: spent whether won or blown, and a fresh one spawns
                # at that location on the next rest (refresh_gigs).
                self.app.location_gigs.pop(self.scene.target_location_id, None)
            self.app.pop_screen()
            return

        stage = self.scene.stages[self._pending_next_stage]
        if stage.combat is not None:
            await self._show_combat(stage)
            return
        if stage.tactical is not None:
            await self._show_tactical(stage)
            return

        self.stage_id = self._pending_next_stage
        await self._show_stage()


# How many lines of the fight scroll back. The screen has to hold the enemy panel,
# the log and the action list inside 24 rows, and the log is the elastic one.
COMBAT_LOG_LINES = 8


class CombatScreen(Screen):
    """One fight, one round at a time.

    Pushed by SceneScreen for a stage with an Encounter, and dismissed with the
    CombatOutcome — the scene, not this screen, decides what winning or running was
    worth (it owns the Encounter's Outcomes). All the rules live in combat.py; this
    only renders CombatState and feeds it the action the player picked.
    """

    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self, encounter: Encounter, drop: Drop) -> None:
        super().__init__()
        self.encounter = encounter
        self.drop = drop
        self.state: CombatState | None = None
        self.actions: list[Action] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Vertical(
            Static(self.encounter.prompt, id="prompt"),
            Static(id="enemies"),
            Static(id="combat_log"),
            ListView(id="actions"),
            id="combat_body",
        )
        yield Footer()

    async def on_mount(self) -> None:
        self.state = start_combat(
            self.app.character, self.encounter.enemies, self.drop, self.app.rng
        )
        await self._refresh()

    def _enemy_text(self) -> Text:
        lines = []
        for fighter in self.state.fighters:
            if not fighter.is_standing:
                lines.append(f"  {fighter.enemy.name}: down")
            else:
                stunned = " (reeling)" if fighter.stunned_rounds else ""
                lines.append(
                    f"  {fighter.enemy.name}: {fighter.health}/{fighter.enemy.health}{stunned}"
                )
        return Text("\n".join(lines))

    async def _refresh(self) -> None:
        state = self.state
        self.query_one(CharacterSheet).refresh()
        self.query_one("#enemies", Static).update(self._enemy_text())
        # Text, not str: enemy names and weapon names are arbitrary content and a
        # stray bracket would be eaten as Rich markup.
        self.query_one("#combat_log", Static).update(Text("\n".join(state.log[-COMBAT_LOG_LINES:])))

        if state.is_over:
            self.actions = []
            await _replace_items(
                self.query_one("#actions", ListView), [ListItem(Static("Continue"), id="done")]
            )
            return

        self.actions = available_actions(self.app.character, self.state.weapon_cooldowns)
        await _replace_items(
            self.query_one("#actions", ListView),
            [ListItem(Static(action.label), id=f"action_{i}") for i, action in enumerate(self.actions)],
        )

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        if self.state.is_over:
            self.dismiss(self.state.outcome)
            return

        action = self.actions[int(event.item.id.removeprefix("action_"))]
        take_turn(self.state, action, self.app.rng)
        await self._refresh()


# Terrain glyphs for the tactical map. A view concern, so kept here rather than in
# tactical.py (which knows tiles, not how they're drawn).
_TAC_TILE = {Tile.WALL: "#", Tile.LOW_COVER: "%", Tile.FLOOR: "."}
_TAC_END_TEXT = {
    TacticalOutcome.VICTORY: "You've cleared them out.",
    TacticalOutcome.ESCAPED: "You slip out.",
    TacticalOutcome.DEAD: "You're down.",
}
# How many log lines the fight scrolls back — the elastic panel, like COMBAT_LOG_LINES.
TACTICAL_LOG_LINES = 6


class TacticalScreen(Screen):
    """One tactical fight on a grid. Pushed by SceneScreen for a stage with a
    TacticalStage, dismissed with the TacticalOutcome — the scene, not this screen,
    decides what winning or slipping out is worth (it owns the TacticalStage's Outcomes).
    All the rules live in tactical.py; this renders TacticalState and feeds it the
    move/attack/end-turn the player picked.

    Movement is the arrow keys, so there is deliberately no action ListView to steal
    them: every action is a footer hotkey instead (see BINDINGS). An attack fires on the
    nearest in-sight target with the best-reaching weapon — targeting is automatic in
    this first version; picking a specific target is a later refinement.
    """

    BINDINGS = [
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("f", "fire", "Attack"),
        ("e", "end_turn", "End turn"),
        ("l", "leave", "Leave (on exit)"),
        ("enter", "continue", "Continue"),
        ("q", "quit_menu", "Menu"),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    CSS = """
    #tac_map { height: 1fr; padding: 0 1; }
    #tac_status, #tac_log { height: auto; padding: 0 1; }
    """

    def __init__(self, stage: TacticalStage) -> None:
        super().__init__()
        self.stage = stage
        self.state: TacticalState | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield CharacterSheet(self.app.character)
        yield Static(self.stage.prompt, id="tac_prompt")
        yield Static(id="tac_status")
        yield Static(id="tac_map")
        yield Static(id="tac_log")
        yield Footer()

    def on_mount(self) -> None:
        self.state = start_tactical(
            self.app.character,
            self.stage.grid,
            self.stage.player_start,
            list(self.stage.enemies),
            self.stage.exits,
        )
        self._refresh()

    def action_move(self, direction: str) -> None:
        if self.state.is_over:
            return
        dx, dy = self.DIRECTIONS[direction]
        px, py = self.state.player.coord
        move_player(self.state, (px + dx, py + dy))
        self._refresh()

    def action_fire(self) -> None:
        if self.state.is_over:
            return
        shot = best_shot(self.state)
        if shot is None:
            self.notify(
                "You've already acted this turn." if self.state.acted else "No target in sight and range."
            )
            return
        weapon, target = shot
        player_attack(self.state, target, weapon, self.app.rng)
        self._refresh()

    def action_end_turn(self) -> None:
        if self.state.is_over:
            return
        end_turn(self.state, self.app.rng)
        self._refresh()

    def action_leave(self) -> None:
        if self.state.is_over:
            return
        if not leave(self.state):
            self.notify("You're not standing on an exit.")
        self._refresh()

    def action_continue(self) -> None:
        if self.state.is_over:
            self.dismiss(self.state.outcome)

    def _map_text(self) -> Text:
        state = self.state
        grid = state.grid
        glyphs = [[_TAC_TILE[grid.tiles[y][x]] for x in range(grid.width)] for y in range(grid.height)]
        styles: dict[tuple[int, int], str] = {}
        for ex, ey in state.exits:
            if grid.tiles[ey][ex] is Tile.FLOOR:
                glyphs[ey][ex] = ">"
                styles[(ey, ex)] = "bold green"
        for unit in state.units:
            ux, uy = unit.coord
            if unit.side is Side.PLAYER:
                glyphs[uy][ux], styles[(uy, ux)] = "@", "bold cyan"
            elif unit.health > 0:
                glyphs[uy][ux], styles[(uy, ux)] = "E", "bold red"
            else:
                glyphs[uy][ux], styles[(uy, ux)] = "x", "grey37"
        text = Text()
        for y in range(grid.height):
            for x in range(grid.width):
                ch = glyphs[y][x]
                default = "grey30" if ch in ("#", "%") else "grey50"
                text.append(ch, style=styles.get((y, x), default))
            text.append("\n")
        return text

    def _refresh(self) -> None:
        state = self.state
        self.query_one(CharacterSheet).refresh()
        self.query_one("#tac_map", Static).update(self._map_text())
        self.query_one("#tac_log", Static).update(Text("\n".join(state.log[-TACTICAL_LOG_LINES:])))
        if state.is_over:
            self.query_one("#tac_status", Static).update(
                f"{_TAC_END_TEXT[state.outcome]}  —  press Enter to continue."
            )
            return
        character = self.app.character
        self.query_one("#tac_status", Static).update(
            f"HP {character.health}/{character.max_health}   "
            f"Moves {state.moves_left}/{state.player.speed}   "
            f"Action {'used' if state.acted else 'ready'}   "
            f"Enemies left {len(state.enemies)}   (arrows move, f attack, e end turn)"
        )


TRAVEL_STAMINA_COST = 1

# Width of one modifier column in the #modifiers panel. Five of them must fit an
# 80-column terminal, and "Surveillance" is the longest label.
MODIFIER_COLUMN = 13


class CorpMapScreen(Screen):
    BINDINGS = [
        ("q", "quit_menu", "Menu"),
        ("escape", "back", "Back"),
        ("up", "move('up')", "Move"),
        ("down", "move('down')", "Move"),
        ("left", "move('left')", "Move"),
        ("right", "move('right')", "Move"),
        ("enter", "travel", "Travel here"),
    ]

    DIRECTIONS = {"up": (0, -1), "down": (0, 1), "left": (-1, 0), "right": (1, 0)}

    # The panels below the map are fixed height and the map scrolls in what is
    # left, so every row they take is a row of board the player cannot see. At
    # 80x24 the budget is exact: keep them free of vertical padding.
    CSS = """
    #map_scroll {
        height: 1fr;
        overflow-x: auto;
        padding: 0 1;
    }

    #territory_info {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }

    #modifiers {
        height: auto;
        border-top: solid $accent;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.selected_id = ""
        self.hovered_id: str | None = None
        self.rendered: RenderedMap | None = None
        self._render_key: tuple[str, str] | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield ScrollableContainer(Static(markup=False, id="map"), id="map_scroll")
        yield Static(id="territory_info")
        yield Static(markup=False, id="modifiers")
        yield Footer()

    def on_mount(self) -> None:
        self.selected_id = self.app.character.location_id
        self._refresh()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_travel(self) -> None:
        character = self.app.character
        here = self.app.corp_map.territories[character.location_id]
        if self.selected_id not in here.connections:
            return
        if character.free_travel_remaining() > 0:
            character.spend_free_travel()
        elif character.can_afford(TRAVEL_STAMINA_COST):
            character.spend_stamina(TRAVEL_STAMINA_COST)
        else:
            return
        character.location_id = self.selected_id
        self._refresh()

    def action_move(self, direction: str) -> None:
        dx, dy = self.DIRECTIONS[direction]
        current = self.app.corp_map.territories[self.selected_id]
        for conn_id in current.connections:
            candidate = self.app.corp_map.territories[conn_id]
            if (candidate.x - current.x, candidate.y - current.y) == (dx, dy):
                self.selected_id = conn_id
                self._refresh()
                return

    def on_mouse_move(self, event: events.MouseMove) -> None:
        offset = event.get_content_offset(self.query_one("#map", Static))
        hovered = (
            self.rendered.territory_at(offset.y, offset.x)
            if offset is not None and self.rendered is not None
            else None
        )
        if hovered != self.hovered_id:
            self.hovered_id = hovered
            self._refresh()

    def on_click(self) -> None:
        if self.hovered_id is not None:
            self.selected_id = self.hovered_id
            self._refresh()

    def _refresh(self) -> None:
        corp_map = self.app.corp_map
        character = self.app.character
        # A passive check here (rather than a one-off in action_travel) so it also
        # catches the starting location and any other way location_id could change.
        discover_fixers_here(self.app.fixers, character)

        # Hover only restyles the finished text, so re-render the board itself
        # only when the cursor or the runner actually moved.
        key = (self.selected_id, character.location_id)
        if self.rendered is None or key != self._render_key:
            self.rendered = render_ascii_map(corp_map, self.selected_id, character.location_id)
            self._render_key = key
        text = Text(self.rendered.text)
        for span in self.rendered.spans:
            color = OWNER_COLORS.get(corp_map.territories[span.territory_id].owner)
            if color:
                text.stylize(color, span.offset, span.offset + span.end - span.start)
            if span.territory_id == self.hovered_id:
                text.stylize("reverse", span.offset, span.offset + span.end - span.start)
        self.query_one("#map", Static).update(text)

        t = corp_map.territories[self.hovered_id or self.selected_id]
        here = corp_map.territories[character.location_id]
        borders = ", ".join(corp_map.territories[c].name for c in t.connections)
        locations = ", ".join(f"{loc.name} ({loc.kind})" for loc in t.locations)
        fixer_here = next(
            (
                fixer
                for fixer in self.app.fixers
                if fixer.location_id == t.id and fixer.id in character.discovered_fixers
            ),
            None,
        )
        fixer_suffix = f", fixer: {fixer_here.name}" if fixer_here else ""
        self.query_one("#territory_info", Static).update(
            f"{t.name} — owner: {owner_label(t.owner)}, value: {t.value}{fixer_suffix}\n"
            f"Borders: {borders}\n"
            f"Locations: {locations}\n"
            f"{self._travel_hint(t, here, character)}"
        )
        self.query_one("#modifiers", Static).update(self._modifier_panel(t))

    def _modifier_panel(self, t: Territory) -> str:
        """Five levers across two lines — a row each would cost the map its viewport."""
        labels, levels = [], []
        for modifier, level in t.modifiers.items():
            labels.append(MODIFIER_LABELS[modifier].ljust(MODIFIER_COLUMN))
            levels.append(f"{level}/{MODIFIER_MAX}".ljust(MODIFIER_COLUMN))
        return f"{''.join(labels).rstrip()}\n{''.join(levels).rstrip()}"

    def _travel_hint(self, t: Territory, here: Territory, character: Character) -> str:
        stamina = f"{character.stamina}/{character.max_stamina}"
        if t.id == here.id:
            return f"You are here. Stamina: {stamina}"
        if t.id not in here.connections:
            return f"No route from {here.name} — travel is only to a bordering district."
        free = character.free_travel_remaining()
        if free > 0:
            return f"enter: travel here (free — {free} left today) — Stamina: {stamina}"
        if not character.can_afford(TRAVEL_STAMINA_COST):
            return f"Too tired to travel ({TRAVEL_STAMINA_COST} stamina). Rest to move."
        return f"enter: travel here ({TRAVEL_STAMINA_COST} stamina) — you have {stamina}"


# The two menus share the same centered-dialog look; kept in one place so the load
# list and the quit menu can't drift apart.
_MENU_CSS = """
__SCREEN__ {
    align: center middle;
}

#__DIALOG__ {
    width: auto;
    height: auto;
    border: round $accent;
    padding: 1 2;
}

#__DIALOG__ ListView {
    width: 28;
    height: auto;
}
"""


def _menu_css(screen: str, dialog: str) -> str:
    """Fill the shared dialog stylesheet for one modal. Uses str.replace, not
    %-formatting or .format — the CSS is full of literal `{ }` and could grow a `%`
    (e.g. a percentage width), either of which would blow those up at import time."""
    return _MENU_CSS.replace("__SCREEN__", screen).replace("__DIALOG__", dialog)


class QuitMenu(ModalScreen):
    """The `q` menu, overlaid on whatever screen is in play. Opening it is a safe
    reflex — `q`/`escape` dismiss it back to the game with nothing lost — so only the
    bottom rows are irreversible: leave the app, or throw this run away for a fresh one.
    There is no meta-progression, so Restart is just a new run from scratch."""

    BINDINGS = [("escape", "close", "Back"), ("q", "close", "Back")]
    CSS = _menu_css("QuitMenu", "quit_dialog")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Menu"),
            ListView(
                ListItem(Static("Save Game"), id="save"),
                ListItem(Static("Load Game"), id="load"),
                ListItem(Static("Quit Game"), id="quit"),
                ListItem(Static("Restart Game"), id="restart"),
            ),
            id="quit_dialog",
        )

    def action_close(self) -> None:
        self.dismiss()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "save":
            # A save must never take the run down with it: a full disk, a read-only
            # home, or an unpicklable object in state should report and leave you in
            # play, not crash — losing the very progress you were trying to keep. Load
            # is guarded the same way.
            try:
                slot = self.app.save_run()
            except Exception as exc:
                self.app.notify(f"Couldn't save: {exc}", severity="error")
                return
            self.app.notify(f"Saved: {slot.label}")
            self.dismiss()
        elif event.item.id == "load":
            slots = list_saves()
            if not slots:
                self.app.notify("No saved games found.", severity="warning")
                return
            # push, not replace: escape from the load list drops back to this menu.
            self.app.push_screen(LoadMenu(slots))
        elif event.item.id == "quit":
            self.app.exit()
        elif event.item.id == "restart":
            self.app.restart_run()


class LoadMenu(ModalScreen):
    """The Load-Game pick-list: every save on disk, newest first. Rows are keyed by
    list index rather than by anything off the save, because two saves made in the same
    minute share a label and a duplicate ListView id would raise."""

    BINDINGS = [("escape", "close", "Back"), ("q", "close", "Back")]
    CSS = _menu_css("LoadMenu", "load_dialog")

    def __init__(self, slots: list[SaveSlot]) -> None:
        super().__init__()
        self._slots = slots

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("Load Game"),
            ListView(
                *(
                    ListItem(Static(slot.label), id=f"slot_{i}")
                    for i, slot in enumerate(self._slots)
                ),
            ),
            id="load_dialog",
        )

    def action_close(self) -> None:
        self.dismiss()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        slot = self._slots[int(event.item.id.removeprefix("slot_"))]
        try:
            state = load_game(slot.path)
        except Exception:
            # A save can go stale as the code moves on (see saves.SAVE_VERSION), and a
            # file can be truncated/corrupt. Either way, report it rather than crash.
            self.app.notify(f"Couldn't load {slot.label}.", severity="error")
            return
        # load_state tears the stack down, taking this menu and the QuitMenu with it.
        self.app.load_state(state)


class TitleMenu(Screen):
    """The screen the app opens on, before CharacterCreationScreen. New Game pushes
    creation the way it always started; Load Game reuses the same LoadMenu the
    in-run Quit menu uses, so there's one load path, not two."""

    BINDINGS = [("q", "quit_menu", "Menu")]
    CSS = _menu_css("TitleMenu", "title_dialog")

    OPTIONS = [
        ("new_game", "New Game"),
        ("load_game", "Load Game"),
        ("test", "Test"),
        ("settings", "Settings"),
    ]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Shadowguy"),
            ListView(*(ListItem(Static(label), id=option_id) for option_id, label in self.OPTIONS)),
            id="title_dialog",
        )
        yield Footer()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item.id == "new_game":
            self.app.push_screen(CharacterCreationScreen())
        elif event.item.id == "load_game":
            slots = list_saves()
            if not slots:
                self.notify("No saved games found.", severity="warning")
                return
            self.app.push_screen(LoadMenu(slots))
        elif event.item.id == "test":
            self.app.push_screen(TestMenu())
        elif event.item.id == "settings":
            self.notify("Settings aren't implemented yet.")


class TestMenu(Screen):
    """Dev-only snapshots of systems normal play doesn't reliably reach — a tactical
    fight is only ~35% of jobs (jobs.TACTICAL_FIGHT_CHANCE), behind a fixer offer and
    several stage checks, so this jumps straight to one at a chosen enemy tier. Runs
    against the same app.character as everything else, but bypasses Scene/apply_outcome
    entirely (there's no job or gig backing it) and restores health afterward so a test
    fight can't leave a mark on the run underneath it."""

    BINDINGS = [("q", "quit_menu", "Menu"), ("escape", "back", "Back")]
    CSS = _menu_css("TestMenu", "test_dialog")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Vertical(
            Static("Test"),
            ListView(
                *(
                    ListItem(Static(f"Tactical Combat — Tier {tier}"), id=f"tactical_{tier}")
                    for tier in sorted(ENEMY_TIERS)
                )
            ),
            id="test_dialog",
        )
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        tier = int(event.item.id.removeprefix("tactical_"))
        rng = self.app.rng
        enemies = roll_enemies(tier, rng)
        tac = generate_map(rng, len(enemies))
        stage = TacticalStage(
            prompt=f"Test fight — tier {tier}.",
            grid=tac.grid,
            player_start=tac.player_start,
            enemies=tuple(zip(enemies, tac.enemy_spawns, strict=True)),
            victory=Outcome(text="Cleared."),
            escape=Outcome(text="You slip out."),
            exits=tac.exits,
        )
        self.app.push_screen(TacticalScreen(stage), self._on_tactical_end)

    def _on_tactical_end(self, result: TacticalOutcome) -> None:
        # Sandbox, not a real fight: whatever happened, leave the character clean for
        # whichever real screen (creation, or a run already in progress) sits under this.
        self.app.character.health = self.app.character.max_health
        self.notify(f"Test fight ended: {result.name.title()}.")


class ShadowguyApp(App):
    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self) -> None:
        super().__init__()
        self._new_run()

    def _new_run(self) -> None:
        """Fresh run state. No meta-progression, so a restart is just this again — one
        seat of state, rebuilt from a new rng, that both boot and restart share."""
        self.rng = random.Random()
        self.corp_map = generate_corp_map(FACTIONS, self.rng)
        self.character = Character(name="Runner", location_id=self.corp_map.player_start_id)
        self.fixers = create_fixers(self.corp_map, self.rng)
        refresh_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        # One gig per location, keyed by location id — the street-work counterpart to
        # the fixers' job board, topped up on each rest (see gigs.refresh_gigs).
        self.location_gigs: dict[str, Scene] = {}
        refresh_gigs(self.corp_map, self.location_gigs, self.character.day, self.rng)

    def advance_day(self) -> None:
        """Advance one day and refresh the day-driven boards. The shared spine of every
        rest: the MainMenu 'end the day' (paying district lodging) and a HospitalScreen
        stay (paying for care and healing) both call this to actually turn the day over."""
        self.character.rest()
        # Indefinite crew draw their daily wage on the turnover; anyone you can't cover walks.
        for name in self.character.pay_crew_wages():
            self.notify(f"{name} walked off the crew — you missed payroll.", severity="warning")
        expire_offers(self.fixers, self.character.day)
        refresh_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        refresh_gigs(self.corp_map, self.location_gigs, self.character.day, self.rng)

    def action_quit_menu(self) -> None:
        # push, not switch: the menu overlays play and dismisses back to it.
        self.push_screen(QuitMenu())

    def restart_run(self) -> None:
        self._new_run()
        self._reopen(CharacterCreationScreen())

    def save_run(self) -> SaveSlot:
        """Pickle the current run — the fields _new_run seeds are exactly the run's
        state, so they are exactly what a save round-trips."""
        state = {
            "rng": self.rng,
            "corp_map": self.corp_map,
            "character": self.character,
            "fixers": self.fixers,
            "location_gigs": self.location_gigs,
        }
        return save_game(state, self.character.day)

    def load_state(self, state: dict) -> None:
        # Resolve all fields before mutating any: load_game already validates the bundle's
        # shape, but keeping the assignment atomic means even a future malformed state
        # replaces the whole run or none of it — never leaving a half-swapped App.
        rng, corp_map = state["rng"], state["corp_map"]
        character, fixers = state["character"], state["fixers"]
        location_gigs = state["location_gigs"]
        self.rng, self.corp_map, self.character, self.fixers = rng, corp_map, character, fixers
        self.location_gigs = location_gigs
        # Where a load resumes depends on whether the saved run had finished creation.
        # A save taken mid-build still has points to spend; dropping it straight into
        # MainMenu would strand those points unspendable (SkillsScreen is read-only),
        # silently forfeiting the build the creation gate exists to protect. So resume
        # an unfinished build on the creation screen and a run already under way on
        # MainMenu — pools-empty is the same "creation done" test action_begin gates on.
        unspent = self.character.stat_points + self.character.skill_points
        self._reopen(CharacterCreationScreen() if unspent else MainMenu())

    def _reopen(self, screen: Screen) -> None:
        """Tear the whole screen stack down to the base and open on `screen` — the menu
        that called this, and any game screens under it, all go. Shared by restart and
        load, the two acts that replace the run wholesale."""
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(screen)

    def on_mount(self) -> None:
        self.push_screen(TitleMenu())


def main() -> None:
    ShadowguyApp().run()


if __name__ == "__main__":
    main()
