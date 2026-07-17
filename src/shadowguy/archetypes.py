"""Preset builds for fast character creation.

An Archetype is just a canned allocation of the same 6 stat points and 20 skill
points the player would otherwise spend by hand on CharacterCreationScreen.
`apply()` spends them through Character.spend_stat_point/spend_skill_point rather
than assigning fields, so a preset is subject to the rank cap and the rank-cost
curve exactly like a hand-built runner — it cannot buy something the player
couldn't. Validation is deferred to first access of ARCHETYPES / ARCHETYPES_BY_ID,
so an unaffordable preset still fails early (the creation screen is the first thing
the game uses) but importing the module alone doesn't construct a Character.

(Not to be confused with jobs.JobArchetype, which is a template for a *job*.)
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shadowguy.character import Character


@dataclass(frozen=True)
class Archetype:
    id: str
    name: str
    description: str
    stats: dict[str, int]
    skills: dict[str, int]

    def apply(self, character: "Character") -> None:
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

# Lazy init state: filled on first access by __getattr__ below.
_ARCHETYPES: list[Archetype] | None = None
_ARCHETYPES_BY_ID: dict[str, Archetype] | None = None


def _validate_preset(archetype: Archetype) -> None:
    from shadowguy.character import Character
    character = Character(name="_check")
    archetype.apply(character)
    if character.stat_points or character.skill_points:
        from shadowguy.character import STARTING_SKILL_POINTS, STARTING_STAT_POINTS
        raise ValueError(
            f"{archetype.id}: leaves {character.stat_points} stat / "
            f"{character.skill_points} skill points unspent; presets must spend "
            f"all {STARTING_STAT_POINTS} and {STARTING_SKILL_POINTS}"
        )


def _init() -> None:
    if _ARCHETYPES is not None:
        return
    import sys
    archetypes_list = [
        Archetype(id=id_, name=name, description=description, stats=stats, skills=skills)
        for id_, name, description, stats, skills in _ARCHETYPE_ROWS
    ]
    for archetype in archetypes_list:
        _validate_preset(archetype)
    # Use sys.modules to avoid the global statement with linting friction.
    mod = sys.modules[__name__]
    mod._ARCHETYPES = archetypes_list
    mod._ARCHETYPES_BY_ID = {archetype.id: archetype for archetype in archetypes_list}


def __getattr__(name: str):
    if name == "ARCHETYPES":
        _init()
        return _ARCHETYPES
    if name == "ARCHETYPES_BY_ID":
        _init()
        return _ARCHETYPES_BY_ID
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
