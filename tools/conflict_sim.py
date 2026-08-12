"""Corp conflict balance harness: maps resolve_attack outcomes across parameter space.

Not part of the game and not imported by it — a developer tool, run by hand.

**Why it exists.** DESIGN.md's corp conflict section quotes CONTEST_DIE, ATTACK_CHANCE,
and EXPANSION_CHANCE as "first-slice numbers, not balance-simulated" and flags
ATTACK_CHANCE vs EXPANSION_CHANCE as "most likely to need tuning — that ratio is
what decides how long a run stays peaceful."

Two modes:
  - Atomic contest grid: sweep committed × defense through resolve_attack dice.
  - Multi-faction map sim: ring of territories, AI factions expand/reinforce/attack
    daily, measuring how ATTACK_CHANCE vs EXPANSION_CHANCE shapes the run.

Usage:

    uv run python tools/conflict_sim.py                      # outcome grid
    uv run python tools/conflict_sim.py --map-sim            # multi-faction sim
    uv run python tools/conflict_sim.py --map-sim --sweep    # parameter sweep
    uv run python tools/conflict_sim.py --losses             # losses grid
    uv run python tools/conflict_sim.py --dice-sweep         # exact die pairs
"""

import argparse
import random
import statistics
from dataclasses import dataclass, field
from typing import Sequence

from shadowguy.corpmap import Territory
from shadowguy.corp_turn import CONTEST_DIE, resolve_attack

# --- Atomic contest constants ------------------------------------------------

COMMITTED_RANGE = range(1, 11)
DEFENSE_RANGE = range(0, 11)
TRIALS = 10000
SEED = 42

# --- Multi-faction map constants ---------------------------------------------

N_FACTIONS = 5
TOTAL_TERRITORIES = 30
MAX_DAYS = 200
MAP_TRIALS = 200

# From rivals.py — the tunable knobs DESIGN.md flags.
EXPANSION_CHANCE = 0.2
ATTACK_CHANCE = 0.09
AI_GARRISON_CHANCE = 0.35
AI_GARRISON_CAP = 5
MIN_AI_ATTACK_FORCE = 2
AI_TERRITORIES_PER_ATTACKER = 2


# =============================================================================
# Atomic contest
# =============================================================================

@dataclass(frozen=True)
class Cell:
    committed: int
    defense: int
    captured: float
    att_loss: float
    def_loss: float
    survivors: float


def make_territory(garrison: int, security: int, owner: str = "defender") -> Territory:
    t = Territory(id="test", name="Test District", x=0, y=0, owner=owner, garrison=garrison)
    if security:
        from shadowguy.corpmap import TerritoryModifier
        t.modifiers[TerritoryModifier.SECURITY] = security
    return t


def run_cell(committed: int, defense: int, trials: int, rng: random.Random) -> Cell:
    captured = 0
    total_att_loss = 0
    total_def_loss = 0
    total_survivors = 0
    for _ in range(trials):
        t = make_territory(garrison=defense, security=0)
        result = resolve_attack(t, "attacker", committed, rng)
        total_att_loss += result.attacker_losses
        total_def_loss += result.defender_losses
        total_survivors += committed - result.attacker_losses
        if result.captured:
            captured += 1
    n = trials
    return Cell(
        committed=committed, defense=defense,
        captured=captured / n,
        att_loss=total_att_loss / n,
        def_loss=total_def_loss / n,
        survivors=total_survivors / n,
    )


def sweep(committed_range, defense_range, trials, seed):
    rng = random.Random(seed)
    return {(c, d): run_cell(c, d, trials, rng) for c in committed_range for d in defense_range}


# =============================================================================
# Multi-faction map sim
# =============================================================================

@dataclass
class MapRun:
    """One run's aggregate stats."""
    days_to_neutral: int | None = None       # day all neutrals claimed
    days_to_first_attack: int | None = None  # day of first cross-border attack
    days_to_first_capture: int | None = None # day a faction first takes rival ground
    total_attacks: int = 0
    successful_attacks: int = 0
    factions_eliminated: int = 0
    largest_share: float = 0.0  # biggest faction's territory share at end
    final_territories: dict[str, int] = field(default_factory=dict)
    territory_history: list[dict[str, int]] = field(default_factory=list)
    attack_history: list[dict[str, int]] = field(default_factory=list)


def _attack_force(territories_held: int, per_attacker: int = AI_TERRITORIES_PER_ATTACKER) -> int:
    return max(MIN_AI_ATTACK_FORCE, territories_held // per_attacker)


def _build_ring(total: int) -> dict[str, Territory]:
    ts = {}
    for i in range(total):
        tid = f"t{i}"
        left = f"t{(i - 1) % total}"
        right = f"t{(i + 1) % total}"
        ts[tid] = Territory(id=tid, name=f"D{i}", x=i % 10, y=i // 10, owner="neutral",
                            connections=[left, right])
    return ts


def _place_factions(territories: dict[str, Territory], n_factions: int) -> list[str]:
    step = len(territories) // n_factions
    faction_ids = [f"f{i}" for i in range(n_factions)]
    for idx, fid in enumerate(faction_ids):
        tid = f"t{idx * step}"
        territories[tid].owner = fid
        territories[tid].garrison = 1
    return faction_ids


def _owned(territories: dict[str, Territory], faction_id: str) -> list[str]:
    return [tid for tid, t in territories.items() if t.owner == faction_id]


def _neighbors_of(territories: dict[str, Territory], owned: list[str]) -> set[str]:
    return {conn for tid in owned for conn in territories[tid].connections
            if territories[conn].owner not in ("neutral", territories[tid].owner)}


def _neutral_neighbors(territories: dict[str, Territory], owned: list[str]) -> set[str]:
    return {conn for tid in owned for conn in territories[tid].connections
            if territories[conn].owner == "neutral"}


def _rival_neighbors(territories: dict[str, Territory], owned: list[str]) -> set[str]:
    return {conn for tid in owned for conn in territories[tid].connections
            if territories[conn].owner not in ("neutral", territories[tid].owner)}


def run_map_once(seed: int, *, exp_chance: float = EXPANSION_CHANCE,
                 att_chance: float = ATTACK_CHANCE,
                 gar_chance: float = AI_GARRISON_CHANCE,
                 gar_cap: int = AI_GARRISON_CAP,
                 territories_per_attacker: int = AI_TERRITORIES_PER_ATTACKER,
                 max_days: int = MAX_DAYS) -> MapRun:
    rng = random.Random(seed)
    territories = _build_ring(TOTAL_TERRITORIES)
    faction_ids = _place_factions(territories, N_FACTIONS)
    result = MapRun()
    alive_factions = set(faction_ids)
    territory_history: list[dict[str, int]] = []
    attack_history: list[dict[str, int]] = []

    for day in range(1, max_days + 1):
        # Count territory snapshots every 10 days
        if day % 10 == 0 or day == 1:
            snapshot = {}
            for fid in faction_ids:
                snapshot[fid] = len(_owned(territories, fid))
            territory_history.append(snapshot)

        # Shuffle faction order so no one faction always goes first.
        order = list(alive_factions)
        rng.shuffle(order)

        attacks_today: dict[str, int] = {}
        captures_today: dict[str, int] = {}

        for fid in order:
            owned_list = _owned(territories, fid)
            if not owned_list:
                # Wiped out earlier in this same day. Leave it in alive_factions for
                # the end-of-day elimination pass to drop and *count* — discarding it
                # here meant every faction killed before its own turn came round was
                # never tallied, halving factions_eliminated.
                continue

            # 1. Expand onto neutral ground.
            neutral_candidates = list(_neutral_neighbors(territories, owned_list))
            if neutral_candidates and rng.random() < exp_chance:
                target_tid = rng.choice(neutral_candidates)
                territories[target_tid].owner = fid
                territories[target_tid].garrison = 0

            # 2. Reinforce the thinnest district — rivals._reinforce: one roll per
            # faction per day, +1 to the single thinnest holding under the cap. Not
            # a roll per district, which grew garrison with holdings (~2.1/day for a
            # six-district faction against the game's 0.35) and saturated every
            # district within days.
            thin = [tid for tid in owned_list if territories[tid].garrison < gar_cap]
            if thin and rng.random() < gar_chance:
                territories[min(thin, key=lambda t: (territories[t].garrison, t))].garrison += 1

            # 3. Attack a rival.
            rival_candidates = list(_rival_neighbors(territories, owned_list))
            if rival_candidates and rng.random() < att_chance:
                target_tid = rng.choice(rival_candidates)
                target = territories[target_tid]
                committed = _attack_force(len(owned_list), territories_per_attacker)
                atk_result = resolve_attack(target, fid, committed, rng)
                attacks_today[fid] = attacks_today.get(fid, 0) + 1
                result.total_attacks += 1
                if atk_result.captured:
                    captures_today[fid] = captures_today.get(fid, 0) + 1
                    result.successful_attacks += 1

        if attacks_today:
            attack_history.append(dict(attacks_today))
            if result.days_to_first_attack is None:
                result.days_to_first_attack = day
            if result.days_to_first_capture is None and any(v > 0 for v in captures_today.values()):
                result.days_to_first_capture = day

        # Check if all neutral ground is claimed.
        if result.days_to_neutral is None:
            remaining = sum(1 for t in territories.values() if t.owner == "neutral")
            if remaining == 0:
                result.days_to_neutral = day

        # Check for eliminations.
        for fid in list(alive_factions):
            if not _owned(territories, fid):
                alive_factions.discard(fid)
                result.factions_eliminated += 1

        # Stalemate check: if no attacks and no neutral left, we're in a cold war.
        if result.days_to_neutral is not None and not attacks_today:
            # Not necessarily a stalemate — could just be a quiet day.
            pass

    # Final snapshot.
    for fid in faction_ids:
        result.final_territories[fid] = len(_owned(territories, fid))
    total_held = sum(result.final_territories.values())
    if total_held:
        result.largest_share = max(result.final_territories.values()) / total_held

    result.territory_history = territory_history
    result.attack_history = attack_history
    return result


def report_map_single(run: MapRun) -> None:
    print("Multi-faction ring sim\n")
    print(f"  Neutral ground claimed: day {run.days_to_neutral or 'never'}")
    print(f"  First cross-border attack: day {run.days_to_first_attack or 'never'}")
    print(f"  First successful capture: day {run.days_to_first_capture or 'never'}")
    if run.total_attacks:
        print(f"  Total attacks: {run.total_attacks} "
              f"({run.successful_attacks} captured, "
              f"{run.successful_attacks / run.total_attacks:.1%})")
    else:
        print(f"  Total attacks: 0")
    print(f"  Factions eliminated: {run.factions_eliminated}")
    print(f"  Final territory distribution: {run.final_territories}")

    print("\n  Territory snapshots (day: [faction counts]):")
    for snap in run.territory_history:
        parts = "  ".join(f"{k}:{v:>2}" for k, v in snap.items())
        print(f"    {parts}")

    if run.attack_history:
        print(f"\n  Attack log ({len(run.attack_history)} days with attacks):")
        for i, day_attacks in enumerate(run.attack_history[:20]):
            print(f"    {day_attacks}")


def report_map_sweep(runs: list[tuple[float, float, list[MapRun]]]) -> None:
    """Parameter sweep table: EXPANSION_CHANCE (row) × ATTACK_CHANCE (col)."""
    print("\nParameter sweep: EXPANSION_CHANCE × ATTACK_CHANCE\n")
    print(f"{'EXP\\ATT':<12}", end="")
    att_values = sorted({att for _, att, _ in runs})
    for att in att_values:
        print(f"{att:>8}", end="")
    print()

    # Reorganize: group by (exp, att)
    grouped: dict[tuple[float, float], list[MapRun]] = {}
    for exp, att, batch in runs:
        grouped.setdefault((exp, att), []).extend(batch)

    all_exp = sorted({e for e, _ in grouped})
    all_att = sorted({a for _, a in grouped})

    for metric_name, fmt, default in [
        ("Neutral day", ">8.0f", None),
        ("1st attack", ">8.0f", None),
        ("1st capture", ">8.0f", None),
        ("Attacks", ">8.1f", None),
        ("Succ%%", ">7.1%", 0.0),
        ("Eliminated", ">9.1f", 0.0),
        ("Largest%%", ">8.1%", 0.0),
    ]:
        print(f"\n  {metric_name}:")
        header = f"{'EXP\\ATT':<12}" + "".join(f"{a:>8}" for a in all_att)
        print(f"  {header}")
        for exp in all_exp:
            row = f"  {exp:<12.2f}"
            for att in all_att:
                batch = grouped.get((exp, att), [])
                if metric_name == "Neutral day":
                    vals = [r.days_to_neutral for r in batch if r.days_to_neutral is not None]
                    v = statistics.mean(vals) if vals else float('nan')
                    row += f"{v:>8.0f}"
                elif metric_name == "1st attack":
                    vals = [r.days_to_first_attack for r in batch
                            if r.days_to_first_attack is not None]
                    v = statistics.mean(vals) if vals else float('nan')
                    row += f"{v:>8.0f}"
                elif metric_name == "1st capture":
                    vals = [r.days_to_first_capture for r in batch
                            if r.days_to_first_capture is not None]
                    v = statistics.mean(vals) if vals else float('nan')
                    row += f"{v:>8.0f}"
                elif metric_name == "Attacks":
                    v = statistics.mean([r.total_attacks for r in batch])
                    row += f"{v:>8.1f}"
                elif metric_name == "Succ%%":
                    vals = [r.successful_attacks / r.total_attacks for r in batch
                            if r.total_attacks > 0]
                    v = statistics.mean(vals) if vals else 0.0
                    row += f"{v:>7.1%}"
                elif metric_name == "Eliminated":
                    v = statistics.mean([r.factions_eliminated for r in batch])
                    row += f"{v:>9.1f}"
                elif metric_name == "Largest%%":
                    v = statistics.mean([r.largest_share for r in batch])
                    row += f"{v:>8.1%}"
            print(row)


def run_map_sweep(trials: int, seed: int) -> list[tuple[float, float, list[MapRun]]]:
    exp_values = [0.12, 0.16, 0.20, 0.24]
    att_values = [0.06, 0.09, 0.12, 0.15, 0.18]
    results = []
    rng = random.Random(seed)
    for exp in exp_values:
        for att in att_values:
            batch = []
            for _ in range(trials):
                run_seed = rng.randint(0, 2**31 - 1)
                batch.append(run_map_once(run_seed, exp_chance=exp, att_chance=att))
            results.append((exp, att, batch))
    return results


def run_defense_sweep(trials: int, seed: int) -> list[tuple[int, int, list[MapRun]]]:
    """Sweep garrison cap × territories-per-attacker at default EXP/ATT."""
    cap_values = [3, 4, 5, 6]
    tpa_values = [2, 3, 4]
    results = []
    rng = random.Random(seed)
    for cap in cap_values:
        for tpa in tpa_values:
            batch = []
            for _ in range(trials):
                run_seed = rng.randint(0, 2**31 - 1)
                batch.append(run_map_once(run_seed, gar_cap=cap, territories_per_attacker=tpa))
            results.append((cap, tpa, batch))
    return results


# =============================================================================
# Renderers (atomic contest)
# =============================================================================

def _pct(v: float) -> str:
    return f"{v:.1%}" if v >= 0.095 else (f"{v:.0%}" if v > 0 else "  0%")


def report_grid(results, committed_range, defense_range):
    header = "cmtd\\def" + "".join(f"{d:>6}" for d in defense_range)
    print(header)
    print("-" * len(header))
    for c in committed_range:
        row = f"{c:>4}    "
        for d in defense_range:
            row += _pct(results[(c, d)].captured).rjust(6)
        print(row)
    print()


def report_losses(results, committed_range, defense_range):
    print("Expected attacker losses (committed down, defense across):")
    header = "cmtd\\def" + "".join(f"{d:>6}" for d in defense_range)
    print(header)
    print("-" * len(header))
    for c in committed_range:
        row = f"{c:>4}    "
        for d in defense_range:
            row += f"{results[(c, d)].att_loss:>6.1f}"
        print(row)
    print()


def report_breakeven(results, committed_range, defense_range):
    print("Committed needed for >50%% capture at each defense level:")
    for d in defense_range:
        for c in committed_range:
            if results[(c, d)].captured > 0.5:
                print(f"  defense {d}: {c} committed ({results[(c,d)].att_loss:.1f} losses, "
                      f"{results[(c,d)].captured:.0%} capture)")
                break
        else:
            print(f"  defense {d}: never >50%% in range")
    print()


def report_dice_sweep():
    print("Dice lattice: attacker d6 ->, defender d6 |, (A)ttacker wins")
    for committed in range(1, 7):
        for defense in range(0, 7):
            print(f"\ncommitted={committed}  defense={defense}")
            print("    " + "".join(f" {ad:>2}" for ad in range(1, 7)))
            for dd in range(1, 7):
                row = f" {dd}: "
                for ad in range(1, 7):
                    row += "  A" if committed + ad > defense + dd else "  ."
                print(row)


def report_efficiency(results, committed_range, defense_range):
    print("Capture %% per operative committed (efficiency):")
    header = "cmtd\\def" + "".join(f"{d:>8}" for d in defense_range)
    print(header)
    print("-" * len(header))
    for c in committed_range:
        row = f"{c:>4}    "
        for d in defense_range:
            eff = results[(c, d)].captured / c if c > 0 else 0
            row += f"{eff:>8.2%}"
        print(row)
    print()


# =============================================================================
# Main
# =============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--trials", type=int, default=TRIALS,
                        help=f"trials per cell (default {TRIALS})")
    parser.add_argument("--losses", action="store_true",
                        help="show expected attacker losses grid")
    parser.add_argument("--breakeven", action="store_true",
                        help="show minimum committed for >50%% capture")
    parser.add_argument("--efficiency", action="store_true",
                        help="show capture%% per operative")
    parser.add_argument("--dice-sweep", action="store_true",
                        help="exact-dice lattice")
    parser.add_argument("--all", action="store_true", help="all atomic reports")
    parser.add_argument("--map-sim", action="store_true",
                        help="run multi-faction ring sim")
    parser.add_argument("--map-trials", type=int, default=MAP_TRIALS,
                        help=f"map sim runs for --map-sim (default {MAP_TRIALS})")
    parser.add_argument("--sweep", action="store_true",
                        help="with --map-sim: parameter sweep over EXP/ATT chances")
    parser.add_argument("--defense-sweep", action="store_true",
                        help="with --map-sim: sweep garrison cap x attack scaling")
    args = parser.parse_args()

    if args.map_sim:
        if args.defense_sweep:
            print(f"Defense sweep: {args.map_trials} runs/cell...")
            sweep_results = run_defense_sweep(args.map_trials, SEED)
            # Reorganize by (cap, tpa)
            grouped: dict[tuple[int, int], list[MapRun]] = {}
            for cap, tpa, batch in sweep_results:
                grouped.setdefault((cap, tpa), []).extend(batch)
            all_cap = sorted({c for c, _ in grouped})
            all_tpa = sorted({t for _, t in grouped})
            for metric_name in ["Attacks", "Succ%%", "Eliminated", "Largest%%"]:
                print(f"\n  {metric_name}  (row: garrison cap, col: terr/attacker):")
                header = f"{'cap\\tpa':<12}" + "".join(f"{t:>8}" for t in all_tpa)
                print(f"  {header}")
                for cap in all_cap:
                    row = f"  {cap:<12}"
                    for tpa in all_tpa:
                        batch = grouped.get((cap, tpa), [])
                        if metric_name == "Attacks":
                            v = statistics.mean([r.total_attacks for r in batch])
                            row += f"{v:>8.1f}"
                        elif metric_name == "Succ%%":
                            vals = [r.successful_attacks / r.total_attacks for r in batch
                                    if r.total_attacks > 0]
                            v = statistics.mean(vals) if vals else 0.0
                            row += f"{v:>7.1%}"
                        elif metric_name == "Eliminated":
                            v = statistics.mean([r.factions_eliminated for r in batch])
                            row += f"{v:>9.1f}"
                        elif metric_name == "Largest%%":
                            v = statistics.mean([r.largest_share for r in batch])
                            row += f"{v:>8.1%}"
                    print(row)
        elif args.sweep:
            print(f"Parameter sweep: {args.map_trials} runs/cell...")
            sweep_results = run_map_sweep(args.map_trials, SEED)
            report_map_sweep(sweep_results)
        else:
            rng = random.Random(SEED)
            for i in range(min(args.map_trials, 5)):
                run_seed = rng.randint(0, 2**31 - 1)
                run = run_map_once(run_seed)
                # The run's own seed, not SEED — that one only seeds the seed picker,
                # so printing it made an interesting run impossible to reproduce.
                print(f"\n--- Run {i + 1} (seed={run_seed}) ---")
                report_map_single(run)
            if args.map_trials > 5:
                print(f"\nShowing first 5 of {args.map_trials} — use --sweep for aggregate stats.")
        return

    results = sweep(COMMITTED_RANGE, DEFENSE_RANGE, args.trials, SEED)

    show_all = args.all or not any([args.losses, args.breakeven, args.efficiency, args.dice_sweep])

    if show_all or not any([args.losses, args.breakeven, args.efficiency, args.dice_sweep]):
        report_grid(results, COMMITTED_RANGE, DEFENSE_RANGE)
    if show_all or args.losses:
        report_losses(results, COMMITTED_RANGE, DEFENSE_RANGE)
    if show_all or args.breakeven:
        report_breakeven(results, COMMITTED_RANGE, DEFENSE_RANGE)
    if show_all or args.efficiency:
        report_efficiency(results, COMMITTED_RANGE, DEFENSE_RANGE)
    if args.dice_sweep:
        report_dice_sweep()

    print(f"CONTEST_DIE = {CONTEST_DIE}  ({args.trials} trials/cell)")


if __name__ == "__main__":
    main()
