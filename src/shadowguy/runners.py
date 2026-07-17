"""Street runners you can hire onto a job's crew.

A small hand-authored roster, like fixer.FIXER_ROSTER and factions.FACTIONS. These
started as rivals-only (identity, no relationship value); recruiting is the mechanic
that changes that -- you meet them at bars (corpmap.LocationKind.BAR) and pay to bring
them on. `archetype` is their specialist and matches jobs.SPECIALIST_FOR_STAT's values
exactly (Netrunner / Solo / Infiltrator), so a runner slots straight onto the crew role
their archetype fits. `rating` is how good they are at that specialty -- an effective
skill_value the run-time crew effect will roll once that increment lands.

Two ways to engage one (see Character.crew / app.BarScreen), each with its own price:
`daily_cost` is the per-day wage if you keep them on indefinitely (charged every rest),
and `job_cut` is the fraction of a single job's payout they take if you sign them for
just that job. Both are the *listed* terms; the recruiter's Leadership skill bends them
(recruit_wage / recruit_cut), the way standing bends shop prices in shops.py.
"""

from dataclasses import dataclass


@dataclass
class RivalRunner:
    id: str
    name: str
    archetype: str  # specialist: "Netrunner" / "Solo" / "Infiltrator"
    description: str
    rating: int  # effective skill_value at their specialty (for the run-time crew effect)
    daily_cost: int  # per-day wage when kept on indefinitely (charged each rest)
    job_cut: float  # fraction of a single job's payout they take when hired for that job


RIVAL_RUNNERS = [
    RivalRunner(
        id="runner_specter",
        name="Specter",
        archetype="Netrunner",
        description="Ghosts through ICE for whoever pays best, and burns a fixer the moment a better offer shows.",
        rating=8,
        daily_cost=60,
        job_cut=0.25,
    ),
    RivalRunner(
        id="runner_juncture",
        name="Juncture",
        archetype="Solo",
        description="Muscle for hire, and the reason two fixers on the board stopped taking new jobs this month.",
        rating=8,
        daily_cost=55,
        job_cut=0.22,
    ),
    RivalRunner(
        id="runner_mireille",
        name="Mireille",
        archetype="Infiltrator",
        description="Works the same jobs you do, one step ahead or one step behind, never both.",
        rating=7,
        daily_cost=45,
        job_cut=0.18,
    ),
]

RUNNERS_BY_ID = {runner.id: runner for runner in RIVAL_RUNNERS}


# Leadership (a cool skill, skills.py) discounts recruiting terms, one-directionally: a
# runner's listed daily_cost/job_cut is what they charge anyone -- they're looking for work
# too, so a recruiter with no Leadership pays full price, never a markup. Each point of
# skill_value("leadership") above LEADERSHIP_BASE (the lowest a skill_value can be: cool 1 +
# rank 1) shaves LEADERSHIP_TERMS_STEP off both, up to LEADERSHIP_TERMS_CAP -- like
# shops._standing_discount but floored at zero on the penalty side. Leadership only moves
# with gear over a run (no XP), so callers pass a live skill_value rather than locking terms
# in at hire. Takes a plain int, like shops.buy_price(base, standing), to keep this a leaf.
LEADERSHIP_BASE = 2
LEADERSHIP_TERMS_STEP = 0.03
LEADERSHIP_TERMS_CAP = 0.20


def _leadership_discount(leadership: int) -> float:
    earned = max(0, leadership - LEADERSHIP_BASE) * LEADERSHIP_TERMS_STEP
    return min(LEADERSHIP_TERMS_CAP, earned)


def recruit_wage(runner: RivalRunner, leadership: int) -> int:
    """The daily wage to keep `runner` on, discounted by the recruiter's Leadership. At or
    below base it's the listed cost; higher Leadership is cheaper, never below 1eb."""
    return max(1, round(runner.daily_cost * (1 - _leadership_discount(leadership))))


def recruit_cut(runner: RivalRunner, leadership: int) -> float:
    """The fraction of a job's payout `runner` takes, discounted by the recruiter's
    Leadership. At or below base it's the listed cut; higher Leadership shrinks it."""
    return runner.job_cut * (1 - _leadership_discount(leadership))
