"""Rival street runners: other operators working the same city.

A roster like fixer.FIXER_ROSTER and factions.FACTIONS -- small and
hand-authored. Identity only for now: unlike fixer trust or faction standing,
nothing in the game moves a rival runner's regard for you yet, because there's
no mechanic (a shared job, a territory conflict) that would drive one. Add a
relationship value here once there's something to trigger it, rather than
inventing a number nothing can change.
"""

from dataclasses import dataclass


@dataclass
class RivalRunner:
    id: str
    name: str
    archetype: str
    description: str


RIVAL_RUNNERS = [
    RivalRunner(
        id="runner_specter",
        name="Specter",
        archetype="Netrunner",
        description="Ghosts through ICE for whoever pays best, and burns a fixer the moment a better offer shows.",
    ),
    RivalRunner(
        id="runner_juncture",
        name="Juncture",
        archetype="Solo",
        description="Muscle for hire, and the reason two fixers on the board stopped taking new jobs this month.",
    ),
    RivalRunner(
        id="runner_mireille",
        name="Mireille",
        archetype="Infiltrator",
        description="Works the same jobs you do, one step ahead or one step behind, never both.",
    ),
]
