"""Tests for shops.py: standing-scaled pricing, slot capacity, buy/sell/equip flows."""


from shadowguy.character import Character
from shadowguy.shops import (
    CATALOG,
    ITEMS_BY_ID,
    PAWN_SELL_FRACTION,
    PROGRAMS_BY_ID,
    STANDING_PRICE_CAP,
    STANDING_PRICE_STEP,
    Slot,
    active_deck_entry,
    buy_consumable,
    buy_item,
    buy_price,
    buy_program,
    free_program_slots,
    install_program,
    installed_programs_for,
    sell_item,
    sell_price,
    slot_usage,
    toggle_equip,
    uninstall_program,
    use_consumable,
)
from shadowguy.shops import CONSUMABLES_BY_ID


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


# --- slot capacity / equip flows ---


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


def test_toggle_equip_unequip_always_succeeds():
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    buy_item(c, weapon)
    assert toggle_equip(c, 0)
    assert not c.inventory[0].equipped


def test_toggle_equip_refuses_when_slot_full():
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    buy_item(c, weapon)
    buy_item(c, weapon)
    buy_item(c, weapon)  # stowed, slot full
    assert not toggle_equip(c, 2)
    assert not c.inventory[2].equipped


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


def test_use_consumable_combat_only_effect_is_refused_without_being_spent():
    grenade = next(c for c in CONSUMABLES_BY_ID.values() if c.effect.value.startswith("combat_"))
    c = Character(name="t", consumables=[grenade.id])
    message = use_consumable(c, 0)
    assert "fight" in message.lower()
    assert c.consumables == [grenade.id]  # not popped


def test_use_consumable_heal_refuses_at_full_health_without_spending():
    heal = next(c for c in CONSUMABLES_BY_ID.values() if c.effect.value == "heal")
    c = Character(name="t", consumables=[heal.id])
    assert c.health == c.max_health
    use_consumable(c, 0)
    assert c.consumables == [heal.id]


def test_use_consumable_heal_capped_once_per_day():
    heal = next(c for c in CONSUMABLES_BY_ID.values() if c.effect.value == "heal")
    c = Character(name="t", consumables=[heal.id, heal.id])
    c.adjust_health(-1000)
    use_consumable(c, 0)
    assert c.health_kit_used_today
    message = use_consumable(c, 0)
    assert "today" in message.lower()
    assert c.consumables == [heal.id]  # second kit not spent


# --- cyberdeck programs ---

ONE_SLOT_DECK = ITEMS_BY_ID["burner_deck"]
TWO_SLOT_DECK = ITEMS_BY_ID["cracked_cyberdeck"]
PASSIVE_PROGRAM = next(p for p in PROGRAMS_BY_ID.values() if p.uses_per_fight == 0)
ACTION_PROGRAM = next(p for p in PROGRAMS_BY_ID.values() if p.uses_per_fight > 0)


def _char_with_deck(deck=ONE_SLOT_DECK, cash=100_000):
    c = Character(name="t", cash=cash)
    assert buy_item(c, deck)
    return c


def test_buy_program_adds_to_owned_pool_and_charges_cash():
    c = _char_with_deck()
    before = c.cash
    message = buy_program(c, PASSIVE_PROGRAM.id)
    assert PASSIVE_PROGRAM.id in c.owned_programs
    assert c.cash == before - buy_price(PASSIVE_PROGRAM.price, 0)
    assert PASSIVE_PROGRAM.name in message


def test_buy_program_does_not_install_it_on_any_deck():
    c = _char_with_deck()
    buy_program(c, PASSIVE_PROGRAM.id)
    assert c.inventory[0].installed_programs == []


def test_buy_program_refuses_if_already_owned():
    c = _char_with_deck()
    buy_program(c, PASSIVE_PROGRAM.id)
    before = c.cash
    message = buy_program(c, PASSIVE_PROGRAM.id)
    assert c.cash == before
    assert "already own" in message.lower()


def test_buy_program_refuses_when_cannot_afford():
    c = Character(name="t", cash=0)
    message = buy_program(c, PASSIVE_PROGRAM.id)
    assert PASSIVE_PROGRAM.id not in c.owned_programs
    assert "afford" in message.lower()


def test_active_deck_entry_picks_best_rated_equipped_deck():
    c = _char_with_deck(ONE_SLOT_DECK)
    assert buy_item(c, TWO_SLOT_DECK)  # cracked_cyberdeck: +2 int > burner_deck's +1
    entry, item = active_deck_entry(c.inventory)
    assert item.id == TWO_SLOT_DECK.id
    assert entry is c.inventory[1]


def test_active_deck_entry_none_without_an_equipped_deck():
    c = Character(name="t")
    assert active_deck_entry(c.inventory) is None


def test_install_program_requires_ownership():
    c = _char_with_deck()
    message = install_program(c, 0, PASSIVE_PROGRAM.id)
    assert c.inventory[0].installed_programs == []
    assert "don't own" in message.lower()


def test_install_program_installs_and_free_program_slots_updates():
    c = _char_with_deck(ONE_SLOT_DECK)
    buy_program(c, PASSIVE_PROGRAM.id)
    assert free_program_slots(ONE_SLOT_DECK, c.inventory[0]) == 1
    message = install_program(c, 0, PASSIVE_PROGRAM.id)
    assert c.inventory[0].installed_programs == [PASSIVE_PROGRAM.id]
    assert free_program_slots(ONE_SLOT_DECK, c.inventory[0]) == 0
    assert PASSIVE_PROGRAM.name in message
    assert installed_programs_for(c.inventory[0]) == [PASSIVE_PROGRAM]


def test_install_program_refuses_beyond_capacity():
    c = _char_with_deck(ONE_SLOT_DECK)  # 1 slot
    buy_program(c, PASSIVE_PROGRAM.id)
    buy_program(c, ACTION_PROGRAM.id)
    install_program(c, 0, PASSIVE_PROGRAM.id)
    message = install_program(c, 0, ACTION_PROGRAM.id)
    assert c.inventory[0].installed_programs == [PASSIVE_PROGRAM.id]
    assert "no free program slots" in message.lower()


def test_install_program_refuses_on_a_non_deck_item():
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    buy_item(c, weapon)
    buy_program(c, PASSIVE_PROGRAM.id)
    message = install_program(c, 0, PASSIVE_PROGRAM.id)
    assert "can't run programs" in message.lower()


def test_uninstall_program_removes_it_but_it_stays_owned():
    c = _char_with_deck(ONE_SLOT_DECK)
    buy_program(c, PASSIVE_PROGRAM.id)
    install_program(c, 0, PASSIVE_PROGRAM.id)
    message = uninstall_program(c, 0, PASSIVE_PROGRAM.id)
    assert c.inventory[0].installed_programs == []
    assert PASSIVE_PROGRAM.id in c.owned_programs  # still owned, just not installed
    assert PASSIVE_PROGRAM.name in message


def test_uninstalled_program_can_be_installed_on_a_different_deck():
    c = _char_with_deck(ONE_SLOT_DECK)
    assert buy_item(c, TWO_SLOT_DECK)
    buy_program(c, PASSIVE_PROGRAM.id)
    install_program(c, 0, PASSIVE_PROGRAM.id)
    uninstall_program(c, 0, PASSIVE_PROGRAM.id)
    message = install_program(c, 1, PASSIVE_PROGRAM.id)
    assert c.inventory[1].installed_programs == [PASSIVE_PROGRAM.id]
    assert PASSIVE_PROGRAM.name in message
