"""Retail LocationKinds: gear and consumables the runner buys with Cash.

Item is persistent stat-boosting gear (bought once, equipped/stowed, never
used up). Consumable is single-use: buying it appends to Character.consumables,
and using it pops that entry and applies a one-off effect (heal, restore
stamina, or a temporary stat boost that clears on Character.rest()).

These locations can land on neutral ground or in a corp district's
non-specialty slot (see corpmap.FILLER_KINDS), so a job can target one —
corpmap.LOCATION_SKILL and jobs.LEGWORK_APPROACH_TEXT each have an entry for
every SHOP_KINDS member to cover that.
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
    bonuses: dict[str, int]  # stat name (see character.CORE_STATS) -> bonus applied to that check
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


def bonus_text(item: Item) -> str:
    return ", ".join(f"+{bonus} {stat.capitalize()}" for stat, bonus in item.bonuses.items())


class EffectKind(Enum):
    HEAL = "heal"
    RESTORE_STAMINA = "restore_stamina"
    TEMP_STAT_BOOST = "temp_stat_boost"  # stat name comes from Consumable.stat
    NONE = "none"  # no mechanical effect yet (grenades: no combat system to target)


@dataclass(frozen=True)
class Consumable:
    id: str
    name: str
    price: int
    effect: EffectKind
    amount: int = 0
    stat: str | None = None  # only set when effect is TEMP_STAT_BOOST


# id, name, price, bonuses, slot
_CATALOG_ROWS: dict[LocationKind, list[tuple[str, str, int, dict[str, int], Slot | None]]] = {
    LocationKind.WEAPON_SHOP: [
        ("brass_knuckles", "Brass Knuckles", 150, {"body": 1}, Slot.WEAPON),
        ("combat_knife", "Combat Knife", 400, {"body": 2}, Slot.WEAPON),
        ("smart_pistol", "Smart Pistol", 900, {"body": 3}, Slot.WEAPON),
    ],
    LocationKind.AUTO_DEALER: [
        ("beater_bike", "Beater Bike", 200, {"cool": 1}, None),
        ("tuned_coupe", "Tuned Coupe", 500, {"cool": 2}, None),
        ("armored_towncar", "Armored Towncar", 1000, {"cool": 3}, None),
    ],
    # Persistent +body gear moved out in favor of the consumables below.
    LocationKind.PHARMACY: [],
    LocationKind.COMPUTER_STORE: [
        ("burner_deck", "Burner Deck", 200, {"intelligence": 1}, None),
        ("cracked_cyberdeck", "Cracked Cyberdeck", 500, {"intelligence": 2}, None),
        ("zetatech_rig", "Zetatech Rig", 1000, {"intelligence": 3}, None),
    ],
    LocationKind.PAWN: [
        ("pawned_knuckles", "Pawned Knuckles", 80, {"body": 1}, Slot.WEAPON),
        ("pawned_deck", "Pawned Deck", 80, {"intelligence": 1}, None),
        ("pawned_charm", "Pawned Lucky Charm", 80, {"cool": 1}, Slot.ACCESSORY),
    ],
}

CATALOG: dict[LocationKind, list[Item]] = {
    kind: [Item(*row) for row in rows] for kind, rows in _CATALOG_ROWS.items()
}

ITEMS_BY_ID = {item.id: item for items in CATALOG.values() for item in items}

# Import-time guard, same pattern as corpmap.py's own tuning-constant checks:
# a shop LocationKind with no catalog would silently show an empty shop
# (ShopScreen's CATALOG.get(..., [])) instead of a clear failure.
if set(CATALOG) != set(SHOP_KINDS):
    raise ValueError("CATALOG must have exactly one entry per corpmap.SHOP_KINDS")

if any(item.two_handed and item.slot is not Slot.WEAPON for item in ITEMS_BY_ID.values()):
    raise ValueError("two_handed items must have slot=Slot.WEAPON")


# id, name, price, effect, amount, stat
_CONSUMABLE_ROWS: dict[LocationKind, list[tuple[str, str, int, EffectKind, int, str | None]]] = {
    LocationKind.PHARMACY: [
        ("health_kit", "Health Kit", 100, EffectKind.HEAL, 5, None),
        ("energy_drink", "Energy Drink", 60, EffectKind.RESTORE_STAMINA, 2, None),
        ("chem_x", "Chem X", 150, EffectKind.TEMP_STAT_BOOST, 2, "body"),
        ("chem_y", "Chem Y", 150, EffectKind.TEMP_STAT_BOOST, 2, "intelligence"),
    ],
    LocationKind.WEAPON_SHOP: [
        ("grenade_smoke", "Smoke Grenade", 100, EffectKind.NONE, 0, None),
        ("grenade_flash", "Flash Grenade", 120, EffectKind.NONE, 0, None),
        ("grenade_frag", "Fragmentation Grenade", 200, EffectKind.NONE, 0, None),
    ],
}

CONSUMABLE_CATALOG: dict[LocationKind, list[Consumable]] = {
    kind: [Consumable(*row) for row in rows] for kind, rows in _CONSUMABLE_ROWS.items()
}

CONSUMABLES_BY_ID = {c.id: c for items in CONSUMABLE_CATALOG.values() for c in items}


def equipped_bonus(inventory: list[InventoryItem], stat: str) -> int:
    return sum(
        ITEMS_BY_ID[entry.item_id].bonuses.get(stat, 0) for entry in inventory if entry.equipped
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


def buy_consumable(character: "Character", consumable: Consumable) -> bool:
    if character.cash < consumable.price:
        return False
    character.cash -= consumable.price
    character.consumables.append(consumable.id)
    return True


def use_consumable(character: "Character", index: int) -> str:
    """Pop and apply consumables[index]. Returns a short message describing the effect."""
    consumable = CONSUMABLES_BY_ID[character.consumables.pop(index)]
    if consumable.effect is EffectKind.HEAL:
        character.adjust_health(consumable.amount)
        return f"+{consumable.amount} Health"
    if consumable.effect is EffectKind.RESTORE_STAMINA:
        character.restore_stamina(consumable.amount)
        return f"+{consumable.amount} Stamina"
    if consumable.effect is EffectKind.TEMP_STAT_BOOST:
        character.add_temp_bonus(consumable.stat, consumable.amount)
        return f"+{consumable.amount} {consumable.stat.capitalize()} until next rest"
    return "No effect yet."


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
