# Shadowguy

A text-based cyberpunk roguelite TUI. Python 3.14, built on [Textual](https://textual.textualize.io/) and [tcod](https://python-tcod.readthedocs.io/).

One run, one life. Play it as a **Runner** — an RPG-scale character with stats, skills, scene-based jobs and permadeath — or as a **Corp** — a 4X-scale area-control game against rival factions. The two halves share the same city and the same day clock. A Runner can eventually buy a controlling stake in a corp; a Corp start never builds a runner at all.

## Quick start

```bash
uv sync
uv run shadowguy

# Reproduce a specific city and dice stream
uv run shadowguy --seed 12345
# or: SHADOWGUY_SEED=12345 uv run shadowguy

# Show the developer Test menu on the title screen
uv run shadowguy --dev
```

`How to Play` on the title screen (and in the in-run `q` menu) is the short field manual. The current run's seed is always visible in the status panel.

**Terminal size:** the TUI needs at least **80×24**; **100×30 or larger is recommended**. At 80×24 the map is a small scrolling viewport, the Locals/actions strip and corp ledger are short and scroll internally, and a few footer hints are clipped.

## Graphical front-end (preview)

The TUI is the canonical game and the full test surface. The Arcade front-end is
an **experimental preview slice**, not an alternative client yet: it shares the
same `ShadowguyApp` state and rules and does not change the TUI, but it starts on
the engine's bare default character and has **no character creation**. The start
screen offers a Runner run, a Corp run, or loading the newest save. Corp mode
covers the map, the daily economy, the Academy, the Research Facility and
Operations; corp takeover/HQ and the surveillance log remain TUI-only, along with
crew, burglary edge cases, grenades, hacker programs and cyberware. Saves are
shared with the TUI: `F5` writes a normal run save and `F9` loads the newest
save.

```bash
uv sync --group gui
uv run --group gui python -m shadowguy.gui --seed 12345
# or, once the gui extra is installed: shadowguy-gui --seed 12345
```

Controls: arrows move the cursor (`W`/`A`/`D` also work; `S` opens shops in a
runner run, so use the Down arrow for south), `Enter` travels or relocates, `R`
rests/ends the day, `F` fixers, `J` jobs on site, `S` shops, `I` gear, `G` gang
den, `V` deliver a smuggling job, `C` opens the corp action list, `F5` saves,
`F9` loads the newest save, `Q` quits.

The slice covers the map/character/district panels, travel with gang and corp
encounters (toll or fight), fixer offers and job acceptance, the scene check
runner, abstract/tactical/matrix fights, shops, inventory, smuggling deliveries,
save/load, and a corp-only run: expand, survey, security/surveillance/development,
fundraise, Academy training and grants, labs and efficiency, the research tree,
and deploy/attack/intel/sabotage. See `src/shadowguy/gui/` and `shadowguy.travel`
for the shared travel-time formula. Playtest reports live in `docs/playtests/`.

## What a run looks like

**Creation.** Everything starts at rank 1: six core stats (Body, Strength, Agility, Perception, Logic, Cool) and 39 skills. Spend 6 stat points and 20 skill points once. Points get dearer as a skill climbs, so a specialist can take one skill to 10 for 19 of the 20. Six presets — Enforcer, Hacker, Infiltrator, Gunslinger, Street Samurai, Fixer — or a hand-built character. Skill points can also be converted into creation-only gear budget (`1 point = 1000eb`); unspent gear budget is written off when the run begins, never banked as cash.

**Growth.** Completed jobs pay Experience, a separate post-creation pool spent on more stats or skill ranks. Gigs, legwork and security contracts do not pay XP.

**Work.** Gigs are short local scenes at a location in the district you are standing in, owned by one of its locals. Jobs come from fixers: each fixer holds a couple of offers and a security contract, and a job has to be run on site, on the right day. Legwork is optional prep that banks an advantage on the job's first check. Security contracts are the inverse of a heist — end the day on site for several nights and get paid.

**Time.** The clock is hours, not stamina. Travel costs hours (cut by a vehicle or the RideShare+ app); jobs cost 8–12. Crossing midnight fires the day tick. Rest is an action: it charges lodging unless you own a place or are in a slum/outskirt, heals one point, halves fatigue, and can be shortened with the Phone's Alarm Clock. Health mostly comes back through paid hospital stays; going more than a day without rest builds Fatigue, which drains stats.

**Fights.** Four surfaces share one dice engine:
- **Abstract** — turn-based rounds, no positions. Used by legwork ambushes and gang encounters.
- **Tactical** — a generated grid with cover, reach, line of sight and exits. Every non-matrix job fight lands here.
- **Matrix** — a Data Heist's fight against ICE on a separate integrity pool; losing ejects you rather than killing you.
- **Burglary** — pick an entrance, then walk a generated building interior past guards, cameras and locked doors.

Running away always works in abstract combat; the roll only decides what it costs. A critical failure on a job stage can drop you into a fight you did not choose.

**Corp mode.** Take one of five factions and run it alongside the city clock. A corp gets 2 action points a day: expand into neutral ground, attack a rival, deploy or task operatives, train staff, build labs/academies, or buy Security/Surveillance/Development. Districts pay income but charge upkeep, and holding more ground than logistics supports adds a growing strain bill. Research points buy 31 technologies, and after the tree runs out they buy repeatable Logistics Optimization levels (+1 district supplied each). A fully staffed Academy can also run a research grant instead of training. Lose your last district and the run is over.

**Takeover.** A Runner can buy a controlling stake at a corp's own HQ — 15,000eb, 20 rep and +15 standing with that corp — and then play both halves of the same run.

## Systems at a glance

| | |
|---|---|
| **Checks** | Opposed d6 pool: roll `skill value + advantage` dice and count 5s and 6s against the opposition. Net successes decide it; a gap of 3 either way is a critical. |
| **Combat** | You take one action, then every standing enemy swings. Weapons are the damage, skills are the hit, and Strength adds to melee/thrown damage. Brace, read the fight, intimidate, flee or throw a grenade. |
| **Tactical** | tcod FOV and A*, cover raises the to-hit difficulty, firearms kite and melee closes. Reach an exit to leave — no roll. |
| **Matrix** | Node-by-node ICE fights. The cyberdeck is the damage, Cybercombat is the attack, Computer extracts data and analyzes nodes. Programs trade alert level for effects. |
| **Burglary** | Entrance check, then a multi-level interior walk with guard sightlines, cameras, locks and stairs. Getting seen sends you loud. |
| **Map** | 260 procedurally generated districts on a 14×24 grid, always connected. Five factions hold 6 districts each by construction; the rest is neutral, plus slums, outskirts, parks and scattered gang turf. |
| **Standing** | Street `rep`, per-corp `standing`, per-fixer trust, and per-person local standing that bends shop prices and gates stock. |
| **Crew** | Meet independent runners at bars or buy an introduction. Hire for a daily wage or a cut of one job. They earn rating and gear from the work they do, including jobs they take without you. |
| **Fixers** | Ten seated per run: street contacts on neutral ground, inside contacts on corp turf, and Amy at Amy's Place. They broker jobs and security contracts. |
| **Gangs** | Four gangs hold scattered turf. Walk onto ground held by one you have crossed and you can be tolled or attacked. |
| **Shops** | Pawn, weapon, auto, pharmacy and computer stores; a Ripperdoc's cyber clinic; real estate; junkyards and docks. Standing moves prices and stock. |
| **Cyberware** | Installed, always active, one piece per slot. Every operation permanently erodes your Humanity ceiling; 0 free Humanity ends the run in cyberpsychosis. Removing a piece returns its Humanity cost, so removal is the way back. |
| **Workshop** | Build one in a safehouse, then install weapon/wearable mods (Armorer) or cook chems (Chemistry) from scavenged materials. |
| **Corp mode** | 2 action points/day, daily income minus upkeep and logistics strain, 31 technologies, repeatable Logistics Optimization projects, Academy training and research grants, operatives, garrisons, corp-vs-corp attacks, and a research/academy loop. |

## Controls

Rows and menus are arrow-key + `enter`. Screen-specific:

| Key | Screen | Action |
|---|---|---|
| `←↑→↓` | Corp map | Move cursor (`*`); `@` is you |
| `enter` | Corp map | Travel to cursor (costs hours, cut by a vehicle) |
| `i` / `d` / `k` / `c` | Corp map | Gear / Cyberdeck / Skills / Phone |
| `j` / `l` | Corp map | Jobs / Legwork |
| `tab` / `escape` | Corp map | Cycle panels / release focus back to the map |
| `R` | Corp map or Corp screen | Rest |
| `r` | Corp map | Run a Corp (or open the corp screen) |
| `t` | Corp map or Corp screen | Research Tree |
| `q` | Most screens | Menu: save, load, how to play, restart, quit |
| `escape` | Most screens | Back |
| `g` / `b` / `r` | Character creation | Gear / begin run / reset build |
| `←` `→` | Creation, contacts, skills | Previous / next panel |
| `←↑→↓` | Tactical fight | Move (numpad also works) |
| `f` / `g` / `h` / `r` / `s` | Tactical fight | Aim attack / grenade / direct hacker / reload / stabilize |
| `e` / `l` / `x` | Tactical fight | End turn / leave via exit / look |
| `>` | Tactical fight | Take stairs |

## Where to read next

- [`DESIGN.md`](DESIGN.md) — why every system is the way it is, which constants are load-bearing, and what has been balance-simulated.
- [`CLAUDE.md`](CLAUDE.md) — codebase map, module layering, save-version history, testing conventions, and Textual gotchas.

## Development

```bash
uv run pytest -q              # full suite (CI runs this on every push/PR to main)
uv run ruff check src/        # lint (ruff is pinned in the dev group)
```

The balance harnesses in `tools/` are developer tools, run by hand:

```bash
uv run python tools/combat_sim.py      # abstract fights, presets x enemy tiers
uv run python tools/matrix_sim.py      # ICE fights, presets x ICE tiers
uv run python tools/conflict_sim.py    # corp attack grid + multi-faction map sim
uv run python tools/fatigue_sim.py     # rest cadence x job length over 60 days
uv run python tools/corp_econ_sim.py   # 6 policies x 30 maps to day 120
```

Tests are seed-swept for generators, driven headlessly through Textual's `run_test()` for screens, and pin check outcomes with fixed-face RNG helpers. `app.rng` is the run seed's stream; every in-game check reads it.
