"""The first slice of "the world keeps moving without the player": rival corp
Factions and independent Runners each get a daily action on day-advance.

A parallel resolution module, like security.py/encounters.py — not a Scene.
Called once per day from ShadowguyApp.advance_day(), the same tick that pays
crew wages and refreshes gigs/offers.

Factions do something real: a faction can push onto neutral ground bordering
its own territory (see claim_territory / _expansion_candidates below) — the
4X-style area-control mechanic CLAUDE.md flags as still missing. Deliberately
scoped to neutral ground only: taking a rival faction's own territory is a
bigger mechanic (contest resolution, standing/rep fallout) left for later.

RivalRunners are still an inert stub: a RivalAction records only that they
acted, never what they did. Nothing here gives them dice or a decision yet;
this is the mechanism, waiting on a driver, the way security.py predated
anything handing out security contracts.

Leaf-ish: imports character/corpmap/factions/runners, never scene or app.
"""

import random
from dataclasses import dataclass
from typing import Literal

from shadowguy.character import Character
from shadowguy.corpmap import CorpMap, claim_territory
from shadowguy.factions import FACTIONS
from shadowguy.runners import RIVAL_RUNNERS

# Per faction, per day; only rolled when the faction has an eligible neutral
# neighbor at all. First-slice number, not balance-simulated.
EXPANSION_CHANCE = 0.2


@dataclass
class RivalAction:
    """One actor's turn on a given day. territory_id is set only when a faction
    claimed neutral ground that day; always None for a runner (still a stub) or
    a faction that didn't expand. No other effect field yet — a consumer that
    wants one (a "world news" surface) doesn't exist; add it when one does."""

    kind: Literal["faction", "runner"]
    actor_id: str
    day: int
    territory_id: str | None = None


def _expansion_candidates(corp_map: CorpMap, faction_id: str) -> list[str]:
    """Neutral territories bordering `faction_id`'s own ground, excluding gang turf
    and the player's start (corp_map.player_start_id) — the same reservation
    _grow_blocs honors at generation time (a faction never seeds or expands onto
    start_cell), kept alive at runtime so the player's home turf is never swallowed."""
    owned = [t for t in corp_map.territories.values() if t.owner == faction_id]
    return sorted(
        {
            conn_id
            for territory in owned
            for conn_id in territory.connections
            if (neighbor := corp_map.territories[conn_id]).owner == "neutral"
            and neighbor.gang_id is None
            and neighbor.id != corp_map.player_start_id
        }
    )


def resolve_rival_day(
    character: Character, corp_map: CorpMap, day: int, rng: random.Random
) -> list[RivalAction]:
    """Every Faction gets a shot at expanding into bordering neutral ground. A
    RivalRunner acts only while independent — excluded the moment they're on the
    player's crew, indefinite or for-job alike, since either engagement means
    they're working for the player that day, not freelancing on their own."""
    actions = []
    for faction in FACTIONS:
        target_id = None
        candidates = _expansion_candidates(corp_map, faction.id)
        if candidates and rng.random() < EXPANSION_CHANCE:
            target_id = rng.choice(candidates)
            claim_territory(corp_map.territories[target_id], faction.id, rng)
        actions.append(RivalAction(kind="faction", actor_id=faction.id, day=day, territory_id=target_id))
    actions += [
        RivalAction(kind="runner", actor_id=runner.id, day=day)
        for runner in RIVAL_RUNNERS
        if not character.on_crew(runner.id)
    ]
    return actions
