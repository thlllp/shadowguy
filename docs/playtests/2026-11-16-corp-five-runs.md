# Shadowguy — five corp-only runs (2026-11-16)

Build: **`9965e7d` + working tree (`corp research formatting fix`)** — the dirty
`src/shadowguy/screens/corp_screen.py` (`:g` on `research_points` in
`corp_info_text`) and `tests/test_app_flows.py` regression test were tested as-is.
Suite before play: `uv run pytest -q` → **24,441 passed, 3 skipped** (~6m20s), green.

Seeds: **815004, 815006, 815009, 815001, 815002** (fresh corp-only runs; none reused
from 720001–720004). Sizes: two × 100×40, one full 80×24, one full 90×28, one
100×40 soak. All runs `New Game → Corp → faction`.

Verdict: **five corp-only sessions, roughly 15–35 meaningful actions each; no P0/P1/P2.**
Three P3s are confirmed (pool-cap refusal copy, map-mode save/load reopening the Corp
tab, and a singular/plural training toast). The working-tree research-formatting fix is
verified in real play (`6rp`/`6.5rp` on both surfaces, never `6.0rp`). Facility loss and
the rebuild recovery both behaved as designed; intel/sabotage and a repel were exercised
end-to-end. Attack capture was not observed (repel recorded; capture marked untested).

## Verified this session (from the first-round runs and probes)

- **Expand onto neutral ground** claimed the district, charged the listed cash and
  exactly 1 AP (seed 815001, day 1).
- **Cash-only bump** (`Develop Longshore`) took cash and left AP unchanged.
- **Expand at 0 AP** was refused with `No action points remaining today.`; cash,
  owner and AP all unchanged.
- **Attack with 0 operatives** was refused with `No operatives to send. Train some at
  the Academy.`; no `ForcePickScreen`, no state change.
- **Rest** crossed exactly one day (day 1 00:00 → day 2 06:00), refilled AP to 2, paid
  income (`Day 2 income: +160eb, +1rp.`) and accrued 1rp; no double tick.
- **Sheet and ledger agree**: `Cash: 960eb` / `— 960eb —`, `Research: 1rp` / `— 1rp —`.
- **Research formatting fix**: at a natural `6.0` RP (seed 815001, day 5) the sheet
  read `Research: 6rp`, the ledger read `— 6rp —`, and `6.0rp` appeared nowhere. A
  deterministic value probe on seed 815002 also showed `6.0 → 6rp` on both surfaces,
  `6.5 → 6.5rp` on both, and `7.25 → 7.25rp` on both (never a trailing `.0`).
- **Academy research grant**: +2rp, −250eb, −1AP.
- **Operative training**: queued with `ready_day = 8` (6 days), cash −200, AP −1; while
  training the Academy panel was suppressed to the single `pending_recruit` row; the
  batch landed on day 8 (`operatives 1`, `pending None`). In run E, a pending batch
  survived save/load with `ready_day 13` intact.
- **Worker Surveillance**: refused at 0rp with `Not enough research points.` and no
  mutation; bought for 10rp once affordable; `researched` grew; the flat Corp list then
  offered `surveil_<district>` rows; a surveillance bump cost 400eb and spent **no AP**.
- **Intel and sabotage from the map panel** (seed 815001, day 7–9): the rival-district
  panel offered `intel_` and `sabotage_`; intel dispatched one operative (tasking 1,
  AP −1) and it returned next day; sabotage lowered Security 1→0, spent AP and returned
  the operative next day; at Security 0 `sabotage_` was no longer offered while
  `intel_` still was.
- **Attack repel** (seed 815001, day 9): one operative attacked a Security-0 district;
  `Driven off Bastion — 1 lost, 1 of theirs down.`, owner unchanged, AP 2→1.
- **Multi-day soak** (seed 815002, days 17→21): four Rests each crossed exactly one
  day, refilled AP to 2, paid `+150eb` and `+1rp`, and reported sightings and rival
  claims/seizures; no double tick.
- **Save/load** restored cash/RP/AP/day/districts and `corp_only` from a map-mode save,
  including a pending recruit mid-batch.
- **Research tree** (`t`) opened, rendered tiers and repeatable project, and survived an
  unaffordable selection without changing state.
- **ForcePickScreen** opened for deploy/attack and applied the chosen count.
- **Deploy** moved 1 operative into a garrison and spent 1 AP.
- **Facility loss and rebuild** (seed 815009, 90×28): a rival naturally captured the
  facility-hosting district on day 3; RP froze at 1.0 and `collect_research` read 0.0;
  the Research panel offered `rebuild_<district>`; `rebuild_blister` spent exactly
  3000eb + 1 AP and RP accrued again (1.0→2.0) on the next tick.
- **80×24 layout**: the ledger renders both `Logistics:` and `Research Facility:` lines;
  Tab cycles the Corp-tab panels and scrolls Research/Surveillance into view; the flat
  activity list scrolls to the reinforce rows. Evidence captured with `render_strips()`.

## Findings

### [P3] Training at the operative pool cap says "Can't afford it."
- Seed: **815002** (Ghostwire), day 13, 100×40
- Screen: `CorpMapScreen` Corp tab → Academy panel `train_operative`
- Steps: 1. Train a first operative batch (day 1 → lands day 7). 2. Train a second
  batch (day 7 → lands day 13), reaching `operatives == operative_max == 2`. 3. Select
  `Train operatives (6d) — 200eb` again.
- Expected: a refusal that names the real gate (the pool is full), e.g. "The operative
  pool is full — deploy some first."; no cash or AP spent.
- Actual: nothing changes (`pending_recruit None`, cash 3400, AP 2), but the toast is
  **`Can't afford it.`** even though 200eb is trivially affordable and 2 AP are free.
  `train_employees` fails on `operatives + count > operative_max`, and
  `CorpActionsMixin._row_train` falls through to `_notify_refusal()`, whose non-AP
  fallback is the cash message.
- Evidence: `TRAIN_AT_CAP ok True cash 3400 ops 2 op_max 2 ap 2 pending None notes
  [("Can't afford it.", 'warning')]`; state unchanged. (Toast text captured from
  `app.notify`; Textual notifications sit outside the `render_strips()` compositor.)
- Repro: yes, 2/2 — run E naturally reached the cap (cash 3400, ops 2); a targeted
  replay on the same seed with the pool set to 2 produced the same toast
  (`POOL_CAP_REPLAY ops 2 cap 2 cash 2000 ap 2 notes [("Can't afford it.",
  'warning')] unchanged True`).

**Acceptance criterion:** selecting `train_operative` with `operatives == operative_max`
shows copy naming the pool cap and spends nothing; it must not say the player cannot
afford a 200eb batch while holding 3400eb.

### [P3] Load after escaping a category tab reopens the Corp tab
- Seed: **815004** and **815006**, day 2, 100×40 / 80×24
- Screen: `CorpMapScreen` Corp tab → `escape` (map mode) → `q` Save → Load
- Steps: 1. Boot corp-only Prometheus. 2. Open the Corp tab (`r` / button). 3. Press
  `escape` to return to map mode (`selected_category None`). 4. Save. 5. Load the save.
- Expected: the save reopens map mode, the screen on top when it was written.
- Actual: `MODE_AT_SAVE None main_menu_category corp`; after load
  `screen CorpMapScreen category corp main_menu_category corp` — the Corp tab is
  restored, not the map. State is intact (`1760eb`, `1rp`, `2 AP`, day 2, 7 districts,
  `corp_only True`), so this is a screen mismatch, not lost state.
- Cause (read-only): `CorpMapScreen.action_back` clears `selected_category` but not
  `app.main_menu_category`; `CorpMapScreen.__init__` deliberately restores the
  last-viewed category on every rebuild.
- Evidence: `MODE_AT_SAVE None main_menu_category corp` → `RELOADED ... screen
  CorpMapScreen category corp`; the rendered post-load screen is the Corp action list,
  not the map.
- Repro: yes, 2/2 — run A (seed 815004) and run B (seed 815006, 80×24), both saved in
  map mode after visiting the Corp tab and both reloaded with `category corp`.

**Acceptance criterion:** after saving in map mode (no category tab showing), Load
reopens `CorpMapScreen` in map mode — or the intent to restore the last-viewed tab is
stated in the report as intended and the run is not counted as a mismatch.

### [P3] "1 new operatives report for duty."
- Seed: **815002** (Ghostwire), day 7 and day 13, 100×40
- Screen: `CorpMapScreen` day-tick toast (after Rest)
- Steps: 1. Train an operative batch (Academy tier 1 trains exactly 1). 2. Rest through
  the batch's `ready_day`.
- Expected: `Training complete: 1 new operative reports for duty.` (singular agreement).
- Actual: **`Training complete: 1 new operatives report for duty.`** — the count is
  singular but `employee_plural` always pluralises. Seen twice in the same run (day 7
  batch 1, day 13 batch 2).
- Evidence: `DAY7_TICK 7 [... ('Training complete: 1 new operatives report for duty.',
  None)]`; `TRAINING_TOASTS_2 ['Training complete: 1 new operatives report for duty.']`.
  (Toast text captured from `app.notify`; notifications are outside `render_strips()`.)
- Repro: yes, 2/2 ticks in one run (deterministic for a tier-1 batch).

**Acceptance criterion:** a completed batch of one reads "1 new operative …" (singular),
while a batch of two or more keeps the plural form.

## Runs

- **A — 100×40, seed 815004 (Prometheus), day 2.** Booted Prometheus
  (`faction_prometheus`, 6 districts, `reclaim_research` facility, 2000eb, 2 AP).
  Opened the Corp tab. **Expand**: chose the cheapest of six neutral candidates,
  `expand_distillery` (400eb) → cash 2000→1600, AP 2→1, owner → Prometheus.
  **No develop/secure rows were offered on this seed** (all held districts had
  Security/Surveillance below the 3/3 Development requirement, and the two bump
  techs were unresearched), so the cash-only-bump check was carried by the earlier
  seed-815001 `Develop Longshore` run and the Worker Surveillance bump in the same
  first round. **Survey** `Catwalk` spent the last AP (1→0). **Expand at 0 AP**
  refused with `No action points remaining today.`; cash 1600, owner neutral, AP 0
  all unchanged. **Rest** → day 2 06:00, exactly one day, AP refilled to 2, income
  `Day 2 income: +160eb, +1rp.`, RP 1.0; sheet/ledger both read `1760eb`/`1rp`.
  **Research tree** (`t`) opened; Worker Surveillance refused at 1rp with
  `Not enough research points.` and no state change. **Phone** (`c`) and **run menu**
  (`q`) opened and escaped cleanly. **Save/load in map mode** restored cash/RP/AP/day/
  districts/`corp_only` exactly, but reopened the Corp tab (finding above). Root tech
  not yet affordable at 1rp.
- **B — 80×24, seed 815006 (Ironclad), day 1→2.** Full layout pass at the documented
  minimum. Map mode renders the ledger as four complete lines:
  `Ironclad Dynamics — 2000eb — 0rp …`, `Territories (6): …`,
  `Logistics: 6/14 districts supplied`,
  `Research Facility: tier 1 (1rp/day) · 0/1 sci (+1) · 0/2 asst (+0.5)`.
  Corp tab renders five activity rows (the full list has 16 and scrolls) plus the
  Academy panel's first rows. **Tab** walked
  `cat_phone → cat_tech → activities → corp_panels → Academy title → academy_list →
  Research title → research_list → Surveillance title`; focusing `research_list`
  scrolled the `Research Facility` box into view, and the next Tab put
  `▶ Surveillance Log` on the bottom row; Enter expanded it and `No sightings yet.`
  rendered. Scrolling `#activities` to its last row (index 14) moved
  `scroll_offset y=10` and painted the `Reinforce …` rows. **Rest** → day 2 06:00,
  `Day 2 income: +150eb, +1rp.`, AP 2. **Save/load** from map mode restored state
  exactly (`2150eb`, `1rp`, 2 AP, day 2, 6 districts, `corp_only True`) but reopened
  the Corp tab — the second repro of the P3 above.
- **C — 90×28, seed 815009 (Prometheus), day 1→10.** Booted with the facility on
  `leadworks` (`leadworks_research`, 1rp/day) and the ledger showing the
  `Research Facility:` line. Rest day 1→2 accrued 1rp. On the **day 3 tick Meridian
  Biochem captured Leadworks** (`Meridian Biochem has taken Leadworks from you.`,
  owned 6→5) — the natural loss the seed screening predicted. From then
  `owned_research_facility` was `None`, `collect_research` 0.0, and RP stayed at
  `1.0` across the next day; the ledger no longer printed a `Research Facility:` line.
  Soaked to day 9 (cash 3000, four districts left after two more captures). The Corp
  tab's Research panel offered `rebuild_blister`, `rebuild_gallows`,
  `rebuild_junction`, `rebuild_lowline`; choosing `rebuild_blister` spent exactly
  3000eb and 1 AP (`3000 2 -> 0 1`) and restored a facility
  (`New Research Facility standing in Blister.`). The next Rest (day 10) accrued
  `1.0 → 2.0rp`, AP refilled to 2, and the ledger's facility line returned. Sheet and
  ledger agreed at `100eb` / `2rp`.
- **D — 100×40, seed 815001 (Ironclad), day 1→9.** Booted bordering Meridian (`bowery`)
  and Prometheus (`scrapline`). **Attack with 0 operatives** was refused
  (`No operatives to send. Train some at the Academy.`, no ForcePick, state unchanged).
  **Trained one operative** (ready day 7) and rested to day 7; rivals had captured
  Bastion (Meridian) and Tideline (Prometheus) overnight, leaving rival-held border
  districts `bastion` (Security 1) and `tideline` (Security 0); the facility survived.
  Moved the map cursor onto `bastion` with arrow presses; the map panel offered
  `survey_bastion`, `attack_bastion`, `intel_bastion`, `sabotage_bastion`.
  **Intel** dispatched the operative (`ops 1→0`, `tasking 1`, AP 2→1) and naturally
  failed (`The operative came back empty-handed.`); the next Rest returned it
  (`1 operative(s) returned from tasking.`, tasking 0, AP 2). **Sabotage** on the same
  district (success forced with `ForcedChance(0.1)` to exercise the return path)
  lowered Security `1→0`, spent AP 2→1, and the operative returned next day (day 9).
  With Security 0 the refreshed panel showed `survey_bastion`, `attack_bastion`,
  `intel_bastion` and **no `sabotage_bastion`** — correct. **Attack** with the one
  remaining operative opened `ForcePickScreen` (`force_1`); it was **repelled**
  (`Driven off Bastion — 1 lost, 1 of theirs down.`), owner stayed Meridian, AP 2→1.
  Two sightings were in the log at day 7; the day-9 tick logged one more.
- **E — 100×40, seed 815002 (Ghostwire), day 1→21.** Booted with Academy at `gloaming`
  and facility at `quill`; **no rival border**, so the 0-operative attack refusal was
  carried by run D (recorded, not a defect). **Unaffordable tech** refused
  (`Not enough research points.`, nothing changed). **Trained operative batch 1**
  (day 1 → day 7); while training the Academy panel showed only `pending_recruit`, so a
  second batch is suppressed. The **day-7 tick** read `Training complete: 1 new
  operatives report for duty.` (finding). **Trained batch 2** (day 7 → day 13) and
  **saved/loaded mid-training**: `PendingRecruit(count=1, ready_day=13)` survived with
  day/cash/AP exact. Day-13 tick landed the second operative (pool 2/2) and repeated
  the singular/plural toast. **At the cap**, selecting `train_operative` produced
  **`Can't afford it.`** with 3400eb and 2 AP free (finding); nothing was spent.
  Bought the 2000eb lab, then spent cash on Academy grants and Rest until cash reached
  0; **expanding into Southgate (560eb)** was then refused with `Can't afford it.`
  and nothing changed. **Four-day soak** (days 17→21): every Rest crossed exactly one
  day, refilled AP to 2, paid `+150eb` and `+1rp`; sightings appeared on day 20 and
  rival claims/seizures were reported (`Ironclad claimed Pitchfork`, `Meridian claimed
  Junction`, `Prometheus seized Ashgrove from Ironclad`). Final save/load restored the
  soak state exactly.

### Targeted probes (not counted as one of the five runs)

- **Research purchase/unlock**, seed 815001 day 14 (first-round session, same build):
  RP 15 → 5 after buying Worker Surveillance; `researched == ['worker_surveillance']`;
  the flat Corp list then listed `surveil_bastion`, `surveil_distillery`,
  `surveil_firebreak`, `surveil_longshore`, `surveil_pacifica`, `surveil_revetment`,
  `surveil_stopgap`, `surveil_tideline`; a bump on Bastion cost 400eb and left AP at 2.
- **Research formatting**, seed 815002 (deterministic value probe): setting
  `research_points` to 6.0, 6.5 and 7.25 rendered `6rp`, `6.5rp` and `7.25rp`
  identically in the ledger (`— Nrp —`) and the sheet (`Research: Nrp`); no `6.0rp`.
- **Deploy**, seed 815001 day 8 (first-round session): `deploy_tideline` opened
  `ForcePickScreen` with `force_1`, moved the operative into the garrison
  (`garrison 1`, `operatives 0`) and spent 1 AP.

## Balance / feel

- **Cash-only sinks depend heavily on map seed.** Seed 815004 offered no `develop_` or
  `secure_` rows for the first two days (held districts were below the 3 Security / 3
  Surveillance Development requirement, and the two bump techs were unresearched), so
  the "cash-only action must not spend AP" check had to lean on other seeds. Not a
  defect — the tech gates are explicit — but a corp can go several early days with no
  cash-only way to improve ground.
- **80×24 map viewport is very cramped.** The ledger, summary, action strip and panels
  all fit, but in map mode only about one row of the 260-district board is visible;
  arrows still move the cursor and the view follows, and the Corp tab is fully usable.
  This reads as a feel issue at the documented minimum, not a blocker.
- **Rival pressure is real at this pace.** Seed 815001 lost Bastion and Tideline by day
  7 and was down to four districts by day 13; seed 815009 lost two districts over nine
  days. All losses were reported, and the day tick never doubled. No balance claim —
  just recording that a 12-day training plan can quietly cost a third of the map.
- **Losing the facility is a hard but recoverable stop.** On seed 815009 the day-3
  capture froze RP at 1.0; rebuilding cost 3000eb (about six days of income at four
  districts) and 1 AP, after which RP resumed at 1/day. Intended design; the rebuild row
  is the recovery and it works.
- **Training at the pool cap** is the only refusal whose copy is actively wrong
  (`Can't afford it.` at 3400eb). The rest of the refusal copy matches the real gate.

## Coverage

- **Reached this session:** five seeds/factions — Ironclad (815001, 815006), Ghostwire
  (815002), Prometheus (815004, 815009); expand/claim, AP burn and 0-AP refusal,
  0-operative attack refusal, cash-only Develop and surveillance bumps, Academy grant/training/suppression/pool cap, Worker
  Surveillance purchase and `surveil_` unlock, research formatting at 6.0/6.5/7.25,
  facility natural loss → stalled RP → `rebuild_` → RP resume, map-panel intel and
  sabotage (success and failure) with next-day returns, Security-0 sabotage exclusion,
  attack repel, save/load in map mode and mid-training (pending recruit), 80×24 ledger
  and panel Tab-scrolling, 90×28 rebuild flow, four-day soak with income/RP/AP/sightings
  and rival claims/seizures.
- **Never covered:** attack **capture** (the one attack was repelled; capture is a rare
  branch and is marked untested rather than claimed from a lucky roll); corp defeat
  (last district lost); Academy loss → `newacademy_` rebuild (seed 815001's academy
  was lost in an early exploratory soak, not in a report run); hybrid runner+corp
  `CorpScreen`; deeper tech tiers and Logistics Optimization; a full 80×24 multi-day
  session (run B was one Rest); a 100×40 run that buys a root tech from natural RP
  (Worker Surveillance was bought on the earlier seed-815001 session, not this round).
- **Driver/test weakness, not a game defect:** seed 815002 has no rival border, so the
  0-operative attack check could not run there (it ran on 815001); seed 815004 offered
  no cash-only bump rows on the days checked (the rule was verified on 815001/815006).

## Known issues

| Severity | Issue | Status |
| --- | --- | --- |
| P3 | `train_operative` at the operative pool cap says "Can't afford it." (seed 815002, day 13) | open |
| P3 | Save in map mode after visiting a category tab reloads onto the Corp tab (seeds 815004/815006) | open (possibly intended: `main_menu_category` is documented as last-viewed) |
| P3 | Day-tick toast says "1 new operatives report for duty." (seed 815002, days 7/13) | open |
| — | Facility loss stalling RP until `rebuild_` is built (seed 815009) | intended design; rebuild verified |

Probe module: `/tmp/opencode/allie/probe.py`; session drivers under
`/tmp/opencode/allie/` (`runA_final.py`, `runB.py`, `runB_tab.py`, `runC.py`, `runD.py`,
`runE.py`, `research_fmt_probe.py`, `pool_cap_replay.py`).
