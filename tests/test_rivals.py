"""Tests for rivals.py: the daily-action pipeline for rival Factions (territory
expansion) and independent Runners (a per-day RunnerActivity, one branch of
which takes a real job off a fixer's board).

Both halves are gated on flat chances (rivals.EXPANSION_CHANCE,
RUNNER_WORK_CHANCE, JOB_INJURY_CHANCE) plus a weighted idle table, so they're
pinned with a random.Random subclass whose random() is fixed — the same
ForcedChance trick tests/test_encounters.py uses for its own flat-chance gate.
Note random.choices() consumes random() too (it bisects cumulative weights
against random() * total), so a fixed 0.0 always draws the *first* idle
activity and 0.99 the last — which is what makes the idle table testable
without touching its weights.

Fixtures are small hand-built CorpMaps (mirroring test_encounters.py's
lightweight-fixture style), not a full generate_corp_map — none of this cares
about the rest of a real map's generated content.
"""

import random

import pytest

from shadowguy.character import Character
from shadowguy.corp_turn import TECHNOLOGIES, FactionEvent
from shadowguy.corpmap import CorpMap, Location, LocationKind, Territory
from shadowguy.factions import FACTIONS
from shadowguy.fixer import Fixer, JobOffer, expire_offers
from shadowguy.jobs import JobTiming
from shadowguy.rivals import (
    AI_GARRISON_CAP,
    AI_TERRITORIES_PER_ATTACKER,
    EXPANSION_CHANCE,
    MIN_AI_ATTACK_FORCE,
    RECOVERY_DAYS,
    RIVAL_RESEARCH_CHANCE,
    RunnerActivity,
    RunnerState,
    _attack_force,
    _pick_attack_target,
    _reinforce,
    resolve_rival_day,
)
from shadowguy.runners import RIVAL_RUNNERS
from shadowguy.scene import Scene, Stage

from helpers import ForcedChance

IRONCLAD, GHOSTWIRE, MERIDIAN, _ = (f.id for f in FACTIONS)


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


def _runner_action(actions, runner_id=None):
    runner_id = runner_id or RIVAL_RUNNERS[0].id
    return next(a for a in actions if a.kind == "runner" and a.actor_id == runner_id)


def _scene(title="Server Pull", territory_id=None):
    """The smallest Scene that passes Scene.__post_init__ — a single choice-less
    start stage. Job-taking only reads .title and .target_territory_id."""
    return Scene(
        id=f"scene_{title}",
        title=title,
        stages={"start": Stage(id="start", prompt="p", choices=[])},
        target_territory_id=territory_id,
    )


def _fixer_with_offer(title="Server Pull", territory_id=None, timing=None, offer_id="offer_1"):
    offer = JobOffer(
        id=offer_id,
        fixer_id="fixer_rook",
        scene=_scene(title, territory_id),
        timing=timing or JobTiming(),
        offered_day=1,
    )
    return Fixer(id="fixer_rook", name="Rook", specialty="s", offers=[offer]), offer


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


def test_expansion_chance_boundary_is_strict_less_than():
    """random() == EXPANSION_CHANCE must miss (>=), matching the >= convention
    test_encounters.py establishes for its own flat-chance gate."""
    corp_map = _map()
    boundary = ForcedChance(EXPANSION_CHANCE)
    actions = resolve_rival_day(Character(name="t"), corp_map, day=1, rng=boundary)
    assert _faction_action(actions, IRONCLAD).territory_id is None


# Rival research (RIVAL_RESEARCH_CHANCE) and the faction_events blog log.


def test_eligible_faction_gains_one_root_technology_on_a_research_hit():
    corp_map = _map()
    researched: dict[str, set[str]] = {}
    events: dict[str, list[FactionEvent]] = {}
    resolve_rival_day(
        Character(name="t"), corp_map, day=4, rng=HIT, rival_researched=researched, faction_events=events
    )
    gained = researched[IRONCLAD]
    assert len(gained) == 1
    technology = next(t for t in TECHNOLOGIES if t.id in gained)
    assert technology.prereqs == ()  # only a root tech is available on day one

    tech_events = [e for e in events[IRONCLAD] if e.kind == "technology"]
    assert len(tech_events) == 1
    assert tech_events[0].technology_id == technology.id
    assert tech_events[0].day == 4


def test_no_faction_researches_on_a_research_miss():
    corp_map = _map()
    researched: dict[str, set[str]] = {}
    resolve_rival_day(Character(name="t"), corp_map, day=1, rng=MISS, rival_researched=researched)
    assert researched[IRONCLAD] == set()


def test_a_faction_with_every_technology_already_researched_never_gains_more():
    corp_map = _map()
    all_ids = {tech.id for tech in TECHNOLOGIES}
    researched = {IRONCLAD: set(all_ids)}
    events: dict[str, list[FactionEvent]] = {}
    resolve_rival_day(
        Character(name="t"), corp_map, day=1, rng=HIT, rival_researched=researched, faction_events=events
    )
    assert researched[IRONCLAD] == all_ids
    assert not any(e.kind == "technology" for e in events.get(IRONCLAD, []))


def test_research_chance_boundary_is_strict_less_than():
    corp_map = _map()
    boundary = ForcedChance(RIVAL_RESEARCH_CHANCE)
    researched: dict[str, set[str]] = {}
    resolve_rival_day(Character(name="t"), corp_map, day=1, rng=boundary, rival_researched=researched)
    assert researched[IRONCLAD] == set()


def test_territory_claim_and_technology_gain_are_both_logged_to_faction_events():
    corp_map = _map()
    events: dict[str, list[FactionEvent]] = {}
    resolve_rival_day(Character(name="t"), corp_map, day=6, rng=HIT, faction_events=events)
    assert {e.kind for e in events[IRONCLAD]} == {"territory", "technology"}
    assert all(e.day == 6 for e in events[IRONCLAD])


def test_player_faction_is_excluded_from_the_research_roll_too():
    corp_map = _map()
    researched: dict[str, set[str]] = {}
    resolve_rival_day(
        Character(name="t"), corp_map, day=1, rng=HIT, player_faction_id=IRONCLAD, rival_researched=researched
    )
    assert IRONCLAD not in researched


def test_omitted_rival_researched_and_faction_events_default_gracefully():
    """Every pre-existing call site doesn't pass either -- must keep working,
    just without any persistence across calls, same as rival_runner_states."""
    actions = resolve_rival_day(Character(name="t"), _map(), day=1, rng=HIT)
    assert actions  # the day still resolves; it just keeps no record of the research


def _pair_map(locations=()):
    """Two territories connected only to each other, so "hopped to a connection"
    is unambiguous without predicting which one rng.choice seeds them onto."""
    return CorpMap(
        territories={
            "a": Territory(id="a", name="a", x=0, y=0, connections=["b"], locations=list(locations)),
            "b": Territory(id="b", name="b", x=0, y=0, connections=["a"], locations=list(locations)),
        },
        player_start_id="a",
    )


def test_runner_states_persist_and_wander_across_days():
    """A runner is placed somewhere on the first call, stays put when the idle
    roll lands on something sedentary, and hops to a connection when it lands
    on LEGWORK -- with rival_runner_states (the caller-owned persistence dict)
    reflecting exactly that across three days."""
    corp_map = _pair_map()
    runner_id = RIVAL_RUNNERS[0].id
    states: dict[str, RunnerState] = {}

    resolve_rival_day(Character(name="t"), corp_map, day=1, rng=MISS, rival_runner_states=states)
    first_location = states[runner_id].territory_id
    assert first_location in corp_map.territories
    # MISS lands on the idle table's last bucket, DRINKING -- with no bar on
    # this map that degrades to LAYING_LOW, which doesn't move them.
    assert states[runner_id].activity is RunnerActivity.LAYING_LOW

    resolve_rival_day(Character(name="t"), corp_map, day=2, rng=MISS, rival_runner_states=states)
    assert states[runner_id].territory_id == first_location

    resolve_rival_day(Character(name="t"), corp_map, day=3, rng=HIT, rival_runner_states=states)
    assert states[runner_id].activity is RunnerActivity.LEGWORK
    assert states[runner_id].territory_id in corp_map.territories[first_location].connections


def test_omitted_rival_runner_states_defaults_to_a_fresh_dict():
    """Every pre-existing call site (and most tests) doesn\'t pass
    rival_runner_states at all -- that must keep working, just without any
    persistence across calls."""
    actions = resolve_rival_day(Character(name="t"), _map(), day=1, rng=MISS)
    assert _runner_action(actions).territory_id in _map().territories


def test_drinking_only_happens_where_there_is_a_bar():
    """The idle table\'s last bucket is DRINKING; without a bar in the territory
    it degrades to LAYING_LOW (asserted above) rather than describing a drink
    with nowhere to happen -- ContactsScreen names the bar at display time."""
    bar = Location(id="loc_bar", name="The Rusted Halo", kind=LocationKind.BAR)
    actions = resolve_rival_day(
        Character(name="t"), _pair_map([bar]), day=1, rng=MISS, rival_runner_states={}
    )
    assert _runner_action(actions).activity is RunnerActivity.DRINKING


def test_runner_takes_a_job_off_the_board_and_moves_to_the_job_site():
    corp_map = _map()
    fixer, offer = _fixer_with_offer(title="Server Pull", territory_id="neutral_a")
    actions = resolve_rival_day(
        Character(name="t"), corp_map, day=2, rng=HIT, rival_runner_states={}, fixers=[fixer]
    )
    action = _runner_action(actions)
    assert action.activity is RunnerActivity.WORKING
    assert action.job_title == "Server Pull"
    assert action.territory_id == "neutral_a"
    assert offer.taken_by == RIVAL_RUNNERS[0].id
    # app gates its "word on the street" toast on having met this fixer.
    assert action.fixer_id == "fixer_rook"


def test_an_idle_runner_reports_no_job():
    actions = resolve_rival_day(Character(name="t"), _map(), day=1, rng=MISS)
    action = _runner_action(actions)
    assert action.job_title is None
    assert action.fixer_id is None


def test_only_one_runner_can_take_the_same_offer():
    """The board is the shared resource: once marked taken, the remaining
    runners must fall through to the idle table rather than double-book it."""
    fixer, offer = _fixer_with_offer()
    actions = resolve_rival_day(
        Character(name="t"), _map(), day=2, rng=HIT, rival_runner_states={}, fixers=[fixer]
    )
    working = [a for a in actions if a.activity is RunnerActivity.WORKING]
    assert len(working) == 1
    assert offer.taken_by == working[0].actor_id


def test_no_runner_takes_work_on_a_missed_work_roll():
    fixer, offer = _fixer_with_offer()
    actions = resolve_rival_day(
        Character(name="t"), _map(), day=2, rng=MISS, rival_runner_states={}, fixers=[fixer]
    )
    assert not any(a.activity is RunnerActivity.WORKING for a in actions)
    assert offer.taken_by is None


def test_a_job_not_runnable_today_is_not_takeable():
    """Scheduled-for-later and already-expired offers are both off the market --
    a runner can\'t do work whose own timing says nobody can do it today."""
    later, _ = _fixer_with_offer(timing=JobTiming(scheduled_day=9))
    expired, _ = _fixer_with_offer(timing=JobTiming(deadline_day=1), offer_id="offer_2")
    actions = resolve_rival_day(
        Character(name="t"), _map(), day=5, rng=HIT, rival_runner_states={}, fixers=[later, expired]
    )
    assert not any(a.activity is RunnerActivity.WORKING for a in actions)
    assert later.offers[0].taken_by is None
    assert expired.offers[0].taken_by is None


def test_a_bad_job_lays_a_runner_up_the_next_day():
    """RECOVERING is only ever caused by a WORKING day that failed the injury
    roll, and while it lasts it pre-empts the whole activity roll -- the runner
    doesn\'t move and can\'t pick up new work."""
    corp_map = _map()
    fixer, _ = _fixer_with_offer(territory_id="neutral_a")
    states: dict[str, RunnerState] = {}
    resolve_rival_day(
        Character(name="t"), corp_map, day=2, rng=HIT, rival_runner_states=states, fixers=[fixer]
    )
    state = states[RIVAL_RUNNERS[0].id]
    assert state.activity is RunnerActivity.WORKING
    assert RECOVERY_DAYS[0] <= state.recovery_days <= RECOVERY_DAYS[1]

    second, _ = _fixer_with_offer(offer_id="offer_2")
    resolve_rival_day(
        Character(name="t"), corp_map, day=3, rng=HIT, rival_runner_states=states, fixers=[second]
    )
    assert state.activity is RunnerActivity.RECOVERING
    assert state.territory_id == "neutral_a"  # laid up where the job left them
    assert state.job_title is None
    assert second.offers[0].taken_by != RIVAL_RUNNERS[0].id


# The other half of the job-taking coupling lives in fixer.py: a taken offer has
# to stay visible for exactly one day, then go.


def test_expire_offers_drops_taken_offers():
    """The day tick expires before resolve_rival_day runs, so an offer marked
    today survives today and is swept at the next tick -- which is the whole
    reason the player ever sees the "TAKEN" row."""
    fixer, offer = _fixer_with_offer()
    expire_offers([fixer], day=1)
    assert fixer.offers == [offer]

    offer.taken_by = RIVAL_RUNNERS[0].id
    expire_offers([fixer], day=1)
    assert fixer.offers == []


def test_a_job_posted_today_cannot_be_stolen_before_the_player_sees_it():
    """The day tick generates the day's offers (refresh_offers) *before* it
    resolves runner turns, so without the offered_day guard a runner could take
    a job in the same tick that created it -- one the player never had a chance
    to sit on. Measured at 10% of all steals before the guard went in."""
    fixer, offer = _fixer_with_offer()  # offered_day=1
    actions = resolve_rival_day(
        Character(name="t"), _map(), day=1, rng=HIT, rival_runner_states={}, fixers=[fixer]
    )
    assert not any(a.activity is RunnerActivity.WORKING for a in actions)
    assert offer.taken_by is None

    # ...but it is fair game the next day.
    actions = resolve_rival_day(
        Character(name="t"), _map(), day=2, rng=HIT, rival_runner_states={}, fixers=[fixer]
    )
    assert offer.taken_by == RIVAL_RUNNERS[0].id


# --- Conflict ---------------------------------------------------------------


class ForcedAttack(ForcedChance):
    """random() fixed (so every daily chance fires or misses on demand) *and*
    randint() pinned to 6 — both contest dice come off the same rng, so matching
    them collapses a capture to the deterministic `committed > defense`."""

    def randint(self, a, b):
        return 6


ATTACK_HIT = ForcedAttack(0.0)
ATTACK_MISS = ForcedAttack(0.99)


def _contested_map():
    """iron_home -- ghost_home, so IRONCLAD and GHOSTWIRE border each other.
    iron_home also holds iron_second, so neither side is one loss from wiped."""
    return CorpMap(
        territories={
            "start": _territory("start", connections=["iron_home"]),
            "iron_home": _territory(
                "iron_home", owner=IRONCLAD, connections=["start", "iron_second", "ghost_home"]
            ),
            "iron_second": _territory("iron_second", owner=IRONCLAD, connections=["iron_home"]),
            "ghost_home": _territory(
                "ghost_home", owner=GHOSTWIRE, connections=["iron_home", "ghost_second"]
            ),
            "ghost_second": _territory("ghost_second", owner=GHOSTWIRE, connections=["ghost_home"]),
        },
        player_start_id="start",
    )


def test_faction_attacks_a_bordering_rival():
    corp_map = _contested_map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=5, rng=ATTACK_HIT)
    attack = _faction_action(actions, IRONCLAD).attack
    assert attack is not None
    assert attack.territory_id in ("ghost_home",)
    assert attack.defender_id == GHOSTWIRE


def test_faction_does_not_attack_without_a_bordering_rival():
    """_map()'s factions are isolated from each other — only neutral ground borders
    IRONCLAD, so there is nothing to attack however the dice fall."""
    actions = resolve_rival_day(Character(name="t"), _map(), day=5, rng=ATTACK_HIT)
    assert _faction_action(actions, IRONCLAD).attack is None


def test_no_attack_on_a_miss():
    corp_map = _contested_map()
    actions = resolve_rival_day(Character(name="t"), corp_map, day=5, rng=ATTACK_MISS)
    assert _faction_action(actions, IRONCLAD).attack is None
    assert corp_map.territories["ghost_home"].owner == GHOSTWIRE


def test_a_capture_is_logged_as_a_seizure_event():
    corp_map = _contested_map()
    events = {}
    resolve_rival_day(
        Character(name="t"), corp_map, day=5, rng=ATTACK_HIT, faction_events=events
    )
    seizures = [e for e in events.get(IRONCLAD, []) if e.kind == "seizure"]
    assert len(seizures) == 1
    assert seizures[0].territory_id == "ghost_home"
    assert seizures[0].from_faction_id == GHOSTWIRE
    assert seizures[0].day == 5


def test_reinforce_adds_to_the_thinnest_district():
    corp_map = _contested_map()
    corp_map.territories["iron_home"].garrison = 2
    corp_map.territories["iron_second"].garrison = 0
    _reinforce(corp_map, IRONCLAD, HIT)
    assert corp_map.territories["iron_second"].garrison == 1
    assert corp_map.territories["iron_home"].garrison == 2


def test_reinforce_stops_at_the_cap():
    corp_map = _contested_map()
    for territory_id in ("iron_home", "iron_second"):
        corp_map.territories[territory_id].garrison = AI_GARRISON_CAP
    _reinforce(corp_map, IRONCLAD, HIT)
    assert corp_map.territories["iron_home"].garrison == AI_GARRISON_CAP
    assert corp_map.territories["iron_second"].garrison == AI_GARRISON_CAP


def test_reinforce_does_nothing_on_a_miss():
    corp_map = _contested_map()
    _reinforce(corp_map, IRONCLAD, MISS)
    assert corp_map.territories["iron_home"].garrison == 0


def test_attack_force_scales_with_holdings():
    corp_map = _contested_map()
    # Two districts -> MIN_AI_ATTACK_FORCE floors it.
    assert _attack_force(corp_map, IRONCLAD) == MIN_AI_ATTACK_FORCE
    for i in range(8):
        corp_map.territories[f"extra_{i}"] = _territory(f"extra_{i}", owner=IRONCLAD)
    assert _attack_force(corp_map, IRONCLAD) == 10 // AI_TERRITORIES_PER_ATTACKER


def test_relations_bias_which_rival_a_faction_moves_on():
    """The first thing to read relations.py at all: a corp hits whoever it already
    gets on worst with. Statistical rather than exact — the weighting is a bias,
    not a rule — but pinned to a seed, so it's deterministic."""
    corp_map = _contested_map()
    corp_map.territories["merid_edge"] = _territory("merid_edge", owner=MERIDIAN)
    relations = {
        frozenset((IRONCLAD, GHOSTWIRE)): -2,  # hated -> weight 5
        frozenset((IRONCLAD, MERIDIAN)): 2,  # liked -> weight 1
    }
    rng = random.Random(0)
    picks = [
        _pick_attack_target(corp_map, IRONCLAD, ["ghost_home", "merid_edge"], relations, rng)
        for _ in range(1000)
    ]
    assert picks.count("ghost_home") > picks.count("merid_edge") * 3


def test_pick_attack_target_falls_back_without_relations():
    """Hand-built fixtures omit CorpMap.relations, so target choice has to survive
    having none rather than raising on a missing pair."""
    corp_map = _contested_map()
    assert _pick_attack_target(corp_map, IRONCLAD, ["ghost_home"], None, random.Random(0)) == "ghost_home"


def test_the_players_faction_takes_no_turn_but_is_still_attackable():
    """The loss condition depends on this: resolve_rival_day skips the player's
    faction as an *actor*, but its ground is ordinary attack_candidates ground for
    everyone else, with no special case anywhere."""
    corp_map = _contested_map()
    actions = resolve_rival_day(
        Character(name="t"), corp_map, day=5, rng=ATTACK_HIT, player_faction_id=IRONCLAD
    )
    assert not [a for a in actions if a.kind == "faction" and a.actor_id == IRONCLAD]
    ghost_attack = _faction_action(actions, GHOSTWIRE).attack
    assert ghost_attack is not None
    assert ghost_attack.defender_id == IRONCLAD


@pytest.mark.parametrize("seed", range(150))
def test_a_rival_day_never_leaves_a_negative_garrison_or_orphan_owner(seed):
    """Seed sweep over the whole daily pipeline: whatever the dice do, garrisons
    stay non-negative and every district is held by a real faction or nobody."""
    corp_map = _contested_map()
    rng = random.Random(seed)
    valid = {f.id for f in FACTIONS} | {"neutral"}
    for day in range(1, 20):
        resolve_rival_day(Character(name="t"), corp_map, day=day, rng=rng)
        for territory in corp_map.territories.values():
            assert territory.garrison >= 0
            assert territory.owner in valid
            if territory.owner == "neutral":
                assert territory.garrison == 0
