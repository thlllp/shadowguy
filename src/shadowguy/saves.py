"""Save/load of a whole run.

A run's state is a deep object graph — the Character carries full accepted-job
Scene graphs, and the CorpMap/Fixers hold their own procedurally-generated content
plus the run's rng. Rather than hand-write (and keep in sync) a JSON serializer for
every invariant-checked dataclass in that graph, a save is a single pickle of the
whole bundle: it captures the rng and the generated content exactly, with no schema.

The tradeoff is that a save is only loadable by code whose dataclass shapes still
match — fine for an in-development roguelite with no meta-progression, where a run is
disposable. `SAVE_VERSION` is the coarse guard: bump it on a breaking state change and
old saves are refused at load rather than exploding half-way through unpickling.

Leaf module: pickle resolves the game classes by their own module paths at load time,
so nothing here imports them, and app.py can import this without a cycle. The filename
carries day + timestamp so `list_saves` can render the load list without unpickling a
single file — a corrupt or stale save only fails when you actually pick it.
"""

import pickle
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SAVE_DIR = Path.home() / ".shadowguy" / "saves"
SAVE_SUFFIX = ".save"
# Bump on any change that makes an old bundle unloadable; older saves are then refused.
SAVE_VERSION = 1


@dataclass(frozen=True)
class SaveSlot:
    """One save file on disk, described from its filename alone (no unpickling)."""

    path: Path
    day: int
    saved_at: datetime

    @property
    def label(self) -> str:
        return f"Day {self.day} — {self.saved_at:%Y-%m-%d %H:%M}"


def save_game(state: dict[str, Any], day: int, now: datetime | None = None) -> SaveSlot:
    """Pickle `state` to a new file auto-named `day{N}_{YYYYMMDD-HHMMSS}`. `now` is a
    seam for tests; production passes nothing and gets the wall clock."""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now()
    path = SAVE_DIR / f"day{day}_{now:%Y%m%d-%H%M%S}{SAVE_SUFFIX}"
    payload = {"version": SAVE_VERSION, "state": state}
    with path.open("wb") as handle:
        pickle.dump(payload, handle)
    return SaveSlot(path, day, now.replace(microsecond=0))


def list_saves() -> list[SaveSlot]:
    """Every parseable save, newest first. Files whose names don't fit the pattern are
    skipped rather than raising — the directory is the user's, not ours to police."""
    if not SAVE_DIR.exists():
        return []
    slots = [slot for path in SAVE_DIR.glob(f"*{SAVE_SUFFIX}") if (slot := _slot_from_path(path))]
    slots.sort(key=lambda slot: slot.saved_at, reverse=True)
    return slots


def load_game(path: Path) -> dict[str, Any]:
    """Unpickle a save and hand back its `state` bundle. Raises on a version mismatch
    or a malformed payload; callers turn that into a user-facing message rather than a
    crash, since a save can go stale as the code moves on."""
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, dict) or payload.get("version") != SAVE_VERSION:
        raise ValueError("save was written by an incompatible version")
    return payload["state"]


def _slot_from_path(path: Path) -> SaveSlot | None:
    # filename stem is `day{N}_{YYYYMMDD-HHMMSS}`; anything else isn't ours.
    try:
        day_part, ts_part = path.stem.split("_", 1)
        day = int(day_part.removeprefix("day"))
        saved_at = datetime.strptime(ts_part, "%Y%m%d-%H%M%S")
    except ValueError:
        return None
    return SaveSlot(path, day, saved_at)
