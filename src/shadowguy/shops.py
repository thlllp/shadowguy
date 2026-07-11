"""Retail LocationKinds: persistent stat-boosting gear the runner buys with Cash.

These can land on neutral ground or in a corp district's non-specialty slot (see
corpmap.FILLER_KINDS), so a job can target one — corpmap.LOCATION_STAT and
jobs.LEGWORK_APPROACH_TEXT each have an entry for every SHOP_KINDS member to
cover that.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadowguy.corpmap import SHOP_KINDS, LocationKind

if TYPE_CHECKING:
    from shadowguy.character import Character

# What a Pawn Shop pays back on an item, relative to its catalog price.
PAWN_SELL_FRACTION = 0.5


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    price: int
    stat: str  # body, skill, or cool — the check this item's bonus applies to
    bonus: int


CATALOG: dict[LocationKind, list[Item]] = {
    LocationKind.WEAPON_SHOP: [
        Item(id="brass_knuckles", name="Brass Knuckles", price=150, stat="body", bonus=1),
        Item(id="combat_knife", name="Combat Knife", price=400, stat="body", bonus=2),
        Item(id="smart_pistol", name="Smart Pistol", price=900, stat="body", bonus=3),
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
        Item(id="pawned_knuckles", name="Pawned Knuckles", price=80, stat="body", bonus=1),
        Item(id="pawned_deck", name="Pawned Deck", price=80, stat="skill", bonus=1),
        Item(id="pawned_charm", name="Pawned Lucky Charm", price=80, stat="cool", bonus=1),
    ],
}

ITEMS_BY_ID = {item.id: item for items in CATALOG.values() for item in items}

# Import-time guard, same pattern as corpmap.py's own tuning-constant checks:
# a shop LocationKind with no catalog would silently show an empty shop
# (ShopScreen's CATALOG.get(..., [])) instead of a clear failure.
if set(CATALOG) != set(SHOP_KINDS):
    raise ValueError("CATALOG must have exactly one entry per corpmap.SHOP_KINDS")


def equipped_bonus(inventory: list[str], stat: str) -> int:
    return sum(ITEMS_BY_ID[item_id].bonus for item_id in inventory if ITEMS_BY_ID[item_id].stat == stat)


def buy_item(character: "Character", item: Item) -> bool:
    if character.cash < item.price:
        return False
    character.cash -= item.price
    character.inventory.append(item.id)
    return True


def sell_item(character: "Character", index: int) -> int:
    # By index, not id: the same item id can be owned more than once.
    item_id = character.inventory.pop(index)
    proceeds = int(ITEMS_BY_ID[item_id].price * PAWN_SELL_FRACTION)
    character.cash += proceeds
    return proceeds
