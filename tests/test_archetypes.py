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
    assert len(archetypes._ARCHETYPES) == 5


def test_archetypes_count():
    assert len(archetypes.ARCHETYPES) == 5


def test_archetypes_by_id_count():
    assert len(archetypes.ARCHETYPES_BY_ID) == 5


def test_archetypes_by_id_keys_match():
    for a in archetypes.ARCHETYPES:
        assert archetypes.ARCHETYPES_BY_ID[a.id] is a


def test_enforcer():
    a = archetypes.ARCHETYPES_BY_ID["enforcer"]
    assert a.name == "Enforcer"
    assert a.stats == {"body": 3, "strength": 3}
    assert a.skills == {"clubs": 7, "toughness": 6, "grapple": 4, "intimidation": 1}


def test_hacker():
    a = archetypes.ARCHETYPES_BY_ID["hacker"]
    assert a.name == "Hacker"
    assert a.stats == {"logic": 6}
    assert a.skills == {"cybercombat": 6, "hack": 5, "computer": 4, "infer": 4, "tinkering": 2}


def test_infiltrator():
    a = archetypes.ARCHETYPES_BY_ID["infiltrator"]
    assert a.name == "Infiltrator"
    assert a.stats == {"agility": 4, "perception": 2}
    assert a.skills == {"stealth": 7, "deception": 5, "sight": 3, "blades": 4}


def test_gunslinger():
    a = archetypes.ARCHETYPES_BY_ID["gunslinger"]
    assert a.name == "Gunslinger"
    assert a.stats == {"agility": 3, "body": 3}
    assert a.skills == {"longarms": 7, "dodge": 5, "toughness": 4, "pistols": 3}


def test_fixer():
    a = archetypes.ARCHETYPES_BY_ID["fixer"]
    assert a.name == "Fixer"
    assert a.stats == {"cool": 4, "logic": 2}
    assert a.skills == {"negotiations": 7, "leadership": 5, "deception": 4, "computer": 3}


def test_no_preset_raises_a_stat_it_never_rolls():
    """Every stat a preset buys, not just its primary. Nothing rolls a core stat
    directly, so a stat raised for skills the preset doesn't own is wasted outright --
    which is how the Hacker's perception and the Fixer's logic points shipped dead."""
    from shadowguy.skills import skill_for
    for a in archetypes.ARCHETYPES:
        rolled = {skill_for(s).stat for s in a.skills}
        assert not set(a.stats) - rolled, f"{a.id}: {sorted(set(a.stats) - rolled)} buys nothing"


def test_validate_preset_rejects_a_stat_the_skills_never_roll():
    """The guard itself, on a preset that spends both pools exactly but puts 3 points
    into a stat none of its skills sit on."""
    import pytest
    from shadowguy.archetypes import Archetype
    idle_stat = Archetype(
        id="idle", name="Idle", description="",
        stats={"body": 3, "cool": 3},
        skills={"toughness": 7, "grapple": 6, "lift": 4, "running": 2},
    )
    with pytest.raises(ValueError, match="buys no skill layered on"):
        archetypes._validate_preset(idle_stat)


def test_the_hacker_clears_the_matrix_readiness_bar():
    """The preset for the matrix must not trip the matrix's own "you aren't ready"
    warning -- it rolls Cybercombat now, not Hack (see matrix.MIN_READY_CYBERCOMBAT)."""
    from shadowguy.matrix import ATTACK_SKILL, MIN_READY_CYBERCOMBAT
    from shadowguy.skills import skill_value
    c = Character(name="test")
    archetypes.ARCHETYPES_BY_ID["hacker"].apply(c)
    assert skill_value(c, ATTACK_SKILL) >= MIN_READY_CYBERCOMBAT


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
    assert c.skill_rank("cybercombat") == 6
    assert c.skill_rank("hack") == 5


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


# --- creation gear (character.convert_skill_point_to_gear) ---


def test_every_preset_ships_a_loadout_it_can_actually_afford():
    """A preset is the whole build, gear included -- the rank lists leave exactly the
    points the kit costs, and apply() converts and buys them."""
    from shadowguy.character import GEAR_EB_PER_POINT, Character
    from shadowguy.shops import ITEMS_BY_ID

    for a in archetypes.ARCHETYPES:
        assert a.gear, f"{a.id}: ships no gear"
        character = Character(name="_gear")
        a.apply(character)
        bill = sum(ITEMS_BY_ID[i].price for i in a.gear)
        assert [e.item_id for e in character.inventory] == list(a.gear)
        assert character.creation_gear == list(a.gear)
        # One point's worth, and the leftover is under a point -- a preset that burned a
        # whole point on nothing would be paying for air.
        assert bill <= GEAR_EB_PER_POINT
        assert character.gear_budget < GEAR_EB_PER_POINT


def test_no_preset_ships_gear_a_fresh_runner_could_not_buy():
    """Standing-gated gear is unreachable before the run starts -- there is nobody to
    have standing with -- so a preset naming one would be a build the player can't make."""
    from shadowguy.shops import ITEMS_BY_ID

    for a in archetypes.ARCHETYPES:
        for item_id in a.gear:
            assert item_id in ITEMS_BY_ID, f"{a.id}: unknown item {item_id}"
            assert not ITEMS_BY_ID[item_id].min_standing, f"{a.id}: {item_id} is gated"


def test_every_preset_still_spends_both_pools_to_zero_with_gear_in_the_mix():
    from shadowguy.character import Character

    for a in archetypes.ARCHETYPES:
        character = Character(name="_pools")
        a.apply(character)
        assert character.stat_points == 0, a.id
        assert character.skill_points == 0, a.id
