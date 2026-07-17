"""Tests for checks.py: the opposed d6 dice pool, DC-to-pool conversion, day tiers."""

import random

import pytest

from shadowguy.checks import (
    CRITICAL_MARGIN,
    CheckResult,
    count_successes,
    day_tier,
    pool_for_difficulty,
    resolve_check,
)


def test_pool_for_difficulty_floors_at_zero():
    assert pool_for_difficulty(9) == 0
    assert pool_for_difficulty(0) == 0
    assert pool_for_difficulty(-100) == 0


def test_pool_for_difficulty_known_points():
    assert pool_for_difficulty(9) == 0
    assert pool_for_difficulty(11) == 1
    assert pool_for_difficulty(13) == 2
    assert pool_for_difficulty(21) == 6


def test_count_successes_all_dice_hit_with_biased_rng():
    class AlwaysSix(random.Random):
        def randint(self, a, b):
            return 6

    assert count_successes(5, AlwaysSix()) == 5


def test_count_successes_no_dice_miss_with_biased_rng():
    class AlwaysOne(random.Random):
        def randint(self, a, b):
            return 1

    assert count_successes(5, AlwaysOne()) == 0


def test_count_successes_floors_negative_pool_at_zero_dice():
    assert count_successes(-5, random.Random(1)) == 0


def test_resolve_check_pool_is_stat_plus_advantage_floored_at_zero():
    rng = random.Random(1)
    roll = resolve_check(stat_value=-10, difficulty=9, advantage=3, rng=rng)
    assert roll.pool == 0  # -10 + 3 floored at 0, not negative


def test_resolve_check_guaranteed_success_with_biased_rng():
    class AlwaysSix(random.Random):
        def randint(self, a, b):
            return 6

    roll = resolve_check(stat_value=5, difficulty=21, advantage=0, rng=AlwaysSix())
    # Attacker rolls 5 dice, all successes; opposing pool (6 dice) also all hit,
    # net margin 5-6 = -1 -> plain failure, not a crit either way.
    assert roll.successes == 5
    assert roll.opposing_successes == 6
    assert roll.margin == -1
    assert roll.result is CheckResult.FAILURE


def test_resolve_check_guaranteed_failure_with_biased_rng():
    class AlwaysOne(random.Random):
        def randint(self, a, b):
            return 1

    roll = resolve_check(stat_value=10, difficulty=21, advantage=0, rng=AlwaysOne())
    assert roll.successes == 0
    assert roll.opposing_successes == 0
    assert roll.result is CheckResult.FAILURE  # 0 margin is a plain failure, not a crit


def test_resolve_check_critical_success_requires_margin_at_least_critical_margin():
    class Mixed(random.Random):
        """Attacker always hits, opposition always misses -- margin = pool size."""

        def __init__(self, attacker_pool):
            super().__init__()
            self.calls = 0
            self.attacker_pool = attacker_pool

        def randint(self, a, b):
            self.calls += 1
            return 6 if self.calls <= self.attacker_pool else 1

    roll = resolve_check(stat_value=CRITICAL_MARGIN, difficulty=9, advantage=0, rng=Mixed(CRITICAL_MARGIN))
    assert roll.margin == CRITICAL_MARGIN
    assert roll.result is CheckResult.CRITICAL_SUCCESS


def test_resolve_check_critical_failure_requires_margin_at_most_negative_critical_margin():
    class Mixed(random.Random):
        """Attacker always misses, opposition always hits -- margin = -opposing pool."""

        def __init__(self, opposing_pool):
            super().__init__()
            self.calls = 0
            self.opposing_pool = opposing_pool

        def randint(self, a, b):
            self.calls += 1
            return 1 if self.calls <= 0 else 6

    difficulty = 9 + 2 * CRITICAL_MARGIN  # pool_for_difficulty -> CRITICAL_MARGIN dice
    roll = resolve_check(stat_value=0, difficulty=difficulty, advantage=0, rng=Mixed(CRITICAL_MARGIN))
    assert roll.margin == -CRITICAL_MARGIN
    assert roll.result is CheckResult.CRITICAL_FAILURE


def test_resolve_check_falls_back_to_module_random_without_rng():
    random.seed(12345)
    roll = resolve_check(stat_value=5, difficulty=13)
    assert roll.pool == 5
    assert isinstance(roll.result, CheckResult)


def test_check_result_passed_property():
    assert CheckResult.SUCCESS.passed
    assert CheckResult.CRITICAL_SUCCESS.passed
    assert not CheckResult.FAILURE.passed
    assert not CheckResult.CRITICAL_FAILURE.passed


@pytest.mark.parametrize(
    "day,tier_count,expected",
    [(1, 3, 0), (3, 3, 0), (4, 3, 1), (6, 3, 1), (7, 3, 2), (100, 3, 2)],
)
def test_day_tier_buckets_every_three_days_and_clamps_to_tier_count(day, tier_count, expected):
    assert day_tier(day, tier_count) == expected
