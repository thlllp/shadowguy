"""Combat: rounds, enemies, and the one place a fight's rules live.

Combat is the only part of the game that is not a single check. It is still the
same dice, though — every roll here is checks.resolve_check(), d20 + skill_value
vs a difficulty, with the same crit rules — so a fight is a *sequence* of the
game's existing checks rather than a second resolution model bolted on beside it.

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
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check
from shadowguy.shops import (
    COMBAT_ONLY_EFFECTS,
    CONSUMABLES_BY_ID,
    ITEMS_BY_ID,
    Consumable,
    EffectKind,
    Item,
    Slot,
)
from shadowguy.skills import skill_for, skill_value

# What an enemy's d20 + attack has to beat to land a hit on you. Defense is built
# from Dodge (skill_value, so gear and rank both count), which is what stops
# Agility from being a stat you only spend on job approaches.
DEFENSE_BASE = 12

# Empty-handed. A real weapon is strictly better, but there is always *an* attack:
# a runner who sold their last knife can still fight, badly.
UNARMED = Item(
    id="unarmed",
    name="Bare Hands",
    price=0,
    bonuses={},
    slot=Slot.WEAPON,
    skill="grapple",
    damage=3,
)

# A crit doubles what a blow takes off — the same idea as jobs.CRITICAL_FAILURE_MULTIPLIER,
# and it cuts both ways: enemies crit you too.
CRITICAL_DAMAGE_MULTIPLIER = 2

# Bracing (Toughness) soaks this much off *each* hit you take that round, so it
# scales with how badly you're outnumbered — the answer to being swarmed.
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
AMBUSH_REMOVES_ENEMY = True


class Drop(StrEnum):
    """Who, if anyone, started the fight on their terms.

    Derived by the caller from the check that routed into the fight (a made ambush
    vs a botched approach), which is why the Encounter itself doesn't carry it —
    the same encounter is a different fight depending on how you walked into it.
    """

    PLAYER = "player"  # your ambush landed: they lose the first round
    NONE = "none"  # a straight fight
    ENEMY = "enemy"  # you were made: they get a free round before you act


def drop_for_result(result: CheckResult) -> Drop:
    """Who got the drop, read straight off the check that routed you into the fight.

    This is the whole reason a fight needs no extra data on Outcome to know how it
    started. One rule covers both doors into combat, because the *result* already
    says everything:

    - You made your ambush (jobs.AMBUSH_SKILL): success, so you picked the moment.
    - You missed it: an even fight — you moved too early, but you still moved first.
    - You critically failed *anything*: they were waiting. This is also the only way
      a normal approach reaches a fight at all, which is why going loud always hands
      the initiative to them and choosing the fight never does.
    """
    if result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
        return Drop.PLAYER
    if result is CheckResult.CRITICAL_FAILURE:
        return Drop.ENEMY
    return Drop.NONE


class CombatOutcome(StrEnum):
    ONGOING = "ongoing"
    VICTORY = "victory"
    ESCAPED = "escaped"
    DEAD = "dead"


@dataclass(frozen=True)
class Enemy:
    """One hostile. `defense` is the difficulty your attack rolls against."""

    id: str
    name: str
    health: int
    attack: int  # added to the enemy's d20
    defense: int  # what your attack roll must beat
    damage: int  # health off you on a hit


# id, name, health, attack, defense, damage.
# The ladder the tiers draw from: a thug is a nuisance, a chromed enforcer is a
# death sentence to a runner who brought the wrong build. Tuned against a runner's
# 15-30 health and DAMAGE_FOR_DELTA in jobs.py — see the balance sim before touching.
_ENEMY_ROWS = (
    ("thug", "Street Thug", 4, 1, 9, 2),
    ("ganger", "Ganger", 5, 2, 10, 2),
    ("corp_sec", "Corp Sec", 7, 2, 11, 3),
    ("sec_heavy", "Sec Heavy", 9, 3, 12, 3),
    ("enforcer", "Chromed Enforcer", 11, 4, 13, 4),
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


def available_actions(character: Character) -> list[Action]:
    """Everything the runner can do this round.

    One attack per equipped weapon, the four stat-spread options, and one row per
    grenade actually carried. Always non-empty: bare hands, bracing and running are
    unconditional, so a round can never present an empty list.
    """
    actions = [
        Action(
            kind=ActionKind.ATTACK,
            label=f"Attack with {weapon.name} ({skill_for(weapon.skill).name}, {weapon.damage} dmg)",
            skill=weapon.skill,
            weapon=weapon,
        )
        for weapon in equipped_weapons(character)
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


@dataclass
class CombatState:
    """A fight in progress. The screen renders this; take_turn advances it."""

    character: Character
    fighters: list[Fighter]
    drop: Drop = Drop.NONE
    round: int = 1
    outcome: CombatOutcome = CombatOutcome.ONGOING
    log: list[str] = field(default_factory=list)
    # Banked by READ, spent by the next ATTACK. Not a permanent buff: setting up a
    # shot you never take is a round you gave away.
    next_attack_bonus: int = 0
    # Soak from a BRACE, applied to every hit this round and then cleared.
    soak: int = 0
    # Rounds the enemies owe you (a landed ambush, a flash grenade).
    enemy_skip_rounds: int = 0

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
        drop=drop,
    )
    if drop is Drop.PLAYER:
        state.enemy_skip_rounds = FREE_ROUND
        state.log.append("You have the drop on them.")
        # Never the last one standing: taking out a lone enemy before the fight would
        # be a fight you never had, and a stage you passed for free.
        if AMBUSH_REMOVES_ENEMY and len(state.fighters) > 1:
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
        roll = resolve_check(
            stat_value=first.enemy.attack, difficulty=player_defense(character), rng=rng
        )
        if roll.result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
            character.adjust_health(-first.enemy.damage)
            state.log.append(f"{first.enemy.name} opens on you for {first.enemy.damage}.")
        else:
            state.log.append(f"{first.enemy.name} fires first, and misses.")
        _settle(state)
    return state


def _damage_fighter(state: CombatState, fighter: Fighter, damage: int) -> None:
    fighter.health = max(0, fighter.health - damage)
    if not fighter.is_standing:
        state.log.append(f"{fighter.enemy.name} goes down.")


def _attack(state: CombatState, action: Action, rng: random.Random) -> None:
    # You fight through them in order: no targeting step, so being outnumbered costs
    # you rounds rather than clicks. Grenades are how you hit the back of the pack.
    target = state.standing[0]
    weapon = action.weapon
    bonus = state.next_attack_bonus
    state.next_attack_bonus = 0

    roll = resolve_check(
        stat_value=skill_value(state.character, weapon.skill),
        difficulty=target.enemy.defense,
        advantage=bonus,
        rng=rng,
    )
    if roll.result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
        damage = weapon.damage
        if roll.result is CheckResult.CRITICAL_SUCCESS:
            damage *= CRITICAL_DAMAGE_MULTIPLIER
            state.log.append(f"Critical hit — {weapon.name} for {damage}.")
        else:
            state.log.append(f"You land {weapon.name} on {target.enemy.name} for {damage}.")
        _damage_fighter(state, target, damage)
    else:
        state.log.append(f"You swing at {target.enemy.name} and miss.")


def _brace(state: CombatState, rng: random.Random) -> None:
    roll = resolve_check(
        stat_value=skill_value(state.character, "toughness"),
        difficulty=BRACE_DIFFICULTY,
        rng=rng,
    )
    hit = roll.result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS)
    state.soak = BRACE_SOAK if hit else BRACE_SOAK_ON_FAILURE
    state.log.append(
        f"You set yourself. Soaking {state.soak} off every hit this round."
        if hit
        else f"You cover up badly. Soaking only {state.soak}."
    )


def _read(state: CombatState, rng: random.Random) -> None:
    roll = resolve_check(
        stat_value=skill_value(state.character, "tactics"),
        difficulty=READ_DIFFICULTY,
        rng=rng,
    )
    if roll.result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
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
    if roll.result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
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


def _flee(state: CombatState, rng: random.Random) -> None:
    difficulty = FLEE_DIFFICULTY + FLEE_DIFFICULTY_PER_ENEMY * len(state.standing)
    roll = resolve_check(
        stat_value=skill_value(state.character, "dodge"),
        difficulty=difficulty,
        rng=rng,
    )
    # Escaping either way — see FLEE_DIFFICULTY. The roll only decides the bill.
    state.outcome = CombatOutcome.ESCAPED
    if roll.result in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
        state.log.append("You break contact clean and go.")
        return

    # One parting shot, from whoever is closest — not one from every enemy. A whole
    # squad's worth of free hits is what a runner eats *because* they were low enough
    # to be running, so it turned the exit into the thing that killed them.
    catcher = state.standing[0]
    state.character.adjust_health(-catcher.enemy.damage)
    state.log.append(f"You run. {catcher.enemy.name} catches you for {catcher.enemy.damage}.")
    if not state.character.is_alive:
        state.outcome = CombatOutcome.DEAD


def _enemy_turn(state: CombatState, rng: random.Random) -> None:
    if state.enemy_skip_rounds > 0:
        state.enemy_skip_rounds -= 1
        state.log.append("They're still catching up. You get this one free.")
        return

    defense = player_defense(state.character)
    for fighter in state.standing:
        if fighter.stunned_rounds > 0:
            fighter.stunned_rounds -= 1
            state.log.append(f"{fighter.enemy.name} is still reeling.")
            continue
        roll = resolve_check(stat_value=fighter.enemy.attack, difficulty=defense, rng=rng)
        if roll.result not in (CheckResult.SUCCESS, CheckResult.CRITICAL_SUCCESS):
            state.log.append(f"{fighter.enemy.name} swings wide.")
            continue
        damage = fighter.enemy.damage
        if roll.result is CheckResult.CRITICAL_SUCCESS:
            damage *= CRITICAL_DAMAGE_MULTIPLIER
        # Soak is per hit, not per round: bracing is how you survive a crowd.
        damage = max(0, damage - state.soak)
        state.character.adjust_health(-damage)
        state.log.append(f"{fighter.enemy.name} hits you for {damage}.")


def _settle(state: CombatState) -> None:
    """Read the board after a turn. Death beats victory: a mutual knockout kills you."""
    if not state.character.is_alive:
        state.outcome = CombatOutcome.DEAD
    elif not state.standing:
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
    state.round += 1
