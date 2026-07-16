"""Rival corp factions for Corp mode (not yet implemented)."""

from dataclasses import dataclass
from enum import StrEnum


class FactionSpecialty(StrEnum):
    WEAPONS = "weapons"
    HACKING = "hacking"
    PHARMA = "pharma"


@dataclass
class Faction:
    id: str
    name: str
    specialty: FactionSpecialty
    description: str


FACTIONS = [
    Faction(
        id="faction_ironclad",
        name="Ironclad Dynamics",
        specialty=FactionSpecialty.WEAPONS,
        description="Arms manufacturer running the city's black-market hardware and muscle.",
    ),
    Faction(
        id="faction_ghostwire",
        name="Ghostwire Collective",
        specialty=FactionSpecialty.HACKING,
        description="Netrunner syndicate that trades in stolen data, backdoors, and ICE-breakers.",
    ),
    Faction(
        id="faction_meridian",
        name="Meridian Biochem",
        specialty=FactionSpecialty.PHARMA,
        description="Pharmaceutical conglomerate pushing combat stims and black-clinic wetware.",
    ),
]

FACTIONS_BY_ID = {faction.id: faction for faction in FACTIONS}

# The corporate officers you can reach inside a corp's HQ (corpmap.LocationKind.CORP_HQ,
# one per faction). A rank ladder: each higher officer needs BOTH more street rep AND
# more standing with that corp to get past — the exec suite is for a runner the corp both
# knows (rep) and trusts (standing). Rows are (role, min_rep, min_standing), applied in
# order, so a location's officers line up with these by index (see corpmap._make_officers,
# app.CorpHQScreen). The ground-floor reception has min_standing None — the lobby is
# public, open even to a runner the corp is hostile to (negative standing), who gets the
# cold shoulder rather than the door (see officer_dialogue). Talking is flavor only for now.
CORP_OFFICER_TIERS = (
    ("receptionist", 0, None),
    ("operations manager", 5, 3),
    ("executive", 12, 8),
)


def officer_dialogue(faction: Faction, role: str, standing: int) -> str:
    """A line from an HQ officer, themed on the corp and how it feels about you (standing).

    Flavor only — talking costs nothing and changes nothing yet; this is the hook the
    corp-side game will hang concrete interactions on.
    """
    if standing < 0:
        return (
            f'The {role} does not stand. "{faction.name} knows exactly who has been '
            f'working against us. You have nerve, walking in here. Say it and go."'
        )
    if standing == 0:
        return (
            f'The {role} looks you over. "{faction.name} does not do business with '
            f'strangers. Make a name for yourself and come back."'
        )
    return (
        f'The {role} waves you to a seat. "Always good to see a friend of '
        f'{faction.name}. We look after the people who look after us."'
    )


# Hitting a corp is a favour to its rivals: they move the opposite way, at half weight.
RIVAL_WEIGHT = 2


def standing_shift(target_faction_id: str, delta: int) -> dict[str, int]:
    """Standing change for every faction when `delta` is applied to the one you hit."""
    rival_delta = -delta // RIVAL_WEIGHT
    return {
        faction.id: delta if faction.id == target_faction_id else rival_delta
        for faction in FACTIONS
    }
