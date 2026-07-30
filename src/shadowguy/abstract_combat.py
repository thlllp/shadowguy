"""Fight surface 1: abstract rounds, with no positions in them.

The shape of a round:

    you take one Action  ->  every standing enemy that isn't stunned attacks you

and that repeats until one side is down or you walk out. Nothing here decides
what a fight *means* to the job around it: this module reports how it ended (see
CombatOutcome) and scene/app map that onto the Encounter's victory/escape
Outcomes.

The rule that carries most of the design: **every action rolls a different core
stat.** Attacking is strength (or perception, with a gun); bracing is body;
reading the fight is intelligence; facing them down is cool; running is agility.
A fight is therefore not a Strength minigame that only an Enforcer can play —
it's the same "every build has a way through, but not the same way" rule that
jobs.py enforces across a stage's approaches, applied to a round.

What a fight is *made of* — the enemy roster, the hit formula, your defense and
soak, which weapons are in your hands — is combat.py, shared with the other two
fight surfaces. This module is only the round loop over it, which is why
tactical.py and matrix.py import combat and not this. Like combat.py it imports
no scene: it owns *how a round resolves* and nothing about what that's worth.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check, resolve_rng
from shadowguy.combat import (
    UNARMED,
    Drop,
    Enemy,
    attack_verbs,
    combat_consumables,
    equipped_weapons,
    player_defense,
    player_soak,
    resolve_hit,
    smartlink_bonus,
    soak_damage,
)
from shadowguy.shops import CONSUMABLES_BY_ID, EffectKind, Item
from shadowguy.skills import skill_for, skill_value

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


class CombatOutcome(StrEnum):
    ONGOING = "ongoing"
    VICTORY = "victory"
    ESCAPED = "escaped"
    DEAD = "dead"
    KNOCKED_OUT = "knocked_out"


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
        for index, consumable in combat_consumables(character)
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
        rng = resolve_rng(rng)
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
    """Apply stun damage to the player. Character.stun is persistent (carries
    into the next fight, only Rest clears it — see Character.mark_rested), so
    entering a fight already stunned makes the threshold below easier to reach.
    If stun >= current health, they're knocked out."""
    character = state.character
    character.adjust_stun(stun_amount)
    state.log.append(f"Your nerves crackle ({character.stun} stun).")
    if character.stun >= character.health:
        state.outcome = CombatOutcome.KNOCKED_OUT
        state.log.append("You're knocked out.")


def _attack(state: CombatState, action: Action, rng: random.Random) -> None:
    # You fight through them in order: no targeting step, so being outnumbered costs
    # you rounds rather than clicks. Grenades are how you hit the back of the pack.
    target = state.standing[0]
    weapon = action.weapon
    bonus = state.next_attack_bonus + smartlink_bonus(state.character, weapon)
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
        state.log.append(f"You {attack_verbs(weapon)[0]} {target.enemy.name} and miss.")
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
        # Same guard as inventory.use_consumable, from the other side: a new combat-only
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
    damage = soak_damage(rng, catcher.enemy.damage, player_soak(state.character))
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


def _tick_cooldowns(state: CombatState) -> None:
    for weapon_id in list(state.weapon_cooldowns):
        state.weapon_cooldowns[weapon_id] -= 1
        if state.weapon_cooldowns[weapon_id] <= 0:
            del state.weapon_cooldowns[weapon_id]


def _perform_action(state: CombatState, action: Action, rng: random.Random) -> None:
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


def take_turn(state: CombatState, action: Action, rng: random.Random | None = None) -> None:
    rng = resolve_rng(rng)
    if state.is_over:
        return

    _perform_action(state, action, rng)
    _settle(state)
    if state.is_over:
        return

    _enemy_turn(state, rng)
    _settle(state)
    state.soak = 0
    _tick_cooldowns(state)
