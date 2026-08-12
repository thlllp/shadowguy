"""Territory encounters: what happens when a runner walks into hostile ground — gang turf
or a corp's territory where they've burned their standing.

A parallel resolution subsystem, like security.py — not a Scene. Territory entry
(CorpMapScreen.action_travel) rolls these when the runner enters a district controlled by
a hostile faction.

None of this drives standing negative on its own; that belongs to job/gig completion.
This is the mechanism on top of the standing score — once a job hits a corp's reputation,
entering their territory carries risk.

Leaf-ish: imports character/combat/corpmap/factions/gangs/scene, never app or a screen.
"""

import random
from dataclasses import dataclass

from shadowguy.character import Character
from shadowguy.combat import roll_enemies
from shadowguy.corpmap import Territory, TerritoryModifier
from shadowguy.factions import FACTIONS_BY_ID, Faction
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
# Standing gained by paying the toll — a small step back toward neutral,
# so cooperating isn't purely punitive.
TOLL_STANDING_GAIN = 1
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


# ── corp territory encounters ────────────────────────────────────────────────

# Detection chance: base 8% + 6% per SURVEILLANCE point on the territory, so even a
# lightly monitored district (SURVEILLANCE 1) has a 14% chance of spotting a hostile
# runner. Full ghostwire territory (5) caps at 38% — not a certainty.
CORP_SPOTTED_BASE = 0.08
CORP_SPOTTED_PER_SURVEILLANCE = 0.06

# Mild hostility: the corp wants you gone, not dead. They demand a fine to walk away.
# Deeper grudges escalate the price. At or below ARREST_STANDING they don't negotiate.
CORP_EXPEL_FINE = 80
CORP_EXPEL_STEP = 50
CORP_ARREST_STANDING = -5

# Corp security is professional, not street muscle — one tier up from gangs.
CORP_SECURITY_TIER = 1

# What an arrest KO costs: time, money, face.
ARREST_HOURS = 6.0
ARREST_CASH_PCT = 0.33
ARREST_STANDING_HIT = -3


def corp_fine_for(standing: int) -> int:
    """The bribe a corp demands to look the other way at `standing` (a toll band,
    -1..-4). After that they don't negotiate — straight to arrest."""
    return CORP_EXPEL_FINE + CORP_EXPEL_STEP * (abs(standing) - 1)


@dataclass
class CorpEncounter:
    """What entering a corp's territory turned up. `fine` None means arrest (no toll
    option — security attacks on sight); otherwise it's the bribe to walk away."""

    faction: Faction
    standing: int
    territory_name: str
    fine: int | None


def roll_corp_encounter(
    character: Character, territory: Territory, rng: random.Random,
) -> CorpEncounter | None:
    """The encounter (if any) when `character` is in `territory`: None when the
    territory is unowned, the runner isn't negative with its owner, or the
    detection roll simply misses."""
    owner = territory.owner
    if owner == "neutral":
        return None
    standing = character.standing_with(owner)
    if standing >= 0:
        return None
    surveillance = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
    chance = CORP_SPOTTED_BASE + CORP_SPOTTED_PER_SURVEILLANCE * surveillance
    if rng.random() >= chance:
        return None
    if standing <= CORP_ARREST_STANDING:
        return CorpEncounter(
            faction=FACTIONS_BY_ID[owner],
            standing=standing,
            territory_name=territory.name,
            fine=None,
        )
    return CorpEncounter(
        faction=FACTIONS_BY_ID[owner],
        standing=standing,
        territory_name=territory.name,
        fine=corp_fine_for(standing),
    )


def corp_security_encounter(faction: Faction, territory_name: str, rng: random.Random) -> Encounter:
    """The fight when corp security moves on a hostile runner — professional-tier
    muscle, tier-1 enemies. No reward for surviving: this is an arrest you escaped."""
    return Encounter(
        prompt=f"{faction.name} security spots you in {territory_name} and moves to detain you.",
        enemies=roll_enemies(CORP_SECURITY_TIER, rng),
        victory=Outcome(
            text=f"You slip through {faction.name}'s security cordon and disappear."
        ),
        escape=Outcome(
            text=f"You break away from {faction.name}'s security and melt into the crowd."
        ),
    )
