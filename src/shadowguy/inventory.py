"""What a runner's inventory is doing right now: equip state, deck programs, and
popping a consumable.

shops.py owns what exists and what it costs (Item/Consumable/Program, the catalogs,
pricing, and the buy/sell transactions that add or remove a Character's inventory
entries). This module owns everything downstream of that: which of a Character's
InventoryItem entries currently contribute their bonus, which deck is active and
what's installed on it, and using up an already-owned consumable. The dependency
is one-way (this module imports from shops.py; shops.py never imports this one) —
buy_item still needs a slot-capacity check, so that stays a shops.py-only concern
(slot_usage/fits_in_slot) rather than round-tripping back here.
"""

from typing import TYPE_CHECKING

from shadowguy.shops import (
    COMBAT_ONLY_EFFECTS,
    CONSUMABLES_BY_ID,
    EffectKind,
    Item,
    ITEMS_BY_ID,
    InventoryItem,
    Program,
    PROGRAMS_BY_ID,
    equipped_items,
    fits_in_slot,
)

if TYPE_CHECKING:
    from shadowguy.character import Character


def equipped_bonus(inventory: list[InventoryItem], stat: str) -> int:
    return sum(item.bonuses.get(stat, 0) for item in equipped_items(inventory))


def equipped_defense(inventory: list[InventoryItem]) -> int:
    return sum(item.defense for item in equipped_items(inventory))


def equipped_skill_bonus(inventory: list[InventoryItem], skill_id: str) -> int:
    return sum(item.skill_bonuses.get(skill_id, 0) for item in equipped_items(inventory))


def equipped_travel_reduction(inventory: list[InventoryItem]) -> float:
    return sum(item.travel_reduction for item in equipped_items(inventory))


def active_deck_entry(inventory: list[InventoryItem]) -> tuple[InventoryItem, Item] | None:
    """The equipped deck with the best Intelligence bonus (ties: first found), or None if
    the runner has no deck equipped. This is *which* deck equipped_deck_rating's number
    comes from, and — since a matrix fight only ever rides on one deck — the one whose
    installed_programs (Item.program_slots) actually matter this fight."""
    best: tuple[InventoryItem, Item] | None = None
    best_rating = -1
    for entry in inventory:
        if not entry.equipped:
            continue
        item = ITEMS_BY_ID[entry.item_id]
        if item.slot is not None:
            continue
        rating = item.bonuses.get("intelligence", 0)
        if rating > best_rating:
            best, best_rating = (entry, item), rating
    return best


def equipped_deck_rating(inventory: list[InventoryItem]) -> int:
    """The best equipped cyberdeck's matrix strength, or 0 if the runner is jacking in
    bare-handed. A cyberdeck is a Slot None item (see Slot / Item.slot: decks aren't
    worn, so any number can be equipped) — burner_deck, cracked_cyberdeck, zetatech_rig,
    pawned_deck today — and its rating *is* its Intelligence bonus, the same number that
    makes a better deck a better hacker. matrix.py reads this the way combat.py reads a
    weapon's damage: it's the deck, not the skill, that decides what a landed intrusion
    costs the ICE, so a runner with no deck can still fight in the matrix, just weakly."""
    entry = active_deck_entry(inventory)
    return entry[1].bonuses.get("intelligence", 0) if entry else 0


def installed_programs_for(entry: InventoryItem) -> list[Program]:
    """Resolve entry.installed_programs to their Program objects, skipping unknown ids."""
    return [PROGRAMS_BY_ID[pid] for pid in entry.installed_programs if pid in PROGRAMS_BY_ID]


def free_program_slots(item: Item, entry: InventoryItem) -> int:
    """How much of this deck's program_slots capacity is still free. Spent in
    Program.ram_cost per installed program, not a flat one-program-per-slot count —
    every program costs 1 RAM today, so this reads identically to a plain count until
    something costs more."""
    used = sum(program.ram_cost for program in installed_programs_for(entry))
    return item.program_slots - used


def install_program(character: "Character", inventory_index: int, program_id: str) -> str:
    """Install an owned Program onto inventory[inventory_index] (must be a deck with a
    free slot). Free and instant — capacity is the only gate, no skill check."""
    if program_id not in character.owned_programs:
        return "You don't own that program."
    entry = character.inventory[inventory_index]
    item = ITEMS_BY_ID[entry.item_id]
    if item.program_slots <= 0:
        return f"{item.name} can't run programs."
    program = PROGRAMS_BY_ID[program_id]
    if program_id in entry.installed_programs:
        return f"{program.name} is already installed on {item.name}."
    if program.ram_cost > free_program_slots(item, entry):
        return f"{item.name} has no free program slots."
    entry.installed_programs.append(program_id)
    return f"Installed {program.name} on {item.name}."


def uninstall_program(character: "Character", inventory_index: int, program_id: str) -> str:
    """Pull a Program off inventory[inventory_index]. Free either way — it stays in the
    owned pool, ready to install on a different deck."""
    entry = character.inventory[inventory_index]
    if program_id not in entry.installed_programs:
        return "Not installed there."
    entry.installed_programs.remove(program_id)
    item = ITEMS_BY_ID[entry.item_id]
    return f"Uninstalled {PROGRAMS_BY_ID[program_id].name} from {item.name}."


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
    if not fits_in_slot(character.inventory, item):
        return False
    entry.equipped = True
    return True


def use_consumable(character: "Character", index: int) -> str:
    """Pop and apply consumables[index]. Returns a short message describing the effect.

    A combat-only consumable is refused rather than spent: there is nothing to throw a
    grenade at out here, and popping it first would burn it for the message.
    """
    consumable = CONSUMABLES_BY_ID[character.consumables[index]]
    if consumable.effect in COMBAT_ONLY_EFFECTS:
        return "Only useful in a fight."
    # A Health Kit only helps if there's a wound to close, and only once a day — refuse
    # (without spending it) rather than let it be popped for nothing or stacked to full.
    if consumable.effect is EffectKind.HEAL:
        if character.health >= character.max_health:
            return "No wounds to treat."
        if character.health_kit_used_today:
            return "Already used a kit today."
    character.consumables.pop(index)
    if consumable.effect is EffectKind.HEAL:
        before = character.health
        character.adjust_health(consumable.amount)
        character.health_kit_used_today = True
        return f"+{character.health - before} Health"
    if consumable.effect is EffectKind.TEMP_STAT_BOOST:
        character.add_temp_bonus(consumable.stat, consumable.amount)
        return f"+{consumable.amount} {consumable.stat.capitalize()} until the next day"
    # Every non-combat effect is handled above; a new EffectKind that is neither
    # listed in COMBAT_ONLY_EFFECTS nor given a branch here would otherwise be silently
    # eaten (the item spent, nothing applied).
    raise ValueError(f"consumable effect not handled out of combat: {consumable.effect}")
