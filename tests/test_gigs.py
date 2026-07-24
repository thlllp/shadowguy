"""Invariant tests for gigs.py's procedural per-Location gig generation."""

import random

import pytest

from shadowguy.corpmap import GENERATED_KINDS, generate_corp_map
from shadowguy.factions import FACTIONS
from shadowguy.gigs import (
    GIG_CRIT_MULT,
    GIG_FAIL_REP_HIT,
    GIG_FAIL_STANDING_HIT,
    GIG_MAX_APPROACHES,
    GIG_STANDING_GAIN,
    generate_gig,
    refresh_gigs,
)

SEEDS = range(150)


@pytest.fixture(scope="module")
def corp_map():
    return generate_corp_map(FACTIONS, random.Random(0))


def _a_location_with_characters(corp_map):
    for territory in corp_map.territories.values():
        for location in territory.locations:
            if location.characters:
                return territory, location
    raise AssertionError("no location with characters found")


@pytest.mark.parametrize("seed", SEEDS)
def test_gig_offers_between_one_and_max_approaches(corp_map, seed):
    territory, location = _a_location_with_characters(corp_map)
    character = location.characters[0]
    scene = generate_gig(day=1, location=location, character=character, territory=territory, rng=random.Random(seed))
    approaches = scene.stages["start"].choices
    assert 1 <= len(approaches) <= GIG_MAX_APPROACHES


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
def test_refresh_gigs_fills_every_eligible_location_exactly_once(corp_map, seed):
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map, gigs, day=1, rng=random.Random(seed))
    # Mirrors refresh_gigs' own eligibility test (location.kind not in _GIG_TEMPLATES,
    # gigs.py) via the public equivalent, GENERATED_KINDS -- so a future UNROLLED_KINDS
    # addition (another injected kind with characters but no gig template) doesn't need
    # this test hand-edited to match, the way corp_hq/gang_den/junkyard each did.
    eligible = [
        location
        for territory in corp_map.territories.values()
        for location in territory.locations
        if location.characters and location.kind in GENERATED_KINDS
    ]
    assert len(gigs) == len(eligible)


def test_refresh_gigs_is_idempotent_and_does_not_churn_existing_offers():
    corp_map_ = generate_corp_map(FACTIONS, random.Random(1))
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map_, gigs, day=1, rng=random.Random(1))
    before = dict(gigs)
    refresh_gigs(corp_map_, gigs, day=2, rng=random.Random(2))
    assert gigs == before


def test_refresh_gigs_skips_corp_hq():
    """A corp HQ has characters (its officers) but no gig template -- refresh_gigs
    must skip it explicitly rather than KeyError in generate_gig."""
    corp_map_ = generate_corp_map(FACTIONS, random.Random(2))
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map_, gigs, day=1, rng=random.Random(2))
    hq_ids = {
        location.id
        for territory in corp_map_.territories.values()
        for location in territory.locations
        if location.kind == "corp_hq"
    }
    assert hq_ids  # every faction has one
    assert not (hq_ids & set(gigs))


def test_refresh_gigs_skips_gang_den():
    """A gang's den has characters (its soldier and lieutenant) but no gig template --
    refresh_gigs must skip it explicitly rather than KeyError in generate_gig."""
    corp_map_ = generate_corp_map(FACTIONS, random.Random(2))
    gigs: dict[str, object] = {}
    refresh_gigs(corp_map_, gigs, day=1, rng=random.Random(2))
    den_ids = {
        location.id
        for territory in corp_map_.territories.values()
        for location in territory.locations
        if location.kind == "gang_den"
    }
    assert den_ids  # every gang has one
    assert not (den_ids & set(gigs))
