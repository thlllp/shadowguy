"""Tests for security.py: procedural Security contract generation and nightly resolution."""

import random

import pytest

from shadowguy.character import Character
from shadowguy.checks import CRITICAL_MARGIN, CheckResult, day_tier
from shadowguy.corpmap import GENERATED_KINDS, PLAYER_OWNED_KINDS, generate_corp_map
from shadowguy.factions import FACTIONS, FACTIONS_BY_ID, RIVAL_WEIGHT
from shadowguy.security import (
    BLOWN_FIXER_TRUST_HIT,
    BLOWN_REP_HIT,
    BLOWN_STANDING_HIT,
    COMPLETION_BONUS_FRACTION,
    COMPLETION_FIXER_TRUST_GAIN,
    COMPLETION_REP_GAIN,
    COMPLETION_STANDING_GAIN,
    CRITICAL_SUCCESS_PAY_MULT,
    DIFFICULTY_BASE,
    NIGHT_FAILURE_DAMAGE,
    NIGHTLY_PAY_BASE,
    NIGHTS_RANGE,
    WATCH_SKILLS,
    SecurityContract,
    generate_security_contract,
    resolve_security_night,
)

SEEDS = range(150)


@pytest.fixture(scope="module")
def corp_map():
    return generate_corp_map(FACTIONS, random.Random(0))


# --- Generation invariants ---


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_contract_targets_a_real_held_territory_and_location(corp_map, seed):
    contract = generate_security_contract(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    territory = corp_map.territories[contract.territory_id]
    assert territory.owner in FACTIONS_BY_ID
    assert contract.faction_id == territory.owner
    location = next(loc for loc in territory.locations if loc.id == contract.location_id)
    assert location.kind in GENERATED_KINDS
    assert location.kind not in PLAYER_OWNED_KINDS


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_contract_nights_total_within_range(corp_map, seed):
    contract = generate_security_contract(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    assert NIGHTS_RANGE[0] <= contract.nights_total <= NIGHTS_RANGE[1]


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_contract_skill_is_a_valid_watch_skill(corp_map, seed):
    contract = generate_security_contract(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    assert contract.skill in WATCH_SKILLS


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("day", [1, 4, 7])
def test_generated_contract_pay_difficulty_and_bonus_match_tier(corp_map, seed, day):
    contract = generate_security_contract(day=day, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    tier = day_tier(day, len(DIFFICULTY_BASE))
    assert contract.nightly_pay == NIGHTLY_PAY_BASE[tier]
    assert DIFFICULTY_BASE[tier] - 1 <= contract.difficulty <= DIFFICULTY_BASE[tier] + 2
    assert contract.completion_bonus == round(
        contract.nightly_pay * contract.nights_total * COMPLETION_BONUS_FRACTION
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_contract_starts_with_no_nights_completed(corp_map, seed):
    contract = generate_security_contract(day=1, corp_map=corp_map, fixer_id="fx", rng=random.Random(seed))
    assert contract.nights_completed == 0
    assert not contract.is_complete


# --- Nightly resolution: force each CheckResult branch with a biased rng ---


def _make_contract(skill: str, difficulty: int, nights_total: int = 3) -> SecurityContract:
    return SecurityContract(
        id="security_test",
        fixer_id="fx",
        faction_id="faction_ironclad",
        territory_id="territory_test",
        location_id="location_test",
        skill=skill,
        difficulty=difficulty,
        nightly_pay=50,
        nights_total=nights_total,
        completion_bonus=100,
        offered_day=1,
    )


def _character_with_skill_value(skill_id: str, value: int) -> Character:
    """A fresh Character with the given skill forced to an exact skill_value, by
    zeroing its rank and setting the tied stat directly (bypasses spend_*, fine for
    a resolution test that only cares about the resulting pool size)."""
    character = Character(name="t")
    character.skill_ranks[skill_id] = 0
    character.perception = value  # every WATCH_SKILLS entry ties to perception or intelligence
    character.intelligence = value
    return character


class AlwaysSix(random.Random):
    def randint(self, a, b):
        return 6


def test_resolve_security_night_critical_success_pays_bonus_and_advances():
    contract = _make_contract(skill="sight", difficulty=9)  # opposing pool 0
    character = _character_with_skill_value("sight", CRITICAL_MARGIN)  # pool == CRITICAL_MARGIN
    result = resolve_security_night(character, contract, rng=AlwaysSix())
    assert result.roll.result is CheckResult.CRITICAL_SUCCESS
    assert result.pay == round(contract.nightly_pay * CRITICAL_SUCCESS_PAY_MULT)
    assert result.bonus == 0
    assert not result.blown
    assert not result.completed
    assert contract.nights_completed == 1
    assert character.cash == 100 + result.pay  # STARTING_CASH + pay


def test_resolve_security_night_success_completes_contract_on_last_night():
    contract = _make_contract(skill="sight", difficulty=9, nights_total=1)
    character = _character_with_skill_value("sight", 1)  # margin 1: plain success, not crit
    result = resolve_security_night(character, contract, rng=AlwaysSix())
    assert result.roll.result is CheckResult.SUCCESS
    assert result.pay == contract.nightly_pay
    assert result.bonus == contract.completion_bonus
    assert result.completed
    assert not result.blown
    assert contract.nights_completed == contract.nights_total
    assert character.cash == 100 + result.pay + result.bonus
    assert character.standing_with(contract.faction_id) == COMPLETION_STANDING_GAIN
    assert character.trust_with(contract.fixer_id) == COMPLETION_FIXER_TRUST_GAIN
    assert character.rep == COMPLETION_REP_GAIN


def test_resolve_security_night_plain_failure_costs_health_no_pay_but_still_advances():
    contract = _make_contract(skill="sight", difficulty=9)
    character = _character_with_skill_value("sight", 0)  # pool 0 both sides -> margin 0 -> FAILURE
    result = resolve_security_night(character, contract, rng=random.Random(0))
    assert result.roll.result is CheckResult.FAILURE
    assert result.pay == 0
    assert result.bonus == 0
    assert not result.blown
    assert not result.completed
    assert contract.nights_completed == 1
    assert character.health == character.max_health - NIGHT_FAILURE_DAMAGE


def test_resolve_security_night_failure_on_the_last_night_still_completes_the_contract():
    """A regression case: a contract must terminate at exactly nights_total, even if
    the final night is a failure -- not keep demanding extra nights because only a
    successful night was ever checked for completion."""
    contract = _make_contract(skill="sight", difficulty=9, nights_total=1)
    character = _character_with_skill_value("sight", 0)  # margin 0 -> FAILURE
    result = resolve_security_night(character, contract, rng=random.Random(0))
    assert result.roll.result is CheckResult.FAILURE
    assert result.pay == 0
    assert result.bonus == contract.completion_bonus  # still paid -- the term is served
    assert result.completed
    assert not result.blown
    assert contract.nights_completed == contract.nights_total
    assert character.cash == 100 + result.bonus


def test_resolve_security_night_critical_failure_blows_the_contract():
    difficulty = 9 + 2 * CRITICAL_MARGIN  # opposing pool == CRITICAL_MARGIN
    contract = _make_contract(skill="sight", difficulty=difficulty)
    character = _character_with_skill_value("sight", 0)  # player pool 0 -> 0 successes always
    result = resolve_security_night(character, contract, rng=AlwaysSix())
    assert result.roll.result is CheckResult.CRITICAL_FAILURE
    assert result.pay == 0
    assert result.bonus == 0
    assert result.blown
    assert not result.completed
    # A blown contract does not advance -- resolve_security_night doesn't touch
    # nights_completed on this branch, and doesn't remove the contract from any
    # list either (that's the caller's job, see MainMenu.on_list_view_selected).
    assert contract.nights_completed == 0
    assert character.health == character.max_health - NIGHT_FAILURE_DAMAGE
    assert character.standing_with(contract.faction_id) == BLOWN_STANDING_HIT
    assert character.trust_with(contract.fixer_id) == BLOWN_FIXER_TRUST_HIT
    assert character.rep == BLOWN_REP_HIT


def test_resolve_security_night_critical_failure_rival_factions_move_the_opposite_way():
    """Same standing_shift fan-out jobs.py's completed-job standing hit uses --
    hurting/helping one corp is a favour/harm to its rivals at half weight."""
    difficulty = 9 + 2 * CRITICAL_MARGIN
    contract = _make_contract(skill="sight", difficulty=difficulty)
    character = _character_with_skill_value("sight", 0)
    resolve_security_night(character, contract, rng=AlwaysSix())
    rival_ids = [fid for fid in FACTIONS_BY_ID if fid != contract.faction_id]
    for rival_id in rival_ids:
        assert character.standing_with(rival_id) == -BLOWN_STANDING_HIT // RIVAL_WEIGHT
