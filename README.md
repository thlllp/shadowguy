# Shadowguy

A text-based cyberpunk roguelite TUI. Python 3.14, built on [Textual](https://textual.textualize.io/) and [tcod](https://python-tcod.readthedocs.io/).

Two coupled game modes — **Runner mode** (RPG-scale: one character, scene-based jobs, permadeath) and **Corp mode** (4X-scale: area control against rival corps). Corp mode is a browsable territory-map preview so far; no turns, economy or conflict yet.

## Quick start

```bash
uv sync
uv run shadowguy
```

## How it plays

You start as a nobody standing on unclaimed ground at the edge of a procedurally-generated city. Everything begins at 1 — six core stats, all 32 skills — and character creation is the *whole* progression system: you spend 6 stat points and 20 skill points once, and there is no XP. Take a preset (Enforcer, Hacker, Infiltrator) or build by hand. Ranks get dearer as they climb, so taking one skill from 1 to 10 costs 19 of your 20 points: a specialist buys one great skill and almost nothing else.

From there you work. **Gigs** are quick single-scene street work, runnable wherever you're standing, owned by a local who'll think better of you for it. **Jobs** come from fixers, are aimed at a real corp's real building on the real map, and have to be run *on site* — so an accepted job is a place you have to travel to, and a scheduled one means being in the right district on the right day. **Legwork** scouts a job beforehand to bank an advantage on its first check. **Security contracts** are the inverse of a heist: a fixer signs you to guard a corp for several nights, and you work them by ending the day on-site — steady pay and free lodging, but they pin you to one district.

Every job stage offers several ways through — a clean approach, a middling one, and an easy one that bleeds — plus the option to just take them first. The approaches in a stage always sit on different stats, so every build has *a* way through, but rarely the same way. Some jobs open on a **burglary**: pick an entrance off a building diagram, then sneak the interior past guard sightlines to the objective. Others are a specialist's work: a **Data Heist** is a netrunner's remote break-in whose fights play out in the matrix against ICE — offered to anyone, but a runner without a cyberdeck and the Hack for it will bleed. Botch badly enough — or get spotted — and you go loud, which drops you into a fight you didn't choose.

Cash goes on gear, chems, weapons, a vehicle (free travel moves), and eventually property. Health does not come back on its own: you pay for days in a hospital ward. Sleeping costs lodging unless you own a place in the district, so buying a safehouse pays for itself.

Death is permanent. No meta-progression between runs.

## Systems

| | |
|---|---|
| **Checks** | An opposed d6 dice pool. Roll `skill_value + advantage` dice, count 5s and 6s; the opposition rolls against you. Net successes decide it; a gap of 3+ either way is a critical. |
| **Combat** | Round-based: you take one action, then every standing enemy swings. Actions span all six stats — attack, brace, read the fight, face them down, break and run, throw a grenade. Weapons are the damage, skills are the hit. Running always works; the roll only decides what it costs you. |
| **Tactical** | ~35% of jobs play their fights on a generated grid instead: tcod FOV and A*, cover as a raised to-hit difficulty, firearms that kite and melee that has to close. Reach an exit to leave, no roll. |
| **Matrix** | A Data Heist's fights are against ICE, not muscle, played out node by node across a small hacked network. Round-based like combat and rolling the same dice, but it drains a separate integrity pool instead of your health — lose and you're ejected (the contract blown) rather than killed. The cyberdeck is the damage, Hack is the hit; jacking out always works. Installed programs add a bypass roll, a soak-ignoring data grab, and remote recon — but pushing them raises the network's alert level, making every ICE hit harder to dodge for the rest of the run. |
| **Burglary** | One job archetype opens on a break-in: pick an entrance off a small building diagram (its check resolves on the spot), then walk the generated interior to the objective, avoiding static guards' sightlines. Getting seen sends you loud into the job's fight. |
| **Corp map** | 38 districts, generated fresh each run and always connected. Three rival corps hold equal territory by construction. Each district has locations — shops, bars, data hubs, clinics, a corp HQ — that jobs, gigs and legwork all hang off. |
| **Standing** | Four separate relationships: street `rep`, per-corp `standing` (hit one corp and its rivals warm to you), per-fixer trust, and per-person local standing that bends shop prices and unlocks stock. |
| **Crew** | Hire runners at a bar — indefinitely for a daily wage, or for one job in exchange for a cut of its payout. Miss payroll and they walk. |
| **Fixers** | Nine seated fresh each run: six street-level contacts on neutral ground plus three planted inside the corps' own turf. Each brokers a couple of jobs and a security contract. |
| **Corp HQs** | Each corp has a headquarters whose officer ladder gates on both street rep and corp standing. The lobby is public; the executive is not. Flavor for now. |

## Controls

Rows and menus are driven with the arrow keys and `enter`. Screen-specific:

| Key | Screen | Action |
|---|---|---|
| `m` / `i` / `k` / `c` | Main menu | Corp map / Gear / Skills / Contacts |
| `←` `→` | Main menu, creation, contacts | Previous / next panel |
| `r` / `b` | Character creation | Reset build / begin run |
| `←↑→↓` | Corp map | Move cursor (`*`); `@` is you |
| `enter` | Corp map | Travel to cursor (costs stamina unless a vehicle covers it) |
| `←↑→↓` / `f` / `e` / `l` | Tactical fight | Move / attack / end turn / leave via exit |
| `q` | Most screens | Menu (save, load, quit) |
| `escape` | Most screens | Back |

## Project structure

```
src/shadowguy/
  app.py          Textual App and every screen (MainMenu, SceneScreen, CombatScreen, CorpMapScreen, ...)
  character.py    Character dataclass: stats, health, skills, inventory, crew, standing, accepted jobs
  archetypes.py   Enforcer/Hacker/Infiltrator creation presets
  skills.py       Skill table (32 skills across 6 core stats); leaf module
  checks.py       resolve_check(): the opposed d6 pool every roll in the game goes through
  combat.py       Round-based combat: enemy roster, the six-stat action set, shared resolve_hit
  tactical.py     Grid combat: Grid/Tile, tcod FOV + A*, turn engine, BSP map generation; also burglary buildings
  matrix.py       Matrix combat (Data Heist): ICE roster, integrity pool, jack-in actions; reuses resolve_hit
  scene.py        Scene/Stage/Choice/Outcome/Encounter/TacticalStage/Entrance/BurglaryStage/MatrixStage data model
  jobs.py         Procedural job generation, stage templates, timing, per-job legwork
  gigs.py         Per-location gig generation from per-kind templates
  fixer.py        Fixer roster, job offers, security offers, refresh/expiry
  security.py     Multi-night security contract generation and nightly resolution (not Scene-based)
  runners.py      Hireable NPC runners (crew)
  factions.py     Rival corps, standing rules, HQ officer ladder
  corpmap.py      Procedural territory map (38 nodes), ASCII renderer, locations, property/lodging
  shops.py        Item and consumable catalogs, buy/sell/equip, standing pricing, hospital care
  content.py      Unwired example scenes (worked examples of the Scene data model)
  saves.py        Pickle-based save/load
```

## Development

There's a `pytest` suite under `tests/`, run alongside `ruff` in CI on every push and PR. Generators are checked by sweeping a range of seeds and asserting invariants (a map that merely *looks* plausible can be quietly unfair); screens are driven headlessly with Textual's `async with app.run_test() as pilot:`; and a check outcome is pinned either by a fixed-face `random.Random` subclass or by seeding the module-level `random` — `app.rng` doesn't control the dice.

```bash
uv sync
uv run pytest -q
uvx ruff check src/
```

See [CLAUDE.md](CLAUDE.md) for the design rationale, invariants, and the balance numbers behind the tuning constants.
