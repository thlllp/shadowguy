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
# v16 added gang standing and turf encounters (encounters.py): Character.gang_standing
# (a pre-v16 pickled Character lacks the field).
# v17 added rival AI actions (rivals.py): ShadowguyApp.rival_actions (a pre-v17 save
# lacks the key).
# v18 added cyberdeck programs: Character.owned_programs and InventoryItem.
# installed_programs (a pre-v18 pickled Character/InventoryItem lacks both).
# v19 reshaped matrix combat from a flat ICE pool to a node network:
# scene.MatrixStage.ice (tuple[Ice, ...]) became .network (a matrix.MatrixNetwork)
# -- a pre-v19 pickled MatrixStage (inside an accepted Data Heist job) has the old
# field, not the new one.
# v20 added the player's own Corp turn (corp_turn.py): ShadowguyApp.corp_state
# (a pre-v20 save lacks the key).
# v21 added each faction's research facility: corpmap.Location gained a
# `research_tier` field and CorpState gained `research_points` (a pre-v21 pickled
# Location/CorpState lacks both).
# v22 added each faction's academy: corpmap.Location gained an `academy_tier`
# field, and CorpState.expansion_used_today was renamed daily_action_used and
# gained a sibling `employees` field (a pre-v22 pickled Location/CorpState has
# neither the new fields nor the renamed one).
# v23 split Academy training into two categories: CorpState.employees was
# replaced by separate `scientists`/`operatives` fields (a pre-v23 pickled
# CorpState has the old single field instead).
# v24 added research facility labs: corpmap.Location gained a `labs_built`
# field (a pre-v24 pickled Location lacks it).
# v25 added research facility efficiency upgrades: corpmap.Location gained an
# `efficiency_upgrades` field (a pre-v25 pickled Location lacks it).
# v26 added research assistants: CorpState gained a `research_assistants`
# field and `research_points` became a float (a pre-v26 pickled CorpState
# lacks the new field).
# v27 added ShadowguyApp.corp_only (a pre-v27 save lacks the key): tracks whether
# this run was started fresh as a Corp (New Game -> Corp, no runner ever built).
# load_state always reopens CorpMapScreen regardless; corp_only instead decides which
# category sidebar (_RUNNER_CATEGORIES or _CORP_CATEGORIES) that screen shows.
# v28 added CorpMap.relations (a pre-v28 pickled CorpMap lacks the field): standing
# between every Faction/Gang pair, independent of the player.
# v29 replaced Character.day (a stored int) with elapsed_hours (a float; day became a
# derived property) and removed stamina (stamina/max_stamina/can_afford/spend_stamina/
# restore_stamina, BASE_STAMINA) and free-travel metering (free_travel_used/
# free_travel_remaining/spend_free_travel) entirely -- runner-mode actions now spend
# hours via ShadowguyApp.spend_time. Also renamed Scene.stamina_cost to hours_cost (a
# pre-v29 pickled Scene -- inside an accepted job, a location's gig, or a fixer's
# offers -- has the old field instead). A pre-v29 pickled Character has day/stamina/
# free_travel_used instead of elapsed_hours.
# v30 added corp technology (corp_turn.TECHNOLOGIES, the first thing to spend
# research points): CorpState gained a `researched` set of technology ids (a
# pre-v30 pickled CorpState lacks it).
# v31 added Surveillance detection (surveillance.py): CorpState gained a
# `sightings` list (a pre-v31 pickled CorpState lacks it), and ShadowguyApp
# gained the `rival_runner_locations` key -- the persistent runner_id ->
# territory_id map rivals.py now uses to wander independent RivalRunners
# around the map (a pre-v31 save lacks the key entirely).
# v32 added post-creation experience (character.py): Character gained
# `experience` (spent via spend_experience_on_stat/spend_experience_on_skill)
# and `crew_experience` (a pre-v32 pickled Character lacks both), and
# scene.Outcome gained an `experience_delta` field -- a pre-v32 pickled Outcome
# (inside an accepted job, a fixer's offers, or a location's gig) lacks it.
# v33 added cyberware (cybernetics.py): Character gained `installed_cyberware`
# (a pre-v33 pickled Character lacks it).
# v34 added Humanity (character.py): Character gained `humanity` (a pre-v34
# pickled Character lacks it). It landed as a fixed baseline with nothing to
# raise or lower it; the cyberpsychosis work later turned it into a ceiling that
# SURGERY_SCARRING erodes, which needed *no* bump of its own -- the field already
# existed and only its annotation widened int -> float, so a save written before
# that change loads at 6 with zero implied scarring, which is exactly right.
# v35 made Academy training take days (corp_turn.py): CorpState gained
# `pending_recruit` (a pre-v35 pickled CorpState lacks it, None when idle).
# v36 decoupled Rest from the midnight tick and added fatigue (character.py):
# Character gained `last_rest_hour` and `fatigue` (a pre-v36 pickled Character
# lacks both).
# v37 added gang delivery jobs (jobs.py): Character gained `smuggling_job`
# (a pre-v37 pickled Character lacks it, None when idle).
# v38 gave independent runners a real turn (rivals.py): the bundle's
# `rival_runner_locations` key (runner_id -> territory_id) is replaced by
# `rival_runner_states` (runner_id -> rivals.RunnerState, carrying activity and
# recovery alongside the territory), and fixer.JobOffer gained `taken_by` -- a
# pre-v38 pickled JobOffer (on a fixer's board or inside an accepted job) lacks it.
# v39 replaced Contacts with the Phone screen (screens/info_screens.py) and added
# its Alarm Clock tab: Character gained `alarm_hour` (a pre-v39 pickled Character
# lacks it, None when no alarm is set).
# v40 added each Faction's public website (WebScreen's webapp_ rows now push
# CorpWebsiteScreen instead of a toast): ShadowguyApp gained `rival_researched`
# (faction_id -> set of Technology ids a rival has "researched", rivals.py's
# unstaffed flavor-only roll) and `faction_events` (faction_id -> list of
# corp_turn.FactionEvent, the blog itself) -- a pre-v40 save lacks both keys.
# v41 made stun a persistent Character stat instead of a per-fight counter:
# Character gained `stun` (a pre-v41 pickled Character lacks it), and
# abstract_combat.CombatState lost its own `player_stun` field entirely -- _stun_player
# now reads/writes Character.stun directly, so it carries across fights and is
# only cleared by Character.mark_rested (a full clear, unlike fatigue's halving).
# v42 gated recruiting a RivalRunner on having met them: Character gained
# `known_runners` (a pre-v42 pickled Character lacks it), granted by
# shop_screens.BarScreen when the player runs into that runner drinking at a bar.
# v43 made a run's independent-runner roster random: ShadowguyApp gained `runners`
# (the guaranteed RIVAL_RUNNERS plus that run's random pick from the new
# runners.RUNNER_POOL, picked once at game start by runners.select_active_runners) --
# a pre-v43 save lacks the key entirely.
# v44 added the crew's own casualties: Character gained `dead_runners` and
# `arrested_runners` (a pre-v44 pickled Character has neither), written by
# tactical.resolve_downed_crew when a hire who went down doesn't walk away from the
# fight, and read through Character.runner_available -- which now joins known_runners
# in deciding whether a runner appears at a bar or takes a rival's daily turn at all.
# v45 tagged a burglary with the kind of building it is: scene.BurglaryStage gained
# `kind` (tactical.BuildingKind) and tactical.BuildingLayout the same -- a pre-v45
# pickled BurglaryStage (inside an accepted Burglary job) lacks it. The building is
# still job-scoped and never a corpmap.Location, so no map state changed.
# v46 rebuilt Burglary around multi-level buildings played as tactical fights:
# scene.BurglaryStage now holds a buildings.Building (levels, links, rooms) plus a
# `guard` Enemy template and a `bailed` Outcome, in place of the old flat
# grid/objective/guards/spotted fields, and scene.Entrance.spawn became (level, cell).
# A pre-v46 pickled BurglaryStage (inside an accepted Burglary job) has the old shape.
# v47 added the OFFICE BuildingKind. Nothing pickled changed *shape* -- a v46 Building is
# structurally a v47 Building -- but a pre-v47 run's accepted Burglary jobs were all
# generated when residential was the only structure and when entrances were placed by the
# level-index rule that put an office's spawns a floor up. Rather than leave those runs
# holding buildings the current generator would never produce, they're refused at load.
# v48 added locked doors and cameras: buildings.Level gained `doors`, buildings.Building
# gained `locks`/`cameras`. A pre-v48 pickled Level/Building (inside an accepted Burglary
# or Wetwork job) lacks all three fields.
# v49 added per-archetype job roster caps: character.CrewHire gained `on_site`, and
# scene.Scene gained `max_on_site`/`max_support` (set from jobs.JobArchetype at
# generation). A pre-v49 pickled CrewHire or Scene (inside accepted_jobs or Character.crew)
# lacks all three fields.
# v50 is the one bump with no new state at all: the grid primitives moved out of
# tactical.py into a new grid.py, and pickle resolves a class by its module path (see
# this module's own docstring). Every pre-v50 save holds `shadowguy.tactical.Grid`
# instances -- reachable from a pickled scene.TacticalStage and from every
# buildings.Level inside a Burglary target -- which no longer resolves.
# v51 renamed the core stat `intelligence` to `logic`: Character's field, the
# CORE_STATS entry, every skills._SKILL_ROWS key, and every bonuses/temp_bonuses key
# that named it. A pre-v51 pickled Character carries `intelligence` and no `logic`, so
# every stat() read of it would raise.
# v52 split the weapon skills by category: short_blade/long_blade/blunt/firearms/misc
# are gone, replaced by pistols/automatics/longarms/clubs/blades/archery/throwing/
# gunnery (33 skills, up from 30). A pre-v52 pickled Character's skill_ranks is keyed
# by the old ids -- every new skill would read as missing, and the ranks bought for the
# old ones would be unspendable.
# v53 added six logic skills (cybercombat, computer, armorer, chemistry, medicine,
# demolitions -- 39 total) and, with the first two, split what the matrix rolls three
# ways: the Attack roll moved off `hack` to `cybercombat`, Extract and remote node
# analysis to `computer`. Character.skill_ranks tolerates the missing keys (skill_rank
# defaults), but a pre-v53 runner's Hack rank silently stops applying to matrix fights
# -- a live character would be markedly worse at the thing they built for.
# v54 rewrote combat.Enemy onto the player's own stat sheet. It was seven hand-tuned
# fields (health/attack/defense/damage/toughness/reach/stun_damage); it is now the six
# CORE_STATS plus `ranks`/`weapon`/`armor`, with all seven of those derived as
# properties through the same skill_value/melee_damage_bonus/DEFENSE_BASE the player
# goes through. A pre-v54 pickled Enemy -- reachable from an accepted job's
# scene.Encounter/TacticalStage and from scene.BurglaryStage.guard -- carries the old
# fields as *instance attributes that now shadow the properties*, so it would keep
# fighting on its old numbers with no stats behind it and no error to say so. The
# roster also grew from 5 to 11 and ENEMY_TIERS was re-pooled, so a pre-v54 run's
# accepted jobs hold squads the current generator would never roll.
# v55 gave runners.RivalRunner a `deck_id` and moved the remote-support gate onto it:
# whether a hire can work support is now "do they own a cyberdeck", not "is their
# archetype Netrunner", and the deck's program_slots caps how many support programs they
# carry. RivalRunner instances are pickled directly (ShadowguyApp.runners, save v43), so
# a pre-v55 roster has objects with no `deck_id` attribute at all -- every support
# lookup on one would raise rather than read as "no deck".
# v56 added creation gear: Character gained `gear_budget` and `creation_gear`
# (convert_skill_point_to_gear trades a creation skill point for GEAR_EB_PER_POINT of
# gear-only eb, and archetypes.Archetype gained a `gear` loadout spent the same way). A
# pre-v56 pickled Character lacks both fields, so reset_build would raise on the first
# one it touches -- and every preset's rank list changed to free the point its kit costs.
SAVE_VERSION = 58
# The run fields a bundle must carry (app.ShadowguyApp writes and reads exactly these).
# Checked at load so a payload that unpickles but isn't a whole run is rejected here,
# at the boundary, rather than half-applied to the live App by the caller.
STATE_KEYS = frozenset(
    {
        "rng",
        "corp_map",
        "character",
        "fixers",
        "runners",
        "location_gigs",
        "rival_actions",
        "rival_runner_states",
        "rival_researched",
        "faction_events",
        "corp_state",
        "corp_only",
    }
)


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
