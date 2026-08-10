"""
Corp-side Surveillance detection: a corp watches its own territory's camera
and informant network (corpmap.TerritoryModifier.SURVEILLANCE) for known
runners passing through — the player, and the independent street runners
(runners.RIVAL_RUNNERS) now taking a turn of their own each day and moving
around the map as a result (rivals.py). A parallel resolution module, like
rivals.py/security.py — not a Scene.

Only resolves for the corp the player is actually running (CorpState — a
None corp_state means "not playing Corp mode" and this is a no-op): nothing
reads a sighting logged against an AI-controlled faction, the same "no
consequence without a reader" restraint jobs/gigs/rivals already apply
elsewhere in Corp mode. Fired once per day tick (app._apply_day_tick), after
rivals.resolve_rival_day has already moved the independent runner roster for
the day — so a runner's territory_id is settled by the time this rolls
against it.

Detection is a level-indexed chance off the territory's own Surveillance
score, with optional bonuses from researched corp technologies (Total
Information Awareness adds a flat detection bonus at every level; Deep
Surveillance extends the cap to level 6). A detection is purely informational
by default, but Counter-Intelligence enriches sightings with faction info and
Operation Intercept has a chance to disrupt the detected runner's current
activity.

Leaf-ish: imports character/corp_turn/corpmap/runners, never scene or app.
rivals.RunnerState is a TYPE_CHECKING-only import — only `.territory_id` is
read off it, so there's no runtime dependency to invert later.
"""

import random
from typing import TYPE_CHECKING

from shadowguy.character import Character
from shadowguy.corp_turn import (
    COUNTER_INTELLIGENCE_ID,
    DEEP_PROTOCOL_DETECTION_BONUS,
    DEEP_SURVEILLANCE_PROTOCOL_ID,
    DETECTION_CHANCE_BONUS,
    EXTENDED_SURVEILLANCE_DETECTION,
    EXTENDED_SURVEILLANCE_MAX,
    GHOSTWIRE_DETECTION_BONUS,
    ICE_CRACKED_NETWORKS_ID,
    INTERCEPTION_CHANCE,
    OPERATION_INTERCEPT_ID,
    TOTAL_INFORMATION_AWARENESS_ID,
    CorpState,
    Sighting,
    has_technology,
    sightings_log_cap,
)
from shadowguy.corpmap import MODIFIER_MAX, CorpMap, Territory, TerritoryModifier
from shadowguy.runners import RIVAL_RUNNERS, RivalRunner

if TYPE_CHECKING:
    from shadowguy.rivals import RunnerState

# Indexed by TerritoryModifier.SURVEILLANCE (0..MODIFIER_MAX). First-slice
# numbers, not balance-simulated: even a fully-watched district (level 5)
# still misses more often than not, so Surveillance is real pressure, not an
# automatic reveal. Level 6 (Deep Surveillance) is handled separately in
# _detection_chance so this tuple doesn't need to grow.
SURVEILLANCE_DETECTION_CHANCE = (0.0, 0.1, 0.2, 0.35, 0.5, 0.65)
if len(SURVEILLANCE_DETECTION_CHANCE) != MODIFIER_MAX + 1:
    raise ValueError(
        "SURVEILLANCE_DETECTION_CHANCE must cover every Surveillance level 0..MODIFIER_MAX"
    )


def _detection_chance(
    territory: Territory, corp_state: CorpState,
) -> float:
    """Detection chance for this territory and corp, factoring in researched
    technologies: the base chance from the territory's Surveillance level, plus
    Total Information Awareness's flat bonus at every level. Level 6 (Deep
    Surveillance) uses EXTENDED_SURVEILLANCE_DETECTION directly."""
    level = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
    if level >= EXTENDED_SURVEILLANCE_MAX:
        chance = EXTENDED_SURVEILLANCE_DETECTION
    else:
        chance = SURVEILLANCE_DETECTION_CHANCE[level]
    if has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        chance += DETECTION_CHANCE_BONUS
    if has_technology(corp_state, DEEP_SURVEILLANCE_PROTOCOL_ID):
        chance += DEEP_PROTOCOL_DETECTION_BONUS
    elif has_technology(corp_state, ICE_CRACKED_NETWORKS_ID):
        chance += GHOSTWIRE_DETECTION_BONUS
    return min(chance, 1.0)


def _detected(territory: Territory, corp_state: CorpState, rng: random.Random) -> bool:
    return rng.random() < _detection_chance(territory, corp_state)


def resolve_surveillance_day(
    character: Character,
    corp_map: CorpMap,
    corp_state: CorpState | None,
    rival_runner_states: dict[str, "RunnerState"],
    day: int,
    rng: random.Random,
    runners: list[RivalRunner] | None = None,
) -> list[Sighting]:
    """One day's Surveillance sweep of the corp's own territory: a detection
    roll against the player (if character.location_id is inside it) and
    against every independent RivalRunner whose current position
    (rival_runner_states, already updated for today by
    rivals.resolve_rival_day) is inside it too.

    Corporately researched technologies modify the sweep:
    - Total Information Awareness adds a flat detection bonus at every level.
    - Counter-Intelligence enriches sightings with the runner's faction name
      and extends the log capacity.
    - Deep Surveillance supports an extended Surveillance level (6) with its
      own detection chance.
    - Operation Intercept has INTERCEPTION_CHANCE to disrupt a detected
      runner's current activity (the sighting carries an intercepted flag).

    runners is the run's actual independent-runner roster (ShadowguyApp.runners
    in production — see rivals.resolve_rival_day's own `runners` param).
    Defaults to the bare RIVAL_RUNNERS three, fine for callers that don't care
    about that run's random extras.

    Returns every sighting logged today, in no particular order; the same list
    is also prepended to corp_state.sightings (most-recent-first, capped at
    the technology-adjusted max). Returns an empty list and mutates nothing
    when corp_state is None — Surveillance detection is a Corp-mode mechanic,
    and a runner-only run has no corp watching anything."""
    if corp_state is None:
        return []
    if runners is None:
        runners = RIVAL_RUNNERS
    owned_ids = {t.id for t in corp_map.territories.values() if t.owner == corp_state.faction_id}

    sightings: list[Sighting] = []
    enriched = has_technology(corp_state, COUNTER_INTELLIGENCE_ID)
    can_intercept = has_technology(corp_state, OPERATION_INTERCEPT_ID)

    if character.location_id in owned_ids:
        territory = corp_map.territories[character.location_id]
        if _detected(territory, corp_state, rng):
            sightings.append(Sighting(
                kind="player", actor_id="player",
                territory_id=territory.id, day=day,
            ))
    for runner in runners:
        state = rival_runner_states.get(runner.id)
        if state is not None and state.territory_id in owned_ids:
            territory = corp_map.territories[state.territory_id]
            if _detected(territory, corp_state, rng):
                intercepted = False
                if can_intercept and rng.random() < INTERCEPTION_CHANCE:
                    intercepted = True
                faction_id = "independent" if enriched else None
                sightings.append(Sighting(
                    kind="runner", actor_id=runner.id,
                    territory_id=territory.id, day=day,
                    runner_faction_id=faction_id,
                    intercepted=intercepted,
                ))

    if sightings:
        cap = sightings_log_cap(corp_state)
        corp_state.sightings = (sightings + corp_state.sightings)[:cap]
    return sightings
