# Shadowguy — five runs on the Arcade front-end build

Date: 2026-11-06
Build: **7c761e4** (`feat: add Arcade graphical front-end slice`), plus the working-tree
`gui/window.py` `view`→`top_view` rename.
Suite: `uv run --group gui pytest -q` → **24,838 passed** (283s). (A plain `uv run pytest`
skips the GUI tests via `importorskip("arcade")`; the GUI is only covered when the group is
installed.)
Saves/drivers: `/tmp/opencode/allie/`.

## Verdict

Five seeded sessions: two TUI baseline runs and three GUI runs. **No crashes or
tracebacks.** The TUI is unchanged and healthy. The new GUI plays the map → fixer → job →
scene → tactical/matrix/burglary slice end to end, but has **two P2 defects and two P3
defects**.

## Findings

### [P2] GUI: `S` opens the shop menu instead of moving down (WASD conflict)
- Seed: 930003
- Screen: GUI `MapView` (seed 930003, granary, south neighbour `trawlline`)
- Steps: stand on a district with a south neighbour; press `S` (the footer advertises
  "Arrows/WASD move" and "S shops"); then press `Down`.
- Expected: `S` moves the cursor down, like `A`/`W`/`D` move the other directions.
- Actual: pressing `S` pushes `ShopPickView` (or sets "No shop here."); the cursor does not
  move. `Down` arrow still moves to `trawlline`. `S` is consumed by the shop shortcut
  before the WASD block in `MapView.on_key`, so WASD down movement is impossible.
- Repro: yes, 1/1 (code path is unconditional).

### [P2] GUI: a fixer's offer already taken by a rival is listed and can be accepted
- Seed: 930004
- Screen: GUI `FixerView`
- Steps: rest four days from seed 930004; Rook (Ventway) keeps a "Sabotage: Ghostwire
  Collective (Sumpline) (8h)" offer with `taken_by = runner_specter`; open `FixerView`,
  select that row.
- Expected: the TUI labels it "TAKEN, <runner> got there first" and refuses (warning
  "already took that one"). The GUI should match.
- Actual: the GUI lists the offer as a normal job (`Sabotage: … (8h)`) and `pick` appends
  it to `accepted_jobs` (`msg Accepted …`). `GameSession.accept_offer` does no `taken_by`
  check, and `FixerView._build` iterates `fixer.offers` rather than `open_offers`. A player
  can run a job a rival already completed.
- Evidence: `found (4, 'Rook', 'ventway', 'Sabotage: … (Sumpline)', 'runner_specter', …)`;
  `listed? [('offer:…', 'Sabotage: … (8h)')]`; `accepted? 1`.
- Repro: yes, 1/1.

### [P3] GUI: unaffordable program purchase reports "Bought <program>."
- Seed: 930004
- Screen: GUI `ShopView` (Coldsnap Chip Shop, computer_store)
- Steps: with 100eb, select "Sleaze — 320eb".
- Expected: a "can't afford" message; no purchase.
- Actual: cash stays 100, `owned_programs` unchanged, but `shop.message` becomes
  `"Bought sleaze."`. `buy_program` returns a `"Can't afford …"` string, and `ShopView.pick`
  only special-cases the "isn't available"/"Already own" strings before falling through to
  the success message.
- Repro: yes, 1/1.

### [P3] GUI: arrow keys are ignored while aiming
- Seed: 930005
- Screen: GUI `TacticalView`
- Steps: begin an attack (`F`) with an adjacent enemy; press `Left` / `Right`.
- Expected: the aim cursor moves, as it does in the TUI (`action_move` moves the aim
  cursor while aim is open).
- Actual: `TacticalView.on_key` returns early in the `self.aiming` branch for every key
  except Tab/Enter/Escape, so the cursor stays on the initial target (`(5,4)` →
  `(5,4)`). Only Tab re-targets; you cannot free-aim a tile.
- Repro: yes, 1/1.

## Runs

1. **TUI runner, seed 930001** (100x40, Infiltrator): three gigs all failed (rep/local −1
   each), Doc Vex heist succeeded: **+250eb, +10xp, +1rep**, faction shifts, legwork banked
   +4 advantage, job cleared. No regressions.
2. **TUI corp, seed 930002** (80x24, Ironclad): expand −720eb/−1 AP, train RA −100eb/−1 AP,
   seven day ticks at **+190eb/+1.0→1.5rp**, four grants (**+2rp/−250eb/−1 AP**), ledger
   visible at 80x24 (`Logistics` + `Research Facility`), save/load exact. No regressions.
3. **GUI core, seed 930003**: rest via `R` (0→8h), S-key conflict found, travelled 5 hops
   to Switchblade Sal, accepted a job, travelled to Foundry, ran the scene to completion
   (failed job: −1 rep, −4 health, no pay — expected), map/fixer draw paths smoke-passed
   (2,141 draw calls).
4. **GUI shops/encounters, seed 930004**: computer-store stock, unaffordable-program
   message bug, bought pistol rounds (100→60eb, 50 rounds), forced gang toll paid
   (60→20eb, +1 standing) and refused into `CombatView` (Victory), shop/gear/toll draw
   smoke passed. The same seed's day-4 Rook board produced the taken-offer bug.
5. **GUI combat views, seed 930005**: tactical fight won with the bare (unarmed)
   character, matrix run seized, burglary entrance → interior `TacticalView` opened and
   rendered; aim-arrow bug found. Draw smoke passed (1,578 calls).

## Balance / feel / limitations

- The GUI session starts on the engine's bare default `Character` (no archetype, no gear,
  no skills), so `InventoryView` is empty and the runner panel is a blank build. There is
  no creation flow in the GUI slice. Documented as a slice, but it makes the GUI feel like
  a debug viewer rather than a playable client.
- The GUI has no gigs, legwork, security resolution, save/load, or day-tick notifications;
  map/fixer/job/shop/combat only. Fine for a slice, worth stating in the README.
- The TUI's economy and rewards are unchanged: job +250eb/+10xp, corp +190eb and +1.5rp
  per day, grants +2rp.

## Coverage

Run 1: TUI creation, map, gigs, fixer, job, legwork, rewards.
Run 2: TUI corp expand/train/income/research/grant/day ticks/save-load/80x24 ledger.
Run 3: GUI map, cursor, rest, shops shortcut, travel, fixer picker, offer accept, job
scene, draw paths.
Run 4: GUI shop stock/purchase, ammo, gear view, toll pay/refuse, abstract fight, taken
offer, draw paths.
Run 5: GUI tactical (aim/fire/end), matrix (move/fight/extract), burglary (entrance,
interior), draw paths.

Not covered: GUI corp mode (the GUI builds a runner `GameSession` only), GUI save/load,
GUI security contracts/smuggling, GUI toll corp branch, and a full GUI job success (the
run-3 job failed its rolls).

## Known issues table

| Issue | Status |
|---|---|
| TUI: ledger / panels / Rest / transient saves / off-site notify | fixed, verified |
| GUI: S key shadows WASD down | open (7c761e4) |
| GUI: accepted a rival-taken offer | open (7c761e4) |
| GUI: "Bought <program>" on failed purchase | open (7c761e4) |
| GUI: arrows ignored while aiming | open (7c761e4) |
