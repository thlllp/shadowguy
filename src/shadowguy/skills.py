"""Skills: finer-grained checks layered on a core stat, with an investable rank.

A skill's effective value (skill_value) is its tied stat (character.stat(),
already including gear/temp bonuses) plus the character's rank in that
specific skill. Ranks are spent from Character.skill_points, a fixed pool
granted at character creation for now — a future XP system will grant more
over the course of a run.
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


AGILITY_SKILLS = [
    Skill(id="stealth", name="Stealth", stat="agility", description="Ability to move unseen"),
    Skill(id="dodge", name="Dodge", stat="agility", description="Ability to avoid attacks"),
    Skill(id="acrobatics", name="Acrobatics", stat="agility", description="Jumping, climbing"),
    Skill(id="infiltration", name="Infiltration", stat="agility", description="Locks"),
    Skill(
        id="sleight_of_hand",
        name="Sleight of Hand",
        stat="agility",
        description="Concealing weapons and pickpocketing",
    ),
]

COOL_SKILLS = [
    Skill(id="forgery", name="Forgery", stat="cool", description="Faking documents and IDs"),
    Skill(id="deception", name="Deception", stat="cool", description="Lying convincingly"),
    Skill(
        id="seduction",
        name="Seduction",
        stat="cool",
        description="Charming or manipulating through attraction",
    ),
    Skill(id="negotiations", name="Negotiations", stat="cool", description="Striking favorable deals"),
    Skill(id="intimidation", name="Intimidation", stat="cool", description="Coercing through fear"),
]

PERCEPTION_SKILLS = [
    Skill(
        id="read_face",
        name="Read Face",
        stat="perception",
        description="Reading expressions and body language",
    ),
    Skill(
        id="pattern_seeking",
        name="Pattern Seeking",
        stat="perception",
        description="Spotting patterns and anomalies",
    ),
    Skill(id="listening", name="Listening", stat="perception", description="Picking up sounds and conversations"),
    Skill(id="sight", name="Sight", stat="perception", description="Spotting details at range or in the dark"),
    Skill(
        id="read_the_room",
        name="Read the Room",
        stat="perception",
        description="Sensing social undercurrents and mood",
    ),
]

BODY_SKILLS = [
    Skill(id="resist_poison", name="Resist Poison", stat="body", description="Shrugging off toxins"),
    Skill(id="resist_disease", name="Resist Disease", stat="body", description="Fighting off infection"),
    Skill(
        id="center_of_gravity",
        name="Center of Gravity",
        stat="body",
        description="Staying upright and balanced",
    ),
    Skill(id="lung_capacity", name="Lung Capacity", stat="body", description="Holding your breath, enduring exertion"),
    Skill(id="toughness", name="Toughness", stat="body", description="Shrugging off blows"),
]

STRENGTH_SKILLS = [
    Skill(id="short_blade", name="Short Blade", stat="strength", description="Fighting with knives and short blades"),
    Skill(id="long_blade", name="Long Blade", stat="strength", description="Fighting with swords and long blades"),
    Skill(id="blunt", name="Blunt", stat="strength", description="Fighting with clubs and blunt weapons"),
    Skill(id="grapple", name="Grapple", stat="strength", description="Wrestling, restraining, breaking holds"),
    Skill(id="lift", name="Lift", stat="strength", description="Lifting, hauling, and forcing objects"),
]

INTELLIGENCE_SKILLS = [
    Skill(id="hack", name="Hack", stat="intelligence", description="Breaking into networks and systems"),
    Skill(id="recon", name="Recon", stat="intelligence", description="Gathering intel and casing a target"),
    Skill(
        id="infer",
        name="Infer",
        stat="intelligence",
        description="Understanding new systems and interfaces",
    ),
    Skill(id="tactics", name="Tactics", stat="intelligence", description="Planning and reading a fight"),
    Skill(id="tinkering", name="Tinkering", stat="intelligence", description="Repairing and modifying hardware"),
]

SKILLS: list[Skill] = [
    *AGILITY_SKILLS,
    *COOL_SKILLS,
    *PERCEPTION_SKILLS,
    *BODY_SKILLS,
    *STRENGTH_SKILLS,
    *INTELLIGENCE_SKILLS,
]
SKILLS_BY_ID = {skill.id: skill for skill in SKILLS}


def skill_value(character: "Character", skill_id: str) -> int:
    skill = SKILLS_BY_ID[skill_id]
    return character.stat(skill.stat) + character.skill_rank(skill_id)
