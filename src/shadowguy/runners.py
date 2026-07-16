"""Street runners you can hire onto a job's crew.

A small hand-authored roster, like fixer.FIXER_ROSTER and factions.FACTIONS. These
started as rivals-only (identity, no relationship value); recruiting is the mechanic
that changes that -- you meet them at bars (corpmap.LocationKind.BAR) and pay to bring
them on. `archetype` is their specialist and matches jobs.SPECIALIST_FOR_STAT's values
exactly (Netrunner / Solo / Infiltrator), so a runner slots straight onto the crew role
their archetype fits. `rating` is how good they are at that specialty -- an effective
skill_value the run-time crew effect will roll once that increment lands; `hire_cost`
is the upfront cash to sign them.
"""

from dataclasses import dataclass


@dataclass
class RivalRunner:
    id: str
    name: str
    archetype: str  # specialist: "Netrunner" / "Solo" / "Infiltrator"
    description: str
    rating: int  # effective skill_value at their specialty (for the run-time crew effect)
    hire_cost: int  # upfront cash to bring them onto your crew


RIVAL_RUNNERS = [
    RivalRunner(
        id="runner_specter",
        name="Specter",
        archetype="Netrunner",
        description="Ghosts through ICE for whoever pays best, and burns a fixer the moment a better offer shows.",
        rating=8,
        hire_cost=600,
    ),
    RivalRunner(
        id="runner_juncture",
        name="Juncture",
        archetype="Solo",
        description="Muscle for hire, and the reason two fixers on the board stopped taking new jobs this month.",
        rating=8,
        hire_cost=550,
    ),
    RivalRunner(
        id="runner_mireille",
        name="Mireille",
        archetype="Infiltrator",
        description="Works the same jobs you do, one step ahead or one step behind, never both.",
        rating=7,
        hire_cost=500,
    ),
]

RUNNERS_BY_ID = {runner.id: runner for runner in RIVAL_RUNNERS}
