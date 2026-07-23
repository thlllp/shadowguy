"""Tests for cybernetics.py: the cyberware catalog, install/remove, and its
Character.stat()/skill_value wiring (character.py folds installed_bonus/
installed_skill_bonus in alongside worn gear -- see the module docstring)."""

from shadowguy.character import Character
from shadowguy.cybernetics import (
    CYBERWARE_BY_ID,
    CYBERWARE_CATALOG,
    CyberSlot,
    install_cyberware,
    installed_bonus,
    installed_skill_bonus,
    remove_cyberware,
)
from shadowguy.skills import skill_value


def _first_for_slot(slot: CyberSlot, *, with_skill_bonus: bool = False):
    for cyberware in CYBERWARE_CATALOG:
        if cyberware.slot is not slot:
            continue
        if with_skill_bonus and not cyberware.skill_bonuses:
            continue
        if not with_skill_bonus and not cyberware.bonuses:
            continue
        return cyberware
    raise AssertionError(f"no catalog entry for {slot} matching with_skill_bonus={with_skill_bonus}")


def test_every_cyberslot_has_a_catalog_entry():
    assert {cyberware.slot for cyberware in CYBERWARE_CATALOG} == set(CyberSlot)


def test_install_cyberware_succeeds_and_charges_cash():
    cyberware = _first_for_slot(CyberSlot.OPTICS)
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, cyberware.id) is True
    assert character.cash == 10_000 - cyberware.price
    assert character.installed_cyberware[cyberware.slot] == cyberware.id


def test_install_cyberware_fails_when_unaffordable():
    cyberware = _first_for_slot(CyberSlot.OPTICS)
    character = Character(name="t", cash=0)
    assert install_cyberware(character, cyberware.id) is False
    assert character.installed_cyberware == {}


def test_install_cyberware_fails_when_slot_already_occupied():
    first = _first_for_slot(CyberSlot.NEURALWARE)
    second = next(c for c in CYBERWARE_CATALOG if c.slot is CyberSlot.NEURALWARE and c.id != first.id)
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, first.id) is True
    cash_after_first = character.cash
    assert install_cyberware(character, second.id) is False
    assert character.cash == cash_after_first
    assert character.installed_cyberware[CyberSlot.NEURALWARE] == first.id


def test_remove_cyberware_frees_the_slot_for_a_swap():
    first = _first_for_slot(CyberSlot.ARMS)
    second = next(c for c in CYBERWARE_CATALOG if c.slot is CyberSlot.ARMS and c.id != first.id)
    character = Character(name="t", cash=10_000)
    install_cyberware(character, first.id)
    assert remove_cyberware(character, CyberSlot.ARMS) == first.id
    assert CyberSlot.ARMS not in character.installed_cyberware
    assert install_cyberware(character, second.id) is True
    assert character.installed_cyberware[CyberSlot.ARMS] == second.id


def test_remove_cyberware_on_an_empty_slot_returns_none():
    character = Character(name="t")
    assert remove_cyberware(character, CyberSlot.INTERNAL) is None


def test_installed_bonus_sums_across_installed_slots():
    optics = _first_for_slot(CyberSlot.OPTICS)
    internal = _first_for_slot(CyberSlot.INTERNAL)
    stat = next(iter(optics.bonuses))
    installed = {CyberSlot.OPTICS: optics.id, CyberSlot.INTERNAL: internal.id}
    expected = optics.bonuses.get(stat, 0) + internal.bonuses.get(stat, 0)
    assert installed_bonus(installed, stat) == expected


def test_installed_bonus_is_zero_with_nothing_installed():
    assert installed_bonus({}, "body") == 0


def test_installed_skill_bonus_reads_the_right_piece():
    cyberware = _first_for_slot(CyberSlot.OPTICS, with_skill_bonus=True)
    skill_id = next(iter(cyberware.skill_bonuses))
    installed = {CyberSlot.OPTICS: cyberware.id}
    assert installed_skill_bonus(installed, skill_id) == cyberware.skill_bonuses[skill_id]


def test_character_stat_folds_in_installed_cyberware_bonus():
    cyberware = _first_for_slot(CyberSlot.INTERNAL)
    stat = next(iter(cyberware.bonuses))
    character = Character(name="t", cash=10_000)
    before = character.stat(stat)
    install_cyberware(character, cyberware.id)
    assert character.stat(stat) == before + cyberware.bonuses[stat]


def test_character_skill_value_folds_in_installed_cyberware_skill_bonus():
    cyberware = _first_for_slot(CyberSlot.ARMS, with_skill_bonus=True)
    skill_id = next(iter(cyberware.skill_bonuses))
    character = Character(name="t", cash=10_000)
    before = skill_value(character, skill_id)
    install_cyberware(character, cyberware.id)
    assert skill_value(character, skill_id) == before + cyberware.skill_bonuses[skill_id]


def test_cyberware_ids_are_unique():
    assert len(CYBERWARE_BY_ID) == len(CYBERWARE_CATALOG)
