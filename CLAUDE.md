# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

Minimum code that solves the problem. Nothing speculative.

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

Touch only what you must. Clean up only your own mess.

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Project: Shadowguy

A text-based cyberpunk roguelite TUI. Python 3.14, managed with `uv`, built on Textual.

### Core concept

Two coupled game modes, not one game with a reskinned second mode:

- **Runner mode** — RPG scale. One character, stats, scene-based missions, permadeath.
- **Corp mode** — 4X scale. Player controls a corp, area-control/resource game against rivals. Not yet implemented.

Switching between runner and corp is optional and meant to be difficult — neither mode is a straight upgrade over the other, each has distinct challenges.

### Run/game-over rules

- No meta-progression between runs (for now). Each run starts fresh.
- Runner mode ends when the character dies.
- Corp mode ends when the corp is destroyed, taken over, or the character is assassinated.

### Stats

`Body`, `Skill`, `Cool`, `Cash`, `Rep`. Health is a separate pool from Body but scales with it (`10 + body * 5`). Expect these to eventually split into finer-grained skills — don't over-fit code to exactly five flat stats.

### Check resolution

Randomized: `d20 + stat vs difficulty`. Natural 20 = critical success, natural 1 = critical failure, regardless of total.

### Runner-mode activity types (`shadowguy/scene.py`, `shadowguy/content.py`)

All three share the same `Scene`/`Stage`/`Choice`/`Outcome` data model, distinguished by `Scene.kind`:

- **Mission** — multi-stage scene-based job, choices branch on stat checks, failure can end the mission early.
- **Gig** — small single-stage activity for quick resources.
- **Legwork** — single-stage prep activity that banks an `advantage` bonus for a *specific* mission (`Scene.prepares_for`), consumed on that mission's first check only, then gone. `Character.advantage` is a `dict[mission_id, int]`, not a flat global bonus — advantage from one mission's legwork can't leak into an unrelated mission or gig.

### Codebase layout

```
src/shadowguy/
  character.py   Character dataclass: stats, health, advantage bank
  checks.py       resolve_check(): d20 + stat vs difficulty
  scene.py        Scene/Stage/Choice/Outcome data model, resolve_choice()
  content.py      actual mission/gig/legwork data
  app.py          Textual App: MainMenu + SceneScreen
```

### Known Textual gotchas hit so far

- `ListView.clear()` returns an `AwaitRemove` — it must be awaited, or a following `.append()` can race the removal and raise `DuplicateIds`. Handlers that clear-then-repopulate a `ListView` must be `async def`.
- `ListView.index` becomes `None` after `clear()`; set it explicitly (e.g. `.index = 0`) after repopulating or keyboard selection (`enter`) has nothing highlighted to act on.
- `Static` renders its string as Rich markup by default — literal square brackets (e.g. `"[Legwork]"`) get parsed as markup tags and silently vanish. Avoid `[...]` in label text, or escape/disable markup.
- `Screen`'s resume hook is the public `on_screen_resume` (message `handler_name` is public even though `Screen` itself also defines a separate private `_on_screen_resume` for internal bookkeeping) — override the public one to refresh a screen's content when it's popped back to.
