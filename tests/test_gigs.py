"""Invariant tests for gigs.py's procedural per-Location gig generation."""

import random

import pytest

from shadowguy.corpmap import GENERATED_KINDS
from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.gigs import (
    GIG_CRIT_MULT,
    GIG_FAIL_REP_HIT,
    GIG_FAIL_STANDING_HIT,
    GIG_STANDING_GAIN,
    generate_gig,
    refresh_gigs,
)


def _eligible_location_ids(corp_map):
    return {
        location.id
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.characters and location.kind in GENERATED_KINDS
    }

SEEDS = range(150)


def _a_location_with_characters(corp_map):
    for territory in corp_map.territories.values():
        for location in territory.locations:
            if location.characters:
                return territory, location
    raise AssertionError("no location with characters found")


@pytest.mark.parametrize("seed", SEEDS)
def test_gig_offers_exactly_two_approaches(corp_map, seed):
    territory, location = _a_location_with_characters(corp_map)
    character = location.characters[0]
    scene = generate_gig(day=1, location=location, character=character, territory=territory, rng=random.Random(seed))
    approaches = scene.stages["start"].choices
    assert len(approaches) == 2


@pytest.mark.parametrize("seed", SEEDS)
def test_gig_targets_the_owning_character_and_location(corp_map, seed):
    territory, location = _a_location_with_characters(corp_map)
    character = location.characters[0]
    scene = generate_gig(day=1, location=location, character=character, territory=territory, rng=random.Random(seed))
    assert scene.target_character_id == character.id
    assert scene.target_location_id == location.id
    assert scene.target_territory_id == territory.id


@pytest.mark.parametrize("seed", SEEDS)
def test_gig_success_pays_cash_and_standing_failure_costs_no_health(corp_map, seed):
    territory, location = _a_location_with_characters(corp_map)
    character = location.characters[0]
    scene = generate_gig(day=1, location=location, character=character, territory=territory, rng=random.Random(seed))
    for choice in scene.stages["start"].choices:
        assert choice.success.cash_delta > 0
        assert choice.success.local_standing_delta == GIG_STANDING_GAIN
        # A plain failure costs no health -- just standing and rep, a clean miss.
        assert choice.failure.health_delta == 0
        assert choice.failure.local_standing_delta == GIG_FAIL_STANDING_HIT
        assert choice.failure.rep_delta == GIG_FAIL_REP_HIT


@pytest.mark.parametrize("seed", SEEDS)
def test_gig_critical_success_pays_more_than_plain_success(corp_map, seed):
    territory, location = _a_location_with_characters(corp_map)
    character = location.characters[0]
    scene = generate_gig(day=1, location=location, character=character, territory=territory, rng=random.Random(seed))
    for choice in scene.stages["start"].choices:
        assert choice.critical_success.cash_delta == int(choice.success.cash_delta * GIG_CRIT_MULT)


@pytest.mark.parametrize("seed", SEEDS)
def test_gig_critical_failure_costs_health_unlike_plain_failure(corp_map, seed):
    territory, location = _a_location_with_characters(corp_map)
    character = location.characters[0]
    scene = generate_gig(day=1, location=location, character=character, territory=territory, rng=random.Random(seed))
    for choice in scene.stages["start"].choices:
        assert choice.critical_failure.health_delta < 0


@pytest.mark.parametrize("seed", SEEDS)
def test_refresh_gigs_only_ever_fills_eligible_locations(corp_map, seed):
    """Spawning is a GIG_SPAWN_CHANCE roll per location now, not a guaranteed fill, but
    it must still never touch a location outside the eligible set (mirrors refresh_gigs'
    own eligibility test -- location.kind not in _GIG_TEMPLATES, gigs.py -- via the
    public equivalent, GENERATED_KINDS -- so a future UNROLLED_KINDS addition doesn't
    need this test hand-edited to match, the way corp_hq/gang_den/junkyard each did)."""
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map, gigs, day=1, rng=random.Random(seed))
    assert set(gigs) <= _eligible_location_ids(corp_map)


@pytest.mark.parametrize("seed", SEEDS)
def test_refresh_gigs_eventually_fills_every_eligible_location(seed):
    """No single tick guarantees a fill any more, but GIG_SPAWN_CHANCE per empty slot
    per day means every eligible location should fill within enough ticks -- a wide
    seed sweep over the roll, not just the map layout, catches an off-by-one in the
    spawn-chance comparison that would leave a slot permanently empty."""
    corp_map_ = generate_corp_map(FACTIONS, random.Random(seed))
    eligible = _eligible_location_ids(corp_map_)
    gigs: dict[str, object] = {}
    rng = random.Random(seed)
    for day in range(1, 200):
        refresh_gigs(corp_map_, gigs, day=day, rng=rng)
        if set(gigs) == eligible:
            break
    assert set(gigs) == eligible


def test_refresh_gigs_never_churns_an_existing_gig():
    corp_map_ = generate_corp_map(FACTIONS, random.Random(1))
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map_, gigs, day=1, rng=random.Random(1))
    before = dict(gigs)
    refresh_gigs(corp_map_, gigs, day=2, rng=random.Random(2))
    for location_id, scene in before.items():
        assert gigs[location_id] is scene


@pytest.mark.parametrize("kind", ["corp_hq", "gang_den"])
def test_refresh_gigs_skips_locations_with_characters_but_no_gig_template(kind):
    """A corp HQ has its officers and a gang den its soldier and lieutenant, but neither
    has a gig template -- refresh_gigs must skip them explicitly rather than KeyError in
    generate_gig."""
    corp_map_ = generate_corp_map(FACTIONS, random.Random(2))
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map_, gigs, day=1, rng=random.Random(2))
    ids = {
        location.id
        for territory in corp_map_.territories.values()
        for location in territory.locations
        if location.kind == kind
    }
    assert ids  # every faction has an HQ, every gang a den
    assert not (ids & set(gigs))
