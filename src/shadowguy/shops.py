"""Retail LocationKinds: gear and consumables the runner buys with Cash.

Item is persistent stat-boosting gear (bought once, equipped/stowed, never
used up). Consumable is single-use: buying it appends to Character.consumables,
and using it pops that entry and applies a one-off effect (heal, or a
temporary stat boost that clears on Character.on_new_day()).

These locations can land on neutral ground or in a corp district's
non-specialty slot (see corpmap.FILLER_KINDS), so a job can target one —
corpmap.LOCATION_SKILL and jobs.LEGWORK_APPROACH_TEXT each have an entry for
every SHOP_KINDS member to cover that.
"""

import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from shadowguy.checks import CheckResult, resolve_check, resolve_rng
from shadowguy.corpmap import SHOP_KINDS, LocationKind
from shadowguy.skills import skill_for, skill_value

if TYPE_CHECKING:
    from shadowguy.character import Character

# What a Pawn Shop pays back on an item, relative to its catalog price.
PAWN_SELL_FRACTION = 0.5

# Standing with a shop's owner (corpmap.LocalCharacter) bends its prices: each point is
# STANDING_PRICE_STEP off what you pay and onto what a pawnbroker pays you, capped both
# ways at STANDING_PRICE_CAP. Negative standing (a botched gig) cuts the other way — an
# owner who's soured on you charges more and pays less. This is the hook a future
# "standing unlocks stock/info" system hangs off; today it only moves price.
STANDING_PRICE_STEP = 0.03
STANDING_PRICE_CAP = 0.20


def _standing_discount(standing: int) -> float:
    return max(-STANDING_PRICE_CAP, min(STANDING_PRICE_CAP, standing * STANDING_PRICE_STEP))


def buy_price(base: int, standing: int) -> int:
    """What you pay for a `base`-priced item at a shop whose owner you have `standing`
    with. Higher standing is cheaper, negative is a markup; never below 1eb."""
    return max(1, round(base * (1 - _standing_discount(standing))))


def sell_price(base: int, standing: int) -> int:
    """What a pawnbroker you have `standing` with pays back on a `base`-priced item:
    PAWN_SELL_FRACTION, improved or worsened by standing."""
    return int(base * PAWN_SELL_FRACTION * (1 + _standing_discount(standing)))


class Slot(Enum):
    HEADWEAR = "headwear"
    FACEWEAR = "facewear"
    TORSO = "torso"
    LEGS = "legs"
    BOOTS = "boots"
    ACCESSORY = "accessory"
    WEAPON = "weapon"
    VEHICLE = "vehicle"


# How many items can be equipped in each slot at once. WEAPON is 2 (e.g. a
# main-hand and off-hand piece) rather than 1 like the wearable slots, so a
# two-handed weapon (Item.two_handed) costs both. VEHICLE is 1 like the
# wearable slots — you only ever have one ride active at a time, even if you
# own more (see Item.travel_reduction).
SLOT_CAPACITY: dict[Slot, int] = {
    Slot.HEADWEAR: 1,
    Slot.FACEWEAR: 1,
    Slot.TORSO: 1,
    Slot.LEGS: 1,
    Slot.BOOTS: 1,
    Slot.ACCESSORY: 1,
    Slot.WEAPON: 2,
    Slot.VEHICLE: 1,
}


# The slots whose items are worn on the body — the only ones that may carry
# `defense` or `skill_bonuses`. WEAPON's profile is damage/skill, VEHICLE is a
# ride, and a None (unlimited) slot is a deck; none of them are armor. Keyed off
# the slot rather than "not weapon and not None" so a future non-wearable slot
# stays excluded by default.
WEARABLE_SLOTS: frozenset[Slot] = frozenset(
    {Slot.HEADWEAR, Slot.FACEWEAR, Slot.TORSO, Slot.LEGS, Slot.BOOTS, Slot.ACCESSORY}
)


@dataclass(frozen=True)
class Item:
    id: str
    name: str
    price: int
    bonuses: dict[str, int]  # stat name (see character.CORE_STATS) -> bonus applied to that check
    # None = unlimited (chems, cyberdecks aren't worn, so any number can be
    # equipped at once). Wearables/weapons/vehicles draw from SLOT_CAPACITY.
    slot: Slot | None = None
    # What the item adds to player_defense() (combat.py) when equipped: 1-8, and
    # only valid on a wearable slot (WEARABLE_SLOTS — not WEAPON, not VEHICLE, not
    # an unlimited-slot item like a deck). Not restricted to Slot.TORSO — armor is
    # the common case, but any wearable can carry it.
    defense: int = 0
    # The next three are what make a weapon a weapon, and are meaningless (and
    # rejected) on anything else: `skill` is the skill id its attack rolls,
    # `damage` (4-10) is the health it takes off an enemy on a hit, and
    # `concealment` (1-5, higher = easier to keep out of sight) is seeded for
    # later — nothing reads it yet, same as corpmap's Territory modifiers. This
    # is the only place a weapon's combat profile is written — combat.py reads
    # it rather than keeping a second table of its own that would have to agree
    # with this one.
    skill: str | None = None
    damage: int = 0
    concealment: int = 0
    # Only meaningful when slot is Slot.WEAPON: occupies both weapon slots.
    two_handed: bool = False
    # skill id (skills.SKILLS_BY_ID) -> bonus applied to that skill only, on top of
    # its stat (see skills.skill_value). Distinct from `bonuses`, which moves a whole
    # core stat and so every skill layered on it — this is for gear that's specialized
    # rather than generally useful. Same wearable-only restriction as `defense`.
    skill_bonuses: dict[str, int] = field(default_factory=dict)
    # Only meaningful (and only valid, a fraction strictly between 0 and 1) on a
    # Slot.VEHICLE item: the fraction it cuts off TRAVEL_HOURS_COST
    # (CorpMapScreen.action_travel) on every hop — a bigger ride makes each trip
    # cheaper, computed fresh per hop rather than metered against a daily allowance.
    travel_reduction: float = 0.0
    # Minimum standing with this shop's owner (LocalCharacter) required to see
    # and buy the item. 0 = no gate (all current items). Tier 2 items use 2
    # (see _CATALOG_ROWS). Hidden from the shop UI until the runner reaches
    # this standing.
    min_standing: int = 0
    # How many rounds this weapon must cool down after being fired before it can
    # be used again. 0 = no cooldown (normal weapon). Only meaningful on a
    # Slot.WEAPON item — enforced at import. The cooldown starts ticking at the
    # end of the round you fired, so recharge_rounds=1 means you skip one round
    # between shots (fire every 2nd round).
    recharge_rounds: int = 0
    # Non-lethal stun damage: builds up a separate stun meter on the target. When
    # stun >= current health, the target is knocked out. A weapon can do both
    # lethal and stun damage (e.g. a shock-baton), or just one.
    stun_damage: int = 0
    # Short flavor tag shown in parentheses on shop/inventory listings, e.g.
    # "old tech" for a pre-war pipe pistol. Empty string = no tag.
    tag: str = ""
    # How many Programs a cyberdeck can carry into the matrix at once. Only
    # meaningful when slot is None (the "a None slot is a deck" convention
    # equipped_deck_rating already relies on) — enforced at import.
    program_slots: int = 0
    # Whether this weapon carries a smartgun interface — only meaningful (and only
    # valid, enforced at import) on a firearm (skill == "firearms"). combat.py's
    # smartlink_bonus() reads this alongside cybernetics.has_smartlink to grant a
    # to-hit bonus only when both the weapon and the runner's implant agree; an
    # unlinked gun gets nothing from the cyberware, and the cyberware does
    # nothing for an unlinked one.
    smartlinked: bool = False


@dataclass
class InventoryItem:
    item_id: str
    # Only equipped items contribute their bonus (see equipped_bonus below).
    equipped: bool = True
    # Program ids installed on this specific deck instance (only meaningful
    # when the item is a deck — see Item.program_slots). A player can own
    # several decks; each carries its own loadout.
    installed_programs: list[str] = field(default_factory=list)


def bonus_text(item: Item) -> str:
    """Every stat a shop/inventory listing should show for this item: gear bonuses,
    plus a weapon's damage or armor's defense — the two combat-facing numbers that
    don't live in `bonuses` and would otherwise be invisible on a buy/equip screen.
    """
    parts = []
    if item.tag:
        parts.append(f"({item.tag})")
    parts += [f"+{bonus} {stat.capitalize()}" for stat, bonus in item.bonuses.items()]
    parts += [
        f"+{bonus} {skill_for(skill_id).name}" for skill_id, bonus in item.skill_bonuses.items()
    ]
    if item.damage:
        parts.append(f"{item.damage} dmg")
    if item.stun_damage:
        parts.append(f"{item.stun_damage} stun")
    if item.defense:
        parts.append(f"+{item.defense} defense")
    if item.travel_reduction:
        parts.append(f"-{item.travel_reduction:.0%} travel time")
    if item.smartlinked:
        parts.append("smartlinked")
    return ", ".join(parts)


class EffectKind(Enum):
    HEAL = "heal"
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
# You patch yourself up *after*, on your own time. Chems are out for the same
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
    # Same gate as Item.min_standing: a consumable the owner only sells to people
    # they trust. 0 = no gate.
    min_standing: int = 0


# id, name, price, bonuses, slot, defense, then for weapons only: skill, damage,
# concealment, two_handed, then skill_bonuses (a wearable's per-skill bonus, e.g.
# Slippers' Stealth), then travel_reduction (a Slot.VEHICLE item's percentage cut
# off travel time, e.g. Beater Bike), then min_standing (tier gate), then
# recharge_rounds (weapon cooldown between shots), and finally stun_damage
# (non-lethal knockout damage). A weapon's skill is what its attack rolls in combat and
# its damage is what a hit takes off — the pool spans blunt/short_blade/long_blade/
# firearms, so no build is stuck swinging a weapon it can't use. The bloodiest one
# is two-handed, which costs both weapon slots (SLOT_CAPACITY). Weapons carry no
# stat bonus by default — damage/skill/concealment are their whole profile — so
# `bonuses` is {} unless a specific weapon deliberately earns one. Armor is the same
# shop's other business: `defense` (1-8) adds straight onto combat.player_defense()
# while equipped, and it's not restricted to Slot.TORSO — any wearable can carry it.
_CATALOG_ROWS: dict[LocationKind, list[tuple]] = {
    LocationKind.WEAPON_SHOP: [
        ("brass_knuckles", "Brass Knuckles", 150, {}, Slot.WEAPON, 0, "blunt", 4, 5),
        ("combat_knife", "Combat Knife", 400, {}, Slot.WEAPON, 0, "short_blade", 5, 4),
        ("monoblade", "Monoblade", 700, {}, Slot.WEAPON, 0, "long_blade", 6, 1, True),
        # Trailing 0, True: program_slots (n/a), smartlinked — the one gun in the
        # catalog today, so it's what makes cybernetics.SMARTLINK_ID reachable at all.
        ("pipe_pistol", "Pipe Pistol", 250, {}, Slot.WEAPON, 0, "firearms", 5, 4, False, {}, 0, 0, 0, 0, "old tech", 0, True),
        ("leather_jacket", "Leather Jacket", 200, {}, Slot.TORSO, 3),
        ("kevlar_vest", "Kevlar Vest", 450, {}, Slot.TORSO, 5),
        ("hardsuit", "Hardsuit", 900, {}, Slot.TORSO, 8),
        ("reinforced_helmet", "Reinforced Helmet", 250, {}, Slot.HEADWEAR, 3),
        ("kevlar_helmet", "Kevlar Helmet", 120, {}, Slot.HEADWEAR, 1),
        ("steel_toe_boots", "Steel Toe Boots", 120, {}, Slot.BOOTS, 1),
        ("slippers", "Slippers", 100, {}, Slot.BOOTS, 0, None, 0, 0, False, {"stealth": 1}),
        # Misc weapons (Misc Weapons skill): tasers with a cooldown between shots.
        # The M5 fires every 3 rounds; the H6 fires every 2. Both do stun damage
        # (non-lethal — builds a stun meter instead of reducing health).
        ("taser_m5", "Taser (M5)", 600, {}, Slot.WEAPON, 0, "misc",
         0,   # damage (lethal)
         5,   # concealment
         False, {}, 0, 0,  # two_handed, skill_bonuses, travel_reduction, min_standing
         2,   # recharge_rounds
         5),  # stun_damage
        ("taser_h6", "Taser (H6)", 800, {}, Slot.WEAPON, 0, "misc",
         0,   # damage (lethal)
         4,   # concealment
         False, {}, 0, 0,  # two_handed, skill_bonuses, travel_reduction, min_standing
         1,   # recharge_rounds
         6),  # stun_damage
        # Tier 2 — requires standing 2 with the gunsmith to browse the back room.
        (
            "mono_katana",
            "Mono Katana",
            1400,
            {},
            Slot.WEAPON,
            0,
            "long_blade",
            7,
            1,
            True,
            {},
            0,
            2,
        ),
    ],
    LocationKind.AUTO_DEALER: [
        ("beater_bike", "Beater Bike", 200, {"cool": 1}, Slot.VEHICLE, 0, None, 0, 0, False, {}, 0.10),
        ("tuned_coupe", "Tuned Coupe", 500, {"cool": 2}, Slot.VEHICLE, 0, None, 0, 0, False, {}, 0.20),
        (
            "armored_towncar",
            "Armored Towncar",
            1000,
            {"cool": 3},
            Slot.VEHICLE,
            0,
            None,
            0,
            0,
            False,
            {},
            0.25,
        ),
    ],
    # Persistent +body gear moved out in favor of the consumables below.
    LocationKind.PHARMACY: [],
    LocationKind.COMPUTER_STORE: [
        # id, name, price, bonuses, slot(None=deck), defense, skill, damage, concealment,
        # two_handed, skill_bonuses, travel_reduction, min_standing, recharge_rounds,
        # stun_damage, tag, program_slots (a deck's matrix-program capacity).
        ("burner_deck", "Burner Deck", 200, {"intelligence": 1}, None, 0, None, 0, 0, False, {}, 0, 0, 0, 0, "", 1),
        (
            "cracked_cyberdeck",
            "Cracked Cyberdeck",
            500,
            {"intelligence": 2},
            None,
            0,
            None,
            0,
            0,
            False,
            {},
            0,
            0,
            0,
            0,
            "",
            2,
        ),
        ("zetatech_rig", "Zetatech Rig", 1000, {"intelligence": 3}, None, 0, None, 0, 0, False, {}, 0, 0, 0, 0, "", 3),
    ],
    LocationKind.PAWN: [
        ("pawned_knuckles", "Pawned Knuckles", 80, {}, Slot.WEAPON, 0, "blunt", 4, 5),
        ("pawned_deck", "Pawned Deck", 80, {"intelligence": 1}, None, 0, None, 0, 0, False, {}, 0, 0, 0, 0, "", 1),
        ("pawned_charm", "Pawned Lucky Charm", 80, {"cool": 1}, Slot.ACCESSORY),
        # Tier 2 — the pawnbroker keeps the interesting finds under the counter.
        (
            "pawned_artifact",
            "Pawned Artifact",
            400,
            {"cool": 2, "perception": 1},
            Slot.ACCESSORY,
            0,
            None,
            0,
            0,
            False,
            {},
            0,
            2,
        ),
    ],
}

CATALOG: dict[LocationKind, list[Item]] = {
    kind: [Item(*row) for row in rows] for kind, rows in _CATALOG_ROWS.items()
}

# Loot-only items: never stocked in any shop's buy catalog (ShopScreen only ever
# lists CATALOG.get(location.kind, []), never ITEMS_BY_ID directly), but still
# resolvable through ITEMS_BY_ID so the existing Pawn Shop sell_item flow can price
# and sell them exactly like anything else. matrix.py's optional CACHE-node reward
# grants STOLEN_DATASHARD_ID straight into inventory, unequipped, on top of (and
# independent from) whatever the run's own Outcome pays out; scavenge() below (a
# Junkyard's one action) does the same with SCAVENGE_MATERIALS.
STOLEN_DATASHARD_ID = "stolen_datashard"

# Raw salvage: no bonuses at all (bare `{}`), unlike everything in CATALOG — there's
# nothing to equip here yet. Sellable today (like any Item); the intended sink is
# crafting/repair systems that don't exist yet, so these are pure future-hooks, same
# deferred-hook shape as cybernetics.py before install_cyberware had a caller. No named
# id constants (unlike STOLEN_DATASHARD_ID) since nothing outside this block refers to
# one by name — SCAVENGE_MATERIALS below is derived from these rows, not retyped.
_SCAVENGE_ROWS: list[tuple] = [
    ("armor_plating", "Armor Plating", 50, {}),
    ("rubber", "Rubber", 15, {}),
    ("salvaged_optics", "Salvaged Optics", 60, {}),
    ("wire", "Wire", 20, {}),
    ("screws", "Screws", 10, {}),
]
_LOOT_ROWS: list[tuple] = [
    (STOLEN_DATASHARD_ID, "Stolen Datashard", 180, {}),
    *_SCAVENGE_ROWS,
]
LOOT_ITEMS = [Item(*row) for row in _LOOT_ROWS]

# What a Junkyard's scavenge() can turn up. Derived from _SCAVENGE_ROWS (not the full
# LOOT_ITEMS list) so STOLEN_DATASHARD_ID -- matrix.py's own loot, unrelated to
# scavenging -- can't turn up from a Junkyard, nor scavenged material from a Cache node.
SCAVENGE_MATERIALS = tuple(row[0] for row in _SCAVENGE_ROWS)

ITEMS_BY_ID = {item.id: item for items in (*CATALOG.values(), LOOT_ITEMS) for item in items}

# Weapon-profile bounds. Shared with combat.py (via import) so the hand-built UNARMED
# stays in sync with the catalog — any edit to these constants updates both sides.
MIN_WEAPON_CONCEALMENT = 1
MAX_WEAPON_CONCEALMENT = 5
MIN_WEAPON_DAMAGE = 4
MAX_WEAPON_DAMAGE = 10
MIN_STUN_DAMAGE = 1
MAX_STUN_DAMAGE = 10

# Import-time guard, same pattern as corpmap.py's own tuning-constant checks:
# a shop LocationKind with no catalog would silently show an empty shop
# (ShopScreen's CATALOG.get(..., [])) instead of a clear failure.
if set(CATALOG) != set(SHOP_KINDS):
    raise ValueError("CATALOG must have exactly one entry per corpmap.SHOP_KINDS")

if any(item.two_handed and item.slot is not Slot.WEAPON for item in ITEMS_BY_ID.values()):
    raise ValueError("two_handed items must have slot=Slot.WEAPON")

# A weapon's combat profile and its slot have to agree, both ways round. A Slot.WEAPON
# item with no skill/damage/concealment/stun would be an attack the combat screen can
# offer but never resolve; a skill, damage, or concealment on a non-weapon would be a
# combat profile nothing can ever reach, since combat only ever swings what's equipped
# in Slot.WEAPON. The skill id goes through skill_for, so a typo'd weapon skill fails on
# import rather than mid-fight. Defense is the wearable counterpart to a weapon's
# damage: it has to live on something you can actually put on (not a weapon, not an
# unlimited-slot item like a deck), and it's bounded the same way damage
# is, so a typo'd 80 doesn't quietly turn one jacket into full plot armor.
for _item in ITEMS_BY_ID.values():
    _is_weapon = _item.slot is Slot.WEAPON
    _has_lethal = bool(_item.damage)
    _has_stun = bool(_item.stun_damage)
    if _is_weapon and (
        _item.skill is None
        or not (MIN_WEAPON_CONCEALMENT <= _item.concealment <= MAX_WEAPON_CONCEALMENT)
        or not (_has_lethal or _has_stun)
        or (_has_lethal and not (MIN_WEAPON_DAMAGE <= _item.damage <= MAX_WEAPON_DAMAGE))
        or (_has_stun and not (MIN_STUN_DAMAGE <= _item.stun_damage <= MAX_STUN_DAMAGE))
    ):
        raise ValueError(
            f"{_item.id}: a Slot.WEAPON item needs a skill,"
            f" {MIN_WEAPON_CONCEALMENT}-{MAX_WEAPON_CONCEALMENT} concealment,"
            f" and either {MIN_WEAPON_DAMAGE}-{MAX_WEAPON_DAMAGE} damage"
            f" or {MIN_STUN_DAMAGE}-{MAX_STUN_DAMAGE} stun_damage (or both)"
        )
    if not _is_weapon and (
        _item.skill is not None or _item.damage or _item.stun_damage or _item.concealment
    ):
        raise ValueError(
            f"{_item.id}: only a Slot.WEAPON item can have a combat skill, damage,"
            " stun_damage, or concealment"
        )
    if _item.skill is not None:
        skill_for(_item.skill)
    if _item.defense and _item.slot not in WEARABLE_SLOTS:
        raise ValueError(f"{_item.id}: only a wearable item can have defense")
    if _item.defense and not (1 <= _item.defense <= 8):
        raise ValueError(f"{_item.id}: defense must be 1-8")
    if _item.skill_bonuses and _item.slot not in WEARABLE_SLOTS:
        raise ValueError(f"{_item.id}: only a wearable item can have skill_bonuses")
    for _skill_id in _item.skill_bonuses:
        skill_for(_skill_id)
    if _item.travel_reduction and _item.slot is not Slot.VEHICLE:
        raise ValueError(f"{_item.id}: only a Slot.VEHICLE item can have travel_reduction")
    if _item.travel_reduction and not (0 < _item.travel_reduction < 1):
        raise ValueError(f"{_item.id}: travel_reduction must be a fraction between 0 and 1")
    if _item.min_standing < 0:
        raise ValueError(f"{_item.id}: min_standing must be >= 0")
    if _item.recharge_rounds and not _is_weapon:
        raise ValueError(f"{_item.id}: recharge_rounds is only valid on a Slot.WEAPON item")
    if _item.recharge_rounds < 0:
        raise ValueError(f"{_item.id}: recharge_rounds must be >= 0")
    if _item.program_slots and _item.slot is not None:
        raise ValueError(f"{_item.id}: program_slots is only valid on a deck (slot is None)")
    if _item.program_slots < 0:
        raise ValueError(f"{_item.id}: program_slots must be >= 0")
    if _item.smartlinked and _item.skill != "firearms":
        raise ValueError(f"{_item.id}: smartlinked is only valid on a firearms weapon")


# id, name, price, effect, amount, stat, min_standing
_CONSUMABLE_ROWS: dict[LocationKind, list[tuple[str, str, int, EffectKind, int, str | None, int]]] = {
    LocationKind.PHARMACY: [
        ("health_kit", "Health Kit", 100, EffectKind.HEAL, 5, None),
        ("chem_x", "Chem X", 150, EffectKind.TEMP_STAT_BOOST, 2, "body"),
        ("chem_y", "Chem Y", 150, EffectKind.TEMP_STAT_BOOST, 2, "intelligence"),
        # Tier 2 — stronger stock the pharmacist reserves for regulars.
        (
            "advanced_health_kit",
            "Advanced Health Kit",
            250,
            EffectKind.HEAL,
            10,
            None,
            2,
        ),
        (
            "chem_x2",
            "Chem X2",
            300,
            EffectKind.TEMP_STAT_BOOST,
            3,
            "body",
            2,
        ),
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

for _c in CONSUMABLES_BY_ID.values():
    if _c.min_standing < 0:
        raise ValueError(f"{_c.id}: min_standing must be >= 0")


@dataclass(frozen=True)
class Program:
    """Software installed on a cyberdeck's program_slots, for use in matrix.py fights.

    uses_per_fight is what tells passive from active apart, derived rather than a
    separate kind field: 0 means the bonus fields below apply continuously while
    installed on the runner's active deck (matrix.active_deck_entry); nonzero means it
    instead grants a MatrixAction, described by the action_* fields — usable that many
    times per fight if positive, or unconditionally if negative (matrix.
    EXTRACT_UNLIMITED_USES is -1; unlimited-use programs pay their cost some other way,
    e.g. Extract raising MatrixState.security on a miss rather than running out). A
    program is exactly one of passive/limited-action/unlimited-action, enforced at import.
    """

    id: str
    name: str
    price: int
    ram_cost: int = 1  # how much of a deck's program_slots capacity this eats when installed
    uses_per_fight: int = 0
    # Passive-only (meaningful when uses_per_fight == 0):
    integrity_bonus: int = 0
    firewall_bonus: int = 0
    soak_bonus: int = 0
    damage_bonus: int = 0
    # Action-only (meaningful when uses_per_fight != 0): exactly one of these is set.
    action_damage: int = 0  # guaranteed, no-roll damage dealt to the target ICE
    action_skip_ice: bool = False  # this round's ICE phase is skipped entirely
    action_sleaze: bool = False  # attempt to talk the target ICE down instead of fighting it
    action_extract: bool = False  # roll an attack against a DATA/CACHE node's ICE, ignoring its soak
    action_analyze: bool = False  # navigation-mode only: read a connected node's role without visiting it
    min_standing: int = 0
    tag: str = ""


# id, name, price, ram_cost, uses_per_fight, integrity_bonus, firewall_bonus, soak_bonus,
# damage_bonus, action_damage, action_skip_ice, action_sleaze, action_extract,
# action_analyze, min_standing, tag. First-slice catalog, not yet balance-simulated —
# see CLAUDE.md's convention for flagging that.
_PROGRAM_ROWS: dict[LocationKind, list[tuple]] = {
    LocationKind.COMPUTER_STORE: [
        ("sleaze", "Sleaze", 230, 1, 2, 0, 0, 0, 0, 0, False, True, False, False, 0, "2 uses"),
        ("extract", "Extract", 260, 1, -1, 0, 0, 0, 0, 0, False, False, True, False, 0, "unlimited"),
        ("analyze", "Analyze", 180, 1, 3, 0, 0, 0, 0, 0, False, False, False, True, 0, "3 uses"),
        ("icebreaker", "Icebreaker", 240, 1, -1, 0, 0, 0, 0, 5, False, False, False, False, 0, "unlimited"),
    ],
}

PROGRAM_CATALOG: dict[LocationKind, list[Program]] = {
    kind: [Program(*row) for row in rows] for kind, rows in _PROGRAM_ROWS.items()
}

PROGRAMS_BY_ID = {p.id: p for programs in PROGRAM_CATALOG.values() for p in programs}

for _p in PROGRAMS_BY_ID.values():
    if _p.min_standing < 0:
        raise ValueError(f"{_p.id}: min_standing must be >= 0")
    if _p.ram_cost < 1:
        raise ValueError(f"{_p.id}: ram_cost must be >= 1")
    if _p.uses_per_fight < -1:
        raise ValueError(f"{_p.id}: uses_per_fight must be >= -1 (-1 means unlimited)")
    _passive_fields = (_p.integrity_bonus, _p.firewall_bonus, _p.soak_bonus, _p.damage_bonus)
    _action_fields = (
        _p.action_damage,
        _p.action_skip_ice,
        _p.action_sleaze,
        _p.action_extract,
        _p.action_analyze,
    )
    if _p.uses_per_fight == 0:
        if any(_action_fields):
            raise ValueError(f"{_p.id}: a passive program (uses_per_fight=0) can't set action_* fields")
    else:
        if any(_passive_fields):
            raise ValueError(f"{_p.id}: an action program (uses_per_fight>0) can't set passive bonus fields")
        if sum(bool(f) for f in _action_fields) != 1:
            raise ValueError(
                f"{_p.id}: an action program must set exactly one of "
                "action_damage/action_skip_ice/action_sleaze/action_extract/action_analyze"
            )

# A HOSPITAL (corpmap.LocationKind.HOSPITAL) heals over time, not on the spot: each day
# you check in you pay this — the same as a nice district's lodging (LODGING per point of
# Development, ~4 on a good block) — and heal 1d6 + Body. Slow but the only real bulk
# healing there is; a Health Kit is a one-off top-up, resting elsewhere doesn't heal.
HOSPITAL_STAY_COST = 20


def _equipped_items(inventory: list[InventoryItem]) -> Iterator[Item]:
    return (ITEMS_BY_ID[entry.item_id] for entry in inventory if entry.equipped)


def equipped_bonus(inventory: list[InventoryItem], stat: str) -> int:
    return sum(item.bonuses.get(stat, 0) for item in _equipped_items(inventory))


def equipped_defense(inventory: list[InventoryItem]) -> int:
    return sum(item.defense for item in _equipped_items(inventory))


def equipped_skill_bonus(inventory: list[InventoryItem], skill_id: str) -> int:
    return sum(item.skill_bonuses.get(skill_id, 0) for item in _equipped_items(inventory))


def equipped_travel_reduction(inventory: list[InventoryItem]) -> float:
    return sum(item.travel_reduction for item in _equipped_items(inventory))


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


def buy_program(character: "Character", program_id: str, standing: int = 0) -> str:
    """Buy a Program into the runner's owned pool (Character.owned_programs) — not
    installed on any deck yet. Installing is a separate, free step (install_program),
    so a program can be moved between decks the runner owns without buying it twice."""
    program = PROGRAMS_BY_ID[program_id]
    if standing < program.min_standing:
        return f"{program.name} isn't available to you yet."
    if program_id in character.owned_programs:
        return f"Already own {program.name}."
    price = buy_price(program.price, standing)
    if character.cash < price:
        return f"Can't afford {program.name} ({price}eb)."
    character.cash -= price
    character.owned_programs.add(program_id)
    return f"Bought {program.name} for {price}eb."


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


def _slot_cost(item: Item) -> int:
    return 2 if item.two_handed else 1


def slot_usage(inventory: list[InventoryItem], slot: Slot) -> int:
    return sum(_slot_cost(item) for item in _equipped_items(inventory) if item.slot is slot)


def _fits_in_slot(inventory: list[InventoryItem], item: Item) -> bool:
    if item.slot is None:
        return True
    return slot_usage(inventory, item.slot) + _slot_cost(item) <= SLOT_CAPACITY[item.slot]


def buy_item(character: "Character", item: Item, standing: int = 0) -> bool:
    if standing < item.min_standing:
        return False
    price = buy_price(item.price, standing)
    if character.cash < price:
        return False
    character.cash -= price
    # Auto-equip only if there's room; otherwise it's bought stowed and the
    # player equips it manually (swapping out whatever's occupying the slot).
    entry = InventoryItem(item.id, equipped=_fits_in_slot(character.inventory, item))
    character.inventory.append(entry)
    return True


def buy_consumable(character: "Character", consumable: Consumable, standing: int = 0) -> bool:
    if standing < consumable.min_standing:
        return False
    price = buy_price(consumable.price, standing)
    if character.cash < price:
        return False
    character.cash -= price
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


def hospital_stay(character: "Character", rng: random.Random | None = None) -> str | None:
    """One day of inpatient care: charge HOSPITAL_STAY_COST and heal 1d6 + Body (raw Body,
    like max_health — recovery is a survivability thing, gear doesn't speed it). Returns
    the result message, or None if the runner can't afford the day (the caller then leaves
    the day unspent). The stay is a day; the caller advances the run around it."""
    if character.cash < HOSPITAL_STAY_COST:
        return None
    character.cash -= HOSPITAL_STAY_COST
    roll = resolve_rng(rng).randint(1, 6)
    before = character.health
    character.adjust_health(roll + character.body)
    return f"A day in the ward: +{character.health - before} Health for {HOSPITAL_STAY_COST}eb."


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
        character.inventory.append(InventoryItem(item_id, equipped=False))
    names = ", ".join(ITEMS_BY_ID[item_id].name for item_id in found)
    return f"You scavenge up: {names}."


def sell_item(character: "Character", index: int, standing: int = 0) -> int:
    # By index, not id: the same item id can be owned more than once.
    entry = character.inventory.pop(index)
    proceeds = sell_price(ITEMS_BY_ID[entry.item_id].price, standing)
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
