"""Street gangs: criminal orgs that hold no territory outright, unlike the corp
Factions in factions.py. A gang keeps a presence in a handful of unclaimed ground
instead of owning a bloc — see corpmap.py for how that turf gets placed on the map.

Leaf module, like factions.py and skills.py: imports nothing from the package.
"""

from dataclasses import dataclass


@dataclass
class Gang:
    id: str
    name: str
    description: str


GANGS = [
    Gang(
        id="gang_splice_row",
        name="Splice Row",
        description="Body-mod chop crews running black-market wetware out of back-alley clinics.",
    ),
    Gang(
        id="gang_undertow",
        name="The Undertow",
        description="Smugglers and dockside muscle moving whatever the corps won't touch.",
    ),
    Gang(
        id="gang_redtooth",
        name="Redtooth",
        description="Street-level enforcers running protection rackets block by block.",
    ),
    Gang(
        id="gang_wire_saints",
        name="Wire Saints",
        description="Bootleg netrunners and fences dealing in stolen data and hot gear.",
    ),
]

GANGS_BY_ID = {gang.id: gang for gang in GANGS}

# The two ranks manning a gang's den (see corpmap._make_gang_den), low to high. No
# rep/standing gate yet, unlike the corp equivalent (factions.CORP_OFFICER_TIERS) —
# just who's there.
GANG_RANKS = ("soldier", "lieutenant")
