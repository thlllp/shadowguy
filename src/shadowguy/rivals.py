"""The first slice of "the world keeps moving without the player": rival corp
Factions and independent Runners each get a daily action on day-advance.

A parallel resolution module, like security.py/encounters.py — not a Scene.
Called once per day from ShadowguyApp's day tick (app._apply_day_tick, fired by
app.spend_time whenever elapsed time crosses midnight), the same tick that pays
crew wages and refreshes gigs/offers.

Factions do something real: a faction can push onto neutral ground bordering
its own territory (see claim_territory / corpmap.expansion_candidates) — the
4X-style area-control mechanic CLAUDE.md flags as still missing. Deliberately
scoped to neutral ground only: taking a rival faction's own territory is a
bigger mechanic (contest resolution, standing/rep fallout) left for later.

Once the player takes over a Faction (corp_turn.py), that faction is excluded
from this AI loop via player_faction_id — its daily move becomes the player's
own decision instead.

RivalRunners still have no decision logic of their own — a RivalAction records
only that they acted, not a choice they made. What they DO now have is a real
position: each independent (not-hired) runner wanders the map, one random step
along its current territory's connections per day, tracked in a caller-owned
`rival_runner_locations` dict (persisted on ShadowguyApp, not here — rivals.py
stays leaf-ish). That's what finally gives RivalAction.territory_id real
content for a "runner" action, and what lets surveillance.py's Surveillance
checks have somewhere real to catch them.

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

# Per independent runner, per day: the odds they relocate at all rather than
# stay put. A runner who moves takes exactly one hop along their current
# territory's connections — a slow wander, not a teleport. First-slice number,
# not balance-simulated.
RUNNER_MOVE_CHANCE = 0.3


@dataclass
class RivalAction:
    """One actor's turn on a given day. For a faction, territory_id is set only
    when it claimed neutral ground that day. For a runner, territory_id is
    always set — their territory after today's wander, whether or not they
    actually moved. No other effect field yet — a consumer that wants one (a
    "world news" surface) doesn't exist; add it when one does."""

    kind: Literal["faction", "runner"]
    actor_id: str
    day: int
    territory_id: str | None = None


def _wander(runner_id: str, corp_map: CorpMap, rng: random.Random, locations: dict[str, str]) -> str:
    """Where `runner_id` ends up today: placed on a random territory the first
    time they're seen, otherwise a coin-flip (RUNNER_MOVE_CHANCE) to hop to one
    of their current territory's connections, else stay. Mutates `locations`
    in place (the caller owns its persistence across days) and returns the
    resulting territory id."""
    current = locations.get(runner_id)
    if current is None:
        current = rng.choice(list(corp_map.territories))
    elif rng.random() < RUNNER_MOVE_CHANCE:
        connections = corp_map.territories[current].connections
        if connections:
            current = rng.choice(connections)
    locations[runner_id] = current
    return current


def resolve_rival_day(
    character: Character,
    corp_map: CorpMap,
    day: int,
    rng: random.Random,
    player_faction_id: str | None = None,
    rival_runner_locations: dict[str, str] | None = None,
) -> list[RivalAction]:
    """Every Faction gets a shot at expanding into bordering neutral ground. A
    RivalRunner acts only while independent — excluded the moment they're on the
    player's crew, indefinite or for-job alike, since either engagement means
    they're working for the player that day, not freelancing on their own — and
    wanders one step per day via _wander.

    player_faction_id skips that faction entirely (no RivalAction recorded) once
    the player has taken it over via corp_turn.py — its move is the player's own
    decision, reported from the Corp screen instead of rolled here.

    rival_runner_locations is the caller's persistent runner_id -> territory_id
    map (ShadowguyApp.rival_runner_locations in production), mutated in place so
    a runner's position carries over day to day. Defaults to a fresh dict when
    omitted, which is fine for callers (mostly tests) that don't care where a
    runner ends up, only that they acted."""
    if rival_runner_locations is None:
        rival_runner_locations = {}
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
        RivalAction(
            kind="runner",
            actor_id=runner.id,
            day=day,
            territory_id=_wander(runner.id, corp_map, rng, rival_runner_locations),
        )
        for runner in RIVAL_RUNNERS
        if not character.on_crew(runner.id)
    ]
    return actions
