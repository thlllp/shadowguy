"""Tests for inventory.py: equip state, cyberdeck programs, and using a consumable."""

from shadowguy.character import Character
from shadowguy.inventory import (
    active_deck_entry,
    equipped_travel_reduction,
    free_program_slots,
    install_program,
    installed_programs_for,
    toggle_equip,
    uninstall_program,
    use_consumable,
)
from shadowguy.shops import (
    CATALOG,
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    PROGRAMS_BY_ID,
    InventoryItem,
    Program,
    Slot,
    buy_item,
    buy_program,
)


def _first_weapon():
    return next(item for items in CATALOG.values() for item in items if item.slot is Slot.WEAPON and not item.two_handed)


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


# --- cyberdeck programs: install/uninstall onto an owned deck ---
#
# These tests exercise install/uninstall bookkeeping only (slot capacity, error
# messages) -- none of it depends on a program's specific effect, so any two distinct
# catalog programs work as fixtures. Every program in today's catalog (sleaze/extract/
# analyze) happens to be action-shaped (uses_per_fight != 0); effect-specific behavior
# (passive bonuses, action rolls) is covered in tests/test_matrix.py instead.

ONE_SLOT_DECK = ITEMS_BY_ID["burner_deck"]
TWO_SLOT_DECK = ITEMS_BY_ID["cracked_cyberdeck"]
_CATALOG_PROGRAMS = sorted(PROGRAMS_BY_ID.values(), key=lambda p: p.id)
PROGRAM_A = _CATALOG_PROGRAMS[0]
PROGRAM_B = _CATALOG_PROGRAMS[1]


def _char_with_deck(deck=ONE_SLOT_DECK, cash=100_000):
    c = Character(name="t", cash=cash)
    assert buy_item(c, deck)
    return c


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
    message = install_program(c, 0, PROGRAM_A.id)
    assert c.inventory[0].installed_programs == []
    assert "don't own" in message.lower()


def test_install_program_installs_and_free_program_slots_updates():
    c = _char_with_deck(ONE_SLOT_DECK)
    buy_program(c, PROGRAM_A.id)
    assert free_program_slots(ONE_SLOT_DECK, c.inventory[0]) == 1
    message = install_program(c, 0, PROGRAM_A.id)
    assert c.inventory[0].installed_programs == [PROGRAM_A.id]
    assert free_program_slots(ONE_SLOT_DECK, c.inventory[0]) == 0
    assert PROGRAM_A.name in message
    assert installed_programs_for(c.inventory[0]) == [PROGRAM_A]


def test_install_program_refuses_beyond_capacity():
    c = _char_with_deck(ONE_SLOT_DECK)  # 1 slot
    buy_program(c, PROGRAM_A.id)
    buy_program(c, PROGRAM_B.id)
    install_program(c, 0, PROGRAM_A.id)
    message = install_program(c, 0, PROGRAM_B.id)
    assert c.inventory[0].installed_programs == [PROGRAM_A.id]
    assert "no free program slots" in message.lower()


def test_program_ram_cost_is_charged_against_capacity_not_just_program_count(monkeypatch):
    """Every catalog program costs 1 RAM today, so this only bites once something
    doesn't -- built with a synthetic higher-cost program to prove free_program_slots
    actually sums ram_cost rather than just counting installed programs."""
    heavy = Program(id="test_heavy", name="Test Heavy", price=0, ram_cost=2, integrity_bonus=1)
    monkeypatch.setitem(PROGRAMS_BY_ID, heavy.id, heavy)
    c = _char_with_deck(TWO_SLOT_DECK)
    c.owned_programs.add(heavy.id)
    assert free_program_slots(TWO_SLOT_DECK, c.inventory[0]) == 2
    install_program(c, 0, heavy.id)
    assert free_program_slots(TWO_SLOT_DECK, c.inventory[0]) == 0  # one ram_cost=2 program fills a 2-slot deck
    buy_program(c, PROGRAM_A.id)
    message = install_program(c, 0, PROGRAM_A.id)
    assert "no free program slots" in message.lower()


def test_install_program_refuses_when_ram_cost_exceeds_partial_free_capacity(monkeypatch):
    """The weaker, easier-to-miss case than "no room at all": free_program_slots can
    be positive (some room left) but still less than the incoming program's own
    ram_cost, and install_program must refuse that too, not just the exactly-full or
    completely-empty cases."""
    heavy = Program(id="test_heavy", name="Test Heavy", price=0, ram_cost=2, integrity_bonus=1)
    monkeypatch.setitem(PROGRAMS_BY_ID, heavy.id, heavy)
    c = _char_with_deck(TWO_SLOT_DECK)  # 2 slots
    buy_program(c, PROGRAM_A.id)
    install_program(c, 0, PROGRAM_A.id)  # uses 1 RAM, leaving 1 free
    assert free_program_slots(TWO_SLOT_DECK, c.inventory[0]) == 1
    c.owned_programs.add(heavy.id)
    message = install_program(c, 0, heavy.id)  # needs 2 RAM, only 1 free
    assert "no free program slots" in message.lower()
    assert heavy.id not in c.inventory[0].installed_programs


def test_install_program_refuses_on_a_non_deck_item():
    weapon = _first_weapon()
    c = Character(name="t", cash=100_000)
    buy_item(c, weapon)
    buy_program(c, PROGRAM_A.id)
    message = install_program(c, 0, PROGRAM_A.id)
    assert "can't run programs" in message.lower()


def test_uninstall_program_removes_it_but_it_stays_owned():
    c = _char_with_deck(ONE_SLOT_DECK)
    buy_program(c, PROGRAM_A.id)
    install_program(c, 0, PROGRAM_A.id)
    message = uninstall_program(c, 0, PROGRAM_A.id)
    assert c.inventory[0].installed_programs == []
    assert PROGRAM_A.id in c.owned_programs  # still owned, just not installed
    assert PROGRAM_A.name in message


def test_uninstalled_program_can_be_installed_on_a_different_deck():
    c = _char_with_deck(ONE_SLOT_DECK)
    assert buy_item(c, TWO_SLOT_DECK)
    buy_program(c, PROGRAM_A.id)
    install_program(c, 0, PROGRAM_A.id)
    uninstall_program(c, 0, PROGRAM_A.id)
    message = install_program(c, 1, PROGRAM_A.id)
    assert c.inventory[1].installed_programs == [PROGRAM_A.id]
    assert PROGRAM_A.name in message


def test_equipped_travel_reduction_reads_only_the_equipped_vehicle():
    c = Character(name="t")
    assert equipped_travel_reduction(c.inventory) == 0.0
    c.inventory.append(InventoryItem("beater_bike", equipped=False))
    assert equipped_travel_reduction(c.inventory) == 0.0
    c.inventory[0].equipped = True
    assert equipped_travel_reduction(c.inventory) == 0.10
