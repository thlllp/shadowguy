# AGENTS.md

Behavioural rules + project reference for LLM coding assistants. Design rationale lives in `DESIGN.md` — read it before changing game behaviour.

## Behavioural rules

1. **State assumptions, ask when uncertain.** Present multiple interpretations, don't pick silently.
2. **Minimum code.** No unasked features, no single-use abstractions, no speculative error handling.
3. **Surgical edits.** Match existing style, don't refactor neighbours, only clean up orphans YOUR changes create.
4. **Goal-driven.** Transform tasks into verifiable checkpoints. Loop until verified.

## Project

Text-based cyberpunk roguelite TUI. Python 3.14, `uv`, Textual.

Two coupled modes: **Runner** (RPG, one character, permadeath) + **Corp** (4X, territory/resources). Switching is earned in-game, not menu-picked. A run starts as either one (New Game → Runner / Corp).

### Run rules
- No meta-progression. Each run fresh.
- Runner ends on death. Corp ends on destruction/takeover/assassination.

## Key commands

```
uv run pytest -q              # test suite (22 files, pytest>=8)
uv run ruff check src/        # lint (ruff pinned in dev group)
```

## Module map

### Core engine
| Module | Role | Must never import |
|---|---|---|
| `skills.py` | 32-skill table, `skill_value()`, `skill_for()` | nothing from package |
| `character.py` | All mutable state: stats, health, stun, fatigue, humanity, skills, standings, crew, inventory | — |
| `checks.py` | `resolve_check()` — single chokepoint for every roll | — |
| `scene.py` | `Scene`/`Stage`/`Choice`/`Outcome` + fight wrappers; `apply_outcome()` | `jobs` |
| `combat.py` | Abstract fight: rounds, enemies, `resolve_hit` | `scene` |
| `tactical.py` | Grid fight: tcod FOV+A\*, `generate_building` for Burglary | `scene` |
| `matrix.py` | Matrix fight: ICE, node networks, integrity, programs | `scene` |
| `jobs.py` | Job generation (9 archetypes) + `JobTiming` + legwork + `SmugglingJob` | — |
| `gigs.py` | Per-Location gig generation | — |
| `fixer.py` | Fixer roster + job/security offers | — |
| `runners.py` | Hireable-runner roster | — |
| `factions.py` | Corp Factions + HQ officer ladder | — |
| `gangs.py` | Street Gangs + `GANG_RANKS` | — |
| `relations.py` | Seeded Faction↔Gang standing | `factions`, `gangs` only |
| `corpmap.py` | Territory map, Locations, `LocalCharacter`s, ASCII renderer | `scene` |
| `corp_turn.py` | Corp mode: `CorpState`, income/research, daily action, `TECHNOLOGIES` | `corpmap` only |
| `security.py` | Parallel: multi-night security contracts | — |
| `encounters.py` | Parallel: gang turf-entry toll-or-attack | — |
| `rivals.py` | Parallel: faction expansion + NPC runner daily turn | — |
| `surveillance.py` | Parallel: detection rolls in player corp's territory | — |
| `shops.py` | Retail catalogs, pricing, programs | — |
| `cybernetics.py` | Cyberware catalog + install/remove (no shop wired yet) | — |
| `saves.py` | Pickle save/load | no game classes |
| `app.py` | `ShadowguyApp`: `spend_time`/`_apply_day_tick`, save/load; no screens | — |

### Screens (`screens/`)
| File | Contents |
|---|---|
| `__init__.py` | `CharacterSheet`, `BackScreen`, `PanelNav`, map glyph helpers |
| `creation_screen.py` | `CharacterCreationScreen` |
| `main_menu.py` | `MainMenu` |
| `menu_screens.py` | `TitleMenu`, `ModeSelect`, `CorpSelect`, `Test`, `Quit`, `Load` |
| `scene_screen.py` | `SceneScreen` |
| `combat_screen.py` | `CombatScreen` |
| `tactical_screen.py` | `TacticalScreen` + `GrenadePickScreen` |
| `matrix_screen.py` | `MatrixScreen` |
| `burglary_screens.py` | `EntrancePick` + `BurglaryWalk` |
| `corp_map_screen.py` | `CorpMapScreen` + `GangTollScreen` |
| `corp_screen.py` | `CorpScreen` + `CorpMainMenu` + `ResearchTreeScreen` |
| `shop_screens.py` | `FixerOffers`, `Shop`, `Bar`, `CorpHQ`, `Hospital`, `RealEstate`, `Safehouse`, `Junkyard`, `GangDen` |
| `info_screens.py` | `Phone` + apps (Contacts, Web, CorpWebsite, AlarmClock, Messages); `Inventory`, `Cyberdeck`, `Skills` |

### Parallel resolution modules
`security.py`, `encounters.py`, `rivals.py`, `surveillance.py` — day-advance pipelines outside the `Scene` model (nothing in `scene.py` is day-aware).

### Module layering rules
- `scene.py` owns *what an outcome is worth*; fight engines own *how it resolves*. Fight engines **must never import `scene`**.
- `skills.py` is a leaf — `character.py` imports it, never the reverse.
- `corpmap.py` has no `scene` import → gigs live on `app.location_gigs`, not `Location`.
- `corp_turn.py` imports `corpmap` only, never `scene`/`app`.
- `relations.py` imports only `factions.py`/`gangs.py`.
- `saves.py` imports no game classes.

## Testing conventions

- **Model/generator changes**: `pytest.mark.parametrize("seed", range(150))` asserting invariants (not exact values). Wider for `test_corpmap.py` (200), narrower for burglary/tactical (80).
- **Forced check outcomes**: `tests/helpers.py` — `AlwaysSix`, `AlwaysOne`, `ForcedChance`, `character_with_skill_value`. Import top-level: `from helpers import ForcedChance`.
- **UI changes**: Textual's `async with app.run_test() as pilot:` + `pilot.press`/`hover`/`click`. Prefer this over asserting on internals.
- **Check outcome assertions without helpers**: must seed `random` at module level or it's flaky.
- **Shared fixtures**: `corp_map` in `tests/conftest.py` (8 suites).
- **Check resolution**: `app.rng` is threaded through map/job *generation* but `resolve_check()` falls back to module-level `random` — seeding `app.rng` does NOT control dice.

## Save versions

Bump `saves.SAVE_VERSION` on any breaking state change. Last version: **47** (the `OFFICE` `BuildingKind`). Full version history in `CLAUDE.md:185-214`.

## Critical Textual gotchas

| Gotcha | Fix |
|---|---|
| `ListView.clear()` returns `AwaitRemove` | Must `await` before `.append()` |
| `ListView.index` is `None` after `clear()` | Set `.index = 0` after repopulating |
| `Static` renders str as Rich markup — `[text]` vanishes | Pass `Static.update()` a Rich `Text` object instead |
| Resume hook is `on_screen_resume` | Override the public one, not `_on_screen_resume` |
| Mouse hit-testing: `event.get_content_offset(widget)` | Returns `Offset` or `None` (pointer outside) |
| `Static` has no `.renderable` in Textual 8 | Use `.content` |
| `run_test()` hands back pilot before first layout | Two `pilot.pause()` before first `click()` (cold boot only) |
| `app.notify()` toasts steal `pilot.click()` coordinates | Gate frequent notifications on player state (e.g. `discovered_fixers`) |

## Key design invariants (details in DESIGN.md)

- **Nothing rolls a core stat directly.** Every roll names a skill.
- **Check resolution**: opposed d6 dice pool, 5s/6s count. Difficulty constants are old d20-DC-scale (~9–21), converted by `pool_for_difficulty`.
- **Time**: `elapsed_hours` is a continuous float clock. `spend_time()` is the single chokepoint. Day boundary fires `_apply_day_tick`.
- **Fatigue**: builds when `elapsed_hours - last_rest_hour > 24`. Rest halves it (doesn't clear). Caps at 3 stat penalty.
- **Stun**: persistent on `Character`, carries between fights. `mark_rested()` clears it.
- **Humanity**: fixed baseline (6), a cyberware budget, not a draining pool.
- **Rep**: floored at -10, not 0. Negative rep bars entry from corp HQ lobbies.
- **Health**: `10 + body * 5` — raw Body, never gear-included.
- **Combat**: you take one action, then every non-stunned enemy attacks. Running always works; the Dodge check only decides cost.
- **Weapons are damage, skills are hit.** `skill_value` decides connection; `Item.damage` decides cost.
- **Fight surfaces**: abstract (`combat.py`), tactical grid (`tactical.py`), matrix (`matrix.py`), burglary walk (`tactical.generate_building` + `BurglaryWalkScreen`). All share `resolve_hit`, differing only in positioning.

## Corp mode quick reference

- **Takeover**: earned at `CORP_HQ`, gated on rep (20) + standing (+15) + cash (15,000eb). `CorpHQScreen`.
- **Daily action**: one of expand (neutral territory), train (Academy employees), build lab, build efficiency upgrade.
- **Research**: Scientists (capped by labs) + Research Assistants (capped by 2×labs). Float RP. Six technologies, two 3-deep chains.
- **Corp websites**: every faction gets public `FactionEvent`s (territory claims, tech research). `CorpWebsiteScreen`.
- **Surveillance**: detection rolls in player corp's territory → `Sighting` log. Informational only.
