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
    bonuses: dict[str, int]  # stat name (body, skill, cool) -> bonus applied to that check


# id, name, price, bonuses
_CATALOG_ROWS: dict[LocationKind, list[tuple[str, str, int, dict[str, int]]]] = {
    LocationKind.WEAPON_SHOP: [
        ("brass_knuckles", "Brass Knuckles", 150, {"body": 1}),
        ("combat_knife", "Combat Knife", 400, {"body": 2}),
        ("smart_pistol", "Smart Pistol", 900, {"body": 3}),
    ],
    LocationKind.AUTO_DEALER: [
        ("beater_bike", "Beater Bike", 200, {"cool": 1}),
        ("tuned_coupe", "Tuned Coupe", 500, {"cool": 2}),
        ("armored_towncar", "Armored Towncar", 1000, {"cool": 3}),
    ],
    LocationKind.PHARMACY: [
        ("synth_adrenal_patch", "Synth-Adrenal Patch", 180, {"body": 1}),
        ("nerve_booster", "Nerve Booster", 450, {"body": 2}),
        ("militech_combat_stim", "Militech Combat Stim", 950, {"body": 3}),
    ],
    LocationKind.COMPUTER_STORE: [
        ("burner_deck", "Burner Deck", 200, {"skill": 1}),
        ("cracked_cyberdeck", "Cracked Cyberdeck", 500, {"skill": 2}),
        ("zetatech_rig", "Zetatech Rig", 1000, {"skill": 3}),
    ],
    LocationKind.PAWN: [
        ("pawned_knuckles", "Pawned Knuckles", 80, {"body": 1}),
        ("pawned_deck", "Pawned Deck", 80, {"skill": 1}),
        ("pawned_charm", "Pawned Lucky Charm", 80, {"cool": 1}),
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


def equipped_bonus(inventory: list[str], stat: str) -> int:
    return sum(ITEMS_BY_ID[item_id].bonuses.get(stat, 0) for item_id in inventory)


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
