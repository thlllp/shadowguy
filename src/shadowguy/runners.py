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
just that job.
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
