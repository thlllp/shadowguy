"""Tests for cybernetics.py: the cyberware catalog, install/remove, and its
Character.stat()/skill_value wiring (character.py folds installed_bonus/
installed_skill_bonus in alongside worn gear -- see the module docstring)."""

from shadowguy.character import HUMANITY_BASELINE, Character
from shadowguy.cybernetics import (
    CYBERWARE_BY_ID,
    CYBERWARE_CATALOG,
    SMARTLINK_ID,
    VALID_CYBERWARE_TIERS,
    CyberSlot,
    free_humanity,
    has_smartlink,
    install_cyberware,
    installed_bonus,
    installed_defense,
    installed_humanity_cost,
    installed_matrix_action_bonus,
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
    cyberware = _first_for_slot(CyberSlot.ARMS, with_skill_bonus=True)
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


def test_free_humanity_starts_at_the_baseline_with_nothing_installed():
    character = Character(name="t")
    assert character.humanity == HUMANITY_BASELINE
    assert free_humanity(character) == HUMANITY_BASELINE


def test_installing_cyberware_spends_free_humanity():
    cyberware = _first_for_slot(CyberSlot.OPTICS)
    character = Character(name="t", cash=10_000)
    install_cyberware(character, cyberware.id)
    assert installed_humanity_cost(character.installed_cyberware) == cyberware.humanity_cost
    assert free_humanity(character) == HUMANITY_BASELINE - cyberware.humanity_cost


def test_install_cyberware_fails_when_it_would_exceed_humanity_capacity():
    character = Character(name="t", cash=100_000, humanity=1)
    expensive = next(c for c in CYBERWARE_CATALOG if c.humanity_cost > 1)
    assert install_cyberware(character, expensive.id) is False
    assert character.installed_cyberware == {}
    assert character.cash == 100_000


def test_install_cyberware_succeeds_exactly_at_remaining_capacity():
    cyberware = _first_for_slot(CyberSlot.INTERNAL)
    character = Character(name="t", cash=10_000, humanity=cyberware.humanity_cost)
    assert install_cyberware(character, cyberware.id) is True
    assert free_humanity(character) == 0


def test_removing_cyberware_frees_its_humanity_cost():
    cyberware = _first_for_slot(CyberSlot.ARMS)
    character = Character(name="t", cash=10_000)
    install_cyberware(character, cyberware.id)
    remove_cyberware(character, CyberSlot.ARMS)
    assert free_humanity(character) == HUMANITY_BASELINE


def test_smartlink_costs_half_a_point_of_humanity():
    assert CYBERWARE_BY_ID[SMARTLINK_ID].humanity_cost == 0.5


def test_installing_smartlink_leaves_a_fractional_remainder():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, SMARTLINK_ID)
    assert free_humanity(character) == HUMANITY_BASELINE - 0.5


def test_has_smartlink_false_with_nothing_installed():
    assert has_smartlink({}) is False


def test_has_smartlink_false_with_a_different_optics_piece():
    assert has_smartlink({CyberSlot.OPTICS: "cybereye_scanner"}) is False


def test_has_smartlink_true_once_installed():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, SMARTLINK_ID)
    assert has_smartlink(character.installed_cyberware) is True


# --- tiers ---


def test_every_catalog_entry_has_a_valid_tier():
    assert {c.tier for c in CYBERWARE_CATALOG} <= set(VALID_CYBERWARE_TIERS)


def test_every_tier_1_piece_has_a_tier_2_3_and_4_variant():
    tier_1 = [c for c in CYBERWARE_CATALOG if c.tier == 1]
    for base in tier_1:
        for tier in (2, 3, 4):
            assert f"{base.id}_t{tier}" in CYBERWARE_BY_ID


def test_higher_tier_keeps_the_same_effect_as_tier_1():
    base = CYBERWARE_BY_ID["reflex_coprocessor"]
    for tier in (2, 3, 4):
        variant = CYBERWARE_BY_ID[f"{base.id}_t{tier}"]
        assert variant.slot is base.slot
        assert variant.bonuses == base.bonuses
        assert variant.skill_bonuses == base.skill_bonuses
        assert variant.tier == tier


def test_tier_2_is_the_same_price_and_10_percent_less_humanity():
    base = CYBERWARE_BY_ID["neural_processor"]
    tier_2 = CYBERWARE_BY_ID["neural_processor_t2"]
    assert tier_2.price == base.price
    assert tier_2.humanity_cost == round(base.humanity_cost * 0.9, 2)


def test_tier_3_is_25_percent_cheaper_and_10_percent_more_humanity():
    base = CYBERWARE_BY_ID["neural_processor"]
    tier_3 = CYBERWARE_BY_ID["neural_processor_t3"]
    assert tier_3.price == round(base.price * 0.75)
    assert tier_3.humanity_cost == round(base.humanity_cost * 1.10, 2)


def test_tier_4_is_50_percent_cheaper_and_60_percent_more_humanity():
    base = CYBERWARE_BY_ID["neural_processor"]
    tier_4 = CYBERWARE_BY_ID["neural_processor_t4"]
    assert tier_4.price == round(base.price * 0.5)
    assert tier_4.humanity_cost == round(base.humanity_cost * 1.6, 2)


def test_every_tier_smartlink_still_grants_smartlink():
    for tier in (2, 3, 4):
        assert CYBERWARE_BY_ID[f"smartlink_t{tier}"].grants_smartlink is True


def test_has_smartlink_true_for_a_higher_tier_smartlink():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, "smartlink_t4")
    assert has_smartlink(character.installed_cyberware) is True


def test_install_cyberware_works_with_a_tier_4_id():
    character = Character(name="t", cash=10_000)
    variant = CYBERWARE_BY_ID["cybereye_scanner_t4"]
    assert install_cyberware(character, variant.id) is True
    assert character.cash == 10_000 - variant.price
    assert character.installed_cyberware[CyberSlot.OPTICS] == variant.id


# --- bone lacing (defense) ---


def test_bone_lacing_catalog_values():
    steel = CYBERWARE_BY_ID["steel_bones"]
    titanium = CYBERWARE_BY_ID["titanium_bones"]
    adamantium = CYBERWARE_BY_ID["adamantium_bones"]
    assert (steel.price, steel.defense, steel.humanity_cost) == (1000, 1, 1)
    assert (titanium.price, titanium.defense, titanium.humanity_cost) == (3000, 2, 2)
    assert (adamantium.price, adamantium.defense, adamantium.humanity_cost) == (6000, 4, 4)
    assert steel.slot is CyberSlot.INTERNAL
    assert titanium.slot is CyberSlot.INTERNAL
    assert adamantium.slot is CyberSlot.INTERNAL


def test_bone_lacing_gets_generated_tier_variants_too():
    for base_id in ("steel_bones", "titanium_bones", "adamantium_bones"):
        for tier in (2, 3, 4):
            variant = CYBERWARE_BY_ID[f"{base_id}_t{tier}"]
            assert variant.defense == CYBERWARE_BY_ID[base_id].defense
            assert variant.tier == tier


def test_installed_defense_sums_across_installed_slots():
    assert installed_defense({CyberSlot.INTERNAL: "titanium_bones"}) == 2


def test_installed_defense_is_zero_with_nothing_installed():
    assert installed_defense({}) == 0


def test_installing_bone_lacing_competes_with_other_internal_pieces():
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, "subdermal_plating") is True
    assert install_cyberware(character, "steel_bones") is False
    assert character.installed_cyberware[CyberSlot.INTERNAL] == "subdermal_plating"


# --- datajack ---


def test_datajack_catalog_values():
    datajack = CYBERWARE_BY_ID["datajack"]
    assert (datajack.price, datajack.humanity_cost) == (1000, 0.5)
    assert datajack.slot is CyberSlot.NEURALWARE


def test_datajack_has_no_stat_or_skill_bonus_or_defense():
    datajack = CYBERWARE_BY_ID["datajack"]
    assert datajack.bonuses == {}
    assert datajack.skill_bonuses == {}
    assert datajack.defense == 0
    assert datajack.grants_smartlink is False


def test_datajack_grants_a_small_matrix_action_bonus():
    assert CYBERWARE_BY_ID["datajack"].matrix_action_bonus == 1


def test_installed_matrix_action_bonus_sums_across_installed_slots():
    assert installed_matrix_action_bonus({CyberSlot.NEURALWARE: "datajack"}) == 1


def test_installed_matrix_action_bonus_is_zero_with_nothing_installed():
    assert installed_matrix_action_bonus({}) == 0


def test_installed_matrix_action_bonus_zero_for_a_different_neuralware_piece():
    assert installed_matrix_action_bonus({CyberSlot.NEURALWARE: "neural_processor"}) == 0


def test_install_datajack_succeeds_and_spends_humanity():
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, "datajack") is True
    assert character.cash == 10_000 - 1000
    assert free_humanity(character) == HUMANITY_BASELINE - 0.5
