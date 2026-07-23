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
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.character import Character
from shadowguy.checks import CheckResult, CheckRoll, pool_for_difficulty, resolve_check, resolve_rng
from shadowguy.combat import Drop, resolve_hit
from shadowguy.cybernetics import installed_matrix_action_bonus
from shadowguy.shops import (
    STOLEN_DATASHARD_ID,
    InventoryItem,
    Program,
    active_deck_entry,
    equipped_deck_rating,
    installed_programs_for,
)
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

# Sleaze (Program.action_sleaze): try to talk a node's ICE into treating the runner as
# a valid user instead of fighting it. Deliberately *not* the normal opposed dice pool
# (checks.resolve_check) — a flat three-way split (success / fail / critical fail) that
# starts cut evenly into thirds and shifts with the margin between the runner's Hack and
# the target ICE's own difficulty (its defense, read through the same pool_for_difficulty
# conversion an ordinary intrusion's opposition already gets). Only the success/critical-
# fail tails move; the fail third is fixed, so a botched Sleaze is never worse odds than
# a coin a hacker could've called going in. First-slice numbers, not balance-simulated —
# see CLAUDE.md's convention for flagging that.
SLEAZE_SKILL = "hack"
SLEAZE_MARGIN_FLOOR = 2  # skill_value's own floor — an unspecialized runner against the
# weakest ICE lands exactly on the neutral 1/3-1/3-1/3 split
SLEAZE_MARGIN_STEP = 0.04  # per net point of margin above the floor, shift 4% out of the
# critical-fail tail and into the success tail
SLEAZE_MAX_SHIFT = 0.28  # caps the swing so no outcome ever fully vanishes

# Extract (Program.action_extract) has unlimited uses per fight (Program.uses_per_fight
# == -1, see EXTRACT_UNLIMITED_USES) rather than a charge cap — MatrixState.security is
# the cost instead: every missed Extract roll raises it, and it never comes back down
# this fight. Uncapped on purpose (see MatrixState.security) — spamming Extract stays
# free in charges but gets riskier the more it misses. First-slice numbers, not
# balance-simulated — see CLAUDE.md's convention for flagging that.
EXTRACT_UNLIMITED_USES = -1
SECURITY_PER_FAILED_EXTRACT = 1

# Below this, a freshly engaged node's guardian plays it neutral — same as any ordinary
# node hop, no opening bite. At or above it, security has tipped the whole network onto
# alert: every *new* node you engage (MatrixRunState._enter_node) opens hostile
# (Drop.ENEMY, the same opening bite an ambush-gone-wrong hands you), not just the run's
# very first guardian. Security never comes back down mid-run, so once you cross this
# there's no un-tripping it for the rest of the crawl. First-slice number, not
# balance-simulated — see CLAUDE.md's convention for flagging that.
SECURITY_HOSTILE_THRESHOLD = 3


@dataclass(frozen=True)
class Ice:
    """One security program. The matrix analogue of combat.Enemy: `integrity` is its
    hit points, `defense` the difficulty your Hack roll must beat, `damage` the integrity
    it takes off you on a hit, `soak` its own mitigation roll (its hardening).

    security_per_round is a second, mutually exclusive way an ICE can "hit" you: 0 (the
    default) means it bites integrity like any ordinary guardian; nonzero means every
    round it's still standing it instead adds that much straight to MatrixState.security,
    no roll, no integrity cost — a different kind of threat than combat damage, one that
    escalates every ICE's danger for the rest of the fight instead of directly hurting
    you (see _ice_phase). It can still be fought down like any other ICE; the drip just
    replaces its attack roll while it's alive."""

    id: str
    name: str
    integrity: int
    attack: int  # dice the ICE rolls against your firewall
    defense: int  # what your intrusion roll must beat
    damage: int  # integrity off you on a hit, before the roll's margin
    soak: int  # added to the ICE's soak roll against your intrusion
    security_per_round: float = 0.0  # if set, drains security instead of integrity — see above


# id, name, integrity, attack, defense, damage, soak, security_per_round. Watchdogs are
# a nuisance; Black ICE is what a light build fears; Sentinel doesn't fight you at all,
# it just phones the rest in — leave it alive too long and everything else gets harder
# to dodge. Tuned against a runner's ~7-20 integrity and the deck damage above —
# first-slice numbers, NOT yet sim-checked (see CLAUDE.md).
_ICE_ROWS = (
    ("watchdog", "Watchdog", 4, 1, 10, 2, 1),
    ("sentry", "Sentry ICE", 6, 2, 11, 2, 2),
    ("tracer", "Tracer", 5, 2, 12, 3, 1),
    ("black_ice", "Black ICE", 8, 3, 13, 3, 3),
    ("sentinel", "Sentinel ICE", 5, 0, 11, 0, 1, 0.3),
)

ICE = [Ice(*row) for row in _ICE_ROWS]
ICE_BY_ID = {ice.id: ice for ice in ICE}

# Day tier (checks.day_tier) -> which ICE turns up, and how many — the same shape as
# combat.ENEMY_TIERS, and the count is the real difficulty lever here too.
ICE_TIERS: dict[int, tuple[list[str], tuple[int, int]]] = {
    0: (["watchdog", "sentry", "sentinel"], (1, 2)),
    1: (["sentry", "tracer", "sentinel"], (1, 2)),
    2: (["tracer", "black_ice"], (2, 3)),
}

if any(ice_id not in ICE_BY_ID for ids, _ in ICE_TIERS.values() for ice_id in ids):
    raise ValueError("ICE_TIERS references an ICE id that is not in _ICE_ROWS")


def roll_ice(tier: int, rng: random.Random) -> tuple[Ice, ...]:
    """The ICE a matrix fight at this tier fields."""
    pool, (low, high) = ICE_TIERS[tier]
    return tuple(ICE_BY_ID[rng.choice(pool)] for _ in range(rng.randint(low, high)))


def _roll_one_ice(tier: int, rng: random.Random) -> Ice:
    """One ICE from a tier's pool — roll_ice's single-guardian counterpart, for a
    matrix network's per-node assignment rather than one flat fight's whole roster."""
    pool, _count_range = ICE_TIERS[tier]
    return ICE_BY_ID[rng.choice(pool)]


class MatrixNodeRole(StrEnum):
    ENTRY = "entry"  # where you jack in; never guarded
    SLAVE = "slave"  # waypoint, no fight
    IC = "ic"  # guarded; must clear it to pass
    DATA = "data"  # the objective
    CPU = "cpu"  # optional, harder, reachable once DATA is cleared
    CACHE = "cache"  # optional side loot hanging off a waypoint; gates nothing, pays an item


@dataclass(frozen=True)
class MatrixNode:
    """One stop in a matrix run's node network — the ICE analogue of
    corpmap.Territory, much smaller and generated fresh per fight rather than
    persistent. `ice` is the guardian that must be cleared to pass; None for
    ENTRY/SLAVE, which are never guarded."""

    id: str
    role: MatrixNodeRole
    connections: tuple[str, ...]
    ice: Ice | None = None


@dataclass(frozen=True)
class MatrixNetwork:
    nodes: dict[str, MatrixNode]
    entry_id: str
    data_id: str


# tier -> ((node count low, high), IC density among non-ENTRY/DATA/CPU/CACHE nodes,
# CPU attach chance, CACHE attach chance). First-slice numbers, not
# balance-simulated — see CLAUDE.md's convention for flagging that. CACHE is
# deliberately zero outside tier 1: a small side-loot chance for the early-mid-game
# band (days 4-6 — see jobs._tier_for_day/checks.day_tier), not a mechanic every
# run leans on.
MATRIX_NETWORK_TIERS: dict[int, tuple[tuple[int, int], float, float, float]] = {
    0: ((5, 6), 0.35, 0.4, 0.0),
    1: ((6, 8), 0.45, 0.5, 0.15),
    2: ((7, 9), 0.55, 0.6, 0.0),
}

if MATRIX_NETWORK_TIERS.keys() != ICE_TIERS.keys():
    raise ValueError("MATRIX_NETWORK_TIERS must cover the same tiers as ICE_TIERS")

# Chance of an extra edge between any two nodes beyond the guaranteed spine —
# corpmap.EXTRA_EDGE_CHANCE's role, here: branches and loops instead of one corridor.
EXTRA_NODE_EDGE_CHANCE = 0.2


def generate_matrix_network(tier: int, rng: random.Random) -> MatrixNetwork:
    """A small connected node graph for one matrix run: a guaranteed ENTRY-to-DATA
    spine (so, unlike tactical.py's BSP rooms, reachability never needs a retry
    loop), a few extra edges for branching, one CPU node hanging off DATA by a flat
    chance (an optional, tougher detour past the objective), and — tier 1 only,
    today, by a much smaller flat chance — one CACHE node hanging off an ordinary
    waypoint instead: side loot (see _settle_run) that pays off in an item whether
    or not the run itself is ever won, rather than a detour past the objective."""
    (low, high), ic_density, cpu_chance, cache_chance = MATRIX_NETWORK_TIERS[tier]
    node_count = rng.randint(low, high)
    ids = [f"node_{i}" for i in range(node_count)]
    connections: dict[str, set[str]] = {node_id: set() for node_id in ids}

    for a, b in zip(ids, ids[1:], strict=False):
        connections[a].add(b)
        connections[b].add(a)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if b not in connections[a] and rng.random() < EXTRA_NODE_EDGE_CHANCE:
                connections[a].add(b)
                connections[b].add(a)

    entry_id, data_id = ids[0], ids[-1]
    roles = {entry_id: MatrixNodeRole.ENTRY, data_id: MatrixNodeRole.DATA}
    for node_id in ids[1:-1]:
        roles[node_id] = MatrixNodeRole.IC if rng.random() < ic_density else MatrixNodeRole.SLAVE

    if rng.random() < cpu_chance:
        cpu_id = f"node_{node_count}"
        ids.append(cpu_id)
        connections[cpu_id] = {data_id}
        connections[data_id].add(cpu_id)
        roles[cpu_id] = MatrixNodeRole.CPU

    if rng.random() < cache_chance:
        # Any ordinary waypoint works — unlike CPU, a cache isn't meant to gate
        # behind the objective, just be a side risk on the way through.
        attach_to = rng.choice(
            [node_id for node_id, role in roles.items() if role in (MatrixNodeRole.SLAVE, MatrixNodeRole.IC)]
        )
        cache_id = f"node_{len(ids)}"
        ids.append(cache_id)
        connections[cache_id] = {attach_to}
        connections[attach_to].add(cache_id)
        roles[cache_id] = MatrixNodeRole.CACHE

    nodes = {}
    for node_id in ids:
        role = roles[node_id]
        if role in (MatrixNodeRole.IC, MatrixNodeRole.DATA, MatrixNodeRole.CACHE):
            ice = _roll_one_ice(tier, rng)
        elif role is MatrixNodeRole.CPU:
            ice = _roll_one_ice(min(tier + 1, max(ICE_TIERS)), rng)
        else:
            ice = None
        nodes[node_id] = MatrixNode(
            id=node_id, role=role, connections=tuple(sorted(connections[node_id])), ice=ice
        )

    return MatrixNetwork(nodes=nodes, entry_id=entry_id, data_id=data_id)


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
    """FIREWALL_BASE + Infer, minus the equipped deck's own Intelligence bonus: a
    better deck should make your hacking sharper (player_attack_damage), not your
    firewall too -- left in, the same deck rating was compounding into player_
    integrity, firewall_defense *and* the Hack roll all at once, which is what made
    a decked-out hacker near-unhittable (see DESIGN.md's Data Heist section)."""
    return (
        FIREWALL_BASE
        + skill_value(character, "infer")
        - equipped_deck_rating(character.inventory)
        + _passive_bonus(character, "firewall_bonus")
    )


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


def _program_label(program: Program, uses_left: int | None) -> str:
    if program.action_damage:
        effect = f"{program.action_damage} dmg"
    elif program.action_sleaze:
        effect = "sleaze the ICE"
    elif program.action_extract:
        effect = "extract data (no soak)"
    else:
        effect = "skip ICE"
    # uses_left is None only for an unlimited-use program (EXTRACT_UNLIMITED_USES) —
    # there's no charge count to show.
    uses = "unlimited uses" if uses_left is None else f"{uses_left} use{'s' if uses_left != 1 else ''} left"
    return f"Run {program.name} ({effect}, {uses})"


def available_matrix_actions(
    character: Character, program_uses: dict[str, int] | None = None
) -> list[MatrixAction]:
    """Everything the runner can do with a round in the matrix. The four base actions are
    fixed, unlike combat's weapon-derived list — your intrusion is your deck-plus-Hack,
    not a rack of weapons to pick between. Always includes JACK_OUT — a matrix fight is
    never a cage, same law as combat's flee (combat.FLEE_DIFFICULTY).

    `program_uses` mirrors combat.available_actions' `cooldowns` param exactly: an
    installed action-program with a positive uses_per_fight only appears while it still
    has a charge left this fight. None (the default) means nothing's been spent yet, so
    every installed action-program is offered — this is what keeps every existing call
    site (tests, and anywhere outside a live fight) working unchanged. A program whose
    uses_per_fight is negative (EXTRACT_UNLIMITED_USES) is never charge-gated at all —
    it always appears, since MatrixState.security is what it costs instead. A program
    with action_analyze is never offered here at all — it's navigation-mode only (see
    analyze_node/usable_analyze_program), read outside a fight rather than spent during one."""
    dmg = player_attack_damage(character)
    actions = [
        MatrixAction(MatrixActionKind.ATTACK, f"Breach the ICE (Hack, {dmg} dmg)", "hack"),
        MatrixAction(MatrixActionKind.HARDEN, "Harden your firewall (Tinkering)", "tinkering"),
        MatrixAction(MatrixActionKind.ANALYZE, "Analyze the ICE (Infer)", "infer"),
        MatrixAction(MatrixActionKind.JACK_OUT, "Jack out (blow the run)", None),
    ]
    for program in _installed_programs(character):
        if program.uses_per_fight == 0 or program.action_analyze:
            continue
        if program.uses_per_fight < 0:
            actions.append(MatrixAction(MatrixActionKind.PROGRAM, _program_label(program, None), None, program))
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
    # Whether clearing every ice here wins the whole run (True by default, so a
    # direct start_matrix() call — every existing caller and test — behaves exactly
    # as before). MatrixRunState sets this False on every node except the DATA one,
    # via engage_node, so clearing a mid-network guardian doesn't end the run early.
    is_final_node: bool = True
    # Whether the current node is something Program.action_extract can pull info
    # from — DATA or CACHE (False by default, matching a direct start_matrix() call
    # having no node context to be extractable from). Set alongside is_final_node
    # in MatrixRunState._enter_node/engage_node.
    is_extractable: bool = False
    # Escalating alert level: a run-wide ratchet, not reset between nodes (unlike
    # ices, which engage_node does swap fresh) — tripping alarms on one node stays
    # tripped for the rest of the run. A float, not an int: Ice.security_per_round
    # drips it up in fractional steps (see _ice_phase) so it builds incrementally
    # rather than only ever jumping by whole points; Program.action_extract's missed
    # rolls (see _extract) add whole points on top of that. It makes every ICE hit
    # harder to dodge (see _ice_bite, which floors it to a whole die bonus) rather
    # than gating anything outright, so leaning on either source gets riskier instead
    # of just running out.
    security: float = 0.0

    @property
    def standing(self) -> list[IceFighter]:
        return [fighter for fighter in self.ices if fighter.is_standing]

    @property
    def is_over(self) -> bool:
        return self.outcome is not MatrixOutcome.ONGOING


def _open_hostile(state: MatrixState, rng: random.Random | None, message: str) -> None:
    """The opening-bite sequence a Drop.ENEMY entry pays before the player can act:
    one free _ice_turn against the first ICE, then settle. Shared by start_matrix's
    ambush/critical-failure drop and engage_node's security-triggered hostile open —
    same mechanic, different reason you're already made."""
    rng = resolve_rng(rng)
    first = state.ices[0]
    state.log.append(message)
    _ice_turn(state, first, rng, harden=0)
    _settle(state)


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
        _open_hostile(state, rng, "The ICE was waiting for you.")
    return state


def engage_node(
    state: MatrixState,
    ices: tuple[Ice, ...],
    is_final_node: bool,
    is_extractable: bool = False,
    drop: Drop = Drop.NONE,
    rng: random.Random | None = None,
) -> None:
    """Swap in a fresh node's guardian(s) on an already-open MatrixState. Integrity,
    program_uses and the log all carry over — unlike start_matrix, nothing is reset,
    because a matrix run's integrity is a run-wide resource, not refilled between
    nodes (see MatrixRunState). `outcome` does reset to ONGOING: a prior node
    resolving SEIZED (is_final_node was True there) must not block this one's fight
    from ever starting — only EJECTED should ever end a run, and that's checked
    before engage_node is ever called again (see MatrixRunState._settle_run/move_to).
    Ordinarily no drop — only the run's very first guardian plays out an ambush/
    critical-failure opening bite (see start_matrix_run). But once state.security has
    crossed SECURITY_HOSTILE_THRESHOLD, _enter_node passes Drop.ENEMY for every
    subsequent node too: high enough alert means the next guardian is already braced
    for you, same opening bite an ambush gone wrong would've handed you."""
    state.ices = [IceFighter(ice=ice, integrity=ice.integrity) for ice in ices]
    state.is_final_node = is_final_node
    state.is_extractable = is_extractable
    state.outcome = MatrixOutcome.ONGOING
    state.log.append("ICE lights up ahead.")
    if drop is Drop.ENEMY:
        _open_hostile(state, rng, "Security's already briefed on you. It doesn't wait.")


def _damage_ice(state: MatrixState, fighter: IceFighter, damage: int) -> None:
    fighter.integrity = max(0, fighter.integrity - damage)
    if not fighter.is_standing:
        state.log.append(f"{fighter.ice.name} collapses.")


def _ice_bite(state: MatrixState, fighter: IceFighter, rng: random.Random, harden: int) -> None:
    """One ICE program's attack on the runner's integrity. Shares combat.resolve_hit, so
    an ICE hit and a meat hit are the same two-roll formula with the roles swapped.
    state.security rides along as the ICE's attack advantage (floored to a whole die
    bonus — a dice pool can't take a fractional die): the alert level doesn't gate
    anything, it just makes every ICE in the fight — not just the one that tripped it —
    hit harder to dodge."""
    roll, damage = resolve_hit(
        rng,
        fighter.ice.attack,
        int(state.security),
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


def _ice_turn(state: MatrixState, fighter: IceFighter, rng: random.Random, harden: int) -> None:
    """One ICE's turn against the player, whether it's the ordinary round phase, the
    opening drop-in bite, or Sleaze's critical-fail extra bite: a security-drip
    guardian (Ice.security_per_round) logs security instead of ever rolling an attack;
    anything else bites integrity via _ice_bite."""
    if fighter.ice.security_per_round:
        state.security += fighter.ice.security_per_round
        state.log.append(
            f"{fighter.ice.name} logs your presence. Security climbs (+{fighter.ice.security_per_round:g})."
        )
    else:
        _ice_bite(state, fighter, rng, harden=harden)


def _intrude(state: MatrixState, target: IceFighter, rng: random.Random, soak: int) -> tuple[CheckRoll, int]:
    """The runner's core intrusion roll: Hack to hit, the deck's rating for damage, any
    banked Analyze bonus consumed on this attempt, plus any installed cyberware's
    matrix_action_bonus (Datajack, today). Shared by the ordinary ATTACK action and
    Extract (Program.action_extract) — the two differ only in what soak pool opposes
    the hit (Extract ignores the target's soak entirely; see _extract)."""
    bonus = state.next_attack_bonus + installed_matrix_action_bonus(state.character.installed_cyberware)
    state.next_attack_bonus = 0
    return resolve_hit(
        rng,
        skill_value(state.character, "hack"),
        bonus,
        target.ice.defense,
        player_attack_damage(state.character),
        soak,
    )


def _attack(state: MatrixState, rng: random.Random) -> None:
    target = state.standing[0]
    roll, damage = _intrude(state, target, rng, target.ice.soak)
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
        advantage=installed_matrix_action_bonus(state.character.installed_cyberware),
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
        advantage=installed_matrix_action_bonus(state.character.installed_cyberware),
        rng=rng,
    )
    if roll.result.passed:
        state.next_attack_bonus += ANALYZE_BONUS
        state.log.append(f"You read its shape. +{ANALYZE_BONUS} to your next intrusion.")
    else:
        state.log.append("The architecture won't resolve. The round is wasted.")


def _sleaze_odds(character: Character, ice: Ice) -> tuple[float, float, float]:
    """Success / fail / critical-fail chances for a Sleaze bypass attempt — see the
    SLEAZE_* constants for why this isn't checks.resolve_check's opposed pool."""
    margin = skill_value(character, SLEAZE_SKILL) - pool_for_difficulty(ice.defense)
    shift = min(SLEAZE_MAX_SHIFT, max(0.0, (margin - SLEAZE_MARGIN_FLOOR) * SLEAZE_MARGIN_STEP))
    return 1 / 3 + shift, 1 / 3, 1 / 3 - shift


def _sleaze(state: MatrixState, program: Program, rng: random.Random) -> None:
    """Try to talk the targeted ICE into standing down instead of fighting it. Success
    drops it outright, same as clearing it in combat. A critical fail means it's not
    just unconvinced but alerted — it gets an extra bite on top of its normal one this
    round. A plain fail costs nothing beyond the wasted round every other missed action
    already costs."""
    target = state.standing[0]
    success, fail, _critical_fail = _sleaze_odds(state.character, target.ice)
    roll = rng.random()
    if roll < success:
        state.log.append(f"{program.name} convinces {target.ice.name} you belong here. It stands down.")
        _damage_ice(state, target, target.integrity)
    elif roll < success + fail:
        state.log.append(f"{program.name} can't sell the lie. {target.ice.name} isn't fooled.")
    else:
        state.log.append(f"{program.name} trips an alarm. {target.ice.name} snaps to alert.")
        _ice_turn(state, target, rng, harden=state.soak)


def _extract(state: MatrixState, program: Program, rng: random.Random) -> None:
    """Roll an attack against the current node's info rather than its guardian's
    fight — DATA and CACHE nodes only (MatrixState.is_extractable); against an
    ordinary IC waypoint there's nothing here to pull. A landed hit ignores the
    target's soak roll entirely — you're not out-fighting it, just grabbing the
    file once you're past its lock, which is the edge that makes this worth a
    program slot over just attacking. Unlimited uses (EXTRACT_UNLIMITED_USES), so
    the cost of leaning on it isn't running out — see SECURITY_PER_FAILED_EXTRACT."""
    target = state.standing[0]
    if not state.is_extractable:
        state.log.append(f"{program.name} finds nothing here worth extracting.")
        return
    roll, damage = _intrude(state, target, rng, 0)
    if not roll.result.passed:
        state.security += SECURITY_PER_FAILED_EXTRACT
        state.log.append(
            f"{program.name} can't get a clean read on {target.ice.name}. "
            f"Security tightens (+{SECURITY_PER_FAILED_EXTRACT})."
        )
        return
    state.log.append(f"{program.name} pulls the file straight through {target.ice.name}.")
    _damage_ice(state, target, damage)


def _use_program(state: MatrixState, program: Program, rng: random.Random) -> None:
    """Spend one charge of an installed action program. action_damage lands with no
    roll — the whole point of a guaranteed program action is that it's not a check,
    unlike the ordinary ATTACK. action_skip_ice reuses ice_skip_rounds, the same
    free-round mechanism Drop.PLAYER's clean breach already grants. action_sleaze and
    action_extract are rolled, unlike the other two — see _sleaze/_extract. A program
    with unlimited uses (uses_per_fight < 0) has no charge to spend."""
    if program.uses_per_fight > 0:
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
    elif program.action_sleaze:
        _sleaze(state, program, rng)
    elif program.action_extract:
        _extract(state, program, rng)


def _ice_phase(state: MatrixState, rng: random.Random) -> None:
    if state.ice_skip_rounds > 0:
        state.ice_skip_rounds -= 1
        state.log.append("The ICE is still reorienting. You get this one free.")
        return
    for fighter in state.standing:
        _ice_turn(state, fighter, rng, harden=state.soak)


def _settle(state: MatrixState) -> None:
    """Read the board after a turn. Ejection (integrity gone) beats seizing: if the last
    intrusion drops the last ICE but a bite already put you at 0, you're still out.
    Clearing every ice here only ends the whole run in SEIZED when this is the final
    node (MatrixState.is_final_node) — a mid-network guardian falling just clears
    that node; MatrixRunState reads state.standing itself to notice and move on."""
    if state.integrity <= 0:
        state.outcome = MatrixOutcome.EJECTED
        if "jack" not in (state.log[-1] if state.log else ""):
            state.log.append("Your integrity fails. You're forced out.")
    elif not state.standing and state.is_final_node:
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


@dataclass
class MatrixRunState:
    """A node-network crawl in progress: navigation wrapped around the persistent
    per-node engagement (MatrixState) that start_matrix_run/move_to swap fresh
    guardians into via engage_node. integrity/program charges/log carry across
    nodes — pushing deeper into the network is a real cost, not a free reset."""

    character: Character
    network: MatrixNetwork
    current_node_id: str
    cleared_node_ids: set[str] = field(default_factory=set)
    fight: MatrixState | None = None  # the live per-node engagement, if any
    outcome: MatrixOutcome = MatrixOutcome.ONGOING
    run_log: list[str] = field(default_factory=list)
    # A node's role is hidden until it's been analyzed (Program.action_analyze, from
    # outside — see analyze_node) or aggressed upon (physically entered — see
    # _enter_node, which adds every node it's called on, guarded or not). Lives here
    # rather than on MatrixState because the ENTRY node is never guarded, so a run can
    # sit with no fight open at all while the player still wants to analyze a neighbor.
    revealed_node_ids: set[str] = field(default_factory=set)
    # program id -> Analyze charges left this run, seeded in start_matrix_run — the
    # navigation-mode counterpart to MatrixState.program_uses, kept separate for the
    # same reason revealed_node_ids is: it has to work with no MatrixState open yet.
    analyze_uses: dict[str, int] = field(default_factory=dict)

    @property
    def is_over(self) -> bool:
        return self.outcome is not MatrixOutcome.ONGOING

    @property
    def in_fight(self) -> bool:
        """True while a node's guardian is still up and blocking movement."""
        return self.fight is not None and bool(self.fight.standing)

    @property
    def current_node(self) -> MatrixNode:
        return self.network.nodes[self.current_node_id]

    @property
    def can_extract(self) -> bool:
        return self.network.data_id in self.cleared_node_ids


def connected_nodes(run: MatrixRunState) -> list[MatrixNode]:
    """The nodes reachable from wherever the runner is standing right now."""
    return [run.network.nodes[node_id] for node_id in run.current_node.connections]


# Shorter than corpmap.CONNECTOR_WIDTH (6): node labels ("@[node_3 IC guard]") run
# shorter than territory labels, so the connector can too.
MATRIX_CONNECTOR_WIDTH = 4


def _matrix_node_label(node: MatrixNode, current_id: str, cleared_ids: set[str], revealed_ids: set[str]) -> str:
    marker = "@" if node.id == current_id else " "
    if node.id not in revealed_ids:
        # Role AND guarded/clear status are both part of a node's "value" — showing
        # one while hiding the other would leak it by elimination (only guarded
        # roles ever show "guard"), so an unrevealed node shows neither.
        return f"{marker}[{node.id} ???]"
    parts = [node.id, node.role.value.upper()]
    if node.id in cleared_ids:
        parts.append("clear")
    elif node.ice is not None:
        parts.append("guard")
    return f"{marker}[{' '.join(parts)}]"


def _nearest_free_row(preferred: int, used: set[int]) -> int:
    """The row closest to `preferred` that isn't already taken in this column —
    lets a single-parent node land exactly on its parent's row (the common case),
    and only nudges an actual fork's second child to the next-nearest lane rather
    than discarding the target row's absolute value the way a plain re-rank
    (0, 1, 2, ... per column) would."""
    if preferred not in used:
        return preferred
    offset = 1
    while True:
        if preferred + offset not in used:
            return preferred + offset
        if preferred - offset not in used:
            return preferred - offset
        offset += 1


def _matrix_network_layout(network: MatrixNetwork) -> dict[str, tuple[int, int]]:
    """Column = shortest-hop distance from entry (a Sugiyama-style layered layout,
    the graph-drawing counterpart to corpmap's territories, which carry real x/y
    instead of needing one derived). A branch (two nodes both reachable in the same
    number of hops) lands in the same column at different rows rather than being
    squashed onto one line — over a few hundred generated networks, ~90% have at
    least one such branch, so a single-row rendering was hiding most of the
    network's real shape.

    Row is a running "lane" a node keeps for the rest of the diagram, not a value
    re-ranked from scratch per column: a node's preferred row is the (rounded)
    average row of its already-placed neighbours in earlier columns, and it takes
    the nearest free row to that if the exact one is already spoken for in this
    column. Re-ranking to 0, 1, 2, ... per column (the first cut at this) forgot
    the parent's absolute row past the second column, so most nodes rendered with
    no connector to anything — this keeps a single-parent chain in the same lane
    all the way across, and only spreads an actual fork onto nearby lanes."""
    distances: dict[str, int] = {network.entry_id: 0}
    queue: deque[str] = deque([network.entry_id])
    while queue:
        node_id = queue.popleft()
        for neighbor in network.nodes[node_id].connections:
            if neighbor not in distances:
                distances[neighbor] = distances[node_id] + 1
                queue.append(neighbor)

    columns: dict[int, list[str]] = {}
    for node_id, col in distances.items():
        columns.setdefault(col, []).append(node_id)

    row_of: dict[str, int] = {network.entry_id: 0}
    for col in sorted(columns):
        if col == 0:
            continue

        def barycenter(node_id: str) -> float:
            placed = [row_of[nb] for nb in network.nodes[node_id].connections if nb in row_of]
            return sum(placed) / len(placed) if placed else 0.0

        used_rows: set[int] = set()
        for node_id in sorted(columns[col], key=barycenter):
            row = _nearest_free_row(round(barycenter(node_id)), used_rows)
            used_rows.add(row)
            row_of[node_id] = row

    # Rows were assigned as free-floating ints (a fork can push a lane negative),
    # so compact them to consecutive 0..n-1 for the grid — this only relabels
    # lanes, it can't change which nodes share one, so alignment survives it.
    lanes = sorted(set(row_of.values()))
    lane_index = {lane: i for i, lane in enumerate(lanes)}
    return {node_id: (distances[node_id], lane_index[row_of[node_id]]) for node_id in network.nodes}


def render_matrix_network(run: MatrixRunState) -> str:
    """ASCII node diagram for a matrix run — corpmap.render_ascii_map's shape ported
    to MatrixNetwork, so a run reads with the same at-a-glance "where am I in the
    structure" feel as the corp map. See _matrix_network_layout for how a node
    without corpmap's persistent x/y gets a grid position. A connection between
    nodes that don't end up grid-adjacent (same row, next column, or same column,
    next row) just doesn't draw a line — it's still a legal move, the diagram only
    shows the edges its layout happens to align, the same simplification corpmap's
    own renderer accepts for ties that aren't grid-adjacent."""
    network = run.network
    positions = _matrix_network_layout(network)
    by_pos = {pos: node_id for node_id, pos in positions.items()}
    max_col = max(c for c, _ in positions.values())
    max_row = max(r for _, r in positions.values())

    def label(node_id: str) -> str:
        return _matrix_node_label(
            network.nodes[node_id], run.current_node_id, run.cleared_node_ids, run.revealed_node_ids
        )

    col_width = {}
    for col in range(max_col + 1):
        labels = [label(nid) for (c, _), nid in by_pos.items() if c == col]
        col_width[col] = (max(len(text) for text in labels) if labels else 0) + 2

    col_offset = {}
    offset = 0
    for col in range(max_col + 1):
        col_offset[col] = offset
        offset += col_width[col] + MATRIX_CONNECTOR_WIDTH
    total_width = offset - MATRIX_CONNECTOR_WIDTH

    lines: list[str] = []
    for row in range(max_row + 1):
        cells = []
        for col in range(max_col + 1):
            node_id = by_pos.get((col, row))
            text = label(node_id) if node_id else ""
            right_id = by_pos.get((col + 1, row))
            linked = bool(node_id and right_id and right_id in network.nodes[node_id].connections)
            is_last_col = col == max_col
            padded = text.ljust(col_width[col], "-" if linked else " ")
            connector = "-" * MATRIX_CONNECTOR_WIDTH if linked else " " * MATRIX_CONNECTOR_WIDTH
            cells.append(padded + ("" if is_last_col else connector))
        lines.append("".join(cells).rstrip())

        if row == max_row:
            continue
        connector_line = [" "] * total_width
        for col in range(max_col + 1):
            node_id = by_pos.get((col, row))
            below_id = by_pos.get((col, row + 1))
            if node_id and below_id and below_id in network.nodes[node_id].connections:
                connector_line[col_offset[col] + 1] = "|"
        lines.append("".join(connector_line).rstrip())

    return "\n".join(lines)


def _settle_run(run: MatrixRunState) -> None:
    """Read the active node fight and fold its result into the run: an ejection
    always ends the whole run; a cleared node (its guardian down) is banked, and —
    only for the DATA node — offers up a win the player still has to choose to take
    (see extract()). Clearing a CACHE node pays out immediately, straight onto
    Character.inventory rather than through an Outcome — it's side loot, kept
    whether the run itself is later seized or blown, not part of the job's own
    payout path."""
    if run.fight is None:
        return
    if run.fight.outcome is MatrixOutcome.EJECTED:
        run.outcome = MatrixOutcome.EJECTED
        return
    if not run.fight.standing:
        run.cleared_node_ids.add(run.current_node_id)
        node = run.network.nodes[run.current_node_id]
        if node.role is MatrixNodeRole.CACHE:
            run.character.inventory.append(InventoryItem(STOLEN_DATASHARD_ID, equipped=False))
            run.run_log.append("You skim a stray cache of corp data — worth selling later.")


def _enter_node(run: MatrixRunState, node_id: str, drop: Drop, rng: random.Random | None) -> None:
    run.current_node_id = node_id
    # Physically arriving reveals a node's value unconditionally — "aggressed upon"
    # for a guarded one (you're about to fight it below), just "visited" for a plain
    # waypoint. Unrelated to analyze_node's remote reveal, which never moves you.
    run.revealed_node_ids.add(node_id)
    node = run.network.nodes[node_id]
    if node.ice is None or node_id in run.cleared_node_ids:
        run.run_log.append(f"You're clear at {node.id} ({node.role.value}).")
        return
    is_final = node.role is MatrixNodeRole.DATA
    is_extractable = node.role in (MatrixNodeRole.DATA, MatrixNodeRole.CACHE)
    if run.fight is None:
        run.fight = start_matrix(run.character, (node.ice,), drop, rng)
        run.fight.is_final_node = is_final
        run.fight.is_extractable = is_extractable
    else:
        # High enough alert and the network stops playing it neutral: every new
        # guardian from here on opens hostile, not just the run's very first one.
        node_drop = Drop.ENEMY if run.fight.security >= SECURITY_HOSTILE_THRESHOLD else Drop.NONE
        engage_node(run.fight, (node.ice,), is_final, is_extractable, node_drop, rng)
    run.run_log.append(f"ICE lights up at {node.id}.")
    _settle_run(run)


def start_matrix_run(
    character: Character, network: MatrixNetwork, drop: Drop = Drop.NONE, rng: random.Random | None = None
) -> MatrixRunState:
    """Jack in. Enters the network's entry node — never guarded, so this never
    triggers a fight; drop only ever matters for whichever node ends up engaged
    first."""
    run = MatrixRunState(
        character=character,
        network=network,
        current_node_id=network.entry_id,
        analyze_uses={
            program.id: program.uses_per_fight
            for program in _installed_programs(character)
            if program.action_analyze and program.uses_per_fight > 0
        },
    )
    _enter_node(run, network.entry_id, drop, rng)
    return run


def _reachable(run: MatrixRunState, node_id: str) -> bool:
    """Whether node_id is something the player could currently act on at all — move to
    it or analyze it. False if the run is over, a guardian is still blocking movement,
    or node_id isn't actually connected to wherever the runner is standing."""
    if run.is_over or run.in_fight:
        return False
    return node_id in run.current_node.connections


def move_to(run: MatrixRunState, node_id: str, rng: random.Random | None = None) -> bool:
    """Attempt to move onto a connected node. Refuses (no state change, returns
    False) if the run is already over, a guardian is still blocking movement, or
    node_id isn't actually reachable from here."""
    if not _reachable(run, node_id):
        return False
    _enter_node(run, node_id, Drop.NONE, rng)
    return True


def usable_analyze_program(run: MatrixRunState) -> Program | None:
    """The installed Analyze program (Program.action_analyze), if the runner has one
    with a charge left — None otherwise, which is what tells MatrixScreen whether to
    offer an "Analyze <node>" row at all. Unlimited-use analyze programs (uses_per_
    fight < 0) would always qualify, the same convention Extract set."""
    program = next((p for p in _installed_programs(run.character) if p.action_analyze), None)
    if program is None:
        return None
    if program.uses_per_fight > 0 and run.analyze_uses.get(program.id, 0) <= 0:
        return None
    return program


def analyze_node(run: MatrixRunState, node_id: str, rng: random.Random | None = None) -> bool:
    """Run the installed Analyze program against a connected-but-unrevealed node to
    read its value (role) remotely, without moving there or risking a fight — the
    navigation-mode counterpart to Sleaze/Extract, same Hack roll and the same
    ANALYZE_DIFFICULTY the in-fight ANALYZE action reads an ICE's shape with, just
    aimed at a node instead. Refuses (no roll, no charge spent, returns False) if the
    run is over, a guardian is blocking movement, node_id isn't reachable from here,
    it's already revealed, or no Analyze program/charge is available. A miss costs
    nothing beyond the attempt, same low-stakes shape as the in-fight version."""
    if not _reachable(run, node_id):
        return False
    if node_id in run.revealed_node_ids:
        return False
    program = usable_analyze_program(run)
    if program is None:
        return False
    if program.uses_per_fight > 0:
        run.analyze_uses[program.id] = run.analyze_uses.get(program.id, 0) - 1
    rng = resolve_rng(rng)
    node = run.network.nodes[node_id]
    roll = resolve_check(
        stat_value=skill_value(run.character, "hack"),
        difficulty=ANALYZE_DIFFICULTY,
        advantage=installed_matrix_action_bonus(run.character.installed_cyberware),
        rng=rng,
    )
    if roll.result.passed:
        run.revealed_node_ids.add(node_id)
        run.run_log.append(f"{program.name} reads {node.id}'s traffic. It's a {node.role.value} node.")
    else:
        run.run_log.append(f"{program.name} can't get a clean read on {node.id}.")
    return True


def take_run_turn(run: MatrixRunState, action: MatrixAction, rng: random.Random | None = None) -> None:
    """Delegate one round to the active node engagement (take_matrix_turn,
    unchanged), then fold the result into the run.

    Gated on `in_fight`, not just `fight.is_over`: clearing a *non-final* node's
    guardian leaves the fight ONGOING with nothing standing (see _settle's
    is_final_node branch), and the per-node actions all target state.standing[0].
    MatrixScreen already stops offering them at that point, but the invariant
    belongs here rather than only in the screen.
    """
    if run.is_over or not run.in_fight:
        return
    take_matrix_turn(run.fight, action, rng)
    _settle_run(run)


def jack_out(run: MatrixRunState) -> None:
    """Bail on the whole run, right now — always works, the same 'never a cage' law
    JACK_OUT already keeps inside a node fight (MatrixActionKind.JACK_OUT). This is
    the navigation-mode equivalent: a node fight might not even be open yet (still
    crossing SLAVE relays), so there's nothing to route a JACK_OUT MatrixAction
    through."""
    run.outcome = MatrixOutcome.EJECTED
    run.run_log.append("You yank the jack and drop the connection. The run is blown.")


def extract(run: MatrixRunState) -> bool:
    """End the run a winner, on the player's terms — the only way SEIZED actually
    fires. Refuses until the DATA node has been cleared: clearing it alone doesn't
    auto-win (see _settle's is_final_node gate), which is what leaves CPU
    (explicitly a detour *past* the objective) reachable at all. No partial credit
    otherwise — jacking out before extracting still blows the run, even with DATA
    already cleared."""
    if run.is_over or run.in_fight or not run.can_extract:
        return False
    run.outcome = MatrixOutcome.SEIZED
    run.run_log.append("You pull out clean, data in hand.")
    return True
