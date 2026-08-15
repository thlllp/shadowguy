"""Tests for inventory.py: equip state, cyberdeck programs, and using a consumable."""

from helpers import character_with_skill_value

from shadowguy.character import Character
from shadowguy.inventory import (
    active_deck_entry,
    equipped_travel_reduction,
    free_passive_slots,
    free_program_slots,
    free_ram,
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
# Action programs specifically: passives draw on Item.passive_slots, a separate pool,
# so a capacity test built on one would be measuring the wrong budget. (Picking the
# first two ids outright used to work, and stopped the day the catalog grew a passive
# whose id sorts second.)
_ACTION_PROGRAMS = sorted(
    (p for p in PROGRAMS_BY_ID.values() if not p.is_passive), key=lambda p: p.id
)
PROGRAM_A = _ACTION_PROGRAMS[0]
PROGRAM_B = _ACTION_PROGRAMS[1]
PASSIVE_PROGRAM = sorted(
    (p for p in PROGRAMS_BY_ID.values() if p.is_passive), key=lambda p: p.id
)[0]


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


def test_every_deck_carries_a_passive_slot_on_top_of_its_program_slots():
    """The floor, asserted over the whole catalog rather than the two decks these
    tests happen to use: a deck with nowhere to put a passive would make the entire
    passive branch unreachable on that rung of the ladder."""
    decks = [item for item in ITEMS_BY_ID.values() if item.program_slots]
    assert decks
    assert all(deck.passive_slots >= 1 for deck in decks)
    assert all(item.passive_slots == 0 for item in ITEMS_BY_ID.values() if not item.program_slots)


def test_a_passive_does_not_consume_an_action_slot():
    """The whole point of the separate pool. A Burner Deck has one program slot; a
    passive installed on it must leave that slot free for an action program."""
    c = _char_with_deck(ONE_SLOT_DECK)
    buy_program(c, PASSIVE_PROGRAM.id)
    buy_program(c, PROGRAM_A.id)
    assert install_program(c, 0, PASSIVE_PROGRAM.id).startswith("Installed")
    assert free_program_slots(ONE_SLOT_DECK, c.inventory[0]) == ONE_SLOT_DECK.program_slots
    assert free_passive_slots(ONE_SLOT_DECK, c.inventory[0]) == 0
    # ...and the action program still fits alongside it.
    assert install_program(c, 0, PROGRAM_A.id).startswith("Installed")
    assert set(c.inventory[0].installed_programs) == {PASSIVE_PROGRAM.id, PROGRAM_A.id}


def test_a_second_passive_is_refused_even_with_action_slots_free(monkeypatch):
    """The pools don't spill into each other in either direction: a full passive slot
    can't borrow the action capacity sitting empty beside it."""
    other = Program(id="test_passive_two", name="Test Passive Two", price=0, soak_bonus=1)
    monkeypatch.setitem(PROGRAMS_BY_ID, other.id, other)
    c = _char_with_deck(TWO_SLOT_DECK)  # 2 action slots, 1 passive
    buy_program(c, PASSIVE_PROGRAM.id)
    c.owned_programs.add(other.id)
    install_program(c, 0, PASSIVE_PROGRAM.id)
    message = install_program(c, 0, other.id)
    assert "no free passive slots" in message.lower()
    assert free_program_slots(TWO_SLOT_DECK, c.inventory[0]) == 2  # untouched
    assert other.id not in c.inventory[0].installed_programs


def test_install_program_refuses_beyond_capacity():
    c = _char_with_deck(ONE_SLOT_DECK)  # 1 slot
    buy_program(c, PROGRAM_A.id)
    buy_program(c, PROGRAM_B.id)
    install_program(c, 0, PROGRAM_A.id)
    message = install_program(c, 0, PROGRAM_B.id)
    assert c.inventory[0].installed_programs == [PROGRAM_A.id]
    assert "no free program slots" in message.lower()


def _char_with_deck_zero_computer(deck=ONE_SLOT_DECK, cash=100_000):
    """Like _char_with_deck, but Computer forced to exactly 0 -- so
    inventory.effective_ram_max(character, item) == item.ram_max, with no skill bonus
    to account for. RAM tests need that determinism; the plain slot tests above don't
    care, so they keep using the ordinary fresh Character."""
    c = character_with_skill_value("computer", 0)
    c.cash = cash
    assert buy_item(c, deck)
    return c


def test_program_ram_cost_is_charged_against_ram_not_slots(monkeypatch):
    """Program.ram_cost gates a deck's RAM budget (free_ram/Item.ram_max) -- a pool
    separate from program_slots, which is now a flat one-slot-per-program count and
    doesn't care how heavy a program is. Built with a synthetic program whose ram_cost
    exactly fills TWO_SLOT_DECK's ram_max, to prove free_ram actually sums ram_cost
    rather than free_program_slots absorbing it the way it used to."""
    heavy = Program(
        id="test_heavy", name="Test Heavy", price=0, ram_cost=TWO_SLOT_DECK.ram_max,
        uses_per_fight=3, action_damage=1,  # action-shaped: this is a program_slots test
    )
    monkeypatch.setitem(PROGRAMS_BY_ID, heavy.id, heavy)
    c = _char_with_deck_zero_computer(TWO_SLOT_DECK)
    entry = c.inventory[0]
    c.owned_programs.add(heavy.id)
    assert free_program_slots(TWO_SLOT_DECK, entry) == 2
    assert free_ram(c, TWO_SLOT_DECK, entry) == TWO_SLOT_DECK.ram_max
    install_program(c, 0, heavy.id)
    assert free_program_slots(TWO_SLOT_DECK, entry) == 1  # one slot gone, flat count
    assert free_ram(c, TWO_SLOT_DECK, entry) == 0  # ram_cost fully spent the RAM budget
    buy_program(c, PROGRAM_A.id)
    message = install_program(c, 0, PROGRAM_A.id)
    assert "ram" in message.lower()
    assert PROGRAM_A.id not in entry.installed_programs
    assert free_program_slots(TWO_SLOT_DECK, entry) == 1  # the free slot was never the blocker


def test_install_program_refuses_when_ram_cost_exceeds_partial_free_ram(monkeypatch):
    """The weaker, easier-to-miss case than "no RAM at all": free_ram can be positive
    (some room left) but still less than the incoming program's own ram_cost, and
    install_program must refuse that too, not just the exactly-full or
    completely-empty cases -- even though a program_slots slot is sitting free."""
    heavy = Program(
        id="test_heavy", name="Test Heavy", price=0, ram_cost=TWO_SLOT_DECK.ram_max,
        uses_per_fight=3, action_damage=1,  # action-shaped: this is a program_slots test
    )
    monkeypatch.setitem(PROGRAMS_BY_ID, heavy.id, heavy)
    c = _char_with_deck_zero_computer(TWO_SLOT_DECK)  # 2 slots
    entry = c.inventory[0]
    buy_program(c, PROGRAM_A.id)
    install_program(c, 0, PROGRAM_A.id)  # spends PROGRAM_A.ram_cost, leaving some RAM free
    free_before = free_ram(c, TWO_SLOT_DECK, entry)
    assert 0 < free_before < heavy.ram_cost
    assert free_program_slots(TWO_SLOT_DECK, entry) == 1  # a slot is free
    c.owned_programs.add(heavy.id)
    message = install_program(c, 0, heavy.id)  # needs more RAM than is left
    assert "ram" in message.lower()
    assert heavy.id not in entry.installed_programs
    assert free_program_slots(TWO_SLOT_DECK, entry) == 1  # untouched by the RAM refusal


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
