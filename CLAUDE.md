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
- **Corp mode** — 4X scale. Player controls a corp, area-control/resource game against rivals. A first-slice turn loop exists (`shadowguy/corp_turn.py`): take over one of the 3 seeded Factions, collect territory income and research, spend one directed move a day (expand onto neutral ground, train employees at your Academy, or upgrade your Research Facility's labs/efficiency). No corp-vs-corp conflict yet — see Corp mode turn loop.

Switching between runner and corp is optional and meant to be difficult — neither mode is a straight upgrade over the other. **A run can also start as either one** (New Game → Runner / Corp): a Corp game never builds a runner at all (`ShadowguyApp.corp_only`), so it isn't "runner mode plus a corp screen" — it's the 4X half on its own.

### Run/game-over rules

- No meta-progression between runs (for now). Each run starts fresh.
- Runner mode ends when the character dies.
- Corp mode ends when the corp is destroyed, taken over, or the character is assassinated.

### Stats and skills (`shadowguy/character.py`, `shadowguy/skills.py`)

Six **core stats** (`character.CORE_STATS`): `Body`, `Strength`, `Agility`, `Perception`, `Intelligence`, `Cool`. `Cash`, `Rep` and `Humanity` are resources, not checkable stats — `STAT_NAMES` is the union, and `Character.stat()` only folds gear bonuses into the six core ones. Health is a separate pool scaling off **raw** Body (`BASE_HEALTH + body * HEALTH_PER_BODY` = `10 + body * 5`), deliberately not `stat("body")`, so gear never moves max health.

**Nothing rolls a core stat directly.** Each stat carries skills (`skills.SKILLS`, **32** total); a `Choice` names a *skill*, and `skills.skill_value()` is what the dice see: the skill's tied stat (gear included) plus the rank invested in that specific skill. Perception carries two extras beyond five — `Firearms` (a gun is aimed, not swung, so it rolls the same faculty as `Sight`) and `Misc Weapons`. Nothing enforces five-per-stat, and cost is per-skill anyway, so extras make perception *broader*, not stronger. `Character.skill_ranks` is `dict[skill_id, int]`, fully populated (every skill starts at `STARTING_SKILL_RANK`). A skill id absent from `SKILLS_BY_ID` raises from `skills.skill_for()`, the single chokepoint — `Scene.__post_init__` runs it over every choice, so a typo fails when the scene is *built*, not mid-roll.

`skills.py` is deliberately a **leaf module** (imports nothing from the package at runtime), because `character.py → shops.py → corpmap.py` all import it. That's why the "every `Skill.stat` is a real core stat" guard lives in `character.py`, the one module that can see both tables. Don't add a runtime `character` import to `skills.py`; it's a cycle.

`Rep` is global standing in the street. `Character.standing` is a separate `dict[faction_id, int]` of per-corp standing (see Faction standing) — the two are not interchangeable.

**`Rep` is floored at `REP_FLOOR` (-10), not 0** — unlike health, it's allowed into the red. A blown job or gig costs a point of it (`Character.adjust_rep`, called from `scene.apply_outcome` and, for the knockout path that bypasses `Outcome`s, directly from `screens.scene_screen.SceneScreen._on_combat_end`): the last stage's plain failure or fleeing any of a job's fights costs `jobs.JOB_FAILURE_REP_HIT` (-1) alongside `JOB_FAILURE_TRUST_HIT` (-1) with the sending fixer; either kind of gig failure costs `gigs.GIG_FAIL_REP_HIT` (-1) alongside `GIG_FAIL_STANDING_HIT` (-1) with the gig's owner. A mid-job stage failure that isn't the last one doesn't trigger this (the job carries on — see Fixers & job generation). Live consequence: `factions.CORP_OFFICER_TIERS`' reception gate is `min_rep = 0`, so a runner whose rep has gone negative gets turned away even from the public lobby (see Corporate HQs & officers).

**`Humanity` is a fixed baseline (`HUMANITY_BASELINE`, 6), not a meter that moves.** Nothing in the game raises or lowers `Character.humanity` today — it's a ceiling, not a draining pool. What it caps is real: it's the runner's total budget for cyberware, spent as `cybernetics.Cyberware.humanity_cost` per installed piece (see Cyberware) rather than an abstract cyberpsychosis tally.

### Character creation (`screens/creation_screen.py`)

**Everything starts at 1** — all six stats, all 32 skill ranks — bought up from there. Entry point is `screens.menu_screens.TitleMenu` (New Game / Load Game / Test / Settings, pushed from `app.py`'s `on_mount`); New Game opens `ModeSelectScreen` (Runner / Corp — see Corp mode turn loop), and only the **Runner** branch reaches `CharacterCreationScreen`, spending `STARTING_STAT_POINTS` (6) and `STARTING_SKILL_POINTS` (20). A Corp game skips this screen entirely and never builds a runner. An unspent runner rolls `skill_value` 2 on everything — the build is entirely what those 26 points bought. (`TitleMenu`'s Test option opens `TestMenu`, a developer shortcut straight into a standalone tactical or matrix fight, reusing whatever `Character` `_new_run()` stamped out — reachable before `CharacterCreationScreen`. No job/gig involved; a tactical test fight resets health to max afterward.)

**Archetypes (`shadowguy/archetypes.py`) are the fast path**: Enforcer, Hacker, Infiltrator — each a canned allocation of the same 6 + 20 points. `Archetype.apply()` spends them through `spend_stat_point`/`spend_skill_point` rather than assigning fields, so a preset obeys the rank cap and cost curve exactly like a hand-built runner and **cannot buy anything the player couldn't**. `_validate_preset()` runs every preset against a fresh `Character` and requires it to spend both pools to exactly zero — deferred to first access of `ARCHETYPES`/`ARCHETYPES_BY_ID` (module `__getattr__`), not plain import, so importing `archetypes` alone doesn't construct a `Character`. Picking one calls `reset_build()` first — a preset is the *whole* build, not a top-up. (`archetypes.Archetype` is a *character* preset, unrelated to `jobs.JobArchetype`, a job template.)

**Not every skill is rolled by something.** Coverage: 25 distinct skills across `jobs.ARCHETYPES`' approaches, 8 via `corpmap.LOCATION_SKILL` (legwork), the per-kind approaches in `gigs._GIG_TEMPLATES`, and combat (`combat.available_actions`: brace/read-the-fight/face-them-down/break-and-run, plus whatever skill each equipped weapon rolls). `Leadership` (`cool`) is *read* rather than rolled — scales crew recruiting terms (see Runners & crew). Gaps: `Firearms`, `Long Blade` and `Misc Weapons` are weapon-gated (need to own the weapon); `Sight` is reached only via legwork at a `WEAPON_SHOP`. The three presets buy narrow on purpose — a preset should read as an archetype, not a hedge — which is what makes each bleed on unsuited job stages.

Both pools are **spent once and never refill**. Load-bearing consequences:

- **Begin is gated on an empty pool** (`action_begin`): the screen refuses to leave until both pools are 0, then `switch_screen`s to `MainMenu` (no going back to respend).
- **`r` resets the whole build** (`Character.reset_build()`) — 26 irreversible allocations, no undo.
- **`SkillsScreen` is read-only for the creation pools, but not for `Character.experience`** — see Post-creation experience.
- **Buying Body raises current health, not just max** (`spend_stat_point`), or a 30-max run would start at 15 health.
- **Skill rank is capped at `MAX_SKILL_RANK` (10) and ranks get dearer as they climb.** `SKILL_RANK_COST` prices the *next* rank: 1 point for ranks 2–4, 2 for 5–7, 3 for 8–9, 4 for rank 10 — one skill from 1 to 10 costs **19 of the 20 points**. Enforced only in `Character.spend_skill_point`, never the UI; a refused buy is **never charged**.
- **Read `next_rank_cost()` before spending, not after** — `None` for a maxed skill, otherwise the price, which lets the UI tell "already maxed" apart from "costs more than you have" (high ranks can cost more than points in hand).
- The begin-gate can't deadlock: ranks 2–4 always cost 1, and 20 points can never push enough of 32 skills past rank 4 to exhaust the 1-point buys.
- Stats are uncapped and flat-priced (1 point each); the 6-point pool is its own ceiling (7 max in one stat).

### Post-creation experience (`shadowguy/character.py`, `shadowguy/jobs.py`, `screens/info_screens.py`)

`Character.experience` is the run's real growth path (creation pools are one-shot) — one pool, spent on a stat or a skill, player's choice each time. Earned **only from completed jobs**: `jobs.JOB_XP_BASE` (10/15/20 by day tier, same tier domain as `REWARD_BASE`) folds into every final-stage `Outcome`'s payout, scaled by the same success/critical-success multiplier (1.0/1.5) cash already uses. Gigs, legwork and security contracts pay none. `scene.apply_outcome` credits it unconditionally (`gain_experience`), same shape as `cash_delta`.

**Spending has its own cost curve per resource, both funded by the one pool:**

- **Skill** — next rank costs exactly what it would at creation (`spend_experience_on_skill` reuses `next_rank_cost`/`SKILL_RANK_COST`), still capped at 10.
- **Stat** — no table (never capped/costed above 1 point at creation): `next_stat_cost(name)` = `current_value - STARTING_STAT + 1` (1, 2, 3, 4…), the same escalating shape as a formula rather than a table. `spend_experience_on_stat` reuses the body-health-bump invariant via a shared private `_raise_stat`/`_raise_skill_rank`.

**`SkillsScreen` is the spend surface** — each stat column gained a "Raise `<Stat>`" row (cost from `next_stat_cost`), skill rows pass `show_cost=True` to the same `_compact_skill_label` helper creation uses. A refused buy is never charged. `CharacterSheet`'s header gained `Experience: {n}xp` alongside cash/rep.

**Crew earns experience too, in parallel.** `_take_crew_cut` (`screens/scene_screen.py`) also calls `Character.grant_crew_experience(hire.runner_id, outcome.experience_delta)` for every runner on a completed job's crew — the *full* amount, not divided by headcount. `Character.crew_experience: dict[runner_id, int]` is the ledger, but `runners.RivalRunner` has no sheet to spend it on — **no spend path yet** (a mechanism waiting on a driver, the same pattern `gang_standing` predated `encounters.py`).

`saves.SAVE_VERSION` 32: `Character` gained `experience`/`crew_experience`, `scene.Outcome` gained `experience_delta` (pre-v32 pickled Outcomes lack it). **Not balance-simulated** — first-slice numbers sized to feel like "one skill rank per one or two completed jobs early on," not seed-swept.

### Check resolution

**An opposed d6 dice pool** (`checks.resolve_check`, the one place any check resolves — jobs, gigs, legwork and every roll in combat/tactical). You roll `skill_value + advantage` d6 and count 5s/6s; the opposition rolls `pool_for_difficulty(difficulty)` d6 and counts the same. Net successes: >0 is a success, `>= CRITICAL_MARGIN` (3) either way is a critical. No natural 20 — a critical is "how wide the gap between the two pools was."

**Every difficulty constant is still written on the old d20-era DC scale** (~9–21), converted at the one chokepoint by `pool_for_difficulty` (`round((DC-9)/2)`, floored at 0). Two gotchas:

- **The conversion is coarse and lopsided.** DCs 9–21 squash into opposing pools of **0–6** while player pools run 2–15. DC 10 converts to **0 opposing dice** (unfailable barring whiffing every die). Adjacent DCs often convert to the same pool, so ±1 tuning frequently does nothing.
- **Critical failure no longer scales the way a natural 1 did.** It was a flat 5% tax; now it's a function of how outmatched you are (~0% at pool 8 vs DC 13, ~11% at pool 2 vs DC 19). Since a critical failure routes a job stage into a fight, weak builds go loud more often than the old rule implied.

**Runs are not reproducible, and `app.rng` is a trap.** `ShadowguyApp.rng` is threaded through map/job *generation*, but `checks.resolve_check()`'s optional `rng` is never passed by any caller, so it falls back to **module-level `random`**. Seeding `app.rng` does not control the dice — only `random.seed()` does, so anything asserting on a job's outcome is flaky by default.

### Runner-mode activity types (`shadowguy/scene.py`, `shadowguy/gigs.py`, `shadowguy/jobs.py`)

All three share the same `Scene`/`Stage`/`Choice`/`Outcome` data model, distinguished by `Scene.kind`. A stage of any of them can be a **fight** or a **tactical map** instead of a set of choices (`Stage.combat` / `Stage.tactical`) — a property of the stage, not the `SceneKind`.

- **Job** (`SceneKind.JOB`) — multi-stage, choices branch on skill checks, failure can end it early. Not freely pickable: procedurally generated by Fixers and accepted before they show up as a runnable activity. Every stage carries a fight beside it, reachable by ambush or by critically failing an approach.
- **Gig** — small single-stage activity for quick resources, no Fixer needed. **Procedurally generated per-Location** by `gigs.generate_gig` from a per-`LocationKind` template. Attached to a place *and* a person — spawns at a `Location`, owned by one of its `LocalCharacter`s, rewards standing with them. Optional and self-selected, so it **may** theme its choices on one stat (unlike a job stage); offers a random 1..`GIG_MAX_APPROACHES` (3) subset. Lives on `app.location_gigs: dict[location_id, Scene]` (not on `Location` — corpmap is a leaf that can't import scene), refreshed by `gigs.refresh_gigs` on run-start and each rest, consumed one-shot. `MainMenu` lists the current territory's gigs, gated only by being there.

  **The cash-stake gate exists but no current gig uses it.** `Scene.max_cash_loss` (the worst `cash_delta` any path can charge, derived from the outcomes) lets `MainMenu` refuse a gig the runner can't cover — `apply_outcome` subtracts a loss straight off cash, and **cash is not floored the way health is** (flooring would let a broke runner ride every losing outcome for free). Health and rep *are* floored (rep at `REP_FLOOR`, see Stats and skills, above) — only cash is allowed to go unaffordable-and-refused instead. Today's gigs cost only health on failure so the gate never bites, but it's live the moment any scene charges cash — a job/legwork scene that charges cash needs the same gate.
- **Legwork** — single-stage prep banking an `advantage` bonus for a *specific* job (`Scene.prepares_for`), consumed on that job's first check only. `Character.advantage` is `dict[job_id, int]`, so advantage can't leak into an unrelated job or gig. Generated per accepted job (`jobs.generate_legwork_for_job`). Its choices are the `Location`s of the job's target territory: casing the job's own site is the hardest check for the most advantage, scouting a neighbour easier for less — why it takes the job's `Scene` and the `CorpMap` rather than a job id. **Can turn violent**: a critical failure gets you jumped (`LEGWORK_FIGHT_STAGE`) by a *street-tier* pair of locals, not the corp response team. No ambush option, no way to *win* an advantage — a fight here means it went wrong, and the best you get is out.

### Runner position & travel (`shadowguy/character.py`, `screens/corp_map_screen.py`)

`Character.location_id` is the `Territory` the runner is standing in, starting at `CorpMap.player_start_id`.

Travel lives on `CorpMapScreen`: `*` is the **cursor** (`selected_id`, moved by arrows along connections, or clicking a node), `@` is the **runner** (`character.location_id`). `enter` moves the runner to the cursor's node, only if it borders `location_id`. Cost is **hours, not stamina** (`CorpMapScreen.action_travel`, see Time & the day clock): a hop costs `TRAVEL_HOURS_COST` (2.0), cut by an equipped `Slot.VEHICLE`'s `shops.Item.travel_reduction` (`equipped_travel_reduction`) — no daily allowance to run out of, travel never refused.

The MainMenu **Local** category lists the runner's node's `Location`s. Screens: the five shop kinds → `ShopScreen`, `BAR` → `BarScreen`, `CORP_HQ` → `CorpHQScreen`, `HOSPITAL` → `HospitalScreen`, `REAL_ESTATE` → `RealEstateScreen`, `APARTMENT`/`SAFEHOUSE` → `SafehouseScreen`. The rest are display-only.

**Position gates jobs.** A job — and its legwork — can only be run while standing in `target_territory_id` (`MainMenu._on_site`). Off-site the row reads `travel to <district>` and is a no-op — enforced in `on_list_view_selected`, not just the label. This stacks with timing (on-site but wrong day is still blocked; the label reports whichever gate bites first). Gigs are gated differently — to wherever a matching `LocationKind` is local, not one territory.

### Rest, lodging & property (`shadowguy/corpmap.py`, `shadowguy/shops.py`, `screens/main_menu.py`, `screens/shop_screens.py` — `HospitalScreen`/`RealEstateScreen`/`SafehouseScreen`)

A day boundary crossed at night is a *transaction*: whichever action pushes the clock past midnight charges `corpmap.lodging_cost(here)` unless the crossing came from a `skip_night_effects` spend (a hospital stay).

- **Lodging** is `LODGING_COST_PER_DEVELOPMENT` (5) × the district's Development — free where `corpmap.has_home(territory)` (an owned `APARTMENT`/`SAFEHOUSE`). **Resting must never be blocked**: the handler pays `min(cost, cash)` and moves on.
- **`Character.on_new_day()` does not heal.** It resets `health_kit_used_today`/`temp_bonuses`, expires jobs and discharges orphan crew, once per day boundary. Health is deliberately excluded.
- **The hospital is the main way health comes back** (`shops.hospital_stay`, `HospitalScreen`): `HOSPITAL_STAY_COST` (20), heals `1d6 + raw Body`. Spends exactly `HOURS_PER_DAY` with `skip_night_effects=True` (already covers room/board, so no double lodging charge). A Health Kit is a small one-off top-up, capped once/day (`health_kit_used_today`).
- **Property**: a `REAL_ESTATE` office lists safehouses *across the map* (`Location.listings`). Buying calls `corpmap.add_safehouse` → `has_home` true there. `safehouse_price` = `SAFEHOUSE_BASE_PRICE` (200) + `SAFEHOUSE_PRICE_PER_DEVELOPMENT` (75) × Development + `SAFEHOUSE_PRICE_PER_VALUE` (50) × value. Districts with an existing home drop off the listing.
- `SafehouseScreen` is a **stub** — rest/stash functions land later.

This is the second thing to read `TerritoryModifier.DEVELOPMENT` for real (see Corp map) — Development is no longer decoration.

### Time & the day clock (`shadowguy/character.py`, `shadowguy/app.py`)

Runner mode has no stamina and no manual "End the day" button. `Character.elapsed_hours: float` is a continuous, never-reset clock; `Character.day` is **derived** (`int(elapsed_hours // HOURS_PER_DAY) + 1`, `HOURS_PER_DAY` = 24) — nothing increments it directly. Travel and every job/gig/legwork spend **hours**, not stamina, with **no exhaustion cap** — deadlines, health and rep are the pacing pressure now, not a stamina wall.

**`ShadowguyApp.spend_time(hours, *, skip_night_effects=False, protect_job_id=None)` is the single chokepoint** advancing the clock, replacing the old `Character.rest()` + `advance_day()`/`end_day()` trio. It bumps `elapsed_hours`, then loops once per day boundary crossed, calling `_apply_day_tick(day, skip_night_effects, protect_job_id)` for each. **A day tick firing is a side effect of crossing midnight**, at wherever the runner currently is. The loop only ever runs once today (nothing spends ≥2×`HOURS_PER_DAY` in one call), kept as the generically-correct form — a direct test (`test_spend_time_fires_the_day_tick_once_per_boundary_crossed`) proves it iterates when needed. `rival_actions` is reset once before the loop and accumulated with `+=` per iteration, same as `CorpState.cash`/`research_points`.

`_apply_day_tick` folds together: `Character.on_new_day(protect_job_id)`, crew wages, `expire_offers`/`refresh_offers`/`refresh_security_offers`/`refresh_gigs`, `resolve_rival_day`, corp income/research + `daily_action_used` reset, and — unless `skip_night_effects` — the lodging charge + security-contract night resolution. **Call-site ordering**: every caller spends time *before* the accompanying mutation (e.g. `action_travel` spends before moving `location_id`) — a boundary crossed mid-trip resolves at the *origin* territory, not the destination.

**Running a job (or its legwork) must not expire itself.** `MainMenu` calls `spend_time(scene.hours_cost, protect_job_id=job.scene.id)` *before* pushing `SceneScreen` — a `scheduled_day` job run late on its one legal day (8–12h cost) could otherwise get pruned by its own time cost before the scene starts. `protect_job_id` exempts that one job (matched by `Scene.id`) from this tick's expiry check only — it's still removed normally once its scene resolves.

**An explicit "Rest" action remains** (`MainMenu`/`CorpScreen`), spending exactly `HOURS_PER_DAY` — guarantees advancing exactly one day, but it's just another `spend_time` call, not a refill button. Rest **restores nothing** — every side effect fires identically from any midnight-crossing action.

**Vehicles cut travel time by a percentage, not a daily allowance.** `shops.Item.travel_reduction` (`Slot.VEHICLE` only) replaced the old free-hops-per-day counter: `equipped_travel_reduction` applies fresh to `TRAVEL_HOURS_COST` (2.0) each hop. Catalog: Beater Bike -10%, Tuned Coupe -20%, Armored Towncar -25%. `corp_map_screen._travel_hours(character)` is the one place the formula lives — both the charge and the pre-travel hint call it.

**First-slice hour costs, not balance-simulated**: `TRAVEL_HOURS_COST` 2.0, `Scene.hours_cost` default 4 (gigs/legwork), job `hours_cost` 8/12 (tier 0 / 1+), Rest/hospital exactly `HOURS_PER_DAY`. `JobTiming.deadline_day`/`scheduled_day` windows (`randint(2,5)`/`randint(1,4)` days) were tuned against the old stamina pace and may feel tighter now — worth a playtest, not a pre-emptive numbers change.

`saves.SAVE_VERSION` bumped to 29: a pre-v29 pickled `Character` has `day`/`stamina`/`free_travel_used` instead of `elapsed_hours`.

### Fixers & job generation (`shadowguy/fixer.py`, `shadowguy/jobs.py`)

Jobs are gated behind a persistent Fixer roster (`fixer.FIXER_ROSTER` / `create_fixers()`). Each `Fixer` holds up to `max_offers` `JobOffer`s, procedurally generated (`jobs.generate_job`) from `jobs.ARCHETYPES` — seven templates (Heist / Extraction / Sabotage / Intrusion / Wetwork / Burglary / Data Heist — the last two documented in their own sections), difficulty/reward scaled by a day-derived tier, flavor from word banks.

**Every fixer in the roster is seated every run**, one per distinct district. A roster row is `(id, name, specialty, faction_id)`: `faction_id` None is a street contact (neutral ground, never the player's start tile); a real `faction_id` is an inside contact, seeded on a district that faction actually owns — corp turf isn't off-limits to its own fixer. `create_fixers` samples a neutral group and one group per faction independently (`fixer._seat`), so two fixers never share a district. `Fixer.faction_id` doesn't constrain which corp that fixer's own offers target (`generate_job` picks independently) — a Ghostwire fixer can hand you a job against Ghostwire itself.

**A job is a sequence of typed stages.** Every archetype walks `StageType.APPROACH` → `OBJECTIVE` → (`COMPLICATION`) → `EXFIL`, each with its own prompt and approach pool (`_ARCHETYPE_ROWS`/`JobStage`). `OPTIONAL_STAGE_CHANCE` maps a stage type to how often it appears at all — absence from the table means mandatory, so membership *is* the "optional?" test. The complication is its only entry (0.4), so a job runs **3 or 4 stages**, dropped before ids are handed out (stage_0..n stay contiguous). `StageType` is the intended hook for hired support (a netrunner covers `OBJECTIVE`, muscle covers `EXFIL`) — nothing reads it that way yet.

Two import-time guards: the **last stage cannot be optional** (the final stage carries all reward), and each stage's `prompt` is `.format`-checked over the real field set (a bad `{field}` fails at import, not mid-job).

**Crew roles (`scene.Role`/`Posture`, `jobs._role_for_stage`).** Every generated job carries `Scene.roles`: one `Role` per beat — a `beat` label, the fitting runner `specialist` (Netrunner/Solo/Infiltrator), and a `Posture` (`ON_SITE`/`REMOTE`). **Derived, not tabulated**: `_role_for_stage` reads the beat's lead (cleanest) approach, maps its skill's stat through `SPECIALIST_FOR_STAT`, marks `REMOTE` iff that skill is in `REMOTE_SKILLS` (just `hack`). Derived from the template pool's lead, so roles don't vary with which approaches an offer drew. `Role` is plain data in `scene.py` (strings + `Posture`, not `jobs.StageType`) so `scene.py` needn't import `jobs`.

**Crew capacity runs along `Posture`**: several `ON_SITE` runners but at most one `REMOTE` — free rather than enforced, since only `hack` beats are `REMOTE` and a job has one objective.

**Runners & crew (`runners.py`, `BarScreen`, `Character.crew`/`CrewHire`).** `RIVAL_RUNNERS` — Specter (Netrunner), Juncture (Solo), Mireille (Infiltrator) — carry a `rating` (future run-time effect), `daily_cost`, `job_cut`. `BAR` opens `BarScreen`: pick a runner, then terms —

- **Indefinitely** — draws `daily_cost` every day tick (`Character.pay_crew_wages`); miss payroll and they **walk** (no debt).
- **For a job** — signed for `job_cut` of that job's payout, taken via `SceneScreen._take_crew_cut`; ends with the job.

**Both terms are discounted by the recruiter's `Leadership`** (a `cool` skill, read not rolled). `runners.recruit_wage`/`recruit_cut` shave cost by `skill_value("leadership")` above `LEADERSHIP_BASE` (2), `LEADERSHIP_TERMS_STEP` (3%)/point capped at `LEADERSHIP_TERMS_CAP` (20%) — **one-directional, floored at zero, never a markup**. Every cost-reading call site goes through them rather than raw `.daily_cost`/`.job_cut`. Computed live off current Leadership, not locked in at hire.

Neither term costs anything upfront. `Character.crew` is `list[CrewHire(runner_id, job_id | None)]` (`None` = indefinite); one live hire per runner. For-job hires are discharged when their job leaves `accepted_jobs` (`_discharge_orphan_crew`). Crew earns `crew_experience` from completed jobs (see Post-creation experience) but no spend path yet. **Still to come**: assigning a hire to a `Role.filled_by`, and the run-time effect. First-slice: the whole roster is hireable at *any* bar.

**A job stage is several `Approach`es, not one check.** Each stage holds a pool of `Approach`s (`skill`, `difficulty_delta`, `flavor`) — hard/clean, middling, easy/bloody. The stage rolls its base difficulty **once**; every approach is offset from it.

**Damage is derived from `difficulty_delta`, never written beside it** (`DAMAGE_FOR_DELTA` = `{1:1, 0:2, -1:3, -2:4}`; a critical failure deals the same plain damage — the routed fight is the real punishment). Calibrated against job length (a body-1 runner has 15 health; an earlier, steeper curve produced a 13% death rate). **Re-run the balance sim if you touch the curve or stage count.**

Difficulty ramps by `STAGE_DIFFICULTY_RAMP` spread *across* the job rather than `+1` per stage index, so a 4-stage job is more checks, not a steeper climb — `REWARD_PER_EXTRA_STAGE` pays a premium for the same reason.

**A generated job offers a subset of each pool, not the whole thing.** `generate_job` takes the full pool `FULL_POOL_CHANCE` (0.35) of the time, else exactly `PARTIAL_POOL_SIZE` (2), keeping order. So two Heists differ in which approach they withhold. Pools should stay wider than `PARTIAL_POOL_SIZE` (a pool of exactly two never varies; smaller would make `rng.sample` raise, guarded at import). `PARTIAL_POOL_SIZE` is an exact draw size, not a floor.

**Some jobs are a specialist's work**: `Intrusion` for the Netrunner, `Wetwork` for the Solo. `jobs.archetype_specialist()` is **derived, not tabulated**: a job whose every beat leads with the same `SPECIALIST_FOR_STAT` specialist *is* that specialist's contract — `strength`/`body` both map to Solo, so Wetwork's lead skill can vary stage to stage and still test as one specialist's job. `generate_job` **pins the lead through the partial draw** for these, so a Netrunner job always offers netrunning; `Scene.roles` falls out already-correct.

A specialist job **guarantees its specialist a lane; it does not lock anyone else out** (Enforcer on Intrusion: ~15% completion vs Hacker's ~69%; the same now holds for Wetwork in reverse).

Why Intrusion exists is balance, not flavor: the generic table (Heist/Extraction/Sabotage) is Strength/Agility work — over 3000 jobs, `intelligence` on 30.7% of stages, `perception` on 9.0%, vs ~52% each for strength/agility. A Hacker finished only **11%** of jobs pre-Intrusion. Adding it moved them to **~69% completion, 220 cash/stamina, 3.3% deaths** (measured under the old stamina economy — re-run before relying on the cash-efficiency figure). **`perception` is still at ~9% with no job that wants it** (only the optional complication and legwork reach it) — a Recon archetype is the obvious next one.

**Wetwork is not that same fix** — it exists for parity with Intrusion (an unambiguous contract for the Enforcer), not to correct a stat gap: strength/body were already the best-served stats, so a second specialist there widens the skew. **Not yet run through the balance sim** (predates Wetwork, measured with four archetypes not five).

The load-bearing rule, enforced at import over the whole pool: **approaches in one stage must sit on different core stats** (a stage is a gate every build must pass; the same failure `corpmap._filler_pool` guards against on legwork). Gigs are exempt (optional/self-selected). Simulated over three presets × archetypes: **45–61% completion, under 2.2% deaths** (a failed stage still advances, costing health and the final reward, never the run). **These numbers predate Wetwork, Burglary, and Data Heist** — re-run before relying on them with all seven archetypes.

**Gig payouts scale by day-tier** (`gigs.GIG_CASH` = 80/110/150 by `_gig_tier`): plain success pays cash + `GIG_STANDING_GAIN` (1) standing; a crit pays ~1.6x + 2 standing (+1 rep). Only cost of attempting is the hours up front (`Scene.hours_cost`, 4 default, paid regardless of outcome). A plain failure costs no health, just `GIG_FAIL_STANDING_HIT` (-1) + `GIG_FAIL_REP_HIT` (-1). A critical failure: same -1 standing but `GIG_CRIT_FAIL_REP_HIT` (-2) rep + `GIG_CRIT_FAIL_DAMAGE` (-3) health — the one place a gig still touches health. **Not** re-simulated against jobs. Levers: `GIG_CASH`/`GIG_DIFFICULTY`, `GIG_STANDING_GAIN`, `GIG_FAIL_STANDING_HIT`/`GIG_FAIL_REP_HIT`, `GIG_CRIT_FAIL_DAMAGE`/`GIG_CRIT_FAIL_REP_HIT`, `Scene.hours_cost`.

Jobs are run **against a real corp, on the real map**. `generate_job` takes the `CorpMap`, picks a faction-owned `Territory` then one of its `Location`s as the site — `generate_job`/`refresh_offers` need the map threaded through. The job records `target_faction_id`, `target_territory_id` and `target_location_id`; flavor names that corp/district/building. Deliberately no separate corp/venue name list — don't disconnect jobs from the map.

Every job offer carries a `JobTiming`: **no deadline** (any day while on the board); **soft deadline** (`deadline_day`, inclusive, expires after); **hard scheduled day** (`scheduled_day`, runnable only that exact day).

Flow: pick a fixer (MainMenu Fixers rows or `ContactsScreen`) → `FixerOffersScreen` → accept → moves from the fixer's board (freeing a slot for `refresh_offers`) into `Character.accepted_jobs`, appears in the activity list alongside its generated legwork. No `FixerListScreen`/`f` binding — fixers are list rows. `on_new_day()` drops expired accepted jobs; `expire_offers`+`refresh_offers` do the same for board offers. Completing a job (reaching a stage with no `next_stage`) removes it via `remove_job(scene.id)` — one-shot, unlike gigs/legwork.

### Security contracts (`shadowguy/security.py`) — first-slice prototype

A **different shape of Fixer work**: a standing engagement resolved one night at a time, not a `Scene` walked once (nothing in `scene.py` is day-aware). Closest analogue to `Character.pay_crew_wages()`, inverted — the corp pays the runner, plus a lodging-waiver side effect.

Offered by a Fixer like a job (`Fixer.security_offers`, capped at `max_security_offers`, topped up by `refresh_security_offers` alongside `refresh_offers`), targeting a faction-held Territory/Location the same way `generate_job` does. `FixerOffersScreen` lists both offer kinds together; accepting moves it into `Character.security_contracts` (`accept_security_contract`) — no Scene to push.

**Progress is gated on physical presence, not a resource.** `app._apply_day_tick`, before charging lodging, resolves every contract whose `territory_id` matches where the runner is (`security.resolve_security_night`), then decides whether to waive lodging — any active contract there waives it regardless of that night's result. No presence = no progress and no penalty; there's no deadline to expire against, so it just waits.

**Each night is one `checks.resolve_check`** against `SecurityContract.skill` (one of `WATCH_SKILLS`: sight/listening/tactics/read_the_room, picked at generation), `difficulty` fixed at generation from `DIFFICULTY_BASE`. That table deliberately mirrors `gigs.GIG_DIFFICULTY` rather than `jobs.DIFFICULTY_BASE` — a lower curve would make `CRITICAL_FAILURE` mathematically unreachable given `pool_for_difficulty`'s cap.

Four outcomes, and a critical failure is not a cheaper plain failure:

- **plain failure** — `NIGHT_FAILURE_DAMAGE` health, no pay, but `nights_completed` still advances.
- **success/critical success** — pays `nightly_pay` (crit pays `CRITICAL_SUCCESS_PAY_MULT`×); reaching `nights_total` also pays `completion_bonus` and raises standing/fixer trust/rep — the **positive** case of `factions.standing_shift`, opposite sign from `jobs.JOB_STANDING_HIT`.
- **critical failure** — costs the *same* health as a plain failure, but ends the contract immediately: standing/trust/rep move against the runner, `MainMenu` drops it. No fight, no combat routing.

`security.resolve_security_night` returns a `NightResult` (roll, pay, bonus, blown, completed) so `MainMenu` builds `notify()` text from data. **Not yet balance-simulated** — `NIGHTLY_PAY_BASE` (35/50/70) sized against `RivalRunner.daily_cost` (45/55/60); `NIGHTS_RANGE` (3–5) × pay + a 50% completion bonus lands near a job's `REWARD_BASE`, spread slower/safer but immobilizing (can't work elsewhere without abandoning progress) — a judgment call, not a simulated one.

### Combat (`shadowguy/combat.py`, `screens/combat_screen.py`)

Combat is the **only part of the game that isn't a single check** — but it's still the same dice: every roll goes through `checks.resolve_check()`. A round: **you take one `Action`, then every standing non-stunned enemy attacks you.**

**A fight is a `Stage`.** `Stage.combat` holds an `Encounter` (prompt, enemies, victory/escape `Outcome`), routed to by an ordinary `next_stage`. A stage is *exactly one* of choices, a fight, or a tactical map, never mixed (guarded in `Scene.__post_init__`) — a combat stage's "choices" are `combat.available_actions`, from the runner's gear/skills, not the scene.

`Encounter` lives in **`scene.py`, not `combat.py`** (it holds `Outcome`s; `combat.py` must not import `scene`). `combat` owns *how* a fight resolves; `Encounter` owns *what winning or running is worth*, via an ordinary `Outcome`.

**Two doors into a fight:**

- **You chose it (ambush).** `generate_job` appends `AMBUSH_LABEL` ("Take them first") to *every* stage on top of the drawn pool — a Tactics check, guaranteeing a bleeding-but-real route through for every build.
- **You botched into it (going loud).** A **critical failure only** routes here; plain failure still just costs health and advances (the property `DAMAGE_FOR_DELTA` is tuned around).

`combat.drop_for_result()` reads the drop straight off the routing `CheckResult` — no extra `Outcome` field needed: success = `Drop.PLAYER`, plain failure = even fight, critical failure = `Drop.ENEMY`.

**Enemy *count* is the real lethality lever** (every one swings every round) — so that's what the drop moves, not just initiative. A landed ambush removes one enemy *and* gives a free round; going loud hands them a free opening attack. (A free round alone let the sim's ambush kill a Hacker 22% of the time it was taken — too costly for a "guaranteed way through.")

**Actions deliberately span all six stats**: attack (weapon-dependent skill), **brace** (Toughness/Body), **read the fight** (Tactics/Int, banks a next-attack bonus), **face them down** (Intimidation/Cool, breaks the weakest enemy's nerve), **break and run** (Dodge/Agility), plus a row per grenade. Bare hands, bracing and running are always available.

**Running always works — the Dodge check only decides what it costs you** (a clean break, or one parting shot from *one* enemy). Load-bearing: without this, the build most needing the exit (a Hacker: 15 health, no Agility) is exactly the build that can't make the roll (it failed ~65% of the time, ate the round, and the squad kept swinging). **A fight must never be a cage.**

**Weapons are the damage, skills are the hit.** `skill_value` decides connection; `shops.Item.damage` decides the cost — the only place a weapon's profile is written. Unarmed is always an attack (`UNARMED`, Grapple), just a bad one.

**Balance, simulated over three presets × job archetypes (best-odds approach, running below 25% health):**

| | never picks a fight | fights when locked out (with a monoblade) |
|---|---|---|
| Enforcer | 60% paid, 0.4% deaths | 74% paid, 1.7% deaths |
| Hacker | 43% paid, 6.9% deaths | 55% paid, 25% deaths |
| Infiltrator | 51% paid, 4.3% deaths | 59% paid, 17% deaths |

**Fighting pays better for the build that invested in it and kills the one that didn't** — that's the intended shape. "Never picks a fight" isn't zero fights — critical failures going loud alone take the Hacker from a ~2% pre-combat baseline to ~7%. The Hacker's death rate is extremely sensitive to *when they run* (14% breaking at 40% health, 31% at 25%) — the flee rules are not a place to tune casually. Re-run the sim if you touch `_ENEMY_ROWS`, `ENEMY_TIERS`, `DEFENSE_BASE`, or the flee/drop rules.

### Tactical combat (`shadowguy/tactical.py`, `scene.TacticalStage`, `screens/tactical_screen.py`)

A **third kind of stage**: some job fights play out on a grid. It is emphatically **not a second combat model** — every attack still goes through `combat.resolve_hit` (shared, so *one hit formula, two surfaces*), enemies are `combat.Enemy`, damage/soak/health unchanged. The grid only adds *position*: LOS and range gate legal attacks, and **cover is nothing but a raised to-hit difficulty** (verified to swing hit rate ~91%→74%).

**The module split mirrors combat exactly.** `tactical.py` is a leaf owning *how space works* (`Grid`/`Tile`, tcod FOV+A*, turn engine), imports no `scene`. `scene.TacticalStage` is the grid analogue of `Encounter` (holds `Outcome`s, routed by `next_stage`). `TacticalScreen` is the `CombatScreen` counterpart.

**LOS and range are separate gates, deliberately.** `has_line_of_sight` is unlimited-radius FOV obstruction; weapon reach is an explicit distance check (`weapon_range`) — tcod's FOV radius is Euclidean and excludes the exact-radius cell, so reading range off it would be off by one. Firearms kite; melee has to close.

**The turn model**: move up to `speed` tiles, then one action. **Enemy reach is per-enemy** (`combat.Enemy.reach`, tactical-only): armed guards shoot from reach 6, street muscle/the bruiser close to reach 1. The AI is "close via A* until in range, then hit" — a ranged enemy holds distance, only advancing when it loses the shot; cover matters on defense. **Fleeing is positional and always works** — reach an exit tile, no roll, no parting shot — the same law as `combat.py`'s flee, enforced spatially.

**Maps are generated** (`tactical.generate_map`): a BSP partition (tcod, seeded off the caller's rng via a derived int for reproducibility), rooms + corridors with scattered low cover, player entering by `exits` at one end, squad at the other. **Retries until every enemy spawn and exit is reachable** (raises rather than hand back an unplayable fight). Maps are `TAC_MAP_WIDTH`×`TAC_MAP_HEIGHT` (30×10), sized to fit `TacticalScreen` at 80×24 without scrolling. Cover density is softly themed by site `LocationKind` (`jobs._cover_density`, `.get`-with-default, no import guard).

**Which jobs are tactical is decided once per job** (`jobs.TACTICAL_FIGHT_CHANCE`, 0.35) — a job's fights are all grid or all abstract, never mixed. Routing otherwise unchanged (`{stage}_fight` holds a `TacticalStage` instead of `Encounter`, same Outcomes).

**Balance — far swingier than abstract combat, by design.** A tier-2 Hacker dies **~68% rushing with a monoblade** but **~10% kiting with a pistol** (abstract baseline 25%) — ranged is *safer* on the grid, melee-only crit-failing into a high-tier fight is real danger. **Left as-is on purpose** — fleeing is always available, and ~68% is a deliberately naive rush. Ranged guards pressure the rusher more than the kiter (firearm outranges them 8>6; the kiter's safety is really a damage-economy fact, not a positioning one, so bumping guard reach barely moves it). Re-run the sim before touching flee/exit rules, `TACTICAL_FIGHT_CHANCE`, enemy counts, or reach.

### Burglary jobs (`shadowguy/jobs.py`, `scene.BurglaryStage`, `screens/burglary_screens.py`)

A **fourth kind of stage**, the second to break the "text list of Choices" mold: replaces one job archetype's whole APPROACH stage with a two-phase UI — pick a labeled entrance on a diagram (`EntrancePickScreen`), then walk a generated interior (`BurglaryWalkScreen`) from that entrance's spawn to an objective tile, avoiding static guards. It's the 6th `jobs.ARCHETYPES` entry (generic, mixed leads, no specialist); every other stage of Burglary itself is an ordinary `Choice` list.

**The entrance check resolves the instant it's picked, before any walk.** `scene.Entrance` is `Choice`-shaped plus a `spawn: Coord`; `resolve_entrance` applies health/cash/rep/standing immediately, only stage advancement (`next_stage`) waits on the walk (the walk is spatial risk on top of the check, not a second roll). Burglary's one departure: `BurglaryStage.spotted`, a second `Outcome` that can fire if a guard's sightline catches the walk, stacking on whatever the entrance check already did — keep its cost modest.

**Three doors into the fight stage.** Critical failure on a real entrance routes to `fight_id`, same as any stage. So does the guaranteed "Take them first" ambush entrance. And `BurglaryStage.spotted` is a third route, triggered positionally rather than by roll. `SceneScreen._on_entrance_picked` tells fight-routing apart from a normal advance by checking whether the *target* stage is combat/tactical, not the `CheckResult` — covers both cases without `scene_screen.py` needing to know `jobs.py`'s naming convention. (A real double-apply bug lived here during development — fixed by routing through the ordinary `_await_continue`, same as any other stage's critical failure.)

**The interior is generated by `tactical.generate_building`, not `generate_map`** — several distinct entrance rooms (one per `Entrance`, draw order) converging on one objective room, with up to `BURGLARY_GUARD_COUNT` (1, fixed not tier-scaled) placed elsewhere. Reuses `_bsp_rooms`/`_carve_room`/`_carve_tunnel`/`_scatter_cover` verbatim. Reachability is re-verified for *every* entrance (not `generate_map`'s single-source `_verify_map`), same retry approach.

**A guard's line of sight is range-capped** (`GUARD_SIGHT_RANGE`) — same reason `combat.Enemy.reach` caps an attack's (unlimited-radius FOV would let a guard spot across an open room instantly).

**The walk state is deliberately not `TacticalState`.** `BurglaryWalkState` is four fields (grid, position, objective, guards) plus `move_walker`/`spotted` — no turns, nothing to fight while sneaking. A guard's sightline ends the walk outright. `BurglaryWalkScreen` is a stripped `TacticalScreen` copy (no attack/turn/HP-moves UI); the whole map renders always (no fog-of-war), matching `TacticalScreen`'s own precedent.

**`EntrancePickScreen`'s diagram is a fixed illustration**, deliberately *not* `render_ascii_map` (built for dozens of interconnected territories; 3–4 unconnected entrances don't need it) — a static glyph over an ordinary `ListView`.

**Not yet balance-simulated**: guard count, sight range, `BURGLARY_SPOTTED_DAMAGE` are first-slice tuning knobs.

### Data Heist & matrix combat (`shadowguy/matrix.py`, `scene.MatrixStage`, `shadowguy/jobs.py`, `screens/matrix_screen.py`)

**Data Heist** is the 7th `jobs.ARCHETYPES` entry (after Burglary), a second Netrunner specialist beside Intrusion — but where Intrusion resolves as ordinary checks and meat fights, a Data Heist's fights **are matrix combat**. A **remote** hack — the netrunner never enters the building, so `_role_for_stage` marks every beat `REMOTE` (`hack` is the lone `REMOTE_SKILLS` member).

**Matrix combat is a third *fight surface*, not a new stage-type pipeline.** Abstract `Encounter`, tactical grid, and matrix are the three things a `{stage}_fight` can hold; `scene.MatrixStage` is the ICE analogue (holds `Outcome`s; `matrix.py` mustn't import `scene`), joins the "exactly one mode" guard. `archetype.matrix` (a whole-job flag, unlike per-stage `JobStage.burglary`) makes every fight a `MatrixStage` and suppresses the tactical roll — no meatspace enemies to roll.

**A matrix fight is a node network** (`MatrixNetwork`/`MatrixNode`/`generate_matrix_network`), ~5–10 nodes, more like `corpmap`'s territory graph than a tile grid, fresh per fight. `MatrixNodeRole`: `ENTRY` (never guarded), `SLAVE` (free waypoint), `IC` (guarded, must clear), `DATA` (objective), `CPU` (optional, tougher, hangs off `DATA` by a flat chance — a detour past the objective, not a gate), `CACHE` (optional side loot off any ordinary waypoint — gates nothing). Generation guarantees an `ENTRY`→`DATA` spine plus a few branching edges.

**`CACHE` differs from `CPU`**: hangs off any `SLAVE`/`IC` waypoint, pays off immediately — clearing it drops a `shops.STOLEN_DATASHARD_ID` ("Stolen Datashard", value 180) into inventory in `_settle_run`, bypassing the `Outcome` pipeline (kept even if the run is later seized/blown). Attach chance is per-tier in `MATRIX_NETWORK_TIERS`, zero outside tier 1 (0.0/0.15/0.0 at tiers 0/1/2).

**The run renders as an ASCII node diagram** (`render_matrix_network`, ported from `render_ascii_map`'s look). Nodes carry no persistent x/y, so `_matrix_network_layout` derives a Sugiyama-style layered layout (column = BFS hop-distance from `ENTRY`; row = barycenter lane keeping single-parent chains aligned, forks spreading to a nearby lane). Non-grid-adjacent connections just aren't drawn (still legal, same simplification `corpmap`'s renderer accepts).

**The round-by-round ICE engine is reused, not rebuilt.** Every roll still goes through `resolve_check`, hits sized by `combat.resolve_hit` (now three surfaces). `MatrixState`/`start_matrix`/`take_matrix_turn`/`available_matrix_actions` now resolve one node's guardian, not the whole encounter. `MatrixState.is_final_node` (default `True`, so pre-existing direct `start_matrix()` calls are unaffected) is how `_settle`'s `SEIZED` branch distinguishes "cleared a mid-network guardian" from "won the whole run."

**`matrix.MatrixRunState` is the new orchestration layer**: `start_matrix_run` jacks in at `network.entry_id` (never guarded); `move_to` walks a connected node, opening a fight via `start_matrix`/`engage_node` on first entry to a guarded uncleared node; `take_run_turn` delegates to `take_matrix_turn`. **Integrity, program charges, and the log are run-wide, not refilled between nodes** — `engage_node` swaps in a fresh guardian on the *same* `MatrixState`. `is_final_node` only ever `True` for `DATA`.

**Clearing `DATA` does not auto-win — `matrix.extract()` does, only after `DATA` is cleared** (so `CPU`, a detour past it, stays reachable). `_settle`'s per-node `SEIZED` just marks that node cleared; the player must choose to extract (or detour to `CPU` first — no reward wired yet). `jack_out()` (available in navigation mode too) always ends `EJECTED` with no partial credit, even with `DATA` cleared — a clean binary outcome.

- **Integrity, not health.** `player_integrity` = `BASE_INTEGRITY + INTEGRITY_PER_INT * stat("intelligence")` (gear-included) is a per-run pool, never touching `Character.health`. Draining it **ejects** (`MatrixOutcome.EJECTED`), doesn't kill — **there is no death in the matrix** (losing blows the contract via the same blown-job escape Outcome, never the run).
- **Intelligence's actions, not the six-stat spread** (matrix is the Hacker's arena on purpose): **Breach** rolls `hack` (deck damage), **Harden** rolls `tinkering` (brace), **Analyze** rolls `infer` (read), **Jack out** always works (the escape valve). A non-hacker can fight here but bleeds.

**The deck is the weapon.** `skill_value("hack")` decides the hit; `shops.equipped_deck_rating` decides ICE cost (`player_attack_damage` = `DECK_BASE_DAMAGE + rating`, or `BARE_JACK_DAMAGE` bare). No new `Item` field/`Slot` — a cyberdeck is already a `slot=None` item, and a deck's rating is its Intelligence bonus, so the four existing decks work unchanged. (Any non-deck `slot=None` `Item` would now be read as a deck — a load-bearing convention.)

**The `drop` only matters for whichever node is engaged first** — breaching cleanly buys a free ICE round, crit failure hands a free bite; every later node plays neutral.

**Shown to all builds, warned not locked.** A Data Heist appears on everyone's offers/menu, but a scene with a matrix stage shows **"⚠ needs a cyberdeck / more Hack skill"** (`matrix_warning`→`matrix_readiness`) when no equipped deck or `hack` below `MIN_READY_HACK` (5). Advisory only.

**Not yet balance-simulated, swingier than intended.** `BASE_INTEGRITY`/`INTEGRITY_PER_INT`, `_ICE_ROWS`/`ICE_TIERS`, `FIREWALL_BASE`, deck-damage constants, `MIN_READY_HACK`, `MATRIX_NETWORK_TIERS` are all hand-set. Pre-network flat-fight numbers (tier 2, always-attack policy): a decked hacker (Int 6 + `zetatech_rig`, `hack` rank 4) seizes ~100%, a deckless Int-1 runner is ejected ~99% — intent realized, but the decked hacker was near-invulnerable (firewall scales off `infer`, which the deck's Int bonus also lifts). Re-run a presets×tiers sim before leaning on either the old rates or the network shape.

**Deferred**: an on-site variant (embedded hacker running smaller matrix fights mid-job, ejecting *painfully* via a health cost instead of blowing the run — an eject-cost constant away, not a new engine); a mechanical reward for reaching `CPU` (unlike `CACHE`, no payoff wired).

**Cyberdeck programs (`shops.Program`, `Item.program_slots`)** are the netrunner's loadout. A deck carries `program_slots` RAM, spent via `Program.ram_cost` per install (every program costs 1 RAM today). `Program.uses_per_fight` distinguishes passive (`0`) from active (nonzero — charge-capped if positive, unlimited if `-1`/`EXTRACT_UNLIMITED_USES`), enforced at import (passive fields zeroed on an action program and vice versa; an action program sets exactly one of `action_damage`/`action_skip_ice`/`action_sleaze`/`action_extract`/`action_analyze`). Acquisition is two steps: `buy_program` adds to `Character.owned_programs` (a set); `install_program`/`uninstall_program` move an owned program onto/off a specific deck's `installed_programs` — free, no check.

Only the **active deck** matters (`shops.active_deck_entry`, the best-rated equipped deck). A passive program's bonus folds into the corresponding base formula; an action program shows as a `MatrixActionKind.PROGRAM` row, gated on remaining charges (`MatrixState.program_uses`, seeded at first node engagement, carried forward like integrity). `available_matrix_actions` takes `program_uses` as an optional second arg (matches `combat.available_actions`'s shape), so pre-existing call sites are unaffected. A damage program lands with no roll (guaranteed hit for the charge); a skip-ICE program increments `ice_skip_rounds` (same field a clean breach already grants). `action_analyze` is navigation-mode only, never offered mid-fight.

**Today's catalog (`shops.PROGRAM_CATALOG`, Computer Store): Sleaze, Extract, Analyze, Icebreaker** — every program is now active (the earlier passive-buff roster is gone). **Sleaze** (`action_sleaze`) is a flat three-way success/fail/critical-fail split (not via `resolve_check`), shifting (`SLEAZE_MARGIN_STEP`, capped by `SLEAZE_MAX_SHIFT`) with the margin between `hack` and the ICE's defense — only the success/crit-fail tails move, fail stays fixed. Success drops the ICE outright; crit failure alerts it into an extra bite. **Extract** (`action_extract`) is aimed at the node's data — legal only when `MatrixState.is_extractable` (current node is `DATA` or `CACHE`); a landed hit ignores the target's soak entirely. Unlimited-use; every missed roll adds `SECURITY_PER_FAILED_EXTRACT` to `MatrixState.security` instead (risk, not charges). **Analyze** is the navigation-mode node-reveal program (below). **Icebreaker** (`action_damage`) is the old guaranteed-hit program, now unlimited-use.

**`MatrixState.security` is a run-wide alert ratchet, never reset per-node.** Raised by a missed Extract roll and by **Sentinel ICE** (`security_per_round` on `Ice`, 0.3/round — a guardian that never bites integrity, just logs presence each round it survives). Security rides along as every ICE's attack advantage this fight (`int(state.security)` bonus dice, floored). Cross `SECURITY_HOSTILE_THRESHOLD` (3) and every subsequent node opens hostile (`Drop.ENEMY`) instead of neutral, checked once per new engagement, never un-trips.

**A matrix node's role is hidden until revealed.** `MatrixRunState.revealed_node_ids` (kept on the run, not `MatrixState`, since `ENTRY` is never guarded) gates what `_matrix_node_label` prints — unrevealed shows neither role nor guarded/clear status. Two reveal paths: physically arriving (`_enter_node`, unconditional), or the installed Analyze program reading a connected node remotely (`analyze_node`, same `ANALYZE_DIFFICULTY`/`hack` roll as the in-fight action, aimed at a node instead) — a miss costs nothing. `analyze_uses` is the navigation-mode counterpart to `program_uses`.

**`CyberdeckScreen` (`screens/info_screens.py`) is where a deck's programs get installed/uninstalled** — its own sidebar category + `d` binding. Moved out of `InventoryScreen` because `active_deck_entry` is a cyberdeck-specific question; `InventoryScreen` keeps the generic equip/stow toggle. Adding this 10th sidebar category needed a fix: `MainMenu`'s `#categories` `ListView` could show only 9 rows at 80×24, clipping `corp` silently — fixed by matching `#sidebar`'s padding to `#main_panel`'s. **Lesson: a tight row budget fails silently — drive the real screen at `size=(80, 24)` and check container vs. virtual size.**

**None of `security_per_round`, the Sleaze odds curve, `SECURITY_HOSTILE_THRESHOLD`, or the catalog's prices are balance-simulated.**

### Faction standing (`shadowguy/factions.py`, `shadowguy/scene.py`, `shadowguy/character.py`)

The first real runner→corp coupling. `Outcome.standing_delta` moves standing with the scene's `target_faction_id` — the Outcome itself never names a faction, so one job template works against any corp. `factions.standing_shift()`: the corp hit moves by `delta`, **every rival moves the opposite way at half weight** (`-delta // RIVAL_WEIGHT`, `RIVAL_WEIGHT` = 2) — hurting a corp is a favour to its competitors. Applied in `scene.apply_outcome`.

Today only a *completed* job moves standing (`jobs.JOB_STANDING_HIT` = -2, on the final stage's success/critical-success). Botched and abandoned jobs cost nothing — a balance choice.

`Scene.__post_init__` rejects a `standing_delta` on a scene with no `target_faction_id` — a gig can't anger a corp it was never aimed at.

**Room left for territory effects:** `Scene.target_territory_id` records *where* a job hit; nothing consumes it beyond flavor yet. A future territory-control effect belongs as a new `Outcome` field applied in `apply_outcome`, keyed off `target_territory_id` — don't invent a second effect pipeline.

### Rival AI (`shadowguy/rivals.py`)

The world's other actors getting a turn of their own (Faction standing above is the player's actions moving the corps). A parallel resolution module like `security.py`/`encounters.py`, not a `Scene`: `resolve_rival_day` is called once per day from `_apply_day_tick`, returns a `RivalAction` (`kind`, `actor_id`, `day`, `territory_id`) per acting actor.

**Factions do something real: territory pressure.** Each day every `FACTIONS` corp gets one roll (`EXPANSION_CHANCE`, first-slice) at claiming one *neutral* territory bordering its own ground (`_expansion_candidates`). Scoped to neutral ground only — taking a rival's territory is bigger future work. Two permanent exclusions: gang turf (`Territory.gang_id`) and the player's start territory — the same reservation `_grow_blocs` honors at generation.

Claiming (`corpmap.claim_territory(territory, faction_id, rng)`): flips `owner`, reseeds `modifiers` via `_corp_modifiers`, clears `gang_id`. `value` untouched, locations not regenerated. `CorpMapScreen` needs no wiring — fresh instance each push, reads `Territory.owner` live.

**Independent runners have a position now.** Every `RIVAL_RUNNERS` entry gets a `RivalAction` too, except while on the player's crew. Each wanders (`rivals._wander`): placed randomly on first sight, then a `RUNNER_MOVE_CHANCE` (0.3, first-slice) coin flip per day either hops them to a connected territory or leaves them put. Position tracked in `ShadowguyApp.rival_runner_locations` (`dict[runner_id, territory_id]`), mutated in place (persistence is the caller's problem, same as `rival_actions`). No decision logic beyond the wander yet — a runner AI is the natural next step. `rival_actions` is overwritten each day (no history read), part of the save bundle (`SAVE_VERSION` 19; `rival_runner_locations` joined at 31).

**Still deliberately inert past the wander**: no UI surfaces a faction's claim. (A runner's wander position *is* now surfaced — see Surveillance detection.)

**Once the player takes over a Faction, it drops out of this loop entirely.** `resolve_rival_day` takes an optional `player_faction_id`, skips that faction and records no `RivalAction` for it. Default `None` keeps every pre-existing call site unchanged.

### Surveillance detection (`shadowguy/surveillance.py`)

The first reader of `TerritoryModifier.SURVEILLANCE` beyond `corp_turn.py`'s own gates, and the first thing that *does* something with a watched district. A parallel resolution module like `rivals.py`/`security.py`: `resolve_surveillance_day` runs once per day tick, right after `resolve_rival_day` (so wandering runner positions are already settled).

**Scoped to the corp the player is actually running.** Takes `CorpState | None`, no-op when `None`. While set, every territory it owns rolls a detection check against two "known runner" kinds: the player (`location_id`) and every `RIVAL_RUNNERS` entry (via its wandered position).

**Detection is a flat, Surveillance-level-indexed chance, not an opposed check.** `SURVEILLANCE_DETECTION_CHANCE` is a 6-entry tuple (index = the territory's Surveillance, 0..`MODIFIER_MAX`), guarded at import. No player-side counter-roll yet (a Concealment/Stealth skill is the obvious hook). First-slice: even a maxed district (level 5, 0.65) misses more often than not.

**A hit is purely informational.** `corp_turn.Sighting` (`kind`, `actor_id`, `territory_id`, `day`) lives in `corp_turn.py` (avoids a corp_turn↔surveillance import cycle). `CorpState.sightings` is a list, most-recent-first, capped at `MAX_SIGHTINGS_LOG`. No standing/rep/combat consequence wired yet.

**Surfaced as a Surveillance Log panel** — a collapsed-by-default `Collapsible` in `CorpScreen`/`CorpMainMenu` (read-only history, unlike Academy/Research Facility). Each row: `"Day {day} — {who} spotted in {territory}"`, resolved at display time. `app.notify()`s once/day with just a count, not one toast per sighting.

`saves.SAVE_VERSION` 31: `CorpState` gained `sightings`, `ShadowguyApp` gained `rival_runner_locations`. **Not balance-simulated** — the detection curve, `MAX_SIGHTINGS_LOG` (10), `RUNNER_MOVE_CHANCE` (0.3) are all first-slice.

### Corp mode turn loop (`shadowguy/corp_turn.py`, `screens/corp_screen.py`)

The other half of Rival AI: once the player takes over a Faction, they get the same kind of daily move plus more. A parallel resolution module — leaf-ish (imports `corpmap` only, never `scene`/`app`).

**Taking over is a plain menu pick, not an earned one yet.** `CorpScreen` (MainMenu `r`/"Corp") lists the 3 `FACTIONS`; picking sets `ShadowguyApp.corp_state = CorpState(faction_id=...)`. No in-fiction coup mechanic — the same shortcut-before-the-real-gate precedent `TestMenu` sets. Once set, `resolve_rival_day` stops rolling for that faction.

**There are two ways in, producing different games.** New Game → `ModeSelectScreen` (Runner/Corp):

- **Runner** — `CharacterCreationScreen`→`MainMenu`, with `CorpScreen` reachable as one activity among many.
- **Corp** — `CorpSelectScreen` picks the Faction, sets `corp_state`, sets **`ShadowguyApp.corp_only = True`**, switches straight to `CorpMainMenu`. No runner built.

`corp_only` decides which home screen `load_state` reopens (part of the save bundle, not a UI flag). `CorpMainMenu` subclasses `CorpScreen`, adds `MainMenu`'s sidebar layout, neutralizes escape-to-back. The `Character` still exists in a corp-only run (carries `elapsed_hours`/`day`) — just never built or played.

**Corp mode shares the runner's own day clock.** No separate calendar: `_apply_day_tick` (fired from any midnight-crossing action, any screen) collects corp income/research and resets `daily_action_used`, alongside `resolve_rival_day`.

**Income is flat and passive.** `collect_income` sums `TERRITORY_INCOME_BASE + TERRITORY_INCOME_PER_VALUE * value` over every held territory, credited daily. First-slice, not balance-simulated.

**A turn is one real decision, shared by four mutually-exclusive moves** gated on `CorpState.daily_action_used`. All fail closed (no charge/mutation) if already moved or unaffordable:

- **`expand_into`** a bordering neutral territory — the same move `rivals.py`'s AI makes. Cost `EXPANSION_COST_BASE + EXPANSION_COST_PER_VALUE * value`.
- **`train_employees`** at the Academy — flat `ACADEMY_TRAINING_COST` for that many employees (Academy tier, currently always 1) in a chosen `EmployeeCategory`.
- **`build_lab`** at the Research Facility — raises scientist capacity.
- **`build_efficiency_upgrade`** at the same facility — raises per-scientist output.

**Employees come in three categories** (`EmployeeCategory.SCIENTIST`/`.OPERATIVE`/`.RESEARCH_ASSISTANT`), separate `CorpState` fields since they don't do the same thing. `CorpScreen` offers one training row per category, same cost/slot.

**Research is the one corp system with a real internal economy.** A corp holds **exactly one** `RESEARCH_FACILITY`, so `owned_research_facility` is singular and `collect_research`/`build_lab`/`build_efficiency_upgrade` all read it. Sums: the facility's `research_tier` (1 RP/day at tier 1) + `research_rate(corp_state, facility)` per scientist actually working (base rate, raised by Brains 2, plus one per efficiency upgrade) + `assistant_rate(corp_state)` per research assistant actually working (not scaled by efficiency upgrades, though Brains 2 raises it).

"Actually working" is the point: `lab_capacity` (`BASE_LAB_CAPACITY` 1, +1/lab) caps scientists, `assistant_capacity` (`RESEARCH_ASSISTANTS_PER_LAB` 2 × lab count) caps assistants — training beyond capacity produces nothing. Both upgrade tracks are **strictly sequential**: `LAB_UPGRADE_COSTS` (2000, 5000) / `EFFICIENCY_UPGRADE_COSTS` (3000, 7000) indexed by `labs_built`/`efficiency_upgrades`. Efficiency is priced steeper (compounds with staffed scientists). **None of these numbers are balance-simulated.**

`collect_research` returns a **float** (`RESEARCH_PER_ASSISTANT`'s 0.5, Brains 2's 1.25/0.75); `CorpState.research_points` is annotated float but defaults to int `0` (renders `0rp` until an assistant contributes — don't "tidy" that default without noticing the display change).

**If corps ever hold more than one facility**, revisit `collect_research`'s old highest-rate-first fill order (collapsed once nothing could produce a second facility) — bring it back alongside whatever eventually lets corps take each other's territory (the conflict layer `rivals.py` explicitly defers).

**Technology is what finally spends research points** (`corp_turn.TECHNOLOGIES`, six entries, two independent three-deep chains gated by `Technology.prereqs`). Only Worker Surveillance and Brains 2 are researchable from day one. A tech's effect isn't a field on it — read wherever it applies, keyed by id. `CorpState.researched` is a set of ids; research is permanent. Descriptions are `.format`ed from the effect constants at construction. `technology_tree_layout()` derives (column, row) from prereq depth for `ResearchTreeScreen` (`t` from `CorpScreen`/`CorpMainMenu`). A box is never hard-disabled — selecting always attempts `research_technology`, reporting the shortfall or lock reason on the box itself.

**Worker Surveillance** (10 RP): `collect_income` adds `WORKER_SURVEILLANCE_INCOME_BONUS` (10) per held territory (doubles `TERRITORY_INCOME_BASE`); unlocks `raise_surveillance` (`SURVEILLANCE_BUMP_COST` 400 cash, +1 Surveillance up to `MODIFIER_MAX`) — `surveillance_targets` is empty until researched.

**Brains 2 → Brains 3 → Cognitive Uplink** (10/20/35 RP): the research-rate chain, scientist 1→1.25→1.5→2.0 RP/day, assistant 0.5→0.75→0.9→1.2 RP/day. Each tier **replaces** the one below (not stacking) — `scientist_base_rate`/`assistant_rate` walk down from the top researched tier — but a facility's efficiency upgrades still stack on top. `research_rate` takes `corp_state` as well as the facility. Each tier pays per working head (nothing for an unstaffed corp) and *compounds* (faster research buys the next tier faster) — why Brains 2 costs the same as Worker Surveillance despite looking smaller.

**`raise_development` is the second modifier purchase, gated rather than derived.** Costs `DEVELOPMENT_BUMP_COST` (800), legal only where Security *and* Surveillance already clear `DEVELOPMENT_MIN_SECURITY`/`DEVELOPMENT_MIN_SURVEILLANCE` (3 each).

**This is the first deliberate exception to "Development is derived."** `_development()` computes it at generation time and on `claim_territory`, but `raise_surveillance` doesn't re-derive it and `raise_development` buys it directly (gated on Security/Surveillance, not computed) — a district can now sit at high Surveillance/low Development, which `raise_development` lets the player close. Runtime purchase vs. generation-time formula — don't collapse them.

**Neither the tech purchase nor either modifier bump touches `daily_action_used`** — RP/cash are their own gates, repeatable within a day. None of these numbers balance-simulated.

`CorpScreen` renders four stacked sections (territory actions + Academy/Research Facility/Surveillance Log — Technology moved to its own screen), overflowing a single 80×24 viewport (it scrolls; click-position UI tests should drive a taller size, since the map's unseeded rng varies row counts).

**Scientists and operatives still buy nothing directly** beyond feeding research (research assistants) — operatives remain a mechanism ahead of its driver. `saves.SAVE_VERSION` climbed once per shape change: 20 (`corp_state`), 21 (`research_tier`/`research_points`), 22 (`academy_tier`, `daily_action_used` rename, a since-replaced `employees` field), 23 (split into `scientists`/`operatives`), 24 (`labs_built`), 25 (`efficiency_upgrades`), 26 (`research_assistants`, float `research_points`), 27 (`corp_only`), 30 (`researched`).

### Corp map (`shadowguy/corpmap.py`, `shadowguy/factions.py`)

The board is generated fresh each run (`generate_corp_map`): `TERRITORY_COUNT` (38) nodes on an 8×6 (`GRID_COLS`×`GRID_ROWS`) grid, one contiguous blob, wired by a random spanning tree (always connected) plus extra edges (`EXTRA_EDGE_CHANCE` 0.35) for loops. The grid is deliberately larger than `TERRITORY_COUNT` — leftover cells are the holes that stop the blob becoming a solid rectangle.

The three factions: Ironclad Dynamics (weapons), Ghostwire Collective (hacking), Meridian Biochem (pharma).

**The runner owns nothing and starts nowhere.** `_player_start` picks an unclaimed rim node (`_on_grid_edge`) — no `"player"` owner exists on the map; `@` marks presence, not ownership. `_grow_blocs` reserves the start cell (no faction seeds/expands onto it), falling through to the neutral branch for value and modifiers like any open district. (The runner does get an `APARTMENT` location there — a place, not a holding.)

The rim start demands `MIN_START_DEGREE` (2) connections — over 2000 seeds, always neutral/rim/degree 2–3.

At 38 nodes: 18 corp (`TERRITORIES_PER_FACTION` 6×3) + 20 unclaimed (one = runner start). `FACTION_VALUE_SPREAD` (3,3,2,2,1,1) must match `TERRITORIES_PER_FACTION` in length — that's what makes fairness free.

**The tuning constants guard each other at import time**: raises if `TERRITORY_COUNT` outgrows the grid or `DISTRICT_NAMES`, if `FACTION_VALUE_SPREAD`/`TERRITORIES_PER_FACTION` drift, or if the name pool can't cover `MAX_SAME_KIND_LOCATIONS`. Only the faction-count guard depends on the caller (stays in `generate_corp_map`). The name-pool guard is load-bearing: `_make_locations` retries an unbounded `while True` on a collision — an exhausted pool **hangs generation instead of raising**, so grow name pools alongside `TERRITORY_COUNT`.

Faction starts are fair **by construction, not by search**: one contiguous bloc per faction races outward from random seeds, then every bloc gets the same value multiset — can't come out unbalanced. A boxed-in bloc reseeds/retries (~29% of maps need at least one retry, never more than four). Value is assigned *after* ownership — don't invert that order.

District names must be single words (id = lowercased name, used in Textual widget ids).

Each Territory holds 4–6 (`MIN`/`MAX_LOCATIONS_PER_TERRITORY`) Locations: a corp district gets `SPECIALTY_LOCATIONS` (2) of its owner's kind + one filler (`FILLER_KINDS`); neutral gets a random mix. One of each corp's highest-value districts also carries its HQ, and two more its Research Facility and Academy (all injected on top — see Corporate HQs & officers).

The scouting skill per location kind lives in `corpmap.LOCATION_SKILL` (flavor separately in `jobs.LEGWORK_APPROACH_TEXT`) — leans on perception/agility, intelligence on wired places, cool where the read comes from conversation.

The stat behind a kind is **derived, never tabulated twice**: `location_stat(kind)` = `skill_for(LOCATION_SKILL[kind]).stat`. `_filler_pool` excludes fillers sharing the district's specialty stat (else legwork would be three checks of one stat); an import-time loop proves the pool never runs dry for any specialty.

Each Territory carries `modifiers`: Security/Surveillance/Unrest/Development/Restricted, 0..`MODIFIER_MAX` (5). **Development is the one live today** (prices lodging/safehouses). Security/Surveillance are read by Corp mode (gate `raise_development`; Surveillance itself raisable). Unrest/Restricted still seeded+displayed only. Display names in `MODIFIER_LABELS` (don't derive from the enum id).

**Two owner profiles, each one function**: `_corp_modifiers` (garrisoned/watched proportional to value, low Unrest, squeezed Restricted 2–5) vs. `_neutral_modifiers` (Security 1, Surveillance 0, Unrest at `MODIFIER_MAX`, Restricted 0, Development rolled 1–2). `_make_modifiers` dispatches on `FACTIONS_BY_ID` membership — the same question `_location_kinds` asks; keep them agreeing.

**Development is derived, not rolled, on held ground** (`_development`: rises with Security/Surveillance, falls with Unrest) — true at generation/`claim_territory` time only; `raise_surveillance` and `raise_development` (Corp mode) break that at runtime by design (see Corp mode turn loop). Neutral ground escapes the formula deliberately (would pin it to 0).

Consequences of the profiles: no district ever at Unrest 3–4 (held 0–2, neutral exactly 5); Security/Surveillance/Development never reach `MODIFIER_MAX` (best corp district: value 3 + 1 jitter = 4). To make 5 reachable, raise `FACTION_VALUE_SPREAD`'s top rather than special-case the modifier. Per-faction modifier totals aren't equal by construction (unlike value) — a live balance question now that Development prices property. Remaining unread hooks: Security→job difficulty, Surveillance→legwork difficulty, Unrest→flipping ownership, Restricted→market price/availability.

**`CorpMapScreen`'s row budget is exact at 80×24** — `#modifiers` renders its five levers as two lines (bare `n/MODIFIER_MAX` scores) rather than a row each, no vertical padding anywhere, no bar gauge — every row the panels take is a row of the (11-line) board the player can't see. Traps: a wrapping row silently doubles panel height and **`Static.content` won't catch it** (compare `content_size.height`; `#territory_info`'s Locations line does wrap at 80 cols); always drive the real screen at `size=(80, 24)` and compare `#map_scroll`'s `content_size.height` to the map's line count, not panels in isolation.

`render_ascii_map` returns a `RenderedMap` (text + per-label `NodeSpan` with line/column/absolute offsets) for mouse hit-testing/highlighting — kept ASCII (not per-node widgets) so connectors survive. At 38 nodes it renders 128–162 columns wide (mean ~151), living in a horizontally-scrollable container (horizontal scroll expected, vertical is the thing to avoid). `_refresh` only re-renders on cursor/runner move; hover just restyles the cached `RenderedMap`.

Known quirk: the spanning tree + extra edges leaves plenty of degree-1 dead ends elsewhere (fine — only the *start* node is guaranteed an out, since that's the one the time budget can't recover from).

### Corporate HQs & officers (`shadowguy/corpmap.py`, `shadowguy/factions.py`, `screens/shop_screens.py`'s `CorpHQScreen`)

Each corp has one HQ (`LocationKind.CORP_HQ`) in one of its highest-value districts (`value == max(FACTION_VALUE_SPREAD)` = 3; a faction holds two such, one picked). Injected not rolled — slot reserved up front, district's rolled count drops by one, capped at `MAX_LOCATIONS_PER_TERRITORY`. A top-value district can double up hospital+HQ (`MAX - MIN` = 2 leaves room for both).

`CORP_HQ` sits in `UNROLLED_KINDS`, out of `GENERATED_KINDS` — carries none of the per-kind world tables (`LOCATION_SKILL`, `LOCATION_ROLES`, gig templates, legwork text), so it's automatically never a gig/legwork/job-target site. `gigs.refresh_gigs` skips it explicitly too (an HQ *has* characters, so the "has characters" test alone wouldn't skip it).

**Officers are a rep+standing ladder** (`CORP_OFFICER_TIERS`: receptionist `(0, None)` → operations manager `(5, 3)` → executive `(12, 8)`). `_make_officers` builds one `LocalCharacter` per rank; the gate is looked up by **role**, not list position (`officer_unlocked`/`officer_gate`), so `CorpHQScreen` can't mis-gate an officer even if `Location.characters` were reordered. Needs both street `rep` and per-faction `standing`. Reception has no standing floor (open even at negative standing) but does need `min_rep = 0` — a negative-rep runner gets turned away from the lobby itself.

Talking is **flavor only** (`officer_dialogue`: hostile/stranger/warm bands) — nothing else moves; the hook for future corp-sanctioned work via `target_faction_id`/`target_territory_id`.

Officers appear in `CorpMap.characters()` but gate on rep+standing, not `local_standing` — nothing moves their `local_standing`, so they never surface in ContactsScreen's Locals panel.

**Two more per-faction locations are injected the same way, for Corp mode.** `RESEARCH_FACILITY` and `ACADEMY` (both `UNROLLED_KINDS`, out of `GENERATED_KINDS`) are seeded one each per faction, always in a district different from each other and from that faction's HQ (`_plan_injections` picks HQ's district first, then Research Facility's excluding HQ's, then Academy's excluding both — `TERRITORIES_PER_FACTION` (6) guarantees enough cells remain). Neither has officers/dialogue/screen — they're read by `corp_turn.py` instead. Each carries a `tier` (`research_tier`/`academy_tier`, `None` on other kinds), starting at 1 — nothing raises either yet.

### Street gangs (`shadowguy/gangs.py`, `shadowguy/corpmap.py`)

The criminal-underworld counterpart to Factions, on the opposite premise: **a gang doesn't own territory.** `gangs.py` is a leaf holding `GANGS` (four gangs: id/name/description) plus `GANG_RANKS = ("soldier", "lieutenant")`.

**Turf is scattered, not grown.** `corpmap._place_gangs` hands each gang `GANG_TURF_MIN..MAX` (2–3) territories from one shuffled pool of unclaimed, non-start ids — no contiguity requirement, no two gangs ever share a territory (free via the shared pool). Lands on `Territory.gang_id`, deliberately separate from `owner` (a gang's ground stays `owner == "neutral"`). `generate_corp_map` guards feasibility up front: `len(GANGS) * GANG_TURF_MAX` must fit the unclaimed non-start pool.

**Each gang gets one den** (`GANG_DEN`, `"<Gang> Safehouse"`), seated in its own turf, chosen before the main build loop to reserve a slot. `UNROLLED_KINDS`/out of `GENERATED_KINDS` — `gigs.refresh_gigs` already skips it. A den's district can double up a hospital reservation (both draw from neutral ground) but never a start/HQ reservation.

**Staffing is presence only, not a ladder.** `_make_gang_members` gives one `LocalCharacter` per `GANG_RANKS` tier (same helper `_make_officers` uses) but no rep/standing gate, dialogue, or screen. `GANG_DEN` isn't in `MainMenu`'s Local-tab screen routing — display-only. Soldier/lieutenant appear in `CorpMap.characters()` but never surface in Locals (nothing moves their `local_standing`).

**Map/menu presence, plus one live consequence.** A territory carrying gang turf shows a `gang: <name>` suffix alongside `owner:` in both `CorpMapScreen` and `MainMenu`. Beyond display, gangs have standing and a turf-entry encounter (below) — still no gigs, jobs, or dialogue.

**Gang standing & turf encounters (`shadowguy/encounters.py`, `Character.gang_standing`, `CorpMapScreen`).** `gang_standing: dict[gang_id, int]` is a fifth relationship value — direct, one-gang, no rival fan-out. **Nothing moves it negative yet** (mechanism ahead of driver). When negative, `encounters.py` resolves entry: `action_travel`, after moving onto gang turf, calls `roll_gang_encounter` (flat `GANG_ENCOUNTER_CHANCE` 0.25). A hit at standing **-1…-4** offers an escalating toll (`toll_for` = `TOLL_BASE` 40 + `TOLL_STEP` 30 × depth) via `GangTollScreen` (pay, refuse, or fail into a fight); standing **-5 or worse** (`ATTACK_STANDING`) skips the toll and attacks outright. The fight is `gang_attack`: street-tier (`ENCOUNTER_ENEMY_TIER` 0) `Encounter` with `Drop.ENEMY`, via ordinary `CombatScreen` with a map-side `_on_gang_combat_end` mirroring `SceneScreen`'s death/knockout handling. `saves.SAVE_VERSION` 16 for the new field. **Not balance-simulated.**

### Faction/gang relations (`shadowguy/relations.py`, `shadowguy/corpmap.py`)

A **sixth relationship value**, and the only one that isn't player-facing: standing between every corp Faction and street Gang, independent of the runner entirely (recall `standing_shift` = player vs. corp; `gang_standing`/`local_standing`/`fixer_trust` = player vs. gang/person/fixer — this is corps and gangs' standing with **each other**).

`relations.py` is a leaf (imports only `factions.py`/`gangs.py`) holding `ENTITY_IDS` (every Faction + Gang id) and `generate_relations(rng)`, seeding one value per unordered pair (`combinations`) within `RELATION_MIN`/`RELATION_MAX` (-2..2) — symmetric, same shape every other standing value uses. `relation(relations, a_id, b_id)` is the order-independent accessor (`frozenset` key).

Generated once per run inside `generate_corp_map`, stored as `CorpMap.relations`, round-trips via the existing `"corp_map"` save key (`saves.SAVE_VERSION` 28 for the field). Defaults to `{}` so hand-built test fixtures don't need updating.

**Data only for now — nothing reads or moves these values yet.** Natural next hooks: `rivals.py`'s expansion roll favoring/avoiding a disliked/liked neighbor, or a gang's turf-entry encounter softening near a friendly corp.

### Location characters & standing (`shadowguy/corpmap.py`, `shadowguy/gigs.py`, `shadowguy/shops.py`, `shadowguy/character.py`)

Every `Location` carries `LocalCharacter`s. `_make_characters` gives a shop exactly one (its owner), every other kind 1–2 (`MAX_CHARACTERS_PER_LOCATION` = 2), named from `CHARACTER_NAMES` with a per-kind `role` from `LOCATION_ROLES` (import-time guards: every kind present, non-shop kinds need ≥2 roles). Ids are location-scoped and unique (`{location_id}_p{i}`), so they key standing cleanly despite repeatable names. `CorpMap.characters()` is the enumeration point.

`Character.local_standing: dict[character_id, int]` is a **fourth relationship value**, mirroring `fixer_trust` — direct, one-person, no rival effect. Moved by `Outcome.local_standing_delta` → `target_character_id`, applied in `apply_outcome`, validated in `Scene.__post_init__` (a delta with no target character raises), shown in ContactsScreen's **Locals** panel (gated on nonzero standing).

**Three things consume standing today.** Gigs *grant* it. Shops *read* it twice: bends prices (`buy_price`/`sell_price`) and **gates stock** (`Item.min_standing`/`Consumable.min_standing`). Info-gating is still an unbuilt hook.

### Shops (`shadowguy/shops.py`)

Five `LocationKind`s are retail: `PAWN`, `WEAPON_SHOP`, `AUTO_DEALER`, `PHARMACY`, `COMPUTER_STORE` (`corpmap.SHOP_KINDS`; `shops.CATALOG`'s keys checked against it at import). Generated like any Location — neutral ground can roll any, a corp district can roll one into filler (excluding shops sharing the specialty stat) — so a shop can be a job's target site. `REAL_ESTATE` is retail too but sells places not items, so it's outside `SHOP_KINDS`/`CATALOG` with its own screen (see Rest, lodging & property).

Selecting one pushes a `ShopScreen`. `CATALOG` maps shop kind → `Item`s. Buying spends cash, appends an `InventoryItem(item_id, equipped=True)` to `Character.inventory` (a list, duplicates allowed). Items are persistent, not consumable; only an equipped item counts (`equipped_bonus` skips unequipped).

An `Item` carries more than a flat bonus: `bonuses` (dict[stat,int], folded into `stat()`), `skill_bonuses` (per-skill, via `equipped_skill_bonus`), `slot`/`defense`/`concealment`/`two_handed`, `skill`+`damage` (weapon profile, below), `travel_reduction` (vehicle slot), `min_standing` (stock gate), `recharge_rounds`/`stun_damage` (combat pacing/knockout).

**Prices bend with standing in the shop's owner.** `buy_price`/`sell_price` apply `STANDING_PRICE_STEP` (3%)/point, capped at `STANDING_PRICE_CAP` (±20%): discount buying/bonus pawn sell-back on positive standing, markup on negative. Take an optional `standing` (default 0). `ShopScreen` reads it off `location.characters[0]`, shows the effective price on every row.

**Pawn Shop is the only kind that buys back**: lists the runner's `inventory` there, selling via `sell_item` for `PAWN_SELL_FRACTION` (0.5) of catalog price. Sell rows are keyed by inventory index, not item id (the same id can repeat and would collide as a Textual `ListView` id).

**A `Slot.WEAPON` item carries its combat profile** (`Item.skill`+`damage`) — the only place it's written, checked both ways at import (a weapon needs a profile; a profile needs a weapon slot). The catalog spans blunt/short_blade/long_blade/firearms/misc; the monoblade is two-handed.

**`COMBAT_ONLY_EFFECTS`** (the three grenades: damage-all, stun, clean escape) can only be reached in a fight — refused by `use_consumable` outside one, without being spent.

**Healing is deliberately *not* usable in combat** — the interesting exclusion, since a Health Kit is the obvious combat item in most games. Health returns slowly and mostly costs days (the hospital), so mid-fight healing would make health — the resource `DAMAGE_FOR_DELTA`'s whole curve is denominated in — refundable, turning a fight into a grind. You patch up *after*. Chems are excluded for the same reason.

### Cyberware (`shadowguy/cybernetics.py`) — first-slice system, not yet acquirable in-run

Persistent body modifications, distinct from `shops.Item`: cyberware is **installed**, not equipped/unequipped — a surgically-installed piece is always active. `CyberSlot` (`NEURALWARE`/`OPTICS`/`ARMS`/`INTERNAL`) mirrors `shops.Slot`, one piece per slot, tracked on `Character.installed_cyberware: dict[CyberSlot, str]`. `CYBERWARE_CATALOG` is hand-authored like `shops.CATALOG`, carrying `bonuses`/`skill_bonuses` in the same shape.

**Load-bearing today, not inert.** `Character.stat()` folds `cybernetics.installed_bonus` in alongside `equipped_bonus`; `skill_gear_bonus()` does the same for `installed_skill_bonus`. `install_cyberware`/`remove_cyberware` are real: installing charges cash **and** Humanity capacity, fails closed on an occupied slot/unaffordable/insufficient Humanity; removing frees both, no refund.

**Humanity is capacity, not a drain.** Every `Cyberware` carries a `humanity_cost` (float — Smartlink costs 0.5), the sum across everything installed can never exceed `Character.humanity` (`HUMANITY_BASELINE`, 6). `installed_humanity_cost`/`free_humanity` mirror `shops.free_program_slots`'s shape. Nothing lowers `Character.humanity` itself yet — purely a loadout budget today.

**Smartlink** (`CyberSlot.OPTICS`) is conditional, not flat: installed alone it does nothing, only granting `combat.smartlink_bonus`'s extra dice (`SMARTLINK_ATTACK_BONUS`, 2) when the equipped weapon is itself `Item.smartlinked` (today, just the pipe pistol — guarded at import to only ever be valid on a `skill == "firearms"` weapon). Threaded through `resolve_hit`'s `advantage` param (not folded into `skill_gear_bonus`, which can't express a weapon-conditional bonus). Gate: `cybernetics.has_smartlink`, checking `Cyberware.grants_smartlink` (a flag, not an id match).

**Four quality tiers, generated rather than hand-authored per tier**: `CYBERWARE_TIER_MULTIPLIERS` maps a tier to `(price_mult, humanity_mult)`, both relative to the same piece's **Tier 1** row — Tier 2: same price/-10% humanity; Tier 3: -25% price/+10% humanity; Tier 4 (cruder): -50% price/+60% humanity. A tier changes only cost, never effect — enforced by generating higher tiers via `dataclasses.replace(tier_1_row, ...)`. `Cyberware.tier` validated against `VALID_CYBERWARE_TIERS`; ids follow `{base_id}_t{tier}` (e.g. `smartlink_t4`).

**What's deliberately missing is acquisition** — no `LocationKind` (a ripperdoc clinic), no `ShopScreen` wiring, no `min_standing` gate — real wiring deferred (adding a shop kind touches corpmap's name pools, `LOCATION_SKILL`, `GENERATED_KINDS`, legwork text, and gig templates all at once). Nothing calls `install_cyberware` yet outside tests/a future screen. `saves.SAVE_VERSION` 34 added `Character.humanity` (33 added `installed_cyberware`).

### Codebase layout

```
src/shadowguy/
  character.py   Character dataclass: core stats, health, humanity (cyberware capacity, fixed),
                 skill ranks/points, post-creation experience (spend_experience_on_stat/skill) +
                 crew_experience, advantage bank, faction standing, local_standing, gang_standing,
                 crew, inventory, owned_programs, accepted_jobs, security_contracts
  archetypes.py  Enforcer/Hacker/Infiltrator creation presets; apply() spends via Character's own methods
  checks.py      resolve_check(): opposed d6 pool; pool_for_difficulty() converts legacy DCs
  skills.py      Skill table (32 skills), skill_value(), skill_for(); leaf module, imports nothing
  combat.py      Enemy roster, rounds, the six-stat action set, Drop/CombatOutcome, shared resolve_hit,
                 smartlink_bonus (cybernetics.has_smartlink + a smartlinked weapon); imports no scene
  tactical.py    grid combat: Grid/Tile, tcod FOV+A*, turn engine (reuses combat.resolve_hit), BSP generate_map;
                 also generate_building/BurglaryWalkState (Burglary jobs, no combat); imports no scene
  matrix.py      matrix combat (Data Heist): MatrixNetwork/generate_matrix_network (node graph),
                 MatrixRunState navigation (move_to/extract/jack_out) wrapping the per-node
                 MatrixState/ICE-roster engine, integrity pool (not health, run-wide not per-node),
                 Int-family actions, cyberdeck Program bonuses/action slots, SEIZED/EJECTED (no death);
                 reuses combat.resolve_hit; leaf, imports no scene
  scene.py       Scene/Stage/Choice/Outcome/Encounter/TacticalStage/Entrance/BurglaryStage/MatrixStage/Role data
                 model, resolve_choice()/resolve_entrance(), apply_outcome()
  jobs.py        procedural job generation + timing (JobTiming) + per-job legwork generator; matrix=Data Heist
  gigs.py        procedural per-Location gig generation (per-kind templates), owned by a LocalCharacter; refresh_gigs
  fixer.py       Fixer/JobOffer persistent roster, offer refresh/expiry; also Fixer.security_offers
  security.py    procedural multi-night Security contract generation + nightly resolution (not Scene-based)
  encounters.py  gang turf-entry encounters: toll-or-attack when the runner enters a Gang's turf they're negative with (not Scene-based)
  runners.py     hireable runner roster (specialist/rating; daily_cost + job_cut, Leadership-scaled via recruit_wage/recruit_cut); recruited onto Character.crew (CrewHire)
  factions.py    rival corp Factions (id/name/specialty); HQ officer ladder (rep+standing) + dialogue
  gangs.py       street Gangs (id/name/description) that hold no territory; GANG_RANKS (soldier/lieutenant)
                 staffing a den; leaf module — turf placement and den staffing live in corpmap.py
  relations.py   generate_relations(): seeded standing between every Faction/Gang pair, independent
                 of the player; symmetric one-value-per-pair, stored on CorpMap.relations; leaf module
  rivals.py      daily-action pipeline: resolve_rival_day() lets every Faction push onto bordering
                 neutral territory (claim_territory) and wanders every independent (not-hired)
                 RivalRunner one step across corpmap connections, each day-advance (not
                 Scene-based); skips a Faction the player has taken over via corp_turn.py
  corp_turn.py   the player's own Corp turn: CorpState (cash/research_points/scientists/
                 operatives/research_assistants/sightings), collect_income/collect_research,
                 expand_into/train_employees/build_lab/build_efficiency_upgrade sharing one
                 daily_action_used slot; also Sighting (plain data for surveillance.py's log,
                 kept here to avoid a corp_turn<->surveillance import cycle) (not Scene-based);
                 leaf, imports corpmap only
  surveillance.py  daily Surveillance detection: resolve_surveillance_day() rolls a
                 TerritoryModifier.SURVEILLANCE-scaled chance against the player and every
                 wandering RivalRunner standing in the player's own corp's territory, logging
                 a hit to CorpState.sightings (not Scene-based, informational only for now)
  corpmap.py     procedural territory map + ASCII renderer; Location, LocalCharacter, one CORP_HQ,
                 one RESEARCH_FACILITY and one ACADEMY per faction, one GANG_DEN per gang;
                 lodging_cost/has_home/add_safehouse/safehouse_price (property + rest);
                 claim_territory (rivals.py's/corp_turn.py's expansion mutator)
  shops.py       retail LocationKinds: Item catalog (bonuses/weapon profile/travel/standing gate,
                 smartlinked flag for combat.smartlink_bonus), consumables, Program catalog (cyberdeck
                 program_slots, buy/install/uninstall_program), buy/sell/equip, standing-scaled pricing,
                 hospital_stay, equipped_deck_rating/active_deck_entry (matrix)
  cybernetics.py Cyberware catalog (CyberSlot, bonuses/skill_bonuses like shops.Item, humanity_cost as
                 install capacity, four generated quality tiers), install_cyberware/remove_cyberware
                 onto Character.installed_cyberware, has_smartlink; no shop/screen wired to it yet; leaf
  saves.py       pickle-based whole-run save/load (SAVE_VERSION, STATE_KEYS); leaf, imports no game classes
  app.py         Textual App: just the ShadowguyApp class (on_mount -> TitleMenu, save/load_state,
                 corp_only -> which home screen to reopen, spend_time/_apply_day_tick);
                 every screen now lives under screens/, not here
  screens/
    creation_screen.py   CharacterCreationScreen
    main_menu.py         MainMenu
    menu_screens.py      TitleMenu (entry point) + ModeSelectScreen (Runner/Corp) + CorpSelectScreen
                         + TestMenu (standalone tactical/matrix test fights) + QuitMenu + LoadMenu
    scene_screen.py      SceneScreen
    combat_screen.py     CombatScreen
    tactical_screen.py   TacticalScreen
    matrix_screen.py     MatrixScreen
    burglary_screens.py  EntrancePickScreen + BurglaryWalkScreen
    corp_map_screen.py   CorpMapScreen + GangTollScreen
    corp_screen.py       CorpScreen (play as a corp: pick a Faction, expand/train/upgrade research)
                         + CorpMainMenu (subclasses it; home screen for a corp-only run)
                         + ResearchTreeScreen (spend research points; pushed from either with 't')
    shop_screens.py      FixerOffersScreen + ShopScreen + BarScreen + CorpHQScreen + HospitalScreen
                         + RealEstateScreen + SafehouseScreen
    info_screens.py      ContactsScreen + InventoryScreen + CyberdeckScreen + SkillsScreen
```

`saves.SAVE_VERSION` is the coarse guard on pickled runs: bump it on any breaking state change.

### Verifying changes

A real test suite exists (`tests/`, 19 files, `pytest>=8` in `pyproject.toml`'s `dev` dependency group), run by CI (`.github/workflows/tests.yml`, every push/PR to `master`): `uv run pytest -q` runs it, `uvx ruff check src/` lints. Guideline §4 still applies; established conventions:

- **Model/generator changes** — a `pytest.mark.parametrize("seed", SEEDS)` test (`SEEDS = range(150)` is the norm; `test_corpmap.py` widens to `range(200)`, `test_burglary_gen.py`/`test_tactical.py` narrow to `range(80)`) over a module-scoped fixture, asserting invariants rather than exact values. This caught a real bug once: `_plan_injections` comparing a `Cell` tuple against a `str` id (always `True`, so the start territory's hospital/gang-den exclusion silently did nothing) — invisible without a wide seed sweep.
- **Forcing an exact `CheckResult` branch** — `tests/test_checks.py`'s pattern: a `random.Random` subclass whose `randint` always returns a fixed face (`AlwaysSix`/`AlwaysOne`) or a call-counted mix, pinning a roll to `CRITICAL_SUCCESS`/`CRITICAL_FAILURE`/etc. deterministically. Reused in `tests/test_security.py`.
- **UI changes** — Textual's `async with app.run_test() as pilot:` drives the real app headlessly (`tests/test_app_flows.py`); `pilot.press(...)`/`pilot.hover(...)`/`pilot.click(...)` exercise real screens. Prefer this over asserting on internals.
- Anything asserting on a **check outcome** without one of the above tricks must seed the module-level `random` (see Check resolution) or it will be flaky.

### Known Textual gotchas hit so far

- `ListView.clear()` returns an `AwaitRemove` — must be awaited, or a following `.append()` can race the removal and raise `DuplicateIds`. Handlers that clear-then-repopulate must be `async def`.
- `ListView.index` becomes `None` after `clear()`; set it explicitly (e.g. `.index = 0`) after repopulating, or keyboard selection (`enter`) has nothing highlighted to act on.
- `Static` renders its string as Rich markup by default — literal square brackets (e.g. `"[Legwork]"`) get parsed as markup tags and silently vanish. **Way out:** pass `Static.update()` a Rich `Text` object instead of a `str` — markup is never parsed, and you can still colour ranges via `Text.stylize(style, start, end)`.
- `Screen`'s resume hook is the public `on_screen_resume` (a separate private `_on_screen_resume` exists for internal bookkeeping) — override the public one to refresh a screen's content when popped back to.
- Mouse hit-testing on a text blob: handle `on_mouse_move` and call `event.get_content_offset(widget)`, returning an `Offset` inside the widget's content or `None` when the pointer is outside it — `None` is the signal to clear hover state. Mouse events bubble to the `Screen`, so the handler fires for the whole screen.
- `Static` has **no** `.renderable` attribute in Textual 8 (it did in older versions); current content is `.content`. Only matters when asserting on widget contents in tests.
