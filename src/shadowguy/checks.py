"""The one place a check gets resolved: a dice pool of d6, 5s and 6s are successes.

resolve_check is an *opposed* roll: the attacker's pool is stat_value+advantage d6,
the opposing pool is `difficulty` converted to a pool size via pool_for_difficulty.
Every difficulty constant elsewhere in the game (jobs, gigs, legwork, combat) is
still written and tuned on the old d20-era DC scale (roughly 9-21) — this module is
the only place that scale becomes a dice pool, so nothing else had to be re-derived
by hand when the mechanic changed.
"""

import random
from dataclasses import dataclass
from enum import Enum, auto

# A d6 is a success on a 5 or 6 — a 1-in-3 chance per die.
SUCCESS_FACES = (5, 6)

# Net successes (attacker's minus the opposing pool's) at or beyond this either way
# is a critical — the pool system's stand-in for the d20's natural 20/natural 1.
# No single die can swing a pool roll the way one d20 face used to, so a critical is
# now "the gap between the two pools was this wide" rather than a literal face.
CRITICAL_MARGIN = 3


class CheckResult(Enum):
    CRITICAL_SUCCESS = auto()
    SUCCESS = auto()
    FAILURE = auto()
    CRITICAL_FAILURE = auto()

    @property
    def passed(self) -> bool:
        """Did the check succeed at all? Crits count — net successes > 0 either way."""
        return self in (CheckResult.CRITICAL_SUCCESS, CheckResult.SUCCESS)


@dataclass
class CheckRoll:
    result: CheckResult
    successes: int  # 5s and 6s rolled on the attacker's side
    opposing_successes: int  # 5s and 6s rolled on the opposing pool
    pool: int  # dice actually rolled by the attacker (stat_value + advantage, floored at 0)
    opposing_pool: int  # dice rolled by the opposition (pool_for_difficulty(difficulty))
    advantage: int

    @property
    def margin(self) -> int:
        """Net successes. Combat reads this to size a hit's damage and its soak;
        scene/jobs only need the four-tier CheckResult, not the number itself."""
        return self.successes - self.opposing_successes


def count_successes(pool: int, rng: random.Random) -> int:
    """Roll `pool` d6 and count the 5s and 6s. A bare utility, not an opposed check —
    combat's soak roll uses this directly, since mitigating a hit isn't a pass/fail
    check against anything, just "how many of these dice come up good."
    """
    return sum(1 for _ in range(max(pool, 0)) if rng.randint(1, 6) in SUCCESS_FACES)


def pool_for_difficulty(difficulty: int) -> int:
    """Convert an old-style DC (tuned for d20 + skill_value >= difficulty) into an
    opposing dice-pool size: half the DC above a floor of 9, rounded, clamped at 0.

    The slope and offset aren't arbitrary -- they're fit against the old d20 curve
    across the game's actual skill_value/difficulty range (skill_value 2-15,
    difficulty 9-21 -- see every difficulty constant in combat.py/jobs.py/content.py).
    A naive difficulty/3 rescale (matching the attacker's pool being skill_value
    dice, unconverted -- see resolve_check) badly overweights the opposing side and
    starves low-skill checks; this formula is what landed closest to the original
    pass rates instead. 9 is the lowest difficulty anything in the game uses today,
    which is why it's the formula's zero point.
    """
    return max(0, round((difficulty - 9) / 2))


def resolve_check(
    stat_value: int,
    difficulty: int,
    advantage: int = 0,
    rng: random.Random | None = None,
) -> CheckRoll:
    rng = resolve_rng(rng)
    pool = max(0, stat_value + advantage)
    opposing_pool = pool_for_difficulty(difficulty)
    successes = count_successes(pool, rng)
    opposing_successes = count_successes(opposing_pool, rng)
    margin = successes - opposing_successes

    if margin >= CRITICAL_MARGIN:
        result = CheckResult.CRITICAL_SUCCESS
    elif margin <= -CRITICAL_MARGIN:
        result = CheckResult.CRITICAL_FAILURE
    elif margin > 0:
        result = CheckResult.SUCCESS
    else:
        result = CheckResult.FAILURE

    return CheckRoll(
        result=result,
        successes=successes,
        opposing_successes=opposing_successes,
        pool=pool,
        opposing_pool=opposing_pool,
        advantage=advantage,
    )


def day_tier(day: int, tier_count: int) -> int:
    return min(tier_count - 1, (day - 1) // 3)


def resolve_rng(rng: random.Random | None) -> random.Random:
    return rng or random
