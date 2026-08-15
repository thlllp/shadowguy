"""What can stop a runner between two districts: a gang wanting a toll, or corp
security wanting them gone. Both are toll-or-fight, both fire off a travel hop, and
both used to be written out twice inside CorpMapScreen — two near-identical modal
screens and two near-identical five-method chains.

Split out as a mixin rather than folded into CorpMapScreen for the same reason
corp_screen.CorpActionsMixin is one: the flow is genuinely stateful across pushed
screens (roll -> toll modal -> maybe CombatScreen -> back), so it reads as its own
thing rather than as more methods on the map.

What the host must provide: `_do_refresh_map()` (an expulsion or an arrest moves the
runner, and the map has to redraw), and `_prev_territory_id` set to the district
walked in *from* whenever an encounter fires — which is what corp security escorts
you back to. The host's travel loop is also the only caller of the two
`_maybe_*_encounter` rolls; everything below them is callbacks.
"""

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import ListItem, ListView, Static

from shadowguy.abstract_combat import CombatOutcome
from shadowguy.combat import (
    KNOCKOUT_FATAL_MAX,
    Drop,
    knockout_roll,
    mug_unconscious,
)
from shadowguy.corpmap import unowned_territory_ids
from shadowguy.encounters import (
    ARREST_CASH_PCT,
    ARREST_HOURS,
    ARREST_STANDING_HIT,
    CorpEncounter,
    TOLL_STANDING_GAIN,
    corp_security_encounter,
    gang_attack,
    roll_corp_encounter,
    roll_gang_encounter,
)

from . import _menu_css, end_run_dead, end_run_never_woke
from .combat_screen import CombatScreen


class TollScreen(ModalScreen):
    """Pay or refuse. Dismisses True only if the player chose to pay *and* can cover
    it — an unaffordable "pay" is a refusal, which is what routes it to the fight.

    Deliberately does not touch the Character: the caller applies the cost, because
    what paying costs beyond the cash differs (a gang toll buys standing back, a corp
    fine costs it). The gang and corp copies this replaces disagreed about that —
    one deducted here, the other in its callback — which is exactly the kind of split
    that makes "who took the money?" a question.
    """

    BINDINGS = [("escape", "refuse", "Refuse")]
    CSS = _menu_css("TollScreen", "toll_dialog")

    def __init__(self, prompt: str, cost: int) -> None:
        super().__init__()
        self.prompt = prompt
        self.cost = cost

    def compose(self) -> ComposeResult:
        can_pay = self.app.character.cash >= self.cost
        pay_label = f"Pay {self.cost}eb" if can_pay else f"Pay {self.cost}eb — can't cover it"
        yield Vertical(
            Static(self.prompt),
            ListView(
                ListItem(Static(pay_label), id="pay"),
                ListItem(Static("Refuse — they'll come at you"), id="refuse"),
            ),
            id="toll_dialog",
        )

    def action_refuse(self) -> None:
        self.dismiss(False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        self.dismiss(event.item.id == "pay" and self.app.character.cash >= self.cost)


class EncounterMixin:
    """The gang and corp halves of a travel encounter. Both `_maybe_*` rolls return
    whether one fired, so the host's travel loop knows to stop at this hop rather
    than carry on to the next."""

    # ── gang encounters ─────────────────────────────────────────────────────

    def _maybe_gang_encounter(self) -> bool:
        character = self.app.character
        territory = self.app.corp_map.territories[character.location_id]
        encounter = roll_gang_encounter(character, territory, self.app.rng)
        if encounter is None:
            return False
        self._pending_gang = encounter.gang
        self._pending_toll = encounter.toll
        if encounter.toll is None:
            self._start_gang_fight(encounter.gang)
        else:
            self.app.push_screen(
                TollScreen(
                    f"{encounter.gang.name} block your way — {encounter.toll}eb to pass.",
                    encounter.toll,
                ),
                self._on_toll,
            )
        return True

    def _on_toll(self, paid: bool) -> None:
        if not paid:
            self._start_gang_fight(self._pending_gang)
            return
        character = self.app.character
        character.cash -= self._pending_toll
        character.adjust_gang_standing(self._pending_gang.id, TOLL_STANDING_GAIN)
        new_standing = character.gang_standing_with(self._pending_gang.id)
        self.notify(
            f"You pay off {self._pending_gang.name} and move on. "
            f"Standing with them rises to {new_standing}."
        )

    def _start_gang_fight(self, gang) -> None:
        self._gang_encounter = gang_attack(gang, self.app.rng)
        self.app.push_screen(
            CombatScreen(self._gang_encounter, Drop.ENEMY), self._on_gang_combat_end
        )

    def _on_gang_combat_end(self, result: CombatOutcome) -> None:
        character = self.app.character
        if result is CombatOutcome.DEAD:
            end_run_dead(self.app, character)
            return
        if result is CombatOutcome.KNOCKED_OUT:
            if knockout_roll(self.app.rng) <= KNOCKOUT_FATAL_MAX:
                end_run_never_woke(self.app, character)
                return
            mug_unconscious(character)
            self.notify("You came to in an alley, lighter a few creds.")
            return
        outcome = (
            self._gang_encounter.victory
            if result is CombatOutcome.VICTORY
            else self._gang_encounter.escape
        )
        self.notify(outcome.text)

    # ── corp territory encounters ─────────────────────────────────────────────

    def _maybe_corp_encounter(self) -> bool:
        character = self.app.character
        territory = self.app.corp_map.territories[character.location_id]
        encounter = roll_corp_encounter(character, territory, self.app.rng)
        if encounter is None:
            return False
        self._pending_corp = encounter
        if encounter.fine is None:
            self._start_corp_fight(encounter)
        else:
            self.app.push_screen(
                TollScreen(
                    f"{encounter.faction.name} security stops you in "
                    f"{encounter.territory_name} — pay {encounter.fine}eb and leave, "
                    f"or they'll take you in.",
                    encounter.fine,
                ),
                self._on_corp_toll,
            )
        return True

    def _on_corp_toll(self, paid: bool) -> None:
        enc = self._pending_corp
        if not paid:
            self._start_corp_fight(enc)
            return
        character = self.app.character
        character.cash -= enc.fine
        character.adjust_standing(enc.faction.id, -1)
        expel_id = getattr(self, "_prev_territory_id", None)
        if expel_id is not None:
            character.location_id = expel_id
            self._do_refresh_map()
        self.notify(f"{enc.faction.name} security escorts you out of {enc.territory_name}.")

    def _start_corp_fight(self, encounter: CorpEncounter) -> None:
        self._corp_encounter = corp_security_encounter(
            encounter.faction, encounter.territory_name, self.app.rng
        )
        self.app.push_screen(
            CombatScreen(self._corp_encounter, Drop.ENEMY), self._on_corp_combat_end
        )

    def _on_corp_combat_end(self, result: CombatOutcome) -> None:
        character = self.app.character
        if result is CombatOutcome.DEAD:
            end_run_dead(self.app, character)
            return
        encounter = self._pending_corp
        if result is CombatOutcome.KNOCKED_OUT:
            # Same wake-up roll as a gang beating; what happens next isn't --
            # corp security takes a smaller cut (ARREST_CASH_PCT) but takes you in,
            # so this doesn't call combat.mug_unconscious.
            if knockout_roll(self.app.rng) <= KNOCKOUT_FATAL_MAX:
                end_run_never_woke(self.app, character)
                return
            lost = int(character.cash * ARREST_CASH_PCT)
            character.cash -= lost
            character.adjust_standing(encounter.faction.id, ARREST_STANDING_HIT)
            self.app.spend_time(ARREST_HOURS)
            unowned = unowned_territory_ids(self.app.corp_map)
            character.location_id = (
                unowned[0] if unowned else self.app.corp_map.player_start_id
            )
            self._do_refresh_map()
            self.notify(
                f"{encounter.faction.name} holds you for hours, then dumps you on the street. "
                f"Lighter by {lost}eb."
            )
            return
        outcome = (
            self._corp_encounter.victory
            if result is CombatOutcome.VICTORY
            else self._corp_encounter.escape
        )
        self.notify(outcome.text)
