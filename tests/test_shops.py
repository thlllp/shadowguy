"""Tests for shops.py: standing-scaled pricing, catalogs, and buy/sell transactions."""

from pathlib import Path

import pytest

from shadowguy.character import Character
from shadowguy.checks import CRITICAL_MARGIN, pool_for_difficulty
from shadowguy.shops import (
    AMMO_BY_KIND,
    AmmoKind,
    CATALOG,
    CRAFT_RECIPES,
    ITEMS_BY_ID,
    MOD_CATALOG,
    MOD_SLOTS_PER_ITEM,
    MODS_BY_ID,
    PAWN_SELL_FRACTION,
    PROGRAMS_BY_ID,
    SCAVENGE_CRITICAL_FINDS,
    SCAVENGE_DIFFICULTY,
    SCAVENGE_MATERIALS,
    STANDING_PRICE_CAP,
    STANDING_PRICE_STEP,
    STOCK_MOD_IDS,
    RANGED_SKILLS,
    STOLEN_DATASHARD_ID,
    WEAPON_MOD_SLOTS,
    WORKSHOP_ARMORER_DIFFICULTY,
    WORKSHOP_CHEMISTRY_DIFFICULTY,
    InventoryItem,
    Slot,
    WeaponModSlot,
    bonus_text,
    buy_ammo,
    buy_consumable,
    buy_item,
    buy_price,
    buy_program,
    craft_consumable,
    effective_item,
    grant_item,
    install_mod,
    install_refusal,
    loaded_rounds,
    remove_mod,
    scavenge,
    sell_item,
    sell_price,
    slot_usage,
)
from shadowguy.combat import has_ammo, spend_round
from shadowguy.inventory import reload_weapon
from shadowguy.shops import CONSUMABLES_BY_ID

from helpers import AlwaysOne, AlwaysSix, character_with_skill_value


def test_buy_price_neutral_standing_is_base_price():
    assert buy_price(100, 0) == 100


def test_buy_price_positive_standing_discounts():
    assert buy_price(100, 5) < 100


def test_buy_price_negative_standing_marks_up():
    assert buy_price(100, -5) > 100


def test_buy_price_never_below_one():
    assert buy_price(1, 1000) >= 1


def test_buy_price_discount_caps_at_standing_price_cap():
    huge_standing = int(STANDING_PRICE_CAP / STANDING_PRICE_STEP) + 100
    capped = buy_price(1000, huge_standing)
    at_cap = round(1000 * (1 - STANDING_PRICE_CAP))
    assert capped == at_cap


def test_sell_price_is_pawn_fraction_of_base_at_neutral_standing():
    assert sell_price(100, 0) == int(100 * PAWN_SELL_FRACTION)


def test_sell_price_improves_with_standing():
    assert sell_price(100, 5) > sell_price(100, 0)


# --- slot capacity / buy-sell flows ---


def _first_weapon():
    return next(item for items in CATALOG.values() for item in items if item.slot is Slot.WEAPON and not item.two_handed)


def _two_handed_weapon():
    return next(item for items in CATALOG.values() for item in items if item.two_handed)


def test_weapon_slot_capacity_is_two_one_handed_weapons():
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    assert buy_item(c, weapon)
    assert buy_item(c, weapon)
    assert slot_usage(c.inventory, Slot.WEAPON) == 2
    assert all(entry.equipped for entry in c.inventory)


def test_third_one_handed_weapon_is_bought_stowed_not_equipped():
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    buy_item(c, weapon)
    buy_item(c, weapon)
    buy_item(c, weapon)
    assert not c.inventory[2].equipped


def test_two_handed_weapon_costs_both_weapon_slots():
    two_handed = _two_handed_weapon()
    c = Character(name="t", cash=100_000)
    assert buy_item(c, two_handed)
    assert slot_usage(c.inventory, Slot.WEAPON) == 2
    weapon = _first_weapon()
    buy_item(c, weapon)
    assert not c.inventory[-1].equipped  # no room left


def test_buy_item_refuses_below_min_standing_gate():
    tier2 = next(item for item in ITEMS_BY_ID.values() if item.min_standing > 0)
    c = Character(name="t", cash=100_000)
    assert not buy_item(c, tier2, standing=tier2.min_standing - 1)
    assert c.cash == 100_000
    assert not c.inventory


def test_buy_item_refuses_when_cannot_afford_and_does_not_charge():
    weapon = _first_weapon()
    c = Character(name="t", cash=0)
    assert not buy_item(c, weapon)
    assert c.cash == 0
    assert not c.inventory


def test_sell_item_by_index_handles_duplicate_ids():
    """Sell is keyed by inventory index, not item id -- the same id can be owned twice."""
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    buy_item(c, weapon)
    buy_item(c, weapon)
    before_cash = c.cash
    proceeds = sell_item(c, 0)
    assert len(c.inventory) == 1
    assert c.cash == before_cash + proceeds


def test_buy_consumable_appends_id_and_charges_cash():
    consumable = next(iter(CONSUMABLES_BY_ID.values()))
    c = Character(name="t", cash=100_000)
    before = c.cash
    assert buy_consumable(c, consumable)
    assert c.consumables == [consumable.id]
    assert c.cash == before - buy_price(consumable.price, 0)


# --- cyberdeck programs: ownership only (buy_program) ---
#
# Installing/uninstalling a bought Program onto a deck is inventory.py's concern --
# see tests/test_inventory.py for that half.

ONE_SLOT_DECK = ITEMS_BY_ID["burner_deck"]
PROGRAM_A = next(iter(PROGRAMS_BY_ID.values()))


def _char_with_deck(deck=ONE_SLOT_DECK, cash=100_000):
    c = Character(name="t", cash=cash)
    assert buy_item(c, deck)
    return c


def test_buy_program_adds_to_owned_pool_and_charges_cash():
    c = _char_with_deck()
    before = c.cash
    message = buy_program(c, PROGRAM_A.id)
    assert PROGRAM_A.id in c.owned_programs
    assert c.cash == before - buy_price(PROGRAM_A.price, 0)
    assert PROGRAM_A.name in message


def test_buy_program_does_not_install_it_on_any_deck():
    c = _char_with_deck()
    buy_program(c, PROGRAM_A.id)
    assert c.inventory[0].installed_programs == []


def test_buy_program_refuses_if_already_owned():
    c = _char_with_deck()
    buy_program(c, PROGRAM_A.id)
    before = c.cash
    message = buy_program(c, PROGRAM_A.id)
    assert c.cash == before
    assert "already own" in message.lower()


def test_buy_program_refuses_when_cannot_afford():
    c = Character(name="t", cash=0)
    message = buy_program(c, PROGRAM_A.id)
    assert PROGRAM_A.id not in c.owned_programs
    assert "afford" in message.lower()


def test_vehicle_catalog_has_the_three_expected_reductions():
    beater, coupe, towncar = (
        ITEMS_BY_ID["beater_bike"],
        ITEMS_BY_ID["tuned_coupe"],
        ITEMS_BY_ID["armored_towncar"],
    )
    assert beater.travel_reduction == 0.10
    assert coupe.travel_reduction == 0.20
    assert towncar.travel_reduction == 0.25


def test_pipe_pistol_is_the_smartlinked_weapon():
    assert ITEMS_BY_ID["pipe_pistol"].smartlinked is True


def test_bonus_text_flags_a_smartlinked_weapon():
    assert "smartlinked" in bonus_text(ITEMS_BY_ID["pipe_pistol"])


def test_bonus_text_omits_smartlinked_for_an_unlinked_weapon():
    assert "smartlinked" not in bonus_text(ITEMS_BY_ID["combat_knife"])


# --- scavenge() ---


def test_scavenge_on_failure_adds_nothing_to_inventory():
    character = character_with_skill_value("tinkering", 0)
    message = scavenge(character, rng=AlwaysOne())
    assert character.inventory == []
    assert "rust" in message.lower()


def test_scavenge_on_success_adds_exactly_one_material():
    # margin 1 (below CRITICAL_MARGIN) with AlwaysSix (every die a success on both
    # sides) gives a plain, non-critical success.
    opposing_pool = pool_for_difficulty(SCAVENGE_DIFFICULTY)
    character = character_with_skill_value("tinkering", opposing_pool + 1)
    message = scavenge(character, rng=AlwaysSix())
    assert len(character.inventory) == 1
    assert character.inventory[0].item_id in SCAVENGE_MATERIALS
    assert character.inventory[0].equipped is False
    assert message != "Nothing but rust and rot."


def test_scavenge_on_critical_success_adds_distinct_materials():
    opposing_pool = pool_for_difficulty(SCAVENGE_DIFFICULTY)
    character = character_with_skill_value("tinkering", opposing_pool + CRITICAL_MARGIN)
    scavenge(character, rng=AlwaysSix())
    found = [entry.item_id for entry in character.inventory]
    assert len(found) == SCAVENGE_CRITICAL_FINDS
    assert len(set(found)) == SCAVENGE_CRITICAL_FINDS
    assert all(item_id in SCAVENGE_MATERIALS for item_id in found)


def test_scavenge_never_grants_the_matrix_only_datashard():
    assert STOLEN_DATASHARD_ID not in SCAVENGE_MATERIALS


# --- Mods / effective_item / install_mod / remove_mod / craft_consumable ---


def test_mod_catalog_weapon_mods_carry_no_defense_and_wearable_mods_carry_no_damage():
    for mod in MOD_CATALOG:
        if mod.applies_to == frozenset({Slot.WEAPON}):
            assert mod.defense == 0
        else:
            assert mod.damage == 0


WEAPON_MOD = next(m for m in MOD_CATALOG if m.applies_to == frozenset({Slot.WEAPON}))
WEARABLE_MOD = next(m for m in MOD_CATALOG if m.applies_to != frozenset({Slot.WEAPON}))


def _weapon_item():
    return next(item for items in CATALOG.values() for item in items if item.slot is Slot.WEAPON)


def _wearable_item():
    return next(item for items in CATALOG.values() for item in items if item.slot is Slot.TORSO)


def _grant_materials(character: Character, materials: dict[str, int]) -> None:
    for material_id, count in materials.items():
        for _ in range(count):
            character.inventory.append(InventoryItem(material_id, equipped=False))


def _char_with_weapon(cash: int = 100_000) -> Character:
    c = Character(name="t", cash=cash)
    weapon = _weapon_item()
    buy_item(c, weapon)
    return c


def test_effective_item_returns_the_base_item_unchanged_when_no_mods():
    entry = InventoryItem("combat_knife")
    assert effective_item(entry) is ITEMS_BY_ID["combat_knife"]


def test_effective_item_folds_a_weapon_mods_damage():
    entry = InventoryItem("combat_knife", mods=[WEAPON_MOD.id])
    base = ITEMS_BY_ID["combat_knife"]
    assert effective_item(entry).damage == base.damage + WEAPON_MOD.damage


def test_effective_item_folds_a_wearable_mods_defense():
    entry = InventoryItem("leather_jacket", mods=[WEARABLE_MOD.id])
    base = ITEMS_BY_ID["leather_jacket"]
    assert effective_item(entry).defense == base.defense + WEARABLE_MOD.defense


def test_install_mod_refuses_when_slot_does_not_match():
    c = Character(name="t", cash=100_000)
    buy_item(c, _wearable_item())
    _grant_materials(c, WEAPON_MOD.materials)
    before_cash = c.cash
    attempted, message = install_mod(c, 0, WEAPON_MOD.id, rng=AlwaysSix())
    assert attempted is False
    assert "doesn't fit" in message
    assert c.inventory[0].mods == []
    assert c.cash == before_cash


def test_install_mod_refuses_without_enough_materials():
    c = _char_with_weapon()
    attempted, message = install_mod(c, 0, WEAPON_MOD.id, rng=AlwaysSix())
    assert attempted is False
    assert "materials" in message.lower()
    assert c.inventory[0].mods == []


def test_install_mod_on_failed_check_spends_nothing():
    c = _char_with_weapon()
    _grant_materials(c, WEAPON_MOD.materials)
    before_cash = c.cash
    before_materials = len(c.inventory)
    c.skill_ranks["armorer"] = 0
    c.logic = 0
    c.perception = 0
    attempted, message = install_mod(c, 0, WEAPON_MOD.id, rng=AlwaysOne())
    assert attempted is True
    assert c.inventory[0].mods == []
    assert c.cash == before_cash
    assert len(c.inventory) == before_materials
    assert "nothing spent" in message


def test_install_mod_on_success_spends_cash_and_materials_and_attaches_mod():
    c = _char_with_weapon()
    _grant_materials(c, WEAPON_MOD.materials)
    opposing_pool = pool_for_difficulty(WORKSHOP_ARMORER_DIFFICULTY)
    c.skill_ranks["armorer"] = 0
    c.logic = opposing_pool + 1
    c.perception = opposing_pool + 1
    before_cash = c.cash
    attempted, message = install_mod(c, 0, WEAPON_MOD.id, rng=AlwaysSix())
    assert attempted is True
    assert c.inventory[0].mods == [WEAPON_MOD.id]
    assert c.cash == before_cash - WEAPON_MOD.price
    assert len(c.inventory) == 1  # the weapon only -- materials consumed
    assert WEAPON_MOD.name in message


def test_install_mod_refuses_a_duplicate():
    c = _char_with_weapon()
    c.inventory[0].mods = [WEAPON_MOD.id]
    _grant_materials(c, WEAPON_MOD.materials)
    attempted, message = install_mod(c, 0, WEAPON_MOD.id, rng=AlwaysSix())
    assert attempted is False
    assert "already has" in message
    assert c.inventory[0].mods == [WEAPON_MOD.id]


def test_install_mod_refuses_when_no_free_slots():
    c = _char_with_weapon()
    c.inventory[0].mods = ["placeholder"] * MOD_SLOTS_PER_ITEM
    _grant_materials(c, WEAPON_MOD.materials)
    attempted, message = install_mod(c, 0, WEAPON_MOD.id, rng=AlwaysSix())
    assert attempted is False
    assert "no free mod slots" in message


def test_remove_mod_is_free_and_detaches_it():
    c = _char_with_weapon()
    c.inventory[0].mods = [WEAPON_MOD.id]
    before_cash = c.cash
    message = remove_mod(c, 0, WEAPON_MOD.id)
    assert c.inventory[0].mods == []
    assert c.cash == before_cash
    assert WEAPON_MOD.name in message


def test_remove_mod_refuses_when_not_installed():
    c = _char_with_weapon()
    message = remove_mod(c, 0, WEAPON_MOD.id)
    assert "not installed" in message.lower()


# --- Pistols' named mod-slot layout (WEAPON_MOD_SLOTS) ---


def _char_with_pistol(cash: int = 100_000) -> Character:
    c = Character(name="t", cash=cash)
    buy_item(c, ITEMS_BY_ID["pipe_pistol"])
    return c


def test_buy_item_seeds_a_pistol_with_one_stock_mod_per_named_slot():
    c = _char_with_pistol()
    assert c.inventory[0].mods == [
        STOCK_MOD_IDS[slot_type] for slot_type in WEAPON_MOD_SLOTS["pistols"]
    ]


def test_buy_item_leaves_a_non_named_slot_weapon_with_no_mods():
    c = _char_with_weapon()
    assert c.inventory[0].mods == []


def test_install_mod_on_a_pistol_swaps_the_named_slot_instead_of_appending():
    c = _char_with_pistol()
    _grant_materials(c, MODS_BY_ID["hollow_points"].materials)
    before_len = len(c.inventory[0].mods)
    attempted, message = install_mod(c, 0, "hollow_points", rng=AlwaysSix())
    assert attempted is True
    assert len(c.inventory[0].mods) == before_len
    barrel_index = WEAPON_MOD_SLOTS["pistols"].index(WeaponModSlot.BARREL)
    assert c.inventory[0].mods[barrel_index] == "hollow_points"
    assert "Installed" in message


def test_install_mod_on_a_pistol_can_swap_a_slot_back_to_stock():
    c = _char_with_pistol()
    _grant_materials(c, MODS_BY_ID["hollow_points"].materials)
    install_mod(c, 0, "hollow_points", rng=AlwaysSix())
    attempted, _ = install_mod(c, 0, "stock_barrel", rng=AlwaysSix())
    assert attempted is True
    barrel_index = WEAPON_MOD_SLOTS["pistols"].index(WeaponModSlot.BARREL)
    assert c.inventory[0].mods[barrel_index] == "stock_barrel"


def test_install_mod_on_a_pistol_refuses_a_mod_already_in_that_slot():
    c = _char_with_pistol()
    barrel_index = WEAPON_MOD_SLOTS["pistols"].index(WeaponModSlot.BARREL)
    stock_barrel_id = c.inventory[0].mods[barrel_index]
    attempted, message = install_mod(c, 0, stock_barrel_id, rng=AlwaysSix())
    assert attempted is False
    assert "already has" in message


def test_install_mod_on_a_pistol_leaves_other_slots_untouched():
    c = _char_with_pistol()
    before = list(c.inventory[0].mods)
    _grant_materials(c, MODS_BY_ID["hollow_points"].materials)
    install_mod(c, 0, "hollow_points", rng=AlwaysSix())
    barrel_index = WEAPON_MOD_SLOTS["pistols"].index(WeaponModSlot.BARREL)
    for i, mod_id in enumerate(c.inventory[0].mods):
        if i != barrel_index:
            assert mod_id == before[i]


# --- The longarms/automatics layouts (the same named-slot machinery, wider) ---

# One catalog weapon per named layout, so a layout that gains a slot is exercised
# against a gun a player can actually buy rather than a synthesised Item.
GUN_BY_SKILL = {"pistols": "pipe_pistol", "longarms": "pump_shotgun", "automatics": "machine_pistol"}


def _char_with_gun(item_id: str) -> Character:
    c = Character(name="t", cash=100_000)
    buy_item(c, ITEMS_BY_ID[item_id])
    return c


def test_gun_layouts_form_a_pistols_longarms_automatics_ladder():
    """The deliberate 4/5/6 ladder, and the one slot that separates the two long guns."""
    lengths = tuple(len(WEAPON_MOD_SLOTS[skill]) for skill in ("pistols", "longarms", "automatics"))
    assert lengths == (4, 5, 6)
    assert WeaponModSlot.MAGAZINE not in WEAPON_MOD_SLOTS["longarms"]


@pytest.mark.parametrize("skill,item_id", sorted(GUN_BY_SKILL.items()))
def test_buy_item_seeds_every_named_layout_gun_with_one_stock_mod_per_slot(skill, item_id):
    c = _char_with_gun(item_id)
    assert c.inventory[0].mods == [
        STOCK_MOD_IDS[slot_type] for slot_type in WEAPON_MOD_SLOTS[skill]
    ]


LAYOUT_SLOTS = sorted(
    {slot for slots in WEAPON_MOD_SLOTS.values() for slot in slots}, key=lambda s: s.value
)


@pytest.mark.parametrize("slot_type", LAYOUT_SLOTS)
def test_every_named_slot_has_a_real_upgrade_and_not_just_its_stock_part(slot_type):
    """A slot with nothing but its stock part to install is dead UI — the workshop
    lists it forever and the player can never change it."""
    upgrades = [
        mod
        for mod in MOD_CATALOG
        if mod.weapon_slot is slot_type and mod.id != STOCK_MOD_IDS[slot_type]
    ]
    assert upgrades, f"no upgrade for {slot_type.value}"


def test_install_mod_fills_an_automatics_only_magazine_slot():
    c = _char_with_gun(GUN_BY_SKILL["automatics"])
    _grant_materials(c, MODS_BY_ID["extended_magazine"].materials)
    attempted, message = install_mod(c, 0, "extended_magazine", rng=AlwaysSix())
    assert attempted is True
    assert "Installed" in message
    magazine_index = WEAPON_MOD_SLOTS["automatics"].index(WeaponModSlot.MAGAZINE)
    assert c.inventory[0].mods[magazine_index] == "extended_magazine"
    assert len(c.inventory[0].mods) == len(WEAPON_MOD_SLOTS["automatics"])


def test_install_mod_fills_a_longarms_buttstock_slot():
    c = _char_with_gun(GUN_BY_SKILL["longarms"])
    _grant_materials(c, MODS_BY_ID["recoil_stock"].materials)
    attempted, _ = install_mod(c, 0, "recoil_stock", rng=AlwaysSix())
    assert attempted is True
    buttstock_index = WEAPON_MOD_SLOTS["longarms"].index(WeaponModSlot.BUTTSTOCK)
    assert c.inventory[0].mods[buttstock_index] == "recoil_stock"


def test_install_mod_refuses_a_magazine_on_a_longarm():
    """A mod whose slot the layout doesn't carry is refused outright, not appended —
    the shotgun's five slots stay five."""
    c = _char_with_gun(GUN_BY_SKILL["longarms"])
    _grant_materials(c, MODS_BY_ID["extended_magazine"].materials)
    attempted, message = install_mod(c, 0, "extended_magazine", rng=AlwaysSix())
    assert attempted is False
    assert "doesn't fit" in message
    assert len(c.inventory[0].mods) == len(WEAPON_MOD_SLOTS["longarms"])


@pytest.mark.parametrize("mod_id", ["extended_magazine", "machined_grip", "reflex_sight"])
def test_gun_furniture_does_not_install_on_a_weapon_with_no_named_layout(mod_id):
    """A magazine on a combat knife passed the applies_to check alone — the flat-list
    fallback exists for sharpened_edge/hollow_points, not for firearm parts."""
    c = _char_with_weapon()
    _grant_materials(c, MODS_BY_ID[mod_id].materials)
    attempted, message = install_mod(c, 0, mod_id, rng=AlwaysSix())
    assert attempted is False
    assert "doesn't fit" in message
    assert c.inventory[0].mods == []


def test_a_stock_part_does_not_burn_a_flat_list_mod_slot():
    """Stock parts only mean something inside a named layout. On a flat-list weapon one
    would spend a capped slot and a workshop trip for zero bonus."""
    c = _char_with_weapon()
    attempted, message = install_mod(c, 0, "stock_barrel", rng=AlwaysSix())
    assert attempted is False
    assert "doesn't fit" in message
    assert c.inventory[0].mods == []


def test_sharpened_edge_still_installs_on_a_flat_list_weapon():
    """The guard above must not close the fallback path it was narrowing."""
    c = _char_with_weapon()
    _grant_materials(c, MODS_BY_ID["sharpened_edge"].materials)
    attempted, _ = install_mod(c, 0, "sharpened_edge", rng=AlwaysSix())
    assert attempted is True
    assert c.inventory[0].mods == ["sharpened_edge"]


@pytest.mark.parametrize("skill,item_id", sorted(GUN_BY_SKILL.items()))
def test_remove_mod_refuses_on_a_named_layout_and_keeps_every_slot_index_valid(skill, item_id):
    """Shortening a named layout's list leaves every later slot index off by one, so the
    next install (and SafehouseScreen's slot listing) would IndexError."""
    c = _char_with_gun(item_id)
    _grant_materials(c, MODS_BY_ID["hollow_points"].materials)
    install_mod(c, 0, "hollow_points", rng=AlwaysSix())
    message = remove_mod(c, 0, "hollow_points")
    assert "stock part" in message
    assert len(c.inventory[0].mods) == len(WEAPON_MOD_SLOTS[skill])
    barrel_index = WEAPON_MOD_SLOTS[skill].index(WeaponModSlot.BARREL)
    assert c.inventory[0].mods[barrel_index] == "hollow_points"


def test_a_fully_kitted_gun_folds_every_slots_damage_into_effective_item():
    """Every named slot contributes: the ceiling this system actually grants is the
    sum of the best mod in each slot, which is what makes it worth the eb."""
    item = ITEMS_BY_ID[GUN_BY_SKILL["automatics"]]
    best = [
        max((m for m in MOD_CATALOG if m.weapon_slot is slot_type), key=lambda m: m.damage)
        for slot_type in WEAPON_MOD_SLOTS["automatics"]
    ]
    entry = InventoryItem(item.id, mods=[mod.id for mod in best])
    assert effective_item(entry).damage == item.damage + sum(mod.damage for mod in best)


# --- Ammo: catalog shape, reserve pool, loading and firing ---


def test_exactly_the_ranged_weapons_take_ammo():
    """The import guard from the other side: every RANGED_SKILLS weapon declares a
    magazine and nothing else does, so no gun fires forever and no blade grows a
    Reload action nothing can feed."""
    for item in ITEMS_BY_ID.values():
        if item.slot is not Slot.WEAPON:
            continue
        assert (item.ammo is not None) == (item.skill in RANGED_SKILLS), item.id
        assert (item.magazine > 0) == (item.ammo is not None), item.id


def test_ammo_kind_values_are_usable_as_ids():
    """They key Character.ammo (pickled) and end up in Textual widget ids, which reject
    spaces — the label is the display form."""
    for kind in AmmoKind:
        assert kind.value.replace("_", "").isalnum()
        assert " " in kind.label or kind.label == kind.value


def test_buy_ammo_adds_a_box_to_the_reserve_and_charges_cash():
    c = Character(name="t", cash=100_000)
    before = c.cash
    assert buy_ammo(c, AmmoKind.PISTOL)
    box = AMMO_BY_KIND[AmmoKind.PISTOL]
    assert c.ammo[AmmoKind.PISTOL.value] == box.rounds
    assert c.cash == before - buy_price(box.price, 0)


def test_buy_ammo_refuses_when_cannot_afford_and_loads_nothing():
    c = Character(name="t", cash=0)
    assert not buy_ammo(c, AmmoKind.PISTOL)
    assert c.ammo == {}


def test_buy_item_hands_over_a_gun_with_a_full_magazine():
    c = _char_with_gun("machine_pistol")
    gun = ITEMS_BY_ID["machine_pistol"]
    assert c.loaded[gun.id] == gun.magazine


def test_an_untracked_weapon_reads_as_full_not_empty():
    """A weapon can enter the inventory by routes that never touch buy_item. Those must
    arrive ready to fire — 'absent' means 'as it came', not 'empty'."""
    c = Character(name="t", cash=0, inventory=[InventoryItem("pipe_pistol", equipped=True)])
    gun = ITEMS_BY_ID["pipe_pistol"]
    assert c.loaded == {}
    assert loaded_rounds(c, gun) == gun.magazine
    assert has_ammo(c, gun)


def test_firing_writes_the_key_so_an_empty_gun_stays_distinguishable():
    c = Character(name="t", cash=0, inventory=[InventoryItem("pipe_pistol", equipped=True)])
    gun = ITEMS_BY_ID["pipe_pistol"]
    spend_round(c, gun)
    assert c.loaded[gun.id] == gun.magazine - 1
    for _ in range(gun.magazine):
        spend_round(c, gun)
    assert c.loaded[gun.id] == 0
    assert not has_ammo(c, gun)


def test_melee_never_runs_dry():
    c = Character(name="t", cash=0)
    knife = ITEMS_BY_ID["combat_knife"]
    assert has_ammo(c, knife)
    spend_round(c, knife)
    assert c.loaded == {}
    assert has_ammo(c, knife)


def test_extended_magazine_buys_capacity_rather_than_damage():
    """The MAGAZINE slot's whole identity, and the reason it isn't just another +1."""
    mod = MODS_BY_ID["extended_magazine"]
    assert mod.magazine > 0
    assert mod.damage == 0
    c = _char_with_gun("machine_pistol")
    _grant_materials(c, mod.materials)
    install_mod(c, 0, "extended_magazine", rng=AlwaysSix())
    base = ITEMS_BY_ID["machine_pistol"]
    modded = effective_item(c.inventory[0])
    assert modded.magazine == base.magazine + mod.magazine
    assert modded.damage == base.damage


def test_reload_weapon_moves_rounds_from_reserve_into_the_gun():
    c = _char_with_gun("pipe_pistol")
    gun = ITEMS_BY_ID["pipe_pistol"]
    c.loaded[gun.id] = 0
    buy_ammo(c, AmmoKind.PISTOL)
    reserve = c.ammo[AmmoKind.PISTOL.value]
    message = reload_weapon(c, 0)
    assert c.loaded[gun.id] == gun.magazine
    assert c.ammo[AmmoKind.PISTOL.value] == reserve - gun.magazine
    assert "Loaded" in message


def test_reload_weapon_partially_fills_from_a_short_reserve():
    c = _char_with_gun("pump_shotgun")
    gun = ITEMS_BY_ID["pump_shotgun"]
    short = gun.magazine - 1
    c.loaded[gun.id] = 0
    c.ammo[AmmoKind.SHOTGUN.value] = short
    reload_weapon(c, 0)
    assert c.loaded[gun.id] == short
    assert c.ammo[AmmoKind.SHOTGUN.value] == 0


def test_reload_weapon_refuses_a_full_gun_and_spends_no_reserve():
    c = _char_with_gun("pipe_pistol")
    buy_ammo(c, AmmoKind.PISTOL)
    reserve = c.ammo[AmmoKind.PISTOL.value]
    message = reload_weapon(c, 0)
    assert "already full" in message
    assert c.ammo[AmmoKind.PISTOL.value] == reserve


def test_reload_weapon_on_an_empty_reserve_says_so():
    c = _char_with_gun("pipe_pistol")
    c.loaded["pipe_pistol"] = 0
    message = reload_weapon(c, 0)
    assert "No pistol rounds left" in message
    assert c.loaded["pipe_pistol"] == 0


def test_reload_weapon_refuses_a_melee_weapon():
    c = _char_with_weapon()
    assert "doesn't take ammo" in reload_weapon(c, 0)


def test_reload_fills_to_the_modded_capacity_not_the_catalog_one():
    c = _char_with_gun("machine_pistol")
    _grant_materials(c, MODS_BY_ID["extended_magazine"].materials)
    install_mod(c, 0, "extended_magazine", rng=AlwaysSix())
    c.loaded["machine_pistol"] = 0
    c.ammo[AmmoKind.PISTOL.value] = 100
    reload_weapon(c, 0)
    expected = ITEMS_BY_ID["machine_pistol"].magazine + MODS_BY_ID["extended_magazine"].magazine
    assert c.loaded["machine_pistol"] == expected


def test_loaded_rounds_clamps_when_a_mod_shrinks_the_magazine():
    """Fitting the stock magazine back over an Extended Magazine is free and is the
    documented way to undo a named-slot upgrade — without the clamp the gun would keep
    reporting (and firing) 30 rounds out of a 20-round magazine."""
    c = _char_with_gun("machine_pistol")
    _grant_materials(c, MODS_BY_ID["extended_magazine"].materials)
    install_mod(c, 0, "extended_magazine", rng=AlwaysSix())
    c.ammo[AmmoKind.PISTOL.value] = 100
    reload_weapon(c, 0)
    big = effective_item(c.inventory[0])
    assert c.loaded["machine_pistol"] == big.magazine
    install_mod(c, 0, "stock_magazine", rng=AlwaysSix())
    small = effective_item(c.inventory[0])
    assert small.magazine == ITEMS_BY_ID["machine_pistol"].magazine
    assert loaded_rounds(c, small) == small.magazine


def test_selling_a_gun_returns_its_rounds_and_clears_the_magazine():
    """Character.loaded is keyed by weapon id, so a stale entry would survive the sale
    and over-load the next gun of the same kind bought."""
    c = _char_with_gun("pump_shotgun")
    gun = ITEMS_BY_ID["pump_shotgun"]
    sell_item(c, 0)
    assert gun.id not in c.loaded
    assert c.ammo[AmmoKind.SHOTGUN.value] == gun.magazine


def test_selling_one_of_two_identical_guns_leaves_the_other_loaded():
    """The cost of id-keying, handled explicitly: two copies share a magazine, so
    clearing it on the first sale would unload the gun still in hand."""
    c = _char_with_gun("pump_shotgun")
    buy_item(c, ITEMS_BY_ID["pump_shotgun"])
    sell_item(c, 0)
    assert c.loaded["pump_shotgun"] == ITEMS_BY_ID["pump_shotgun"].magazine
    assert c.ammo.get(AmmoKind.SHOTGUN.value, 0) == 0


def test_a_fresh_gun_is_not_over_loaded_by_a_previous_owners_leftovers():
    c = _char_with_gun("machine_pistol")
    _grant_materials(c, MODS_BY_ID["extended_magazine"].materials)
    install_mod(c, 0, "extended_magazine", rng=AlwaysSix())
    c.ammo[AmmoKind.PISTOL.value] = 100
    reload_weapon(c, 0)
    sell_item(c, 0)
    buy_item(c, ITEMS_BY_ID["machine_pistol"])
    fresh = effective_item(c.inventory[-1])
    assert loaded_rounds(c, fresh) == ITEMS_BY_ID["machine_pistol"].magazine


def test_grant_item_is_the_only_direct_inventory_construction_left():
    """The chokepoint's whole point: any path that builds an InventoryItem by hand skips
    stock-mod seeding and loading, and a hand-built pistol IndexErrors SafehouseScreen on
    entry.mods[slot_index] — the failure the v62/v64 save notes describe."""
    root = Path(__file__).resolve().parents[1] / "src" / "shadowguy"
    offenders = []
    for path in root.rglob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "InventoryItem(" not in line or line.lstrip().startswith("#"):
                continue
            if path.name == "shops.py" or "class InventoryItem" in line:
                continue
            offenders.append(f"{path.name}:{number}")
    assert not offenders, f"build these through shops.grant_item: {offenders}"


def test_grant_item_seeds_stock_mods_and_loads_a_gun():
    c = Character(name="t", cash=0)
    entry = grant_item(c, ITEMS_BY_ID["machine_pistol"])
    assert entry.mods == [STOCK_MOD_IDS[s] for s in WEAPON_MOD_SLOTS["automatics"]]
    assert c.loaded["machine_pistol"] == ITEMS_BY_ID["machine_pistol"].magazine
    assert entry.equipped is True


def test_grant_item_honours_an_explicit_stowed_flag_for_loot():
    c = Character(name="t", cash=0)
    entry = grant_item(c, ITEMS_BY_ID[STOLEN_DATASHARD_ID], equipped=False)
    assert entry.equipped is False


def test_install_refusal_agrees_with_install_mod_on_every_item_and_mod():
    """The two used to keep separate copies of these rules and drifted. Sweep the whole
    catalog: whenever install_refusal permits, a funded install must actually attempt."""
    for item in ITEMS_BY_ID.values():
        for mod in MOD_CATALOG:
            c = Character(name="t", cash=100_000)
            grant_item(c, item)
            _grant_materials(c, mod.materials)
            permitted = install_refusal(c.inventory[0], mod) is None
            attempted, _ = install_mod(c, 0, mod.id, rng=AlwaysSix())
            assert attempted is permitted, f"{item.id} + {mod.id}"


CRAFTABLE_ID = next(iter(CRAFT_RECIPES))


def test_craft_consumable_refuses_an_unknown_recipe():
    c = Character(name="t", cash=100_000)
    attempted, message = craft_consumable(c, "health_kit", rng=AlwaysSix())
    assert attempted is False
    assert "can't craft" in message.lower()
    assert c.consumables == []


def test_craft_consumable_refuses_without_enough_materials():
    c = Character(name="t", cash=100_000)
    attempted, message = craft_consumable(c, CRAFTABLE_ID, rng=AlwaysSix())
    assert attempted is False
    assert "materials" in message.lower()
    assert c.consumables == []


def test_craft_consumable_on_failed_check_spends_nothing():
    c = Character(name="t", cash=100_000)
    _grant_materials(c, CRAFT_RECIPES[CRAFTABLE_ID])
    c.skill_ranks["chemistry"] = 0
    c.logic = 0
    c.perception = 0
    before_cash = c.cash
    before_materials = len(c.inventory)
    attempted, message = craft_consumable(c, CRAFTABLE_ID, rng=AlwaysOne())
    assert attempted is True
    assert c.consumables == []
    assert c.cash == before_cash
    assert len(c.inventory) == before_materials
    assert "nothing spent" in message


def test_craft_consumable_on_success_spends_cash_and_materials_and_appends_it():
    c = Character(name="t", cash=100_000)
    _grant_materials(c, CRAFT_RECIPES[CRAFTABLE_ID])
    opposing_pool = pool_for_difficulty(WORKSHOP_CHEMISTRY_DIFFICULTY)
    c.skill_ranks["chemistry"] = 0
    c.logic = opposing_pool + 1
    c.perception = opposing_pool + 1
    before_cash = c.cash
    attempted, message = craft_consumable(c, CRAFTABLE_ID, rng=AlwaysSix())
    assert attempted is True
    assert c.consumables == [CRAFTABLE_ID]
    assert c.cash < before_cash
    materials_left = sum(1 for e in c.inventory if e.item_id in CRAFT_RECIPES[CRAFTABLE_ID])
    assert materials_left == 0
    assert CONSUMABLES_BY_ID[CRAFTABLE_ID].name in message
