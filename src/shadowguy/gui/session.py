"""The graphical front-end's boundary to the game.

`GameSession` wraps the existing `ShadowguyApp` state and offers the GUI the
actions it needs without the GUI reaching into screens. The Textual app is still
the engine for now: it owns the run state and the clock/day tick, which means
both clients share one implementation. A future extraction can move this state
out of `app.py` behind the same interface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from shadowguy.character import Character
from shadowguy.combat import Drop, drop_for_result
from shadowguy.corpmap import Location, travel_path
from shadowguy.encounters import (
    corp_security_encounter,
    gang_attack,
    roll_corp_encounter,
    roll_gang_encounter,
)
from shadowguy.fixer import Fixer, discover_fixers_here
from shadowguy.scene import Scene
from shadowguy.travel import travel_hours

if TYPE_CHECKING:
    from shadowguy.app import ShadowguyApp
    from shadowguy.encounters import CorpEncounter, GangEncounter


class HopEncounter:
    """What a travel hop ran into. `kind` is "gang" or "corp"."""

    def __init__(self, kind: str, encounter: GangEncounter | CorpEncounter) -> None:
        self.kind = kind
        self.encounter = encounter


class GameSession:
    def __init__(self, *, seed: int | None = None) -> None:
        from shadowguy.app import ShadowguyApp

        self.app: ShadowguyApp = ShadowguyApp(seed=seed)
        discover_fixers_here(self.app.fixers, self.character)
        self.fight_encounter = None
        self.pending_encounter: HopEncounter | None = None
        self.entry_territory_id: str | None = None

    # ── state ───────────────────────────────────────────────────────────────

    @property
    def character(self) -> Character:
        return self.app.character

    @property
    def corp_map(self):
        return self.app.corp_map

    @property
    def rng(self):
        return self.app.rng

    @property
    def seed(self) -> int | None:
        return self.app.seed

    def travel_path(self, target_id: str) -> list[str]:
        return travel_path(self.corp_map, self.character.location_id, target_id)

    def territory(self, territory_id: str):
        return self.corp_map.territories[territory_id]

    # ── clock ───────────────────────────────────────────────────────────────

    def rest(self) -> None:
        self.app.rest()

    def travel_hop(self, next_id: str) -> HopEncounter | None:
        """Move one hop, spending the time and rolling the same encounters the TUI
        does. Returns the encounter (if any); the caller stops the route on it."""
        character = self.character
        self.entry_territory_id = character.location_id
        self.app.spend_time(travel_hours(character))
        character.location_id = next_id
        discover_fixers_here(self.app.fixers, character)
        return self._roll_encounter()

    def _roll_encounter(self) -> HopEncounter | None:
        character = self.character
        here = self.corp_map.territories[character.location_id]
        gang = roll_gang_encounter(character, here, self.rng)
        if gang is not None:
            self.pending_encounter = HopEncounter("gang", gang)
            return self.pending_encounter
        corp = roll_corp_encounter(character, here, self.rng)
        if corp is not None:
            self.pending_encounter = HopEncounter("corp", corp)
            return self.pending_encounter
        return None

    # ── encounters ──────────────────────────────────────────────────────────

    def pay_gang_toll(self, gang_encounter: GangEncounter) -> None:
        if gang_encounter.toll is not None:
            self.character.cash = max(0, self.character.cash - gang_encounter.toll)
            self.character.adjust_gang_standing(gang_encounter.gang.id, 1)

    def pay_corp_fine(self, corp_encounter: CorpEncounter) -> None:
        if corp_encounter.fine is not None:
            self.character.cash = max(0, self.character.cash - corp_encounter.fine)
        self.character.adjust_standing(corp_encounter.faction.id, -1)
        if self.entry_territory_id is not None:
            self.character.location_id = self.entry_territory_id

    def gang_fight(self, gang_encounter: GangEncounter):
        self.fight_encounter = gang_attack(gang_encounter.gang, self.rng)
        return self.fight_encounter

    def corp_fight(self, corp_encounter: CorpEncounter):
        self.fight_encounter = corp_security_encounter(
            corp_encounter.faction, corp_encounter.territory_name, self.rng
        )
        return self.fight_encounter

    # ── fixers and work ─────────────────────────────────────────────────────

    def fixers_here(self) -> list[Fixer]:
        location_id = self.character.location_id
        return [
            fixer
            for fixer in self.app.fixers
            if fixer.location_id == location_id and fixer.id in self.character.discovered_fixers
        ]

    def jobs_here(self) -> list:
        return [
            job
            for job in self.character.accepted_jobs
            if job.scene.target_territory_id == self.character.location_id
            and job.timing.is_available(self.character.day)
        ]

    def accept_offer(self, fixer: Fixer, offer) -> None:
        self.character.accept_job(offer)
        fixer.offers = [candidate for candidate in fixer.offers if candidate.id != offer.id]

    def start_job(self, job) -> Scene:
        self.app.spend_time(job.scene.hours_cost, protect_job_id=job.scene.id)
        return job.scene

    def end_scene(self, scene: Scene) -> None:
        if scene.kind.value == "job":
            self.character.remove_job(scene.id)
        else:
            self.app.location_gigs.pop(scene.target_location_id, None)

    # ── shops and gear ──────────────────────────────────────────────────────

    def local_shops(self) -> list[Location]:
        from shadowguy.corpmap import SHOP_KINDS

        return [
            location
            for location in self.corp_map.territories[self.character.location_id].locations
            if location.kind in SHOP_KINDS
        ]

    def drop_for_result(self, result):
        return drop_for_result(result)

    def fight_drop(self, default: Drop = Drop.ENEMY):
        return default
