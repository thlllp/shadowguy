"""Skills: the finer-grained checks that scenes actually roll against.

A Choice names a skill, never a raw stat (see scene.Choice). The skill's
effective value (skill_value) is its tied core stat — character.stat(), which
already folds in gear and chem bonuses — plus the character's invested rank in
that specific skill, plus any gear bonus aimed at that specific skill alone
(shops.Item.skill_bonuses, e.g. Slippers' Stealth). Ranks are spent from
Character.skill_points, a fixed pool granted at character creation for now; a
future XP system will grant more over the course of a run.

This module is deliberately a leaf: it imports nothing from the package at
runtime, because character.py -> shops.py -> corpmap.py all end up importing
it. That's why the "every Skill.stat is a real core stat" guard lives in
character.py (which owns CORE_STATS) rather than here.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shadowguy.character import Character


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    stat: str  # which core stat (character.CORE_STATS) this skill is layered on
    description: str


# core stat -> (id, name, description). The key is the tied stat, so a skill
# can't be filed under one stat and claim another.
_SKILL_ROWS: dict[str, list[tuple[str, str, str]]] = {
    "body": [
        ("resist_poison", "Resist Poison", "Shrugging off toxins"),
        ("resist_disease", "Resist Disease", "Fighting off infection"),
        ("center_of_gravity", "Center of Gravity", "Staying upright and balanced"),
        ("lung_capacity", "Lung Capacity", "Holding your breath, enduring exertion"),
        ("toughness", "Toughness", "Shrugging off blows"),
    ],
    "strength": [
        ("short_blade", "Short Blade", "Fighting with knives and short blades"),
        ("long_blade", "Long Blade", "Fighting with swords and long blades"),
        ("blunt", "Blunt", "Fighting with clubs and blunt weapons"),
        ("grapple", "Grapple", "Wrestling, restraining, breaking holds"),
        ("lift", "Lift", "Lifting, hauling, and forcing objects"),
    ],
    "agility": [
        ("stealth", "Stealth", "Ability to move unseen"),
        ("dodge", "Dodge", "Ability to avoid attacks"),
        ("acrobatics", "Acrobatics", "Jumping, climbing"),
        ("infiltration", "Infiltration", "Locks"),
        ("sleight_of_hand", "Sleight of Hand", "Concealing weapons and pickpocketing"),
    ],
    "perception": [
        ("read_face", "Read Face", "Reading expressions and body language"),
        ("pattern_seeking", "Pattern Seeking", "Spotting patterns and anomalies"),
        ("listening", "Listening", "Picking up sounds and conversations"),
        ("sight", "Sight", "Spotting details at range or in the dark"),
        ("read_the_room", "Read the Room", "Sensing social undercurrents and mood"),
        # Filed under perception, not strength: a gun is aimed, not swung, so what
        # it rolls is the same faculty as Sight. This is the one stat with six
        # skills — nothing enforces five, and a stat's cost is per-skill anyway,
        # so a sixth makes perception broader, not stronger. The seventh (misc)
        # follows the same rule: exotic and improvised weapons are aimed, too.
        ("firearms", "Firearms", "Shooting straight under pressure"),
        ("misc", "Misc Weapons", "Exotic and improvised weapons"),
    ],
    "intelligence": [
        ("hack", "Hack", "Breaking into networks and systems"),
        ("recon", "Recon", "Gathering intel and casing a target"),
        ("infer", "Infer", "Understanding new systems and interfaces"),
        ("tactics", "Tactics", "Planning and reading a fight"),
        ("tinkering", "Tinkering", "Repairing and modifying hardware"),
    ],
    "cool": [
        ("forgery", "Forgery", "Faking documents and IDs"),
        ("deception", "Deception", "Lying convincingly"),
        ("leadership", "Leadership", "Rallying people to your crew and holding their loyalty"),
        ("negotiations", "Negotiations", "Striking favorable deals"),
        ("intimidation", "Intimidation", "Coercing through fear"),
    ],
}

SKILLS: list[Skill] = [
    Skill(id=skill_id, name=name, stat=stat, description=description)
    for stat, rows in _SKILL_ROWS.items()
    for skill_id, name, description in rows
]
SKILLS_BY_ID = {skill.id: skill for skill in SKILLS}

# A duplicate id would silently collapse SKILLS_BY_ID and hand two rows of the
# skills screen the same Textual widget id (DuplicateIds on mount).
if len(SKILLS_BY_ID) != len(SKILLS):
    raise ValueError("skill ids must be unique across _SKILL_ROWS")


def skill_for(skill_id: str) -> Skill:
    """The Skill with this id. The one place an unknown skill id is caught."""
    try:
        return SKILLS_BY_ID[skill_id]
    except KeyError:
        raise ValueError(f"unknown skill: {skill_id!r}") from None


def skill_value(character: "Character", skill_id: str) -> int:
    skill = skill_for(skill_id)
    return (
        character.stat(skill.stat)
        + character.skill_rank(skill_id)
        + character.skill_gear_bonus(skill_id)
    )
