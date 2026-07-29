import os
import random
import sys
import traceback
from datetime import datetime, timezone

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Static
from rich.text import Text

from shadowguy.character import HOURS_PER_DAY, REST_HOURS_COST, Character
from shadowguy.corp_turn import (
    CorpState,
    FactionEvent,
    advance_training,
    collect_income,
    collect_research,
    employee_plural,
)
from shadowguy.corpmap import generate_corp_map, lodging_cost
from shadowguy.factions import FACTIONS
from shadowguy.fixer import create_fixers, expire_offers, refresh_offers, refresh_security_offers
from shadowguy.gangs import GANGS_BY_ID
from shadowguy.gigs import refresh_gigs
from shadowguy.jobs import GANG_JOB_STANDING_GAIN
from shadowguy.rivals import RivalAction, RunnerActivity, RunnerState, resolve_rival_day
from shadowguy.runners import RUNNERS_BY_ID, select_active_runners
from shadowguy.saves import SaveSlot, save_game
from shadowguy.scene import Scene
from shadowguy.screens.corp_map_screen import CorpMapScreen
from shadowguy.screens.creation_screen import CharacterCreationScreen
from shadowguy.screens.menu_screens import QuitMenu, TitleMenu
from shadowguy.security import resolve_security_night
from shadowguy.surveillance import resolve_surveillance_day


class CrashScreen(Screen):
    CSS = """
    CrashScreen {
        align: center middle;
    }
    #crash-header {
        color: red;
        text-style: bold;
        margin-bottom: 1;
    }
    #crash-traceback {
        width: 90%;
        height: 70%;
        border: solid red;
        padding: 1;
    }
    #crash-exit {
        margin-top: 1;
        dock: bottom;
    }
    """

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error

    def compose(self) -> ComposeResult:
        tb = "".join(traceback.format_exception(self._error))
        yield Static(Text.from_markup("[bold red]FATAL ERROR[/]\n\nThe game encountered an unexpected error:"), id="crash-header")
        yield VerticalScroll(Static(Text(tb)), id="crash-traceback")
        yield Button("Exit", id="crash-exit")

    def on_button_pressed(self) -> None:
        self.app.exit()


def _write_crash_log(error: BaseException) -> str:
    path = os.path.join("/tmp", "shadowguy_crash.log")
    with open(path, "w") as f:
        f.write(f"Crash at {datetime.now(timezone.utc).isoformat()}\n")
        f.write("".join(traceback.format_exception(error)))
    return path


class ShadowguyApp(App):
    BINDINGS = [("q", "quit_menu", "Menu")]

    def _handle_exception(self, error: BaseException) -> None:
        _write_crash_log(error)
        try:
            while self.screen_stack:
                self.pop_screen()
        except Exception:
            pass
        try:
            self.push_screen(CrashScreen(error))
        except Exception:
            self.exit()

    def __init__(self) -> None:
        super().__init__()
        self._new_run()

    def _new_run(self) -> None:
        self.rng = random.Random()
        self.corp_map = generate_corp_map(FACTIONS, self.rng)
        self.character = Character(name="Runner", location_id=self.corp_map.player_start_id)
        self.fixers = create_fixers(self.corp_map, self.rng)
        self.runners = select_active_runners(self.rng)
        day = self.character.day
        refresh_offers(self.fixers, day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, day, self.corp_map, self.rng)
        self.location_gigs: dict[str, Scene] = {}
        refresh_gigs(self.corp_map, self.location_gigs, day, self.rng)
        self.rival_actions: list[RivalAction] = []
        self.rival_runner_states: dict[str, RunnerState] = {}
        self.rival_researched: dict[str, set[str]] = {}
        self.faction_events: dict[str, list[FactionEvent]] = {}
        self.corp_state: CorpState | None = None
        self.corp_only = False
        # CorpMapScreen reads this back to restore the last-viewed category across a
        # full screen rebuild (load_state/restart_run) -- None (the map itself) is the
        # correct default for a fresh run, same as CorpMapScreen.selected_category.
        self.main_menu_category: str | None = None

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
        an active security contract here, same as an owned home (corpmap.lodging_cost) —
        and free outright for a corp-only game (self.corp_only): there's no runner
        body paying for a flophouse, and CorpMapScreen's travel is a free reposition
        for exactly that reason, so charging lodging by wherever '@' happens to sit
        would just be a cost with no matching mechanic behind it."""
        if self.corp_only:
            return 0
        character = self.character
        here = self.corp_map.territories[character.location_id]
        active_here = any(c.territory_id == character.location_id for c in character.security_contracts)
        return 0 if active_here else lodging_cost(here)

    def _rest_hours(self) -> int:
        """How long the next Rest will actually run: REST_HOURS_COST, unless the
        Phone's Alarm Clock tab has set Character.alarm_hour, in which case it's
        only until that hour — wrapping to the next day, and never 0 (an alarm set
        to the current hour wakes a full day later, not instantly)."""
        character = self.character
        if character.alarm_hour is None:
            return REST_HOURS_COST
        return (character.alarm_hour - character.hour_of_day) % HOURS_PER_DAY or HOURS_PER_DAY

    def rest_label(self) -> str:
        """The "Rest" menu item's text for MainMenu/CorpScreen, previewing rest_cost()
        and, with an alarm set, the shorter actual duration."""
        hours = self._rest_hours()
        cost = self.rest_cost()
        if not cost:
            return f"Rest ({hours}h)"
        return f"Rest ({hours}h, {cost}eb lodging)"

    def rest(self) -> None:
        """Shared "Rest" action wiring for MainMenu and CorpScreen. Spends
        REST_HOURS_COST hours, wherever the runner currently is — same lodging pricing
        as the old midnight charge (corpmap.lodging_cost), just paid at the moment of
        resting instead of at whatever territory happened to hold the runner at
        midnight.

        If the Phone's Alarm Clock tab has set Character.alarm_hour, _rest_hours()
        overrides the fixed duration: sleep only until that hour instead of a flat 8.
        The alarm is cleared here so it doesn't keep firing on every later Rest.

        Resting only halves accumulated fatigue rather than clearing it (see
        Character.fatigue) — a bad stretch of skipped rest still costs a few more
        nights to fully shake off. It does heal a flat point of health, though,
        wherever the runner rests and whatever else happened that day."""
        character = self.character
        cost = self.rest_cost()
        if cost:
            paid = min(cost, character.cash)
            character.cash -= paid
            here = self.corp_map.territories[character.location_id]
            self.notify(f"Paid {paid}eb for lodging in {here.name}.")
        hours = self._rest_hours()
        character.alarm_hour = None
        self.spend_time(hours)
        character.mark_rested()
        character.adjust_health(1)

    def _apply_day_tick(self, day: int, skip_night_effects: bool, protect_job_id: str | None = None) -> None:
        """Everything that used to fire from a deliberate "End the day" click —
        now fired once per day boundary crossed, by whatever action crossed it."""
        self.character.on_new_day(day, protect_job_id)
        expired = self.character.expire_smuggling_job(day, GANG_JOB_STANDING_GAIN)
        if expired is not None:
            self.notify(
                f"{GANGS_BY_ID[expired.gang_id].name} isn't happy — the delivery never showed.",
                severity="warning",
            )
        for name in self.character.pay_crew_wages():
            self.notify(f"{name} walked off the crew — you missed payroll.", severity="warning")
        expire_offers(self.fixers, day)
        refresh_offers(self.fixers, day, self.corp_map, self.rng)
        refresh_security_offers(self.fixers, day, self.corp_map, self.rng)
        refresh_gigs(self.corp_map, self.location_gigs, day, self.rng)
        player_faction_id = self.corp_state.faction_id if self.corp_state else None
        today_actions = resolve_rival_day(
            self.character,
            self.corp_map,
            day,
            self.rng,
            player_faction_id,
            self.rival_runner_states,
            self.fixers,
            self.rival_researched,
            self.faction_events,
            self.runners,
        )
        self.rival_actions += today_actions
        # Runners take jobs off the fixers' boards overnight (the offer stays
        # listed, marked taken, for the rest of today). Only news you'd plausibly
        # hear gets a toast: a job lifted off a fixer you've actually met. Losing
        # one from a fixer you've never found is still real, just discovered on
        # the board rather than announced. One notify for the whole roster, like
        # the surveillance sweep below.
        taken = [
            f"{RUNNERS_BY_ID[action.actor_id].name} took the {action.job_title} job"
            for action in today_actions
            if action.activity is RunnerActivity.WORKING
            and action.fixer_id in self.character.discovered_fixers
        ]
        if taken:
            self.notify(f"Word on the street: {', '.join(taken)}.")
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
                self.character, self.corp_map, self.corp_state, self.rival_runner_states, day, self.rng, self.runners
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

    def home_menu(self) -> Screen:
        """Backwards-compatible stub: the sidebar is now part of CorpMapScreen itself,
        so pressing 'm' on the map is no longer a thing -- callers that still pass
        through here get the home screen directly instead."""
        return CorpMapScreen()

    def restart_run(self) -> None:
        self._new_run()
        self._reopen(CharacterCreationScreen())

    def save_run(self) -> SaveSlot:
        state = {
            "rng": self.rng,
            "corp_map": self.corp_map,
            "character": self.character,
            "fixers": self.fixers,
            "runners": self.runners,
            "location_gigs": self.location_gigs,
            "rival_actions": self.rival_actions,
            "rival_runner_states": self.rival_runner_states,
            "rival_researched": self.rival_researched,
            "faction_events": self.faction_events,
            "corp_state": self.corp_state,
            "corp_only": self.corp_only,
        }
        return save_game(state, self.character.day)

    def load_state(self, state: dict) -> None:
        rng, corp_map = state["rng"], state["corp_map"]
        character, fixers = state["character"], state["fixers"]
        location_gigs = state["location_gigs"]
        self.rng, self.corp_map, self.character, self.fixers = rng, corp_map, character, fixers
        self.runners = state["runners"]
        self.location_gigs = location_gigs
        self.rival_actions = state["rival_actions"]
        self.rival_runner_states = state["rival_runner_states"]
        self.rival_researched = state["rival_researched"]
        self.faction_events = state["faction_events"]
        self.corp_state = state["corp_state"]
        self.corp_only = state["corp_only"]
        unspent = self.character.stat_points + self.character.skill_points
        if unspent:
            self._reopen(CharacterCreationScreen())
        else:
            self._reopen(CorpMapScreen())

    def _reopen(self, screen: Screen) -> None:
        while len(self.screen_stack) > 1:
            self.pop_screen()
        self.push_screen(screen)

    def on_mount(self) -> None:
        self.push_screen(TitleMenu())


def _show_crash_console(error: BaseException) -> None:
    path = _write_crash_log(error)
    print("".join(traceback.format_exception(error)), flush=True)
    print(f"\nCrash log saved to: {path}", flush=True)
    input("\nThe game crashed. Press Enter to exit...")


def main() -> None:
    def _excepthook(etype, value, tb):
        _show_crash_console(value)

    sys.excepthook = _excepthook
    try:
        ShadowguyApp().run()
    except BaseException as exc:
        if isinstance(exc, KeyboardInterrupt):
            raise
        _show_crash_console(exc)


if __name__ == "__main__":
    main()
