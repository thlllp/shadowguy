# Shadowguy

A text-based cyberpunk roguelite TUI. Python 3.14, built on Textual.

Two coupled game modes — **Runner mode** (RPG-scale, one character, scene-based missions, permadeath) and **Corp mode** (4X-scale, area control against rival corps).

## Quick start

```bash
uv sync
uv run shadowguy
```

## How it plays

You start as a nobody on the edge of a procedurally-generated city. Spend stat and skill points to build a runner (Enforcer, Hacker, or Infiltrator), then take gigs and fixer-issued jobs to earn cash and rep. Travel the corp map, build standing with shopkeepers and fixers, gear up, and try to survive.

Combat is round-based with d20 + skill checks. Weapons deal lethal or stun damage. Tactical grid fights use tcod FOV and pathfinding.

Death is permanent. No meta-progression between runs.

## Controls

| Key | Screen | Action |
|---|---|---|
| Arrow keys | Corp map | Move cursor |
| Enter | Corp map | Travel to node |
| f | Main menu | Browse fixers |
| q | Most screens | Open menu |
| Escape | Most screens | Back |

## Project structure

```
src/shadowguy/
  app.py          Textual App, screens (MainMenu, CombatScreen, CorpMapScreen, etc.)
  character.py    Character dataclass, stats, skills, inventory, standing
  skills.py       Skill table (31 skills across 6 core stats)
  checks.py       d20 + skill vs difficulty resolution
  combat.py       Round-based combat, actions, enemy roster, stun/knockout
  tactical.py     Grid tactical combat (tcod FOV + A*, BSP map gen)
  scene.py        Scene/Stage/Choice/Outcome data model
  jobs.py         Procedural job generation, stage templates, legwork
  gigs.py         Per-location gig generation
  fixer.py        Fixer roster, job offers, refresh/expiry
  runners.py      Hireable NPC runners (crew)
  factions.py     Rival corps, HQ officer ladder, standing rules
  corpmap.py      Procedural territory map (38 nodes), ASCII renderer, locations
  shops.py        Item/consumable catalogs, buy/sell/equip, standing pricing
  archetypes.py   Enforcer/Hacker/Infiltrator creation presets
  saves.py        Pickle-based save/load
```
