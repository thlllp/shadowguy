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
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.character import CORE_STATS, HEALTH_PER_BODY, Character
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
    effective_item,
)
from shadowguy.skills import skill_for, skill_value

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

# How far a weapon reaches, in tiles, derived from its skill rather than a new Item
# field: the shooting skills (shops.RANGED_SKILLS -- the three gun categories, gunnery
# and archery) reach across the map, throwing gets a middle band of its own, everything
# else is arm's length. Three bands rather than two because the weapon categories made
# "ranged or not" too coarse: a thrown knife shouldn't cover the same ground as a rifle.
#
# Lives here rather than in tactical.py (which is where it used to, and which imports
# these back under their old names) because reach is now read by both sides of a fight:
# Enemy.reach is weapon_range of whatever that enemy is holding, exactly as the player's
# is. A hand-set enemy reach that disagreed with the gun in its hands is precisely the
# second source of truth this module exists to prevent.
MELEE_RANGE = 1
THROWN_RANGE = 4
FIREARM_RANGE = 8


def weapon_range(weapon: Item) -> int:
    if weapon.skill in RANGED_SKILLS:
        return FIREARM_RANGE
    return THROWN_RANGE if weapon.skill == "throwing" else MELEE_RANGE


def smartlink_bonus(character: Character, weapon: Item) -> int:
    if not weapon.smartlinked or not has_smartlink(character.installed_cyberware):
        return 0
    return SMARTLINK_ATTACK_BONUS


def melee_damage_bonus(combatant: "Character | Enemy", weapon: Item) -> int:
    """Strength added to the damage of a hit you put your body behind — full stat, 1:1,
    uncapped. Blades, clubs, bare hands and a thrown knife qualify; a gun or a bow does
    not (pulling a trigger or a string isn't muscle), which is exactly "not a
    shops.RANGED_SKILLS weapon" and so needs no second list to keep in step.

    Weapon-conditional like smartlink_bonus, and threaded in the same way — the player's
    two attack sites pass it into resolve_hit's base_damage rather than it living inside
    resolve_hit, because resolve_hit takes final numbers and knows nothing about who is
    swinging.

    Takes anything with a `.stat()`, which is now both sides: an Enemy carries the same
    six CORE_STATS the player does, so `Enemy.damage` folds this in the same way and a
    thug's pipe gets its owner's Strength exactly as the player's does. That symmetry is
    the point — this used to be a player-only bonus, and a hired runner swinging a blade
    beside you got nothing from it.

    Deliberately large on the player's side. A melee weapon does 4-7 before the roll's
    margin, so a Strength 6 runner with a katana one-shots most of the roster and
    Strength alone out-damages the whole weapon catalog at the top end. That is the
    intent -- Strength carries only two skills (see DESIGN.md's Stats and skills) and
    this is what it buys instead. **Not balance-simulated.**
    """
    return 0 if weapon.skill in RANGED_SKILLS else combatant.stat("strength")

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


# What the NPCs are holding. Built by hand outside shops.CATALOG for the same reason
# UNARMED is: nothing here is for sale, so the catalog's import-time guard never sees it
# — and, the reason this table *can't* live in the catalog, most of it sits under
# shops.MIN_WEAPON_DAMAGE (4, the floor for a weapon a player can buy). Street gear is
# meant to be worse than anything on a shelf, and that headroom is what keeps the
# roster's damage where it was tuned: an enemy's damage is now weapon + Strength like
# everyone else's, so arming a thug out of the player's catalog would have doubled it.
# The three long guns at the top do reach catalog-grade damage — they're what the
# corporate tier and a hired Solo carry, and they are still not for sale.
#
# id, name, skill, damage, stun_damage, concealment, two_handed.
#
# Deliberately no recharge_rounds anywhere in here: weapon cooldowns are tracked on the
# player's CombatState/TacticalState only, so an NPC weapon that declared one would have
# it silently ignored by both fight surfaces. Give NPCs a cooldown weapon only alongside
# the AI bookkeeping to honour it.
_NPC_WEAPON_ROWS = (
    ("scrap_pipe", "Scrap Pipe", "clubs", 1, 0, 1, False),
    ("switchblade", "Switchblade", "blades", 1, 0, 5, False),
    ("machete", "Machete", "blades", 2, 0, 2, False),
    ("chrome_fist", "Chrome Fist", "clubs", 2, 0, 5, False),
    # Stun 1, not 2. Stun is the harshest number in the table for its size: it bypasses
    # the soak roll entirely, it persists between fights (Character.stun), and the KO
    # threshold is `stun >= current health` — so the bar falls to meet the total as the
    # target is hurt, rather than the total having to climb to a fixed bar. At 2 this put
    # a Hacker down 72% of the time it turned up in a pair, at half health, *without the
    # flee threshold ever tripping* — a loss the escape valve doesn't watch for is close
    # to the cage abstract_combat's flee rules exist to prevent. See tools/combat_sim.py.
    ("stun_baton", "Stun Baton", "clubs", 1, 1, 2, False),
    ("holdout_pistol", "Holdout Pistol", "pistols", 3, 0, 5, False),
    ("guard_smg", "Guard SMG", "automatics", 3, 0, 2, False),
    ("riot_shotgun", "Riot Shotgun", "longarms", 4, 0, 1, True),
    ("combat_rifle", "Combat Rifle", "automatics", 5, 0, 1, True),
    ("marksman_rifle", "Marksman Rifle", "longarms", 4, 0, 1, True),
)

NPC_WEAPONS = {
    row[0]: Item(
        id=row[0],
        name=row[1],
        price=0,
        bonuses={},
        slot=Slot.WEAPON,
        skill=row[2],
        damage=row[3],
        stun_damage=row[4],
        concealment=row[5],
        two_handed=row[6],
    )
    for row in _NPC_WEAPON_ROWS
}

# The bounds shops.py applies at import to everything in CATALOG, re-applied here to the
# NPC gear it never sees — minus the damage floor these rows exist to sit under. Same
# guard-at-its-bound reasoning as UNARMED above: a bad edit fails at import, not
# mid-fight.
for _weapon in NPC_WEAPONS.values():
    if not (MIN_WEAPON_CONCEALMENT <= _weapon.concealment <= MAX_WEAPON_CONCEALMENT):
        raise ValueError(f"{_weapon.id}: NPC weapon concealment out of bounds")
    if _weapon.stun_damage and not (MIN_STUN_DAMAGE <= _weapon.stun_damage <= MAX_STUN_DAMAGE):
        raise ValueError(f"{_weapon.id}: NPC weapon stun_damage out of bounds")
    if not (_weapon.damage or _weapon.stun_damage):
        raise ValueError(f"{_weapon.id}: an NPC weapon must do damage or stun")


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
    """One combatant who isn't the player, written on the same sheet the player is.

    Six CORE_STATS, invested skill ranks, a weapon in hand and worn armor — and every
    number a fight actually consumes (`attack`/`defense`/`damage`/`toughness`/`health`/
    `reach`/`stun_damage`) derived from those through the *same functions the player
    goes through*. Those seven were hand-tuned fields until now, which meant a Corp Sec's
    accuracy and a runner's accuracy were two unrelated numbers that happened to be
    compared against each other.

    The derivation is literal, not parallel: `skills.skill_value` takes anything with
    `.stat()`/`.skill_rank()`/`.skill_gear_bonus()` (it imports Character only under
    TYPE_CHECKING), so the three methods below are all it takes to run an enemy through
    the player's own skill maths. `defense` is `player_defense`'s formula, `toughness` is
    `player_soak`'s, `damage` folds in `melee_damage_bonus`. Add a stat, a skill or a
    gear effect to the player and an enemy holding the relevant thing gets it too.

    Still "a combatant who isn't the player", so a *friendly* hire is also one of these
    (see crew_stats) — `tactical.Unit.side` is what says which way a unit is pointing.

    Two places the symmetry stops, both deliberate:

    - `health` is `body * HEALTH_PER_BODY` **without** character.BASE_HEALTH. That flat
      +10 is the protagonist's alone; handing it to every mook would triple the roster's
      health and make a tier-0 thug outlast a Chromed Enforcer does today.
    - No cyberware and no `skill_gear_bonus` — an enemy's gear is its weapon and its
      armor rating, nothing finer. `armor` is a plain number rather than worn Items
      because nothing drops or loots it yet; make it an inventory the day it does.

    `ranks` being a dict costs two things worth knowing: an Enemy is **no longer
    hashable** (it was, when every field was a scalar), so it can't go in a set or key a
    dict — use `.id`, which is what ENEMIES_BY_ID does — and `frozen=True` no longer
    protects it all the way down, since the dict itself is still mutable. Nothing does
    either today.
    """

    id: str
    name: str
    body: int
    strength: int
    agility: int
    perception: int
    logic: int
    cool: int
    weapon: Item
    # skill id (skills.SKILLS_BY_ID) -> invested rank. Absent means rank 0, which is
    # *below* a player's character.STARTING_SKILL_RANK of 1: an untrained thug swinging
    # a pipe is worse at it than any runner who ever picked one up.
    ranks: dict[str, int] = field(default_factory=dict)
    # Worn protection, the enemy-side counterpart of inventory.equipped_defense — added
    # to the soak pool by `toughness` exactly as armor is for the player.
    armor: int = 0

    # --- the Character-shaped surface skills.skill_value duck-types against ---

    def stat(self, name: str) -> int:
        if name not in CORE_STATS:
            raise ValueError(f"unknown stat: {name!r}")
        return getattr(self, name)

    def skill_rank(self, skill_id: str) -> int:
        return self.ranks.get(skill_id, 0)

    def skill_gear_bonus(self, skill_id: str) -> int:
        return 0  # no skill-targeted gear on an NPC — see the class docstring

    # --- what a fight reads, all derived from the sheet above ---

    @property
    def health(self) -> int:
        return self.body * HEALTH_PER_BODY

    @property
    def attack(self) -> int:
        """The attack pool: dice rolled against your defense — skill_value of whatever
        they're holding, the same call the player's own attack makes."""
        return skill_value(self, self.weapon.skill)

    @property
    def defense(self) -> int:
        """What your attack roll must beat — player_defense's formula, same base."""
        return DEFENSE_BASE + skill_value(self, "dodge")

    @property
    def damage(self) -> int:
        """Base health off the target on a hit, before the attack roll's margin."""
        return self.weapon.damage + melee_damage_bonus(self, self.weapon)

    @property
    def toughness(self) -> int:
        """Added to the soak roll that mitigates a landed hit — player_soak's formula,
        minus the cyberware term an NPC has no room for."""
        return self.body + self.armor

    @property
    def stun_damage(self) -> int:
        """Non-lethal stun dealt per hit (0 = none), off the weapon like the player's."""
        return self.weapon.stun_damage

    @property
    def reach(self) -> int:
        """How many tiles away it can attack, in tactical combat only
        (tactical._enemy_phase). Abstract combat has no positions, so it ignores this."""
        return weapon_range(self.weapon)


def _enemy(
    id: str,
    name: str,
    stats: tuple[int, int, int, int, int, int],
    weapon_id: str,
    ranks: dict[str, int] | None = None,
    armor: int = 0,
) -> Enemy:
    """Row -> Enemy, with the six stats positional in CORE_STATS order."""
    body, strength, agility, perception, logic, cool = stats
    return Enemy(
        id=id,
        name=name,
        body=body,
        strength=strength,
        agility=agility,
        perception=perception,
        logic=logic,
        cool=cool,
        weapon=NPC_WEAPONS[weapon_id],
        ranks=ranks or {},
        armor=armor,
    )


# The ladder the tiers draw from: a thug is a nuisance, a chromed enforcer is a death
# sentence to a runner who brought the wrong build.
#
# Stats are (body, strength, agility, perception, logic, cool) — CORE_STATS order.
#
# **The five originals are calibrated, not re-tuned.** thug/ganger/corp_sec/sec_heavy/
# enforcer keep their previously tuned attack, damage and toughness *exactly*, and their
# health within 2, because those were fit against a balance sim (see DESIGN.md) and the
# sim isn't in this repo to re-run. What could not be held is `defense`: it was 9-13 on
# the old hand-set scale, and DEFENSE_BASE alone is 12, so deriving it puts the floor at
# 13. Every enemy is correspondingly ~1.4 dodge dice harder to hit than before; the
# health numbers absorb most of that back. Anything you change here, re-measure.
#
# Note nearly every weapon skill sits on *agility*, the same stat as Dodge (see
# skills.py), so an enemy's accuracy and its evasiveness move together — that coupling
# is the main thing constraining a row, and it's why the two shapes that break out of it
# (Bulwark, armor instead of agility; Razorgirl, agility instead of armor) sit at
# opposite ends of the roster.
#
# The roster's spread is deliberately *shape*, not power. Within a tier the entries
# differ by what they punish: something to kite, something that punishes standing still,
# something that has to be killed before it shoots twice, something that soaks.
_ENEMY_ROWS: tuple[Enemy, ...] = (
    # --- tier 0: the street ---
    # The baseline nuisance: no training, no armor, a length of pipe.
    _enemy("thug", "Street Thug", (1, 1, 1, 1, 1, 1), "scrap_pipe"),
    # Trained where the thug isn't, and wearing something.
    _enemy("ganger", "Ganger", (1, 1, 1, 1, 1, 1), "switchblade", {"blades": 1}, armor=1),
    # A gun at tier 0 — can't hit much, but reaches the whole map and doesn't have to
    # close. The first thing on the ladder that punishes standing in the open.
    _enemy("lookout", "Street Lookout", (1, 1, 1, 2, 1, 1), "holdout_pistol"),
    # --- tier 1: organized ---
    _enemy("corp_sec", "Corp Sec", (1, 1, 2, 1, 1, 1), "holdout_pistol", armor=1),
    # Glass cannon: combat drugs instead of training. Hits harder than anything else at
    # this tier and folds to one solid answer. Strength, not skill — it swings wildly
    # (attack 2) and it is the Strength term that makes the hit hurt.
    _enemy("juicer", "Juicer", (1, 2, 2, 1, 1, 1), "machete"),
    # Non-lethal: threatens a knockout rather than a death, via the stun meter that
    # carries between fights (Character.stun). Nothing else in the roster does this.
    _enemy(
        "shock_trooper", "Shock Trooper", (2, 1, 2, 1, 1, 1), "stun_baton", armor=1
    ),
    # --- tier 2: corporate ---
    # Speed instead of armor: the hardest thing in the game to land a hit on, and it
    # dies to the first one that lands.
    _enemy("razorgirl", "Razorgirl", (1, 2, 3, 1, 1, 1), "machete", {"dodge": 1}),
    _enemy(
        "sec_heavy", "Sec Heavy", (2, 2, 2, 1, 1, 1), "guard_smg", {"automatics": 1}, armor=1
    ),
    _enemy(
        "enforcer", "Chromed Enforcer", (2, 2, 2, 1, 1, 2), "chrome_fist", {"clubs": 2}, armor=2
    ),
    # Reaches across the map and hits for more than anything else, on the least health in
    # the tier. A priority target that punishes ignoring it.
    _enemy("marksman", "Marksman", (1, 1, 3, 2, 1, 1), "marksman_rifle"),
    # The opposite trade: the most health and soak on the board, barely able to hit
    # anything. A wall to be worked around rather than a threat to be raced.
    _enemy("bulwark", "Bulwark", (2, 2, 1, 1, 1, 2), "riot_shotgun", armor=3),
)

ENEMIES = list(_ENEMY_ROWS)
ENEMIES_BY_ID = {enemy.id: enemy for enemy in ENEMIES}

if len(ENEMIES_BY_ID) != len(ENEMIES):
    raise ValueError("_ENEMY_ROWS has a duplicate enemy id")

# Day tier (checks.day_tier) -> who turns up, and how many. The count is the real
# difficulty lever, not the stats: two gangers is a far worse round than one Corp Sec,
# because every one of them swings at you every round.
ENEMY_TIERS: dict[int, tuple[list[str], tuple[int, int]]] = {
    0: (["thug", "ganger", "lookout"], (1, 2)),
    1: (["ganger", "corp_sec", "shock_trooper", "juicer"], (2, 2)),
    2: (["corp_sec", "sec_heavy", "enforcer", "razorgirl", "marksman", "bulwark"], (2, 3)),
}

if any(enemy_id not in ENEMIES_BY_ID for ids, _ in ENEMY_TIERS.values() for enemy_id in ids):
    raise ValueError("ENEMY_TIERS references an enemy id that is not in _ENEMY_ROWS")


def roll_enemies(tier: int, rng: random.Random) -> tuple[Enemy, ...]:
    """The squad a fight at this tier fields."""
    pool, (low, high) = ENEMY_TIERS[tier]
    return tuple(ENEMIES_BY_ID[rng.choice(pool)] for _ in range(rng.randint(low, high)))


# What a hired runner brings to a fight, by their runners.RivalRunner.archetype:
# (stats, weapon, armor, combat_gap) on the same sheet every other Enemy is written on.
# A Solo is the one you hire to shoot people; the Netrunner is along for the deck and
# bleeds if you make them fight; the Infiltrator works up close.
#
# **combat_gap is how much of `rating` is *not* gun skill.** RivalRunner.rating is a
# runner's general standing on the street, not their marksmanship, and without this the
# two were the same number: every archetype bought weapon rank straight up to `rating`,
# so a Netrunner shot exactly as well as a Solo of equal rating. A netrunner is hired for
# the deck. What their rating buys is what they're good at, and for three of the four
# archetypes that isn't shooting.
#
# First-slice numbers, deliberately NOT balance-simulated: the sim in DESIGN.md's
# tactical Balance note assumes a lone runner, and an ally shifts every one of its
# figures. Re-run it before treating these as tuned.
_CREW_PROFILES: dict[str, tuple[tuple[int, int, int, int, int, int], str, int, int]] = {
    "Solo": ((3, 2, 3, 2, 1, 2), "combat_rifle", 0, 0),
    "Infiltrator": ((2, 2, 3, 2, 1, 1), "machete", 0, 1),
    "Netrunner": ((2, 1, 2, 1, 3, 1), "holdout_pistol", 0, 3),
}
_CREW_DEFAULT_PROFILE = ((2, 1, 2, 1, 1, 1), "holdout_pistol", 0, 2)


def crew_stats(runner: RivalRunner) -> Enemy:
    """The stat block a hired runner fights with — `runners.RivalRunner` in, combat
    numbers out.

    Returns an `Enemy` for a *friendly*, which reads oddly until you notice `Enemy` is
    already "a combatant who isn't the player": a stat sheet with no Character behind it,
    which is exactly what a hire is on the grid too. Building a second identical
    dataclass would only mean resolving attacks twice, once per side — the thing
    `resolve_hit` exists to prevent. `tactical.Unit.stats` is typed on this for the same
    reason, and `Unit.side` is what says which way they're pointing.

    Their `rating` (runners.RivalRunner.rating, 7-8 today) reaches the attack pool by
    *buying rank* in their weapon's skill rather than being assigned to `attack` directly
    — so a better-rated hire is a better-trained one, and the number means the same thing
    it means everywhere else. It lands at `rating - combat_gap` rather than at `rating`:
    see _CREW_PROFILES for why a netrunner's standing is not their marksmanship.
    """
    stats, weapon_id, armor, combat_gap = _CREW_PROFILES.get(
        runner.archetype, _CREW_DEFAULT_PROFILE
    )
    weapon = NPC_WEAPONS[weapon_id]
    skill = weapon.skill
    # rank = the gun half of rating, less the skill's own stat, so skill_value comes out
    # at rating - combat_gap on the nose.
    stat_name = skill_for(skill).stat
    stat_value = stats[CORE_STATS.index(stat_name)]
    return _enemy(
        runner.id,
        runner.name,
        stats,
        weapon_id,
        {skill: max(0, runner.rating - combat_gap - stat_value)},
        armor=armor,
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
        effective_item(entry)
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
