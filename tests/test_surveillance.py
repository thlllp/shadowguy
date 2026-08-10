"""Tests for surveillance.py: nightly Surveillance detection of known runners
(the player and independent RivalRunners) caught inside the corp's own
territory.

Detection is a flat, Surveillance-level-indexed chance, so it's pinned the
same way tests/test_encounters.py pins its own flat-chance gate: a
random.Random subclass whose random() is fixed. choice()/randint() aren't
used by resolve_surveillance_day at all, so unlike test_rivals.py's ForcedChance
there's no real-RNG state to worry about here.
"""

from shadowguy.character import Character
from shadowguy.corp_turn import (
    COUNTER_INTELLIGENCE_ID,
    MAX_SIGHTINGS_LOG,
    OPERATION_INTERCEPT_ID,
    TOTAL_INFORMATION_AWARENESS_ID,
    CorpState,
)
from shadowguy.corpmap import CorpMap, Territory, TerritoryModifier
from shadowguy.factions import FACTIONS
from shadowguy.rivals import RunnerState
from shadowguy.runners import RIVAL_RUNNERS
from shadowguy.surveillance import resolve_surveillance_day

from helpers import ForcedChance

IRONCLAD, GHOSTWIRE, MERIDIAN, _, _ = (f.id for f in FACTIONS)

HIT = ForcedChance(0.0)
MISS = ForcedChance(0.99)


def _territory(id, owner="neutral", surveillance=0):
    return Territory(
        id=id,
        name=id,
        x=0,
        y=0,
        owner=owner,
        modifiers={TerritoryModifier.SURVEILLANCE: surveillance},
    )


def _map():
    return CorpMap(
        territories={
            "watched": _territory("watched", owner=IRONCLAD, surveillance=3),
            "unwatched_corp": _territory("unwatched_corp", owner=IRONCLAD, surveillance=0),
            "rival_turf": _territory("rival_turf", owner=GHOSTWIRE, surveillance=5),
            "open_ground": _territory("open_ground"),
        },
        player_start_id="open_ground",
    )


def test_no_corp_state_means_no_detection_and_no_mutation():
    corp_map = _map()
    character = Character(name="t", location_id="watched")
    sightings = resolve_surveillance_day(character, corp_map, None, {}, day=1, rng=HIT)
    assert sightings == []


def test_player_detected_on_a_hit_inside_owned_territory():
    corp_map = _map()
    character = Character(name="t", location_id="watched")
    corp_state = CorpState(faction_id=IRONCLAD)
    sightings = resolve_surveillance_day(character, corp_map, corp_state, {}, day=7, rng=HIT)
    assert len(sightings) == 1
    sighting = sightings[0]
    assert sighting.kind == "player"
    assert sighting.territory_id == "watched"
    assert sighting.day == 7
    assert corp_state.sightings == [sighting]


def test_player_not_detected_on_a_miss():
    corp_map = _map()
    character = Character(name="t", location_id="watched")
    corp_state = CorpState(faction_id=IRONCLAD)
    sightings = resolve_surveillance_day(character, corp_map, corp_state, {}, day=1, rng=MISS)
    assert sightings == []
    assert corp_state.sightings == []


def test_player_outside_owned_territory_is_never_checked():
    """Standing on neutral ground, or another faction's turf, never logs a
    sighting against the corp the player runs -- even on a guaranteed hit."""
    corp_map = _map()
    corp_state = CorpState(faction_id=IRONCLAD)
    for location_id in ("open_ground", "rival_turf"):
        character = Character(name="t", location_id=location_id)
        assert resolve_surveillance_day(character, corp_map, corp_state, {}, day=1, rng=HIT) == []


def test_zero_surveillance_never_detects_even_on_a_forced_hit():
    corp_map = _map()
    character = Character(name="t", location_id="unwatched_corp")
    corp_state = CorpState(faction_id=IRONCLAD)
    assert resolve_surveillance_day(character, corp_map, corp_state, {}, day=1, rng=HIT) == []


def test_rival_runner_detected_when_wandered_into_owned_territory():
    corp_map = _map()
    character = Character(name="t", location_id="open_ground")
    corp_state = CorpState(faction_id=IRONCLAD)
    runner_id = RIVAL_RUNNERS[0].id
    sightings = resolve_surveillance_day(
        character, corp_map, corp_state, {runner_id: RunnerState(territory_id="watched")}, day=3, rng=HIT
    )
    assert len(sightings) == 1
    assert sightings[0] == corp_state.sightings[0]
    assert sightings[0].kind == "runner"
    assert sightings[0].actor_id == runner_id
    assert sightings[0].territory_id == "watched"


def test_sightings_log_is_capped_and_most_recent_first():
    corp_map = _map()
    character = Character(name="t", location_id="watched")
    corp_state = CorpState(faction_id=IRONCLAD)
    for day in range(1, MAX_SIGHTINGS_LOG + 5):
        resolve_surveillance_day(character, corp_map, corp_state, {}, day=day, rng=HIT)
    assert len(corp_state.sightings) == MAX_SIGHTINGS_LOG
    assert corp_state.sightings[0].day == MAX_SIGHTINGS_LOG + 4  # most recent first
    assert corp_state.sightings[-1].day == 5  # oldest kept entry


# --- Technology-aware detection tests ---------------------------------------

NOT_MISS = ForcedChance(0.05)  # beats level-0's 0.0 but loses to everything else


def test_counter_intelligence_enriches_runner_sightings():
    corp_map = _map()
    character = Character(name="t", location_id="open_ground")
    corp_state = CorpState(faction_id=IRONCLAD, researched={COUNTER_INTELLIGENCE_ID})
    runner_id = RIVAL_RUNNERS[0].id
    sightings = resolve_surveillance_day(
        character, corp_map, corp_state,
        {runner_id: RunnerState(territory_id="watched")},
        day=3, rng=HIT,
    )
    assert len(sightings) == 1
    assert sightings[0].runner_faction_id == "independent"


def test_counter_intelligence_extends_sightings_log():
    corp_map = _map()
    character = Character(name="t", location_id="watched")
    corp_state = CorpState(faction_id=IRONCLAD, researched={COUNTER_INTELLIGENCE_ID})
    from shadowguy.corp_turn import COUNTERINTEL_SIGHTINGS_LOG
    for day in range(1, COUNTERINTEL_SIGHTINGS_LOG + 5):
        resolve_surveillance_day(character, corp_map, corp_state, {}, day=day, rng=HIT)
    assert len(corp_state.sightings) == COUNTERINTEL_SIGHTINGS_LOG


def test_total_information_awareness_adds_detection_bonus():
    """Level-0 surveillance has 0% detection chance; TIA's 5% bonus makes it
    possible. NOT_MISS = 0.05 beats 0.05 exactly (< is strict)."""
    corp_map = _map()
    character = Character(name="t", location_id="unwatched_corp")
    corp_state = CorpState(faction_id=IRONCLAD, researched={TOTAL_INFORMATION_AWARENESS_ID})
    # Without the bonus, level 0 never detects (tested above).
    without = resolve_surveillance_day(
        character, corp_map,
        CorpState(faction_id=IRONCLAD),
        {}, day=1, rng=HIT,
    )
    assert without == []
    # With TIA, the flat bonus pushes the chance from 0.0 to 0.05.
    # rng=HIT (0.0) beats 0.05, so it detects.
    with_bonus = resolve_surveillance_day(
        character, corp_map, corp_state, {}, day=1, rng=HIT,
    )
    assert len(with_bonus) == 1


def test_operation_intercept_triggers_on_a_hit():
    """INTERCEPTION_CHANCE is 0.25, so a rolled 0.0 hits it."""
    corp_map = _map()
    character = Character(name="t", location_id="open_ground")
    corp_state = CorpState(
        faction_id=IRONCLAD,
        researched={COUNTER_INTELLIGENCE_ID, OPERATION_INTERCEPT_ID},
    )
    runner_id = RIVAL_RUNNERS[0].id
    sightings = resolve_surveillance_day(
        character, corp_map, corp_state,
        {runner_id: RunnerState(territory_id="watched")},
        day=3, rng=HIT,
    )
    assert len(sightings) == 1
    assert sightings[0].intercepted is True
    assert sightings[0].runner_faction_id == "independent"


def test_operation_intercept_misses_on_a_high_roll():
    """INTERCEPTION_CHANCE is 0.25. Roll 0.30 hits detection (surveillance=3,
    detection 0.35) but misses interception (0.30 >= 0.25)."""
    corp_map = _map()
    character = Character(name="t", location_id="open_ground")
    corp_state = CorpState(
        faction_id=IRONCLAD,
        researched={COUNTER_INTELLIGENCE_ID, OPERATION_INTERCEPT_ID},
    )
    runner_id = RIVAL_RUNNERS[0].id
    DETECT_NOT_INTERCEPT = ForcedChance(0.30)
    sightings = resolve_surveillance_day(
        character, corp_map, corp_state,
        {runner_id: RunnerState(territory_id="watched")},
        day=3, rng=DETECT_NOT_INTERCEPT,
    )
    assert len(sightings) == 1
    assert sightings[0].intercepted is False


def test_interception_does_not_fire_without_tech():
    """Counter-Intelligence researched but not Operation Intercept: no intercept."""
    corp_map = _map()
    character = Character(name="t", location_id="open_ground")
    corp_state = CorpState(faction_id=IRONCLAD, researched={COUNTER_INTELLIGENCE_ID})
    runner_id = RIVAL_RUNNERS[0].id
    sightings = resolve_surveillance_day(
        character, corp_map, corp_state,
        {runner_id: RunnerState(territory_id="watched")},
        day=3, rng=HIT,
    )
    assert len(sightings) == 1
    assert sightings[0].intercepted is False
    assert sightings[0].runner_faction_id == "independent"


def test_player_sighting_never_has_faction_nor_interception():
    corp_map = _map()
    character = Character(name="t", location_id="watched")
    corp_state = CorpState(
        faction_id=IRONCLAD,
        researched={COUNTER_INTELLIGENCE_ID, OPERATION_INTERCEPT_ID},
    )
    sightings = resolve_surveillance_day(
        character, corp_map, corp_state, {}, day=7, rng=HIT,
    )
    assert len(sightings) == 1
    assert sightings[0].kind == "player"
    assert sightings[0].runner_faction_id is None
    assert sightings[0].intercepted is False
