"""What a fight is made of, shared by all three fight surfaces.

Combat is the only part of the game that is not a single check. It is still the
same dice, though — every roll here is checks.resolve_check(), an opposed d6 pool
with the same four-tier CheckResult — so a fight is a *sequence* of the game's
existing checks rather than a second resolution model bolted on beside it.

This module is the *foundation* under that: the enemy roster, the hit formula,
your defense and soak, which weapons are in your hands and which consumables
count as grenades, and who got the drop. It runs no rounds of its own. All three
fight surfaces are built on it —

    abstract_combat.py  fight surface 1: rounds, no positions
    tactical.py         fight surface 2: the grid
    matrix.py           fight surface 3: ICE

— and each imports this rather than each other, which is what stops a player's
attack and an enemy's attack (or a grid attack and an abstract one) from quietly
drifting into different formulas. It is a leaf on the scene graph: it imports
character, checks, cybernetics, inventory, runners, shops and skills, and
deliberately not scene, which is what lets scene.Encounter hold Enemy without a
cycle.

The rule that carries most of the design: **weapons are the damage, skills are
the hit.** skill_value decides *whether* you connect; shops.Item.damage decides
what that costs the enemy. So investing in Short Blade makes you land the knife
more often, and buying a better knife makes each landing hurt more. Neither
substitutes for the other.

A landed hit is not the final damage. Every attack is two rolls: the attacker's
stat_value+advantage d6 opposed against the target's dodge (an ordinary
resolve_check), and the margin — net successes the attack pool cleared the dodge
pool by — is added on top of the weapon's (or enemy's) base damage, so a clean hit
costs more than a scraped one. The target then rolls a soak: body + defense
(armor, or an enemy's toughness) d6, and every success blocks one point of that
damage. `resolve_hit` is the one place both halves happen, for both directions of
a fight, so a player's attack and an enemy's attack are the same function with the
roles swapped.
"""

import random
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import (
    CheckResult,
    CheckRoll,
    count_successes,
    resolve_check,
)
from shadowguy.cybernetics import has_smartlink, installed_defense
from shadowguy.inventory import equipped_defense
from shadowguy.runners import RivalRunner
from shadowguy.shops import (
    COMBAT_ONLY_EFFECTS,
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    MAX_STUN_DAMAGE,
    MAX_WEAPON_CONCEALMENT,
    MIN_STUN_DAMAGE,
    MIN_WEAPON_CONCEALMENT,
    RANGED_SKILLS,
    Consumable,
    Item,
    Slot,
)
from shadowguy.skills import skill_value

# What an enemy's attack pool has to beat to land a hit on you. Defense is built
# from Dodge (skill_value, so gear and rank both count), which is what stops
# Agility from being a stat you only spend on job approaches.
DEFENSE_BASE = 12

# Extra to-hit dice a Smartlink implant grants when firing a weapon that's
# itself smartlinked (shops.Item.smartlinked) -- zero otherwise, including a
# melee weapon or an unlinked gun. Conditional on *which* weapon this attack
# uses, so it goes through resolve_hit's advantage parameter rather than
# skill_gear_bonus (an unconditional per-check bonus with no way to express
# "only with this weapon").
SMARTLINK_ATTACK_BONUS = 2


def smartlink_bonus(character: Character, weapon: Item) -> int:
    if not weapon.smartlinked or not has_smartlink(character.installed_cyberware):
        return 0
    return SMARTLINK_ATTACK_BONUS


def melee_damage_bonus(character: Character, weapon: Item) -> int:
    """Strength added to the damage of a hit you put your body behind — full stat, 1:1,
    uncapped. Blades, clubs, bare hands and a thrown knife qualify; a gun or a bow does
    not (pulling a trigger or a string isn't muscle), which is exactly "not a
    shops.RANGED_SKILLS weapon" and so needs no second list to keep in step.

    Weapon-conditional like smartlink_bonus, and threaded in the same way — the two
    player attack sites pass it into resolve_hit's base_damage, rather than it living
    inside resolve_hit, because resolve_hit is the *shared* formula: an Enemy has no
    strength (combat.Enemy is health/attack/defense/damage/toughness and nothing else),
    so folding this in there would mean inventing one for every NPC. Consequence worth
    naming: this is a player-only bonus. A hired runner swinging a blade beside you
    resolves through the same resolve_hit with their Enemy stat block and gets nothing.

    Deliberately large. Enemy health tops out at 11 (ENEMY_TIERS' Chromed Enforcer) and
    a melee weapon already does 4-7 before the roll's margin, so a Strength 6 runner
    with a katana one-shots anything in the roster and Strength alone out-damages the
    whole weapon catalog at the top end. That is the intent -- Strength carries only two
    skills (see DESIGN.md's Stats and skills) and this is what it buys instead. **Not
    balance-simulated.**
    """
    return 0 if weapon.skill in RANGED_SKILLS else character.stat("strength")

# Empty-handed. A real weapon is strictly better, but there is always *an* attack:
# a runner who sold their last knife can still fight, badly. Built by hand rather
# than through shops.CATALOG, so shops.py's import-time weapon-profile guard never
# sees it — the assertion below is that guard's bound, re-applied here, so a bad
# edit to this Item still fails at import instead of mid-fight.
UNARMED = Item(
    id="unarmed",
    name="Bare Hands",
    price=0,
    bonuses={},
    slot=Slot.WEAPON,
    skill="grapple",
    damage=0,
    stun_damage=4,
    concealment=5,  # nothing to search or confiscate
)
if not (MIN_WEAPON_CONCEALMENT <= UNARMED.concealment <= MAX_WEAPON_CONCEALMENT) or not (
    MIN_STUN_DAMAGE <= UNARMED.stun_damage <= MAX_STUN_DAMAGE
):
    raise ValueError("UNARMED must satisfy the same weapon-profile bounds as shops.CATALOG")


class Drop(StrEnum):
    """Who, if anyone, started the fight on their terms.

    Derived by the caller from the check that routed into the fight (a made ambush
    vs a botched approach), which is why the Encounter itself doesn't carry it —
    the same encounter is a different fight depending on how you walked into it.
    """

    PLAYER = "player"  # your ambush landed: they lose the first round
    NONE = "none"  # a straight fight
    ENEMY = "enemy"  # you were made: they get a free round before you act


def drop_for_result(result: CheckResult | None) -> Drop:
    """Who got the drop, read straight off the check that routed you into the fight.

    This is the whole reason a fight needs no extra data on Outcome to know how it
    started. One rule covers both doors into combat, because the *result* already
    says everything:

    - You made your ambush (jobs.AMBUSH_SKILL): success, so you picked the moment.
    - You missed it: an even fight — you moved too early, but you still moved first.
    - You critically failed *anything*: they were waiting. This is also the only way
      a normal approach reaches a fight at all, which is why going loud always hands
      the initiative to them and choosing the fight never does.

    None means no check routed you here at all (a fight chained straight off another
    fight's outcome), and nobody has the drop in a fight nobody set up.
    """
    if result is None:
        return Drop.NONE
    if result.passed:
        return Drop.PLAYER
    if result is CheckResult.CRITICAL_FAILURE:
        return Drop.ENEMY
    return Drop.NONE


@dataclass(frozen=True)
class Enemy:
    """One hostile. `defense` is the difficulty your attack rolls against (their
    dodge); `toughness` is their soak-roll bonus (their body + armor, collapsed
    into one number since an enemy carries no separate stats or gear)."""

    id: str
    name: str
    health: int
    attack: int  # the enemy's attack pool (dice rolled against your defense)
    defense: int  # what your attack roll must beat
    damage: int  # base health off you on a hit, before the attack roll's margin
    toughness: int  # added to the soak roll that mitigates a landed hit
    # How many tiles away it can attack, in tactical combat only (tactical._enemy_phase).
    # 1 is arm's length; a gun reaches across the room. Abstract combat has no positions,
    # so it ignores this — like a positional counterpart to stun_damage.
    reach: int = 1
    stun_damage: int = 0  # non-lethal stun damage dealt per hit (0 = none)


# id, name, health, attack, defense, damage, toughness, reach.
# The ladder the tiers draw from: a thug is a nuisance, a chromed enforcer is a
# death sentence to a runner who brought the wrong build. Tuned against a runner's
# 15-30 health and DAMAGE_FOR_DELTA in jobs.py — see the balance sim before touching.
# The armed guards (corp_sec, sec_heavy) shoot; the street muscle and the chromed
# bruiser close to melee, so a fight still has both something to kite and something
# that punishes standing still.
_ENEMY_ROWS = (
    ("thug", "Street Thug", 4, 1, 9, 2, 1, 1),
    ("ganger", "Ganger", 5, 2, 10, 2, 2, 1),
    ("corp_sec", "Corp Sec", 7, 2, 11, 3, 2, 6),
    ("sec_heavy", "Sec Heavy", 9, 3, 12, 3, 3, 6),
    ("enforcer", "Chromed Enforcer", 11, 4, 13, 4, 4, 1),
)

ENEMIES = [Enemy(*row) for row in _ENEMY_ROWS]
ENEMIES_BY_ID = {enemy.id: enemy for enemy in ENEMIES}

# Day tier (checks.day_tier) -> who turns up, and how many. The count is the real
# difficulty lever, not the stats: two gangers is a far worse round than one Corp Sec,
# because every one of them swings at you every round.
ENEMY_TIERS: dict[int, tuple[list[str], tuple[int, int]]] = {
    0: (["thug", "ganger"], (1, 2)),
    1: (["ganger", "corp_sec"], (2, 2)),
    2: (["corp_sec", "sec_heavy", "enforcer"], (2, 3)),
}

if any(enemy_id not in ENEMIES_BY_ID for ids, _ in ENEMY_TIERS.values() for enemy_id in ids):
    raise ValueError("ENEMY_TIERS references an enemy id that is not in _ENEMY_ROWS")


def roll_enemies(tier: int, rng: random.Random) -> tuple[Enemy, ...]:
    """The squad a fight at this tier fields."""
    pool, (low, high) = ENEMY_TIERS[tier]
    return tuple(ENEMIES_BY_ID[rng.choice(pool)] for _ in range(rng.randint(low, high)))


# What a hired runner brings to a fight, by their runners.RivalRunner.archetype:
# (health, damage, toughness, reach). A Solo is the one you hire to shoot people; the
# Netrunner is along for the deck and bleeds if you make them fight; the Infiltrator
# works up close. Reach matches the enemy guns' 6 rather than the player's
# FIREARM_RANGE of 8 — a hire is backup, not a better version of you.
#
# First-slice numbers, deliberately NOT balance-simulated: the sim in DESIGN.md's
# tactical Balance note assumes a lone runner, and an ally shifts every one of its
# figures. Re-run it before treating these as tuned.
_CREW_PROFILES: dict[str, tuple[int, int, int, int]] = {
    "Solo": (14, 5, 3, 6),
    "Infiltrator": (11, 4, 2, 1),
    "Netrunner": (10, 3, 1, 6),
}
_CREW_DEFAULT_PROFILE = (10, 3, 1, 1)
# Their rating (runners.RivalRunner.rating, 7-8 today) is the attack pool directly, and
# half of it over this base is what an enemy's attack roll has to beat.
CREW_DEFENSE_BASE = 8


def crew_stats(runner: RivalRunner) -> Enemy:
    """The stat block a hired runner fights with — `runners.RivalRunner` in, combat
    numbers out.

    Returns an `Enemy` for a *friendly*, which reads oddly until you notice `Enemy` is
    already "a combatant who isn't the player": six numbers with no gear, inventory or
    skill sheet behind them, which is exactly what a hire is on the grid too. Building a
    second identical dataclass would only mean resolving attacks twice, once per side —
    the thing `resolve_hit` exists to prevent. `tactical.Unit.stats` is typed on this for
    the same reason, and `Unit.side` is what says which way they're pointing.
    """
    health, damage, toughness, reach = _CREW_PROFILES.get(runner.archetype, _CREW_DEFAULT_PROFILE)
    return Enemy(
        id=runner.id,
        name=runner.name,
        health=health,
        attack=runner.rating,
        defense=CREW_DEFENSE_BASE + runner.rating // 2,
        damage=damage,
        toughness=toughness,
        reach=reach,
    )


def player_defense(character: Character) -> int:
    return DEFENSE_BASE + skill_value(character, "dodge")


def player_soak(character: Character) -> int:
    """Body + equipped armor's defense + installed cyberware's defense (e.g. bone
    lacing): the player's soak pool size (dice rolled to mitigate a landed hit —
    see resolve_hit).

    Bracing (abstract_combat.CombatState.soak) is added on top of this per-round, at
    the call site — it is not part of the character's standing soak, since it clears
    at round end.
    """
    return (
        character.stat("body")
        + equipped_defense(character.inventory)
        + installed_defense(character.installed_cyberware)
    )


def soak_damage(rng: random.Random, base_damage: int, soak_pool: int) -> int:
    """Roll soak_pool d6 and take the successes off base_damage, floored at 0.

    The shared tail end of any damage a target takes, whether it followed a to-hit
    roll (resolve_hit) or was guaranteed (a flee's parting shot) — the soak isn't
    opposed by anything, so it doesn't go through the four-tier CheckResult, just a
    plain success count.

    Public (not underscore-private) because abstract_combat's flee resolves its
    parting shot through it without a to-hit roll of its own.
    """
    return max(0, base_damage - count_successes(soak_pool, rng))


# A crit still hits harder — it's whatever margin (net successes) it takes to clear
# checks.CRITICAL_MARGIN, which feeds straight into the margin resolve_hit adds to
# base damage. There's no separate multiplier; the margin does that work on its own.
def resolve_hit(
    rng: random.Random,
    attacker_stat_value: int,
    attacker_advantage: int,
    to_hit_difficulty: int,
    base_damage: int,
    soak_pool: int,
) -> tuple[CheckRoll, int]:
    """Roll to hit; on a hit, add the margin to base_damage and mitigate with a soak
    roll off soak_pool. Returns the to-hit roll (miss/crit is read off this) and the
    final damage — 0 on a miss, and also 0 if the soak roll swallows the hit whole.

    This is the one function both directions of a fight go through, so a player's
    attack and an enemy's attack can never quietly drift into two different formulas.
    Public (not underscore-private) because tactical.py's grid combat resolves its
    attacks through it too — same reason: one hit formula, two combat surfaces. A
    grid attack just passes a to-hit difficulty raised by the target's cover.
    """
    roll = resolve_check(
        stat_value=attacker_stat_value,
        difficulty=to_hit_difficulty,
        advantage=attacker_advantage,
        rng=rng,
    )
    if not roll.result.passed:
        return roll, 0
    # roll.margin is always > 0 here: resolve_check only passes on margin > 0.
    return roll, soak_damage(rng, base_damage + roll.margin, soak_pool)


def equipped_weapons(character: Character) -> list[Item]:
    """Every weapon the runner is actually holding, or bare hands if none.

    Reads the equipped flag, not just ownership: a knife in your bag is not a knife
    in your hand, and the weapon slots (shops.SLOT_CAPACITY) are what cap how many
    attacks you get to choose between.
    """
    weapons = [
        ITEMS_BY_ID[entry.item_id]
        for entry in character.inventory
        if entry.equipped and ITEMS_BY_ID[entry.item_id].slot is Slot.WEAPON
    ]
    return weapons or [UNARMED]


def consumables_with(character: Character, effects) -> list[tuple[int, Consumable]]:
    """What the runner is carrying that does one of `effects`, as (index into
    Character.consumables, consumable). The index is the currency every spend path here
    and in tactical.py deals in — `Character.consumables` is a list of ids with
    duplicates allowed, so position is the only handle on a particular one.

    Public because both fight surfaces filter that list for their own reasons —
    combat_consumables for grenades, tactical.healing_kits for stabilizing a downed
    hire. One place that knows how the list is addressed, several questions asked of it.
    """
    return [
        (index, consumable)
        for index, consumable_id in enumerate(character.consumables)
        if (consumable := CONSUMABLES_BY_ID[consumable_id]).effect in effects
    ]


def combat_consumables(character: Character) -> list[tuple[int, Consumable]]:
    """The grenades, and only the grenades — see shops.COMBAT_ONLY_EFFECTS.

    Notably not health kits: healing mid-fight would make a fight the cheapest place
    to spend one, and health does not come back fast enough in this game for that to
    be anything but a grind. (Stabilizing a *downed hire* with one is a different
    action, not healing — see tactical.stabilize_ally.)

    Public (not underscore-private) because tactical.py's grenade-throw action reuses
    it too — same reason resolve_hit is public: one list of "what's a grenade", two
    fight surfaces.
    """
    return consumables_with(character, COMBAT_ONLY_EFFECTS)


# How an attack reads in the log, keyed on the weapon's skill: (what a miss is, what a
# hit is). Flavor only — nothing resolves off this — but it's shared by both fight
# surfaces so a katana never "fires" on the grid and a pistol never "swings" in the
# abstract fight. Keyed on the skill rather than the item id so a new weapon reads right
# the day it's added to the catalog, and unknown skills fall back to the generic pair
# rather than raising: a missing verb should not be able to break a fight mid-round.
_ATTACK_VERBS: dict[str, tuple[str, str]] = {
    "pistols": ("fire on", "shoot"),
    "automatics": ("open up on", "riddle"),
    "longarms": ("draw a bead on", "blast"),
    "blades": ("cut at", "cut"),
    "clubs": ("swing at", "smash"),
    "archery": ("loose at", "skewer"),
    "throwing": ("throw at", "hit"),
    "gunnery": ("traverse onto", "shred"),
    "grapple": ("grab at", "wrestle"),
}
_DEFAULT_ATTACK_VERBS = ("swing at", "hit")


def attack_verbs(weapon: Item) -> tuple[str, str]:
    """This weapon's (miss, hit) verbs — "You {miss} them and miss." / "You {hit} them
    for N." See _ATTACK_VERBS."""
    return _ATTACK_VERBS.get(weapon.skill, _DEFAULT_ATTACK_VERBS)
