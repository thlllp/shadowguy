# Shadowguy — "cover everything" pass

Date: 2026-11-06
Build: **7c761e4** (Arcade slice) + working-tree `gui/window.py` rename.
Suite: `uv run --group gui pytest -q` → **24,838 passed**.

This pass closes the gaps listed at the end of the five-run report and sweeps the
remaining TUI systems. New GUI defects are below; the coverage ledger at the end says
exactly what is still not covered and why.

## New findings

### [P1] GUI toll/encounter fights ignore death — a dead runner keeps playing
- Seeds: 940050 / 940060 (forced outcome; branch is reachable from any gang/corp toll)
- Screen: GUI `TollView` → `CombatView`
- Steps: reach a toll fight; let the fight resolve as `DEAD` (forced by setting the
  combat state's outcome; the handler is the same one a real death reaches).
- Expected: `end_run_dead` / a GameOver screen, as in the TUI fallback.
- Actual: `TollView._after_fight` only sets `"Fight over: Dead."` and pops back to the
  map. With health 0, `character.is_alive` is False but the map is still live; pressing
  `R` rested and healed the dead runner to 1hp. No GameOverView is pushed.
- Evidence: `DEAD toll fight: hp 0 alive False top MapView gameover False`; `after R on
  dead runner: day 1 hour 8 hp 1`.
- Note: `TollView._after_fight` also ignores `KNOCKED_OUT` (no knockout roll, no mug).
- Repro: 2/2 forced.

### [P2] GUI job fights treat a knockout as a win
- Seed: 940051 (forced outcome)
- Screen: GUI `SceneView` → `CombatView`
- Steps: run a scene fight; resolve it as `KNOCKED_OUT`.
- Expected: the loss branch (TUI does `knockout_roll`/mug or ends the run), not the
  victory payload.
- Actual: `SceneView._after_fight` only special-cases `DEAD`; for `KNOCKED_OUT` it takes
  `stage.combat.victory` and applies it. In the test the victory Outcome carried
  `cash_delta=999, experience_delta=99` and both were paid: `cash 100 -> 1099, xp 0 -> 99`.
- Repro: 1/1 forced.

### [P2] GUI: a job whose first stage is a burglary dead-ends with no rows
- Seeds: 940001, 940004
- Screen: GUI `SceneView`
- Steps: accept a "Burglary: …" job whose stage 0 is a `BurglaryStage`; open Jobs on site.
- Expected: the entrance picker, as the TUI `SceneScreen.on_mount` does (`_show_burglary`).
- Actual: `SceneView._show_stage` only handles narration and choices, not `stage.burglary`,
  so the screen has zero rows, an empty prompt, and no action except Escape (which pops
  the scene and leaves the accepted job unplayed). `_advance` handles burglary for a
  *next* stage, but not a start stage.
- Evidence: `stage stage_0 burglary=True choices=0 … rows 0`.
- Repro: 2/2 seeds.

### [P2] GUI: `S` opens shops instead of moving down (WASD conflict)
- Seed: 930003 (re-confirmed this pass)
- `MapView.on_key` consumes `arcade.key.S` for shops before the WASD block, so `S`
  never moves south; the footer advertises both "Arrows/WASD move" and "S shops".
- Repro: 1/1 (unconditional).

### [P2] GUI: rival-taken offers are listed and accepted
- Seed: 930004, day 4 (re-confirmed)
- Rook's "Sabotage … (Sumpline)" had `taken_by = runner_specter`; `FixerView` lists it as
  a normal job and `pick` appends it to `accepted_jobs`. The TUI labels it TAKEN and
  refuses.
- Repro: 1/1.

### [P3] GUI: failed program purchase reports success
- Seed: 930004 — "Sleaze — 320eb" with 100eb → message `"Bought sleaze."`, cash and
  `owned_programs` unchanged.
- Repro: 1/1.

### [P3] GUI: arrows ignored while aiming
- Seed: 930005 — `F` aim on `(5,4)`, `Left`/`Right` leave the cursor at `(5,4)`; only Tab
  re-targets. TUI moves the aim cursor with arrows.
- Repro: 1/1.

## Coverage this pass

**GUI**
- Successful job: seed **940005** — `+250eb, +10xp, +1rep, −2hp` after a full scene.
- Security contract: seed **940030** — accepted at Doc Vex, travelled to Sablewood,
  watched 4 nights; two successful nights paid **+35** each, completion paid **35 + 70
  bonus** (cash 135→240). No contract panel exists in the GUI, but the mechanics run.
- Corp toll: seed **940030** — `CorpEncounter` pay moved cash 240→160, standing −1, and
  escorted back to the entry territory; refuse pushed `CombatView` and the fight resolved
  (`Knocked_Out`). Draw paths smoke-passed.
- Save/load: **not implemented** — `MapView`/`GameSession` expose no save hooks; no key
  binding, no menu.
- Corp mode: **not implemented** — setting a `CorpState`/`corp_only` on the session still
  renders the runner map with no corp actions (`has corp view? False`).
- Smuggling: **unreachable** — the GUI has no gang-den/location interaction beyond shops.

**TUI sweep (seed 950002/950003/950004/950050)**
- Bar screen (empty roster at the hour), cyber clinic browse + refusal, phone
  Contacts/Web/Messages, Alarm Clock (alarm fired; rest ran 0→24h at the chosen hour),
  Cyberdeck (no deck), Safehouse workshop, Junkyard scavenge (gained `wire`),
  Real Estate browse (safehouses 325–450eb, all unaffordable at 100eb).
- Workshop craft **succeeded**: Chem Y crafted for **−75eb, +1 consumable, −1 wire**.
- Corp player attack (seed 950004): trained an operative, attacked Ghostwire's Stratum —
  attack resolved, **repelled** (owner unchanged, operative lost, 1 AP spent).
- 80x24 matrix and burglary: both render and are playable (matrix actions available,
  burglary interior 4-row map, leave via exit).
- Runner death: forced `DEAD` on a live fight → app printed "Runner has died. Game over."
  (`end_run_dead`), confirming the TUI path.

## Still not covered (and why)

- **Crew hiring** — needs a runner drinking in the bar at the hour you arrive. Across 14
  rests at one bar and a 38-seed scan at hour 0 no runner was drinking where we stood;
  the state flips every 8 hours and travel moves the clock. Not a defect, just a timing
  precondition we did not line up.
- **Safehouse purchase / cyberware install / cyberdeck purchase** — all cost more than
  the starting 100eb (safehouse 325+, cyberware 700+, deck 350+). We did not run a money
  run first. Browse/refusal paths are covered.
- **Corp Sabotage** — no `sabotage_targets` on day 1 for the seeds tried.
- **Corp defeat** — requires losing all districts; none of the 120-day soaks got there.
- **Player-initiated attack capture** — the one attack we could launch was repelled.
- **Shop selling** — not exercised.
- **GUI save/load, corp mode, smuggling** — unimplemented in the slice, not test gaps.

## Notes

- Plain `uv run pytest` skips the GUI tests; only `uv run --group gui` covers them.
- The GUI session starts on the engine's bare default character (all stats 1, no gear,
  no skills); there is no creation flow. Successful jobs are possible (seed 940005) but
  the GUI is effectively a debug client until creation/save exist.
