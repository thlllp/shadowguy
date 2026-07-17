"""Gang turf encounters: what happens when a runner who's fallen out with a street
Gang walks onto that gang's turf.

A parallel resolution subsystem, like security.py — not a Scene. Territory entry
(CorpMapScreen.action_travel) rolls one of these when the runner steps onto the turf of
a gang (corpmap.Territory.gang_id) they hold negative Character.gang_standing with.
Nothing drives that standing into the red yet; this is the mechanism, waiting on a driver
(a failed gang gig, a hit on their turf) the way security contracts predated theirs.

Leaf-ish: imports character/combat/corpmap/gangs/scene, never app or a screen.
"""

import random
from dataclasses import dataclass

from shadowguy.character import Character
from shadowguy.combat import roll_enemies
from shadowguy.corpmap import Territory
from shadowguy.gangs import GANGS_BY_ID, Gang
from shadowguy.scene import Encounter, Outcome

# Flat and deliberately not oppressive: a quarter of entries onto turf you're crosswise
# with actually get stopped. The depth of the grudge sets the *stakes* (toll size, then a
# fight), not the odds of being noticed.
GANG_ENCOUNTER_CHANCE = 0.25

# Toll bands: standing -1..-4 buys your way past for an escalating fee; -5 or worse, there
# is no toll — they just come at you. The fee climbs TOLL_STEP per point of grudge.
TOLL_BASE = 40
TOLL_STEP = 30
ATTACK_STANDING = -5

# Street muscle, not a corp response team — the same tier legwork's ambush fields.
ENCOUNTER_ENEMY_TIER = 0


def toll_for(standing: int) -> int:
    """The fee a gang shakes you down for at `standing` (a toll band, -1..-4)."""
    return TOLL_BASE + TOLL_STEP * (abs(standing) - 1)


@dataclass
class GangEncounter:
    """What entering a gang's turf turned up. `toll` None means they attack outright;
    otherwise it's the fee to pass, which the runner can still refuse into the same fight."""

    gang: Gang
    standing: int
    toll: int | None


def roll_gang_encounter(
    character: Character, territory: Territory, rng: random.Random
) -> GangEncounter | None:
    """The encounter (if any) when `character` enters `territory`: None when the turf holds
    no gang, the runner isn't negative with it, or the flat roll simply misses."""
    gang_id = territory.gang_id
    if gang_id is None:
        return None
    standing = character.gang_standing_with(gang_id)
    if standing >= 0 or rng.random() >= GANG_ENCOUNTER_CHANCE:
        return None
    toll = None if standing <= ATTACK_STANDING else toll_for(standing)
    return GangEncounter(gang=GANGS_BY_ID[gang_id], standing=standing, toll=toll)


def gang_attack(gang: Gang, rng: random.Random) -> Encounter:
    """The fight when a gang jumps you on their turf — street-tier muscle, flavor-only
    victory/escape (no reward: this is a mugging you survived, not a job)."""
    return Encounter(
        prompt=f"{gang.name} corner you the moment you set foot on their block.",
        enemies=roll_enemies(ENCOUNTER_ENEMY_TIER, rng),
        victory=Outcome(text=f"You leave {gang.name}'s people in the gutter and move on."),
        escape=Outcome(text=f"You break clear of {gang.name} and keep moving."),
    )
