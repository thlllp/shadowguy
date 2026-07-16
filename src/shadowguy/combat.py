"""Combat: rounds, enemies, and the one place a fight's rules live.

Combat is the only part of the game that is not a single check. It is still the
same dice, though — every roll here is checks.resolve_check(), an opposed d6 pool
with the same four-tier CheckResult — so a fight is a *sequence* of the game's
existing checks rather than a second resolution model bolted on beside it.

The shape of a round:

    you take one Action  ->  every standing enemy that isn't stunned attacks you

and that repeats until one side is down or you walk out. Nothing here decides
what a fight *means* to the job around it: combat reports how it ended (see
CombatOutcome) and scene/app map that onto the Encounter's victory/escape
Outcomes. That's why this module is a leaf on the scene graph — it imports
character, checks, shops and skills, and deliberately not scene, which is what
lets scene.Encounter hold Enemy without a cycle.

Two rules carry most of the design:

- **Every action rolls a different core stat.** Attacking is strength (or
  perception, with a gun); bracing is body; reading the fight is intelligence;
  facing them down is cool; running is agility. A fight is therefore not a
  Strength minigame that only an Enforcer can play — it's the same "every build
  has a way through, but not the same way" rule that jobs.py enforces across a
  stage's approaches, applied to a round.
- **Weapons are the damage, skills are the hit.** skill_value decides *whether*
  you connect; shops.Item.damage decides what that costs the enemy. So investing
  in Short Blade makes you land the knife more often, and buying a better knife
  makes each landing hurt more. Neither substitutes for the other.

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

from shadowguy.character import Character
from shadowguy.checks import CheckResult, CheckRoll, count_successes, resolve_check
from shadowguy.shops import (
    COMBAT_ONLY_EFFECTS,
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    Consumable,
    EffectKind,
    Item,
    Slot,
    equipped_defense,
)
from shadowguy.skills import skill_for, skill_value

# What an enemy's attack pool has to beat to land a hit on you. Defense is built
# from Dodge (skill_value, so gear and rank both count), which is what stops
# Agility from being a stat you only spend on job approaches.
DEFENSE_BASE = 12

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
if not (1 <= UNARMED.concealment <= 5) or not (1 <= UNARMED.stun_damage <= 10):
    raise ValueError("UNARMED must satisfy the same weapon-profile bounds as shops.CATALOG")

# A crit still hits harder — it's whatever margin (net successes) it takes to clear
# checks.CRITICAL_MARGIN, which feeds straight into the margin resolve_hit adds to
# base damage. There's no separate multiplier; the margin does that work on its own.

# Bracing (Toughness) adds this much to the soak roll for *every* hit you take that
# round, so it scales with how badly you're outnumbered — the answer to being swarmed.
BRACE_DIFFICULTY = 11
BRACE_SOAK = 3
# A failed brace is not a wasted round; you still get something for covering up.
BRACE_SOAK_ON_FAILURE = 1

# Reading the fight (Tactics) buys your *next* attack this much. Two rounds of
# setup for one big swing is a real choice when they're hitting you meanwhile.
READ_DIFFICULTY = 12
READ_BONUS = 4

# Facing them down (Intimidation): break the nerve of the enemy with the least
# health left and they run. Scales off the enemy's own defense, so the last one
# standing is easier to scare than a fresh squad.
INTIMIDATE_DIFFICULTY_BONUS = 2

# Running (Dodge). Running *always works* — the check only decides what it costs you.
#
# It used to be a check you could fail, and that was the single most lethal thing in
# the module: flee rolls Dodge, and the build most likely to need the exit (a Hacker,
# 15 health, no Agility) is exactly the build that can't make the roll. It failed ~65%
# of the time, ate the round, and the squad kept swinging — so the escape valve was
# shut for precisely the runner it existed for, and the sim showed them dying in a
# third of their fights. A fight must never be a cage: you can always walk out, and a
# clean break is what the Dodge check buys. Miss it and every one of them gets a
# parting shot as you turn your back.
FLEE_DIFFICULTY = 10
FLEE_DIFFICULTY_PER_ENEMY = 2

# What "getting the drop" is worth, in whichever direction it points.
#
# A free round is the obvious payoff, and on its own it is far too small a one: in a
# four-round fight it's a 25% swing, which is not enough to make *choosing* a fight
# meaningfully better than being dropped into one — the balance sim had the ambush
# killing a Hacker 22% of the time it was taken, which makes the "guaranteed way
# through" a trap rather than a way through.
#
# So a landed ambush also takes one of them off the board before the fight starts: you
# caught a straggler away from the squad. Enemy *count* is the real lethality lever
# (every one of them swings at you every round), so this is the lever that matters,
# and it reads right — the difference between picking your moment and having the alarm
# bring everyone is who you have to fight, not just who moves first.
FREE_ROUND = 1


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


class CombatOutcome(StrEnum):
    ONGOING = "ongoing"
    VICTORY = "victory"
    ESCAPED = "escaped"
    DEAD = "dead"
    KNOCKED_OUT = "knocked_out"


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
    stun_damage: int = 0  # non-lethal stun damage dealt per hit (0 = none)


# id, name, health, attack, defense, damage, toughness.
# The ladder the tiers draw from: a thug is a nuisance, a chromed enforcer is a
# death sentence to a runner who brought the wrong build. Tuned against a runner's
# 15-30 health and DAMAGE_FOR_DELTA in jobs.py — see the balance sim before touching.
_ENEMY_ROWS = (
    ("thug", "Street Thug", 4, 1, 9, 2, 1),
    ("ganger", "Ganger", 5, 2, 10, 2, 2),
    ("corp_sec", "Corp Sec", 7, 2, 11, 3, 2),
    ("sec_heavy", "Sec Heavy", 9, 3, 12, 3, 3),
    ("enforcer", "Chromed Enforcer", 11, 4, 13, 4, 4),
)

ENEMIES = [Enemy(*row) for row in _ENEMY_ROWS]
ENEMIES_BY_ID = {enemy.id: enemy for enemy in ENEMIES}

# Job tier (jobs._tier_for_day) -> who turns up, and how many. The count is the real
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


@dataclass
class Fighter:
    """A live enemy in a fight: the Enemy is the template, this is the one bleeding."""

    enemy: Enemy
    health: int
    stunned_rounds: int = 0
    # Accumulated stun damage (non-lethal). When stun >= health, the fighter is
    # incapacitated — same effect as reaching 0 health but they stay alive.
    # Stun builds up from 0, health drops from max; when they meet, they're stunned.
    stun: int = 0

    @property
    def is_standing(self) -> bool:
        return self.health > 0


class ActionKind(StrEnum):
    ATTACK = "attack"
    BRACE = "brace"
    READ = "read"
    INTIMIDATE = "intimidate"
    FLEE = "flee"
    CONSUMABLE = "consumable"


@dataclass(frozen=True)
class Action:
    """One thing you can do with a round, and what it rolls.

    `weapon` is set on ATTACK, `consumable_index` on CONSUMABLE (an index into
    Character.consumables, not an id — the same grenade can be carried twice, and
    the one you throw is a specific one of them). `skill` is None only for
    CONSUMABLE, which is the one action that isn't a check: a grenade goes off.
    """

    kind: ActionKind
    label: str
    skill: str | None = None
    weapon: Item | None = None
    consumable_index: int | None = None


def player_defense(character: Character) -> int:
    return DEFENSE_BASE + skill_value(character, "dodge")


def player_soak(character: Character) -> int:
    """Body + equipped armor's defense: the player's soak pool size (dice rolled to
    mitigate a landed hit — see resolve_hit).

    Bracing (CombatState.soak) is added on top of this per-round, at the call site
    — it is not part of the character's standing soak, since it clears at round end.
    """
    return character.stat("body") + equipped_defense(character.inventory)


def _soak_damage(rng: random.Random, base_damage: int, soak_pool: int) -> int:
    """Roll soak_pool d6 and take the successes off base_damage, floored at 0.

    The shared tail end of any damage a target takes, whether it followed a to-hit
    roll (resolve_hit) or was guaranteed (a flee's parting shot) — the soak isn't
    opposed by anything, so it doesn't go through the four-tier CheckResult, just a
    plain success count.
    """
    return max(0, base_damage - count_successes(soak_pool, rng))


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
    return roll, _soak_damage(rng, base_damage + roll.margin, soak_pool)


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


def _combat_consumables(character: Character) -> list[tuple[int, Consumable]]:
    """The grenades, and only the grenades — see shops.COMBAT_ONLY_EFFECTS.

    Notably not health kits: healing mid-fight would make a fight the cheapest place
    to spend one, and health does not come back fast enough in this game for that to
    be anything but a grind.
    """
    return [
        (index, CONSUMABLES_BY_ID[consumable_id])
        for index, consumable_id in enumerate(character.consumables)
        if CONSUMABLES_BY_ID[consumable_id].effect in COMBAT_ONLY_EFFECTS
    ]


def available_actions(
    character: Character, cooldowns: dict[str, int] | None = None
) -> list[Action]:
    """Everything the runner can do this round.

    One attack per equipped weapon (excluding any on cooldown), the four stat-spread
    options, and one row per grenade actually carried. Always non-empty: bare hands,
    bracing and running are unconditional, so a round can never present an empty list.
    """
    weapons = [
        weapon
        for weapon in equipped_weapons(character)
        if not (cooldowns and cooldowns.get(weapon.id, 0) > 0)
    ]
    # If every weapon is on cooldown, you can still use your fists.
    if not weapons:
        weapons = [UNARMED]
    actions = [
        Action(
            kind=ActionKind.ATTACK,
            label=_weapon_label(weapon),
            skill=weapon.skill,
            weapon=weapon,
        )
        for weapon in weapons
    ]


    actions.append(
        Action(kind=ActionKind.BRACE, label="Brace for it (Toughness)", skill="toughness")
    )
    actions.append(Action(kind=ActionKind.READ, label="Read the fight (Tactics)", skill="tactics"))
    actions.append(
        Action(kind=ActionKind.INTIMIDATE, label="Face them down (Intimidation)", skill="intimidation")
    )
    actions.extend(
        Action(
            kind=ActionKind.CONSUMABLE,
            label=f"Throw {consumable.name}",
            consumable_index=index,
        )
        for index, consumable in _combat_consumables(character)
    )
    actions.append(Action(kind=ActionKind.FLEE, label="Break and run (Dodge)", skill="dodge"))
    return actions


def _weapon_label(weapon: Item) -> str:
    """Action label for an attack with this weapon."""
    parts = []
    if weapon.damage:
        parts.append(f"{weapon.damage} dmg")
    if weapon.stun_damage:
        parts.append(f"{weapon.stun_damage} stun")
    profile = " + ".join(parts) if parts else "?"
    return f"Attack with {weapon.name} ({skill_for(weapon.skill).name}, {profile})"


@dataclass
class CombatState:
    """A fight in progress. The screen renders this; take_turn advances it."""

    character: Character
    fighters: list[Fighter]
    outcome: CombatOutcome = CombatOutcome.ONGOING
    log: list[str] = field(default_factory=list)
    # Banked by READ, spent by the next ATTACK. Not a permanent buff: setting up a
    # shot you never take is a round you gave away.
    next_attack_bonus: int = 0
    # Soak from a BRACE, applied to every hit this round and then cleared.
    soak: int = 0
    # Rounds the enemies owe you (a landed ambush, a flash grenade).
    enemy_skip_rounds: int = 0
    # weapon id (shops.ITEMS_BY_ID) -> rounds remaining before it can fire again.
    # Populated by _attack when the weapon has recharge_rounds > 0; decremented at
    # round end in take_turn. Resets between fights (CombatState is per-fight).
    weapon_cooldowns: dict[str, int] = field(default_factory=dict)
    # Player's accumulated stun damage this fight. Starts at 0, goes up; when it
    # meets or exceeds the player's current health, the outcome becomes
    # KNOCKED_OUT — the fight ends (see _stun_player). Clears with CombatState.
    player_stun: int = 0

    @property
    def standing(self) -> list[Fighter]:
        return [fighter for fighter in self.fighters if fighter.is_standing]

    @property
    def is_over(self) -> bool:
        return self.outcome is not CombatOutcome.ONGOING


def start_combat(
    character: Character,
    enemies: tuple[Enemy, ...],
    drop: Drop = Drop.NONE,
    rng: random.Random | None = None,
) -> CombatState:
    """Open a fight. An enemy drop is paid immediately, before you get to act."""
    state = CombatState(
        character=character,
        fighters=[Fighter(enemy=enemy, health=enemy.health) for enemy in enemies],
    )
    if drop is Drop.PLAYER:
        state.enemy_skip_rounds = FREE_ROUND
        state.log.append("You have the drop on them.")
        # Never the last one standing: taking out a lone enemy before the fight would
        # be a fight you never had, and a stage you passed for free.
        if len(state.fighters) > 1:
            straggler = state.fighters[-1]
            straggler.health = 0
            state.log.append(f"You put {straggler.enemy.name} down before they see you.")
    elif drop is Drop.ENEMY:
        # They were waiting for you: a free hit before you can act, which is what
        # makes going loud worse than picking the fight. One of them, though, not all
        # of them — a whole squad's round landing on top of the critical failure's own
        # damage, before the runner has taken a single action, is a nat-1 killing a
        # light build outright, and that fight was never chosen.
        rng = rng or random
        first = state.fighters[0]
        state.log.append("They were ready for you.")
        # No BRACE bonus here — it's a free hit before your first action, so there's
        # been no round to brace in yet.
        roll, damage = resolve_hit(
            rng, first.enemy.attack, 0, player_defense(character), first.enemy.damage,
            player_soak(character),
        )
        if roll.result.passed:
            character.adjust_health(-damage)
            if damage:
                state.log.append(f"{first.enemy.name} opens on you for {damage}.")
            else:
                state.log.append(f"{first.enemy.name} opens on you, but it doesn't get through.")
        else:
            state.log.append(f"{first.enemy.name} fires first, and misses.")
        _settle(state)
    return state


def _damage_fighter(state: CombatState, fighter: Fighter, damage: int) -> None:
    fighter.health = max(0, fighter.health - damage)
    if not fighter.is_standing:
        state.log.append(f"{fighter.enemy.name} goes down.")


def _stun_fighter(state: CombatState, fighter: Fighter, stun_amount: int) -> None:
    """Apply stun damage to a fighter. If stun >= health, they're incapacitated."""
    fighter.stun += stun_amount
    state.log.append(f"{fighter.enemy.name} reels from the shock ({fighter.stun} stun).")
    if fighter.stun >= fighter.enemy.health:
        fighter.health = 0
        state.log.append(f"{fighter.enemy.name} is stunned unconscious.")


def _stun_player(state: CombatState, stun_amount: int) -> None:
    """Apply stun damage to the player. If stun >= health, they're knocked out."""
    state.player_stun += stun_amount
    state.log.append(f"Your nerves crackle ({state.player_stun} stun).")
    if state.player_stun >= state.character.health:
        state.outcome = CombatOutcome.KNOCKED_OUT
        state.log.append("You're knocked out.")


def _attack(state: CombatState, action: Action, rng: random.Random) -> None:
    # You fight through them in order: no targeting step, so being outnumbered costs
    # you rounds rather than clicks. Grenades are how you hit the back of the pack.
    target = state.standing[0]
    weapon = action.weapon
    bonus = state.next_attack_bonus
    state.next_attack_bonus = 0

    roll, damage = resolve_hit(
        rng,
        skill_value(state.character, weapon.skill),
        bonus,
        target.enemy.defense,
        weapon.damage,
        target.enemy.toughness,
    )
    if not roll.result.passed:
        state.log.append(f"You swing at {target.enemy.name} and miss.")
        return

    if weapon.stun_damage:
        parts = [f"{weapon.stun_damage} stun"]
        if damage:
            parts.insert(0, f"{damage} damage")
        state.log.append(
            f"You land {weapon.name} on {target.enemy.name} for {' and '.join(parts)}."
        )
    elif damage:
        prefix = "Critical hit — " if roll.result is CheckResult.CRITICAL_SUCCESS else ""
        state.log.append(f"{prefix}You land {weapon.name} on {target.enemy.name} for {damage}.")
    else:
        state.log.append(f"You land {weapon.name} on {target.enemy.name}, but it doesn't get through.")
    _damage_fighter(state, target, damage)
    if weapon.stun_damage:
        _stun_fighter(state, target, weapon.stun_damage)
    if weapon.recharge_rounds:
        state.weapon_cooldowns[weapon.id] = weapon.recharge_rounds


def _brace(state: CombatState, rng: random.Random) -> None:
    roll = resolve_check(
        stat_value=skill_value(state.character, "toughness"),
        difficulty=BRACE_DIFFICULTY,
        rng=rng,
    )
    hit = roll.result.passed
    state.soak = BRACE_SOAK if hit else BRACE_SOAK_ON_FAILURE
    state.log.append(
        f"You set yourself. +{state.soak} to your soak roll against every hit this round."
        if hit
        else f"You cover up badly. Only +{state.soak} to your soak roll."
    )


def _read(state: CombatState, rng: random.Random) -> None:
    roll = resolve_check(
        stat_value=skill_value(state.character, "tactics"),
        difficulty=READ_DIFFICULTY,
        rng=rng,
    )
    if roll.result.passed:
        state.next_attack_bonus += READ_BONUS
        state.log.append(f"You see the opening. +{READ_BONUS} to your next attack.")
    else:
        state.log.append("You can't read them. The round is wasted.")


def _intimidate(state: CombatState, rng: random.Random) -> None:
    target = min(state.standing, key=lambda fighter: fighter.health)
    roll = resolve_check(
        stat_value=skill_value(state.character, "intimidation"),
        difficulty=target.enemy.defense + INTIMIDATE_DIFFICULTY_BONUS,
        rng=rng,
    )
    if roll.result.passed:
        # Not killed — gone. Same effect on the fight, different story, and it's the
        # only way to end a fight without putting anyone on the floor.
        target.health = 0
        state.log.append(f"{target.enemy.name} breaks and runs.")
    else:
        state.log.append("They don't scare.")


def _throw(state: CombatState, action: Action) -> None:
    consumable = CONSUMABLES_BY_ID[state.character.consumables.pop(action.consumable_index)]
    if consumable.effect is EffectKind.COMBAT_DAMAGE_ALL:
        state.log.append(f"{consumable.name} — {consumable.amount} to everything standing.")
        for fighter in list(state.standing):
            _damage_fighter(state, fighter, consumable.amount)
    elif consumable.effect is EffectKind.COMBAT_STUN:
        for fighter in state.standing:
            fighter.stunned_rounds = consumable.amount
        state.log.append(f"{consumable.name} — they're blind and deaf for {consumable.amount}.")
    elif consumable.effect is EffectKind.COMBAT_ESCAPE:
        # The one exit with no parting shot at all — not even the failed-Dodge one.
        # That's what you paid for.
        state.outcome = CombatOutcome.ESCAPED
        state.log.append(f"{consumable.name} — you walk out of the fight clean.")
    else:
        # Same guard as shops.use_consumable, from the other side: a new combat-only
        # effect with no branch here would otherwise be popped and silently do nothing.
        raise ValueError(f"consumable effect not handled in combat: {consumable.effect}")


def _flee(state: CombatState, rng: random.Random) -> None:
    difficulty = FLEE_DIFFICULTY + FLEE_DIFFICULTY_PER_ENEMY * len(state.standing)
    roll = resolve_check(
        stat_value=skill_value(state.character, "dodge"),
        difficulty=difficulty,
        rng=rng,
    )
    # Escaping either way — see FLEE_DIFFICULTY. The roll only decides the bill.
    state.outcome = CombatOutcome.ESCAPED
    if roll.result.passed:
        state.log.append("You break contact clean and go.")
        return

    # One parting shot, from whoever is closest — not one from every enemy. A whole
    # squad's worth of free hits is what a runner eats *because* they were low enough
    # to be running, so it turned the exit into the thing that killed them. It's
    # guaranteed (no to-hit roll, you're already turning your back), but still goes
    # through your soak roll — armor helps even on the way out.
    catcher = state.standing[0]
    damage = _soak_damage(rng, catcher.enemy.damage, player_soak(state.character))
    state.character.adjust_health(-damage)
    if damage:
        state.log.append(f"You run. {catcher.enemy.name} catches you for {damage}.")
    else:
        state.log.append(f"You run. {catcher.enemy.name} gets a shot off, but it doesn't land clean.")
    # If the parting shot kills you, _settle turns ESCAPED into DEAD right after.


def _enemy_turn(state: CombatState, rng: random.Random) -> None:
    if state.enemy_skip_rounds > 0:
        state.enemy_skip_rounds -= 1
        state.log.append("They're still catching up. You get this one free.")
        return

    defense = player_defense(state.character)
    # Bracing (state.soak) folds into the soak pool here, not the final damage — so
    # it applies per hit, for every attacker this round, same as before.
    soak_pool = player_soak(state.character) + state.soak
    for fighter in state.standing:
        if fighter.stunned_rounds > 0:
            fighter.stunned_rounds -= 1
            state.log.append(f"{fighter.enemy.name} is still reeling.")
            continue
        roll, damage = resolve_hit(
            rng, fighter.enemy.attack, 0, defense, fighter.enemy.damage, soak_pool
        )
        if not roll.result.passed:
            state.log.append(f"{fighter.enemy.name} swings wide.")
            continue
        state.character.adjust_health(-damage)
        if fighter.enemy.stun_damage:
            _stun_player(state, fighter.enemy.stun_damage)
        if damage:
            state.log.append(f"{fighter.enemy.name} hits you for {damage}.")
        else:
            state.log.append(f"{fighter.enemy.name} connects, but your armor holds.")


def _settle(state: CombatState) -> None:
    """Read the board after a turn. Death beats victory: a mutual knockout kills you.
    KNOCKED_OUT is already set by _stun_player and is terminal — don't overwrite."""
    if not state.character.is_alive:
        state.outcome = CombatOutcome.DEAD
    elif not state.standing and state.outcome is not CombatOutcome.KNOCKED_OUT:
        state.outcome = CombatOutcome.VICTORY


def take_turn(state: CombatState, action: Action, rng: random.Random | None = None) -> None:
    """Resolve one full round: your action, then theirs."""
    rng = rng or random
    if state.is_over:
        return

    if action.kind is ActionKind.ATTACK:
        _attack(state, action, rng)
    elif action.kind is ActionKind.BRACE:
        _brace(state, rng)
    elif action.kind is ActionKind.READ:
        _read(state, rng)
    elif action.kind is ActionKind.INTIMIDATE:
        _intimidate(state, rng)
    elif action.kind is ActionKind.CONSUMABLE:
        _throw(state, action)
    elif action.kind is ActionKind.FLEE:
        _flee(state, rng)

    _settle(state)
    if state.is_over:
        return

    _enemy_turn(state, rng)
    _settle(state)
    # Soak was bought for the round that just resolved, not the next one.
    state.soak = 0
    # Weapon cooldowns tick down at round end, so a weapon fired this round
    # stays unavailable through the cooldown's full duration.
    for weapon_id in list(state.weapon_cooldowns):
        state.weapon_cooldowns[weapon_id] -= 1
        if state.weapon_cooldowns[weapon_id] <= 0:
            del state.weapon_cooldowns[weapon_id]

