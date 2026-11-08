# Playtest reports

Archived reports from the `allie` playtester. Each session pins a seed, quotes the
rendered screen or state it observed, and separates defects from balance/feel.
Reports are ordered by the build they were run against, not by filename.

| Report | Build | Verdict |
|---|---|---|
| `2026-11-06-runs-555001-555002.md` | pre-`2c3fd19` | corp-only map clips actions/ledger at 80x24 (P2); Research Facility line hides its own rate (P3) |
| `2026-11-06-coverage.md` | pre-`2c3fd19` | gang toll / abstract combat, corp defeat, security contracts covered; no new defects |
| `2026-11-06-review.md` | `2c3fd19` | review of the first fix round: 7 findings fixed, 80x24 runner map only partly closed |
| `2026-11-06-postfix-90210.md` | post-`2c3fd19` | fixes hold in real play; no regressions |
| `2026-11-06-20261106.md` | `76f58e7`-era | runner session: mid-fight save (P2), Rest discoverability and off-site no-op (P3) |
| `2026-11-06-20261107.md` | `76f58e7`-era | corp session: ledger clipped/unscrollable and invisible focusable panels (P2), Rest hint (P3) |
| `2026-11-06-three-runs.md` | `76f58e7`-era | corp-tab panel repro; correction: the Rest key does exist |
| `2026-11-06-review-2.md` | `76f58e7`-era | review of round 2: corp ledger and invisible-panel findings verified |
| `2026-11-06-reward-audit-three-runs.md` | `76f58e7`-era | every reward path paid; no missing cash/XP/rep/research |
| `2026-11-06-updates-reruns.md` | `010a48f` | all five open TUI findings fixed and verified |
| `2026-11-06-gui-five-runs.md` | `7c761e4` | GUI slice: two P2 and two P3 defects |
| `2026-11-06-cover-everything.md` | `7c761e4` | GUI sweep: one P1 (dead runner keeps playing), four P2, two P3 |
| `2026-11-07-five-runs.md` | `e891049` | post-fix audit: corp intel/sabotage regression and burglary picker ordering (P2); death/KO/taken-offer fixes verified |
| `2026-11-07-five-runs-2.md` | `5b8cba3` | both P2s verified in real play; three P3s re-confirmed; crew hiring and safehouse purchase/build now covered |
| `2026-11-07-five-runs-3.md` | `1dc8f16` | all open fixes verified; live capture and a blown security contract now covered; no new defects |

## Status at `1dc8f16`

**No open findings.** Fixed and verified across the reports: the TUI findings
from the `2c3fd19` → `010a48f` sequence (corp ledger and panels at 80x24, Rest
key, transient saves, off-site notify); the GUI death/knockout handling,
knockout-as-victory, burglary starting stages, rival-taken offers; the two
`e891049` P2s (burglary picker ordering `3263118`, map-panel intel/sabotage
`47382c4`); the three GUI P3s (footer, unaffordable-program message, aim arrows,
`f6e9929`); and sabotage-at-zero-Security being withheld (`9779ebf`).

Coverage now includes: crew hiring and the wage tick, safehouse purchase and
workshop build, cyberdeck/program and cyberware purchase/install, shop selling,
crafting, corp-map intel and sabotage execution, corp attack, rival district
losses, 80x24 matrix and burglary, the GUI death/KO/burglary/entrance and
save/load flows, a live player capture, and a security contract blown through
the app day tick.

Remaining gaps: the GUI's **unimplemented** corp mode, smuggling and character
creation — preview limits, not test gaps.

Corp defeat was re-verified on this build with a passive corp-only soak (seed
`555002`, Ironclad): 6 districts → 0 by day 104, `"Ironclad Dynamics has been
broken up. Game over."` That closes the last mechanical playtest gap.

Design note (decided): a capture can leave the taken district at garrison 0 when
every committed operative dies (`attacker_losses = min(committed, defense)`). That
is an intended pyrrhic-capture risk, not a bug — committing more than the defense
is what leaves a garrison, and the force picker now says so. See DESIGN.md's Corp
conflict section.

## Running again

Reports are written by the `allie` subagent to `/tmp/opencode/allie/reports/`.
Copy a finished session here before it is lost; `/tmp` is ephemeral. Keep the
seed, build commit and repro count in the file.
