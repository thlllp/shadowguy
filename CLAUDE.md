# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Project: Shadowguy

A text-based cyberpunk roguelite TUI. Python 3.14, managed with `uv`, built on Textual.

### Core concept

Two coupled game modes, not one game with a reskinned second mode:

- **Runner mode** — RPG scale. One character, stats, scene-based missions, permadeath.
- **Corp mode** — 4X scale. Player controls a corp, area-control/resource game against rivals. Take over one of the 4 seeded Factions, collect territory income and research, spend one directed move a day (expand onto neutral ground, train employees at your Academy, upgrade your Research Facility, garrison a district, or attack a rival's), and spend research points on `corp_turn.TECHNOLOGIES`. Corps fight each other over territory — operatives vs. garrison + Security, see Corp conflict in `DESIGN.md` — and a corp that loses its last district ends the run.

Switching between runner and corp is optional and meant to be difficult — a runner earns a corp by buying a controlling stake at that corp's own HQ (`CorpHQScreen`, gated on rep + standing + 15,000eb), not by picking one off a menu. Neither mode is a straight upgrade over the other. **A run can also start as either one** (New Game → Runner / Corp): a Corp game never builds a runner at all (`ShadowguyApp.corp_only`), so it isn't "runner mode plus a corp screen" — it's the 4X half on its own.

### Run/game-over rules

- No meta-progression between runs (for now). Each run starts fresh.
- Runner mode ends when the character dies.
- Corp mode ends when the corp is destroyed, taken over, or the character is assassinated.

### Game systems — see `DESIGN.md`

The design rationale for every game system — stats and skills, character
creation and experience, check resolution, jobs/gigs/legwork, the day clock,
fixers, all four fight surfaces (abstract, tactical, burglary, matrix),
standings, the corp turn loop, the map, shops and cyberware — lives in
**`DESIGN.md`**.

**Read it before changing game behavior.** The constants in those systems are
frequently load-bearing, several were set against a balance simulation, and the
sections flag which ones are safe to touch.

### Codebase layout

Orientation only — each module's own section in `DESIGN.md` carries the detail.

```
src/shadowguy/
  character.py   the run's whole mutable state: stats, health, stun, fatigue, humanity,
                 skill ranks, experience, every standing, crew, inventory, accepted work
  archetypes.py  Enforcer/Hacker/Infiltrator/Gunslinger/Fixer creation presets
  checks.py      resolve_check() — the one place any check resolves
  skills.py      the 39-skill table, skill_value(), skill_for()
  scene.py       Scene/Stage/Choice/Outcome + the four fight/stage wrappers; apply_outcome()

  combat.py      what a fight is made of, shared by all three surfaces: enemy roster,
                 resolve_hit, player defense/soak, weapon+consumable queries, the Drop
  abstract_combat.py  fight surface 1: abstract rounds, no positions
  grid.py        space itself: Grid/Tile, tcod FOV+A*, distance -- no game state
  tactical.py    fight surface 2: the grid fight, plus the BSP generate_map
  matrix.py      fight surface 3: ICE, node networks, integrity pool, cyberdeck programs

  jobs.py        job generation (9 archetypes) + JobTiming + per-job legwork + SmugglingJob
  gigs.py        per-Location gig generation
  fixer.py       the Fixer roster holding job and security offers
  runners.py     the hireable-runner roster + the remote-support programs a hire runs

  factions.py    corp Factions + the HQ officer ladder
  gangs.py       street Gangs + GANG_RANKS
  relations.py   seeded Faction<->Gang standing
  corpmap.py     the territory map model, its Locations/LocalCharacters, the modifier
                 levers, and the ASCII renderer -- what every other system reads
  corpmap_gen.py generate_corp_map: laying one new map out, run once per run
  buildings.py   burglary targets: rooms, levels and the links between them. Job-scoped
                 and never a corpmap.Location — see Burglary in DESIGN.md

  security.py    parallel resolution: multi-night security contracts
  encounters.py  parallel resolution: gang turf-entry toll-or-attack
  rivals.py      parallel resolution: faction expansion, garrison upkeep and attacks on
                 rival corps + a flavor-only rival research roll + the NPC runners' daily
                 activity turn (one of which takes a job off a fixer's board), once a day
  surveillance.py parallel resolution: detection rolls in the player corp's territory
  corp_turn.py   the player's own Corp turn — CorpState, income/research, the daily action,
                 and the corp-vs-corp contest (resolve_attack) both sides settle through

  shops.py       the retail catalogs (items, consumables, programs) + pricing +
                 buy/sell transactions
  inventory.py   equip state, deck programs and using a consumable -- what happens to
                 a Character's inventory *after* shops.py adds something to it
  cybernetics.py the Cyberware catalog + install/remove; no shop wired to it yet
  saves.py       pickle-based whole-run save/load
  app.py         ShadowguyApp itself: spend_time/_apply_day_tick, save/load; no screens

  screens/
    __init__.py          shared UI: CharacterSheet (the always-visible runner panel),
                         BackScreen, PanelNav, the tactical/burglary map glyph helpers
    creation_screen.py   CharacterCreationScreen + GearScreen (creation gear)
    menu_screens.py      TitleMenu (entry point) + ModeSelect + BuildSelect + ArchetypeSelect
                         + CorpSelect + Test + Quit + Load
    scene_screen.py      SceneScreen
    combat_screen.py     CombatScreen (the abstract_combat surface)
    tactical_screen.py   TacticalScreen + GrenadePickScreen + HackerPickScreen (the
                         remote-support menu)
    matrix_screen.py     MatrixScreen
    burglary_screens.py  EntrancePick (the interior itself plays on TacticalScreen)
    corp_map_screen.py   CorpMapScreen + GangTollScreen -- the home screen for both a
                         runner and a corp-only run; no separate MainMenu any more
    corp_screen.py       CorpScreen + ResearchTreeScreen + ForcePickScreen
    shop_screens.py      FixerOffers + Shop + Bar + CorpHQ + Hospital + RealEstate +
                         Safehouse + Junkyard + GangDen
    info_screens.py      Phone (home grid) + its apps: Contacts + Web + CorpWebsite +
                         AlarmClock + Messages; plus Inventory + Cyberdeck + Skills
```

The four **parallel resolution** modules are a deliberate category: day-advance pipelines that resolve outside the `Scene` model entirely, because nothing in `scene.py` is day-aware.

### Module layering

`scene.py` owns *what an outcome is worth* (`Outcome`, and the `Encounter`/`TacticalStage`/`BurglaryStage`/`MatrixStage` wrappers that hold them). The engines own *how a fight resolves* and **must never import `scene`** — that split is why `Encounter` lives in `scene.py` rather than beside the code that runs it.

Leaf modules, and why each has to stay one:

- **`skills.py`** — imports nothing from the package; `character.py → shops.py → corpmap.py` all import it. The "every `Skill.stat` is a real core stat" guard therefore lives in `character.py`, the one module seeing both tables. A runtime `character` import here is a cycle.
- **`combat.py`** — the shared fight foundation, no `scene`. All three fight surfaces (`abstract_combat.py` / `tactical.py` / `matrix.py`) import *it* and never each other; `soak_damage` is public (not `_soak_damage`) because `abstract_combat`'s flee resolves a parting shot through it. `Enemy` stays here rather than moving to `abstract_combat.py` — a pickled `scene.Encounter` holds one, so the move would cost a save version for nothing.
- **`abstract_combat.py` / `tactical.py` / `matrix.py`** — the three fight surfaces, no `scene`.
- **`grid.py`** — imports nothing from the package: `Grid`/`Tile` and the FOV/A*/distance functions over them, with no units, turns or game state. Both `buildings.py` and `tactical.py` import it, which is what lets the arrow run `grid → buildings → tactical` in one direction.
- **`corpmap.py`** — no `scene`, which is why gigs live on `app.location_gigs` rather than on `Location`. `corpmap_gen.py` imports it and it never imports back; the modifier cluster (`make_modifiers` and friends) stays here rather than moving to the generator because `claim_territory` reseeds a district at runtime through it.
- **`buildings.py`** — imports `grid` for the geometry and nothing else from the package; `scene`/`jobs`/`tactical` import *it*. `tactical.py` imports `Building`/`Lock` at runtime, no `TYPE_CHECKING` dance: extracting `grid.py` is what removed the cycle that used to need one.
- **`corp_turn.py`** — imports `corpmap` only, never `scene`/`app`. `Sighting` lives here rather than in `surveillance.py` to avoid a corp_turn↔surveillance cycle. `resolve_attack` deliberately takes a bare `Territory` rather than a `CorpState`, which is what lets `rivals.py`'s AI factions (which have no `CorpState`) settle an attack through the same dice the player does.
- **`relations.py`** — imports only `factions.py`/`gangs.py`. Read by `rivals._pick_attack_target`; nothing writes to it after generation.
- **`gangs.py`** — turf placement and den staffing live in `corpmap.py` instead.
- **`saves.py`** — imports no game classes.
- **`shops.py` / `inventory.py`** — `inventory.py` imports `shops.py` (for `Item`/`Program`/the catalog registries and `fits_in_slot`) and `shops.py` never imports `inventory.py` back; `buy_item`'s auto-equip check is why `fits_in_slot`/`slot_usage` stay in `shops.py` rather than moving over with the rest of the equip-state functions.

`scene.py` itself needn't import `jobs`: `Role` is plain data (strings + `Posture`, not `jobs.StageType`). It *does* import `corpmap` (for `Outcome.security_delta`'s target) — a legal edge, since corpmap's own closure is `factions`/`gangs`/`relations`/`skills` and it never imports `scene` back.

### Save versions

`saves.SAVE_VERSION` is the coarse guard on pickled runs: bump it on any breaking state change. What each bump added:

| v | Change |
|---|---|
| 16 | `Character.gang_standing` |
| 19 | `rival_actions` (part of the save bundle) |
| 20 | `corp_state` |
| 21 | `research_tier`, `research_points` |
| 22 | `academy_tier`, `daily_action_used` rename, a since-replaced `employees` field |
| 23 | `employees` split into `scientists`/`operatives` |
| 24 | `labs_built` |
| 25 | `efficiency_upgrades` |
| 26 | `research_assistants`, float `research_points` |
| 27 | `corp_only` |
| 28 | `CorpMap.relations` |
| 29 | `elapsed_hours` **replacing** `day`/`stamina`/`free_travel_used` |
| 30 | `CorpState.researched` |
| 31 | `CorpState.sightings`, `ShadowguyApp.rival_runner_locations` |
| 32 | `Character.experience`/`crew_experience`, `Outcome.experience_delta` |
| 33 | `Character.installed_cyberware` |
| 34 | `Character.humanity` |
| 35 | `CorpState.pending_recruit` (Academy training takes days) |
| 36 | `Character.last_rest_hour`, `Character.fatigue` (Rest decoupled from the midnight tick) |
| 37 | `Character.smuggling_job` (gang delivery jobs) |
| 38 | `rival_runner_states` **replacing** `rival_runner_locations`, `JobOffer.taken_by` |
| 39 | `Character.alarm_hour` (the Phone's Alarm Clock) |
| 40 | `rival_researched`, `faction_events` (every corp's public website) |
| 41 | `Character.stun` **replacing** `CombatState.player_stun` (stun now carries between fights) |
| 42 | `Character.known_runners` (recruiting gated on having met them) |
| 43 | `ShadowguyApp.runners` (a run's random independent-runner roster) |
| 44 | `Character.dead_runners`/`arrested_runners` (a hire who goes down on a job) |
| 45 | `BurglaryStage.kind` (the building tag) |
| 46 | `BurglaryStage.building`/`guard`/`bailed` **replacing** its flat grid/objective/guards/spotted; `Entrance.spawn` is now (level, cell) |
| 47 | the `OFFICE` `BuildingKind` — no shape change, but pre-v47 burglary targets are all residential and were placed by the old ground-floor rule |
| 48 | `buildings.Level.doors`, `buildings.Building.locks`/`cameras` (locked doors and camera hazards) |
| 49 | `Character.CrewHire.on_site`, `Scene.max_on_site`/`max_support` (per-archetype job roster caps) |
| 50 | no new state — `Grid`/`Tile` moved from `tactical.py` to a new `grid.py`, and pickle resolves a class by module path (a `Grid` hangs off `scene.TacticalStage` and every `buildings.Level`) |
| 51 | the core stat `intelligence` **renamed** to `logic` (`Character` field, `CORE_STATS`, every `_SKILL_ROWS`/`bonuses` key) |
| 52 | the weapon skills **split by category**: `short_blade`/`long_blade`/`blunt`/`firearms`/`misc` replaced by `pistols`/`automatics`/`longarms`/`clubs`/`blades`/`archery`/`throwing`/`gunnery` (33 skills, up from 30), so a pre-v52 `Character.skill_ranks` is keyed by ids that no longer exist |
| 53 | six logic skills added — `cybercombat`/`computer`/`armorer`/`chemistry`/`medicine`/`demolitions` (39 total) — and the matrix's rolls split three ways with them: ATTACK moved `hack`→`cybercombat`, Extract and node analysis → `computer` |
| 56 | `Character.gear_budget`/`creation_gear` — creation skill points convert to gear-only eb (`GEAR_EB_PER_POINT`), `archetypes.Archetype.gear` ships a loadout, and every preset's rank list changed to free the point it costs |
| 55 | `runners.RivalRunner.deck_id` — remote support is gated on **owning a cyberdeck**, not on the `Netrunner` archetype, and the deck's `program_slots` caps how many support programs a hire carries. `RivalRunner` instances are pickled directly (`ShadowguyApp.runners`), so a pre-v55 roster lacks the attribute entirely |
| 54 | `combat.Enemy` rewritten onto the player's stat sheet: the six `CORE_STATS` + `ranks`/`weapon`/`armor` **replacing** the hand-set `health`/`attack`/`defense`/`damage`/`toughness`/`reach`/`stun_damage`, which are now derived properties. A pre-v54 pickled `Enemy` (reachable from an accepted job's `Encounter`/`TacticalStage` and from `BurglaryStage.guard`) carries the old fields as instance attributes that **shadow the properties**. Roster also grew 5 → 11 and `ENEMY_TIERS` was re-pooled |
| 57 | the workshop system: `corpmap.Location.workshop_built` (free on the injected apartment, built at a safehouse for `WORKSHOP_BUILD_COST`) and `shops.InventoryItem.mods` (per-instance mod ids, folded into combat/inventory stats by `shops.effective_item`) |
| 58 | the corp conflict layer: `corpmap.Territory.garrison`, `scene.Outcome.security_delta`, and `corp_turn.FactionEvent.from_faction_id` + its new `"seizure"` kind. `rivals.RivalAction.attack` holds a `corp_turn.AttackResult`, and `apply_outcome`/`resolve_choice`/`resolve_entrance` all take a `CorpMap` now |

### Verifying changes

A real test suite exists (`tests/`, 25 test files plus `conftest.py`/`helpers.py`, `pytest>=8` in `pyproject.toml`'s `dev` dependency group), run by CI (`.github/workflows/tests.yml`, every push/PR to `main`): `uv run pytest -q` runs it, `uv run ruff check src/` lints (ruff is pinned in the `dev` group so CI and local agree — an unpinned `uvx ruff` drifts to whatever's newest). Guideline §4 still applies; established conventions:

- **Model/generator changes** — a `pytest.mark.parametrize("seed", SEEDS)` test (`SEEDS = range(150)` is the norm; `test_corpmap_gen.py` widens to `range(200)`, `test_buildings.py`/`test_tactical.py` narrow to `range(80)`) over a module-scoped fixture, asserting invariants rather than exact values. This caught a real bug once: `_plan_injections` comparing a `Cell` tuple against a `str` id (always `True`, so the start territory's hospital/gang-den exclusion silently did nothing) — invisible without a wide seed sweep.
- **Forcing an exact `CheckResult` branch** — a `random.Random` subclass whose `randint` always returns a fixed face, pinning a roll to `CRITICAL_SUCCESS`/`CRITICAL_FAILURE`/etc. deterministically. Now shared from **`tests/helpers.py`** (`AlwaysSix`/`AlwaysOne`, `ForcedChance` for a call-counted mix, `character_with_skill_value`) rather than re-derived per file — import it as a top-level module (`from helpers import ForcedChance`), the way `test_matrix.py`/`test_shops.py`/`test_rivals.py` do. The module-scoped `corp_map` fixture lives in **`tests/conftest.py`** and is shared by the eight suites that need a real map.
- **UI changes** — Textual's `async with app.run_test() as pilot:` drives the real app headlessly (`tests/test_app_flows.py`); `pilot.press(...)`/`pilot.hover(...)`/`pilot.click(...)` exercise real screens. Prefer this over asserting on internals.
- Anything asserting on a **check outcome** without one of the above tricks must seed the module-level `random` (see Check resolution in `DESIGN.md`) or it will be flaky.

### Known Textual gotchas hit so far

- `ListView.clear()` returns an `AwaitRemove` — must be awaited, or a following `.append()` can race the removal and raise `DuplicateIds`. Handlers that clear-then-repopulate must be `async def`.
- `ListView.index` becomes `None` after `clear()`; set it explicitly (e.g. `.index = 0`) after repopulating, or keyboard selection (`enter`) has nothing highlighted to act on.
- `Static` renders its string as Rich markup by default — literal square brackets (e.g. `"[Legwork]"`) get parsed as markup tags and silently vanish. **Way out:** pass `Static.update()` a Rich `Text` object instead of a `str` — markup is never parsed, and you can still colour ranges via `Text.stylize(style, start, end)`.
- `Screen`'s resume hook is the public `on_screen_resume` (a separate private `_on_screen_resume` exists for internal bookkeeping) — override the public one to refresh a screen's content when popped back to.
- Mouse hit-testing on a text blob: handle `on_mouse_move` and call `event.get_content_offset(widget)`, returning an `Offset` inside the widget's content or `None` when the pointer is outside it — `None` is the signal to clear hover state. Mouse events bubble to the `Screen`, so the handler fires for the whole screen.
- `Static` has **no** `.renderable` attribute in Textual 8 (it did in older versions); current content is `.content`. Only matters when asserting on widget contents in tests.
- **A layout-affecting mutation needs two pauses before a coordinate click or hover, not one.** `click()`/`hover()` read the target's `.region` once up front, then pause between the events they post — and `Pilot.pause()` *ends* by calling `screen._on_timer_update()`, which is what runs a pending layout. A single pause after the mutation can still leave the click aimed pre-layout, delivered post-layout. First found on cold boot: `run_test()` hands back a pilot before the first layout has run, which moved `TitleMenu`'s `#new_game` between y=18 and y=17 and opened Load Game instead of New Game in ~2% of runs. Recurred on an already-mounted screen expanding a `Collapsible` and calling `scroll_end()` back to back before clicking the revealed row — one pause per mutation wasn't enough there either, and it took down two different tests in CI before the pattern was traced (`test_shop_screen_buy_flow_spends_cash_and_adds_inventory`, `test_buy_deck_and_program_then_install_via_cyberdeck_screen`). **Way out:** `tests/test_app_flows.py`'s `_settle(pilot)` (two pauses) after *any* layout-affecting mutation — boot, expand/collapse, scroll — that precedes a coordinate-based click or hover. A plain `push_screen()` mid-test still settles inside one pause (measured 150/150 stable); it's specifically expand/scroll-then-click sequences that need the second pause too.
- **A frequent `app.notify()` toast makes `tests/test_app_flows.py` flaky.** Toasts render in an overlay, and `pilot.click(selector)` clicks *screen coordinates* — a live toast can sit on top of the target and swallow the click, so unrelated tests start failing intermittently in different places each run. A day-tick notification that fires on most days is enough to do it. Gate frequent notifications on something the player actually has (e.g. `Character.discovered_fixers`) rather than firing them unconditionally.
