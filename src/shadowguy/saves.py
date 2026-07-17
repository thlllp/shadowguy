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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SAVE_DIR = Path.home() / ".shadowguy" / "saves"
SAVE_SUFFIX = ".save"
# Bump on any change that makes an old bundle unloadable; older saves are then refused.
# v2 added location_gigs (per-location gig offers).
# v3 added Character.free_travel_used and moved vehicles onto Slot.VEHICLE (a pre-v3
# Character has no free_travel_used and its owned vehicles were unlimited-slot).
# v4 added real estate / safehouses: Location.listings, and the SAFEHOUSE/REAL_ESTATE
# LocationKinds (a pre-v4 map has neither).
# v5 added Character.health_kit_used_today (a pre-v5 Character lacks it) and reworked
# hospitals into daily inpatient stays.
# v6 added corp HQs: the CORP_HQ LocationKind and one HQ location per faction (a pre-v6
# map has none).
# v7 added tactical-combat stages: scene.Stage gained a `tactical` field, so a pre-v7
# pickled Stage (inside an accepted job or a gig) lacks the attribute.
# v8 added ranged enemies: combat.Enemy gained a `reach` field, so a pre-v8 pickled
# Enemy (inside an accepted job's fight stages) lacks the attribute.
# v9 added job crew roles: scene.Scene gained a `roles` field (a pre-v9 pickled Scene,
# inside an accepted job, lacks it).
# v10 renamed the SOCIAL LocationKind to BAR (a pre-v10 pickled map holds LocationKind
# .SOCIAL, which no longer exists) and added Character.crew (recruited runners).
# v11 reshaped Character.crew from a list of runner ids into a list of CrewHire (runner +
# optional job_id: for-job vs indefinite engagement).
# v12 grew FIXER_ROSTER (3 street fixers -> 6 street + 3 corp-affiliated) and added
# Fixer.faction_id (a pre-v12 save's fixers list is the old roster of 3, seated by the
# old neutral-only rule).
# v13 added Security contracts (security.py): Character.security_contracts and
# Fixer.security_offers/max_security_offers (a pre-v13 Character/Fixer lacks all three).
# v14 added Burglary jobs: scene.Stage gained a `burglary` field (same shape of break
# as v7's `tactical` field) -- a pre-v14 pickled Stage (inside an accepted job) lacks it.
# v15 added Data Heist / matrix combat: scene.Stage gained a `matrix` field (same shape
# of break again) -- a pre-v15 pickled Stage lacks it.
SAVE_VERSION = 16
# The run fields a bundle must carry (app.ShadowguyApp writes and reads exactly these).
# Checked at load so a payload that unpickles but isn't a whole run is rejected here,
# at the boundary, rather than half-applied to the live App by the caller.
STATE_KEYS = frozenset({"rng", "corp_map", "character", "fixers", "location_gigs"})


@dataclass(frozen=True)
class SaveSlot:
    """One save file on disk, described from its filename alone (no unpickling)."""

    path: Path
    day: int
    saved_at: datetime

    @property
    def label(self) -> str:
        return f"Day {self.day} — {self.saved_at:%Y-%m-%d %H:%M}"


# Filename timestamp: microsecond resolution so two saves in the same second get
# distinct names instead of the later one silently clobbering the earlier. The load
# list still shows minute resolution (SaveSlot.label) — this is only about uniqueness.
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S-%f"


def save_game(state: dict[str, Any], day: int, now: datetime | None = None) -> SaveSlot:
    """Pickle `state` to a new file auto-named `day{N}_{timestamp}`. `now` is a seam for
    tests; production passes nothing and gets the wall clock."""
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    path = SAVE_DIR / f"day{day}_{now:{_TIMESTAMP_FORMAT}}{SAVE_SUFFIX}"
    payload = {"version": SAVE_VERSION, "state": state}
    with path.open("wb") as handle:
        pickle.dump(payload, handle)
    return SaveSlot(path, day, now)


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
    state = payload.get("state")
    if not isinstance(state, dict) or not STATE_KEYS <= state.keys():
        raise ValueError("save is missing required run state")
    return state


def _slot_from_path(path: Path) -> SaveSlot | None:
    # filename stem is `day{N}_{timestamp}`; anything else isn't ours.
    try:
        day_part, ts_part = path.stem.split("_", 1)
        day = int(day_part.removeprefix("day"))
        saved_at = datetime.strptime(ts_part, _TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return SaveSlot(path, day, saved_at)
