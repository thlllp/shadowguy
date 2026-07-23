"""Corp-side Surveillance detection: a corp watches its own territory's camera
and informant network (corpmap.TerritoryModifier.SURVEILLANCE) for known
runners passing through — the player, and the independent street runners
(runners.RIVAL_RUNNERS) now wandering the map on their own each day
(rivals.py). A parallel resolution module, like rivals.py/security.py — not a
Scene.

Only resolves for the corp the player is actually running (CorpState — a
None corp_state means "not playing Corp mode" and this is a no-op): nothing
reads a sighting logged against an AI-controlled faction, the same "no
consequence without a reader" restraint jobs/gigs/rivals already apply
elsewhere in Corp mode. Fired once per day tick (app._apply_day_tick), after
rivals.resolve_rival_day has already moved the independent runner roster for
the day — so a runner's territory_id is settled by the time this rolls
against it.

Detection is a flat, level-indexed chance off the territory's own Surveillance
score, not an opposed check: there's no player-side skill counterplay yet
(a Concealment/Stealth-style counter is the obvious next hook), so this only
reads the corp's own investment — the same "world acts on you, you don't get
to roll back" shape encounters.py's toll/attack split uses before a fight even
opens. A detection is purely informational for now: it's logged to
CorpState.sightings, nothing else moves (no standing/rep/combat) — the same
"mechanism built ahead of its driver" pattern gang_standing and
CorpMap.relations started as.

Leaf-ish: imports character/corp_turn/corpmap/runners, never scene or app.
"""

import random

from shadowguy.character import Character
from shadowguy.corp_turn import CorpState, Sighting
from shadowguy.corpmap import MODIFIER_MAX, CorpMap, Territory, TerritoryModifier
from shadowguy.runners import RIVAL_RUNNERS

# Indexed by TerritoryModifier.SURVEILLANCE (0..MODIFIER_MAX). First-slice
# numbers, not balance-simulated: even a fully-watched district (level 5)
# still misses more often than not, so Surveillance is real pressure, not an
# automatic reveal.
SURVEILLANCE_DETECTION_CHANCE = (0.0, 0.1, 0.2, 0.35, 0.5, 0.65)
if len(SURVEILLANCE_DETECTION_CHANCE) != MODIFIER_MAX + 1:
    raise ValueError("SURVEILLANCE_DETECTION_CHANCE must cover every Surveillance level 0..MODIFIER_MAX")

# CorpState.sightings is capped at this many entries (most-recent-first) —
# an unbounded log would grow for the life of a run for no read anything
# further back than a handful of days actually wants.
MAX_SIGHTINGS_LOG = 10


def _detected(territory: Territory, rng: random.Random) -> bool:
    level = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
    return rng.random() < SURVEILLANCE_DETECTION_CHANCE[level]


def resolve_surveillance_day(
    character: Character,
    corp_map: CorpMap,
    corp_state: CorpState | None,
    rival_runner_locations: dict[str, str],
    day: int,
    rng: random.Random,
) -> list[Sighting]:
    """One day's Surveillance sweep of the corp's own territory: a detection
    roll against the player (if character.location_id is inside it) and
    against every independent RivalRunner whose current wander position
    (rival_runner_locations, already updated for today by
    rivals.resolve_rival_day) is inside it too.

    Returns every sighting logged today, in no particular order; the same list
    is also prepended to corp_state.sightings (most-recent-first, capped at
    MAX_SIGHTINGS_LOG). Returns an empty list and mutates nothing when
    corp_state is None — Surveillance detection is a Corp-mode mechanic, and a
    runner-only run has no corp watching anything."""
    if corp_state is None:
        return []
    owned_ids = {t.id for t in corp_map.territories.values() if t.owner == corp_state.faction_id}

    sightings = []
    if character.location_id in owned_ids:
        territory = corp_map.territories[character.location_id]
        if _detected(territory, rng):
            sightings.append(Sighting(kind="player", actor_id="player", territory_id=territory.id, day=day))
    for runner in RIVAL_RUNNERS:
        location_id = rival_runner_locations.get(runner.id)
        if location_id in owned_ids:
            territory = corp_map.territories[location_id]
            if _detected(territory, rng):
                sightings.append(Sighting(kind="runner", actor_id=runner.id, territory_id=territory.id, day=day))

    if sightings:
        corp_state.sightings = (sightings + corp_state.sightings)[:MAX_SIGHTINGS_LOG]
    return sightings
