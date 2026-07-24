"""Tests for rivals.py: the daily-action pipeline for rival Factions
(real territory-expansion behavior) and independent Runners (still a stub).

Expansion is gated on a flat chance (rivals.EXPANSION_CHANCE), so it's pinned
with a random.Random subclass whose random() is fixed — the same ForcedChance
trick tests/test_encounters.py uses for its own flat-chance gate. Fixtures are
small hand-built CorpMaps (mirroring test_encounters.py's lightweight-fixture
style), not a full generate_corp_map — expansion only cares about ownership
and connections, not the rest of a real map's generated content.
"""

import random

from shadowguy.character import Character
from shadowguy.corpmap import CorpMap, Territory
from shadowguy.factions import FACTIONS
from shadowguy.rivals import EXPANSION_CHANCE, resolve_rival_day
from shadowguy.runners import RIVAL_RUNNERS

IRONCLAD, GHOSTWIRE, MERIDIAN, PROMETHEUS = (f.id for f in FACTIONS)


class ForcedChance(random.Random):
    """A Random whose random() always returns `value`; choice()/randint() still
    work normally, so a fixed value forces the expansion roll to hit or miss."""

    def __init__(self, value: float) -> None:
        super().__init__(0)
        self._value = value

    def random(self) -> float:
        return self._value


HIT = ForcedChance(0.0)  # 0.0 < EXPANSION_CHANCE -> always triggers
MISS = ForcedChance(0.99)  # 0.99 >= EXPANSION_CHANCE -> never triggers


def _territory(id, owner="neutral", connections=(), gang_id=None):
    return Territory(id=id, name=id, x=0, y=0, owner=owner, connections=list(connections), gang_id=gang_id)


def _map():
    """start -- iron_home -- neutral_a
                          \\-- neutral_gang (gang turf)
    ghost_home and merid_home are isolated: their owning factions have no
    neutral neighbor at all."""
    return CorpMap(
        territories={
            "start": _territory("start", connections=["iron_home"]),
            "iron_home": _territory(
                "iron_home", owner=IRONCLAD, connections=["start", "neutral_a", "neutral_gang"]
            ),
            "neutral_a": _territory("neutral_a", connections=["iron_home"]),
            "neutral_gang": _territory("neutral_gang", connections=["iron_home"], gang_id="gang_x"),
            "ghost_home": _territory("ghost_home", owner=GHOSTWIRE),
            "merid_home": _territory("merid_home", owner=MERIDIAN),
        },
        player_start_id="start",
    )


def _faction_action(actions, faction_id):
    return next(a for a in actions if a.kind == "faction" and a.actor_id == faction_id)


def test_eligible_faction_claims_its_only_candidate_on_a_hit():
    corp_map = _map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=5, rng=HIT)
    action = _faction_action(actions, IRONCLAD)
    assert action.territory_id == "neutral_a"
    assert action.day == 5
    assert corp_map.territories["neutral_a"].owner == IRONCLAD


def test_eligible_faction_claims_nothing_on_a_miss():
    corp_map = _map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=1, rng=MISS)
    assert _faction_action(actions, IRONCLAD).territory_id is None
    assert corp_map.territories["neutral_a"].owner == "neutral"


def test_gang_turf_and_player_start_are_never_candidates():
    corp_map = _map()
    resolve_rival_day(Character(name="t"), corp_map, day=1, rng=HIT)
    assert corp_map.territories["neutral_gang"].owner == "neutral"
    assert corp_map.territories["start"].owner == "neutral"


def test_boxed_in_faction_gets_no_expansion_and_no_crash():
    corp_map = _map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=1, rng=HIT)
    assert _faction_action(actions, GHOSTWIRE).territory_id is None
    assert _faction_action(actions, MERIDIAN).territory_id is None


def test_every_independent_runner_acts_with_empty_crew():
    corp_map = _map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=1, rng=MISS)
    runner_actions = [a for a in actions if a.kind == "runner"]
    assert {a.actor_id for a in runner_actions} == {r.id for r in RIVAL_RUNNERS}
    assert all(a.territory_id in corp_map.territories for a in runner_actions)


def test_indefinite_hire_excludes_that_runner():
    character = Character(name="t")
    runner_id = RIVAL_RUNNERS[0].id
    character.hire_indefinite(runner_id)
    actions = resolve_rival_day(character, _map(), day=1, rng=MISS)
    runner_ids = {a.actor_id for a in actions if a.kind == "runner"}
    assert runner_id not in runner_ids
    assert runner_ids == {r.id for r in RIVAL_RUNNERS[1:]}


def test_for_job_hire_also_excludes_that_runner():
    character = Character(name="t")
    runner_id = RIVAL_RUNNERS[0].id
    character.hire_for_job(runner_id, "job_123")
    actions = resolve_rival_day(character, _map(), day=1, rng=MISS)
    runner_ids = {a.actor_id for a in actions if a.kind == "runner"}
    assert runner_id not in runner_ids


def test_total_action_count():
    actions = resolve_rival_day(Character(name="t"), _map(), day=1, rng=MISS)
    assert len(actions) == len(FACTIONS) + len(RIVAL_RUNNERS)


def test_player_faction_is_skipped_entirely():
    """Once the player has taken over a Faction (corp_turn.py), the AI loop must
    neither roll for it nor record a RivalAction — that faction's move is now
    the player's own decision, made from CorpScreen instead."""
    corp_map = _map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=1, rng=HIT, player_faction_id=IRONCLAD)
    assert not any(a.kind == "faction" and a.actor_id == IRONCLAD for a in actions)
    assert corp_map.territories["neutral_a"].owner == "neutral"
    # The other factions are unaffected by the skip.
    assert _faction_action(actions, GHOSTWIRE).territory_id is None


def test_chance_boundary_is_strict_less_than():
    """random() == EXPANSION_CHANCE must miss (>=), matching the >= convention
    test_encounters.py establishes for its own flat-chance gate."""
    corp_map = _map()
    boundary = ForcedChance(EXPANSION_CHANCE)
    actions = resolve_rival_day(Character(name="t"), corp_map, day=1, rng=boundary)
    assert _faction_action(actions, IRONCLAD).territory_id is None


def test_runner_locations_persist_and_wander_across_days():
    """A runner is placed somewhere on the first call, stays there on a miss,
    and hops to a connection on a hit -- and rival_runner_locations (the
    caller-owned persistence dict) reflects exactly that across two days.

    A dedicated two-node, no-faction map sidesteps needing to predict exactly
    which node rng.choice's untouched Mersenne Twister state lands the runner
    on first: with both territories connected only to each other, "hop to a
    connection" is unambiguous regardless of which one that turns out to be."""
    corp_map = CorpMap(
        territories={"a": _territory("a", connections=["b"]), "b": _territory("b", connections=["a"])},
        player_start_id="a",
    )
    runner_id = RIVAL_RUNNERS[0].id
    locations: dict[str, str] = {}

    resolve_rival_day(Character(name="t"), corp_map, day=1, rng=MISS, rival_runner_locations=locations)
    first_location = locations[runner_id]
    assert first_location in corp_map.territories

    resolve_rival_day(Character(name="t"), corp_map, day=2, rng=MISS, rival_runner_locations=locations)
    assert locations[runner_id] == first_location  # a miss never moves them

    resolve_rival_day(Character(name="t"), corp_map, day=3, rng=HIT, rival_runner_locations=locations)
    assert locations[runner_id] in corp_map.territories[first_location].connections


def test_omitted_rival_runner_locations_defaults_to_a_fresh_dict():
    """Every pre-existing call site (and most tests) doesn't pass
    rival_runner_locations at all -- that must keep working exactly as before,
    just without any persistence across calls."""
    actions = resolve_rival_day(Character(name="t"), _map(), day=1, rng=MISS)
    runner_action = next(a for a in actions if a.kind == "runner" and a.actor_id == RIVAL_RUNNERS[0].id)
    assert runner_action.territory_id in _map().territories
