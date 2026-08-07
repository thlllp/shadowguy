"""Fishing: a Docks location's one activity (corpmap.LocationKind.DOCKS).

A three-stage flavor Scene -- cast, wait, reel -- built from the same Choice/Outcome/
narration primitives every other Scene uses, so nothing new is needed in scene.py or
SceneScreen. Reward is cash only (no health/rep/standing), and it doesn't scale with
day the way jobs/gigs do: this is a low-stakes break, not another income lever.

Unlike a gig, a fishing trip is never stored anywhere -- DocksScreen generates a fresh
one each time it's picked, the same way corp_map_screen.py builds a legwork Scene on
demand. So there's no id collision to guard against, and no save-state to add.
"""

from shadowguy.scene import Choice, Outcome, Scene, SceneKind, Stage
from shadowguy.skills import skill_for

# Same "casing a place" tier of check as scavenging/legwork's NEARBY_DIFFICULTY (11) --
# a fishing trip is no harder to call than reading a block before you hit it.
FISHING_DIFFICULTY = 11
FISHING_HOURS_COST = 4

FISH_CASH = 30
FISH_CRITICAL_CASH = 50

CAST_SKILLS = (
    ("pattern_seeking", "Read the water for where they're feeding"),
    ("fortitude", "Settle in and wait them out"),
)
REEL_SKILLS = (
    ("sturdy", "Muscle it in hand over hand"),
    ("intuition", "Play it, feel for the give"),
)


def _cast_choice(skill: str, label: str) -> Choice:
    return Choice(
        label=f"{label} ({skill_for(skill).name})",
        skill=skill,
        difficulty=FISHING_DIFFICULTY,
        success=Outcome(text="Something takes interest. The line goes taut.", next_stage="wait"),
        failure=Outcome(text="Nothing bites. You call it a day."),
        critical_success=Outcome(text="A bite almost before the hook settles.", next_stage="wait"),
        critical_failure=Outcome(text="You spook every fish within casting distance."),
    )


def _reel_choice(skill: str, label: str) -> Choice:
    return Choice(
        label=f"{label} ({skill_for(skill).name})",
        skill=skill,
        difficulty=FISHING_DIFFICULTY,
        success=Outcome(text="It comes up thrashing. Worth something at the market.", cash_delta=FISH_CASH),
        failure=Outcome(text="The line goes slack. It's gone."),
        critical_success=Outcome(text="A real haul -- worth carrying home.", cash_delta=FISH_CRITICAL_CASH),
        critical_failure=Outcome(text="The rod nearly goes in the water after it."),
    )


def generate_fishing_trip() -> Scene:
    """A fresh cast/wait/reel Scene, offering both CAST_SKILLS/REEL_SKILLS approaches
    at their stage -- same "a build buys the skills it actually has" choice as a gig's
    two approaches, not a pre-rolled one. Any skill roll's failure ends the trip on the
    spot (no next_stage) -- a botched cast never costs anything beyond the trip's own
    hours_cost, same as a missed scavenge or gig."""
    return Scene(
        id="fishing",
        title="Fishing at the Docks",
        kind=SceneKind.FISHING,
        hours_cost=FISHING_HOURS_COST,
        start_stage="cast",
        stages={
            "cast": Stage(
                id="cast",
                prompt="You find a spot on the pier and bait a line.",
                choices=[_cast_choice(skill, label) for skill, label in CAST_SKILLS],
            ),
            "wait": Stage(
                id="wait",
                prompt="",
                choices=[],
                narration=Outcome(text="You give it slack and let it run.", next_stage="reel"),
            ),
            "reel": Stage(
                id="reel",
                prompt="Now or never.",
                choices=[_reel_choice(skill, label) for skill, label in REEL_SKILLS],
            ),
        },
    )
