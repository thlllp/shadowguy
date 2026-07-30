"""Tests for skills.py: the skill table, skill_for/skill_value, leaf-module guarantee."""

import ast
import pathlib

import pytest

from shadowguy.character import CORE_STATS, Character
from shadowguy.skills import SKILLS, SKILLS_BY_ID, skill_for, skill_value


def test_33_skills_total():
    assert len(SKILLS) == 33


def test_skill_ids_unique():
    assert len(SKILLS_BY_ID) == len(SKILLS)


def test_every_skill_stat_is_a_core_stat():
    assert all(skill.stat in CORE_STATS for skill in SKILLS)


def test_agility_carries_every_weapon_skill():
    """Every weapon a Slot.WEAPON item can roll is an agility skill -- handling the
    weapon, not the muscle behind it or the eye down the sight."""
    agility_skills = {s.id for s in SKILLS if s.stat == "agility"}
    assert {
        "pistols", "automatics", "longarms", "clubs",
        "blades", "archery", "throwing", "gunnery",
    } <= agility_skills


def test_perception_carries_four_skills():
    """Read Face/Read the Room folded into Intuition, and the weapon skills that used
    to sit here moved to agility."""
    assert len([s for s in SKILLS if s.stat == "perception"]) == 4


def test_skill_for_known_id_returns_skill():
    skill = skill_for("hack")
    assert skill.id == "hack"
    assert skill.stat == "logic"


def test_skill_for_unknown_id_raises_value_error():
    with pytest.raises(ValueError):
        skill_for("not_a_skill")


def test_skill_value_combines_stat_rank_and_gear():
    c = Character(name="t", logic=3)
    # skill_rank defaults to STARTING_SKILL_RANK (1), no gear equipped.
    assert skill_value(c, "hack") == c.stat("logic") + c.skill_rank("hack")


def test_skill_value_rises_with_invested_rank():
    c = Character(name="t")
    before = skill_value(c, "hack")
    c.spend_skill_point("hack")
    assert skill_value(c, "hack") == before + 1


def test_skills_module_imports_nothing_from_the_package_at_runtime():
    """skills.py must stay a leaf: character -> shops -> corpmap all import it, so a
    runtime import back into the package would be a cycle."""
    source = pathlib.Path(__import__("shadowguy.skills", fromlist=["_"]).__file__).read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("shadowguy"):
            # TYPE_CHECKING-guarded imports are fine; only flag runtime ones.
            parent_ifs = [
                n for n in ast.walk(tree)
                if isinstance(n, ast.If)
                and getattr(n.test, "id", None) == "TYPE_CHECKING"
                and node in ast.walk(n)
            ]
            assert parent_ifs, f"runtime import of {node.module} would create a cycle"
