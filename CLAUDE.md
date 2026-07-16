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

**Nothing rolls a core stat directly.** Each stat carries five **skills** (`skills.SKILLS`, 31 total — `perception` is the one stat with six, because `Firearms` is filed there: a gun is aimed, not swung, so it rolls the same faculty as `Sight`. Nothing enforces five-per-stat, and a stat's cost is per-skill anyway, so a sixth makes perception *broader*, not stronger); a `Choice` names a *skill*, and `skills.skill_value()` is what the dice see: the skill's tied stat (gear and chems included) plus the rank the player invested in that specific skill. `Character.skill_ranks` is `dict[skill_id, int]`, fully populated (every skill starts at `STARTING_SKILL_RANK`), not sparse. A skill id that isn't in `SKILLS_BY_ID` raises from `skills.skill_for()`, which is the single chokepoint: `Scene.__post_init__` runs it over every choice, so a typo fails when the scene is *built*, not mid-roll.

`skills.py` is deliberately a **leaf module** — it imports nothing from the package at runtime, because `character.py → shops.py → corpmap.py` all end up importing it. That's why the "every `Skill.stat` is a real core stat" guard lives in `character.py` (the one module that can see both tables) rather than next to the skill table. Don't add a runtime `character` import to `skills.py`; it's a cycle.

`Rep` is global standing in the street. Separate from that, `Character.standing` is a `dict[faction_id, int]` of per-corp standing — see Faction standing below. Rep is not faction-specific and the two are not interchangeable.

### Character creation (`app.CharacterCreationScreen`)

**Everything starts at 1** — all six stats, all thirty-one skill ranks — and is bought up from there at creation. The run opens on `CharacterCreationScreen` (not `MainMenu`), where the player spends `STARTING_STAT_POINTS` (6) and `STARTING_SKILL_POINTS` (20). A stat point raises a stat by 1; a skill point raises one skill's rank by 1. So an unspent runner rolls `skill_value` 2 on everything, and the build is entirely what those 26 points bought.

**Archetypes (`shadowguy/archetypes.py`) are the fast path**: Enforcer, Hacker, Infiltrator, listed above the stat and skill rows. Each is a canned allocation of the same 6 + 20 points, and `Archetype.apply()` spends them through `spend_stat_point`/`spend_skill_point` rather than assigning fields — so a preset obeys the rank cap and the cost curve exactly like a hand-built runner and **cannot buy anything the player couldn't**. `_check_affordable()` runs every preset against a fresh `Character` at import and raises unless it spends both pools to exactly zero, so a preset that doesn't add up is a startup error rather than a half-applied runner. Picking one calls `reset_build()` first: a preset is the *whole* build, not a top-up, so picking twice or picking after hand-spending replaces cleanly instead of running the pools dry mid-apply.

**All 31 skills are rolled by something**, so there are no dead points to spend: the 24 in `jobs.ARCHETYPES`' approaches, the 8 in `corpmap.LOCATION_SKILL` (legwork), the ones across the per-kind gig approaches in `gigs._GIG_TEMPLATES`, and the 6 combat actions (`combat.available_actions` — Grapple/Toughness/Tactics/Intimidation/Dodge, plus whatever skill your weapon rolls). The sets overlap; between them they cover all 31, with `Sight` reached only by legwork at a `WEAPON_SHOP` and **`Firearms` only by owning a gun** — it is the one skill whose usefulness you have to buy, and a runner with no firearm has no reason to rank it. The three presets still buy narrow anyway — a preset should read as an archetype, not a hedge — which is what makes each of them bleed on the job stages that don't suit it. (Note `archetypes.Archetype` is a *character* preset — unrelated to `jobs.JobArchetype`, which is a job template.)

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

### Runner-mode activity types (`shadowguy/scene.py`, `shadowguy/gigs.py`, `shadowguy/jobs.py`)

All three share the same `Scene`/`Stage`/`Choice`/`Outcome` data model, distinguished by `Scene.kind`. A stage of any of them can be a **fight** or a **tactical map** instead of a set of choices (`Stage.combat` / `Stage.tactical`; see Combat and Tactical combat below) — that's a property of the stage, not of the `SceneKind`.

- **Job** (`SceneKind.JOB`, formerly "mission") — multi-stage scene-based job, choices branch on skill checks, failure can end the job early. Jobs are not freely pickable: they're procedurally generated by Fixers (see below) and must be accepted before they show up as a runnable activity. Every stage also carries a fight beside it, reachable by choosing an ambush or by critically failing an approach.
- **Gig** — small single-stage activity for quick resources, no Fixer needed. **Procedurally generated per-Location** (`jobs.py`'s counterpart for street work is `gigs.py`), not hand-authored: `gigs.generate_gig` builds a single-stage gig from a per-`LocationKind` content template (a DATA hub reads as netrunning, a bar as social hustle), themed on that kind's skills. A gig is *attached to a place and a person* — it spawns at a `Location`, is owned by one of that location's `LocalCharacter`s, and its reward includes standing with that character (see Location characters & standing below). Because a gig is optional and the player self-selects into it, a gig **may** theme its choices on one stat — unlike a job stage, which may not; it offers a random 1..`GIG_MAX_APPROACHES` subset of the kind's approaches, so which skills a given gig wants is part of the draw. Gigs live on the App as `app.location_gigs: dict[location_id, Scene]` (not on the `Location` — `corpmap` is a leaf that can't import `scene`), topped up by `gigs.refresh_gigs` on run-start and each `rest()`, and consumed one-shot on completion (`SceneScreen._advance` clears the slot; a fresh gig spawns next rest). `MainMenu` lists the current territory's location gigs, gated only by *being there* — street work, runnable wherever you're standing, no `target_territory_id` travel-gate like a job.

  **The cash-stake gate still exists but no current gig uses it.** `Scene.max_cash_loss` is the worst `cash_delta` any path through a scene can charge (derived from the outcomes, never written beside them); `MainMenu` refuses a gig the runner can't cover, labelling it `can't cover the stake`, because `apply_outcome` subtracts a loss straight off `Character.cash` (**cash is not floored the way health is** — flooring would let a broke runner ride every losing outcome for free). Today's generated gigs cost only health on failure, so `max_cash_loss` is 0 for all of them and the gate never bites — but it's live infrastructure the moment a gig (or any scene) charges cash again, and the gate lives on the gig path in `MainMenu.on_list_view_selected`; a job or legwork scene that charges cash needs the same gate, because nothing downstream will catch it. `rep` *is* floored at 0 (like health, unlike cash): a botched gig can burn rep you earned, but 0 is being a nobody and there's nothing below it.
- **Legwork** — single-stage prep activity that banks an `advantage` bonus for a *specific* job (`Scene.prepares_for`), consumed on that job's first check only, then gone. `Character.advantage` is a `dict[job_id, int]`, not a flat global bonus — advantage from one job's legwork can't leak into an unrelated job or gig. For fixer-issued jobs, the legwork Scene itself is generated on the fly per accepted job (`jobs.generate_legwork_for_job`), since each job has a unique procedurally-generated id — there's no fixed legwork-to-job mapping to hand-author anymore. Its choices are the `Location`s of the job's target territory (see Corp map): casing the job's own site is the hardest check for the most advantage, scouting a neighbouring place in the same district is easier for less. That's why it takes the job's `Scene` and the `CorpMap` rather than a job id — legwork can't be built without knowing where the job lands. **Legwork can also turn violent**: a critical failure while scouting used to be a flat -2 health, and now gets you jumped (`LEGWORK_FIGHT_STAGE`) — but by a *street-tier* pair of locals who don't like being looked at, not the corp response team you'd meet inside on the job. There's no ambush option there and no way to *win* your way to an advantage: legwork is scouting, so a fight means it went wrong, and the best you get out of it is out.

### Runner position & travel (`shadowguy/character.py`, `shadowguy/app.py`)

`Character.location_id` is the `Territory` the runner is standing in, starting at `CorpMap.player_start_id`. It's the second runner↔corp coupling after standing: the runner is somewhere *on the corp board*, not in an abstract city.

Travel lives on `CorpMapScreen`, and the two markers there are different things: `*` is the **cursor** (`selected_id`, moved by the arrow keys along connections, or by clicking any node) and `@` is the **runner** (`character.location_id`). `enter` moves the runner to the cursor's node — but only if it borders `location_id`, and only for `TRAVEL_STAMINA_COST` stamina, so crossing the map costs days. Base stamina is 5 (`character.BASE_STAMINA`), which is the budget travel competes with gigs, jobs and legwork for.

The MainMenu **Local** category lists the `Location`s of whatever node the runner is in. Most rows are still display-only, except the five shop `LocationKind`s (see Shops below), which open a `ShopScreen`, and a corp's `CORP_HQ`, which opens a `CorpHQScreen` (see Corporate HQs & officers below).

**Position gates jobs.** A job — and the legwork that preps it — can only be run while the runner is standing in the job's `target_territory_id` (`MainMenu._on_site`). Off-site, the row is labelled `travel to <district>` and selecting it is a no-op; the check is enforced in `on_list_view_selected`, not just in the label. That's what pays for travel: an accepted job is a *place you have to go*, and a hard `scheduled_day` job means being in the right district on the right day. Gigs are unaffected — they're street work, runnable anywhere.

Note this stacks with timing: on-site but on the wrong day is still blocked, and the label reports whichever gate bites first (travel, then timing, then stamina).

Gigs are gated differently: not to one `target_territory_id` like a job, but to whichever territories have a matching `LocationKind` locally (see Gig above) — street work, but not work that happens just anywhere.

### Fixers & job generation (`shadowguy/fixer.py`, `shadowguy/jobs.py`)

Jobs are gated behind a persistent roster of Fixers (`fixer.FIXER_ROSTER` / `create_fixers()`), not listed directly. Each `Fixer` holds up to `max_offers` `JobOffer`s; offers are procedurally generated (`jobs.generate_job`) from a small set of archetypes (Heist/Extraction/Sabotage), with difficulty and reward scaled by a day-derived tier and flavor text drawn from word banks — not picked from hand-authored content.

**A job is a sequence of typed stages.** Every archetype walks `StageType.APPROACH` → `OBJECTIVE` → (`COMPLICATION`) → `EXFIL`, each with its own prompt and its own pool of approaches (`_ARCHETYPE_ROWS` is the table; `JobStage` is the row). `OPTIONAL_STAGE_CHANCE` maps a stage type to how often it shows up at all; a type absent from it is mandatory, so membership *is* the "is this optional?" test and there is one table rather than two that must agree. The complication is its only entry (0.4), so a job runs **3 or 4 stages** — dropped before ids are handed out, so `stage_0..n` stay contiguous. Keep the odds in that table when you add a second optional type: a lone `COMPLICATION_CHANCE` constant would silently hand the newcomer the complication's rate, and `REWARD_PER_EXTRA_STAGE` would pay for it at the complication's premium. `StageType` is the semantic handle on a beat, and it is **the hook for hired support** (a netrunner covers your `OBJECTIVE`, muscle covers your `EXFIL`); crew roles (below) are the first thing to read it that way, and future support should hang off the type, not off a stage index.

**Crew roles (`scene.Role`/`Posture`, `jobs._role_for_stage`).** Every generated job carries `Scene.roles`: one `Role` per beat it actually has (`job_stages`, after the optional complication is rolled), naming the crew position that beat offers — a `beat` label (the `StageType` value), the runner `specialist` who fits it (`Netrunner`/`Solo`/`Infiltrator`), and a `Posture` (`ON_SITE` / `REMOTE`). It's **derived, not tabulated**: `_role_for_stage` reads the beat's *lead* (cleanest) approach, maps its skill's stat through `SPECIALIST_FOR_STAT`, and marks the role `REMOTE` iff that skill is in `REMOTE_SKILLS` (just `hack` today — the netrunner in the car). So a Heist's crack-the-ice objective reads as a remote Netrunner while a Sabotage's rig-the-hardware one reads as an on-site Netrunner and an Extraction's grab-the-target one as a Solo, all from the same table. Derived from the *template* pool's lead, so a job's roles are the same regardless of which approaches its offer drew. `Role` lives in `scene.py` as **plain data** (strings + `Posture`, not `jobs.StageType`) so the `Scene` can hold it without `scene.py` importing `jobs` — `jobs.py` owns the derivation, `scene.py` just carries the record. Descriptive for now (shown per-offer in `FixerOffersScreen`). **Recruiting runners exists** (hire at a bar — see Runners & crew below), but **assigning** a hired runner to a specific role is still to come: `Role.filled_by` (a runner id) is where that lands. **Crew capacity runs along `Posture`:** a job may take **several `ON_SITE` runners** (the meat going in with you) but **at most one `REMOTE` support** (the netrunner in the car). That cap is free rather than enforced-by-search — a beat is only `REMOTE` when its lead skill is in `REMOTE_SKILLS` (just `hack`), and a job has one objective, so **a job carries at most one remote role** (0 or 1 over any seed; ~a third of jobs have one). So "one support" is a property of the board, and recruiting only has to cap *on-site* count, not remote.

**Runners & crew (`runners.py`, `app.BarScreen`, `Character.crew`/`CrewHire`).** The `RIVAL_RUNNERS` roster — Specter (Netrunner), Juncture (Solo), Mireille (Infiltrator) — are the hireable operators; each carries a `rating` (their effective `skill_value` at their specialty, for the run-time crew effect once that lands) and the two prices below. A `BAR` opens `app.BarScreen` (routed like the shops), a two-level menu: pick a runner, then the **terms**, which are the whole point of this increment:

- **Indefinitely** — they stay on the crew and draw `daily_cost` every rest (`Character.pay_crew_wages`, called from `advance_day`); miss payroll and they **walk** (no debt — you pay what you can and the rest leave).
- **For a job** — signed to one accepted job for `job_cut` of *that job's* payout, taken when it pays (`SceneScreen._take_crew_cut`, on the JOB outcome that ends the scene with cash); the engagement ends with the job.

Neither costs anything upfront — the price is the wage or the cut, paid later. `Character.crew` is a list of `CrewHire(runner_id, job_id | None)` (`job_id` None = indefinite); a runner has at most one live hire. For-job hires are discharged whenever their job leaves `accepted_jobs` — completed, blown, or expired (`_discharge_orphan_crew`, from `remove_job` and `rest`). A runner's `archetype` is exactly a role's `specialist`, so a hire slots onto the role it fits — but **assigning** a hire to a specific `Role.filled_by` (respecting the many-on-site / one-remote cap above) and the **run-time effect** (a remote Netrunner cracks the objective from the car; on-site muscle joins the fight) are still the next increments; a crew member changes the job's economics but doesn't yet act on it. First-slice simplification: the whole roster is hireable at *any* bar; seating runners at particular bars the way `fixer.create_fixers` seats fixers to territories is the natural follow-up.

Two things the guards pin down: the **last stage cannot be optional**, because the cash/rep/standing all ride on the final stage and dropping it would silently move the payout; and each stage's `prompt` is `.format`-checked at import over the real field set, so a bad `{field}` fails on import rather than mid-job.

**A job stage is several `Approach`es, not one check.** Each stage holds a **pool** of `Approach`s (`skill`, `difficulty_delta`, `flavor`) — a hard/clean, a middling and an easy/bloody way through. The stage rolls its base difficulty **once** and every approach is offset from it, so a `difficulty_delta` means the same thing on every job.

**Damage is derived from `difficulty_delta`, never written beside it** (`DAMAGE_FOR_DELTA`; a critical failure deals the same plain damage — the fight it routes into is its real punishment). That's the only place job damage is set, so "the easy way in is the one that hurts" is structural rather than a convention a row can drift out of. It is calibrated against job *length*: a body-1 runner has 15 health, a job is 3–4 stages, and an off-stat specialist takes the bloody route on most of them. The 2/3/6 curve that was fine for 2-stage jobs produces a **13% death rate** at 3–4 stages — if you touch either the curve or the stage count, re-run the balance sim.

Difficulty likewise ramps by `STAGE_DIFFICULTY_RAMP` spread *across* the job rather than `+1` per stage index, so a 4-stage job is more checks, not a steeper climb — the flat per-index ramp quietly made longer jobs harder to finish for the same money. For the same reason `REWARD_PER_EXTRA_STAGE` pays a premium for a job that ran its complication.

**A generated job offers a subset of each pool, not the whole thing.** `generate_job` takes the full pool `FULL_POOL_CHANCE` of the time and exactly `PARTIAL_POOL_SIZE` of it otherwise (about a 35/65 split today), keeping pool order so the clean approach still reads before the bloody one. So two Heists are not the same Heist: one leaves the door open for your build, the next withholds the very approach you specced into, and *which ways in a job happens to have* is part of what makes one fixer's offer better than another's. This is why pools want to stay wider than `PARTIAL_POOL_SIZE` — a pool of exactly two never varies, and one smaller than two would make `rng.sample` raise mid-generation (guarded at import). Note `PARTIAL_POOL_SIZE` is an **exact draw size, not a floor**: widening a pool adds approaches the *full-pool* roll can reach, but the partial draw stays two. If you want a wider pool to also mean a more variable partial draw, that's a change to `generate_job`, not just to the table.

The load-bearing rule, enforced at import over the whole pool (so it holds for every subset that can be drawn): **the approaches in one stage must sit on different core stats.** A job stage is a gate every build has to pass, so two approaches on one stat hand that stat's runner a second bite and everyone else nothing — the same failure `corpmap._filler_pool` guards against on legwork. (Gigs are exempt: they're optional and self-selected, so a gig's approaches may cluster on one stat — a `gigs._GIG_TEMPLATES` entry isn't held to the cross-stat rule a job stage is.) A specialist is therefore *meant* to have stages that don't suit them; they bleed through on the easy approach rather than being locked out. Simulated over the three presets × three archetypes, that lands at 45–61% completion with under 2.2% deaths and 3–6 health spent — because a **failed stage still advances** (`failure` carries `next_stage`), so failure costs health and the final stage's reward, never the run.

**Gig payouts scale by day-tier** (`gigs.GIG_CASH` = 80/110/150 by `gigs._gig_tier`), a plain success paying that cash plus `GIG_STANDING_GAIN` (1) standing with the owning character; a crit pays ~1.6x and +2 standing, a critical botch costs health and −1 standing. Since every gig defaults to `Scene.stamina_cost` 1, that's ~80–150 cash per stamina — a rough continuation of the old ~75–100 target, **not** re-simulated against jobs (~82–119 cash per stamina per the older sim, below). Re-run the balance sim before relying on either number. The levers are `gigs.GIG_CASH`/`GIG_DIFFICULTY`, `gigs.GIG_STANDING_GAIN`, and `Scene.stamina_cost`.

Jobs are run **against a real corp, on the real map**. `generate_job` takes the `CorpMap`, picks a `Territory` that a faction actually owns this run, then picks one of that territory's `Location`s as the site — so `generate_job`/`refresh_offers` need the map threaded through them. The job records `Scene.target_faction_id`, `Scene.target_territory_id` and `Scene.target_location_id`, and its flavor text names that corp, that district and that building. There is deliberately no separate list of corp names or venue names: if you find yourself adding one, you've disconnected jobs from the map again.

Every job offer carries a `JobTiming`:
- **no deadline** — can be run any day, indefinitely, while it sits on the fixer's board.
- **soft deadline** (`deadline_day`) — must be run by that day (inclusive); expires after.
- **hard scheduled day** (`scheduled_day`) — can *only* be run on that exact day; unrunnable before, expired after.

Flow: browse Fixers (`f` keybinding) → accept an offer → it moves from the fixer's board (freeing a slot for `refresh_offers` to fill on the next day) into `Character.accepted_jobs`, and appears in the main activity list alongside its dynamically-generated legwork. `Character.rest()` (advancing the day) drops any accepted job whose timing has expired; `fixer.expire_offers` + `fixer.refresh_offers` do the same for un-accepted board offers, keeping each fixer topped up to `max_offers`. Completing a job (reaching a stage with no `next_stage`) removes it from `accepted_jobs` via `Character.remove_job(scene.id)` — jobs are one-shot, not repeatable busywork like gigs/legwork.

Note: `content.JOB_DATA_HEIST` and `content.LEGWORK_CASE_THE_BLOCK` are hand-authored example content left in place but no longer wired into `app.py` — they predate the Fixer system and don't fit its per-offer unique-id model.

### Combat (`shadowguy/combat.py`, `app.CombatScreen`)

Combat is the **only part of the game that isn't a single check** — but it is still the same dice. Every roll in `combat.py` goes through `checks.resolve_check()` (d20 + `skill_value` vs a difficulty, same crit rules), so a fight is a *sequence* of the game's existing checks, not a second resolution model bolted on beside it. A round is: **you take one `Action`, then every standing enemy that isn't stunned attacks you.**

**A fight is a `Stage`.** `Stage.combat` holds an `Encounter` (prompt, enemies, and a `victory`/`escape` `Outcome`), and it's routed to by an ordinary `next_stage` like anything else — so nothing needed a new effect pipeline. A stage is *exactly one* of choices, a fight, or a tactical map, never a mix (guarded in `Scene.__post_init__`; the grid fight is the third mode — see Tactical combat below): a combat stage's "choices" are `combat.available_actions`, which come from the runner's own gear and skills rather than from the scene.

`Encounter` lives in **`scene.py`, not `combat.py`** — it holds `Outcome`s, and `combat.py` must not import `scene` (`scene` imports `combat` for `Enemy`). So `combat` owns *how a fight resolves* and knows nothing about jobs; `Encounter` owns *what winning or running is worth*, and says so with an ordinary `Outcome` — which is how fighting through a job's last stage pays its cash, rep and standing without a second reward path.

**Two doors into a fight, and which one you came through is the whole difference:**

- **You chose it (ambush).** `generate_job` appends `AMBUSH_LABEL` to *every* stage on top of the drawn pool, so a job can never withhold every route your build can pass — there is always a way through, and it's the one that bleeds. It's a Tactics check.
- **You botched into it (going loud).** A **critical failure only** on a normal approach routes to the same fight. Plain failure still costs health and advances, which is the property `DAMAGE_FOR_DELTA` is tuned around — routing *every* failure into a fight is how you get a job that is mostly fighting and a death rate to match.

`combat.drop_for_result()` reads who got the drop straight off the `CheckResult` that routed you in, which is why a fight needs **no extra field on `Outcome`** to know how it started: success = you picked the moment (`Drop.PLAYER`), plain failure = an even fight, critical failure = they were waiting (`Drop.ENEMY`). One rule, both doors.

**Enemy *count* is the real lethality lever** — every one of them swings at you every round — so that's what the drop moves, not just initiative. A landed ambush takes one enemy off the board before the fight ("you caught a straggler") *and* gives a free round; going loud hands them a free opening attack. A free round alone was far too small a payoff: the sim had the ambush killing a Hacker 22% of the time it was taken, which makes the "guaranteed way through" a trap rather than a way through.

**Actions deliberately span all six stats**, so a fight is not a Strength minigame only an Enforcer can play — the same "every build has a way through, but not the same way" rule `jobs.py` enforces across a stage's approaches, applied to a round: attack (Strength, or Perception with a gun), **brace** (Toughness/Body), **read the fight** (Tactics/Int, banks a bonus for your next attack), **face them down** (Intimidation/Cool, breaks the weakest enemy's nerve and they run), **break and run** (Dodge/Agility), and throwing a grenade.

**Running always works — the Dodge check only decides what it costs you** (a clean break, or one parting shot). This is load-bearing, and it was the single most lethal thing in the module when it wasn't: flee rolls Dodge, and the build most likely to *need* the exit (a Hacker: 15 health, no Agility) is exactly the build that can't make the roll. It failed ~65% of the time, ate the round, and the squad kept swinging — the escape valve was shut for precisely the runner it existed for. **A fight must never be a cage.** (Note the parting shot is from *one* enemy, not each of them: a whole squad's worth of free hits is what a runner eats *because* they were low enough to be running, so it turned the exit into the thing that killed them.)

**Weapons are the damage, skills are the hit.** `skill_value` decides whether you connect; `shops.Item.damage` decides what it costs them. Investing in Short Blade lands the knife more often; buying a better knife makes each landing hurt more. Neither substitutes for the other, and `Item.skill`/`Item.damage` are the only place a weapon's combat profile is written (`combat.py` reads it rather than keeping a second table that would have to agree). Empty-handed is always *an* attack (`UNARMED`, Grapple), just a bad one.

**Balance, simulated over the three presets × three job archetypes (playing the stage's best-odds approach, running below 25% health):**

| | never picks a fight | fights when locked out (with a monoblade) |
|---|---|---|
| Enforcer | 60% paid, 0.4% deaths | 74% paid, 1.7% deaths |
| Hacker | 43% paid, 6.9% deaths | 55% paid, 25% deaths |
| Infiltrator | 51% paid, 4.3% deaths | 59% paid, 17% deaths |

That's the shape it's meant to have: **fighting pays better for the build that invested in it and kills the one that didn't.** Two numbers to keep an eye on. The "never picks a fight" column is *not* zero fights — it's the nat-1s going loud, and those alone take the Hacker from the pre-combat baseline of ~2% deaths to ~7%. And the Hacker's death rate is extremely sensitive to *when they run* (14% if they break at 40% health, 31% at 25%) — combat is on a knife edge for a light build, which is intended, but it means the flee rules are not a place to tune casually. Re-run the sim if you touch `_ENEMY_ROWS`, `ENEMY_TIERS`, `DEFENSE_BASE`, or the flee/drop rules.

### Tactical combat (`shadowguy/tactical.py`, `scene.TacticalStage`, `app.TacticalScreen`)

A **third kind of stage**, beside choices and the abstract fight: some job fights play out on a grid — move a runner through rooms, take cover, shoot or close, a squad holding the far end. It is emphatically **not a second combat model**. Every attack still goes through `combat.resolve_hit` (promoted from `_resolve_hit` to shared API for exactly this — *one hit formula, two surfaces*), enemies are `combat.Enemy`, and damage/soak/health are unchanged. The grid only adds *position*: line of sight and range gate which of those attacks are legal, and **cover is nothing but a raised to-hit difficulty** (`pool_for_difficulty` turns the bump into a bigger dodge pool, so a shielded target is harder to hit in the very same formula — verified to swing hit rate ~91%→74%).

**The module split mirrors combat exactly.** `tactical.py` is a leaf that owns *how space works* — `Grid`/`Tile` (floor/wall/low-cover), tcod-backed FOV and A*, the turn/movement engine — and imports no `scene`, the same rule `combat.py` follows. `scene.TacticalStage` is the grid analogue of `Encounter`: it lives in `scene.py` because it holds `Outcome`s (`victory`/`escape`), and it's routed to by an ordinary `next_stage`. `app.TacticalScreen` renders `TacticalState` and feeds it the player's move/attack/end-turn — the `CombatScreen` counterpart. `Stage` now enforces **exactly one** of choices / `combat` / `tactical` (`Scene.__post_init__`), and a scene still can't *open* on a fight or a map.

**LOS and range are separate gates, deliberately.** `has_line_of_sight` is pure obstruction via *unlimited-radius* FOV; a weapon's reach is an explicit distance check (`weapon_range`: Firearms ranged, everything else arm's length). They're split because tcod's FOV `radius` measures Euclidean distance and **excludes the cell exactly at the radius** — reading range off it would be off by one. So firearms kite; melee has to close.

**The turn model** is move up to `speed` tiles, then one action — `speed` is a field on the unit, so a future ability can raise it rather than a global constant. **Enemy reach is per-enemy** (`combat.Enemy.reach`, a tactical-only field abstract combat ignores like `stun_damage`): the armed guards (`corp_sec`, `sec_heavy`) shoot from `reach` 6, the street muscle and the chromed bruiser close to `reach` 1. `_enemy_phase` reads it through `_can_hit_player` (within reach *and* line of sight), and the AI is simply "close via A* until you can hit, then hit" — so a ranged enemy **holds its distance**, only advancing when it loses the shot, while melee has to reach arm's length. This also makes cover matter on defense (a ranged enemy's shot eats the player's `cover_bonus`). **Fleeing is positional and always works**: reach an exit tile and leave, no roll and no parting shot (the risk was crossing the room to get there). Same law as `combat.py`'s flee — *a fight must never be a cage* — enforced spatially instead of by a Dodge check.

**Maps are generated** (`tactical.generate_map`): a BSP partition (tcod, seeded off the caller's `rng` via a derived int so a run stays reproducible), carved into rooms + corridors with scattered low cover, the player entering by the `exits` at one end and the squad at the other. It **retries until every enemy spawn and exit is reachable** from the player start — scattered cover can wall a cell off, and the caller can't recover from an unplayable fight, so it raises rather than hand one back. Maps are `TAC_MAP_WIDTH`×`TAC_MAP_HEIGHT` (30×10), sized to fit `TacticalScreen` at 80×24 without scrolling; grow those and re-check the row budget the way `CorpMapScreen` does. Cover density is *softly* themed by the site's `LocationKind` (`jobs._cover_density`, a `.get`-with-default — no exhaustive table, no import guard).

**Which jobs are tactical is decided once per job** (`jobs.TACTICAL_FIGHT_CHANCE`, ~35%): a job's fights are all grid or all abstract, never a mix, so it reads as one thing. The routing is otherwise unchanged — the fight beside every stage (`{stage}_fight`, reached by the `AMBUSH_LABEL` choice and by critical failures) simply holds a `TacticalStage` instead of an `Encounter`, with the same `payout`/blown `Outcome`s. `saves.SAVE_VERSION` is 7 because `Stage` gained the `tactical` field.

**Balance — far swingier than abstract combat, by design.** Position and loadout dominate. Simulated with a simple auto-player, a tier-2 Hacker dies **~68% rushing in with a monoblade** but **~10% kiting with a pistol** (abstract-combat baseline 25%): a competent/ranged runner is *safer* on the grid, while a melee-only light build that crit-fails into a high-tier fight is in real danger. This is **left as-is on purpose** — fleeing to an exit is always available, and the ~68% is a deliberately naive rush, not typical play. Ranged guards (`reach` 6) pressure the *rusher* far more than the kiter — a firearm outranges them (8 > 6) and the kiter's safety is really a damage-economy fact (guards have small attack pools and die in a shot or two), not a positioning one, so bumping guard `reach` barely moves the kite number; only tankier/harder-hitting enemies would, which is a bigger change that also touches abstract combat. The flee/exit rules, `TACTICAL_FIGHT_CHANCE`, enemy counts and `reach` are not places to tune casually, and the auto-player's *policy* moves these numbers as much as the constants do. Re-run the sim (presets × tiers, a reasonable policy) before touching them.

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

Each `Territory` also holds `LOCATIONS_PER_TERRITORY` `Location`s — the concrete places (data vaults, clinics, depots, bars, shops) a job actually hits. They're stocked from the owner: a corp district gets `SPECIALTY_LOCATIONS` of its owner's own kind (`LOCATION_KIND_FOR_SPECIALTY`) plus a filler slot rolled from `FILLER_KINDS` (the bar, or one of the five shop kinds — see Shops below), while neutral and player ground get a random mix of every `LocationKind`. One of each corp's highest-value districts also carries its **headquarters**, injected on top of that stock — see Corporate HQs & officers below. Each `Location` also carries `LocalCharacter`s (its owner/regulars — see Location characters & standing below) and is where a per-Location gig spawns. Location *kinds* are map data; the **skill** each kind is scouted with lives in `corpmap.LOCATION_SKILL` (the flavor text is `jobs.LEGWORK_APPROACH_TEXT`, kept separate so there's one place, not two, that has to agree on which skill a kind uses). Legwork is scouting, so the table leans on perception and agility, with intelligence on the wired places and cool where the read comes out of a conversation — that's where `Perception` earns its keep, since no job archetype rolls it.

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

### Corporate HQs & officers (`shadowguy/corpmap.py`, `shadowguy/factions.py`, `app.CorpHQScreen`)

Each corp has **one headquarters** — a `LocationKind.CORP_HQ` location seated in one of that faction's **highest-value districts** it owns (`value == max(FACTION_VALUE_SPREAD)` = 3; a faction holds two such, and one is picked). It's **injected, not rolled**: like the apartment and the hospital it's appended to a chosen district, and its slot is reserved up front so the district's rolled `count` drops by one and the total still caps at `MAX_LOCATIONS_PER_TERRITORY`. A top-value corp district can be drawn for both a hospital and an HQ — the reserve counts each, and `MAX - MIN` (6−4) is exactly what leaves room for the two together (the neutral start never stacks past its apartment, since it's neither an HQ nor in the hospital draw).

`CORP_HQ` sits in `UNROLLED_KINDS` (alongside the player-owned kinds), so it's **out of `GENERATED_KINDS`** and therefore carries none of the per-kind world tables (`LOCATION_SKILL`, `LOCATION_ROLES`, `gigs._GIG_TEMPLATES`, `jobs.LEGWORK_APPROACH_TEXT`) — every one of those guards against `GENERATED_KINDS`, which is what makes the HQ automatically never a gig site, a legwork target, or a job's target location. `gigs.refresh_gigs` skips it explicitly too: an HQ *has* characters (its officers), so the "has characters" test alone wouldn't skip it and `generate_gig` would `KeyError` on the missing template.

**Officers are a rep+standing ladder** (`factions.CORP_OFFICER_TIERS`, rows `(role, min_rep, min_standing)`): receptionist → operations manager → executive. `_make_officers` builds one `LocalCharacter` per rank in table order, but the gate is looked up by **role**, not list position: `factions.officer_unlocked(rep, standing, role)`/`officer_gate(role)` key off `officer.role` into a `CORP_OFFICER_TIERS`-derived dict, so `CorpHQScreen` can't mis-gate an officer even if `Location.characters` were ever reordered. Reaching an officer needs **both** the street `rep` and the per-faction `standing` its row demands — that's the "communicate depending on your street rep and corp rep" rule. The ground-floor reception has `min_standing = None`: the lobby is public, open even at negative standing (a runner who's been hitting the corp gets the cold shoulder rather than the door). Talking is **flavor only** — `factions.officer_dialogue(faction, role, standing)` returns one of three standing-band lines (hostile `< 0` / stranger `== 0` / warm `> 0`), and all three bands are reachable *because* reception has no standing floor; nothing else about the run moves. It's the hook the corp-side game will hang concrete interactions on: `Scene.target_faction_id`/`target_territory_id` are the existing effect fields if an officer should later hand out corp-sanctioned work.

Note officers are `LocalCharacter`s and so appear in `CorpMap.characters()`, but their gate is `rep` + faction `standing`, **not** `local_standing` — nothing moves an officer's `local_standing`, so they never surface in ContactsScreen's Locals panel (which gates on nonzero `local_standing`). Don't wire HQ access to `local_standing` without deciding what would move it.

### Location characters & standing (`shadowguy/corpmap.py`, `shadowguy/gigs.py`, `shadowguy/shops.py`, `shadowguy/character.py`)

Every `Location` carries `LocalCharacter`s — the people who run or haunt it. `_make_characters` gives a **shop exactly one** (its owner) and **every other kind 1–2** (`MAX_CHARACTERS_PER_LOCATION` = 2), named from `CHARACTER_NAMES` and given a per-kind `role` from `LOCATION_ROLES` (import-time guards: every kind present, non-shop kinds need ≥2 roles for the two-character case, shops need 1). Character ids are location-scoped and unique (`{location_id}_p{i}`), so they key standing cleanly even though *names* may repeat across the map. `CorpMap.characters()` is the one place other systems enumerate them (Contacts, gigs, shop pricing).

`Character.local_standing: dict[character_id, int]` is a **fourth relationship value**, mirroring `fixer_trust` exactly: a direct, one-person regard (no rival/opposite effect, unlike `standing_shift`'s corp-vs-corp `standing`). It's moved by `Outcome.local_standing_delta` → `Scene.target_character_id`, applied in `scene.apply_outcome` and validated in `Scene.__post_init__` (a delta with no target character raises), and shown in ContactsScreen's **Locals** panel (gated on nonzero standing, like the Fixers panel gates on trust).

**Two things consume standing today.** Gigs *grant* it (see Gig above — a location's gig rewards standing with that location's owning character). Shops *read* it: a shop's owner is its single character, and their standing bends prices (see Shops below). Everything else the standing could gate — stock, info — is left as a **hook**, not built: `shops.STANDING_PRICE_STEP`/`STANDING_PRICE_CAP` is where a "standing unlocks new items or reveals info" system will attach, but until such a mechanic exists there's no number nothing can change (the same "don't invent a number nothing moves" ethos `runners.py` held until recruiting gave its `rating`/`hire_cost` a use).

### Shops (`shadowguy/shops.py`)

Five `LocationKind`s are retail rather than job-related: `PAWN`, `WEAPON_SHOP`, `AUTO_DEALER`, `PHARMACY`, `COMPUTER_STORE` (`corpmap.SHOP_KINDS`; `shops.CATALOG`'s keys are checked against this at import time). They're generated exactly like any other `Location` (see Corp map above): neutral ground can roll any of them, and a corp district can roll one into its filler slot (`corpmap.FILLER_KINDS`, excluding whichever shops share the district's own specialty stat) alongside its two specialty locations — so a shop can end up as a job's target site, and `corpmap.LOCATION_SKILL`/`jobs.LEGWORK_APPROACH_TEXT` each have an entry for every shop kind to cover that. Real Estate was on the original request but deliberately left out: it doesn't fit the "carryable item" model below and wasn't worth special-casing yet.

Selecting one of these locations from the MainMenu **Local** tab pushes a `ShopScreen` (`app.py`) instead of being a no-op. `shops.CATALOG` maps each shop `LocationKind` to a fixed list of `Item`s (id, name, price, `stat`, `bonus`). Buying spends `Cash` and appends the item id to `Character.inventory` — a flat `list[str]`, duplicates allowed, so the same item can be bought (and owned) more than once. Items are **persistent, not consumable**: `Character.stat()` adds up every owned item's bonus for the requested stat on top of the raw attribute, so gear silently strengthens every check that uses that stat — jobs and legwork included, since both go through the same `stat()` call.

**Prices bend with standing in the shop's owner** (see Location characters & standing above). `shops.buy_price`/`sell_price` take the runner's standing with the owning `LocalCharacter` and apply `STANDING_PRICE_STEP` (3%) per point, capped at `STANDING_PRICE_CAP` (±20%): positive standing is a discount on buying and a bonus on pawn sell-back, negative (a botched gig) is a markup. `buy_item`/`buy_consumable`/`sell_item` take an optional `standing` (default 0 = neutral, so non-shop callers and any test stay unaffected); `ShopScreen` reads it off `location.characters[0]`, shows the owner + standing in its header, and lists the *effective* price on every row — so the number you see is the number you pay.

**Pawn Shop is the only kind that buys back**: `ShopScreen` also lists the runner's current `inventory` there, and selecting one sells it via `shops.sell_item` for `PAWN_SELL_FRACTION` of its catalog price. Sell rows are keyed by **inventory index**, not item id — the same item id can appear more than once in `inventory`, and a repeated id would collide as a Textual `ListView` id (see Known Textual gotchas).

**A `Slot.WEAPON` item also carries a combat profile** — `Item.skill` (what its attack rolls) and `Item.damage` (what a hit takes off) — and it is the *only* place that profile is written, so `combat.py` reads gear rather than keeping a second weapon table that would have to agree with this one. The slot and the profile are checked against each other **both ways** at import: a weapon with no skill/damage is an attack combat can offer but never resolve, and a skill/damage on a non-weapon is a profile nothing can reach, since combat only ever swings what's equipped in `Slot.WEAPON`. The catalog spans blunt/short_blade/long_blade/firearms so no build is stuck holding a weapon it can't use, and the bloodiest (`monoblade`) is two-handed, costing both weapon slots.

**`COMBAT_ONLY_EFFECTS` (the three grenades — damage-all, stun, clean escape) is both what a fight can reach and what nothing else can.** Outside a fight they are **refused by `use_consumable` without being spent**: a grenade thrown at no one is a grenade wasted, so popping it first just to report the failure would burn it. This retires the old `EffectKind.NONE` placeholder, whose comment read "no combat system to target."

**Healing is deliberately *not* usable in combat**, and it's the interesting exclusion, because a Health Kit is the obvious combat item in most games. Health comes back slowly in this game, which means a fight would be the cheapest possible place to spend a kit — top up, swing again, top up — turning a fight from something you survive into something you grind, and making health (the resource the entire `DAMAGE_FOR_DELTA` curve is denominated in) refundable mid-encounter. You patch yourself up *after*, on your own time. Stamina and chems are out for the same reason.

### Codebase layout

```
src/shadowguy/
  character.py   Character dataclass: core stats, health, skill ranks/points, advantage bank, faction standing, local_standing, accepted_jobs
  archetypes.py   Enforcer/Hacker/Infiltrator creation presets; apply() spends via Character's own spend methods
  checks.py       resolve_check(): d20 + skill_value vs difficulty
  skills.py       Skill table (5 per core stat, 6 on perception), skill_value(), skill_for(); leaf module, imports nothing
  combat.py       Enemy roster, rounds, the six-stat action set, Drop/CombatOutcome, the shared resolve_hit; imports no scene
  tactical.py     grid tactical combat: Grid/Tile, tcod FOV+A*, turn engine (reuses combat.resolve_hit), BSP generate_map; imports no scene
  scene.py        Scene/Stage/Choice/Outcome/Encounter/TacticalStage data model, resolve_choice(), apply_outcome()
  content.py      unwired example job/legwork scenes (worked examples of the Scene data model)
  jobs.py         procedural job generation + timing (JobTiming) + per-job legwork generator
  gigs.py         procedural per-Location gig generation (per-kind templates), owned by a LocalCharacter; refresh_gigs
  fixer.py        Fixer/JobOffer persistent roster, offer refresh/expiry
  runners.py      hireable runner roster (specialist/rating; daily_cost + job_cut); recruited onto Character.crew (CrewHire) at a BarScreen
  factions.py     rival corp Factions (id/name/specialty) that own map territory; HQ officer ladder (rep+standing) + dialogue
  corpmap.py      procedural Corp-mode territory map + ASCII renderer; Location, LocalCharacter (per-location NPCs), one CORP_HQ per faction
  shops.py        retail LocationKinds: Item catalog (incl. weapon skill/damage), consumables, buy/sell/equip, standing-scaled pricing
  saves.py        pickle-based whole-run save/load (SAVE_VERSION, STATE_KEYS); leaf, imports no game classes
  app.py          Textual App: CharacterCreationScreen (start) + MainMenu + FixerListScreen/FixerOffersScreen + SceneScreen + CombatScreen + TacticalScreen + CorpMapScreen + ShopScreen + BarScreen + CorpHQScreen + InventoryScreen/SkillsScreen + QuitMenu/LoadMenu
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
