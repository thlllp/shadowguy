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
from shadowguy.skills import skill_for

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
    # The next two are what make a weapon a weapon, and are meaningless (and
    # rejected) on anything else: `skill` is the skill id its attack rolls, and
    # `damage` is the health it takes off an enemy on a hit. This is the only place
    # a weapon's combat profile is written — combat.py reads it rather than keeping
    # a second table of its own that would have to agree with this one.
    skill: str | None = None
    damage: int = 0
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
    # The three below only mean anything inside a fight, and are spent by combat.py
    # rather than by use_consumable — see COMBAT_ONLY_EFFECTS.
    COMBAT_DAMAGE_ALL = "combat_damage_all"  # amount = health off every standing enemy
    COMBAT_STUN = "combat_stun"  # amount = rounds the enemies lose
    COMBAT_ESCAPE = "combat_escape"  # walk out of the fight, no check


# Effects with nothing to act on outside a fight, and equally the *only* things
# reachable from inside one. use_consumable refuses these without spending them (a
# grenade thrown at no one is a grenade wasted), and combat.py is the only thing that
# resolves them.
#
# Healing is deliberately not in here, and it's the interesting exclusion: a Health Kit
# is the obvious combat item in most games, but health comes back slowly in this one, so
# a fight would be the cheapest possible place to spend a kit — top up, swing again, top
# up. That turns a fight from a thing you survive into a thing you grind, and it makes
# health (the resource the whole damage curve is denominated in) refundable mid-encounter.
# You patch yourself up *after*, on your own time. Stamina and chems are out for the same
# reason: a fight is not the place to come up on a stim.
COMBAT_ONLY_EFFECTS = frozenset(
    {EffectKind.COMBAT_DAMAGE_ALL, EffectKind.COMBAT_STUN, EffectKind.COMBAT_ESCAPE}
)


@dataclass(frozen=True)
class Consumable:
    id: str
    name: str
    price: int
    effect: EffectKind
    amount: int = 0
    stat: str | None = None  # only set when effect is TEMP_STAT_BOOST


# id, name, price, bonuses, slot, then for weapons only: skill, damage, two_handed.
# A weapon's skill is what its attack rolls in combat and its damage is what a hit
# takes off — the pool spans blunt/short_blade/long_blade/firearms, so no build is
# stuck swinging a weapon it can't use. The bloodiest one is two-handed, which costs
# both weapon slots (SLOT_CAPACITY).
_CATALOG_ROWS: dict[LocationKind, list[tuple]] = {
    LocationKind.WEAPON_SHOP: [
        ("brass_knuckles", "Brass Knuckles", 150, {"body": 1}, Slot.WEAPON, "blunt", 3),
        ("combat_knife", "Combat Knife", 400, {"body": 2}, Slot.WEAPON, "short_blade", 4),
        ("monoblade", "Monoblade", 700, {"body": 2}, Slot.WEAPON, "long_blade", 6, True),
        ("smart_pistol", "Smart Pistol", 900, {"body": 3}, Slot.WEAPON, "firearms", 5),
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
        ("pawned_knuckles", "Pawned Knuckles", 80, {"body": 1}, Slot.WEAPON, "blunt", 2),
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

# A weapon's combat profile and its slot have to agree, both ways round. A Slot.WEAPON
# item with no skill/damage would be an attack the combat screen can offer but never
# resolve; a skill or damage on a non-weapon would be a combat profile nothing can ever
# reach, since combat only ever swings what's equipped in Slot.WEAPON. The skill id goes
# through skill_for, so a typo'd weapon skill fails on import rather than mid-fight.
for _item in ITEMS_BY_ID.values():
    _is_weapon = _item.slot is Slot.WEAPON
    if _is_weapon and (_item.skill is None or _item.damage <= 0):
        raise ValueError(f"{_item.id}: a Slot.WEAPON item needs a skill and damage to attack with")
    if not _is_weapon and (_item.skill is not None or _item.damage):
        raise ValueError(f"{_item.id}: only a Slot.WEAPON item can have a combat skill or damage")
    if _item.skill is not None:
        skill_for(_item.skill)


# id, name, price, effect, amount, stat
_CONSUMABLE_ROWS: dict[LocationKind, list[tuple[str, str, int, EffectKind, int, str | None]]] = {
    LocationKind.PHARMACY: [
        ("health_kit", "Health Kit", 100, EffectKind.HEAL, 5, None),
        ("energy_drink", "Energy Drink", 60, EffectKind.RESTORE_STAMINA, 2, None),
        ("chem_x", "Chem X", 150, EffectKind.TEMP_STAT_BOOST, 2, "body"),
        ("chem_y", "Chem Y", 150, EffectKind.TEMP_STAT_BOOST, 2, "intelligence"),
    ],
    LocationKind.WEAPON_SHOP: [
        ("grenade_smoke", "Smoke Grenade", 100, EffectKind.COMBAT_ESCAPE, 0, None),
        ("grenade_flash", "Flash Grenade", 120, EffectKind.COMBAT_STUN, 1, None),
        ("grenade_frag", "Fragmentation Grenade", 200, EffectKind.COMBAT_DAMAGE_ALL, 5, None),
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
    """Pop and apply consumables[index]. Returns a short message describing the effect.

    A combat-only consumable is refused rather than spent: there is nothing to throw a
    grenade at out here, and popping it first would burn it for the message.
    """
    consumable = CONSUMABLES_BY_ID[character.consumables[index]]
    if consumable.effect in COMBAT_ONLY_EFFECTS:
        return "Only useful in a fight."
    character.consumables.pop(index)
    if consumable.effect is EffectKind.HEAL:
        character.adjust_health(consumable.amount)
        return f"+{consumable.amount} Health"
    if consumable.effect is EffectKind.RESTORE_STAMINA:
        character.restore_stamina(consumable.amount)
        return f"+{consumable.amount} Stamina"
    if consumable.effect is EffectKind.TEMP_STAT_BOOST:
        character.add_temp_bonus(consumable.stat, consumable.amount)
        return f"+{consumable.amount} {consumable.stat.capitalize()} until next rest"
    # Every non-combat effect is handled above; a new EffectKind that is neither
    # listed in COMBAT_ONLY_EFFECTS nor given a branch here would otherwise be silently
    # eaten (the item spent, nothing applied).
    raise ValueError(f"consumable effect not handled out of combat: {consumable.effect}")


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
