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
