# AGENTS.md

Behavioural rules + project reference for LLM coding assistants. Design rationale lives in `DESIGN.md` — read it before changing game behaviour. This file is the condensed map; `CLAUDE.md` carries the same material at length.

## Behavioural rules

1. **State assumptions, ask when uncertain.** Present multiple interpretations, don't pick silently.
2. **Minimum code.** No unasked features, no single-use abstractions, no speculative error handling.
3. **Surgical edits.** Match existing style, don't refactor neighbours, only clean up orphans YOUR changes create.
4. **Goal-driven.** Transform tasks into verifiable checkpoints. Loop until verified.

## Project

Text-based cyberpunk roguelite TUI. Python 3.14, `uv`, Textual.

Two coupled modes: **Runner** (RPG, one character, permadeath) + **Corp** (4X, territory/resources). Switching is earned in-game, not menu-picked. A run starts as either one (New Game → Runner / Corp); a Corp game never builds a runner (`ShadowguyApp.corp_only`).

### Run rules
- No meta-progression. Each run fresh.
- Runner ends on death. **Corp ends when it holds no territory** (`corp_turn.corp_defeated`, checked on the day tick) — in a hybrid runner+corp run too, not just `corp_only`.

## Key commands

```
uv run pytest -q              # test suite (26 test files, pytest>=8)
uv run ruff check src/        # lint (ruff pinned in dev group)
```

## Module map

### Core engine
| Module | Role | Must never import |
|---|---|---|
| `skills.py` | 39-skill table, `skill_value()`, `skill_for()` | nothing from package |
| `character.py` | All mutable state: stats, health, stun, fatigue, humanity, skills, standings, crew, inventory | — |
| `checks.py` | `resolve_check()` — single chokepoint for every roll | — |
| `archetypes.py` | 5 creation presets (Enforcer/Hacker/Infiltrator/Gunslinger/Fixer), each shipping a gear loadout | — |
| `scene.py` | `Scene`/`Stage`/`Choice`/`Outcome` + fight wrappers; `apply_outcome()` | `jobs` |
| `combat.py` | Shared fight foundation: enemy roster (11, 3 tiers), `resolve_hit`, soak, weapon reach | `scene` |
| `abstract_combat.py` | Fight surface 1: abstract rounds, no positions | `scene` |
| `grid.py` | `Grid`/`Tile`, tcod FOV + A\*, distance — space itself, no game state | nothing from package |
| `tactical.py` | Fight surface 2: grid fight + BSP `generate_map` | `scene` |
| `matrix.py` | Fight surface 3: ICE, node networks, integrity, deck programs | `scene` |
| `buildings.py` | Burglary targets: rooms, levels, links, locks, cameras | only `grid` |
| `jobs.py` | Job generation (9 archetypes) + `JobTiming` + legwork + `SmugglingJob` | — |
| `gigs.py` | Per-Location gig generation | — |
| `fixer.py` | Fixer roster + job/security offers | — |
| `runners.py` | Hireable-runner roster (3 guaranteed + 6 of a 9-strong pool) + remote-support programs | — |
| `factions.py` | Corp Factions + HQ officer ladder | — |
| `gangs.py` | Street Gangs + `GANG_RANKS` | — |
| `relations.py` | Seeded corp↔gang standing; **read by `rivals._pick_attack_target`** | `factions`, `gangs` only |
| `corpmap.py` | Territory map, Locations, `LocalCharacter`s, modifier levers, ASCII renderer | `scene` |
| `corpmap_gen.py` | `generate_corp_map` — run once per run | — |
| `corp_turn.py` | Corp mode: `CorpState`, income/research, daily action, `TECHNOLOGIES` (6), the corp-vs-corp contest | `corpmap` only |
| `security.py` | Parallel: multi-night security contracts | — |
| `encounters.py` | Parallel: gang turf-entry toll-or-attack | — |
| `rivals.py` | Parallel: faction expansion / garrison upkeep / attacks + NPC runner daily turn | — |
| `surveillance.py` | Parallel: detection rolls in player corp's territory | — |
| `shops.py` | Retail catalogs, pricing, programs, mods | — |
| `inventory.py` | Equip state, deck programs, using a consumable | imports `shops`, never the reverse |
| `cybernetics.py` | Cyberware catalog (48 = 12 rows × 4 tiers) + install/remove, bought at a `CYBER_CLINIC` | nothing at runtime |
| `saves.py` | Pickle save/load | no game classes |
| `app.py` | `ShadowguyApp`: `spend_time`/`_apply_day_tick`, save/load; no screens | — |

### Screens (`screens/`)
| File | Contents |
|---|---|
| `__init__.py` | `CharacterSheet`, `BackScreen`, `PanelNav`, map glyph helpers |
| `creation_screen.py` | `CharacterCreationScreen` + `GearScreen` |
| `menu_screens.py` | `TitleMenu`, `ModeSelect`, `BuildSelect`, `ArchetypeSelect`, `CorpSelect`, `Test`, `Quit`, `Load` |
| `scene_screen.py` | `SceneScreen` |
| `combat_screen.py` | `CombatScreen` (the `abstract_combat` surface) |
| `tactical_screen.py` | `TacticalScreen` + `GrenadePickScreen` + `HackerPickScreen` |
| `matrix_screen.py` | `MatrixScreen` |
| `burglary_screens.py` | `EntrancePickScreen` (the interior itself plays on `TacticalScreen`) |
| `corp_map_screen.py` | `CorpMapScreen` (home screen for both runner and corp-only runs) + `GangTollScreen` |
| `corp_screen.py` | `CorpScreen` + `ResearchTreeScreen` + `ForcePickScreen` + `OperationsMixin` |
| `shop_screens.py` | `FixerOffers`, `Shop`, `Bar`, `CorpHQ`, `Hospital`, `RealEstate`, `Safehouse`, `Junkyard`, `GangDen`, `Ripperdoc` |
| `info_screens.py` | `Phone` + apps (Contacts, Web, CorpWebsite, AlarmClock, Messages); `Inventory`, `Cyberdeck`, `Skills` |

### Parallel resolution modules
`security.py`, `encounters.py`, `rivals.py`, `surveillance.py` — day-advance pipelines outside the `Scene` model (nothing in `scene.py` is day-aware).

### Module layering rules
- `scene.py` owns *what an outcome is worth*; fight engines own *how it resolves*. The three fight surfaces **must never import `scene`**, and never each other.
- `skills.py` and `grid.py` are leaves — import nothing from the package.
- `grid.py` → `buildings.py` → `tactical.py` runs one direction; extracting `grid.py` is what removed the old cycle.
- `corpmap.py` has no `scene` import → gigs live on `app.location_gigs`, not `Location`.
- `corp_turn.py` imports `corpmap` only, never `scene`/`app`. `resolve_attack` takes a bare `Territory`, not a `CorpState`, so `rivals.py`'s AI (which has no `CorpState`) rolls the same dice.
- `relations.py` imports only `factions.py`/`gangs.py`.
- `scene.py` **does** import `corpmap` (for `Outcome.security_delta`) — legal, since corpmap's closure is all leaves and it never imports `scene` back.
- `saves.py` imports no game classes.

## Testing conventions

- **Model/generator changes**: `pytest.mark.parametrize("seed", range(150))` asserting invariants (not exact values). Wider for `test_corpmap_gen.py` (200), narrower for `test_buildings.py`/`test_tactical.py` (80).
- **Forced check outcomes**: `tests/helpers.py` — `AlwaysSix`, `AlwaysOne`, `ForcedChance`, `character_with_skill_value`. Import top-level: `from helpers import ForcedChance`.
- **Forced *contests*** (corp conflict): both dice come off the same rng, so `AlwaysSix`/`AlwaysOne` fix them equal and a capture collapses to the deterministic `committed > defense`.
- **UI changes**: Textual's `async with app.run_test() as pilot:` + `pilot.press`/`hover`/`click`. Prefer this over asserting on internals.
- **UI tests on a generated map** must not assume map shape (e.g. that the player's corp borders a rival) — construct the situation, and re-run the test 15–20× to confirm stability, since the map is unseeded per run.
- **Check outcome assertions without helpers**: must seed `random` at module level or it's flaky.
- **Shared fixtures**: `corp_map` in `tests/conftest.py`.
- **Check resolution**: `app.rng` is threaded through map/job *generation* but `resolve_check()` falls back to module-level `random` — seeding `app.rng` does NOT control dice.

## Save versions

Bump `saves.SAVE_VERSION` on any breaking state change. Current: **58** (corp conflict — `Territory.garrison`, `Outcome.security_delta`, `FactionEvent.from_faction_id` + `"seizure"` kind, `RivalAction.attack`, and a `CorpMap` param on `apply_outcome`/`resolve_choice`/`resolve_entrance`). Full version history in CLAUDE.md's "Save versions" section.

Note what does **not** need a bump: catalogs are keyed by id in save state (`Character.installed_cyberware` stores cyberware ids), so adding a field to a frozen catalog dataclass changes no pickled shape.

## Critical Textual gotchas

| Gotcha | Fix |
|---|---|
| `ListView.clear()` returns `AwaitRemove` | Must `await` before `.append()` |
| `ListView.index` is `None` after `clear()` | Set `.index = 0` after repopulating |
| `Static` renders str as Rich markup — `[text]` vanishes | Pass `Static.update()` a Rich `Text` object instead |
| Resume hook is `on_screen_resume` | Override the public one, not `_on_screen_resume` |
| Mouse hit-testing: `event.get_content_offset(widget)` | Returns `Offset` or `None` (pointer outside) |
| `Static` has no `.renderable` in Textual 8 | Use `.content` |
| A layout-affecting mutation (boot, expand/collapse, scroll) precedes a coordinate click/hover | Two `pilot.pause()`s (`_settle`) after the mutation, not one — recurs beyond cold boot |
| `app.notify()` toasts steal `pilot.click()` coordinates | Gate frequent notifications on player state (e.g. `discovered_fixers`) |
| `push_screen_wait` raises `NoActiveWorker` outside a worker | Use `push_screen(screen, callback)` — the house pattern for modal picks |

## Key design invariants (details in DESIGN.md)

- **Nothing rolls a core stat directly.** Every roll names a skill. All 8 weapon skills sit on `agility`.
- **Check resolution**: opposed d6 dice pool, 5s/6s count. Difficulty constants are old d20-DC-scale (~9–21), converted by `pool_for_difficulty`.
- **Time**: `elapsed_hours` is a continuous float clock. `spend_time()` is the single chokepoint. Day boundary fires `_apply_day_tick`.
- **Fatigue**: builds when `elapsed_hours - last_rest_hour > 24`. Rest halves it (doesn't clear). Caps at 3 stat penalty.
- **Stun**: persistent on `Character`, carries between fights. `mark_rested()` clears it. Still inert on the tactical grid.
- **Humanity**: two numbers. `Character.humanity` is a *ceiling* (6) permanently eroded by `SURGERY_SCARRING` on every install **and** removal; `cybernetics.free_humanity` is the ceiling minus installed chrome — that's the one that matters. Graded stat penalty via `Character.stat()`; **0 ends the run** (cyberpsychosis). Removing chrome rebounds its full cost, so pulling implants is the way back out.
- **Rep**: floored at -10, not 0. Negative rep bars entry from corp HQ lobbies.
- **Health**: `10 + body * 5` — raw Body, never gear-included.
- **Combat**: you take one action, then every non-stunned enemy attacks. Running always works; the Dodge check only decides cost.
- **Weapons are damage, skills are hit.** `skill_value` decides connection; `Item.damage` decides cost.
- **Fight surfaces**: abstract (`abstract_combat.py`), tactical grid (`tactical.py`), matrix (`matrix.py`). Burglary is a `tactical.py` walk over a `buildings.Building`. All share `combat.resolve_hit`, differing only in positioning.
- **Shop gating**: every catalog row carries `min_standing`, checked against standing with the location's owner `LocalCharacter`. A row above it is **hidden**, never shown locked.

## Corp mode quick reference

- **Takeover**: earned at `CORP_HQ`, gated on rep (20) + standing (+15) + cash (15,000eb). `CorpHQScreen`.
- **Daily action** (one of): expand onto neutral ground, **attack a bordering rival**, **deploy operatives** to a district you hold, train at the Academy, build lab, build efficiency upgrade, **rebuild a captured Research Facility or Academy**.
- **Conflict**: `attack_power = operatives + d6` vs `defense_power = (garrison + Security) + d6`; ties hold the ground. Operatives are the only consumer of `EmployeeCategory.OPERATIVE`; `TerritoryModifier.SECURITY` is the durable half of a defense. Capturing a district carries its `Location`s over — including labs, which is why `owned_research_facilities` is plural.
- **Runner ↔ corp**: a completed job applies `JOB_SECURITY_HIT` to the district it hit (`Outcome.security_delta`), softening it for an attack.
- **Research**: Scientists (capped by labs) + Research Assistants (capped by 2×labs), filled best-facility-first. Float RP. Six technologies, two 3-deep chains.
- **Corp websites**: every faction gets public `FactionEvent`s (territory claims, tech research, seizures). `CorpWebsiteScreen`.
- **Surveillance**: detection rolls in player corp's territory → `Sighting` log. Informational only.

## Known open threads

Mechanisms built ahead of their drivers, as of save v58:

- **`Medicine` / `Demolitions`** — in the 39-skill table, rolled by nothing. Hooks exist and are checkless today: `tactical.stabilize_ally`'s health kits, and grenade throws.
- **`Gunnery`** — reached by nothing at all; no mounted weapon exists.
- **`Character.crew_experience`** — a ledger with no spend path; `runners.RivalRunner` has no sheet.
- **`CorpState.sightings`** — purely informational, no standing/rep/combat consequence.
- **Matrix `CPU` node** — no reward wired, unlike `CACHE`.
- **Balance simulation is one surface deep** — `tools/combat_sim.py` drives only the abstract fight. The matrix, tactical/crew, burglary, security contracts, the whole corp economy, experience, fatigue, the conflict layer and Humanity erosion are all hand-set. `SURGERY_SCARRING` and `HUMANITY_PENALTY_THRESHOLDS` in particular were tuned by hand against the catalog, and the threshold placement is load-bearing — an earlier band made a +1 implant a net stat *loss*.
