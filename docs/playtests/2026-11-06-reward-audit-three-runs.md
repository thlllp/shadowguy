# Shadowguy — three reward-audited runs

Date: 2026-11-06
Seeds: **810001** (runner/gig/job), **810042** (runner/security/fishing), **810003** (corp)
All runs headless `run_test()` on the real boot path (no Test menu). Saves in
`/tmp/opencode/allie/saves`; drivers `/tmp/opencode/allie/runA.py`, `runB.py`, `runC.py`.

## Verdict

Three seeded sessions, each snapshotting character/corp state before and after every
reward-bearing action. **Every reward path paid out.** No missing cash, XP, rep,
standing, research points, or income found. One over-reward note (security completion
bonus on a contract whose nights all failed) is documented as intended in the code.
No crashes.

## Direct answer: have we been rewarded?

Yes. Audited deltas:

| Action | Run / seed | Reward observed |
|---|---|---|
| Gig success (`Lot Work`) | 810001 | **+80eb**, **+1 local standing** |
| Gig failure (×2) | 810001 | −1 rep, −1 local standing (no cash — expected) |
| Legwork prep | 810001 | **+2 advantage banked** (no cash; consumed by the job's first check) |
| Fixer job (`Heist: Prometheus (Moorland)`) | 810001 | **+325eb, +10 XP, +1 rep**, faction standing shifts, −2 health |
| Security night success | 810042 | **+35eb** each (nights 3 and 6 of 10 rests) |
| Security night failure | 810042 | 0eb (expected), −2 health net +1 rest heal |
| Fishing catch | 810042 | **+30eb** and **+50eb** on two casts; 0 on the miss |
| Corp daily income | 810003 | **+190eb/day** (7 districts, Worker-Surveillance-less) |
| Corp research | 810003 | **+1.0rp/day**, **+1.5rp/day** after the assistant lands |
| Academy research grant | 810003 | **+2rp for −250eb, −1 AP** |
| Expand | 810003 | −720eb, −1 AP, +1 district (charge correct) |
| Train research assistant | 810003 | −100eb, −1 AP, lands on day 4 (correct) |
| Save/load | 810003 | cash/RP/AP/day identical after reload |

Cross-check from the previous session (same code): smuggling delivery paid **+250eb and
+2 gang standing**; hospital healed; shop purchases deducted exactly once.

The only case that *looked* like a missing reward previously — a security contract where
cash stayed flat — was failed watch rolls paying 0 by design. Successful nights do pay.

## Runs

### Run A — gig/job rewards — seed 810001, Infiltrator, 100x40
Gig at Refinery (success +80eb, standing +1), two failed gigs (rep/local −1 each), then
Stitch's heist at Moorland: legwork banked +2 advantage, job paid +325eb/+10xp/+1rep with
−2 health. Job list cleared afterwards. No reward missing.

### Run B — security/fishing rewards — seed 810042, Infiltrator, 100x40
Switchblade Sal's sight contract (4 nights × 35eb, +70 bonus) at Pacifica; on-site rest
cost 0. Over 10 rests, two successful watches paid +35 each, failures paid 0; contract
reached 3/4 nights (an 8h rest does not always cross midnight). Then Copperhead Docks:
two catches paid +30 and +50. No reward missing.

### Run C — corp economy — seed 810003, Ironclad, 80x24
Expand (−720eb/−1 AP), train RA (−100eb/−1 AP), seven day ticks at +190eb and +1.0→1.5rp,
four grants (+2rp/−250eb/−1 AP each), save/load exact. Ledger visible at 80x24 on this
seed (`#corp_info` y=19 h=4, all four lines).

## Balance / feel

- **Security completion bonus is paid even when every night failed.** On the previous
  seed (20261106) three failed nights still paid the 52eb bonus on completion. The code
  comments say this is deliberate ("you served the term"), but as a player it reads as
  being paid for a job you never actually did. Worth a design look, not a bug.
- **Security night pacing is slow.** A rest is 8h, so from 6 AM it takes three rests to
  cross one midnight; four nights is ~12 rests. Correct, but it makes a 4-night contract
  a long sit.
- Corp payback: a 720eb expansion adds ~30eb/day of income; about 24 days to pay for
  itself before upkeep. Fine at this scale.
- Gig rewards (80/110/150) are small next to a first job's 325eb; the job is clearly the
  intended income step.

## Coverage

Run A: creation, map travel, gigs (success/failure), fixer offers, job accept, legwork,
job resolution, reward deltas.
Run B: fixer security offer, contract accept, on-site nights, nightly pay, fishing.
Run C: corp expand/train/income/research/grant/day ticks, 80x24 ledger, save/load.

Not covered this pass: smuggling (verified in the prior session), corp research-tree
purchase, operative tasking, a real gang/corp fight, runner death.
