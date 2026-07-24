import random

from textual.app import App
from textual.screen import Screen

from shadowguy.character import REST_HOURS_COST, Character
from shadowguy.corp_turn import (
    CorpState,
    advance_training,
    collect_income,
    collect_research,
    employee_plural,
)
from shadowguy.corpmap import generate_corp_map, lodging_cost
from shadowguy.factions import FACTIONS
from shadowguy.fixer import create_fixers, expire_offers, refresh_offers, refresh_security_offers
from shadowguy.gigs import refresh_gigs
from shadowguy.rivals import RivalAction, resolve_rival_day
from shadowguy.saves import SaveSlot, save_game
from shadowguy.scene import Scene
from shadowguy.screens.corp_screen import CorpMainMenu
from shadowguy.screens.creation_screen import CharacterCreationScreen
from shadowguy.screens.main_menu import MainMenu
from shadowguy.screens.menu_screens import QuitMenu, TitleMenu
from shadowguy.security import resolve_security_night
from shadowguy.surveillance import resolve_surveillance_day


class ShadowguyApp(App):
    BINDINGS = [("q", "quit_menu", "Menu")]

    def __init__(self) -> None:
        super().__init__()
        self._new_run()

    def _new_run(self) -> None:
        self.rng = random.Random()
        self.corp_map = generate_corp_map(FACTIONS, self.rng)
        self.character = Character(name="Runner", location_id=self.corp_map.player_start_id)
        self.fixers = create_fixers(self.corp_map, self.rng)
        day = self.character.day
        refresh_offers(self.fixers, day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, day, self.corp_map, self.rng)
        self.location_gigs: dict[str, Scene] = {}
        refresh_gigs(self.corp_map, self.location_gigs, day, self.rng)
        self.rival_actions: list[RivalAction] = []
        self.rival_runner_locations: dict[str, str] = {}
        self.corp_state: CorpState | None = None
        self.corp_only = False

    def spend_time(
        self, hours: float, *, skip_night_effects: bool = False, protect_job_id: str | None = None
    ) -> None:
        """The single chokepoint that advances the clock — travel, every job/gig/
        legwork run, a hospital stay, and the Rest action all funnel through this
        instead of the old stamina spend. Firing a day tick is a side effect of
        crossing midnight, not something the player triggers directly: whichever
        action happens to push elapsed_hours past a multiple of HOURS_PER_DAY
        fires it, at wherever the runner currently is.

        `protect_job_id` (a Scene.id) is passed by the job/legwork run handlers so
        the job being run right now can't be expired out from under itself by the
        very tick its own hours_cost triggers — see Character.on_new_day.

        The loop below only ever runs once with today's costs (no single spend
        reaches 2*HOURS_PER_DAY), but it's written to handle more than one
        boundary crossing in a single spend — e.g. a future long job — rather
        than assuming one tick per call. rival_actions is reset once here (not
        inside the loop) so a spend crossing several boundaries accumulates every
        day's actions into one list instead of only keeping the last day's.
        """
        old_day = self.character.day
        self.character.elapsed_hours += hours
        new_day = self.character.day
        if new_day > old_day:
            self.rival_actions = []
            for day in range(old_day + 1, new_day + 1):
                self._apply_day_tick(day, skip_night_effects, protect_job_id)

    def rest_cost(self) -> int:
        """What Rest would charge for lodging right now, wherever the runner is —
        also read by MainMenu/CorpScreen to preview it on the menu item. Free under
        an active security contract here, same as an owned home (corpmap.lodging_cost)."""
        character = self.character
        here = self.corp_map.territories[character.location_id]
        active_here = any(c.territory_id == character.location_id for c in character.security_contracts)
        return 0 if active_here else lodging_cost(here)

    def rest_label(self) -> str:
        """The "Rest" menu item's text for MainMenu/CorpScreen, previewing rest_cost()."""
        cost = self.rest_cost()
        if not cost:
            return f"Rest ({REST_HOURS_COST}h)"
        return f"Rest ({REST_HOURS_COST}h, {cost}eb lodging)"

    def rest(self) -> None:
        """Shared "Rest" action wiring for MainMenu and CorpScreen. Spends exactly
        REST_HOURS_COST hours, wherever the runner currently is — same lodging pricing
        as the old midnight charge (corpmap.lodging_cost), just paid at the moment of
        resting instead of at whatever territory happened to hold the runner at
        midnight.

        Resting only halves accumulated fatigue rather than clearing it (see
        Character.fatigue) — a bad stretch of skipped rest still costs a few more
        nights to fully shake off."""
        character = self.character
        cost = self.rest_cost()
        if cost:
            paid = min(cost, character.cash)
            character.cash -= paid
            here = self.corp_map.territories[character.location_id]
            self.notify(f"Paid {paid}eb for lodging in {here.name}.")
        self.spend_time(REST_HOURS_COST)
        character.mark_rested()

    def _apply_day_tick(self, day: int, skip_night_effects: bool, protect_job_id: str | None = None) -> None:
        """Everything that used to fire from a deliberate "End the day" click —
        now fired once per day boundary crossed, by whatever action crossed it."""
        self.character.on_new_day(day, protect_job_id)
        for name in self.character.pay_crew_wages():
            self.notify(f"{name} walked off the crew — you missed payroll.", severity="warning")
        expire_offers(self.fixers, day)
        refresh_offers(self.fixers, day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, day, self.corp_map, self.rng)
        refresh_gigs(self.corp_map, self.location_gigs, day, self.rng)
        player_faction_id = self.corp_state.faction_id if self.corp_state else None
        self.rival_actions += resolve_rival_day(
            self.character, self.corp_map, day, self.rng, player_faction_id, self.rival_runner_locations
        )
        if self.corp_state:
            self.corp_state.cash += collect_income(self.corp_state, self.corp_map)
            self.corp_state.research_points += collect_research(self.corp_state, self.corp_map)
            self.corp_state.daily_action_used = False
            trained = advance_training(self.corp_state, day)
            if trained:
                self.notify(
                    f"Training complete: {trained.count} new "
                    f"{employee_plural(trained.category)} report for duty."
                )
            sightings = resolve_surveillance_day(
                self.character, self.corp_map, self.corp_state, self.rival_runner_locations, day, self.rng
            )
            if sightings:
                self.notify(f"Surveillance logged {len(sightings)} sighting(s) in your territory today.")

        if skip_night_effects:
            return
        # A hospital stay skips this block (nothing progresses on a contract you're
        # not on-site for) — everything above still fires the same as any other
        # crossing.
        character = self.character
        here_id = character.location_id
        here = self.corp_map.territories[here_id]
        # Snapshotted before any resolution/removal below: a contract that completes
        # tonight must still count toward tonight's resolution, since it was active
        # here when the night started.
        active_here = [c for c in character.security_contracts if c.territory_id == here_id]
        for contract in active_here:
            result = resolve_security_night(character, contract, self.rng)
            if result.blown:
                self.notify(
                    f"Security contract at {here.name} blown — you're compromised.",
                    severity="error",
                )
                character.remove_security_contract(contract.id)
            elif result.completed:
                self.notify(f"Security contract at {here.name} complete — {result.pay + result.bonus}eb paid.")
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
                elsewhere = self.corp_map.territories[contract.territory_id]
                self.notify(f"Not on-site for the {elsewhere.name} contract tonight — no progress.")

    def action_quit_menu(self) -> None:
        self.push_screen(QuitMenu())

    def restart_run(self) -> None:
        self._new_run()
        self._reopen(CharacterCreationScreen())

    def save_run(self) -> SaveSlot:
        state = {
            "rng": self.rng,
            "corp_map": self.corp_map,
            "character": self.character,
            "fixers": self.fixers,
            "location_gigs": self.location_gigs,
            "rival_actions": self.rival_actions,
            "rival_runner_locations": self.rival_runner_locations,
            "corp_state": self.corp_state,
            "corp_only": self.corp_only,
        }
        return save_game(state, self.character.day)

    def load_state(self, state: dict) -> None:
        rng, corp_map = state["rng"], state["corp_map"]
        character, fixers = state["character"], state["fixers"]
        location_gigs = state["location_gigs"]
        self.rng, self.corp_map, self.character, self.fixers = rng, corp_map, character, fixers
        self.location_gigs = location_gigs
        self.rival_actions = state["rival_actions"]
        self.rival_runner_locations = state["rival_runner_locations"]
        self.corp_state = state["corp_state"]
        self.corp_only = state["corp_only"]
        unspent = self.character.stat_points + self.character.skill_points
        if unspent:
            self._reopen(CharacterCreationScreen())
        else:
            self._reopen(CorpMainMenu() if self.corp_only else MainMenu())

    def _reopen(self, screen: Screen) -> None:
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(screen)

    def on_mount(self) -> None:
        self.push_screen(TitleMenu())


def main() -> None:
    ShadowguyApp().run()


if __name__ == "__main__":
    main()
