# AGENTS.md

**Read [`CLAUDE.md`](CLAUDE.md).** It is the single source of truth for how to work in this
repo — behavioural rules, the module map, module layering, save versions, testing
conventions, and the Textual gotchas.

Design rationale for every game system lives in [`DESIGN.md`](DESIGN.md). Read it before
changing game behaviour: the constants in those systems are frequently load-bearing, and
several were set against a balance simulation.

## Why this file is a pointer

This file used to carry a condensed copy of `CLAUDE.md`'s material. Duplicating it meant
duplicating the parts that rot — counts, version numbers, roster sizes — and the copy fell
behind: it claimed `SAVE_VERSION` 61 against an actual 70, six technologies against 31, and
a nine-runner pool against twelve. `CLAUDE.md` stayed correct because it is the file loaded
every session; this one only got updated when someone remembered.

So there is one copy now. Anything you would have added here belongs in `CLAUDE.md`.

## Quick start

```
uv run pytest -q              # test suite, run by CI on every push/PR to main
uv run ruff check src/        # lint (ruff pinned in the dev dependency group)
```
