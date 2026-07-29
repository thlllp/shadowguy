"""Tests for shops.py: standing-scaled pricing, catalogs, and buy/sell transactions."""

from shadowguy.character import Character
from shadowguy.checks import CRITICAL_MARGIN, pool_for_difficulty
from shadowguy.shops import (
    CATALOG,
    ITEMS_BY_ID,
    PAWN_SELL_FRACTION,
    PROGRAMS_BY_ID,
    SCAVENGE_CRITICAL_FINDS,
    SCAVENGE_DIFFICULTY,
    SCAVENGE_MATERIALS,
    STANDING_PRICE_CAP,
    STANDING_PRICE_STEP,
    STOLEN_DATASHARD_ID,
    Slot,
    bonus_text,
    buy_consumable,
    buy_item,
    buy_price,
    buy_program,
    scavenge,
    sell_item,
    sell_price,
    slot_usage,
)
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
