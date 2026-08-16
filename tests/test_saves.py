"""restore_defaults: the __setstate__ helper that lets a purely-additive dataclass
field backfill its default on an old pickle rather than needing a SAVE_VERSION bump.
See saves.py's module docstring and CLAUDE.md's Save versions section."""

import pickle

from shadowguy.corpmap import Territory
from shadowguy.matrix import Ice
from shadowguy.runners import RivalRunner
from shadowguy.saves import restore_defaults


def _drop(instance, *field_names):
    """Simulate an old pickle written before `field_names` existed on this class."""
    for name in field_names:
        del instance.__dict__[name]
    return pickle.loads(pickle.dumps(instance))


def test_territory_backfills_fields_missing_from_an_old_pickle():
    territory = Territory(id="x", name="X", x=0, y=0)
    restored = _drop(territory, "is_slum", "is_outskirts", "garrison")
    assert restored.is_slum is False
    assert restored.is_outskirts is False
    assert restored.garrison == 0


def test_territory_keeps_a_value_actually_present_in_the_pickle():
    territory = Territory(id="x", name="X", x=0, y=0, garrison=3, is_slum=True)
    restored = pickle.loads(pickle.dumps(territory))
    assert restored.garrison == 3
    assert restored.is_slum is True


def test_ice_backfills_human():
    ice = Ice(id="i", name="I", integrity=1, attack=1, defense=1, damage=1, soak=1)
    restored = _drop(ice, "human")
    assert restored.human is False


def test_rival_runner_backfills_progression_fields():
    runner = RivalRunner(
        id="r", name="R", archetype="Solo", description="", rating=1, daily_cost=1, job_cut=0.1
    )
    restored = _drop(runner, "deck_id", "experience", "cash", "gear")
    assert restored.deck_id is None
    assert restored.experience == 0
    assert restored.cash == 0
    assert restored.gear == []


def test_corp_map_backfills_faction_grudge(corp_map):
    restored = _drop(corp_map, "faction_grudge")
    assert restored.faction_grudge == {}
    # territories/relations were actually in the pickle, not backfilled
    assert restored.territories.keys() == corp_map.territories.keys()


def test_restore_defaults_does_not_invent_a_field_the_class_no_longer_has():
    """A stale key in `state` that the current class dropped is left alone rather
    than raising — restore_defaults only ever *adds* missing current fields."""
    territory = Territory(id="x", name="X", x=0, y=0)
    state = dict(territory.__dict__)
    state["stale_field_from_an_old_version"] = 1
    restore_defaults(territory, state)
    assert territory.__dict__["stale_field_from_an_old_version"] == 1
    assert territory.garrison == 0
