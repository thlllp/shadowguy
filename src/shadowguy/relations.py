"""Standing between every corp Faction and street Gang, independent of the player.

A separate axis from factions.standing_shift (player-vs-faction) and
Character.gang_standing (player-vs-gang): this is how the Factions and Gangs feel
about *each other*. Symmetric, one value per unordered pair (Ironclad<->Ghostwire
is a single number, not two) — the same shape factions.standing_shift and
gang_standing already use, no per-side asymmetry modeled anywhere else in the
codebase. Data only for now: nothing reads or moves these values yet, the same
"mechanism built ahead of its driver" pattern gang_standing and
CorpState.research_points started as. Leaf module, like factions.py/gangs.py:
imports only those two, nothing from the rest of the package.
"""

import random
from itertools import combinations

from shadowguy.factions import FACTIONS
from shadowguy.gangs import GANGS

# Every corp and gang shares one relations graph, keyed by id regardless of kind —
# a corp-vs-corp, corp-vs-gang, and gang-vs-gang pair all look the same.
ENTITY_IDS = [faction.id for faction in FACTIONS] + [gang.id for gang in GANGS]

# Seeded within a small neutral band: some rivalries/alliances already exist day
# one, none of them extreme, mirroring how corpmap jitters territory value/modifiers.
RELATION_MIN = -2
RELATION_MAX = 2

Relations = dict[frozenset[str], int]


def generate_relations(rng: random.Random) -> Relations:
    """One randomly seeded value per unordered (faction/gang) pair."""
    return {
        frozenset((a, b)): rng.randint(RELATION_MIN, RELATION_MAX)
        for a, b in combinations(ENTITY_IDS, 2)
    }


def relation(relations: Relations, a_id: str, b_id: str) -> int:
    """How `a_id` and `b_id` (each a faction or gang id) feel about each other."""
    return relations[frozenset((a_id, b_id))]
