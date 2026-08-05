from textual.app import ComposeResult
from textual.containers import ScrollableContainer, Vertical
from textual.screen import ModalScreen
from textual.widgets import Collapsible, Footer, Header, ListItem, ListView, Static

from shadowguy.corp_turn import (
    ACADEMY_REBUILD_COST,
    ACADEMY_TRAINING_COST,
    DEVELOPMENT_BUMP_COST,
    RESEARCH_FACILITY_REBUILD_COST,
    SURVEILLANCE_BUMP_COST,
    TECHNOLOGIES,
    TECHNOLOGIES_BY_ID,
    TRAINING_DAYS,
    CorpState,
    EmployeeCategory,
    FactionEvent,
    assistant_capacity,
    assistant_rate,
    attack_territory,
    build_efficiency_upgrade,
    build_academy,
    build_lab,
    build_research_facility,
    defense_strength,
    deploy_operatives,
    deployable_targets,
    development_targets,
    employee_plural,
    expand_into,
    expansion_cost,
    has_technology,
    lab_capacity,
    log_faction_event,
    next_efficiency_cost,
    next_lab_cost,
    owned_research_facility,
    prereqs_met,
    raise_development,
    raise_surveillance,
    rebuild_academy_targets,
    rebuild_facility_targets,
    research_rate,
    research_technology,
    surveillance_targets,
    technology_tree_layout,
    train_employees,
)
from shadowguy.corpmap import TerritoryModifier, attack_candidates, expansion_candidates
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.runners import RUNNERS_BY_ID

from . import (
    MENU_BACK_BINDINGS,
    BackScreen,
    _boxed_text,
    _menu_css,
    _replace_items,
)


def _sighting_label(sighting, corp_map) -> str:
    who = "You" if sighting.kind == "player" else RUNNERS_BY_ID[sighting.actor_id].name
    territory_name = corp_map.territories[sighting.territory_id].name
    return f"Day {sighting.day} — {who} spotted in {territory_name}"


def force_options(available: int) -> list[int]:
    """The operative counts ForcePickScreen offers out of a pool of `available`:
    a quarter, half, three quarters and all of it, plus 1, deduplicated and sorted.

    Quartiles rather than every number from 1 to N because the pool grows without
    bound as a corp trains up, and a list of forty rows is not a decision anyone
    makes. 1 is always in it — committing a token force to see what a district's
    garrison is actually made of is a real move, and the quartiles of a small pool
    would otherwise skip it."""
    return sorted({1, *(available * n // 4 for n in (1, 2, 3, 4))} - {0})


class ForcePickScreen(ModalScreen):
    """How many operatives to commit to a deploy or an attack. Dismisses the chosen
    count, or None if cancelled — the same dismiss-a-value modal shape
    GrenadePickScreen/HackerPickScreen use.

    A separate step rather than a fixed all-in commitment because how much to send
    is the actual decision: hold everything back and you never take ground, commit
    everything and one bad roll leaves every district you own undefended. The
    where and the how-many are two different questions and the screen asks them in
    that order."""

    BINDINGS = [("escape", "cancel", "Back")]
    CSS = _menu_css("ForcePickScreen", "force_dialog")

    def __init__(self, prompt: str, available: int) -> None:
        super().__init__()
        self._prompt = prompt
        self._available = available

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static(self._prompt),
            ListView(
                *(
                    ListItem(Static(f"{n} operative{'s' if n != 1 else ''}"), id=f"force_{n}")
                    for n in force_options(self._available)
                ),
            ),
            id="force_dialog",
        )

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(int(event.item.id.removeprefix("force_")))


def operations_rows(corp_state, corp_map) -> list[ListItem]:
    """The corp's operative moves as ListItems: one "Reinforce" row per district it
    holds, one "Attack" row per bordering rival district.

    Shared by CorpScreen (its own #operations_list panel) and CorpMapScreen (folded
    into the flat corp-category list a corp_only run plays from), because a
    corp-only game never opens CorpScreen and would otherwise have no way to fight
    at all. Both rows spend the day's directed move, so both carry the same
    "already acted today" note the expansion rows do.
    """
    rows = []
    for territory in deployable_targets(corp_state, corp_map):
        security = territory.modifiers.get(TerritoryModifier.SECURITY, 0)
        label = (
            f"Reinforce {territory.name} — defense {defense_strength(territory)} "
            f"({territory.garrison} garrison + {security} Security)"
        )
        if corp_state.daily_action_used:
            label += " (already acted today)"
        elif not corp_state.operatives:
            label += " (no operatives spare)"
        rows.append(ListItem(Static(label), id=f"deploy_{territory.id}"))

    for territory_id in attack_candidates(corp_map, corp_state.faction_id):
        territory = corp_map.territories[territory_id]
        holder = FACTIONS_BY_ID[territory.owner].name
        label = f"Attack {territory.name} ({holder}) — defense {defense_strength(territory)}"
        if corp_state.daily_action_used:
            label += " (already acted today)"
        elif not corp_state.operatives:
            label += " (no operatives to send)"
        rows.append(ListItem(Static(label), id=f"attack_{territory_id}"))
    return rows


class OperationsMixin:
    """The deploy/attack flow, shared by CorpScreen and CorpMapScreen.

    A mixin rather than duplicated handlers because this one is two screens long
    and genuinely stateful — it spans a pushed ForcePickScreen and back — unlike
    the one-liner expand/train/build handlers the two screens do just repeat.
    Requires the host to provide `_refresh()`.
    """

    async def _commit_operatives(self, item_id: str) -> None:
        """Check the two gates that make the pick pointless *before* asking anything,
        then ask how many (ForcePickScreen) and hand the answer to corp_turn.

        The gates are checked here rather than letting the corp_turn call fail closed
        so the player isn't walked through a force picker only to be told they'd
        already acted — the module still fails closed underneath either way."""
        corp_state = self.app.corp_state
        is_attack = item_id.startswith("attack_")
        territory_id = item_id.removeprefix("attack_" if is_attack else "deploy_")
        territory = self.app.corp_map.territories[territory_id]
        if corp_state.daily_action_used:
            self.notify("Already made your move today.", severity="warning")
            return
        if not corp_state.operatives:
            self.notify("No operatives to send. Train some at the Academy.", severity="warning")
            return

        prompt = (
            f"Attack {territory.name} — defense {defense_strength(territory)}. Commit how many?"
            if is_attack
            else f"Reinforce {territory.name}. Send how many?"
        )
        # Stashed rather than closed over: the callback fires after this handler has
        # returned, and it needs to know which district the count is for. Same
        # push_screen(screen, callback) shape TacticalScreen uses for its own picks.
        self._pending_target = (territory_id, is_attack)
        self.app.push_screen(ForcePickScreen(prompt, corp_state.operatives), self._on_force_picked)

    async def _on_force_picked(self, committed: int | None) -> None:
        if committed is None:
            return
        territory_id, is_attack = self._pending_target
        corp_state = self.app.corp_state
        territory = self.app.corp_map.territories[territory_id]

        if not is_attack:
            deploy_operatives(corp_state, self.app.corp_map, territory_id, committed)
            self.notify(f"{committed} operative(s) now hold {territory.name}.")
            await self._refresh()
            return

        result = attack_territory(corp_state, self.app.corp_map, territory_id, committed, self.app.rng)
        if result is None:
            self.notify("That attack can't go in.", severity="warning")
            await self._refresh()
            return
        if result.captured:
            self.notify(
                f"{territory.name} is yours — {result.attacker_losses} lost, "
                f"{committed - result.attacker_losses} holding it."
            )
            log_faction_event(
                self.app.faction_events,
                corp_state.faction_id,
                FactionEvent(
                    kind="seizure",
                    day=self.app.character.day,
                    territory_id=territory_id,
                    from_faction_id=result.defender_id,
                ),
            )
        else:
            self.notify(
                f"Driven off {territory.name} — {result.attacker_losses} lost, "
                f"{result.defender_losses} of theirs down.",
                severity="warning",
            )
        await self._refresh()


class CorpScreen(OperationsMixin, BackScreen):
    """Play as a corp instead of the runner. Getting one is not a choice made here:
    a runner takes a corp from inside its own HQ (shop_screens.CorpHQScreen), by
    reaching the executive and buying a controlling stake — see factions.can_take_over.
    With no corp yet this screen only points at that. With one, you spend a directed
    move a day on either the same neutral-ground expansion rivals.py's AI factions
    make, or training up employees at the corp's Academy.

    Actions are grouped by the thing they're attached to, not left in one flat
    list: territory expansion + end-day stay in #corp_list, Academy training
    goes in the #academy_list collapsible, Research Facility upgrades go in
    the #research_list collapsible, the corp's operatives (reinforce a district
    you hold, attack one you don't) go in #operations_list, and a read-only
    Surveillance Log goes in #surveillance_list — all four always present once a
    corp is picked, since every faction's territory carries one guaranteed
    Academy and one guaranteed Research Facility from the start (corp_turn.py).
    Technology itself lives on its own pushed ResearchTreeScreen, below.

    Every Operations row goes through ForcePickScreen for its headcount, so both
    moves ask *where* on this screen and *how many* on that one."""

    BINDINGS = [
        *MENU_BACK_BINDINGS,
        ("t", "research_tree", "Research Tree"),
    ]

    # ListView defaults to height: 1fr, which -- with three of them stacked as
    # siblings (corp_list plus the Collapsible-wrapped ones) -- squashes each
    # to a sliver and lets the Collapsibles overlap on top of it. height: auto
    # (the same fix MainMenu applies to its own Collapsible-wrapped lists) sizes
    # each to its actual item count instead.
    CSS = """
    #corp_list, #academy_list, #research_list, #operations_list, #surveillance_list {
        height: auto;
    }

    #academy_panel, #research_panel, #operations_panel, #surveillance_panel {
        height: auto;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="corp_info")
        yield ListView(id="corp_list")
        yield Collapsible(ListView(id="academy_list"), title="Academy", collapsed=False, id="academy_panel")
        yield Collapsible(
            ListView(id="research_list"), title="Research Facility", collapsed=False, id="research_panel"
        )
        yield Collapsible(
            ListView(id="operations_list"), title="Operations", collapsed=False, id="operations_panel"
        )
        yield Collapsible(
            ListView(id="surveillance_list"),
            title="Surveillance Log",
            collapsed=True,
            id="surveillance_panel",
        )
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    def action_research_tree(self) -> None:
        if self.app.corp_state is not None:
            self.app.push_screen(ResearchTreeScreen())

    async def _refresh(self) -> None:
        corp_state = self.app.corp_state
        info = self.query_one("#corp_info", Static)
        list_view = self.query_one("#corp_list", ListView)
        academy_list = self.query_one("#academy_list", ListView)
        research_list = self.query_one("#research_list", ListView)

        if corp_state is None:
            # No picker here any more: a corp is taken from inside its own HQ
            # (shop_screens.CorpHQScreen), by a runner the executive will see and who can
            # cover factions.TAKEOVER_COST. This screen just says where to go and how
            # each corp currently feels about you, since standing is the gate that moves
            # slowest.
            character = self.app.character
            info.update(
                "You aren't running a corp.\n"
                "Corps are taken, not chosen — go see one in person, at its HQ."
            )
            items = [
                ListItem(
                    Static(
                        f"{faction.name} ({faction.specialty}) — "
                        f"standing {character.standing_with(faction.id):+d}"
                    ),
                    id=f"corpinfo_{faction.id}",
                )
                for faction in FACTIONS
            ]
            await _replace_items(list_view, items)
            await _replace_items(academy_list, [])
            await _replace_items(research_list, [])
            await _replace_items(self.query_one("#operations_list", ListView), [])
            await _replace_items(self.query_one("#surveillance_list", ListView), [])
            self.query_one("#academy_panel").display = False
            self.query_one("#research_panel").display = False
            self.query_one("#operations_panel").display = False
            self.query_one("#surveillance_panel").display = False
            return

        self.query_one("#academy_panel").display = True
        self.query_one("#research_panel").display = True
        self.query_one("#operations_panel").display = True
        self.query_one("#surveillance_panel").display = True

        corp_map = self.app.corp_map
        faction = FACTIONS_BY_ID[corp_state.faction_id]
        owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
        candidates = expansion_candidates(corp_map, corp_state.faction_id)
        territory_names = ", ".join(t.name for t in owned) or "none"
        facility = owned_research_facility(corp_state, self.app.corp_map)
        facility_line = ""
        if facility is not None:
            capacity = lab_capacity(facility)
            working = min(corp_state.scientists, capacity)
            assist_capacity = assistant_capacity(facility)
            working_assistants = min(corp_state.research_assistants, assist_capacity)
            facility_line = (
                f"\nResearch Facility: tier {facility.research_tier}, "
                f"{working}/{capacity} scientists at work "
                f"({research_rate(corp_state, facility):g}rp/scientist), "
                f"{working_assistants}/{assist_capacity} assistants at work "
                f"({assistant_rate(corp_state):g}rp/assistant)"
            )
        info.update(
            f"{faction.name} — {corp_state.cash}eb — {corp_state.research_points}rp — "
            f"{corp_state.scientists} scientists — {corp_state.operatives} operatives — "
            f"{corp_state.research_assistants} research assistants — "
            f"Day {self.app.character.day}\n"
            f"Territories ({len(owned)}): {territory_names}"
            f"{facility_line}"
        )

        items = []
        for territory_id in candidates:
            territory = corp_map.territories[territory_id]
            cost = expansion_cost(territory)
            label = f"Expand into {territory.name} — {cost}eb"
            if corp_state.daily_action_used:
                label += " (already acted today)"
            elif cost > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"expand_{territory_id}"))
        if not candidates:
            items.append(ListItem(Static("No neutral ground borders your territory."), id="none"))

        # The two modifier bumps are cash-gated and repeatable, so they never
        # carry the "already acted today" note the expansion rows above do.
        for territory in surveillance_targets(corp_state, corp_map):
            level = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
            label = (
                f"Raise Surveillance in {territory.name} "
                f"({level}→{level + 1}) — {SURVEILLANCE_BUMP_COST}eb"
            )
            if SURVEILLANCE_BUMP_COST > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"surveil_{territory.id}"))

        for territory in development_targets(corp_state, corp_map):
            level = territory.modifiers.get(TerritoryModifier.DEVELOPMENT, 0)
            label = f"Develop {territory.name} ({level}→{level + 1}) — {DEVELOPMENT_BUMP_COST}eb"
            if DEVELOPMENT_BUMP_COST > corp_state.cash:
                label += " (can't afford)"
            items.append(ListItem(Static(label), id=f"develop_{territory.id}"))

        items.append(ListItem(Static(self.app.rest_label()), id="rest"))
        await _replace_items(list_view, items)

        academy_items = []
        rebuild_sites = rebuild_academy_targets(corp_state, corp_map)
        pending = corp_state.pending_recruit
        if rebuild_sites:
            # No Academy at all: it was captured with the district under it, so the only
            # move left here is standing a new one up. Nothing trains until one does.
            for territory in rebuild_sites:
                label = f"Build an Academy in {territory.name} — {ACADEMY_REBUILD_COST}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif ACADEMY_REBUILD_COST > corp_state.cash:
                    label += " (can't afford)"
                academy_items.append(ListItem(Static(label), id=f"newacademy_{territory.id}"))
        elif pending is not None:
            days_left = pending.ready_day - self.app.character.day
            academy_items.append(
                ListItem(
                    Static(
                        f"Training {employee_plural(pending.category)} — "
                        f"ready in {days_left} day{'s' if days_left != 1 else ''}"
                    ),
                    id="pending_recruit",
                )
            )
        else:
            for category in EmployeeCategory:
                cost = ACADEMY_TRAINING_COST[category]
                label = f"Train {employee_plural(category)} ({TRAINING_DAYS[category]}d) — {cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif cost > corp_state.cash:
                    label += " (can't afford)"
                academy_items.append(ListItem(Static(label), id=f"train_{category}"))
        await _replace_items(academy_list, academy_items)

        research_items = []
        if facility is None:
            # Nothing to upgrade: the corp's labs were captured with the district under
            # them, so the only research move left is standing a new one up.
            for territory in rebuild_facility_targets(corp_state, corp_map):
                label = f"Build a Research Facility in {territory.name} — {RESEARCH_FACILITY_REBUILD_COST}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif RESEARCH_FACILITY_REBUILD_COST > corp_state.cash:
                    label += " (can't afford)"
                research_items.append(ListItem(Static(label), id=f"rebuild_{territory.id}"))
        else:
            cost = next_lab_cost(facility)
            if cost is None:
                research_items.append(ListItem(Static("Labs fully upgraded"), id="labs_maxed"))
            else:
                label = f"Build a lab — {cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif cost > corp_state.cash:
                    label += " (can't afford)"
                research_items.append(ListItem(Static(label), id="build_lab"))

            efficiency_cost = next_efficiency_cost(facility)
            if efficiency_cost is None:
                research_items.append(ListItem(Static("Efficiency fully upgraded"), id="efficiency_maxed"))
            else:
                label = f"Upgrade efficiency — {efficiency_cost}eb"
                if corp_state.daily_action_used:
                    label += " (already acted today)"
                elif efficiency_cost > corp_state.cash:
                    label += " (can't afford)"
                research_items.append(ListItem(Static(label), id="build_efficiency"))
        await _replace_items(research_list, research_items)

        operations_items = operations_rows(corp_state, corp_map) or [
            ListItem(Static("No territory to hold and nobody bordering you."), id="no_operations")
        ]
        await _replace_items(self.query_one("#operations_list", ListView), operations_items)

        if corp_state.sightings:
            sighting_items = [
                ListItem(Static(_sighting_label(sighting, corp_map)), id=f"sighting_{i}")
                for i, sighting in enumerate(corp_state.sightings)
            ]
        else:
            sighting_items = [ListItem(Static("No sightings yet."), id="no_sightings")]
        await _replace_items(self.query_one("#surveillance_list", ListView), sighting_items)

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id
        if item_id.startswith("sighting_") or item_id in ("no_sightings", "no_operations"):
            return

        if item_id.startswith("deploy_") or item_id.startswith("attack_"):
            await self._commit_operatives(item_id)
            return

        if item_id.startswith("corpinfo_"):
            # Read-only rows: the takeover lives at the corp's HQ, not here.
            faction = FACTIONS_BY_ID[item_id.removeprefix("corpinfo_")]
            self.notify(f"Find {faction.name}'s HQ on the map and walk in.")
            return

        if item_id == "rest":
            self.app.rest()
            await self._refresh()
            return

        if item_id.startswith("expand_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("expand_")
            territory = self.app.corp_map.territories[territory_id]
            if expand_into(corp_state, self.app.corp_map, territory_id, self.app.rng):
                self.notify(f"Claimed {territory.name}.")
                log_faction_event(
                    self.app.faction_events,
                    corp_state.faction_id,
                    FactionEvent(kind="territory", day=self.app.character.day, territory_id=territory_id),
                )
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("surveil_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("surveil_")
            territory = self.app.corp_map.territories[territory_id]
            if raise_surveillance(corp_state, self.app.corp_map, territory_id):
                self.notify(f"Surveillance raised in {territory.name}.")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("develop_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("develop_")
            territory = self.app.corp_map.territories[territory_id]
            if raise_development(corp_state, self.app.corp_map, territory_id):
                self.notify(f"{territory.name} builds up. Development raised.")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("train_"):
            corp_state = self.app.corp_state
            category = EmployeeCategory(item_id.removeprefix("train_"))
            if train_employees(corp_state, self.app.corp_map, category, self.app.character.day):
                self.notify(
                    f"Training a batch of {employee_plural(category)} — "
                    f"ready in {TRAINING_DAYS[category]} days."
                )
            elif corp_state.pending_recruit is not None:
                self.notify("The Academy's already training a batch.", severity="warning")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("newacademy_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("newacademy_")
            territory = self.app.corp_map.territories[territory_id]
            if build_academy(corp_state, self.app.corp_map, territory_id):
                self.notify(f"New Academy standing in {territory.name}.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id.startswith("rebuild_"):
            corp_state = self.app.corp_state
            territory_id = item_id.removeprefix("rebuild_")
            territory = self.app.corp_map.territories[territory_id]
            if build_research_facility(corp_state, self.app.corp_map, territory_id):
                self.notify(f"New Research Facility standing in {territory.name}.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id == "build_lab":
            corp_state = self.app.corp_state
            if build_lab(corp_state, self.app.corp_map):
                self.notify("Built a new lab at the Research Facility.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()
            return

        if item_id == "build_efficiency":
            corp_state = self.app.corp_state
            if build_efficiency_upgrade(corp_state, self.app.corp_map):
                self.notify("Upgraded the Research Facility's efficiency.")
            elif corp_state.daily_action_used:
                self.notify("Already made your move today.", severity="warning")
            else:
                self.notify("Can't afford it.", severity="warning")
            await self._refresh()


class ResearchTreeScreen(BackScreen):
    """The corp's Technology tree (see corp_turn.TECHNOLOGIES/technology_tree_layout),
    reached from CorpScreen/CorpMainMenu with 't'. One Collapsible per prereq-chain
    depth ("Tier 0", "Tier 1", ...) rather than a single flat list, so the tree reads
    top-to-bottom as it deepens; each box's own "Requires: ..." line is what shows the
    edge back to its prereq — with two independent chains today that's always exactly
    one name, not worth the ASCII connector/hit-test machinery corpmap.py/matrix.py
    carry for an actual graph with branches.

    A box is never hard-disabled — selecting it always attempts
    corp_turn.research_technology(), the same "fails closed, notify() why" shape every
    other corp purchase in CorpScreen already uses — so a box short on RP stays
    selectable and reports the shortfall via its own label rather than going inert.
    The one thing worth calling out on the box itself is a prereq that isn't met yet,
    since no amount of RP would make selecting it succeed."""

    BINDINGS = MENU_BACK_BINDINGS

    CSS = """
    ListView {
        height: auto;
    }

    ListItem.tech_box {
        height: auto;
        border: round $accent;
        padding: 0 1;
        margin: 0 0 1 0;
    }

    ListItem.tech_box.-researched {
        border: round $success;
    }

    ListItem.tech_box.-locked {
        border: round $panel;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="tree_info")
        max_tier = max(col for col, _ in technology_tree_layout().values())
        panels = [
            Collapsible(
                ListView(id=f"tier_{tier}_list"),
                title=f"Tier {tier}",
                collapsed=False,
                id=f"tier_{tier}_panel",
            )
            for tier in range(max_tier + 1)
        ]
        yield ScrollableContainer(*panels, id="tiers")
        yield Footer()

    async def on_mount(self) -> None:
        await self._refresh()

    async def on_screen_resume(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        corp_state = self.app.corp_state
        self.query_one("#tree_info", Static).update(f"{corp_state.research_points:g}rp available.")

        layout = technology_tree_layout()
        max_tier = max(col for col, _ in layout.values())
        by_tier: dict[int, list] = {tier: [] for tier in range(max_tier + 1)}
        for technology in TECHNOLOGIES:
            by_tier[layout[technology.id][0]].append(technology)

        for tier, technologies in by_tier.items():
            list_view = self.query_one(f"#tier_{tier}_list", ListView)
            items = [self._tech_item(corp_state, technology) for technology in technologies]
            await _replace_items(list_view, items)

    def _tech_item(self, corp_state: CorpState, technology) -> ListItem:
        researched = has_technology(corp_state, technology.id)
        locked = not researched and not prereqs_met(corp_state, technology)

        detail_lines = []
        if researched:
            detail_lines.append("Researched")
        elif locked:
            names = ", ".join(TECHNOLOGIES_BY_ID[prereq].name for prereq in technology.prereqs)
            detail_lines.append(f"Locked — requires {names}")
        else:
            cost_line = f"{technology.cost}rp"
            if technology.cost > corp_state.research_points:
                short = technology.cost - corp_state.research_points
                cost_line += f" (need {short:g}rp more)"
            detail_lines.append(cost_line)
        detail_lines.append(technology.description)

        item = ListItem(
            Static(_boxed_text(technology.name, "\n".join(detail_lines))),
            id=f"tech_{technology.id}",
            classes="tech_box",
        )
        if researched:
            item.add_class("-researched")
        elif locked:
            item.add_class("-locked")
        return item

    async def on_list_view_selected(self, event: ListView.Selected) -> None:
        technology_id = event.item.id.removeprefix("tech_")
        corp_state = self.app.corp_state
        technology = TECHNOLOGIES_BY_ID[technology_id]

        if has_technology(corp_state, technology_id):
            return
        if not prereqs_met(corp_state, technology):
            self.notify("Research the prerequisites first.", severity="warning")
            return
        if research_technology(corp_state, technology_id):
            self.notify(f"Researched {technology.name}.")
            log_faction_event(
                self.app.faction_events,
                corp_state.faction_id,
                FactionEvent(kind="technology", day=self.app.character.day, technology_id=technology_id),
            )
        else:
            self.notify("Not enough research points.", severity="warning")
        await self._refresh()
