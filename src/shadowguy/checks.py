import random
from dataclasses import dataclass
from enum import Enum, auto


class CheckResult(Enum):
    CRITICAL_SUCCESS = auto()
    SUCCESS = auto()
    FAILURE = auto()
    CRITICAL_FAILURE = auto()

    @property
    def passed(self) -> bool:
        """Did the check succeed at all? Crits count — a nat 20 is still a success."""
        return self in (CheckResult.CRITICAL_SUCCESS, CheckResult.SUCCESS)


@dataclass
class CheckRoll:
    result: CheckResult
    d20: int
    stat_value: int
    advantage: int
    difficulty: int


def resolve_check(
    stat_value: int,
    difficulty: int,
    advantage: int = 0,
    rng: random.Random | None = None,
) -> CheckRoll:
    rng = rng or random
    d20 = rng.randint(1, 20)
    total = d20 + stat_value + advantage

    if d20 == 20:
        result = CheckResult.CRITICAL_SUCCESS
    elif d20 == 1:
        result = CheckResult.CRITICAL_FAILURE
    else:
        result = CheckResult.SUCCESS if total >= difficulty else CheckResult.FAILURE

    return CheckRoll(
        result=result,
        d20=d20,
        stat_value=stat_value,
        advantage=advantage,
        difficulty=difficulty,
    )
