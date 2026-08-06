"""What a fight is made of, shared by all three fight surfaces.

The enemy roster, the hit formula, your defense and soak, which weapons are in your
hands and which consumables count as grenades, and who got the drop. It runs no rounds
of its own — those live in the three surfaces built on this one:

    abstract_combat.py  fight surface 1: rounds, no positions
    tactical.py         fight surface 2: the grid
    matrix.py           fight surface 3: ICE

Each imports *this* and never each other, which is what stops a player's attack and an
enemy's attack (or a grid attack and an abstract one) from drifting into different
formulas. A leaf on the scene graph: never imports scene, which is what lets
scene.Encounter hold an Enemy without a cycle.

Two rules carry the design, both spelled out in DESIGN.md's Combat section:

- **Weapons are the damage, skills are the hit** — skill_value decides whether you
  connect, shops.Item.damage decides what it costs. Neither substitutes for the other.
  One deliberate exception, melee_damage_bonus below.
- **Every attack is two rolls** — an opposed to-hit whose *margin* is added to base
  damage, then a soak roll that subtracts successes from it. resolve_hit is the one
  place both halves happen, for both directions of a fight.
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
from shadowguy.runners import RivalRunner, best_weapon_id, gear_defense
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

# tactical.py's scheduler pace at "no agility bonus". A true floor on the Enemy side
# (agility never drops below 1), but not on the player's — Character.stat() can take a
# fatigued or scarred agility negative. First-slice, not balance-simulated.
QUICKNESS_BASE = 10

# Extra to-hit dice a Smartlink implant grants, but only on a weapon that is itself
# smartlinked. Conditional on *which* weapon this attack uses, so it rides resolve_hit's
# advantage parameter rather than skill_gear_bonus (which is unconditional per-check and
# has no way to say "only with this weapon").
SMARTLINK_ATTACK_BONUS = 2

# How far a weapon reaches, in tiles, derived from its skill rather than a new Item
# field. Three bands rather than two because "ranged or not" was too coarse once weapons
# were categorized: a thrown knife shouldn't cover a rifle's ground.
#
# Lives here rather than in tactical.py because both sides read it — Enemy.reach is
# weapon_range of whatever that enemy holds, exactly as the player's is. A hand-set enemy
# reach disagreeing with the gun in its hands is the second source of truth this module
# exists to prevent.
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
    uncapped. Qualifying is derived, not tabulated: anything that isn't a
    shops.RANGED_SKILLS weapon, so blades, clubs, bare hands and thrown knives get it and
    guns and bows don't, with no second list to keep in step.

    Threaded like smartlink_bonus — the player's attack sites pass it into resolve_hit's
    base_damage rather than it living inside resolve_hit, which takes final numbers and
    knows nothing about who is swinging. Takes anything with a `.stat()`, so it applies
    to both sides: Enemy.damage folds it in the same way, and a hired runner's blade gets
    it too.

    **Deliberately large, and not balance-simulated.** At Strength 6 a katana one-shots
    most of the roster and Strength alone out-damages the whole weapon catalog. That's
    what Strength buys — it carries only two skills. See DESIGN.md's Combat section.
    """
    return 0 if weapon.skill in RANGED_SKILLS else combatant.stat("strength")

# Empty-handed: a real weapon is strictly better, but there is always *an* attack. Built
# by hand outside shops.CATALOG, so its import-time weapon-profile guard never sees this
# — the check below re-applies that guard's bounds, so a bad edit fails at import rather
# than mid-fight.
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


# What the NPCs are holding — id, name, skill, damage, stun_damage, concealment,
# two_handed. Built by hand outside shops.CATALOG because nothing here is for sale, and
# because most of it sits *under* shops.MIN_WEAPON_DAMAGE (4, the floor for anything
# purchasable). That headroom is what keeps the roster's damage where it was tuned:
# enemy damage is now weapon + Strength like everyone else's, so arming a thug out of
# the player's catalog would have doubled it.
#
# **Deliberately no recharge_rounds anywhere in here.** Cooldowns are tracked on the
# player's CombatState/TacticalState only, so an NPC weapon declaring one would have it
# silently ignored by both surfaces. Add one only alongside the AI bookkeeping for it.
_NPC_WEAPON_ROWS = (
    ("scrap_pipe", "Scrap Pipe", "clubs", 1, 0, 1, False),
    ("switchblade", "Switchblade", "blades", 1, 0, 5, False),
    ("machete", "Machete", "blades", 2, 0, 2, False),
    ("chrome_fist", "Chrome Fist", "clubs", 2, 0, 5, False),
    # Stun 1, not 2. Stun is the harshest number in the table for its size: it bypasses
    # soak, persists between fights (Character.stun), and its KO threshold is
    # `stun >= current health`, so the bar *falls to meet* the total as the target is
    # hurt. At 2 it put a Hacker down 72% of the time without the flee threshold ever
    # tripping. **Treat any future stun weapon as a flee-rules change, not a damage-table
    # one** — see DESIGN.md's enemy-roster section.
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


def weapon_item(weapon_id: str) -> Item:
    """The Item behind a weapon id an NPC is holding: the rows above first, then the
    retail catalog. Two tables rather than one because they're priced differently (an NPC
    weapon costs 0 and sits under the catalog's damage floor), but a hire who has spent a
    run's earnings on a gun (runners.buy_gear) carries a real shops.Item, and a fight
    shouldn't care which table a weapon came out of."""
    return NPC_WEAPONS.get(weapon_id) or ITEMS_BY_ID[weapon_id]

# shops.py's CATALOG bounds, re-applied to the NPC gear it never sees — minus the damage
# floor these rows exist to sit under. Same reasoning as UNARMED: fail at import.
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
    """Who got the drop, read straight off the check that routed you into the fight —
    which is why a fight needs no extra data on Outcome to know how it started.

    Success (you made your ambush) = you picked the moment. Plain failure = an even
    fight. Critical failure = they were waiting, and that's also the only way a normal
    approach reaches a fight at all, so going loud always hands them the initiative and
    choosing the fight never does. None = no check routed you here (a fight chained off
    another fight's outcome), and nobody has the drop in a fight nobody set up.
    """
    if result is None:
        return Drop.NONE
    if result.passed:
        return Drop.PLAYER
    if result is CheckResult.CRITICAL_FAILURE:
        return Drop.ENEMY
    return Drop.NONE


# What a landed drop (Drop.PLAYER) buys, in rounds, on every surface that has rounds:
# abstract_combat and matrix both pay exactly this. It lives here beside Drop rather than
# once per surface because it *is* the drop's payout — two independent copies of one
# shared mechanic is how a shared mechanic quietly stops being shared.
#
# A free round alone is deliberately not the whole prize on either surface (in a
# four-round fight it's a 25% swing, which the balance sim showed was too small to make
# choosing a fight better than being dropped into one) — abstract_combat also takes an
# enemy off the board, since count is the real lethality lever. That extra half is
# per-surface; this constant is the part they share.
#
# tactical.py has no use for it: a grid fight opens with position, not a free round.
FREE_ROUND = 1


def roll_tier_pool[T](
    table: dict[int, tuple[list[str], tuple[int, int]]],
    by_id: dict[str, T],
    tier: int,
    rng: random.Random,
) -> tuple[T, ...]:
    """Draw a fight's opposition from a day-tier table: `rng.randint(low, high)` picks
    with replacement out of that tier's id pool.

    Shared by combat.roll_enemies and matrix.roll_ice, which are the same three lines
    over two different rosters — the *count* being the real difficulty lever (not the
    stats) is a rule both surfaces keep, so it should be one function keeping it. The
    tables themselves stay per-surface: ENEMY_TIERS and ICE_TIERS are tuned separately
    and have no business being merged.
    """
    pool, (low, high) = table[tier]
    return tuple(by_id[rng.choice(pool)] for _ in range(rng.randint(low, high)))


@dataclass(frozen=True)
class Enemy:
    """One combatant who isn't the player, written on the same sheet the player is.

    Six CORE_STATS, invested skill ranks, a weapon and worn armor — and every number a
    fight consumes derived from those through the *same functions the player goes
    through*. The derivation is literal, not parallel: `skills.skill_value` duck-types
    onto anything exposing `.stat()`/`.skill_rank()`/`.skill_gear_bonus()`, so the three
    methods below are the whole bridge. Add a stat, skill or gear effect to the player
    and an enemy holding the relevant thing gets it too. DESIGN.md's enemy-roster
    section carries the full derived/player-counterpart table.

    "A combatant who isn't the player" includes *friendlies*: a hire is one of these too
    (crew_stats), and `tactical.Unit.side` is what says which way a unit points.

    Two places the symmetry deliberately stops:

    - `health` omits character.BASE_HEALTH. That flat +10 is the protagonist's alone;
      handing it to every mook would triple the roster's health.
    - No cyberware and no `skill_gear_bonus` — an enemy's gear is a weapon and an armor
      number, nothing finer. Make `armor` an inventory the day anything loots it.

    `ranks` being a dict makes an Enemy **unhashable** (it can't go in a set or key a
    dict — use `.id`, as ENEMIES_BY_ID does), and means `frozen=True` no longer protects
    it all the way down. Nothing relies on either today.
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
        """Dice rolled against your defense — the same skill_value call the player's own
        attack makes, on whatever they're holding."""
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
        """Soak pool — player_soak's formula, minus the cyberware an NPC has no room
        for."""
        return self.body + self.armor

    @property
    def stun_damage(self) -> int:
        """Non-lethal stun per hit (0 = none), off the weapon like the player's."""
        return self.weapon.stun_damage

    @property
    def reach(self) -> int:
        """Tiles it can attack from. Tactical only — abstract combat has no positions."""
        return weapon_range(self.weapon)

    @property
    def quickness(self) -> int:
        """How often its turn comes up on tactical.py's scheduler. Tactical-only, like
        reach, and raw agility with no bonus terms (the same asymmetry as
        toughness/defense): an enemy's speed is what it's born with."""
        return QUICKNESS_BASE + self.agility


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
        weapon=weapon_item(weapon_id),
        ranks=ranks or {},
        armor=armor,
    )


# The ladder the tiers draw from. Stats are (body, strength, agility, perception, logic,
# cool) — CORE_STATS order. The spread within a tier is deliberately *shape*, not power:
# entries differ by what they punish, not by how strong they are.
#
# **Calibrated, not re-tuned — re-measure anything you change here.** The five originals
# (thug/ganger/corp_sec/sec_heavy/enforcer) keep their previously tuned attack, damage
# and toughness exactly and their health within 2, because those were fit against a
# balance sim that isn't in this repo to re-run. `defense` could not be held: deriving it
# floors every enemy at 13, ~1.4 dodge dice harder to hit than before. See DESIGN.md's
# enemy-roster section for the measured death rates.
#
# Note nearly every weapon skill sits on *agility*, the same stat as Dodge, so an enemy's
# accuracy and evasiveness move together. That coupling is the main constraint on a new
# row, and it's why the two shapes breaking out of it (Bulwark, armor instead of agility;
# Razorgirl, the reverse) sit at opposite ends of the roster.
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
    return roll_tier_pool(ENEMY_TIERS, ENEMIES_BY_ID, tier, rng)


# What a hired runner brings to a fight, by archetype: (stats, weapon, armor,
# combat_gap), on the same sheet every other Enemy is written on.
#
# **combat_gap is how much of `rating` is *not* gun skill.** RivalRunner.rating is a
# runner's standing on the street, not their marksmanship; without it every archetype
# bought weapon rank straight up to `rating` and a Netrunner shot exactly as well as a
# Solo. It's 0 only for the Solo — the one you hire to shoot people.
#
# First-slice, deliberately NOT balance-simulated: the sim assumes a lone runner, and an
# ally shifts every one of its figures.
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
    already "a combatant who isn't the player". A second identical dataclass would only
    mean resolving attacks twice, once per side — the thing `resolve_hit` exists to
    prevent.

    Their `rating` reaches the attack pool by *buying rank* in their weapon's skill
    rather than being assigned to `attack` directly, so a better-rated hire is a
    better-trained one. It lands at `rating - combat_gap`: see _CREW_PROFILES.

    Both halves of that move over a run. `rating` is earned (runners.gain_experience), and
    **anything the runner has bought for themselves beats the profile's issued kit**: a
    solo who has spent a season's fees on a rifle turns up carrying it, and the vest they
    bought is worn over the 0 armor the profile hands out. The profile is what a hire owns
    when you meet them, not a cap on what they can ever bring.
    """
    stats, profile_weapon_id, profile_armor, combat_gap = _CREW_PROFILES.get(
        runner.archetype, _CREW_DEFAULT_PROFILE
    )
    weapon_id = best_weapon_id(runner) or profile_weapon_id
    armor = max(profile_armor, gear_defense(runner))
    weapon = weapon_item(weapon_id)
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


def player_quickness(character: Character) -> int:
    """The player's side of Enemy.quickness. Through Character.stat(), so gear,
    cyberware, fatigue and Humanity all feed it like any other stat read."""
    return QUICKNESS_BASE + character.stat("agility")


def player_soak(character: Character) -> int:
    """The player's soak pool: body + equipped armor + installed cyberware defense (bone
    lacing and friends). Bracing is added on top per-round at the call site, since it
    clears at round end and isn't part of a character's standing soak."""
    return (
        character.stat("body")
        + equipped_defense(character.inventory)
        + installed_defense(character.installed_cyberware)
    )


def soak_damage(rng: random.Random, base_damage: int, soak_pool: int) -> int:
    """Roll soak_pool d6 and take the successes off base_damage, floored at 0.

    The shared tail end of any damage a target takes, whether it followed a to-hit roll
    (resolve_hit) or was guaranteed (abstract_combat's flee parting shot, which is why
    this is public). Nothing opposes a soak, so it's a plain success count rather than a
    four-tier CheckResult.
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

    The one function both directions of a fight go through, on all three surfaces, so a
    player's attack and an enemy's attack can never drift into two different formulas. A
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
    """Every weapon the runner is actually holding, or bare hands if none. Reads the
    equipped flag, not ownership: a knife in your bag is not a knife in your hand."""
    weapons = [
        effective_item(entry)
        for entry in character.inventory
        if entry.equipped and ITEMS_BY_ID[entry.item_id].slot is Slot.WEAPON
    ]
    return weapons or [UNARMED]


def consumables_with(character: Character, effects) -> list[tuple[int, Consumable]]:
    """What the runner is carrying that does one of `effects`, as (index into
    Character.consumables, consumable). The index is the currency every spend path deals
    in — the list allows duplicate ids, so position is the only handle on a particular
    one. One place knows how the list is addressed; several callers ask it different
    questions (combat_consumables for grenades, tactical.healing_kits for stabilizing).
    """
    return [
        (index, consumable)
        for index, consumable_id in enumerate(character.consumables)
        if (consumable := CONSUMABLES_BY_ID[consumable_id]).effect in effects
    ]


def combat_consumables(character: Character) -> list[tuple[int, Consumable]]:
    """The grenades, and only the grenades — see shops.COMBAT_ONLY_EFFECTS.

    Notably not health kits: healing mid-fight would make a fight the cheapest place to
    spend one, and health doesn't come back fast enough for that to be anything but a
    grind. (Stabilizing a downed *hire* is a different action — tactical.stabilize_ally.)
    """
    return consumables_with(character, COMBAT_ONLY_EFFECTS)


# How an attack reads in the log, keyed on the weapon's skill: (miss verb, hit verb).
# Flavor only, but shared by both surfaces so a katana never "fires" on the grid. Keyed
# on the skill rather than the item id so a new weapon reads right the day it's added,
# and an unknown skill falls back rather than raising — a missing verb must not be able
# to break a fight mid-round.
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
