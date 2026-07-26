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

`RIVAL_RUNNERS` (Specter/Juncture/Mireille) are the three guaranteed every run, the
same way every FIXER_ROSTER entry is seated every run. `RUNNER_POOL` is a larger bench
of extras `select_active_runners` samples `RANDOM_RUNNER_COUNT` of at game start
(ShadowguyApp._new_run, stored as `app.runners`) -- the run's actual independent-runner
roster is `RIVAL_RUNNERS + those six`, so which extras are in a given run varies, the
way fixer.create_fixers varies *where* the fixed roster sits rather than *who's on it*.
`RUNNERS_BY_ID` still spans the whole universe (guaranteed three plus every pool
candidate), not just what got sampled -- a saved `CrewHire`/`JobOffer.taken_by` id must
resolve by id lookup regardless of whether this run happened to roll that runner in.
"""

import random
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

# The bench: not guaranteed, but a run's other RANDOM_RUNNER_COUNT independent runners
# are sampled from here. Spans budget/mid/elite tiers (rating 5/6/9) evenly across all
# three archetypes so no matter which six get rolled, a run still sees the same rough
# spread the guaranteed three already give it.
RUNNER_POOL = [
    RivalRunner(
        id="runner_null",
        name="Null",
        archetype="Netrunner",
        description="Cheap because the last three fixers who vouched for him are dead.",
        rating=5,
        daily_cost=30,
        job_cut=0.10,
    ),
    RivalRunner(
        id="runner_convoy",
        name="Convoy",
        archetype="Solo",
        description="Rents out as backup, never as the plan -- and that's fine by him.",
        rating=5,
        daily_cost=32,
        job_cut=0.11,
    ),
    RivalRunner(
        id="runner_tourmaline",
        name="Tourmaline",
        archetype="Infiltrator",
        description="New in town, still learning which doors don't have alarms.",
        rating=5,
        daily_cost=28,
        job_cut=0.09,
    ),
    RivalRunner(
        id="runner_flatline",
        name="Flatline",
        archetype="Netrunner",
        description="Learned to crack ICE by bricking three decks and surviving the fourth.",
        rating=6,
        daily_cost=40,
        job_cut=0.15,
    ),
    RivalRunner(
        id="runner_riptide",
        name="Riptide",
        archetype="Solo",
        description="Doesn't threaten. Just shows up, and the job stops arguing.",
        rating=6,
        daily_cost=38,
        job_cut=0.14,
    ),
    RivalRunner(
        id="runner_vellum",
        name="Vellum",
        archetype="Infiltrator",
        description="Reads a room's cameras before she reads the room.",
        rating=6,
        daily_cost=36,
        job_cut=0.13,
    ),
    RivalRunner(
        id="runner_dominion",
        name="Dominion",
        archetype="Netrunner",
        description="Corp security still argues about whether he was ever really in their system.",
        rating=9,
        daily_cost=70,
        job_cut=0.28,
    ),
    RivalRunner(
        id="runner_switchback",
        name="Switchback",
        archetype="Solo",
        description="Took a bullet meant for a client once, then billed them for the jacket.",
        rating=9,
        daily_cost=68,
        job_cut=0.27,
    ),
    RivalRunner(
        id="runner_glasswing",
        name="Glasswing",
        archetype="Infiltrator",
        description="Nobody's seen her leave a building; they've only noticed she's gone.",
        rating=9,
        daily_cost=65,
        job_cut=0.26,
    ),
]

# How many of RUNNER_POOL join the guaranteed three each run, picked once at game start.
RANDOM_RUNNER_COUNT = 6
if len(RUNNER_POOL) < RANDOM_RUNNER_COUNT:
    raise ValueError("RUNNER_POOL must hold at least RANDOM_RUNNER_COUNT candidates")

RUNNERS_BY_ID = {runner.id: runner for runner in RIVAL_RUNNERS + RUNNER_POOL}
if len(RUNNERS_BY_ID) != len(RIVAL_RUNNERS) + len(RUNNER_POOL):
    raise ValueError("RivalRunner ids must be unique across RIVAL_RUNNERS and RUNNER_POOL")


def select_active_runners(rng: random.Random) -> list["RivalRunner"]:
    """The independent-runner roster for a run: the guaranteed three plus a random
    RANDOM_RUNNER_COUNT from RUNNER_POOL. Called once at game start
    (ShadowguyApp._new_run) and persisted as `app.runners`, the same "seeded once,
    lives for the run" treatment fixer.create_fixers gives the fixer roster."""
    return RIVAL_RUNNERS + rng.sample(RUNNER_POOL, RANDOM_RUNNER_COUNT)


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
