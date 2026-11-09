# Shadowguy — three test runs

Date: 2026-11-06
Driver + saves: `/tmp/opencode/allie/`
All runs headless via `run_test()`; seeds pinned per run. The Test menu is headless/dev-only
and is called out where used.

## Verdict

Three seeded sessions (runner @80x24, corp @80x24, systems @100x40). **No crashes or
tracebacks from the game.** One P2 reproduced (80x24 corp layout), one P2 shown to be
content-dependent (ledger), one earlier P3 corrected (the Rest key does exist).

## Run 1 — runner at the 80x24 minimum — seed 700001

Goal: play a runner at the documented minimum size through a gig, rest, and save/load.

- New Game → Runner → Premade → Gunslinger → begin; map renders exactly 80x24 (24 rows,
  max width 80), seed line visible.
- Gig at Bramble (1 hop): opened the location box, took "Test Drive", played the scene
  (FAILURE: rep 0→−1, 4h spent). No layout clipping.
- Rest via the Rest button: 8h, −5eb lodging, health unchanged at 30/30 (already full).
- Save → load: cash/rep/health/day/location identical; reopens CorpMapScreen.
- Attempted the awkward path of resting with `R`: **the key works** (8h spent). See the
  correction below.

No new defects.

## Run 2 — corp-only at 80x24 — seed 700002

Goal: expansion, training, grants, ledger and panel layout, save/load.

- Corp → Ironclad Dynamics; 6 owned districts, 2000eb, 2 AP.
- Corp tab: 25 action rows; expanded once (AP 2→1, cash 2000→1600, owned 6→7) and queued
  a research assistant (AP→0, cash 1600→1180).
- Day 1 panel regions at 80x24: `#activities y=8 h=5`, `#academy_panel y=15 h=5`,
  `#research_panel y=20 h=6`, `#surveillance_panel y=26 h=3` (last one below the viewport).
- After the RA finished (day 5), regions became `#academy_panel y=5 h=9` — overlapping the
  top bar / `#activities` (y=8 h=5) — and the Academy rows ("Expand Academy", "Research
  grant", …) were **not in the rendered text**. Tabbing focus to `academy_list` moved it to
  `y=18 h=5` and the rows appeared. The panel is laid out in the wrong place until focused.
- Blind action was not re-spent on this seed because AP was 0 at that moment; the same
  defect on seed 20261107 did spend 250eb + 1 AP from an invisible row (previous report).
- Map-mode ledger at 80x24: `#corp_info` `y=19 h=4`, all four lines visible (`Logistics:`
  and `Research Facility:` both present) — this seed's district names were short enough not
  to wrap. See the content-dependence note below.
- Save → load (with pending recruit): cash/RP/AP/day/owned districts all restored.

### Finding (P2, reproduces across seeds)

**At 80x24 the Corp tab's Academy / Research / Surveillance panels are laid out outside the
normal flow and can be invisible yet focusable.** On 20261107 the panels were entirely below
the 24-row viewport (`academy y=28`, `research y=37`) and an invisible `Research grant`
row still spent 250eb + 1 AP; on 700002 the Academy panel overlapped the top bar until it
was focused. The Screen cannot scroll (`virtual_size` is 24), so there is no user path to
the hidden content other than tabbing into it blind.

### Finding (P2, content-dependent)

**The 80x24 corp ledger clips its lower lines once its first line wraps.** On 20261107
`#corp_info` was `y=20 h=3` with 4 wrapped content lines, so `Logistics:` and
`Research Facility:` never rendered and `virtual_size` reported no overflow (no scroll).
On 700002 the same four lines fit in `h=4`. The rendered output depends on district-name
length, and the existing regression test asserts on `.content`, so it passes either way.

## Run 3 — combat surfaces at 100x40 — seed 700003

Goal: drive the four fight/activity surfaces (Test menu, dev-only, so not player-reachable
without `--dev`).

- Tactical tier 0: full fight, VICTORY after 19 rounds, ended at 7/15 health.
- Matrix tier 0: routed to the data node, fought the guardian, SEIZED.
- Burglary — Residential: entrance pick, board rendered, spawn `(22,5)` on an exit,
  objective `(0,(2,4))`; left via `l` (ESCAPED; screen stays for the Enter confirmation).
- Fishing: cast at the pier, scene resolved, returned to the Test menu.

No new defects. (Driver note: the earlier attempt to walk a burglary to its objective got
stuck at a wall in my pathing code, not in the game.)

## Correction to the earlier reports

**The Rest key exists.** `Binding("R", "rest_action", "Rest")` is on both CorpMapScreen and
CorpScreen, and pressing `R` was verified to rest 8h on a runner and to 6 AM next day on a
corp. The earlier "Rest has no keyboard path" P3 was wrong. What remains is a discoverability
P3: at 80/100 columns the runner map's Footer is full and truncates before the `R Rest` tile,
the `#rest_button` is `can_focus=False`, and the field manual's CONTROLS list never mentions
`R`. The two earlier report files have been corrected.

## Still-open defects from the previous session (unchanged code)

- **P2 — mid-fight save/load drops the encounter** (seed 20261106): save during a live
  CombatScreen, load, and the fight is gone with health retained; a free escape from a
  losing fight and a refused toll.
- **P3 — off-district Job/Legwork row silently no-ops** (seed 20261106).

## Coverage

Run 1: 80x24 runner map, creation, travel, location box, gig scene, Rest (button and `R`),
save/load, 80x24 rendering.
Run 2: corp-only map, Corp tab, expand, Academy training, day ticks, panel/ledger layout at
80x24, save/load with pending recruit.
Run 3: Test-menu tactical, matrix, burglary, fishing; 100x40 layouts.

Not reached in these three: corp research tree/logistics purchase, operative tasking, real
gang/corp encounter, runner death, cyberdeck/programs, crew hiring, 80x24 tactical.
