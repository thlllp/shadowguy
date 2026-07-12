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
- **Corp mode** — 4X scale. Player controls a corp, area-control/resource game against rivals. Only the territory map exists so far (browsable preview, no turns/economy/conflict yet).

Switching between runner and corp is optional and meant to be difficult — neither mode is a straight upgrade over the other, each has distinct challenges.

### Run/game-over rules

- No meta-progression between runs (for now). Each run starts fresh.
- Runner mode ends when the character dies.
- Corp mode ends when the corp is destroyed, taken over, or the character is assassinated.

### Stats and skills (`shadowguy/character.py`, `shadowguy/skills.py`)

Six **core stats** (`character.CORE_STATS`): `Body`, `Strength`, `Agility`, `Perception`, `Intelligence`, `Cool`. Plus `Cash` and `Rep`, which are resources, not checkable stats — `STAT_NAMES` is the union, and `Character.stat()` only folds gear/chem bonuses into the six core ones. Health is a separate pool from Body but scales with it (`10 + body * 5`), off the raw attribute, so gear never moves max health.

**Nothing rolls a core stat directly.** Each stat carries five **skills** (`skills.SKILLS`, 30 total); a `Choice` names a *skill*, and `skills.skill_value()` is what the dice see: the skill's tied stat (gear and chems included) plus the rank the player invested in that specific skill. `Character.skill_ranks` is `dict[skill_id, int]`, fully populated (every skill starts at `STARTING_SKILL_RANK`), not sparse. A skill id that isn't in `SKILLS_BY_ID` raises from `skills.skill_for()`, which is the single chokepoint: `Scene.__post_init__` runs it over every choice, so a typo fails when the scene is *built*, not mid-roll.

`skills.py` is deliberately a **leaf module** — it imports nothing from the package at runtime, because `character.py → shops.py → corpmap.py` all end up importing it. That's why the "every `Skill.stat` is a real core stat" guard lives in `character.py` (the one module that can see both tables) rather than next to the skill table. Don't add a runtime `character` import to `skills.py`; it's a cycle.

`Rep` is global standing in the street. Separate from that, `Character.standing` is a `dict[faction_id, int]` of per-corp standing — see Faction standing below. Rep is not faction-specific and the two are not interchangeable.

### Character creation (`app.CharacterCreationScreen`)

**Everything starts at 1** — all six stats, all thirty skill ranks — and is bought up from there at creation. The run opens on `CharacterCreationScreen` (not `MainMenu`), where the player spends `STARTING_STAT_POINTS` (6) and `STARTING_SKILL_POINTS` (20). A stat point raises a stat by 1; a skill point raises one skill's rank by 1. So an unspent runner rolls `skill_value` 2 on everything, and the build is entirely what those 26 points bought.

**Archetypes (`shadowguy/archetypes.py`) are the fast path**: Enforcer, Hacker, Infiltrator, listed above the stat and skill rows. Each is a canned allocation of the same 6 + 20 points, and `Archetype.apply()` spends them through `spend_stat_point`/`spend_skill_point` rather than assigning fields — so a preset obeys the rank cap and the cost curve exactly like a hand-built runner and **cannot buy anything the player couldn't**. `_check_affordable()` runs every preset against a fresh `Character` at import and raises unless it spends both pools to exactly zero, so a preset that doesn't add up is a startup error rather than a half-applied runner. Picking one calls `reset_build()` first: a preset is the *whole* build, not a top-up, so picking twice or picking after hand-spending replaces cleanly instead of running the pools dry mid-apply.

Presets only spend on the **11 skills something actually rolls** — the six in `jobs.ARCHETYPES`' `skill_sequence`s, the eight in `corpmap.LOCATION_SKILL` (legwork), and Negotiations (the gig). The other 19 skills exist but no check names them yet, so points there would be dead. Keep new presets inside the live set, and widen the set by wiring skills into content, not by having a preset buy them. (Note `archetypes.Archetype` is a *character* preset — unrelated to `jobs.JobArchetype`, which is a job template.)

Both pools are **spent once and never refill** — there is no XP system, so this screen is the whole character-progression system. Consequences that are load-bearing:

- **Begin is gated on an empty pool** (`action_begin`): unspent points would be silently forfeited once the run starts, so the screen refuses to leave until both pools are 0. It `switch_screen`s to `MainMenu` rather than pushing, so there's no going back to respend.
- **`r` resets the whole build** (`Character.reset_build()`). 26 irreversible allocations with no undo is a footgun; reset is the way out of a misclick.
- **`SkillsScreen` is read-only after creation.** It displays ranks; it does not spend. Don't re-add a spend path there without deciding where the points come from.
- **Buying Body raises current health, not just max** (`spend_stat_point`). `max_health` is derived from Body, so without that the runner would start a 30-max run at 15 health.
- **Skill rank is capped at `MAX_SKILL_RANK` (10) and ranks get dearer as they climb.** `SKILL_RANK_COST` prices the *next* rank: 1 point for ranks 2–4, 2 for 5–7, 3 for 8–9, 4 for rank 10 — so taking one skill from its starting rank 1 all the way to 10 costs **19 of the 20 points**. A specialist buys one great skill and almost nothing else; that's the trade. Both the cap and the price are enforced in `Character.spend_skill_point`, never in the UI, and a refused buy is **never charged**.
- **Read `next_rank_cost()` before spending, not after.** It returns `None` for a maxed skill and otherwise the price, which is what lets `CharacterCreationScreen` tell apart "already at rank 10" from "rank 8 costs 3 points, you have 2". Since high ranks cost 3–4, *"can't afford" happens with points still in hand* — a bare `if not spend_skill_point(...)` would report "no points left" to a player staring at their remaining points.
- The begin-gate can't deadlock on an unspendable remainder: ranks 2–4 always cost 1, and 20 points can never push enough of the 30 skills past rank 4 to exhaust the 1-point buys (a leftover point always has ~29 skills to land on).
- Stats are uncapped and flat-priced (1 point each), but the 6-point pool is its own ceiling — 7 at most in one stat.

### Check resolution

Randomized: `d20 + skill_value vs difficulty`, where `skill_value` is stat + gear + chems + invested rank. Natural 20 = critical success, natural 1 = critical failure, regardless of total.

**Runs are not reproducible, and `app.rng` is a trap.** `ShadowguyApp.rng` is threaded through map and job *generation*, but `checks.resolve_check()` takes an optional `rng` that nobody passes, so it falls back to the **module-level `random`**. Seeding `app.rng` therefore does not control the dice. If you want seeded/replayable runs, thread `rng` down into `resolve_check` — until then, only `random.seed()` makes a check deterministic, and anything asserting on a job's outcome is flaky by default.

### Runner-mode activity types (`shadowguy/scene.py`, `shadowguy/content.py`, `shadowguy/jobs.py`)

All three share the same `Scene`/`Stage`/`Choice`/`Outcome` data model, distinguished by `Scene.kind`:

- **Job** (`SceneKind.JOB`, formerly "mission") — multi-stage scene-based job, choices branch on skill checks, failure can end the job early. Jobs are not freely pickable: they're procedurally generated by Fixers (see below) and must be accepted before they show up as a runnable activity.
- **Gig** — small single-stage activity for quick resources, still freely available from the main activity list (no Fixer needed).
- **Legwork** — single-stage prep activity that banks an `advantage` bonus for a *specific* job (`Scene.prepares_for`), consumed on that job's first check only, then gone. `Character.advantage` is a `dict[job_id, int]`, not a flat global bonus — advantage from one job's legwork can't leak into an unrelated job or gig. For fixer-issued jobs, the legwork Scene itself is generated on the fly per accepted job (`jobs.generate_legwork_for_job`), since each job has a unique procedurally-generated id — there's no fixed legwork-to-job mapping to hand-author anymore. Its choices are the `Location`s of the job's target territory (see Corp map): casing the job's own site is the hardest check for the most advantage, scouting a neighbouring place in the same district is easier for less. That's why it takes the job's `Scene` and the `CorpMap` rather than a job id — legwork can't be built without knowing where the job lands.

### Runner position & travel (`shadowguy/character.py`, `shadowguy/app.py`)

`Character.location_id` is the `Territory` the runner is standing in, starting at `CorpMap.player_start_id`. It's the second runner↔corp coupling after standing: the runner is somewhere *on the corp board*, not in an abstract city.

Travel lives on `CorpMapScreen`, and the two markers there are different things: `*` is the **cursor** (`selected_id`, moved by the arrow keys along connections, or by clicking any node) and `@` is the **runner** (`character.location_id`). `enter` moves the runner to the cursor's node — but only if it borders `location_id`, and only for `TRAVEL_STAMINA_COST` stamina, so crossing the map costs days. Base stamina is 5 (`character.BASE_STAMINA`), which is the budget travel competes with gigs, jobs and legwork for.

The MainMenu **Local** category lists the `Location`s of whatever node the runner is in. Most rows are still display-only, except the five shop `LocationKind`s (see Shops below), which open a `ShopScreen`.

**Position gates jobs.** A job — and the legwork that preps it — can only be run while the runner is standing in the job's `target_territory_id` (`MainMenu._on_site`). Off-site, the row is labelled `travel to <district>` and selecting it is a no-op; the check is enforced in `on_list_view_selected`, not just in the label. That's what pays for travel: an accepted job is a *place you have to go*, and a hard `scheduled_day` job means being in the right district on the right day. Gigs are unaffected — they're street work, runnable anywhere.

Note this stacks with timing: on-site but on the wrong day is still blocked, and the label reports whichever gate bites first (travel, then timing, then stamina).

### Fixers & job generation (`shadowguy/fixer.py`, `shadowguy/jobs.py`)

Jobs are gated behind a persistent roster of Fixers (`fixer.FIXER_ROSTER` / `create_fixers()`), not listed directly. Each `Fixer` holds up to `max_offers` `JobOffer`s; offers are procedurally generated (`jobs.generate_job`) from a small set of archetypes (Heist/Extraction/Sabotage), with difficulty and reward scaled by a day-derived tier and flavor text drawn from word banks — not picked from hand-authored content.

Jobs are run **against a real corp, on the real map**. `generate_job` takes the `CorpMap`, picks a `Territory` that a faction actually owns this run, then picks one of that territory's `Location`s as the site — so `generate_job`/`refresh_offers` need the map threaded through them. The job records `Scene.target_faction_id`, `Scene.target_territory_id` and `Scene.target_location_id`, and its flavor text names that corp, that district and that building. There is deliberately no separate list of corp names or venue names: if you find yourself adding one, you've disconnected jobs from the map again.

Every job offer carries a `JobTiming`:
- **no deadline** — can be run any day, indefinitely, while it sits on the fixer's board.
- **soft deadline** (`deadline_day`) — must be run by that day (inclusive); expires after.
- **hard scheduled day** (`scheduled_day`) — can *only* be run on that exact day; unrunnable before, expired after.

Flow: browse Fixers (`f` keybinding) → accept an offer → it moves from the fixer's board (freeing a slot for `refresh_offers` to fill on the next day) into `Character.accepted_jobs`, and appears in the main activity list alongside its dynamically-generated legwork. `Character.rest()` (advancing the day) drops any accepted job whose timing has expired; `fixer.expire_offers` + `fixer.refresh_offers` do the same for un-accepted board offers, keeping each fixer topped up to `max_offers`. Completing a job (reaching a stage with no `next_stage`) removes it from `accepted_jobs` via `Character.remove_job(scene.id)` — jobs are one-shot, not repeatable busywork like gigs/legwork.

Note: `content.JOB_DATA_HEIST` and `content.LEGWORK_CASE_THE_BLOCK` are hand-authored example content left in place but no longer wired into `app.py` — they predate the Fixer system and don't fit its per-offer unique-id model.

### Faction standing (`shadowguy/factions.py`, `shadowguy/scene.py`, `shadowguy/character.py`)

This is the first real runner→corp coupling: what you do in Runner mode changes how the corps feel about you.

`Outcome.standing_delta` moves standing with the *scene's* `target_faction_id` — the Outcome itself never names a faction, so the same job template works against any corp. `factions.standing_shift()` owns the rule: the corp you hit moves by `delta`, and **every rival moves the opposite way at half weight** (`RIVAL_WEIGHT`), because hurting a corp is a favour to its competitors. `scene.apply_outcome` is the single place this is applied.

Today only a *completed* job moves standing (`jobs.JOB_STANDING_HIT`, on the final stage's success/critical-success, where the cash and rep rewards already live). Botched and abandoned jobs cost nothing — that's a balance choice, not an oversight.

`Scene.__post_init__` rejects a `standing_delta` on a scene with no `target_faction_id`, so a gig can't silently anger a corp it was never aimed at.

**Room left for territory effects:** `Scene.target_territory_id` records *where* a job hit, not just who. Nothing consumes it yet beyond flavor text. A job that should also move territory control (weaken a faction's hold, flip a node neutral) belongs as a new `Outcome` field applied in `apply_outcome` alongside `standing_delta`, keyed off `target_territory_id` — don't invent a second effect pipeline.

### Corp map (`shadowguy/corpmap.py`, `shadowguy/factions.py`)

The Corp-mode board is generated fresh each run (`generate_corp_map`), not hand-authored: `TERRITORY_COUNT` (38) `Territory` nodes on an 8x6 grid, picked as one contiguous blob, wired by a random spanning tree (so the map is always connected) plus extra edges for loops/flanking routes. The rest is faction blocs and neutral ground. The grid is deliberately larger than `TERRITORY_COUNT` — the leftover cells are the holes that stop the blob degenerating into a solid rectangle.

**The runner owns nothing and starts nowhere.** `_player_start` picks a node on the *rim* of the grid (`_on_grid_edge`) that stays **unclaimed** — there is no `"player"` owner on the map at all, and `OWNER_TAGS` deliberately has no `YOU` entry. The map marks where the runner *is* with `@`, not with a tag saying they hold it. `generate_corp_map` passes that cell to `_grow_blocs` as `start_cell`, which reserves it: no faction may seed on it or expand onto it, so it is still neutral when the blocs stop growing. It then falls through to the neutral branch for both its `value` (`NEUTRAL_VALUES`) and its `modifiers` (`_neutral_modifiers`), like any other open district. Don't reintroduce a player-owned home node without deciding what Corp mode does with it — starting as a nobody on the edge of town is the point.

The rim is also where the dead ends are, so `_player_start` demands `MIN_START_DEGREE` (2) connections. Over 2000 seeds the start comes out neutral, on the rim, with degree 2 or 3, every time. (This retires the old "degree-1 start" quirk, but only *for the start node* — other nodes can still be dead ends, which is fine.)

At 38 nodes the board splits about evenly between held and open ground: 18 corp (`TERRITORIES_PER_FACTION` 6 × 3 factions) + 20 unclaimed, one of which the runner is standing on. The `+ 1` in the bloc guard below is that reserved start cell. `FACTION_VALUE_SPREAD` must hold exactly one value per faction territory — it *is* the per-faction value multiset, so `len(FACTION_VALUE_SPREAD) == TERRITORIES_PER_FACTION` is what makes fairness free rather than searched-for. Change one, change the other.

**The tuning constants guard each other at import time.** `corpmap.py` raises on import if `TERRITORY_COUNT` outgrows the grid, outgrows `DISTRICT_NAMES`, if `FACTION_VALUE_SPREAD` and `TERRITORIES_PER_FACTION` drift apart, or if the location name pool (`LOCATION_PREFIXES` × `LOCATION_SUFFIXES`) can't cover `MAX_SAME_KIND_LOCATIONS`. They live at module scope rather than in `generate_corp_map` because every one of them compares module constants — they're import-time facts, not per-call ones. Only the faction-count guard (`factions * per_faction + 1 <= TERRITORY_COUNT`) depends on the caller, so that one stays in `generate_corp_map`. The name-pool guard is the load-bearing one: `_make_locations` retries a colliding name in an unbounded `while True`, so an exhausted pool **hangs generation instead of raising**. If you raise `TERRITORY_COUNT` again, grow the name pools with it.

Faction starts are fair **by construction, not by search**: the generator races one contiguous bloc per faction outward from random seeds, then hands every bloc the *same* value multiset (`FACTION_VALUE_SPREAD`). Equal territory count and equal total value therefore can't come out unbalanced — there's no fairness check to fail. A bloc that gets boxed in before hitting its quota just reseeds and retries (about 29% of maps need at least one retry, never more than four). District names must stay **single words**: a territory's id is its lowercased name and ends up inside Textual widget ids (`MainMenu`'s `local_` rows), which cannot contain spaces.

Territory `value` is assigned *after* ownership, which is why fairness is free. Don't invert that order to give nodes "intrinsic" value without replacing the balance guarantee.

Each `Territory` also holds `LOCATIONS_PER_TERRITORY` `Location`s — the concrete places (data vaults, clinics, depots, bars, shops) a job actually hits. They're stocked from the owner: a corp district gets `SPECIALTY_LOCATIONS` of its owner's own kind (`LOCATION_KIND_FOR_SPECIALTY`) plus a filler slot rolled from `FILLER_KINDS` (the bar, or one of the five shop kinds — see Shops below), while neutral and player ground get a random mix of every `LocationKind`. Location *kinds* are map data; the **skill** each kind is scouted with lives in `corpmap.LOCATION_SKILL` (the flavor text is `jobs.LEGWORK_APPROACH_TEXT`, kept separate so there's one place, not two, that has to agree on which skill a kind uses). Legwork is scouting, so the table leans on perception and agility, with intelligence on the wired places and cool where the read comes out of a conversation — that's where `Perception` earns its keep, since no job archetype rolls it.

The stat behind a kind is **derived, never tabulated twice**: `corpmap.location_stat(kind)` is `skill_for(LOCATION_SKILL[kind]).stat`. That's what keeps a district's filler slot off its own specialty's stat — a district is `SPECIALTY_LOCATIONS` of one kind plus filler, so a filler sharing the specialty's stat (e.g. `COMPUTER_STORE`, also intelligence, next to a Hacking corp's `DATA`) would make that district's legwork three checks of one stat and no real choice. `_filler_pool` excludes them, and an import-time loop proves the pool can never run dry for any specialty a faction can actually have — otherwise `rng.sample` would raise mid-generation. If you retune `LOCATION_SKILL`, that guard is what catches you.

Each `Territory` also carries `modifiers`: a `dict[TerritoryModifier, int]` of `Security` / `Surveillance` / `Unrest` / `Development` / `Restricted`, each 0..`MODIFIER_MAX`. These are the levers a Corp-mode player will eventually pull on ground it holds; today they are **seeded at generation and displayed only** — the `#modifiers` panel under the corp map — and nothing reads them. The enum values are ids; the display names live in `MODIFIER_LABELS` (don't go back to deriving the label from the id, or a two-word modifier renders with its underscore showing).

**Two owners, two profiles**, each one function and each the single place to read that owner's rules. `_make_modifiers` dispatches on `FACTIONS_BY_ID` membership, the same question `_location_kinds` asks — keep them agreeing, or an unrecognised owner gets corp modifiers and neutral locations on the same node:

- `_corp_modifiers` — **corp turf**: garrisoned and watched in proportion to `value`, Unrest low, black market squeezed (Restricted 2–5).
- `_neutral_modifiers` — **ground nobody holds**, including the runner's start: Security 1, Surveillance 0, Unrest at `MODIFIER_MAX`, Restricted 0, Development **rolled** at 1–2.

**Development is derived, not rolled, on held ground** (`_development`): it rises with Security and Surveillance and falls with Unrest, so a holder's Development can never contradict the levers that produce it — you raise it by policing the block and putting the street down, not on its own. Neutral ground is the one place it escapes that formula, deliberately: running neutral through `_development` would pin every neutral node to 0. Don't "fix" either half into the other.

Consequences of the profiles, not bugs: no district is ever at Unrest 3–4 (held is 0–2, neutral is exactly 5), and Security / Surveillance / Development **never reach `MODIFIER_MAX`** — the best corp district is `value` 3 (`FACTION_VALUE_SPREAD`) +1 jitter = 4, so the top of the bar is dead. If you want a 5 to be reachable, raise the top of `FACTION_VALUE_SPREAD` rather than special-casing the modifier. Note also that unlike `value`, per-faction modifier totals are *not* equal by construction (the seeding jitters), which is harmless while nothing consumes them but is a balance question the moment something does. The obvious hooks when that day comes: Security → job difficulty in that territory, Surveillance → *legwork* difficulty (it's the scouting counterpart to Security, which is why `LEGWORK_APPROACH` is the natural place for it), Unrest → flipping a node's owner, Development → `value`, Restricted → price/availability if a street market ever exists. Restricted reads as *how hard the owner squeezes the market*, not how much contraband is lying around — high means scarce.

**`CorpMapScreen`'s row budget is exact at 80x24, and the panels are what threaten it.** `#map_scroll` is `1fr` under two fixed-height panels, so every row `#territory_info` and `#modifiers` take is a row of board the player can't see — and the map is 11 lines tall. That's why `#modifiers` renders its five levers as *two* lines (labels over bare `n/MODIFIER_MAX` scores, `MODIFIER_COLUMN` wide each) rather than a row each, and why all three panels carry no vertical padding. A row-per-modifier panel with explanatory text per row fits the width fine and still costs the map half its viewport. There is deliberately no `###..` bar gauge — the score carries the same information and the hashes only added noise. Two traps here:

- A wrapping row silently doubles a panel's height, and **asserting on `Static.content` will not catch it** — that's the pre-render source string. Compare `content_size.height` to the line count instead. (`#territory_info` does wrap at 80 cols today: its `Locations:` line overflows. It costs a row, and it's the first thing to fix if the budget gets tight again.)
- Don't check the panels in isolation. Drive the real screen at `size=(80, 24)` and compare `#map_scroll`'s `content_size.height` against the map's line count — that's the number that actually says whether the board is visible.

`render_ascii_map` returns a `RenderedMap` (text + `NodeSpan` per label, with both line/column and absolute offsets), not a bare string — that's what lets `CorpMapScreen` hit-test the mouse for hover-info and highlight the hovered node. Kept as ASCII rather than one widget per node so the `----` / `|` connector lines survive. At 38 nodes the map renders **128–162 columns wide** (mean ~151), so it lives in a horizontally scrollable container and can never fit an 80-column terminal — horizontal scrolling is expected, vertical scrolling is the thing to avoid. `CorpMapScreen._refresh` re-renders it only when the cursor or the runner moves; hover just restyles the cached `RenderedMap`, so don't put a `render_ascii_map` call back on the mouse-move path.

Known quirk: the spanning tree plus `EXTRA_EDGE_CHANCE` still leaves plenty of **degree-1 dead ends** elsewhere on the board. That's fine — a cul-de-sac is a real place. It's only the *start* node that's guaranteed a way out (`MIN_START_DEGREE`), because that's the one the stamina budget can't recover from.

### Shops (`shadowguy/shops.py`)

Five `LocationKind`s are retail rather than job-related: `PAWN`, `WEAPON_SHOP`, `AUTO_DEALER`, `PHARMACY`, `COMPUTER_STORE` (`corpmap.SHOP_KINDS`; `shops.CATALOG`'s keys are checked against this at import time). They're generated exactly like any other `Location` (see Corp map above): neutral ground can roll any of them, and a corp district can roll one into its filler slot (`corpmap.FILLER_KINDS`, excluding whichever shops share the district's own specialty stat) alongside its two specialty locations — so a shop can end up as a job's target site, and `corpmap.LOCATION_SKILL`/`jobs.LEGWORK_APPROACH_TEXT` each have an entry for every shop kind to cover that. Real Estate was on the original request but deliberately left out: it doesn't fit the "carryable item" model below and wasn't worth special-casing yet.

Selecting one of these locations from the MainMenu **Local** tab pushes a `ShopScreen` (`app.py`) instead of being a no-op. `shops.CATALOG` maps each shop `LocationKind` to a fixed list of `Item`s (id, name, price, `stat`, `bonus`). Buying spends `Cash` and appends the item id to `Character.inventory` — a flat `list[str]`, duplicates allowed, so the same item can be bought (and owned) more than once. Items are **persistent, not consumable**: `Character.stat()` adds up every owned item's bonus for the requested stat on top of the raw attribute, so gear silently strengthens every check that uses that stat — jobs and legwork included, since both go through the same `stat()` call.

**Pawn Shop is the only kind that buys back**: `ShopScreen` also lists the runner's current `inventory` there, and selecting one sells it via `shops.sell_item` for `PAWN_SELL_FRACTION` of its catalog price. Sell rows are keyed by **inventory index**, not item id — the same item id can appear more than once in `inventory`, and a repeated id would collide as a Textual `ListView` id (see Known Textual gotchas).

### Codebase layout

```
src/shadowguy/
  character.py   Character dataclass: core stats, health, skill ranks/points, advantage bank, faction standing, accepted_jobs
  archetypes.py   Enforcer/Hacker/Infiltrator creation presets; apply() spends via Character's own spend methods
  checks.py       resolve_check(): d20 + skill_value vs difficulty
  skills.py       Skill table (5 per core stat), skill_value(), skill_for(); leaf module, imports nothing
  scene.py        Scene/Stage/Choice/Outcome data model, resolve_choice(), apply_outcome()
  content.py      hand-authored example gig/job/legwork data
  jobs.py         procedural job generation + timing (JobTiming) + per-job legwork generator
  fixer.py        Fixer/JobOffer persistent roster, offer refresh/expiry
  factions.py     rival corp Factions (id/name/specialty) that own map territory
  corpmap.py      procedural Corp-mode territory map + ASCII renderer
  shops.py        retail LocationKinds: Item catalog, buy_item/sell_item, equipped stat bonuses
  app.py          Textual App: CharacterCreationScreen (start) + MainMenu + FixerListScreen/FixerOffersScreen + SceneScreen + CorpMapScreen + ShopScreen + InventoryScreen/SkillsScreen
```

### Verifying changes

There is **no test suite and no test framework** in this project — `pyproject.toml` has no dev dependencies. Guideline §4 still applies, so verification means driving the code directly:

- **Model/generator changes** — a throwaway script that runs the generator over a few thousand seeds and asserts its invariants. That's how the corp map's "always connected, every faction equal in count and value" guarantee is checked; a map that merely *looks* plausible can be quietly unfair.
- **UI changes** — Textual's `async with app.run_test() as pilot:` drives the real app headlessly. `pilot.press(...)`, `pilot.hover(widget, offset=...)` and `pilot.click(...)` exercise the actual screens, and you can read widget state back afterwards. Prefer this over asserting on internals: the hover/hit-test work was only trustworthy once the real `MouseMove` events had been through Textual's own dispatch.
- Anything asserting on a **check outcome** must seed the module-level `random` (see Check resolution) or it will be flaky.

`uvx ruff check src/` lints.

### Known Textual gotchas hit so far

- `ListView.clear()` returns an `AwaitRemove` — it must be awaited, or a following `.append()` can race the removal and raise `DuplicateIds`. Handlers that clear-then-repopulate a `ListView` must be `async def`.
- `ListView.index` becomes `None` after `clear()`; set it explicitly (e.g. `.index = 0`) after repopulating or keyboard selection (`enter`) has nothing highlighted to act on.
- `Static` renders its string as Rich markup by default — literal square brackets (e.g. `"[Legwork]"`) get parsed as markup tags and silently vanish. Avoid `[...]` in label text, or escape/disable markup. **Way out:** pass `Static.update()` a Rich `Text` object instead of a `str`. Markup is never parsed, so brackets survive, and you can still colour arbitrary character ranges via `Text.stylize(style, start, end)` — that's how the corp map highlights the hovered node despite every label being wrapped in `[...]`.
- `Screen`'s resume hook is the public `on_screen_resume` (message `handler_name` is public even though `Screen` itself also defines a separate private `_on_screen_resume` for internal bookkeeping) — override the public one to refresh a screen's content when it's popped back to.
- Mouse hit-testing on a text blob: handle `on_mouse_move` and call `event.get_content_offset(widget)`, which returns an `Offset` inside the widget's content (padding/border/scroll already accounted for) or `None` when the pointer is outside it. `None` is the signal to clear hover state — mouse events bubble to the `Screen`, so the handler fires for the whole screen, not just the widget you care about.
- `Static` has **no** `.renderable` attribute in Textual 8 (it did in older versions); the current content is `.content`. Only matters when asserting on widget contents in tests.
