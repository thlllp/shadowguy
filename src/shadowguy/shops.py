"""Retail LocationKinds: persistent stat-boosting gear the runner buys with Cash.

These can land on neutral ground or in a corp district's non-specialty slot (see
corpmap.FILLER_KINDS), so a job can target one — corpmap.LOCATION_STAT and
jobs.LEGWORK_APPROACH_TEXT each have an entry for every SHOP_KINDS member to
cover that.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from shadowguy.corpmap import SHOP_KINDS, LocationKind

if TYPE_CHECKING:
    from shadowguy.character import Character

# What a Pawn Shop pays back on an item, relative to its catalog price.
PAWN_SELL_FRACTION = 0.5


class Slot(Enum):
    HEADWEAR = "headwear"
    FACEWEAR = "facewear"
    TORSO = "torso"
    LEGS = "legs"
    BOOTS = "boots"
    ACCESSORY = "accessory"
    WEAPON = "weapon"


# How many items can be equipped in each slot at once. WEAPON is 2 (e.g. a
# main-hand and off-hand piece) rather than 1 like the wearable slots, so a
# two-handed weapon (Item.two_handed) costs both.
SLOT_CAPACITY: dict[Slot, int] = {
    Slot.HEADWEAR: 1,
    Slot.FACEWEAR: 1,
    Slot.TORSO: 1,
    Slot.LEGS: 1,
    Slot.BOOTS: 1,
    Slot.ACCESSORY: 1,
    Slot.WEAPON: 2,
}


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    price: int
    stat: str  # body, skill, or cool — the check this item's bonus applies to
    bonus: int
    # None = unlimited (vehicles, chems, cyberdecks aren't worn, so any number
    # can be equipped at once). Wearables/weapons draw from SLOT_CAPACITY.
    slot: Slot | None = None
    # Only meaningful when slot is Slot.WEAPON: occupies both weapon slots.
    two_handed: bool = False


@dataclass
class InventoryItem:
    item_id: str
    # Only equipped items contribute their bonus (see equipped_bonus below).
    equipped: bool = True


CATALOG: dict[LocationKind, list[Item]] = {
    LocationKind.WEAPON_SHOP: [
        Item(id="brass_knuckles", name="Brass Knuckles", price=150, stat="body", bonus=1, slot=Slot.WEAPON),
        Item(id="combat_knife", name="Combat Knife", price=400, stat="body", bonus=2, slot=Slot.WEAPON),
        Item(id="smart_pistol", name="Smart Pistol", price=900, stat="body", bonus=3, slot=Slot.WEAPON),
    ],
    LocationKind.AUTO_DEALER: [
        Item(id="beater_bike", name="Beater Bike", price=200, stat="cool", bonus=1),
        Item(id="tuned_coupe", name="Tuned Coupe", price=500, stat="cool", bonus=2),
        Item(id="armored_towncar", name="Armored Towncar", price=1000, stat="cool", bonus=3),
    ],
    LocationKind.PHARMACY: [
        Item(id="synth_adrenal_patch", name="Synth-Adrenal Patch", price=180, stat="body", bonus=1),
        Item(id="nerve_booster", name="Nerve Booster", price=450, stat="body", bonus=2),
        Item(id="militech_combat_stim", name="Militech Combat Stim", price=950, stat="body", bonus=3),
    ],
    LocationKind.COMPUTER_STORE: [
        Item(id="burner_deck", name="Burner Deck", price=200, stat="skill", bonus=1),
        Item(id="cracked_cyberdeck", name="Cracked Cyberdeck", price=500, stat="skill", bonus=2),
        Item(id="zetatech_rig", name="Zetatech Rig", price=1000, stat="skill", bonus=3),
    ],
    LocationKind.PAWN: [
        Item(id="pawned_knuckles", name="Pawned Knuckles", price=80, stat="body", bonus=1, slot=Slot.WEAPON),
        Item(id="pawned_deck", name="Pawned Deck", price=80, stat="skill", bonus=1),
        Item(id="pawned_charm", name="Pawned Lucky Charm", price=80, stat="cool", bonus=1, slot=Slot.ACCESSORY),
    ],
}

ITEMS_BY_ID = {item.id: item for items in CATALOG.values() for item in items}

# Import-time guard, same pattern as corpmap.py's own tuning-constant checks:
# a shop LocationKind with no catalog would silently show an empty shop
# (ShopScreen's CATALOG.get(..., [])) instead of a clear failure.
if set(CATALOG) != set(SHOP_KINDS):
    raise ValueError("CATALOG must have exactly one entry per corpmap.SHOP_KINDS")

if any(item.two_handed and item.slot is not Slot.WEAPON for item in ITEMS_BY_ID.values()):
    raise ValueError("two_handed items must have slot=Slot.WEAPON")


def equipped_bonus(inventory: list[InventoryItem], stat: str) -> int:
    return sum(
        ITEMS_BY_ID[entry.item_id].bonus
        for entry in inventory
        if entry.equipped and ITEMS_BY_ID[entry.item_id].stat == stat
    )


def _slot_cost(item: Item) -> int:
    return 2 if item.two_handed else 1


def slot_usage(inventory: list[InventoryItem], slot: Slot) -> int:
    return sum(
        _slot_cost(ITEMS_BY_ID[entry.item_id])
        for entry in inventory
        if entry.equipped and ITEMS_BY_ID[entry.item_id].slot is slot
    )


def _fits_in_slot(inventory: list[InventoryItem], item: Item) -> bool:
    if item.slot is None:
        return True
    return slot_usage(inventory, item.slot) + _slot_cost(item) <= SLOT_CAPACITY[item.slot]


def buy_item(character: "Character", item: Item) -> bool:
    if character.cash < item.price:
        return False
    character.cash -= item.price
    # Auto-equip only if there's room; otherwise it's bought stowed and the
    # player equips it manually (swapping out whatever's occupying the slot).
    entry = InventoryItem(item.id, equipped=_fits_in_slot(character.inventory, item))
    character.inventory.append(entry)
    return True


def sell_item(character: "Character", index: int) -> int:
    # By index, not id: the same item id can be owned more than once.
    entry = character.inventory.pop(index)
    proceeds = int(ITEMS_BY_ID[entry.item_id].price * PAWN_SELL_FRACTION)
    character.cash += proceeds
    return proceeds


def toggle_equip(character: "Character", index: int) -> bool:
    """Flip the equipped state of inventory[index].

    Unequipping always succeeds. Equipping fails (returns False, no change)
    if it would exceed that item's slot capacity.
    """
    entry = character.inventory[index]
    if entry.equipped:
        entry.equipped = False
        return True

    item = ITEMS_BY_ID[entry.item_id]
    if not _fits_in_slot(character.inventory, item):
        return False
    entry.equipped = True
    return True
