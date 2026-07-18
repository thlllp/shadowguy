"""Matrix combat: the netrunner's fight, against ICE instead of muscle.

This is a *parallel* combat surface to combat.py, not a reskin of it. It shares the
one thing the whole game shares — every roll goes through checks.resolve_check, and a
landed intrusion is sized by combat.resolve_hit, the same public hit primitive
tactical.py reuses (one hit formula, now three surfaces). What it deliberately does
*not* share is the stakes and the state:

- **The runner's integrity is a separate pool from health.** ICE drains integrity, and
  running it dry doesn't kill you — it ejects you (MatrixOutcome.EJECTED). A *remote*
  Data Heist (the only kind today) is fought jacked in from outside, so losing blows the
  contract but never the run; there is no death in the matrix. Integrity is per-fight,
  rebuilt from Intelligence each time a MatrixScreen opens, so nothing here persists onto
  the Character the way health does.
- **The actions are Intelligence's, not the six-stat spread.** The matrix is the
  Hacker's arena on purpose (unlike meat combat, which spans every stat so no build is
  locked out of a round): you breach with Hack, harden with Tinkering, analyze with
  Infer. A non-hacker can still fight here, but bleeds — the deckless/low-Hack warning
  (matrix_readiness) is the honest heads-up, not a lockout.

Like combat.py, this module is a leaf on the scene graph: it imports character, checks,
combat, shops and skills, and never scene — which is what lets scene.MatrixStage hold
Ice without a cycle. combat owns *how a meat fight resolves*; this owns *how a matrix
fight resolves*; scene.MatrixStage owns *what seizing the data or being ejected is
worth*, through an ordinary Outcome, on the same reward path as every other stage.

The `drop` a matrix fight opens on is read off the check that routed you in, exactly
like combat (combat.drop_for_result): breaching cleanly (the ambush) buys a free round;
tripping black ICE (a critical failure) hands one to the ICE.

Left room, not built: an on-site variant (a hacker embedded with the muscle) that boots
you out *painfully* — a health cost — instead of blowing the run. That's an EJECT_COST
and a caller flag away, not a second engine, which is why loss is already funnelled
through a single MatrixOutcome.EJECTED rather than a die-here branch.

**Cyberdecks carry programs** (shops.Item.program_slots / Program), the netrunner's
loadout the way combat.py's weapons are the meat runner's: each installed Program is
either a passive bonus (folded straight into player_integrity/firewall_defense/
firewall_soak/player_attack_damage) or, if Program.uses_per_fight > 0, a limited-use
MatrixAction (MatrixActionKind.PROGRAM) offered alongside the four fixed actions —
uses_per_fight alone tells the two apart, so there's no separate kind field to drift
out of sync with the bonus fields actually set. Only the *active* deck's programs count
(shops.active_deck_entry — the same one equipped_deck_rating already reads off), and
charges (MatrixState.program_uses) are per-fight, seeded fresh in start_matrix like
integrity itself.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, resolve_check, resolve_rng
from shadowguy.combat import Drop, resolve_hit
from shadowguy.shops import Program, active_deck_entry, equipped_deck_rating, installed_programs_for
from shadowguy.skills import skill_value

# The runner's matrix hit points, rebuilt each fight from Intelligence (gear included,
# unlike health's raw Body — a better deck raises Int, so it buys resilience in the
# matrix as well as reach). A fresh Int-1 runner brings 7; a decked Hacker brings ~20.
BASE_INTEGRITY = 5
INTEGRITY_PER_INT = 2

# What an ICE program's attack pool has to beat to bite your integrity: your firewall,
# built from Infer (reading the system to slip its countermeasures) the way combat's
# player_defense is built from Dodge. Same DEFENSE_BASE convention as combat.py.
FIREWALL_BASE = 12

# Your attack rolls Hack; your *damage* comes from the deck, not the skill (equipped_
# deck_rating), the same split combat.py keeps between skill (the hit) and weapon (the
# damage). A bare jack-in still does something, just badly — the matrix's UNARMED.
BARE_JACK_DAMAGE = 1
DECK_BASE_DAMAGE = 2

# Hardening (Tinkering): patch your firewall for +soak against every ICE hit this round,
# scaling with how outnumbered you are — combat's BRACE, in the matrix.
HARDEN_DIFFICULTY = 11
HARDEN_SOAK = 3
HARDEN_SOAK_ON_FAILURE = 1

# Analyzing (Infer): read an ICE's shape to buy your *next* intrusion — combat's READ.
ANALYZE_DIFFICULTY = 12
ANALYZE_BONUS = 4

# A landed clean breach (the ambush drop) buys a free round, same lever and same reason
# as combat.FREE_ROUND: ICE *count* is the lethality knob, since every standing program
# claws at your integrity every round.
FREE_ROUND = 1


@dataclass(frozen=True)
class Ice:
    """One security program. The matrix analogue of combat.Enemy: `integrity` is its
    hit points, `defense` the difficulty your Hack roll must beat, `damage` the integrity
    it takes off you on a hit, `soak` its own mitigation roll (its hardening)."""

    id: str
    name: str
    integrity: int
    attack: int  # dice the ICE rolls against your firewall
    defense: int  # what your intrusion roll must beat
    damage: int  # integrity off you on a hit, before the roll's margin
    soak: int  # added to the ICE's soak roll against your intrusion


# id, name, integrity, attack, defense, damage, soak. Watchdogs are a nuisance; Black
# ICE is what a light build fears. Tuned against a runner's ~7-20 integrity and the
# deck damage above — first-slice numbers, NOT yet sim-checked (see CLAUDE.md).
_ICE_ROWS = (
    ("watchdog", "Watchdog", 4, 1, 10, 2, 1),
    ("sentry", "Sentry ICE", 6, 2, 11, 2, 2),
    ("tracer", "Tracer", 5, 2, 12, 3, 1),
    ("black_ice", "Black ICE", 8, 3, 13, 3, 3),
)

ICE = [Ice(*row) for row in _ICE_ROWS]
ICE_BY_ID = {ice.id: ice for ice in ICE}

# Day tier (checks.day_tier) -> which ICE turns up, and how many — the same shape as
# combat.ENEMY_TIERS, and the count is the real difficulty lever here too.
ICE_TIERS: dict[int, tuple[list[str], tuple[int, int]]] = {
    0: (["watchdog", "sentry"], (1, 2)),
    1: (["sentry", "tracer"], (1, 2)),
    2: (["tracer", "black_ice"], (2, 3)),
}

if any(ice_id not in ICE_BY_ID for ids, _ in ICE_TIERS.values() for ice_id in ids):
    raise ValueError("ICE_TIERS references an ICE id that is not in _ICE_ROWS")


def roll_ice(tier: int, rng: random.Random) -> tuple[Ice, ...]:
    """The ICE a matrix fight at this tier fields."""
    pool, (low, high) = ICE_TIERS[tier]
    return tuple(ICE_BY_ID[rng.choice(pool)] for _ in range(rng.randint(low, high)))


def _installed_programs(character: Character) -> list[Program]:
    """Programs live on the active deck (shops.active_deck_entry) — the same one
    equipped_deck_rating's number comes from, since a matrix fight only ever rides on
    one deck. No deck equipped means no programs, passive or otherwise."""
    entry = active_deck_entry(character.inventory)
    return installed_programs_for(entry[0]) if entry else []


def _passive_bonus(character: Character, attr: str) -> int:
    return sum(getattr(program, attr) for program in _installed_programs(character) if program.uses_per_fight == 0)


def player_integrity(character: Character) -> int:
    return BASE_INTEGRITY + INTEGRITY_PER_INT * character.stat("intelligence") + _passive_bonus(
        character, "integrity_bonus"
    )


def firewall_defense(character: Character) -> int:
    return FIREWALL_BASE + skill_value(character, "infer") + _passive_bonus(character, "firewall_bonus")


def firewall_soak(character: Character) -> int:
    """Dice rolled to shrug off a landed ICE hit — the matrix counterpart to combat's
    body+armor soak, here just the runner's own Intelligence (no armor in cyberspace),
    plus any installed program's soak_bonus."""
    return character.stat("intelligence") + _passive_bonus(character, "soak_bonus")


def player_attack_damage(character: Character) -> int:
    """Base integrity a landed intrusion takes off an ICE, before the roll's margin.
    Comes from the deck (equipped_deck_rating), not the Hack skill — jack in bare and
    you still get BARE_JACK_DAMAGE, the matrix's bare hands. A program's damage_bonus
    still applies bare-handed — it's the software doing the work, not the rig."""
    rating = equipped_deck_rating(character.inventory)
    base = BARE_JACK_DAMAGE if rating == 0 else DECK_BASE_DAMAGE + rating
    return base + _passive_bonus(character, "damage_bonus")


# Below this Hack value, matrix_readiness flags the runner — a fresh runner rolls Hack 2,
# so this warns anyone who hasn't put real Intelligence and rank behind it.
MIN_READY_HACK = 5


def matrix_readiness(character: Character) -> list[str]:
    """What the runner is missing to fight in the matrix, for the warning a Data Heist
    offer shows (empty = ready). Advisory, never a lockout: an under-equipped runner can
    still accept and attempt one, they'll just bleed integrity and blow the contract."""
    missing = []
    if equipped_deck_rating(character.inventory) == 0:
        missing.append("a cyberdeck")
    if skill_value(character, "hack") < MIN_READY_HACK:
        missing.append("more Hack skill")
    return missing


class MatrixOutcome(StrEnum):
    ONGOING = "ongoing"
    SEIZED = "seized"  # every ICE down: you have the data
    EJECTED = "ejected"  # integrity gone or you jacked out: the contract is blown


class MatrixActionKind(StrEnum):
    ATTACK = "attack"
    HARDEN = "harden"
    ANALYZE = "analyze"
    JACK_OUT = "jack_out"
    PROGRAM = "program"


@dataclass(frozen=True)
class MatrixAction:
    kind: MatrixActionKind
    label: str
    skill: str | None = None  # None only for JACK_OUT/PROGRAM, the actions that aren't a check
    program: Program | None = None  # set only for PROGRAM — which one, the way Action.weapon does


def _program_label(program: Program, uses_left: int) -> str:
    effect = f"{program.action_damage} dmg" if program.action_damage else "skip ICE"
    return f"Run {program.name} ({effect}, {uses_left} use{'s' if uses_left != 1 else ''} left)"


def available_matrix_actions(
    character: Character, program_uses: dict[str, int] | None = None
) -> list[MatrixAction]:
    """Everything the runner can do with a round in the matrix. The four base actions are
    fixed, unlike combat's weapon-derived list — your intrusion is your deck-plus-Hack,
    not a rack of weapons to pick between. Always includes JACK_OUT — a matrix fight is
    never a cage, same law as combat's flee (combat.FLEE_DIFFICULTY).

    `program_uses` mirrors combat.available_actions' `cooldowns` param exactly: an
    installed action-program (Program.uses_per_fight > 0) only appears while it still has
    a charge left this fight. None (the default) means nothing's been spent yet, so every
    installed action-program is offered — this is what keeps every existing call site
    (tests, and anywhere outside a live fight) working unchanged."""
    dmg = player_attack_damage(character)
    actions = [
        MatrixAction(MatrixActionKind.ATTACK, f"Breach the ICE (Hack, {dmg} dmg)", "hack"),
        MatrixAction(MatrixActionKind.HARDEN, "Harden your firewall (Tinkering)", "tinkering"),
        MatrixAction(MatrixActionKind.ANALYZE, "Analyze the ICE (Infer)", "infer"),
        MatrixAction(MatrixActionKind.JACK_OUT, "Jack out (blow the run)", None),
    ]
    for program in _installed_programs(character):
        if program.uses_per_fight == 0:
            continue
        uses_left = program.uses_per_fight if program_uses is None else program_uses.get(program.id, 0)
        if uses_left > 0:
            actions.append(MatrixAction(MatrixActionKind.PROGRAM, _program_label(program, uses_left), None, program))
    return actions


@dataclass
class IceFighter:
    """A live ICE program: the Ice is the template, this is the one being torn apart."""

    ice: Ice
    integrity: int

    @property
    def is_standing(self) -> bool:
        return self.integrity > 0


@dataclass
class MatrixState:
    """A matrix fight in progress. The screen renders this; take_matrix_turn advances it.
    `integrity`/`max_integrity` are the runner's matrix HP for this fight only — nothing
    like combat's character.health, which the fight would mutate directly."""

    character: Character
    ices: list[IceFighter]
    integrity: int
    max_integrity: int
    outcome: MatrixOutcome = MatrixOutcome.ONGOING
    log: list[str] = field(default_factory=list)
    next_attack_bonus: int = 0
    soak: int = 0
    ice_skip_rounds: int = 0
    # program id -> charges left this fight, seeded in start_matrix from each installed
    # action-program's uses_per_fight. Per-fight like integrity, not persisted.
    program_uses: dict[str, int] = field(default_factory=dict)

    @property
    def standing(self) -> list[IceFighter]:
        return [fighter for fighter in self.ices if fighter.is_standing]

    @property
    def is_over(self) -> bool:
        return self.outcome is not MatrixOutcome.ONGOING


def start_matrix(
    character: Character,
    ices: tuple[Ice, ...],
    drop: Drop = Drop.NONE,
    rng: random.Random | None = None,
) -> MatrixState:
    """Open a matrix fight. Integrity is rolled fresh from Intelligence; an ICE drop is
    paid immediately, before you act."""
    integrity = player_integrity(character)
    state = MatrixState(
        character=character,
        ices=[IceFighter(ice=ice, integrity=ice.integrity) for ice in ices],
        integrity=integrity,
        max_integrity=integrity,
        program_uses={
            program.id: program.uses_per_fight
            for program in _installed_programs(character)
            if program.uses_per_fight > 0
        },
    )
    if drop is Drop.PLAYER:
        state.ice_skip_rounds = FREE_ROUND
        state.log.append("You breach clean, ahead of their countermeasures.")
    elif drop is Drop.ENEMY:
        # You tripped it: the first ICE gets a free bite at your integrity before you can
        # act. One program, not the whole datastore — the same reason combat's ENEMY drop
        # is one opener, not a squad's worth (that stacks into a killing blow you never
        # chose). No harden here: there's been no round to harden in yet.
        rng = resolve_rng(rng)
        first = state.ices[0]
        state.log.append("The ICE was waiting for you.")
        _ice_bite(state, first, rng, harden=0)
        _settle(state)
    return state


def _damage_ice(state: MatrixState, fighter: IceFighter, damage: int) -> None:
    fighter.integrity = max(0, fighter.integrity - damage)
    if not fighter.is_standing:
        state.log.append(f"{fighter.ice.name} collapses.")


def _ice_bite(state: MatrixState, fighter: IceFighter, rng: random.Random, harden: int) -> None:
    """One ICE program's attack on the runner's integrity. Shares combat.resolve_hit, so
    an ICE hit and a meat hit are the same two-roll formula with the roles swapped."""
    roll, damage = resolve_hit(
        rng,
        fighter.ice.attack,
        0,
        firewall_defense(state.character),
        fighter.ice.damage,
        firewall_soak(state.character) + harden,
    )
    if not roll.result.passed:
        state.log.append(f"{fighter.ice.name} lunges, but your firewall holds.")
        return
    state.integrity = max(0, state.integrity - damage)
    if damage:
        state.log.append(f"{fighter.ice.name} bites you for {damage} integrity.")
    else:
        state.log.append(f"{fighter.ice.name} connects, but you shrug it off.")


def _attack(state: MatrixState, rng: random.Random) -> None:
    target = state.standing[0]
    bonus = state.next_attack_bonus
    state.next_attack_bonus = 0
    roll, damage = resolve_hit(
        rng,
        skill_value(state.character, "hack"),
        bonus,
        target.ice.defense,
        player_attack_damage(state.character),
        target.ice.soak,
    )
    if not roll.result.passed:
        state.log.append(f"Your intrusion glances off {target.ice.name}.")
        return
    if damage:
        prefix = "Clean break — " if roll.result is CheckResult.CRITICAL_SUCCESS else ""
        state.log.append(f"{prefix}You tear into {target.ice.name} for {damage}.")
    else:
        state.log.append(f"You reach {target.ice.name}, but its hardening holds.")
    _damage_ice(state, target, damage)


def _harden(state: MatrixState, rng: random.Random) -> None:
    roll = resolve_check(
        stat_value=skill_value(state.character, "tinkering"),
        difficulty=HARDEN_DIFFICULTY,
        rng=rng,
    )
    hit = roll.result.passed
    state.soak = HARDEN_SOAK if hit else HARDEN_SOAK_ON_FAILURE
    state.log.append(
        f"You shore up your firewall. +{state.soak} soak against every ICE hit this round."
        if hit
        else f"Your patch is sloppy. Only +{state.soak} soak this round."
    )


def _analyze(state: MatrixState, rng: random.Random) -> None:
    roll = resolve_check(
        stat_value=skill_value(state.character, "infer"),
        difficulty=ANALYZE_DIFFICULTY,
        rng=rng,
    )
    if roll.result.passed:
        state.next_attack_bonus += ANALYZE_BONUS
        state.log.append(f"You read its shape. +{ANALYZE_BONUS} to your next intrusion.")
    else:
        state.log.append("The architecture won't resolve. The round is wasted.")


def _use_program(state: MatrixState, program: Program, rng: random.Random) -> None:
    """Spend one charge of an installed action program. action_damage lands with no
    roll — the whole point of a program action is that it's guaranteed, unlike the
    ordinary ATTACK. action_skip_ice reuses ice_skip_rounds, the same free-round
    mechanism Drop.PLAYER's clean breach already grants."""
    state.program_uses[program.id] = state.program_uses.get(program.id, 0) - 1
    if program.action_damage:
        target = state.standing[0]
        state.log.append(
            f"{program.name} tears into {target.ice.name} for {program.action_damage}, no roll needed."
        )
        _damage_ice(state, target, program.action_damage)
    elif program.action_skip_ice:
        state.ice_skip_rounds += 1
        state.log.append(f"{program.name} scrambles your signature. The ICE loses track of you this round.")


def _ice_phase(state: MatrixState, rng: random.Random) -> None:
    if state.ice_skip_rounds > 0:
        state.ice_skip_rounds -= 1
        state.log.append("The ICE is still reorienting. You get this one free.")
        return
    for fighter in state.standing:
        _ice_bite(state, fighter, rng, harden=state.soak)


def _settle(state: MatrixState) -> None:
    """Read the board after a turn. Ejection (integrity gone) beats seizing: if the last
    intrusion drops the last ICE but a bite already put you at 0, you're still out."""
    if state.integrity <= 0:
        state.outcome = MatrixOutcome.EJECTED
        if "jack" not in (state.log[-1] if state.log else ""):
            state.log.append("Your integrity fails. You're forced out.")
    elif not state.standing:
        state.outcome = MatrixOutcome.SEIZED


def take_matrix_turn(state: MatrixState, action: MatrixAction, rng: random.Random | None = None) -> None:
    rng = resolve_rng(rng)
    if state.is_over:
        return

    if action.kind is MatrixActionKind.JACK_OUT:
        # Always works — the escape valve. You bail before the ICE finishes you, which
        # keeps your integrity but blows the contract just the same.
        state.outcome = MatrixOutcome.EJECTED
        state.log.append("You yank the jack and drop the connection. The run is blown.")
        return
    if action.kind is MatrixActionKind.ATTACK:
        _attack(state, rng)
    elif action.kind is MatrixActionKind.HARDEN:
        _harden(state, rng)
    elif action.kind is MatrixActionKind.ANALYZE:
        _analyze(state, rng)
    elif action.kind is MatrixActionKind.PROGRAM:
        _use_program(state, action.program, rng)

    _settle(state)
    if state.is_over:
        return

    _ice_phase(state, rng)
    _settle(state)
    state.soak = 0
