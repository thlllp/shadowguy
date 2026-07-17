"""Tests for skills.py: the skill table, skill_for/skill_value, leaf-module guarantee."""

import ast
import pathlib

import pytest

from shadowguy.character import CORE_STATS, Character
from shadowguy.skills import SKILLS, SKILLS_BY_ID, skill_for, skill_value


def test_32_skills_total():
    assert len(SKILLS) == 32


def test_skill_ids_unique():
    assert len(SKILLS_BY_ID) == len(SKILLS)


def test_every_skill_stat_is_a_core_stat():
    assert all(skill.stat in CORE_STATS for skill in SKILLS)


def test_perception_carries_seven_skills():
    """Perception is the one stat with more than five: the base five plus Firearms
    and Misc Weapons (a gun/exotic weapon is aimed, so it rolls the same faculty)."""
    perception_skills = [s for s in SKILLS if s.stat == "perception"]
    assert len(perception_skills) == 7
    assert {"firearms", "misc"} <= {s.id for s in perception_skills}


def test_skill_for_known_id_returns_skill():
    skill = skill_for("hack")
    assert skill.id == "hack"
    assert skill.stat == "intelligence"


def test_skill_for_unknown_id_raises_value_error():
    with pytest.raises(ValueError):
        skill_for("not_a_skill")


def test_skill_value_combines_stat_rank_and_gear():
    c = Character(name="t", intelligence=3)
    # skill_rank defaults to STARTING_SKILL_RANK (1), no gear equipped.
    assert skill_value(c, "hack") == c.stat("intelligence") + c.skill_rank("hack")


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
