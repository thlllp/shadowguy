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
- **Corp mode** — 4X scale. Player controls a corp, area-control/resource game against rivals. A first-slice turn loop exists (`shadowguy/corp_turn.py`): take over one of the 3 seeded Factions, collect territory income and research, spend one directed move a day (expand onto neutral ground, train employees at your Academy, or upgrade your Research Facility's labs/efficiency). No corp-vs-corp conflict yet — see Corp mode turn loop in `DESIGN.md`.

Switching between runner and corp is optional and meant to be difficult — neither mode is a straight upgrade over the other. **A run can also start as either one** (New Game → Runner / Corp): a Corp game never builds a runner at all (`ShadowguyApp.corp_only`), so it isn't "runner mode plus a corp screen" — it's the 4X half on its own.

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
  character.py   the run's whole mutable state: stats, health, humanity, skill ranks,
                 experience, every standing, crew, inventory, accepted work
  archetypes.py  Enforcer/Hacker/Infiltrator creation presets
  checks.py      resolve_check() — the one place any check resolves
  skills.py      the 32-skill table, skill_value(), skill_for()
  scene.py       Scene/Stage/Choice/Outcome + the four fight/stage wrappers; apply_outcome()

  combat.py      fight surface 1: abstract rounds, enemy roster, shared resolve_hit
  tactical.py    fight surface 2: grid (tcod FOV+A*); also generate_building for Burglary walks
  matrix.py      fight surface 3: ICE, node networks, integrity pool, cyberdeck programs

  jobs.py        job generation + JobTiming + per-job legwork
  gigs.py        per-Location gig generation
  fixer.py       the Fixer roster holding job and security offers
  runners.py     the hireable-runner roster

  factions.py    corp Factions + the HQ officer ladder
  gangs.py       street Gangs + GANG_RANKS
  relations.py   seeded Faction<->Gang standing
  corpmap.py     the territory map, its Locations/LocalCharacters, and the ASCII renderer

  security.py    parallel resolution: multi-night security contracts
  encounters.py  parallel resolution: gang turf-entry toll-or-attack
  rivals.py      parallel resolution: faction expansion + runner wander, once a day
  surveillance.py parallel resolution: detection rolls in the player corp's territory
  corp_turn.py   the player's own Corp turn — CorpState, income/research, the daily action

  shops.py       the retail catalogs (items, consumables, programs) + pricing
  cybernetics.py the Cyberware catalog + install/remove; no shop wired to it yet
  saves.py       pickle-based whole-run save/load
  app.py         ShadowguyApp itself: spend_time/_apply_day_tick, save/load; no screens

  screens/
    creation_screen.py   CharacterCreationScreen
    main_menu.py         MainMenu
    menu_screens.py      TitleMenu (entry point) + ModeSelect + CorpSelect + Test + Quit + Load
    scene_screen.py      SceneScreen
    combat_screen.py     CombatScreen
    tactical_screen.py   TacticalScreen
    matrix_screen.py     MatrixScreen
    burglary_screens.py  EntrancePick + BurglaryWalk
    corp_map_screen.py   CorpMapScreen + GangTollScreen
    corp_screen.py       CorpScreen + CorpMainMenu (subclasses it) + ResearchTreeScreen
    shop_screens.py      FixerOffers + Shop + Bar + CorpHQ + Hospital + RealEstate + Safehouse
    info_screens.py      Contacts + Inventory + Cyberdeck + Skills
```

The four **parallel resolution** modules are a deliberate category: day-advance pipelines that resolve outside the `Scene` model entirely, because nothing in `scene.py` is day-aware.

### Module layering

`scene.py` owns *what an outcome is worth* (`Outcome`, and the `Encounter`/`TacticalStage`/`BurglaryStage`/`MatrixStage` wrappers that hold them). The engines own *how a fight resolves* and **must never import `scene`** — that split is why `Encounter` lives in `scene.py` rather than beside the code that runs it.

Leaf modules, and why each has to stay one:

- **`skills.py`** — imports nothing from the package; `character.py → shops.py → corpmap.py` all import it. The "every `Skill.stat` is a real core stat" guard therefore lives in `character.py`, the one module seeing both tables. A runtime `character` import here is a cycle.
- **`combat.py` / `tactical.py` / `matrix.py`** — the three fight surfaces, no `scene`.
- **`corpmap.py`** — no `scene`, which is why gigs live on `app.location_gigs` rather than on `Location`.
- **`corp_turn.py`** — imports `corpmap` only, never `scene`/`app`. `Sighting` lives here rather than in `surveillance.py` to avoid a corp_turn↔surveillance cycle.
- **`relations.py`** — imports only `factions.py`/`gangs.py`.
- **`gangs.py`** — turf placement and den staffing live in `corpmap.py` instead.
- **`saves.py`** — imports no game classes.

`scene.py` itself needn't import `jobs`: `Role` is plain data (strings + `Posture`, not `jobs.StageType`).

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

### Verifying changes

A real test suite exists (`tests/`, 19 files, `pytest>=8` in `pyproject.toml`'s `dev` dependency group), run by CI (`.github/workflows/tests.yml`, every push/PR to `master`): `uv run pytest -q` runs it, `uv run ruff check src/` lints (ruff is pinned in the `dev` group so CI and local agree — an unpinned `uvx ruff` drifts to whatever's newest). Guideline §4 still applies; established conventions:

- **Model/generator changes** — a `pytest.mark.parametrize("seed", SEEDS)` test (`SEEDS = range(150)` is the norm; `test_corpmap.py` widens to `range(200)`, `test_burglary_gen.py`/`test_tactical.py` narrow to `range(80)`) over a module-scoped fixture, asserting invariants rather than exact values. This caught a real bug once: `_plan_injections` comparing a `Cell` tuple against a `str` id (always `True`, so the start territory's hospital/gang-den exclusion silently did nothing) — invisible without a wide seed sweep.
- **Forcing an exact `CheckResult` branch** — `tests/test_checks.py`'s pattern: a `random.Random` subclass whose `randint` always returns a fixed face (`AlwaysSix`/`AlwaysOne`) or a call-counted mix, pinning a roll to `CRITICAL_SUCCESS`/`CRITICAL_FAILURE`/etc. deterministically. Reused in `tests/test_security.py`.
- **UI changes** — Textual's `async with app.run_test() as pilot:` drives the real app headlessly (`tests/test_app_flows.py`); `pilot.press(...)`/`pilot.hover(...)`/`pilot.click(...)` exercise real screens. Prefer this over asserting on internals.
- Anything asserting on a **check outcome** without one of the above tricks must seed the module-level `random` (see Check resolution in `DESIGN.md`) or it will be flaky.

### Known Textual gotchas hit so far

- `ListView.clear()` returns an `AwaitRemove` — must be awaited, or a following `.append()` can race the removal and raise `DuplicateIds`. Handlers that clear-then-repopulate must be `async def`.
- `ListView.index` becomes `None` after `clear()`; set it explicitly (e.g. `.index = 0`) after repopulating, or keyboard selection (`enter`) has nothing highlighted to act on.
- `Static` renders its string as Rich markup by default — literal square brackets (e.g. `"[Legwork]"`) get parsed as markup tags and silently vanish. **Way out:** pass `Static.update()` a Rich `Text` object instead of a `str` — markup is never parsed, and you can still colour ranges via `Text.stylize(style, start, end)`.
- `Screen`'s resume hook is the public `on_screen_resume` (a separate private `_on_screen_resume` exists for internal bookkeeping) — override the public one to refresh a screen's content when popped back to.
- Mouse hit-testing on a text blob: handle `on_mouse_move` and call `event.get_content_offset(widget)`, returning an `Offset` inside the widget's content or `None` when the pointer is outside it — `None` is the signal to clear hover state. Mouse events bubble to the `Screen`, so the handler fires for the whole screen.
- `Static` has **no** `.renderable` attribute in Textual 8 (it did in older versions); current content is `.content`. Only matters when asserting on widget contents in tests.
