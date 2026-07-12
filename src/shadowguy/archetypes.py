"""Preset builds for fast character creation.

An Archetype is just a canned allocation of the same 6 stat points and 20 skill
points the player would otherwise spend by hand on CharacterCreationScreen.
`apply()` spends them through Character.spend_stat_point/spend_skill_point rather
than assigning fields, so a preset is subject to the rank cap and the rank-cost
curve exactly like a hand-built runner — it cannot buy something the player
couldn't. `_check_affordable` proves at import that each one spends its pools to
exactly zero, so an unaffordable preset is a startup error, not a broken run.

Every skill here is one something actually rolls: the six in jobs.ARCHETYPES'
skill_sequences, the eight in corpmap.LOCATION_SKILL (legwork), and Negotiations
(the gig). Points in any of the other skills would be dead — nothing checks them
yet — so presets stay out of them.

(Not to be confused with jobs.JobArchetype, which is a template for a *job*.)
"""

from dataclasses import dataclass

from shadowguy.character import (
    STARTING_SKILL_POINTS,
    STARTING_STAT_POINTS,
    Character,
)


@dataclass(frozen=True)
class Archetype:
    id: str
    name: str
    description: str
    stats: dict[str, int]  # core stat -> points poured into it
    skills: dict[str, int]  # skill id -> rank to buy it up to

    def apply(self, character: Character) -> None:
        """Spend this build onto a freshly reset character."""
        for stat, points in self.stats.items():
            for _ in range(points):
                if not character.spend_stat_point(stat):
                    raise ValueError(f"{self.id}: ran out of stat points buying {stat}")
        for skill_id, target_rank in self.skills.items():
            while character.skill_rank(skill_id) < target_rank:
                if not character.spend_skill_point(skill_id):
                    raise ValueError(f"{self.id}: cannot afford {skill_id} rank {target_rank}")


# id, name, description, stats, skills (id -> target rank)
_ARCHETYPE_ROWS = (
    (
        "enforcer",
        "Enforcer",
        "Muscle. Hits hard, soaks hits, and is no good at all at casing a place.",
        {"body": 3, "strength": 3},
        {"grapple": 7, "toughness": 6, "negotiations": 4, "read_the_room": 2},
    ),
    (
        "hacker",
        "Hacker",
        "Breaks systems. Owns the wired half of the board, weak the moment it turns physical.",
        {"intelligence": 4, "perception": 2},
        {"hack": 7, "tinkering": 5, "infer": 4, "pattern_seeking": 4},
    ),
    (
        "infiltrator",
        "Infiltrator",
        "Gets in unseen and talks their way out. Broad, but tops out lower than a specialist.",
        {"agility": 4, "perception": 2},
        {"stealth": 7, "deception": 5, "sight": 4, "read_the_room": 4},
    ),
)

ARCHETYPES = [
    Archetype(id=id_, name=name, description=description, stats=stats, skills=skills)
    for id_, name, description, stats, skills in _ARCHETYPE_ROWS
]
ARCHETYPES_BY_ID = {archetype.id: archetype for archetype in ARCHETYPES}


def _check_affordable() -> None:
    """Every preset must spend both pools to exactly zero, at import."""
    for archetype in ARCHETYPES:
        character = Character(name="_check")
        archetype.apply(character)  # raises if a buy is refused
        if character.stat_points or character.skill_points:
            raise ValueError(
                f"{archetype.id}: leaves {character.stat_points} stat / "
                f"{character.skill_points} skill points unspent; presets must spend "
                f"all {STARTING_STAT_POINTS} and {STARTING_SKILL_POINTS}"
            )


_check_affordable()
