"""The first slice of "the world keeps moving without the player": rival corp
Factions and independent Runners each get a daily action on day-advance.

A parallel resolution module, like security.py/encounters.py — not a Scene.
Called once per day from ShadowguyApp.advance_day(), the same tick that pays
crew wages and refreshes gigs/offers.

Factions do something real: a faction can push onto neutral ground bordering
its own territory (see claim_territory / corpmap.expansion_candidates) — the
4X-style area-control mechanic CLAUDE.md flags as still missing. Deliberately
scoped to neutral ground only: taking a rival faction's own territory is a
bigger mechanic (contest resolution, standing/rep fallout) left for later.

Once the player takes over a Faction (corp_turn.py), that faction is excluded
from this AI loop via player_faction_id — its daily move becomes the player's
own decision instead.

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
from shadowguy.corpmap import CorpMap, claim_territory, expansion_candidates
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


def resolve_rival_day(
    character: Character,
    corp_map: CorpMap,
    day: int,
    rng: random.Random,
    player_faction_id: str | None = None,
) -> list[RivalAction]:
    """Every Faction gets a shot at expanding into bordering neutral ground. A
    RivalRunner acts only while independent — excluded the moment they're on the
    player's crew, indefinite or for-job alike, since either engagement means
    they're working for the player that day, not freelancing on their own.

    player_faction_id skips that faction entirely (no RivalAction recorded) once
    the player has taken it over via corp_turn.py — its move is the player's own
    decision, reported from the Corp screen instead of rolled here."""
    actions = []
    for faction in FACTIONS:
        if faction.id == player_faction_id:
            continue
        target_id = None
        candidates = expansion_candidates(corp_map, faction.id)
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
