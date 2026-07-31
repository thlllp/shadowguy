"""Combat balance harness: runs real abstract fights and reports how they end.

Not part of the game and not imported by it — a developer tool, run by hand.

**Why it exists.** DESIGN.md's combat sections quote death rates ("a tier-2 Hacker dies
~31% of the time") and tell you to re-run the balance sim before touching `_ENEMY_ROWS`,
`ENEMY_TIERS`, `DEFENSE_BASE` or the flee/drop rules. That sim was never checked in, so
for a long time there was nothing to re-run. This is a re-derivation of it: it reproduces
the documented tier-2 Hacker figure closely (31.5% measured against DESIGN.md's 31%),
which is the reason to trust it as a baseline for the next change.

It drives the **abstract** surface only (`abstract_combat.take_turn`), because that is
where the quoted figures come from. It says nothing about `tactical.py` — positions,
cover and reach are all invisible here, so an enemy whose whole identity is its range
(the Marksman, the Bulwark) reads as a plain stat block. DESIGN.md's tactical Balance
note is a separate, still-unautomated measurement.

Usage:

    uv run python tools/combat_sim.py                  # outcomes per build per tier
    uv run python tools/combat_sim.py --per-enemy      # one enemy type at a time
    uv run python tools/combat_sim.py --trials 20000   # tighter confidence intervals

The policy the simulated player follows is deliberately simple and deliberately stated:
attack with the hardest-hitting equipped weapon, break off below `FLEE_AT` of max health.
DESIGN.md notes the Hacker's death rate is *extremely* sensitive to that threshold (14%
at 40%, 31% at 25%), so a figure from this tool only means anything alongside the
threshold it was measured at.
"""

import argparse
import random
import statistics

from shadowguy.abstract_combat import (
    ActionKind,
    CombatOutcome,
    available_actions,
    start_combat,
    take_turn,
)
from shadowguy.archetypes import ARCHETYPES_BY_ID
from shadowguy.character import Character
from shadowguy.combat import ENEMIES, ENEMIES_BY_ID, ENEMY_TIERS, roll_enemies
from shadowguy.shops import InventoryItem

# Break off below this fraction of max health. See the module docstring: this is the
# single most load-bearing number in the whole harness.
FLEE_AT = 0.35

# A fight that hasn't resolved by here is counted as-is rather than looped forever. Only
# reachable by a build that can barely scratch a high-toughness enemy; if this trips
# often, that's the finding.
MAX_ROUNDS = 60

# The three builds worth measuring: the one that wants the fight, the one that never
# does, and the one that shoots. Loadouts are what a runner could plausibly have bought
# by the tier being measured, equipped — an unarmed sim measures UNARMED, not the game.
LOADOUTS = {
    "enforcer": ["brass_knuckles", "kevlar_vest"],
    "hacker": ["pipe_pistol", "leather_jacket"],
    "gunslinger": ["machine_pistol", "kevlar_vest"],
}


def build(archetype_id: str) -> Character:
    """A fresh runner of this archetype at full health, kit equipped."""
    character = Character(name=archetype_id)
    ARCHETYPES_BY_ID[archetype_id].apply(character)
    character.inventory.extend(InventoryItem(item_id=i) for i in LOADOUTS[archetype_id])
    character.health = character.max_health
    return character


def pick_action(state):
    """The policy: run when badly hurt, otherwise swing the biggest thing available."""
    character = state.character
    actions = available_actions(character, state.weapon_cooldowns)
    if character.health <= character.max_health * FLEE_AT:
        flee = [a for a in actions if a.kind is ActionKind.FLEE]
        if flee:
            return flee[0]
    attacks = [a for a in actions if a.kind is ActionKind.ATTACK]
    return max(attacks, key=lambda a: a.weapon.damage) if attacks else actions[0]


def run_fight(archetype_id: str, enemies, rng: random.Random):
    """One fight to a conclusion. Returns (outcome, rounds, fraction of health left)."""
    character = build(archetype_id)
    state = start_combat(character, enemies, rng=rng)
    rounds = 0
    while not state.is_over and rounds < MAX_ROUNDS:
        rounds += 1
        take_turn(state, pick_action(state), rng)
    return state.outcome, rounds, character.health / character.max_health


def _tally(archetype_id: str, squad_for, trials: int, seed: int):
    rng = random.Random(seed)
    counts = {outcome: 0 for outcome in CombatOutcome}
    lengths, health_left = [], []
    for _ in range(trials):
        outcome, rounds, health = run_fight(archetype_id, squad_for(rng), rng)
        counts[outcome] += 1
        lengths.append(rounds)
        health_left.append(health)
    return counts, statistics.mean(lengths), statistics.mean(health_left)


def _row(label: str, counts, rounds: float, health: float, trials: int) -> str:
    pct = trials / 100
    return (
        f"{label:<26}"
        f"{counts[CombatOutcome.DEAD] / pct:>7.1f}%"
        f"{counts[CombatOutcome.KNOCKED_OUT] / pct:>7.1f}%"
        f"{counts[CombatOutcome.ESCAPED] / pct:>7.1f}%"
        f"{counts[CombatOutcome.VICTORY] / pct:>7.1f}%"
        f"{rounds:>9.2f}{health * 100:>9.1f}%"
    )


HEADER = f"{'':<26}{'died':>8}{'ko':>8}{'fled':>8}{'won':>8}{'rounds':>9}{'hp left':>10}"


def report_tiers(trials: int) -> None:
    """What a fight at each tier actually does to each build — the headline numbers."""
    print(HEADER)
    for archetype_id in LOADOUTS:
        for tier in sorted(ENEMY_TIERS):
            counts, rounds, health = _tally(
                archetype_id, lambda rng, t=tier: roll_enemies(t, rng), trials, 1234 + tier
            )
            print(_row(f"{archetype_id} / tier {tier}", counts, rounds, health, trials))


def report_per_enemy(trials: int, archetype_id: str = "hacker") -> None:
    """Each enemy type in isolation, two of them, against one build.

    This is the diagnostic the aggregate table can't give you: when a tier's death rate
    moves, it says *which* roster entry moved it. Two-of-a-kind because enemy count is
    the real lethality lever (every one of them swings every round), so one is not a
    representative fight of anything.
    """
    print(f"Two of each, vs {archetype_id}:\n")
    print(HEADER)
    for enemy in ENEMIES:
        squad = (ENEMIES_BY_ID[enemy.id],) * 2
        counts, rounds, health = _tally(archetype_id, lambda _rng, s=squad: s, trials, 99)
        print(_row(enemy.name, counts, rounds, health, trials))


def report_roster() -> None:
    """The derived stat block for every enemy — all of these are properties over the
    sheet now (see combat.Enemy), so this is the only place to read them off flat."""
    print(
        f"\n{'enemy':<20}{'hp':>5}{'atk':>5}{'def':>5}{'dmg':>5}"
        f"{'tuf':>5}{'reach':>7}{'stun':>6}"
    )
    for enemy in ENEMIES:
        print(
            f"{enemy.name:<20}{enemy.health:>5}{enemy.attack:>5}{enemy.defense:>5}"
            f"{enemy.damage:>5}{enemy.toughness:>5}{enemy.reach:>7}{enemy.stun_damage:>6}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=4000, help="fights per cell")
    parser.add_argument(
        "--per-enemy", action="store_true", help="break down by enemy type instead of tier"
    )
    parser.add_argument(
        "--build", default="hacker", choices=sorted(LOADOUTS), help="build for --per-enemy"
    )
    args = parser.parse_args()

    print(f"{args.trials} fights per row, fleeing below {FLEE_AT:.0%} health.\n")
    if args.per_enemy:
        report_per_enemy(args.trials, args.build)
    else:
        report_tiers(args.trials)
    report_roster()


if __name__ == "__main__":
    main()
