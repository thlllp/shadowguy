"""Views for the Arcade front-end.

A small view stack instead of a full UI framework: each view owns its input and
drawing, and the window only dispatches to the top one. Menus share `MenuView`;
tactical and matrix get dedicated views because their state is positional.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import arcade

from shadowguy.abstract_combat import CombatOutcome, available_actions, start_combat, take_turn
from shadowguy.combat import (
    KNOCKOUT_FATAL_MAX,
    KNOCKOUT_ROUGH_MAX,
    Drop,
    drop_for_result,
    knockout_roll,
    mug_unconscious,
)
from shadowguy.corp_turn import (
    ACADEMY_GRANT_COST,
    ACADEMY_TRAINING_COST,
    DEVELOPMENT_BUMP_COST,
    TECHNOLOGIES,
    TECHNOLOGIES_BY_ID,
    TRAINING_DAYS,
    EmployeeCategory,
    academy_grant_rp,
    academy_research_grant,
    attack_territory,
    build_efficiency_upgrade,
    build_lab,
    can_fundraise,
    corp_defeated,
    defense_strength,
    deploy_operatives,
    development_targets,
    employee_plural,
    expand_into,
    expansion_cost,
    force_options,
    fundraise,
    fundraise_amount,
    gather_intel,
    has_technology,
    intel_targets,
    next_academy_upgrade_cost,
    next_efficiency_cost,
    next_lab_cost,
    operative_training_cost,
    operative_training_days,
    owned_academy,
    owned_research_facility,
    prereqs_met,
    raise_development,
    raise_security,
    raise_surveillance,
    research_technology,
    sabotage,
    sabotage_targets,
    security_bump_cost,
    security_targets,
    survey,
    survey_targets,
    surveillance_bump_cost,
    surveillance_targets,
    train_employees,
    upgrade_academy,
)
from shadowguy.corpmap import (
    LocationKind,
    TerritoryModifier,
    attack_candidates,
    expansion_candidates,
    owner_label,
    travel_path,
    unowned_territory_ids,
)
from shadowguy.encounters import ARREST_CASH_PCT, ARREST_HOURS, ARREST_STANDING_HIT
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.gangs import GANGS_BY_ID
from shadowguy.gigs import GIG_FAIL_REP_HIT, GIG_FAIL_STANDING_HIT
from shadowguy.grid import Tile, visible_tiles
from shadowguy.inventory import reload_weapon, rounds_needed, toggle_equip, use_consumable
from shadowguy.jobs import JOB_FAILURE_REP_HIT, JOB_FAILURE_TRUST_HIT
from shadowguy.matrix import (
    analyze_node,
    available_matrix_actions,
    connected_nodes,
    extract,
    jack_out,
    move_to,
    player_integrity,
    render_matrix_network,
    start_matrix_run,
    take_run_turn,
    usable_analyze_program,
)
from shadowguy.scene import SceneKind, apply_outcome, resolve_choice, resolve_entrance
from shadowguy.shops import (
    AMMO_CATALOG,
    AmmoKind,
    CATALOG,
    CONSUMABLE_CATALOG,
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    PROGRAM_CATALOG,
    buy_ammo,
    buy_consumable,
    buy_item,
    buy_price,
    buy_program,
)
from shadowguy.tactical import (
    TacticalOutcome,
    begin_attack_aim,
    cancel_aim,
    confirm_aim,
    end_turn,
    leave,
    move_aim_cursor,
    move_player,
    snap_aim_to_next_target,
    start_burglary,
    start_tactical,
    take_stairs,
)


if TYPE_CHECKING:
    from .window import ShadowguyWindow


class View:
    def __init__(self, window: "ShadowguyWindow") -> None:
        self.window = window

    def draw(self) -> None:  # pragma: no cover - drawing
        ...

    def on_key(self, symbol: int, modifiers: int) -> None:  # pragma: no cover - input
        ...


class MenuView(View):
    """A list of rows with intro text. Subclasses set `rows` and implement `pick`."""

    title = "MENU"
    footer = "Up/Down move   Enter select   Esc back"

    def __init__(self, window: "ShadowguyWindow") -> None:
        super().__init__(window)
        self.intro: list[str] = []
        self.rows: list[tuple[str, str]] = []
        self.index = 0
        self.message = ""

    def pick(self, row_id: str) -> None:  # pragma: no cover - subclass
        ...

    def cancel(self) -> None:
        self.window.pop()

    def draw(self) -> None:  # pragma: no cover - drawing
        w = self.window
        w.draw_map_screen()
        rect = (w.width * 0.16, 90, w.width * 0.68, w.height - 200)
        y = w.panel(rect, self.title)
        x = rect[0] + 18
        for line in self.intro:
            w.text(line, x, y, size=11, color=w.MUTED)
            y -= 17
        if self.intro:
            y -= 6
        for i, (_row_id, label) in enumerate(self.rows):
            color = w.ACCENT if i == self.index else w.TEXT
            prefix = "> " if i == self.index else "  "
            w.text(prefix + label, x, y, size=11, color=color, bold=i == self.index)
            y -= 18
        if self.message:
            w.text(self.message, x, rect[1] + 26, size=11, color=w.YELLOW)
        w.text(self.footer, x, rect[1] + 10, size=10, color=w.MUTED)

    def on_key(self, symbol: int, modifiers: int) -> None:  # pragma: no cover - input
        if not self.rows:
            if symbol == arcade.key.ESCAPE:
                self.cancel()
            return
        self.index = min(self.index, len(self.rows) - 1)
        if symbol in (arcade.key.UP, arcade.key.W):
            self.index = (self.index - 1) % len(self.rows)
        elif symbol in (arcade.key.DOWN, arcade.key.S):
            self.index = (self.index + 1) % len(self.rows)
        elif symbol in (arcade.key.ENTER, arcade.key.SPACE):
            if self.rows:
                self.pick(self.rows[self.index][0])
        elif symbol == arcade.key.ESCAPE:
            self.cancel()


def _corp_over(window) -> bool:
    """Push the GUI's game-over view if a day tick broke the corp up. The Textual
    app's own app.exit() only stops a Textual run; the Arcade client has to notice
    the same condition itself."""
    app = window.session.app
    if not app.corp_only or app.corp_state is None:
        return False
    if not corp_defeated(app.corp_state, app.corp_map):
        return False
    faction = FACTIONS_BY_ID[app.corp_state.faction_id]
    window.push(GameOverView(window, f"{faction.name} has been broken up. Game over."))
    return True


class StartView(MenuView):
    title = "SHADOWGUY"

    def __init__(self, window: "ShadowguyWindow") -> None:
        super().__init__(window)
        self.intro = ["Choose a run"]
        self.rows = [
            ("runner", "Runner — one character, one life"),
            ("corp", "Corp — run a faction"),
            ("load", "Load the newest save"),
        ]

    def pick(self, row_id: str) -> None:
        if row_id == "runner":
            self.window.start_runner()
        elif row_id == "corp":
            self.window.push(FactionPickView(self.window))
        else:
            error = self.window.session.load_latest()
            if error is not None:
                self.message = error
                return
            self.window.show_map()
            self.window.map_view.message = "Loaded the newest save."

    def on_key(self, symbol: int, modifiers: int) -> None:
        if symbol in (arcade.key.ESCAPE, arcade.key.Q):
            self.window.close()
            return
        super().on_key(symbol, modifiers)


class FactionPickView(MenuView):
    title = "PICK A CORP"

    def __init__(self, window: "ShadowguyWindow") -> None:
        super().__init__(window)
        self.intro = ["Every faction starts with six districts and two action points a day."]
        self.rows = [
            (f"faction:{faction.id}", f"{faction.name} — {faction.specialty}")
            for faction in FACTIONS
        ]

    def pick(self, row_id: str) -> None:
        self.window.start_corp(row_id.removeprefix("faction:"))

    def on_key(self, symbol: int, modifiers: int) -> None:
        if symbol == arcade.key.Q:
            self.window.close()
            return
        super().on_key(symbol, modifiers)


class MapView(View):
    def __init__(self, window: "ShadowguyWindow") -> None:
        super().__init__(window)
        self.selected_id = window.session.character.location_id
        if window.session.app.corp_only:
            self.message = (
                "Arrows/WASD move | Enter relocate | C corp actions | R end day | "
                "F5 save | F9 load | Q quit"
            )
        else:
            self.message = "Arrows move | Enter travel | R rest | F fixers | J jobs | S shops | I gear | G den | V deliver | F5 save | F9 load | Q quit"

    def draw(self) -> None:  # pragma: no cover - drawing
        self.window.selected_id = self.selected_id
        self.window.draw_map_screen(self.message)

    def on_key(self, symbol: int, modifiers: int) -> None:  # pragma: no cover - input
        if symbol in (arcade.key.ESCAPE, arcade.key.Q):
            self.window.close()
            return
        corp_only = self.window.session.app.corp_only
        if symbol == arcade.key.R:
            self._rest()
            return
        if symbol == arcade.key.F5:
            self._save()
            return
        if symbol == arcade.key.F9:
            self._load()
            return
        if corp_only:
            if symbol == arcade.key.C:
                self.window.push(CorpActionsView(self.window, self))
                return
        else:
            if symbol == arcade.key.F:
                self._open_fixers()
                return
            if symbol == arcade.key.J:
                self._open_jobs()
                return
            if symbol == arcade.key.S:
                self._open_shops()
                return
            if symbol == arcade.key.I:
                self.window.push(InventoryView(self.window))
                return
            if symbol == arcade.key.G:
                self._open_gang_den()
                return
            if symbol == arcade.key.V:
                self._deliver_smuggling()
                return
        if symbol in (arcade.key.ENTER, arcade.key.SPACE):
            self._travel()
            return

        dx = dy = 0
        if symbol in (arcade.key.LEFT, arcade.key.A):
            dx = -1
        elif symbol in (arcade.key.RIGHT, arcade.key.D):
            dx = 1
        elif symbol in (arcade.key.UP, arcade.key.W):
            dy = -1
        elif symbol in (arcade.key.DOWN, arcade.key.S):
            dy = 1
        if dx or dy:
            self.window.move_cursor(self, dx, dy)

    def _travel(self) -> None:
        session = self.window.session
        character = session.character
        if self.selected_id == character.location_id:
            self.message = "You are already here."
            return
        if session.app.corp_only:
            character.location_id = self.selected_id
            self.message = (
                f"Relocated to {session.territory(self.selected_id).name} - "
                f"{self.window.clock()}"
            )
            return
        path = travel_path(session.corp_map, character.location_id, self.selected_id)
        if not path:
            self.message = "No route."
            return
        for next_id in path:
            encounter = session.travel_hop(next_id)
            if encounter is not None:
                self.selected_id = next_id
                self.window.push(TollView(self.window, encounter, self))
                return
        message = f"Travelled to {session.territory(self.selected_id).name} - {self.window.clock()}"
        job = character.smuggling_job
        if job is not None and character.location_id == job.destination_territory_id:
            message = (
                f"Arrived — deliver the package with V (due day {job.deadline_day})."
            )
        self.message = message

    def _open_fixers(self) -> None:
        fixers = self.window.session.fixers_here()
        if not fixers:
            self.message = "No fixer seated here."
            return
        self.window.push(FixerPickView(self.window, fixers))

    def _open_jobs(self) -> None:
        jobs = self.window.session.jobs_here()
        if not jobs:
            self.message = "No accepted job is on site."
            return
        self.window.push(JobsView(self.window, jobs))

    def _open_shops(self) -> None:
        shops = self.window.session.local_shops()
        if not shops:
            self.message = "No shop here."
            return
        self.window.push(ShopPickView(self.window, shops))

    def _rest(self) -> None:
        session = self.window.session
        character = session.character
        job_before = character.smuggling_job
        session.rest()
        if _corp_over(self.window):
            return
        if (
            job_before is not None
            and character.smuggling_job is None
            and character.day > job_before.deadline_day
        ):
            gang = GANGS_BY_ID[job_before.gang_id]
            self.message = (
                f"Rested — but the {gang.name} delivery expired. Standing -2."
            )
        elif character.smuggling_job is not None:
            self.message = (
                f"Rested — delivery still due day {character.smuggling_job.deadline_day}."
            )
        else:
            self.message = f"Rested - {self.window.clock()}"

    def _open_gang_den(self) -> None:
        session = self.window.session
        territory = session.territory(session.character.location_id)
        if not territory.gang_id:
            self.message = "No gang den here."
            return
        den = next(
            (location for location in territory.locations if location.kind is LocationKind.GANG_DEN),
            None,
        )
        if den is None:
            self.message = "No gang den here."
            return
        self.window.push(GangDenView(self.window, GANGS_BY_ID[territory.gang_id]))

    def _deliver_smuggling(self) -> None:
        session = self.window.session
        character = session.character
        job = character.smuggling_job
        if job is None:
            self.message = "You aren't carrying a delivery."
            return
        if character.location_id != job.destination_territory_id:
            destination = session.territory(job.destination_territory_id)
            self.message = (
                f"Deliver it in {destination.name} by day {job.deadline_day}."
            )
            return
        delivered = session.deliver_smuggling_job()
        if delivered is None:
            self.message = "You can't deliver that here."
            return
        self.message = f"Delivered. +{delivered.reward_cash}eb."

    def _save(self) -> None:
        try:
            label = self.window.session.save()
        except Exception as exc:
            self.message = f"Couldn't save: {exc}"
            return
        self.message = f"Saved - {label}."

    def _load(self) -> None:
        error = self.window.session.load_latest()
        if error is not None:
            self.message = error
            return
        self.window.show_map()
        self.window.map_view.message = "Loaded the newest save."


class CorpActionsView(MenuView):
    """The corp-only run's daily menu: territory moves for the cursored district
    plus the corp-wide Academy, Research and Operations actions. Kept as one short
    list rather than the TUI's five panels; anything that needs a second step
    (force size, the tech tree) pushes its own view."""

    title = "CORP ACTIONS"

    def __init__(self, window: "ShadowguyWindow", map_view: MapView) -> None:
        super().__init__(window)
        self.map_view = map_view
        self._build()

    def _state(self):
        return self.window.session.app.corp_state

    def _map(self):
        return self.window.session.corp_map

    def _here(self):
        return self._map().territories[self.map_view.selected_id]

    def _build(self) -> None:
        state = self._state()
        corp_map = self._map()
        here = self._here()
        faction = FACTIONS_BY_ID[state.faction_id]
        self.intro = [
            f"{faction.name} — {state.cash}eb — {state.research_points:g}rp — "
            f"{state.action_points} AP",
            f"{here.name}: {owner_label(here.owner)}, value {here.value}, "
            f"garrison {here.garrison}, Security {here.modifiers.get(TerritoryModifier.SECURITY, 0)}",
        ]
        rows: list[tuple[str, str]] = []

        for territory_id in expansion_candidates(corp_map, state.faction_id):
            target = corp_map.territories[territory_id]
            cost = expansion_cost(target, state, corp_map)
            rows.append((f"expand:{territory_id}", f"Expand into {target.name} — {cost}eb"))
        survey_ids = {territory.id for territory in survey_targets(state, corp_map)}
        if here.id in survey_ids:
            rows.append((f"survey:{here.id}", f"Survey {here.name} — free"))
        security_ids = {territory.id for territory in security_targets(state, corp_map)}
        if here.id in security_ids:
            level = here.modifiers.get(TerritoryModifier.SECURITY, 0)
            rows.append(
                (
                    f"secure:{here.id}",
                    f"Raise Security {level} -> {level + 1} — "
                    f"{security_bump_cost(state, here)}eb",
                )
            )
        surveillance_ids = {territory.id for territory in surveillance_targets(state, corp_map)}
        if here.id in surveillance_ids:
            level = here.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
            rows.append(
                (
                    f"surveil:{here.id}",
                    f"Raise Surveillance {level} -> {level + 1} — "
                    f"{surveillance_bump_cost(state, here)}eb",
                )
            )
        development_ids = {territory.id for territory in development_targets(state, corp_map)}
        if here.id in development_ids:
            level = here.modifiers.get(TerritoryModifier.DEVELOPMENT, 0)
            rows.append(
                (
                    f"develop:{here.id}",
                    f"Develop {level} -> {level + 1} — {DEVELOPMENT_BUMP_COST}eb",
                )
            )
        if can_fundraise(state, corp_map):
            rows.append(
                ("fundraise", f"Fundraise — +{fundraise_amount(state, corp_map)}eb")
            )

        academy = owned_academy(state, corp_map)
        pending = state.pending_recruit
        if pending is not None:
            days_left = pending.ready_day - self.window.session.character.day
            rows.append(
                (
                    "inert",
                    f"Training {employee_plural(pending.category)} — ready in "
                    f"{days_left} day{'s' if days_left != 1 else ''}",
                )
            )
        elif academy is not None:
            upgrade_cost = next_academy_upgrade_cost(academy)
            if upgrade_cost is not None:
                rows.append(("upgrade_academy", f"Expand Academy — {upgrade_cost}eb"))
            grant_rp = academy_grant_rp(state, corp_map)
            if grant_rp:
                rows.append(
                    (
                        "grant",
                        f"Research grant — +{grant_rp}rp — {ACADEMY_GRANT_COST}eb",
                    )
                )
            for category in EmployeeCategory:
                if category is EmployeeCategory.OPERATIVE:
                    cost = operative_training_cost(state)
                    days = operative_training_days(state)
                else:
                    cost = ACADEMY_TRAINING_COST[category]
                    days = TRAINING_DAYS[category]
                rows.append(
                    (
                        f"train:{category}",
                        f"Train {employee_plural(category)} ({days}d) — {cost}eb",
                    )
                )

        facility = owned_research_facility(state, corp_map)
        if facility is not None:
            lab_cost = next_lab_cost(facility)
            if lab_cost is not None:
                rows.append(("lab", f"Build a lab — {lab_cost}eb"))
            efficiency_cost = next_efficiency_cost(facility)
            if efficiency_cost is not None:
                rows.append(("efficiency", f"Upgrade efficiency — {efficiency_cost}eb"))
        rows.append(("research_tree", "Open the research tree"))

        if here.owner == state.faction_id and state.operatives:
            rows.append((f"deploy:{here.id}", f"Deploy operatives here — {state.operatives} spare"))
        if here.id in set(attack_candidates(corp_map, state.faction_id)) and state.operatives:
            rows.append(
                (
                    f"attack:{here.id}",
                    f"Attack {here.name} — defense {defense_strength(here)}",
                )
            )
        intel_ids = {territory.id for territory in intel_targets(state, corp_map)}
        if here.id in intel_ids:
            rows.append((f"intel:{here.id}", f"Gather intel on {here.name}"))
        sabotage_ids = {territory.id for territory in sabotage_targets(state, corp_map)}
        if here.id in sabotage_ids:
            rows.append(
                (
                    f"sabotage:{here.id}",
                    f"Sabotage {here.name} — Security "
                    f"{here.modifiers.get(TerritoryModifier.SECURITY, 0)}",
                )
            )

        rows.append(("rest", "End day — rest to 6 AM"))
        self.rows = rows

    def pick(self, row_id: str) -> None:
        kind, _, argument = row_id.partition(":")
        state = self._state()
        corp_map = self._map()
        session = self.window.session
        here = self._here()
        if kind == "inert":
            return
        if kind == "expand":
            if expand_into(state, corp_map, argument, session.rng):
                self.message = f"Claimed {corp_map.territories[argument].name}."
            else:
                self.message = "Can't expand there."
        elif kind == "survey":
            target = survey(state, corp_map, argument)
            self.message = (
                f"Surveyed {target.name}: garrison {target.garrison}, value {target.value}."
                if target is not None
                else "Can't survey there."
            )
        elif kind == "secure":
            self.message = (
                f"Security raised in {here.name}."
                if raise_security(state, corp_map, argument)
                else "Can't afford it."
            )
        elif kind == "surveil":
            self.message = (
                f"Surveillance raised in {here.name}."
                if raise_surveillance(state, corp_map, argument)
                else "Can't afford it."
            )
        elif kind == "develop":
            self.message = (
                f"{here.name} developed."
                if raise_development(state, corp_map, argument)
                else "Can't afford it."
            )
        elif kind == "fundraise":
            raised = fundraise(state, corp_map)
            self.message = (
                f"Emergency fundraising: +{raised}eb."
                if raised is not None
                else "Can't fundraise right now."
            )
        elif kind == "upgrade_academy":
            self.message = (
                "Academy expanded."
                if upgrade_academy(state, corp_map)
                else "Can't expand the Academy."
            )
        elif kind == "grant":
            grant_rp = academy_grant_rp(state, corp_map)
            self.message = (
                f"Research grant paid: +{grant_rp}rp."
                if academy_research_grant(state, corp_map)
                else "Can't take a grant right now."
            )
        elif kind == "train":
            category = EmployeeCategory(argument)
            if train_employees(state, corp_map, category, session.character.day):
                self.message = f"Training {employee_plural(category)}."
            else:
                self.message = "Can't start that batch."
        elif kind == "lab":
            self.message = "Lab built." if build_lab(state, corp_map) else "Can't build a lab."
        elif kind == "efficiency":
            self.message = (
                "Efficiency upgraded."
                if build_efficiency_upgrade(state, corp_map)
                else "Can't upgrade efficiency."
            )
        elif kind == "research_tree":
            self.window.push(ResearchTreeView(self.window, self._build))
            return
        elif kind == "deploy":
            self.window.push(
                ForcePickView(
                    self.window,
                    f"Deploy to {here.name}. Send how many?",
                    state.operatives,
                    self._deploy,
                )
            )
            return
        elif kind == "attack":
            self.window.push(
                ForcePickView(
                    self.window,
                    f"Attack {here.name} — defense {defense_strength(here)}. "
                    f"Commit how many?",
                    state.operatives,
                    self._attack,
                )
            )
            return
        elif kind == "intel":
            self.message = (
                f"Intel gathered on {here.name}."
                if gather_intel(state, corp_map, argument, session.rng)
                else "The operative came back empty-handed."
            )
        elif kind == "sabotage":
            result = sabotage(state, corp_map, argument, session.rng)
            self.message = (
                f"Sabotage in {result} — Security "
                f"{here.modifiers.get(TerritoryModifier.SECURITY, 0)}."
                if result is not None
                else "Sabotage failed — operative captured or killed."
            )
        elif kind == "rest":
            session.rest()
            if _corp_over(self.window):
                return
            self.message = (
                f"Day {session.character.day}, {session.character.hour_of_day:02d}:00 — "
                f"{state.action_points} AP."
            )
            self.map_view.message = self.message
        self._build()

    def _deploy(self, count: int) -> None:
        state = self._state()
        here = self._here()
        if deploy_operatives(state, self._map(), here.id, count):
            self.message = f"{count} operative(s) deployed to {here.name}."
        else:
            self.message = "Can't deploy that force."
        self._build()

    def _attack(self, count: int) -> None:
        state = self._state()
        corp_map = self._map()
        here = self._here()
        result = attack_territory(state, corp_map, here.id, count, self.window.session.rng)
        if result is None:
            self.message = "Can't attack that district."
        elif result.captured:
            self.message = (
                f"{here.name} is yours — {result.attacker_losses} lost, "
                f"{here.garrison} holding it."
            )
        else:
            self.message = f"Attack repelled — {result.attacker_losses} lost."
        self._build()


class ForcePickView(MenuView):
    title = "SEND OPERATIVES"

    def __init__(self, window, prompt: str, available: int, callback) -> None:
        super().__init__(window)
        self.intro = [prompt]
        self.callback = callback
        self.rows = [
            (f"force:{count}", f"{count} operative{'s' if count != 1 else ''}")
            for count in force_options(available)
        ]

    def pick(self, row_id: str) -> None:
        count = int(row_id.removeprefix("force:"))
        self.window.pop()
        self.callback(count)


class ResearchTreeView(MenuView):
    title = "RESEARCH TREE"

    def __init__(self, window, on_close=None) -> None:
        super().__init__(window)
        self.on_close = on_close
        self._build()

    def _state(self):
        return self.window.session.app.corp_state

    def _build(self) -> None:
        state = self._state()
        self.intro = [f"{state.research_points:g}rp available"]
        rows = []
        for technology in TECHNOLOGIES:
            if technology.faction_id is not None and technology.faction_id != state.faction_id:
                continue
            if has_technology(state, technology.id):
                label = f"{technology.name} — researched"
            elif not prereqs_met(state, technology):
                missing = ", ".join(
                    TECHNOLOGIES_BY_ID[prereq].name
                    for prereq in technology.prereqs
                    if not has_technology(state, prereq)
                )
                label = f"{technology.name} — needs {missing}"
            else:
                label = f"{technology.name} — {technology.cost}rp"
            rows.append((f"tech:{technology.id}", label))
        self.rows = rows

    def pick(self, row_id: str) -> None:
        technology = TECHNOLOGIES_BY_ID[row_id.removeprefix("tech:")]
        if research_technology(self._state(), technology.id):
            self.message = f"Researched {technology.name}."
        else:
            self.message = "Can't research that yet."
        self._build()

    def cancel(self) -> None:
        self.window.pop()
        if self.on_close is not None:
            self.on_close()


class TollView(MenuView):
    title = "ENCOUNTER"

    def __init__(self, window, encounter, map_view: MapView) -> None:
        super().__init__(window)
        self.encounter = encounter
        self.map_view = map_view
        if encounter.kind == "gang":
            gang = encounter.encounter
            self.prompt = (
                f"{gang.gang.name} block your way - {gang.toll}eb to pass."
                if gang.toll is not None
                else f"{gang.gang.name} come at you without a word."
            )
        else:
            corp = encounter.encounter
            self.prompt = (
                f"{corp.faction.name} security stops you in {corp.territory_name} - "
                f"pay {corp.fine}eb and leave, or they take you in."
                if corp.fine is not None
                else f"{corp.faction.name} security moves to detain you."
            )
        self.intro = [self.prompt]
        self._build()

    def _build(self) -> None:
        enc = self.encounter.encounter
        if self.encounter.kind == "gang":
            cost = enc.toll
            if cost is None:
                self.rows = [("fight", "Fight")]
            elif self.window.session.character.cash >= cost:
                self.rows = [("pay", f"Pay {cost}eb"), ("fight", "Refuse - they'll come at you")]
            else:
                self.rows = [("fight", "Refuse - you can't cover the toll")]
        else:
            cost = enc.fine
            if cost is None:
                self.rows = [("fight", "They attack")]
            elif self.window.session.character.cash >= cost:
                self.rows = [("pay", f"Pay {cost}eb"), ("fight", "Refuse - they'll take you in")]
            else:
                self.rows = [("fight", "Refuse - you can't cover the fine")]

    def pick(self, row_id: str) -> None:
        session = self.window.session
        enc = self.encounter.encounter
        if row_id == "pay":
            if self.encounter.kind == "gang":
                session.pay_gang_toll(enc)
                self.window.pop()
                self.map_view.message = f"Paid off {enc.gang.name}. Standing {enc.gang.name}: {session.character.gang_standing_with(enc.gang.id):+d}."
            else:
                session.pay_corp_fine(enc)
                self.window.pop()
                self.map_view.selected_id = session.character.location_id
                self.map_view.message = f"Escorted out of {enc.territory_name}."
            return
        if self.encounter.kind == "gang":
            fight = session.gang_fight(enc)
        else:
            fight = session.corp_fight(enc)
        self.window.pop()
        self.window.push(CombatView(self.window, fight, Drop.ENEMY, self._after_fight))

    def _after_fight(self, outcome: CombatOutcome) -> None:
        """Resolve what the fight meant, matching the TUI's EncounterMixin: a death
        ends the run, a knockout is a wake-up roll (a gang mugs you, corp security
        arrests you), and anything else just reports the result."""
        character = self.window.session.character
        if outcome is CombatOutcome.DEAD:
            self.window.pop()
            self.window.push(GameOverView(self.window, "You died."))
            return
        if outcome is CombatOutcome.KNOCKED_OUT:
            self.window.pop()
            roll = knockout_roll(self.window.session.rng)
            if roll <= KNOCKOUT_FATAL_MAX:
                self.window.push(GameOverView(self.window, "You didn't wake up."))
                return
            if self.encounter.kind == "gang":
                mug_unconscious(character)
                self.map_view.message = "You came to in an alley, lighter a few creds."
                return
            encounter = self.encounter.encounter
            lost = int(character.cash * ARREST_CASH_PCT)
            character.cash -= lost
            character.adjust_standing(encounter.faction.id, ARREST_STANDING_HIT)
            self.window.session.app.spend_time(ARREST_HOURS)
            unowned = unowned_territory_ids(self.window.session.corp_map)
            character.location_id = (
                unowned[0] if unowned else self.window.session.corp_map.player_start_id
            )
            self.map_view.selected_id = character.location_id
            self.map_view.message = (
                f"{encounter.faction.name} holds you for hours, then dumps you on the "
                f"street. Lighter by {lost}eb."
            )
            return
        self.map_view.message = f"Fight over: {outcome.name.title()}."
        self.window.pop()


class FixerPickView(MenuView):
    title = "FIXERS"

    def __init__(self, window, fixers) -> None:
        super().__init__(window)
        self.fixers = fixers
        self._build()

    def _build(self) -> None:
        self.intro = []
        self.rows = [(fixer.id, f"{fixer.name} - {fixer.specialty}") for fixer in self.fixers]

    def pick(self, row_id: str) -> None:
        fixer = next(f for f in self.fixers if f.id == row_id)
        self.window.push(FixerView(self.window, fixer))


class FixerView(MenuView):
    title = "FIXER"

    def __init__(self, window, fixer) -> None:
        super().__init__(window)
        self.fixer = fixer
        self._build()

    def _build(self) -> None:
        self.intro = [f"{self.fixer.name} - {self.fixer.specialty}"]
        self.rows = []
        for offer in self.fixer.offers:
            scene = offer.scene
            deadline = ""
            if offer.timing.deadline_day is not None:
                deadline = f" | by day {offer.timing.deadline_day}"
            elif offer.timing.scheduled_day is not None:
                deadline = f" | day {offer.timing.scheduled_day}"
            if offer.taken_by is not None:
                taken_by = self.window.session.app.runner(offer.taken_by).name
                label = f"{scene.title} - TAKEN, {taken_by} got there first"
            else:
                label = f"{scene.title} ({scene.hours_cost}h){deadline}"
            self.rows.append((f"offer:{offer.id}", label))
        for contract in self.fixer.security_offers:
            self.rows.append(
                (f"sec:{contract.id}", f"Security - {contract.nights_total} nights, {contract.nightly_pay}eb/night")
            )

    def pick(self, row_id: str) -> None:
        session = self.window.session
        if row_id.startswith("offer:"):
            offer = next(o for o in self.fixer.offers if o.id == row_id.removeprefix("offer:"))
            if not session.accept_offer(self.fixer, offer):
                self.message = "Someone else already took that one."
                return
            self.message = f"Accepted {offer.scene.title}."
        else:
            contract = next(c for c in self.fixer.security_offers if c.id == row_id.removeprefix("sec:"))
            session.character.accept_security_contract(contract)
            self.fixer.security_offers = [
                c for c in self.fixer.security_offers if c.id != contract.id
            ]
            self.message = f"Accepted security at {session.territory(contract.territory_id).name}."
        self._build()


class JobsView(MenuView):
    title = "JOBS ON SITE"

    def __init__(self, window, jobs) -> None:
        super().__init__(window)
        self.jobs = jobs
        self.rows = [(job.id, job.scene.title) for job in jobs]

    def pick(self, row_id: str) -> None:
        job = next(j for j in self.jobs if j.id == row_id)
        scene = self.window.session.start_job(job)
        # Replace the list with the scene so finishing returns to the map, not to a
        # stale "jobs on site" list. Push the scene before `start()`: a burglary
        # start pushes its entrance picker, which must land on top of this view.
        view = SceneView(self.window, scene, self.window.pop)
        self.window.pop()
        self.window.push(view)
        view.start()


class ShopPickView(MenuView):
    title = "LOCAL SHOPS"

    def __init__(self, window, shops) -> None:
        super().__init__(window)
        self.shops = shops
        self.rows = [(shop.id, f"{shop.name} ({shop.kind.value})") for shop in shops]

    def pick(self, row_id: str) -> None:
        shop = next(s for s in self.shops if s.id == row_id)
        self.window.push(ShopView(self.window, shop))


class GangDenView(MenuView):
    title = "GANG DEN"

    def __init__(self, window, gang) -> None:
        super().__init__(window)
        self.gang = gang
        self._build()

    def _build(self) -> None:
        character = self.window.session.character
        standing = character.gang_standing_with(self.gang.id)
        self.intro = [
            f"{self.gang.name} — {self.gang.description} (standing {standing:+d})"
        ]
        job = character.smuggling_job
        if job is not None:
            destination = self.window.session.territory(job.destination_territory_id)
            self.intro.append(
                f"Carrying {job.item} to {destination.name} by day {job.deadline_day}."
            )
            self.rows = [("busy", "Already carrying a job — deliver it or wait it out.")]
        else:
            self.rows = [("take_job", "Take a delivery job")]

    def pick(self, row_id: str) -> None:
        if row_id != "take_job":
            return
        job = self.window.session.take_smuggling_job(self.gang.id)
        if job is None:
            self.message = "You're already carrying a job for them."
        else:
            destination = self.window.session.territory(job.destination_territory_id)
            self.message = (
                f"{self.gang.name} hands you {job.item}. Get it to {destination.name} "
                f"by day {job.deadline_day}."
            )
            if self.window.map_view is not None:
                self.window.map_view.message = (
                    f"Delivery: {job.item} to {destination.name} by day {job.deadline_day}."
                )
        self._build()


class ShopView(MenuView):
    title = "SHOP"

    def __init__(self, window, location) -> None:
        super().__init__(window)
        self.location = location
        self._build()

    def _standing(self) -> int:
        owner = self.location.characters[0] if self.location.characters else None
        return self.window.session.character.local_standing_with(owner.id) if owner else 0

    def _build(self) -> None:
        c = self.window.session.character
        standing = self._standing()
        self.intro = [f"{self.location.name} - standing {standing:+d}"]
        rows = []
        for item in CATALOG.get(self.location.kind, []):
            if item.min_standing > standing:
                continue
            price = buy_price(item.price, standing, c)
            rows.append((f"item:{item.id}", f"{item.name} - {price}eb"))
        for consumable in CONSUMABLE_CATALOG.get(self.location.kind, []):
            if consumable.min_standing > standing:
                continue
            price = buy_price(consumable.price, standing, c)
            rows.append((f"consumable:{consumable.id}", f"{consumable.name} - {price}eb"))
        for ammo in AMMO_CATALOG.get(self.location.kind, []):
            price = buy_price(ammo.price, standing, c)
            rows.append((f"ammo:{ammo.kind.value}", f"{ammo.name} - {price}eb"))
        for program in PROGRAM_CATALOG.get(self.location.kind, []):
            if program.min_standing > standing:
                continue
            price = buy_price(program.price, standing, c)
            rows.append((f"program:{program.id}", f"{program.name} - {price}eb"))
        self.rows = rows

    def pick(self, row_id: str) -> None:
        c = self.window.session.character
        standing = self._standing()
        kind, item_id = row_id.split(":", 1)
        if kind == "item":
            if not buy_item(c, ITEMS_BY_ID[item_id], standing):
                self.message = "Can't afford it."
                return
        elif kind == "consumable":
            if not buy_consumable(c, CONSUMABLES_BY_ID[item_id], standing):
                self.message = "Can't afford it."
                return
        elif kind == "ammo":
            if not buy_ammo(c, AmmoKind(item_id), standing):
                self.message = "Can't afford it."
                return
        else:
            result = buy_program(c, item_id, standing)
            # buy_program reports every failure ("Can't afford …", "Already own …",
            # "… isn't available") and only says "Bought …" on success.
            self.message = result
            if result.startswith("Bought"):
                self._build()
            return
        self.message = f"Bought {item_id}."
        self._build()


class InventoryView(MenuView):
    title = "GEAR"

    def __init__(self, window) -> None:
        super().__init__(window)
        self._build()

    def _build(self) -> None:
        c = self.window.session.character
        self.intro = [f"Cash {c.cash}eb | Health {c.health}/{c.max_health}"]
        rows = []
        for index, entry in enumerate(c.inventory):
            item = ITEMS_BY_ID[entry.item_id]
            state = "Equipped" if entry.equipped else "Stowed"
            rows.append((f"toggle:{index}", f"{state} - {item.name}"))
            if item.ammo is not None:
                reserve = c.ammo.get(item.ammo.value, 0)
                if rounds_needed(c, item) and reserve:
                    rows.append((f"reload:{index}", f"Reload {item.name} - {reserve} held"))
        for index, consumable_id in enumerate(c.consumables):
            rows.append((f"use:{index}", f"Use {CONSUMABLES_BY_ID[consumable_id].name}"))
        self.rows = rows

    def pick(self, row_id: str) -> None:
        c = self.window.session.character
        kind, index_s = row_id.split(":", 1)
        index = int(index_s)
        if kind == "toggle":
            toggle_equip(c, index)
        elif kind == "reload":
            self.message = reload_weapon(c, index)
        else:
            self.message = use_consumable(c, index)
        self._build()


class CombatView(MenuView):
    title = "FIGHT"

    def __init__(self, window, encounter, drop: Drop, callback) -> None:
        super().__init__(window)
        self.encounter = encounter
        self.callback = callback
        self.state = start_combat(window.session.character, encounter.enemies, drop, window.session.rng)
        self.intro = [encounter.prompt]
        self._build()

    def _build(self) -> None:
        self.rows = [(f"action:{i}", action.label) for i, action in enumerate(self._actions())]
        self.index = min(self.index, max(0, len(self.rows) - 1))

    def _actions(self):
        return available_actions(self.window.session.character, self.state.weapon_cooldowns)

    def draw(self) -> None:  # pragma: no cover - drawing
        w = self.window
        w.draw_map_screen()
        rect = (w.width * 0.16, 90, w.width * 0.68, w.height - 200)
        y = w.panel(rect, self.title)
        x = rect[0] + 18
        for line in self.intro:
            w.text(line, x, y, size=12, color=w.TEXT)
            y -= 20
        for fighter in self.state.fighters:
            if fighter.is_standing:
                w.text(
                    f"{fighter.enemy.name}: {fighter.health}/{fighter.enemy.health}",
                    x, y, size=11, color=w.RED,
                )
                y -= 17
        y -= 6
        for line in self.state.log[-5:]:
            w.text(line, x, y, size=10, color=w.MUTED)
            y -= 15
        if not self.state.is_over:
            for i, (_id, label) in enumerate(self.rows):
                color = w.ACCENT if i == self.index else w.TEXT
                w.text(("> " if i == self.index else "  ") + label, x, y, size=11, color=color)
                y -= 18
        else:
            w.text(f"{self.state.outcome.name.title()} - press Enter to continue.", x, y, size=12, color=w.ACCENT)
        w.text("Up/Down move   Enter select   Esc back", x, rect[1] + 10, size=10, color=w.MUTED)

    def on_key(self, symbol: int, modifiers: int) -> None:  # pragma: no cover - input
        if self.state.is_over:
            if symbol in (arcade.key.ENTER, arcade.key.SPACE):
                self.callback(self.state.outcome)
            return
        super().on_key(symbol, modifiers)

    def pick(self, row_id: str) -> None:
        index = int(row_id.removeprefix("action:"))
        action = self._actions()[index]
        take_turn(self.state, action, self.window.session.rng)
        self._build()


class SceneView(MenuView):
    title = "JOB"

    def __init__(self, window, scene, on_end) -> None:
        super().__init__(window)
        self.scene = scene
        self.on_end = on_end
        self.stage_id = scene.start_stage
        self.awaiting = False
        self.pending_next: str | None = None
        self.pending_result = None
        self._show_stage()

    def start(self) -> None:
        """Open the first stage's child view, if it has one.

        Must be called *after* this view is pushed: a burglary-start job's entrance
        picker is a separate view, and pushing it from `__init__` would bury it
        under whichever view the caller pushes next.
        """
        stage = self._stage()
        if stage.burglary is not None:
            self.window.push(EntranceView(self.window, stage.burglary, self))

    def _stage(self):
        return self.scene.stages[self.stage_id]

    def _show_stage(self) -> None:
        stage = self._stage()
        self.title = stage.prompt.split(".")[0][:40] or "JOB"
        self.intro = [stage.prompt]
        if stage.narration is not None:
            self._finish_stage_outcome(stage.narration)
            return
        self.rows = [(f"choice:{i}", choice.label) for i, choice in enumerate(stage.choices)]
        self.awaiting = False

    def pick(self, row_id: str) -> None:
        if self.awaiting:
            self._advance()
            return
        index = int(row_id.removeprefix("choice:"))
        choice = self._stage().choices[index]
        result, outcome = resolve_choice(
            self.window.session.character, self.scene, choice, self.window.session.corp_map,
            rng=self.window.session.rng,
        )
        self.pending_result = result
        self._finish_stage_outcome(outcome, applied=True)
        if not self.window.session.character.is_alive:
            self.window.push(GameOverView(self.window, "You died."))
            return

    def _finish_stage_outcome(self, outcome, *, applied: bool = False) -> None:
        if not applied:
            apply_outcome(self.window.session.character, outcome, self.scene, self.window.session.corp_map)
        self.pending_next = outcome.next_stage
        self.intro = [outcome.text]
        self.rows = [("continue", "Continue")]
        self.awaiting = True

    def _advance(self) -> None:
        if self.pending_next is None:
            self.window.session.end_scene(self.scene)
            self.on_end()
            return
        stage = self.scene.stages[self.pending_next]
        self.stage_id = stage.id
        if stage.combat is not None:
            self.window.push(
                CombatView(self.window, stage.combat, drop_for_result(self.pending_result), self._after_fight)
            )
            return
        if stage.tactical is not None:
            self.window.push(TacticalView.from_stage(self.window, stage.tactical, self._after_tactical))
            return
        if stage.matrix is not None:
            self.window.push(MatrixView(self.window, stage.matrix, self._after_matrix))
            return
        if stage.burglary is not None:
            self.window.push(EntranceView(self.window, stage.burglary, self))
            return
        self._show_stage()

    def _after_fight(self, outcome: CombatOutcome) -> None:
        self.window.pop()
        character = self.window.session.character
        if outcome is CombatOutcome.DEAD:
            self.window.push(GameOverView(self.window, "You died."))
            return
        if outcome is CombatOutcome.KNOCKED_OUT:
            # The TUI's SceneScreen applies the loss, not the victory: a knockout
            # mugs you, fails the job/gig, and returns to the map.
            roll = knockout_roll(self.window.session.rng)
            if roll <= KNOCKOUT_FATAL_MAX:
                self.window.push(GameOverView(self.window, "You didn't wake up."))
                return
            mug_unconscious(character)
            message = (
                "Most of your creds are gone."
                if roll <= KNOCKOUT_ROUGH_MAX
                else "At least you're alive."
            )
            if self.scene.kind is SceneKind.JOB:
                character.adjust_fixer_trust(self.scene.target_fixer_id, JOB_FAILURE_TRUST_HIT)
                character.adjust_rep(JOB_FAILURE_REP_HIT)
            elif self.scene.kind is SceneKind.GIG:
                character.adjust_local_standing(
                    self.scene.target_character_id, GIG_FAIL_STANDING_HIT
                )
                character.adjust_rep(GIG_FAIL_REP_HIT)
            self.window.session.end_scene(self.scene)
            self.on_end()
            if self.window.views and isinstance(self.window.views[-1], MapView):
                self.window.views[-1].message = "You came to in an alley. " + message
            return
        stage = self._stage()
        result = stage.tactical.victory if stage.tactical is not None else stage.combat.victory
        self._finish_stage_outcome(result)

    def _after_tactical(self, outcome: TacticalOutcome) -> None:
        self.window.pop()
        if outcome is TacticalOutcome.DEAD:
            self.window.push(GameOverView(self.window, "You died."))
            return
        stage = self._stage()
        result = stage.tactical.victory if outcome is TacticalOutcome.VICTORY else stage.tactical.escape
        self._finish_stage_outcome(result)

    def _after_matrix(self, outcome) -> None:
        from shadowguy.matrix import MatrixOutcome

        self.window.pop()
        stage = self._stage()
        result = stage.matrix.victory if outcome is MatrixOutcome.SEIZED else stage.matrix.escape
        self._finish_stage_outcome(result)


class EntranceView(MenuView):
    title = "ENTRANCE"

    def __init__(self, window, burglary, scene_view: SceneView) -> None:
        super().__init__(window)
        self.burglary = burglary
        self.scene_view = scene_view
        self.intro = [burglary.prompt]
        self.rows = [(f"entrance:{i}", entrance.label) for i, entrance in enumerate(burglary.entrances)]

    def pick(self, row_id: str) -> None:
        index = int(row_id.removeprefix("entrance:"))
        entrance = self.burglary.entrances[index]
        result, outcome = resolve_entrance(
            self.window.session.character, self.scene_view.scene, entrance,
            self.window.session.corp_map, rng=self.window.session.rng,
        )
        self.scene_view.pending_result = result
        self.scene_view.pending_next = outcome.next_stage
        if not self.window.session.character.is_alive:
            self.window.push(GameOverView(self.window, "You died."))
            return
        # A critical failure (or the always-loud ambush entrance) routes straight
        # to the fight instead of the walk, exactly as SceneScreen does.
        target = (
            self.scene_view.scene.stages[outcome.next_stage]
            if outcome.next_stage is not None
            else None
        )
        if target is not None and (
            target.combat is not None or target.tactical is not None or target.matrix is not None
        ):
            self.window.pop()
            self.scene_view._finish_stage_outcome(outcome, applied=True)
            return
        self.window.pop()
        self.window.push(
            TacticalView.from_burglary(
                self.window, self.burglary, entrance.spawn, self.scene_view,
            )
        )


class MatrixView(MenuView):
    title = "MATRIX"

    def __init__(self, window, stage, callback) -> None:
        super().__init__(window)
        self.stage = stage
        self.callback = callback
        self.run = start_matrix_run(window.session.character, stage.network, Drop.ENEMY, window.session.rng)
        self._build()

    def _build(self) -> None:
        self.intro = [self.stage.prompt]
        run = self.run
        if run.in_fight:
            self.rows = [
                (f"action:{i}", action.label)
                for i, action in enumerate(available_matrix_actions(self.window.session.character, run.fight.program_uses))
            ]
        else:
            rows = []
            program = usable_analyze_program(run)
            for node in connected_nodes(run):
                rows.append((f"move:{node.id}", f"Move to {node.id}"))
                if program is not None and node.id not in run.revealed_node_ids:
                    rows.append((f"analyze:{node.id}", f"Analyze {node.id}"))
            if run.can_extract:
                rows.append(("extract", "Extract the data"))
            rows.append(("jack", "Jack out"))
            self.rows = rows

    def draw(self) -> None:  # pragma: no cover - drawing
        w = self.window
        w.draw_map_screen()
        rect = (w.width * 0.12, 80, w.width * 0.76, w.height - 180)
        y = w.panel(rect, self.title)
        x = rect[0] + 18
        w.text(f"Integrity {player_integrity(w.session.character)}", x, y, size=11, color=w.ACCENT)
        y -= 20
        for line in render_matrix_network(self.run).splitlines()[:12]:
            w.text(line, x, y, size=10, color=w.TEXT)
            y -= 14
        y -= 8
        if self.run.is_over:
            w.text(f"{self.run.outcome.name.title()} - Enter to continue.", x, y, size=11, color=w.ACCENT)
        else:
            for i, (_id, label) in enumerate(self.rows):
                w.text(("> " if i == self.index else "  ") + label, x, y, size=11,
                       color=w.ACCENT if i == self.index else w.TEXT)
                y -= 17
        w.text("Up/Down move   Enter select   Esc back", x, rect[1] + 10, size=10, color=w.MUTED)

    def pick(self, row_id: str) -> None:
        run = self.run
        if run.is_over:
            self.callback(run.outcome)
            return
        if run.in_fight:
            index = int(row_id.removeprefix("action:"))
            action = available_matrix_actions(
                self.window.session.character, run.fight.program_uses
            )[index]
            take_run_turn(run, action, self.window.session.rng)
        elif row_id == "extract":
            extract(run)
        elif row_id == "jack":
            jack_out(run)
        elif row_id.startswith("move:"):
            move_to(run, row_id.removeprefix("move:"), self.window.session.rng)
        elif row_id.startswith("analyze:"):
            analyze_node(run, row_id.removeprefix("analyze:"), self.window.session.rng)
        self._build()

    def on_key(self, symbol: int, modifiers: int) -> None:  # pragma: no cover - input
        if self.run.is_over and symbol in (arcade.key.ENTER, arcade.key.SPACE):
            self.callback(self.run.outcome)
            return
        super().on_key(symbol, modifiers)


class TacticalView(View):
    """A functional grid view: move, aim/fire, end turn, leave, stairs."""

    def __init__(self, window, state, callback) -> None:
        super().__init__(window)
        self.state = state
        self.callback = callback
        self.message = "Arrows move | f attack | Tab target | Enter fire | e end turn | l leave | > stairs"
        self.aiming = False

    @classmethod
    def from_stage(cls, window, stage, callback):
        state = start_tactical(
            window.session.character, stage.grid, stage.player_start, list(stage.enemies), stage.exits
        )
        return cls(window, state, callback)

    @classmethod
    def from_burglary(cls, window, stage, spawn, scene_view):
        state = start_burglary(window.session.character, stage.building, spawn, stage.guard)
        def callback(outcome):
            scene_view.window.pop()
            if outcome is TacticalOutcome.SECURED:
                scene_view._advance()
            elif outcome is TacticalOutcome.DEAD:
                scene_view.window.push(GameOverView(scene_view.window, "You died."))
            else:
                scene_view._finish_stage_outcome(stage.bailed)
        return cls(window, state, callback)

    def draw(self) -> None:  # pragma: no cover - drawing
        w = self.window
        w.draw_map_screen()
        rect = (w.width * 0.08, 70, w.width * 0.84, w.height - 160)
        w.panel(rect, "TACTICAL")
        x = rect[0] + 18
        state = self.state
        grid = state.grid
        seen = visible_tiles(grid, state.player.coord)
        cell = min((rect[2] - 30) / grid.width, (rect[3] - 90) / grid.height)
        ox = rect[0] + 20
        oy = rect[1] + 40
        for gy in range(grid.height):
            for gx in range(grid.width):
                tile = grid.tiles[gy][gx]
                color = {
                    Tile.WALL: (40, 52, 64),
                    Tile.LOW_COVER: (74, 88, 102),
                }.get(tile, (22, 31, 40))
                if not seen[gy][gx] and (gx, gy) not in state.explored.get(state.level_index, set()):
                    color = (12, 17, 23)
                arcade.draw_lbwh_rectangle_filled(ox + gx * cell, oy + (grid.height - 1 - gy) * cell, cell - 1, cell - 1, color)
        for unit in state.units:
            ux = ox + (unit.coord[0] + 0.5) * cell
            uy = oy + (grid.height - 1 - unit.coord[1] + 0.5) * cell
            color = w.ACCENT if unit.side.name == "PLAYER" else (w.GREEN if unit.is_ally else w.RED)
            if unit.is_down:
                color = w.MUTED
            arcade.draw_circle_filled(ux, uy, max(3, cell * 0.28), color)
        for ex, ey in state.exits:
            arcade.draw_lbwh_rectangle_outline(ox + ex * cell, oy + (grid.height - 1 - ey) * cell, cell - 1, cell - 1, w.GREEN, 1)
        w.text(self.message, x, rect[1] + 10, size=10, color=w.MUTED)
        w.text(state.log[-1] if state.log else "", x, rect[1] + 26, size=10, color=w.TEXT)
        if state.is_over:
            w.text(f"{state.outcome.name.title()} - Enter to continue.", x, rect[1] + 46, size=12, color=w.ACCENT)

    def on_key(self, symbol: int, modifiers: int) -> None:  # pragma: no cover - input
        state = self.state
        if state.is_over:
            if symbol in (arcade.key.ENTER, arcade.key.SPACE):
                self.callback(state.outcome)
            return
        if self.aiming:
            if symbol == arcade.key.TAB:
                snap_aim_to_next_target(state)
            elif symbol in (arcade.key.ENTER, arcade.key.SPACE):
                confirm_aim(state, self.window.session.rng)
                # A misaimed confirm leaves the cursor open; keep our wrapper in
                # step with the engine rather than dropping out of aim mode.
                self.aiming = state.aim_cursor is not None
            elif symbol == arcade.key.ESCAPE:
                cancel_aim(state)
                self.aiming = False
            else:
                dx = dy = 0
                if symbol in (arcade.key.LEFT, arcade.key.A):
                    dx = -1
                elif symbol in (arcade.key.RIGHT, arcade.key.D):
                    dx = 1
                elif symbol in (arcade.key.UP, arcade.key.W):
                    dy = -1
                elif symbol in (arcade.key.DOWN, arcade.key.S):
                    dy = 1
                if dx or dy:
                    move_aim_cursor(state, dx, dy)
            return
        if symbol == arcade.key.F:
            self.aiming = begin_attack_aim(state)
            return
        if symbol == arcade.key.E:
            end_turn(state, self.window.session.rng)
            return
        if symbol == arcade.key.L:
            leave(state)
            return
        if symbol == arcade.key.GREATER:
            take_stairs(state)
            return
        dx = dy = 0
        if symbol in (arcade.key.LEFT, arcade.key.A):
            dx = -1
        elif symbol in (arcade.key.RIGHT, arcade.key.D):
            dx = 1
        elif symbol in (arcade.key.UP, arcade.key.W):
            dy = -1
        elif symbol in (arcade.key.DOWN, arcade.key.S):
            dy = 1
        if dx or dy:
            px, py = state.player.coord
            move_player(state, (px + dx, py + dy), self.window.session.rng)


class GameOverView(MenuView):
    title = "GAME OVER"

    def __init__(self, window, message: str) -> None:
        super().__init__(window)
        self.intro = [message]
        self.rows = [("quit", "Quit")]

    def cancel(self) -> None:
        """A dead run has no back: only Quit leaves this view."""
        return

    def pick(self, row_id: str) -> None:
        self.window.close()
