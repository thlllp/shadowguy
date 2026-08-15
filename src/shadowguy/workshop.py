"""Making things rather than buying them: the Junkyard's scavenging run, and the
two bench actions a built workshop offers (fitting a Mod, cooking a Consumable).

Split out of shops.py, which is retail — everything there is gated on cash and
standing and adds a catalog entry to a Character. Nothing here is: a scavenge
costs only the trip, and both workshop actions roll a skill against materials
the runner picked out of somebody's scrap. Same three-way shape in all of them
(spend the time, roll, keep what you made), and none of it is a transaction.

Imports shops.py for the catalogs it works over (ITEMS_BY_ID, MODS_BY_ID, the
named weapon-mod layouts) and is imported only by screens.shop_screens —
shops.py never imports back, so the arrow stays one-way.
"""

import random
from typing import TYPE_CHECKING

from shadowguy.checks import CheckResult, resolve_check, resolve_rng
from shadowguy.shops import (
    CONSUMABLES_BY_ID,
    GUN_ONLY_MOD_SLOTS,
    ITEMS_BY_ID,
    MOD_SLOTS_PER_ITEM,
    MODS_BY_ID,
    SCAVENGE_MATERIALS,
    STOCK_MOD_ID_SET,
    WEAPON_MOD_SLOTS,
    InventoryItem,
    Item,
    Mod,
    grant_item,
)
from shadowguy.skills import skill_value

if TYPE_CHECKING:
    from shadowguy.character import Character

# A JUNKYARD (corpmap.LocationKind.JUNKYARD) is a rare, neutral-only spot with one
# action: pick through the scrap with the scrapper who works it. Rolled against
# Tinkering — an eye for what's actually repairable versus junk — at legwork's
# NEARBY_DIFFICULTY (11), since it's the same "casing a place" tier of check.
SCAVENGE_SKILL = "tinkering"
SCAVENGE_DIFFICULTY = 11
SCAVENGE_HOURS_COST = 4
SCAVENGE_CRITICAL_FINDS = 2


def scavenge(character: "Character", rng: random.Random | None = None) -> str:
    """Pick through a Junkyard's scrap. A made check turns up one random entry from
    SCAVENGE_MATERIALS; a critical turns up SCAVENGE_CRITICAL_FINDS distinct ones. A
    miss costs nothing but the trip — the caller still spends the time either way,
    same as a gig or a piece of legwork."""
    rng = resolve_rng(rng)
    roll = resolve_check(stat_value=skill_value(character, SCAVENGE_SKILL), difficulty=SCAVENGE_DIFFICULTY, rng=rng)
    if not roll.result.passed:
        return "Nothing but rust and rot."
    count = SCAVENGE_CRITICAL_FINDS if roll.result is CheckResult.CRITICAL_SUCCESS else 1
    found = rng.sample(SCAVENGE_MATERIALS, count)
    for item_id in found:
        grant_item(character, ITEMS_BY_ID[item_id], equipped=False)
    names = ", ".join(ITEMS_BY_ID[item_id].name for item_id in found)
    return f"You scavenge up: {names}."


def _material_count(character: "Character", material_id: str) -> int:
    return sum(1 for entry in character.inventory if entry.item_id == material_id)


def _has_materials(character: "Character", materials: dict[str, int]) -> bool:
    return all(
        _material_count(character, material_id) >= count for material_id, count in materials.items()
    )


def _consume_materials(character: "Character", materials: dict[str, int]) -> None:
    for material_id, count in materials.items():
        removed = 0
        for index in range(len(character.inventory) - 1, -1, -1):
            if removed >= count:
                break
            if character.inventory[index].item_id == material_id:
                character.inventory.pop(index)
                removed += 1


# A workshop's two actions (see corpmap.Location.workshop_built, screens.SafehouseScreen)
# both roll a logic skill DESIGN.md flags as otherwise unrolled, at the same difficulty
# tier legwork/scavenging use for a "working with what's in front of you" check. A failed
# roll spends nothing — same "costs nothing but the trip" shape as scavenge() — so a
# botched attempt doesn't also burn the materials it took to get here.
WORKSHOP_ARMORER_DIFFICULTY = 11
WORKSHOP_CHEMISTRY_DIFFICULTY = 11
# Time SafehouseScreen spends on a workshop action, made or missed — same shape as
# SCAVENGE_HOURS_COST (the caller spends it regardless of outcome). Hand-set shorter
# than a Junkyard trip: this is bench work at home, not a trip across town.
WORKSHOP_HOURS_COST = 2


def mod_slot_index(item: Item, mod: Mod) -> int | None:
    """Which position in InventoryItem.mods this mod occupies on this item, or None when
    the item has no named layout (the old flat, append-only list)."""
    weapon_slots = WEAPON_MOD_SLOTS.get(item.skill, ())
    if not weapon_slots or mod.weapon_slot not in weapon_slots:
        return None
    return weapon_slots.index(mod.weapon_slot)


def install_refusal(entry: InventoryItem, mod: Mod) -> str | None:
    """Why this mod can't go on this item, or None if it can — everything *except*
    affording it, which is a "you could, later" the workshop shows rather than hides.

    The single source for these rules. install_mod gates on it and SafehouseScreen
    filters its rows with it; when the two kept separate copies they drifted (the
    screen's named-layout branch never checked applies_to at all), and every new slot
    type had to be taught to both."""
    item = ITEMS_BY_ID[entry.item_id]
    if item.slot not in mod.applies_to:
        return f"{mod.name} doesn't fit {item.name}."
    if WEAPON_MOD_SLOTS.get(item.skill, ()):
        slot_index = mod_slot_index(item, mod)
        if slot_index is None:
            return f"{mod.name} doesn't fit {item.name}."
        if entry.mods[slot_index] == mod.id:
            return f"{item.name} already has {mod.name}."
        return None
    # No named layout, so nothing here has a slot to occupy: a stock part would burn a
    # capped slot for zero bonus, and gun furniture has no business on a blade or a bow.
    # Both would otherwise pass on applies_to alone.
    if mod.id in STOCK_MOD_ID_SET or mod.weapon_slot in GUN_ONLY_MOD_SLOTS:
        return f"{mod.name} doesn't fit {item.name}."
    if mod.id in entry.mods:
        return f"{item.name} already has {mod.name}."
    if len(entry.mods) >= MOD_SLOTS_PER_ITEM:
        return f"{item.name} has no free mod slots."
    return None


def install_mod(
    character: "Character", inventory_index: int, mod_id: str, rng: random.Random | None = None
) -> tuple[bool, str]:
    """Attach a Mod to the weapon/wearable at character.inventory[inventory_index].
    Caller (SafehouseScreen) gates this on the location's workshop_built.

    On a weapon skill with a named layout (WEAPON_MOD_SLOTS), mod_id's own
    weapon_slot picks which slot it goes into — installing always replaces whatever
    that slot currently holds (a stock part or a prior upgrade), including
    installing a stock mod back over an upgrade; there is no separate "remove" for
    these, since a named slot is never empty. Every other weapon/wearable keeps the
    old flat MOD_SLOTS_PER_ITEM-capped list (append-only, paired with remove_mod).

    Returns (attempted, message): attempted is False on a precondition the caller
    should treat as never having happened (wrong slot, already installed, no free
    slots, can't afford, missing materials) — no time should be spent on those, the
    same way HospitalScreen only spends time when hospital_stay doesn't return None.
    It's True once a roll is actually made, whether the roll passes or not."""
    entry = character.inventory[inventory_index]
    item = ITEMS_BY_ID[entry.item_id]
    mod = MODS_BY_ID[mod_id]
    refusal = install_refusal(entry, mod)
    if refusal is not None:
        return False, refusal
    slot_index = mod_slot_index(item, mod)
    if character.cash < mod.price:
        return False, f"Can't afford {mod.name} ({mod.price}eb)."
    if not _has_materials(character, mod.materials):
        return False, f"Not enough materials for {mod.name}."
    roll = resolve_check(
        stat_value=skill_value(character, "armorer"),
        difficulty=WORKSHOP_ARMORER_DIFFICULTY,
        rng=resolve_rng(rng),
    )
    if not roll.result.passed:
        return True, f"The {mod.name} install doesn't take — nothing spent, try again."
    character.cash -= mod.price
    _consume_materials(character, mod.materials)
    if slot_index is not None:
        entry.mods[slot_index] = mod_id
    else:
        entry.mods.append(mod_id)
    return True, f"Installed {mod.name} on {item.name}."


def remove_mod(character: "Character", inventory_index: int, mod_id: str) -> str:
    """Pull a Mod off character.inventory[inventory_index]. Free either way — same as
    uninstall_program — it's just labor, no skill check and nothing to lose.

    Refused outright on a weapon with a named layout: a named slot is never empty, so
    the way to undo an upgrade there is to install the matching stock part back over
    it. Shortening that list instead would leave every later slot index off by one and
    IndexError the next install (and SafehouseScreen's slot listing with it)."""
    entry = character.inventory[inventory_index]
    item = ITEMS_BY_ID[entry.item_id]
    if WEAPON_MOD_SLOTS.get(item.skill):
        return f"{item.name}'s slots are fixed — fit the stock part back instead."
    if mod_id not in entry.mods:
        return "Not installed there."
    entry.mods.remove(mod_id)
    return f"Removed {MODS_BY_ID[mod_id].name} from {item.name}."


# Which Consumables a workshop can craft, and the scavenged materials each one costs on
# top of a cash price (WORKSHOP_CRAFT_PRICE_FRACTION of retail — cheaper than buying, the
# materials make up the difference). Deliberately narrow to the chem-flavored
# TEMP_STAT_BOOST rows: health kits and grenades are the hooks DESIGN.md earmarks for
# Medicine's and Demolitions' own future checks, not this one.
CRAFT_RECIPES: dict[str, dict[str, int]] = {
    "chem_x": {"salvaged_optics": 1},
    "chem_y": {"wire": 1},
    "chem_x2": {"salvaged_optics": 1, "wire": 1},
}
WORKSHOP_CRAFT_PRICE_FRACTION = 0.5

for _recipe_id, _recipe_materials in CRAFT_RECIPES.items():
    for _material_id in _recipe_materials:
        if _material_id not in SCAVENGE_MATERIALS:
            raise ValueError(f"{_recipe_id}: {_material_id!r} is not a scavenged material")


def craft_consumable(
    character: "Character", consumable_id: str, rng: random.Random | None = None
) -> tuple[bool, str]:
    """Craft an already-owned-the-recipe-for Consumable from CRAFT_RECIPES. Caller
    (SafehouseScreen) gates this on the location's workshop_built.

    Returns (attempted, message) — see install_mod's docstring for the split."""
    if consumable_id not in CRAFT_RECIPES:
        return False, "Can't craft that here."
    consumable = CONSUMABLES_BY_ID[consumable_id]
    materials = CRAFT_RECIPES[consumable_id]
    price = round(consumable.price * WORKSHOP_CRAFT_PRICE_FRACTION)
    if character.cash < price:
        return False, f"Can't afford {consumable.name} ({price}eb)."
    if not _has_materials(character, materials):
        return False, f"Not enough materials for {consumable.name}."
    roll = resolve_check(
        stat_value=skill_value(character, "chemistry"),
        difficulty=WORKSHOP_CHEMISTRY_DIFFICULTY,
        rng=resolve_rng(rng),
    )
    if not roll.result.passed:
        return True, "The batch doesn't come together — nothing spent, try again."
    character.cash -= price
    _consume_materials(character, materials)
    character.consumables.append(consumable_id)
    return True, f"Crafted {consumable.name} for {price}eb."
