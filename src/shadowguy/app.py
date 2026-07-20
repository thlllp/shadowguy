import random

from textual.app import App
from textual.screen import Screen

from shadowguy.character import Character
from shadowguy.corp_turn import CorpState, collect_income, collect_research
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
        refresh_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        self.location_gigs: dict[str, Scene] = {}
        refresh_gigs(self.corp_map, self.location_gigs, self.character.day, self.rng)
        self.rival_actions: list[RivalAction] = []
        self.corp_state: CorpState | None = None
        self.corp_only = False

    def advance_day(self) -> None:
        self.character.rest()
        for name in self.character.pay_crew_wages():
            self.notify(f"{name} walked off the crew — you missed payroll.", severity="warning")
        expire_offers(self.fixers, self.character.day)
        refresh_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        refresh_gigs(self.corp_map, self.location_gigs, self.character.day, self.rng)
        player_faction_id = self.corp_state.faction_id if self.corp_state else None
        self.rival_actions = resolve_rival_day(
            self.character, self.corp_map, self.character.day, self.rng, player_faction_id
        )
        if self.corp_state:
            self.corp_state.cash += collect_income(self.corp_state, self.corp_map)
            self.corp_state.research_points += collect_research(self.corp_state, self.corp_map)
            self.corp_state.daily_action_used = False

    def end_day(self) -> None:
        """Charge lodging/resolve the night's security contracts and turn the day
        over — shared by MainMenu's "End the day" row and CorpScreen's, since
        it's the same character/clock regardless of which screen ends it."""
        character = self.character
        here_id = character.location_id
        here = self.corp_map.territories[here_id]
        # Computed before any resolution/removal below: a contract that completes
        # tonight must still count toward tonight's free lodging, since it was
        # active here when the night started.
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

        cost = 0 if active_here else lodging_cost(here)
        if cost:
            paid = min(cost, character.cash)
            character.cash -= paid
            self.notify(f"Paid {paid}eb for lodging in {here.name}.")
        self.advance_day()

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
