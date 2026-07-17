import random

from textual.app import App
from textual.screen import Screen

from shadowguy.character import Character
from shadowguy.corpmap import generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.fixer import create_fixers, expire_offers, refresh_offers, refresh_security_offers
from shadowguy.gigs import refresh_gigs
from shadowguy.saves import SaveSlot, save_game
from shadowguy.scene import Scene
from shadowguy.screens.creation_screen import CharacterCreationScreen
from shadowguy.screens.main_menu import MainMenu
from shadowguy.screens.menu_screens import QuitMenu, TitleMenu


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

    def advance_day(self) -> None:
        self.character.rest()
        for name in self.character.pay_crew_wages():
            self.notify(f"{name} walked off the crew — you missed payroll.", severity="warning")
        expire_offers(self.fixers, self.character.day)
        refresh_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, self.character.day, self.corp_map, self.rng)
        refresh_gigs(self.corp_map, self.location_gigs, self.character.day, self.rng)

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
        }
        return save_game(state, self.character.day)

    def load_state(self, state: dict) -> None:
        rng, corp_map = state["rng"], state["corp_map"]
        character, fixers = state["character"], state["fixers"]
        location_gigs = state["location_gigs"]
        self.rng, self.corp_map, self.character, self.fixers = rng, corp_map, character, fixers
        self.location_gigs = location_gigs
        unspent = self.character.stat_points + self.character.skill_points
        self._reopen(CharacterCreationScreen() if unspent else MainMenu())

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
