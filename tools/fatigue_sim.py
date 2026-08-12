"""Fatigue balance harness: simulates rest cycles and stat penalties over time.

Not part of the game and not imported by it — a developer tool, run by hand.

DESIGN.md: "Not balance-simulated — the grace window, growth divisor, and cap are
first-slice numbers, easy to retune."

Models a runner's daily cycle: jobs cost hours, rest costs 8h, fatigue compounds on
the day tick when elapsed - last_rest > FATIGUE_GRACE_HOURS. Penalty caps at 3.

Usage:
    uv run python tools/fatigue_sim.py
    uv run python tools/fatigue_sim.py --sweep     # parameter sweep
"""

import argparse
from dataclasses import dataclass, field

# From character.py
FATIGUE_GRACE_HOURS = 24
FATIGUE_GROWTH_DIVISOR = 3
FATIGUE_STAT_PENALTY_CAP = 3
REST_HOURS_COST = 8
HOURS_PER_DAY = 24

# Job costs (from DESIGN.md)
JOB_HOURS_TIER0 = 8
JOB_HOURS_TIER1 = 12
TRAVEL_HOURS = 2


@dataclass
class FatigueTrace:
    """One day's snapshot."""
    day: int
    fatigue: int
    penalty: int
    hours_since_rest: float
    rested: bool = False


def day_of(elapsed: float) -> int:
    """Character.day: elapsed_hours // HOURS_PER_DAY + 1, so day 1 is hours 0-23."""
    return int(elapsed // HOURS_PER_DAY) + 1


def simulate(rest_every: int, job_hours: float, travel_per_job: float,
             max_days: int = 60) -> list[FatigueTrace]:
    """Run one fatigue trace. rest_every=N means rest once every N days.

    Drives the real clock rather than one tick per work cycle: a job costs
    JOB_HOURS + TRAVEL_HOURS (10-14h), so a cycle does *not* equal a day, and the
    fatigue tick fires per midnight crossed — sometimes none, sometimes one. Ticking
    once per cycle instead, as this used to, ran roughly twice as many ticks as the
    game does and made every pattern look far harsher than it is.

    Matching ShadowguyApp.spend_time / Character.on_new_day:
    - a spend crossing K midnights fires K ticks, all reading the *final* elapsed
      (spend_time sets elapsed_hours before looping the boundaries)
    - each tick adds fatigue when elapsed - last_rest_hour > FATIGUE_GRACE_HOURS
    - a Rest is its own REST_HOURS_COST spend (which can itself cross midnight, and
      that crossing still ticks against the *old* last_rest_hour), and only then does
      Character.mark_rested halve fatigue and stamp last_rest_hour
    """
    fatigue = 0
    last_rest_hour = 0.0
    elapsed = 0.0
    trace: list[FatigueTrace] = []
    rest_days: set[int] = set()
    last_rest_day = 0

    def spend(hours: float) -> None:
        """app.spend_time: advance the clock, one day tick per midnight crossed."""
        nonlocal elapsed, fatigue
        old_day = day_of(elapsed)
        elapsed += hours
        for day in range(old_day + 1, day_of(elapsed) + 1):
            if elapsed - last_rest_hour > FATIGUE_GRACE_HOURS:
                fatigue += 1 + fatigue // FATIGUE_GROWTH_DIVISOR
            trace.append(FatigueTrace(
                day=day,
                fatigue=fatigue,
                penalty=min(FATIGUE_STAT_PENALTY_CAP, fatigue),
                hours_since_rest=elapsed - last_rest_hour,
            ))

    while day_of(elapsed) <= max_days:
        spend(job_hours + travel_per_job)
        if day_of(elapsed) - last_rest_day >= rest_every:
            spend(REST_HOURS_COST)
            fatigue //= 2  # Character.mark_rested
            last_rest_hour = elapsed
            last_rest_day = day_of(elapsed)
            rest_days.add(last_rest_day)

    for entry in trace:
        entry.rested = entry.day in rest_days
    return [entry for entry in trace if entry.day <= max_days]


def report_trace(label: str, trace: list[FatigueTrace]) -> None:
    print(f"\n{label}")
    print(f"{'day':>4} {'fatigue':>8} {'penalty':>8} {'h since rest':>14} {'rested':>8}")
    print("-" * 48)
    step = 1 if len(trace) <= 20 else max(1, len(trace) // 20)
    for i, t in enumerate(trace):
        if i % step == 0:
            rest_marker = "R" if t.rested else ""
            print(f"{t.day:>4} {t.fatigue:>8} {t.penalty:>8} {t.hours_since_rest:>14.0f} {rest_marker:>8}")


def report_summary(traces: dict[str, list[FatigueTrace]]) -> None:
    """Days until penalty reaches 1, 2, 3 under each pattern."""
    print("\nFatigue penalty timeline:")
    print(f"{'pattern':<30} {'penalty 1':>10} {'penalty 2':>10} {'penalty 3':>10} {'max fatigue':>12}")
    print("-" * 74)
    for label, trace in traces.items():
        p1 = next((t.day for t in trace if t.penalty >= 1), None)
        p2 = next((t.day for t in trace if t.penalty >= 2), None)
        p3 = next((t.day for t in trace if t.penalty >= 3), None)
        max_fat = max(t.fatigue for t in trace)
        print(f"{label:<30} {str(p1 or 'never'):>10} {str(p2 or 'never'):>10} "
              f"{str(p3 or 'never'):>10} {max_fat:>12}")


def sweep(grace_values: list[int], divisor_values: list[int],
          rest_every: int, job_hours: float, max_days: int = 120) -> None:
    """Parameter sweep over FATIGUE_GRACE_HOURS and FATIGUE_GROWTH_DIVISOR."""
    global FATIGUE_GRACE_HOURS, FATIGUE_GROWTH_DIVISOR
    original_grace = FATIGUE_GRACE_HOURS
    original_div = FATIGUE_GROWTH_DIVISOR

    print(f"\nSweep: GRACE × DIVISOR at rest_every={rest_every}, job_hours={job_hours}")
    print(f"{'grace\\div':<12}", end="")
    for div in divisor_values:
        print(f"{div:>8}", end="")
    print()

    for grace in grace_values:
        row = f"{grace:<12}"
        for div in divisor_values:
            FATIGUE_GRACE_HOURS = grace
            FATIGUE_GROWTH_DIVISOR = div
            trace = simulate(rest_every, job_hours, TRAVEL_HOURS, max_days)
            mean_penalty = sum(t.penalty for t in trace) / len(trace)
            row += f"{mean_penalty:>8.2f}"
        print(row)

    FATIGUE_GRACE_HOURS = original_grace
    FATIGUE_GROWTH_DIVISOR = original_div


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sweep", action="store_true",
                        help="parameter sweep over GRACE hours and DIVISOR")
    parser.add_argument("--days", type=int, default=60,
                        help="days to simulate (default 60)")
    args = parser.parse_args()

    job_labels = [
        ("rest every day, tier0 job", 1, JOB_HOURS_TIER0),
        ("rest every day, tier1+ job", 1, JOB_HOURS_TIER1),
        ("rest every 2 days, tier0 job", 2, JOB_HOURS_TIER0),
        ("rest every 2 days, tier1+ job", 2, JOB_HOURS_TIER1),
        ("rest every 3 days, tier0 job", 3, JOB_HOURS_TIER0),
        ("rest every 3 days, tier1+ job", 3, JOB_HOURS_TIER1),
        ("rest every 5 days, tier0 job", 5, JOB_HOURS_TIER0),
        ("rest every 5 days, tier1+ job", 5, JOB_HOURS_TIER1),
        ("never rest, tier0 job", 999, JOB_HOURS_TIER0),
        ("never rest, tier1+ job", 999, JOB_HOURS_TIER1),
    ]

    traces = {}
    for label, rest_every, job_hours in job_labels:
        trace = simulate(rest_every, job_hours, TRAVEL_HOURS, args.days)
        traces[label] = trace
        report_trace(label, trace)

    report_summary(traces)

    if args.sweep:
        for rest_every, job_hours in [(2, 12.0), (3, 12.0), (999, 12.0)]:
            sweep(
                grace_values=[12, 18, 24, 30, 36, 48],
                divisor_values=[1, 2, 3, 4, 5, 6],
                rest_every=rest_every,
                job_hours=job_hours,
                max_days=args.days,
            )

    print(f"\nFATIGUE_GRACE_HOURS={FATIGUE_GRACE_HOURS}  "
          f"FATIGUE_GROWTH_DIVISOR={FATIGUE_GROWTH_DIVISOR}  "
          f"FATIGUE_STAT_PENALTY_CAP={FATIGUE_STAT_PENALTY_CAP}")


if __name__ == "__main__":
    main()
