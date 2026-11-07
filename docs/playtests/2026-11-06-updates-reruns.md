# Shadowguy — reruns on the updates

Date: 2026-11-06
Build: **010a48f** (`fix: corp small-screen UI and transient saves` / `chore: tune logistics sink…`)
Suite: `uv run pytest -q` → **24,828 passed** (297s), green before play.

Seeds: **910001** (runner, 80x24), **910002** (corp, 80x24), **20261106** (runner, 100x40,
the old mid-fight repro).
Drivers: `/tmp/opencode/allie/upd1.py`, `upd2.py`, `upd3.py`.

## Verdict

All five findings from the previous reports are fixed and verified on the updated build.
No new defects. Rewards still pay correctly.

## Fix verification

### 1. Corp ledger at 80x24 — FIXED
Seed 910002, 7 territories. `#corp_info` is `Region(x=0, y=19, width=80, height=4)`, and
the rendered screen contains both `Logistics: 7/15 districts supplied` and
`Research Facility: tier 1 (1rp/day) · 0/1 sci (+1) · 1/2 asst (+0.5)`. The territory line
is capped (`Territories (7): ... +3 more`), so it cannot wrap into a fifth line.
Repro: yes, 2/2 at 80x24.

### 2. Corp tab panels at 80x24 — FIXED
Seed 910002. The action list is 16 rows (no `intel_`/`sabotage_` entries) and `#activities`
is `y=8 h=5`. The three panels now live in `#corp_panels` (`y=17 h=6`, visible). Tabbing to
`academy_list` scrolls it into view (`Region(x=4, y=18, w=74, h=5)`) and "Research grant" is
readable before selecting it. Grant then moved cash 2140→1890, RP 4.5→6.5, AP 2→1 — an
intentional action taken with the row on screen.
Repro: yes, 2/2.

### 3. Rest key — FIXED
Seed 910001: `R` on the runner map advanced 0.0h → 8.0h. Seed 910002: four `R` presses
advanced day 1 → day 5. The field manual now contains the
`R                 map or corp screen: Rest` line (verified in the Help screen's content).
Repro: yes, 2/2.

### 4. Transient saves — FIXED
- Seed 910001, inside a gig SceneScreen: `q` → Save Game → QuitMenu stays open, no save
  file written, note `"Can't save during a scene or fight."`.
- Seed 20261106, real Ghostwire encounter at Garnet: refused the 130eb fine → live
  CombatScreen (`is_over == False`) → `q` → Save → same refusal, no file. Closing the menu
  returns to the **still-live** fight. After finishing it, saving on the map succeeds and
  load restores health/cash/day/location exactly (29hp, 425eb, day 4, Pressgang).
Repro: yes, 2/2 (scene and fight).

### 5. Out-of-district Job/Legwork row — FIXED
Seed 910001: accepted The Tallyman's job (target Blacktide), pressed `L` and selected the
row while standing in Lattice. Note captured:
`"You must be on site — travel to Blacktide."`; elapsed hours unchanged (18.0), no scene
pushed.
Repro: yes, 2/2.

### 6. Logistics tuning — verified
Seed 910002 research tree: `Logistics Optimization` reads
`150rp — +1 district supplied (level 0 → 1) (need 143.5rp more)`. (The purchase path was
verified at the old 200rp price; this pass checks the new label/cost, not a second 40-day
soak.)

## Rewards re-audit

- Gig success (seed 910001, "Off the Books" at Parapet): **+80eb, +1 local standing**.
- Corp grant (910002): +2rp / −250eb / −1 AP, exactly once.
- Corp income during four `R` rests (910002): +190eb/day, RP +1.5/day once the assistant
  landed; AP refilled to 2.
- Job completion (20261106 rerun): Ghostwire standing moved −2 as expected; the old job
  payout (+325eb, +10xp, +1rep) was unaffected by the save fixes.

No reward was missing in any of the three runs.

## Notes / still open

- Mid-scene/fight save refusal leaves the QuitMenu open; the player closes it with Escape.
  That matches the regression test and reads fine with the toast.
- The Corp tab's panels are inside a scroller now; before focusing, only the scroller's
  visible slice of the Academy list shows. Focusing/Tab scrolls the row into view, which is
  the intended pattern.
- Not retested this pass: purchasing Logistics at 150rp (needs ~40 in-game days), corp
  defeat, player-initiated attacks, operative attack rows under the new capped list.
- Prior fixes already verified on earlier builds (job/gig/security/smuggling/fishing payouts)
  were not regressed by these changes.

## Coverage

Run 1: 80x24 runner map, creation, `R` rest, gig scene + mid-scene save refusal, gig reward,
fixer job accept, off-site notify, map save, Help.
Run 2: 80x24 corp map, ledger, capped action list + panel scroller, Academy focus/grant, `R`
rest ×4, research-tree cost label, save/load.
Run 3: 100x40 runner, fixer heist, corp encounter/toll/refuse, live CombatScreen, mid-fight
save refusal, fight completion, map save/load.
