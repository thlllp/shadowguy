"""Matrix fight balance harness: simulates ICE combat across build tiers.

Not part of the game and not imported by it — a developer tool, run by hand.

DESIGN.md: "Not yet balance-simulated, swingier than intended. ... Re-run a presets x
tiers sim before leaning on either the old rates or the network shape."

Models the core ATTACK loop: player Cybercombat vs ICE defense, ICE bites back every
round. No Sleaze/Extract/Harden/security — just the straight fight, which is where
DESIGN.md's old figures came from.

Usage:
    uv run python tools/matrix_sim.py
    uv run python tools/matrix_sim.py --per-ice     # one ICE type at a time
"""

import argparse
import random
import statistics
from dataclasses import dataclass

from shadowguy.character import Character
from shadowguy.combat import resolve_hit
from shadowguy.checks import CheckResult
from shadowguy.matrix import (
    MIN_READY_CYBERCOMBAT,
    ICE,
    ICE_BY_ID,
    ICE_TIERS,
    roll_ice,
    firewall_defense,
    firewall_soak,
    player_attack_damage,
    player_integrity,
    ATTACK_SKILL,
    SLEAZE_SKILL,
)
from shadowguy.skills import skill_value
from shadowguy.shops import ITEMS_BY_ID, InventoryItem
from shadowguy.archetypes import ARCHETYPES_BY_ID
from shadowguy.inventory import equipped_deck_rating

TRIALS = 4000
SEED = 42
MAX_ROUNDS = 40


@dataclass
class MatrixFightResult:
    won: bool
    rounds: int
    integrity_left: int
    max_integrity: int


# The builds worth measuring: Hacker (the specialist), a medium-Logic build
# (Gunslinger), and a Logic-1 build (Enforcer with no deck).
BUILDS = {
    "hacker": {
        "archetype": "hacker",
        "logic": None,      # keep archetype's default
        "infer": None,
        "cybercombat": None,
        "deck": "zetatech_rig",  # rating 3, 3 slots
    },
    "hacker_burner": {
        "archetype": "hacker",
        "deck": "burner_deck",  # rating 1, 1 slot
    },
    "gunslinger": {
        "archetype": "gunslinger",
        "deck": "cracked_cyberdeck",  # rating 2, 2 slots
    },
    "enforcer_nodeck": {
        "archetype": "enforcer",
        "deck": None,  # no deck — jacking in bare
    },
}


def _is_deck(item_id: str) -> bool:
    """A cyberdeck: inventory.active_deck_entry's rule (Slot None) plus a Logic bonus,
    which is what separates a deck from the other slotless items (junk, datashards)."""
    item = ITEMS_BY_ID[item_id]
    return item.slot is None and item.bonuses.get("logic", 0) > 0


def build_character(build_id: str, deck_id: str | None) -> Character:
    """The archetype's creation loadout, with its deck swapped for this build's.

    The swap has to *replace*, not append: a deck is a Slot None item, so any number
    can be equipped at once, and active_deck_entry takes the best of them. The Hacker
    preset already ships a zetatech_rig, so appending left two decks equipped — the
    burner build silently measured the rig, and the extra rig's Logic bonus stacked
    into integrity, Cybercombat and firewall all at once.

    Stripping the rig also drops the programs the preset installed on it (they live on
    the InventoryItem), which is the honest reading: a burner has one slot, not three.
    """
    spec = BUILDS[build_id]
    char = Character(name=build_id)
    ARCHETYPES_BY_ID[spec["archetype"]].apply(char)
    char.inventory = [entry for entry in char.inventory if not _is_deck(entry.item_id)]
    if deck_id:
        char.inventory.append(InventoryItem(item_id=deck_id))
    char.health = char.max_health
    return char


def matrix_fight(char: Character, ices: list, rng: random.Random) -> MatrixFightResult:
    """Run one matrix fight: player attacks one ICE per round, then all ICE bite.

    Every derived number comes from matrix.py's own helpers rather than being
    recomputed here, so the sim can't drift from the game the way it had: soak was
    hard-zeroed, when _ice_bite actually passes firewall_soak (the runner's whole
    Logic) plus Harden, and player damage was the bare deck rating rather than
    player_attack_damage's DECK_BASE_DAMAGE + rating.

    Simplified from the real game:
    - Player always attacks the first standing ICE (no target choice model)
    - No Sleaze/Extract/Harden — just the Attack loop, so no Harden bonus on top
      of firewall_soak
    - No opening Drop bite (no ambush)
    - No security mechanic
    - No active programs; the passive bonuses of whatever the preset installed on
      its deck do count, since the helpers fold them in
    """
    integrity = player_integrity(char)
    max_integrity = integrity
    cybercombat = skill_value(char, ATTACK_SKILL)
    attack_damage = player_attack_damage(char)
    firewall = firewall_defense(char)
    harden_soak = firewall_soak(char)  # no Harden in this model, so soak alone

    fighters = [IceFightSim(ice=ice, active=True) for ice in ices]

    for _round in range(1, MAX_ROUNDS + 1):
        # Player attacks first standing ICE
        target = next((f for f in fighters if f.active), None)
        if target is None:
            return MatrixFightResult(won=True, rounds=_round, integrity_left=integrity, max_integrity=max_integrity)

        # Resolve player hit
        roll, damage = resolve_hit(
            rng, cybercombat, 0, target.ice.defense, attack_damage, target.ice.soak
        )
        if roll.result.passed:
            target.hp = max(0, target.hp - damage)
            if target.hp <= 0:
                target.active = False

        # ICE phase — each standing ICE bites
        for f in fighters:
            if not f.active:
                continue
            roll, dmg = resolve_hit(
                rng, f.ice.attack, 0, firewall, f.ice.damage, harden_soak
            )
            if roll.result.passed:
                integrity = max(0, integrity - dmg)
                if integrity <= 0:
                    return MatrixFightResult(won=False, rounds=_round, integrity_left=0, max_integrity=max_integrity)

    # Ran out of rounds — count as a loss
    return MatrixFightResult(won=False, rounds=MAX_ROUNDS, integrity_left=integrity, max_integrity=max_integrity)


@dataclass
class IceFightSim:
    ice: object  # Ice dataclass
    hp: int = 0
    active: bool = True

    def __post_init__(self):
        if not self.hp:
            self.hp = self.ice.integrity


def run_sweep(trials: int) -> dict[tuple[str, int], list[MatrixFightResult]]:
    results = {}
    rng = random.Random(SEED)
    for build_id in BUILDS:
        spec = BUILDS[build_id]
        for tier in sorted(ICE_TIERS):
            batch = []
            char = build_character(build_id, spec["deck"])
            for _ in range(trials):
                ices = list(roll_ice(tier, rng))
                batch.append(matrix_fight(char, ices, rng))
            results[(build_id, tier)] = batch
    return results


def run_per_ice(build_id: str, trials: int) -> dict[str, list[MatrixFightResult]]:
    results = {}
    rng = random.Random(SEED)
    char = build_character(build_id, BUILDS[build_id]["deck"])
    for ice in ICE:
        batch = []
        # Two of each — DESIGN.md measured against pairs
        for _ in range(trials):
            batch.append(matrix_fight(char, [ice, ice], rng))
        results[ice.name] = batch
    return results


def report_tiers(results: dict[tuple[str, int], list[MatrixFightResult]]) -> None:
    trials = len(next(iter(results.values())))
    print(f"\n{trials} fights per cell:\n")
    print(f"{'build':<22} {'tier':>5} {'won':>7} {'rounds':>8} {'int left':>9} {'max int':>8}")
    print("-" * 64)
    for build_id in BUILDS:
        for tier in sorted(ICE_TIERS):
            batch = results[(build_id, tier)]
            wins = sum(1 for r in batch if r.won)
            avg_rounds = statistics.mean(r.rounds for r in batch)
            avg_int = statistics.mean(r.integrity_left / r.max_integrity for r in batch)
            avg_max = statistics.mean(r.max_integrity for r in batch)
            print(f"{build_id:<22} {tier:>5} {wins / trials:>6.1%} {avg_rounds:>8.1f} "
                  f"{avg_int:>8.1%} {avg_max:>8.0f}")


def report_per_ice(results: dict[str, list[MatrixFightResult]], build_id: str) -> None:
    trials = len(next(iter(results.values())))
    print(f"\nTwo of each, vs {build_id} ({trials} trials):\n")
    print(f"{'ICE':<18} {'won':>7} {'rounds':>8} {'int left':>9}")
    print("-" * 46)
    for name, batch in results.items():
        wins = sum(1 for r in batch if r.won)
        avg_rounds = statistics.mean(r.rounds for r in batch)
        avg_int = statistics.mean(r.integrity_left / r.max_integrity for r in batch)
        print(f"{name:<18} {wins / trials:>6.1%} {avg_rounds:>8.1f} {avg_int:>8.1%}")


def report_charlie() -> None:
    """Print the derived matrix stats for each build — integrity, firewall, cybercombat."""
    print("\nDerived matrix stats per build:")
    print(f"{'build':<22} {'int':>5} {'fw':>5} {'soak':>5} {'cb':>5} {'dmg':>5} {'deck':>5}")
    print("-" * 60)
    for build_id, spec in BUILDS.items():
        char = build_character(build_id, spec["deck"])
        print(f"{build_id:<22} {player_integrity(char):>5} {firewall_defense(char):>5} "
              f"{firewall_soak(char):>5} {skill_value(char, ATTACK_SKILL):>5} "
              f"{player_attack_damage(char):>5} {equipped_deck_rating(char.inventory):>5}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=TRIALS,
                        help=f"fights per cell (default {TRIALS})")
    parser.add_argument("--per-ice", action="store_true",
                        help="break down by ICE type instead of tier")
    parser.add_argument("--build", default="hacker",
                        choices=sorted(BUILDS), help="build for --per-ice")
    args = parser.parse_args()

    report_charlie()

    if args.per_ice:
        results = run_per_ice(args.build, args.trials)
        report_per_ice(results, args.build)
    else:
        results = run_sweep(args.trials)
        report_tiers(results)


if __name__ == "__main__":
    main()
