"""Tests for archetypes lazy-init and preset validation."""

import subprocess
import sys

import shadowguy.archetypes as archetypes
from shadowguy.character import Character


def test_lazy_init_no_side_effect_on_import():
    """Importing the module -- even the whole app -- must not construct ARCHETYPES.

    Checked in a fresh subprocess: within this test process, some other test may
    have already legitimately driven CharacterCreationScreen (a real access, not an
    import side effect), which would otherwise taint _ARCHETYPES for the rest of
    the run regardless of test order.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import shadowguy.app; import shadowguy.archetypes as a; "
            "assert a._ARCHETYPES is None, a._ARCHETYPES",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_lazy_init_populates_on_access():
    archetypes._ARCHETYPES = None
    archetypes._ARCHETYPES_BY_ID = None
    _ = archetypes.ARCHETYPES
    assert archetypes._ARCHETYPES is not None
    assert len(archetypes._ARCHETYPES) == 3


def test_archetypes_count():
    assert len(archetypes.ARCHETYPES) == 3


def test_archetypes_by_id_count():
    assert len(archetypes.ARCHETYPES_BY_ID) == 3


def test_archetypes_by_id_keys_match():
    for a in archetypes.ARCHETYPES:
        assert archetypes.ARCHETYPES_BY_ID[a.id] is a


def test_enforcer():
    a = archetypes.ARCHETYPES_BY_ID["enforcer"]
    assert a.name == "Enforcer"
    assert a.stats == {"body": 3, "strength": 3}
    assert a.skills == {"grapple": 7, "toughness": 6, "negotiations": 4, "read_the_room": 2}


def test_hacker():
    a = archetypes.ARCHETYPES_BY_ID["hacker"]
    assert a.name == "Hacker"
    assert a.stats == {"intelligence": 4, "perception": 2}
    assert a.skills == {"hack": 7, "tinkering": 5, "infer": 4, "pattern_seeking": 4}


def test_infiltrator():
    a = archetypes.ARCHETYPES_BY_ID["infiltrator"]
    assert a.name == "Infiltrator"
    assert a.stats == {"agility": 4, "perception": 2}
    assert a.skills == {"stealth": 7, "deception": 5, "sight": 4, "read_the_room": 4}


def test_apply_spends_all_points():
    for a in archetypes.ARCHETYPES:
        c = Character(name="test")
        a.apply(c)
        assert c.stat_points == 0
        assert c.skill_points == 0


def test_apply_sets_correct_stats():
    a = archetypes.ARCHETYPES_BY_ID["enforcer"]
    c = Character(name="test")
    a.apply(c)
    assert c.stat("body") == 1 + 3
    assert c.stat("strength") == 1 + 3


def test_apply_sets_correct_skill_ranks():
    a = archetypes.ARCHETYPES_BY_ID["hacker"]
    c = Character(name="test")
    a.apply(c)
    assert c.skill_rank("hack") == 7
    assert c.skill_rank("tinkering") == 5


def test_bad_preset_raises():
    from shadowguy.archetypes import Archetype

    bad = Archetype(
        id="bad", name="Bad", description="over budget",
        stats={"body": 7}, skills={"toughness": 10},
    )
    c = Character(name="test")
    import pytest
    with pytest.raises(ValueError, match="ran out of stat points"):
        bad.apply(c)
