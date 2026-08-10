"""The player's own Corp turn: a parallel resolution module, like rivals.py/
security.py — not a Scene.

The player runs one of the 5 seeded Factions (CorpState.faction_id) rather than
founding a new one. Two ways in, both building a CorpState: a corp-only run picks
one at New Game (screens/menu_screens.py's CorpSelectScreen), and a runner earns
one mid-run by buying a controlling stake at that corp's own HQ
(screens/shop_screens.py's CorpHQScreen, gated on rep + standing +
factions.TAKEOVER_COST).

Corp mode shares the runner's own day clock rather than keeping a separate
calendar: ShadowguyApp's day tick (app._apply_day_tick) collects each day's
territory income into CorpState.cash and resets action_points, right
alongside the AI factions' own resolve_rival_day (which skips the player's
faction_id once this is set).

**A corp turn has two independent budgets**, which is the one thing worth knowing
before reading any function here:

- **The day's action points**, gated on CorpState.action_points (2 per day).
  Most actions cost 1 AP: expand_into, attack_territory, deploy_operatives,
  train_employees, build_lab, build_efficiency_upgrade, build_research_facility,
  build_academy, and operative tasking (tail/gather_intel/sabotage/investigate).
- **Whatever cash/RP has piled up.** research_technology and the two territory
  bumps (raise_surveillance, raise_development) deliberately do NOT touch
  action_points — RP and cash are their own pacing gates, and double-gating
  them behind action points would make researching compete with expanding for
  no design reason.

Three actions cost AP and nothing else, so a corp that's out of cash still has
something to spend the day on: fundraise (an emergency valve, only under
FUNDRAISE_CASH_CEILING), levy (Development traded back for cash) and survey
(gather_intel's recon without the operative).

Each faction is seeded one RESEARCH_FACILITY and one ACADEMY (corpmap.add_research_facility
/add_academy, called by the generator). A corp can come to hold two of a kind
(capturing a rival's district takes its buildings with it) or none (losing its own
the same way); build_research_facility/build_academy are the way back from none.

TECHNOLOGIES is the researchable list: two three-deep chains gated by
Technology.prereqs, rendered as a tree by screens.corp_screen.ResearchTreeScreen
(see technology_tree_layout). A tech's *effect* is not a field on Technology — it
is read wherever it applies, keyed off the id, so follow the id from the constants
below to its consumer (collect_income for the surveillance chain,
scientist_base_rate/assistant_rate for the brains chain).

Leaf-ish: imports corpmap only, never scene or app.
"""

import random
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from shadowguy.corpmap import (
    MODIFIER_MAX,
    STARTING_ACADEMY_TIER,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    TerritoryModifier,
    add_academy,
    add_research_facility,
    attack_candidates,
    capture_territory,
    claim_territory,
    expansion_candidates,
)

# First-slice numbers, not balance-simulated.
STARTING_CASH = 2000

# The most untasked operatives a corp can hold in its pool at once (garrisoned
# operatives don't count against this cap). Enforced by train_employees and
# advance_training. Low on purpose — the first few bodies are a real decision
# between defense, offense, and the new tasking options (tail_runner / gather_intel).
# May be raised by a future technology.
STARTING_OPERATIVE_MAX = 2

AP_COST = 1

TERRITORY_INCOME_BASE = 10
TERRITORY_INCOME_PER_VALUE = 15

# Mirrors corpmap.safehouse_price's base + per-value shape: a richer neutral
# territory costs more to move into.
EXPANSION_COST_BASE = 150
EXPANSION_COST_PER_VALUE = 100


class EmployeeCategory(StrEnum):
    """What a training session at the Academy produces. All three now have a
    consumer: scientists and research assistants staff the research facility
    (collect_research), and operatives are the corp's field force — deployed onto
    a district as its garrison, or committed to an attack on a rival's
    (deploy_operatives / attack_territory). They are tracked as three pools rather
    than one because they aren't fungible: an operative can't staff a lab and a
    scientist can't hold a block."""

    SCIENTIST = "scientist"
    OPERATIVE = "operative"
    RESEARCH_ASSISTANT = "research_assistant"


# Days a batch spends at the Academy before the hires land in the pool. Training
# is no longer instant: train_employees queues the batch and advance_training
# completes it on the day tick this many days later. Different roles take
# different amounts of time to train up. Not balance-simulated.
TRAINING_DAYS = {
    EmployeeCategory.SCIENTIST: 9,
    EmployeeCategory.OPERATIVE: 6,
    EmployeeCategory.RESEARCH_ASSISTANT: 3,
}

# Cash cost of one training batch, per category. Used to be a single flat 200 --
# same cost regardless of category made Research Assistants a dead pick once a
# game runs long enough for the training slot's opportunity cost to matter: same
# price as a Scientist, a third of the training time, but half the RP/day, so a
# Scientist trained back-to-back always overtakes an Assistant trained in the same
# stretch of slot-time (crossover ~day 15, and it never comes back). Pricing each
# category off its own RESEARCH_PER_SCIENTIST/RESEARCH_PER_ASSISTANT rate keeps
# cash-per-RP even across the two, so the real choice is capacity (lab_capacity vs
# assistant_capacity) and how soon you want the hire, not a strictly dominated
# option. An Operative produces no RP at all, so there's no rate to peg its price
# to -- it keeps the original flat price. Not balance-simulated.
ACADEMY_TRAINING_COST = {
    EmployeeCategory.SCIENTIST: 200,
    EmployeeCategory.OPERATIVE: 200,
    EmployeeCategory.RESEARCH_ASSISTANT: 100,
}

# A research facility seats this many working scientists for free, before any
# lab is built.
BASE_LAB_CAPACITY = 1
# Cost of the 1st and 2nd extra lab, indexed by Location.labs_built -- strictly
# sequential, so the 2nd lab's cost/capacity isn't reachable without the 1st.
LAB_UPGRADE_COSTS = (2000, 5000)
MAX_LABS_BUILT = len(LAB_UPGRADE_COSTS)
# RP/day each working scientist adds, on top of the facility's own tier.
RESEARCH_PER_SCIENTIST = 1
# Standing a new research facility up after a rival captured the last one the corp
# held (build_research_facility). Priced between the 1st and 2nd lab: dearer than a
# lab, since it's a whole building and it comes with the free base seat, but not so
# dear that losing your labs ends research for the run. The rebuild starts bare, so
# the real cost is this plus re-buying every lab and efficiency upgrade that was in
# the captured one. Not balance-simulated.
RESEARCH_FACILITY_REBUILD_COST = 3000

# The academy's rebuild (build_academy). Restores at STARTING_ACADEMY_TIER — any
# accumulated tier upgrades on the captured one are lost, same as a Research
# Facility's labs and efficiency. Deliberately cheaper than the facility's bare
# rebuild (3000) even though an academy is the worse loss, because the facility's
# rebuild buys a shell whose upgrades add another 17,000 on top; pricing them
# equal would make the academy the strictly worse deal for restoring strictly
# more. Not balance-simulated.
ACADEMY_REBUILD_COST = 2000

# Pay these to raise academy_tier by one (STARTING_ACADEMY_TIER → 2 → 3). Two
# slots, progressively steeper. Costs 1 AP, same as build_lab / build_academy.
ACADEMY_UPGRADE_COSTS = (3000, 8000)
MAX_ACADEMY_TIER = STARTING_ACADEMY_TIER + len(ACADEMY_UPGRADE_COSTS)  # 3

# Cost of the 1st and 2nd efficiency upgrade, indexed by
# Location.efficiency_upgrades -- strictly sequential, same shape as
# LAB_UPGRADE_COSTS. Priced steeper than a lab: +1 RP/scientist compounds with
# however many scientists are staffed, so it can be worth more than +1 capacity.
EFFICIENCY_UPGRADE_COSTS = (3000, 7000)
MAX_EFFICIENCY_UPGRADES = len(EFFICIENCY_UPGRADE_COSTS)

# Each lab (including the free base one) seats this many research assistants,
# on top of its own scientist.
RESEARCH_ASSISTANTS_PER_LAB = 2
# RP/day each working research assistant adds — flat, unlike research_rate:
# efficiency upgrades boost scientists only.
RESEARCH_PER_ASSISTANT = 0.5

# CorpState.sightings is capped at this many entries (most-recent-first) —
# an unbounded log would grow for the life of a run for no read anything
# further back than a handful of days actually wants. Also imported by
# surveillance.py.
MAX_SIGHTINGS_LOG = 10

# --- Conflict ---------------------------------------------------------------
# Both sides of a contest add one die of this size to their strength, so a
# defended district is never a foregone conclusion in either direction: a d6
# swing on top of operatives-vs-(garrison + Security) means a 2-point edge is a
# strong favorite and a 6-point edge is a certainty. First-slice numbers, not
# balance-simulated.
CONTEST_DIE = 6
# Ties go to the defender (attack_power must strictly exceed defense_power), so
# an unattended, unpoliced district still costs the attacker at least one
# operative and one lucky roll rather than falling to a bare zero.
MIN_ATTACK_FORCE = 1


@dataclass(frozen=True)
class Technology:
    """One researchable corp technology. `cost` is in research points. `prereqs`
    names other Technology ids that must already be researched before this one can
    be — a tuple so a tech can (today doesn't, but could) name more than one — and
    is what turns the flat catalog into the tree
    screens/corp_screen.ResearchTreeScreen renders.

    Effects are *not* fields here: a tech's effect is read where it applies
    (collect_income for the income bonus, raise_surveillance for the ability),
    keyed off its id, rather than described by a generic bonus field the reader
    would then have to hunt for the consumer of. One tech, one place that asks
    "is it researched?" — the same shape jobs.archetype_specialist uses, derived
    at the point of use instead of tabulated.
    """

    id: str
    name: str
    cost: int  # research points
    prereqs: tuple[str, ...]
    description: str
    # When set, only this Faction (factions.Faction.id) can research this
    # technology. None means any faction can. The ResearchTreeScreen hides
    # faction-gated techs that don't match the player's corp.
    faction_id: str | None = None


WORKER_SURVEILLANCE_ID = "worker_surveillance"
PANOPTICON_GRID_ID = "panopticon_grid"
SHADOW_ECONOMY_ID = "shadow_economy"
BRAINS_2_ID = "brains_2"
BRAINS_3_ID = "brains_3"
COGNITIVE_UPLINK_ID = "cognitive_uplink"
TOTAL_INFORMATION_AWARENESS_ID = "total_information_awareness"
COUNTER_INTELLIGENCE_ID = "counter_intelligence"
DEEP_SURVEILLANCE_ID = "deep_surveillance"
OPERATION_INTERCEPT_ID = "operation_intercept"
HARDENED_GARRISON_ID = "hardened_garrison"
SHOCK_ASSAULT_ID = "shock_assault"
SIGNAL_INTERCEPT_ID = "signal_intercept"
ICE_CRACKED_NETWORKS_ID = "ice_cracked_networks"
COMBAT_STIMS_ID = "combat_stims"
RAPID_DEPLOYMENT_ID = "rapid_deployment"
OPTIMIZED_WORKFORCE_ID = "optimized_workforce"
SUPPLY_CHAIN_ID = "supply_chain"
CONSOLIDATED_HOLDINGS_ID = "consolidated_holdings"
FORTIFIED_DEFENSES_ID = "fortified_defenses"
RESEARCH_EXPANSION_ID = "research_expansion"
TOTAL_WAR_ID = "total_war"
DEEP_SURVEILLANCE_PROTOCOL_ID = "deep_surveillance_protocol"
ACCELERATED_METABOLISM_ID = "accelerated_metabolism"
MARKET_MONOPOLY_ID = "market_monopoly"
TITHES_ID = "tithes"
CRUSADE_ID = "crusade"

# id, name, cost (RP), prereqs, description — six public roots (Worker
# Surveillance, Brains 2, Counter-Intelligence, Consolidated Holdings,
# Fortified Defenses, Research Expansion) and their chains, plus five
# faction-specific chains (Ironclad: Hardened Garrison → Shock Assault → Total
# War; Ghostwire: Signal Intercept → ICE-Cracked Networks → Deep Surveillance
# Protocol; Meridian: Combat Stims → Rapid Deployment → Accelerated Metabolism;
# Prometheus: Optimized Workforce → Supply Chain → Market Monopoly; Sanctuary:
# Tithes → Crusade). Every root has empty prereqs, researchable from day one;
# every other row names the tech directly below it in its own chain. A row's
# prereqs must already have appeared earlier in this tuple — enforced below,
# because technology_tree_layout() (and the topological loop that builds
# TECHNOLOGIES itself) both assume a prereq's own row is already processed by
# the time a dependent reads it.
_TECHNOLOGY_ROWS = (
    (
        WORKER_SURVEILLANCE_ID,
        "Worker Surveillance",
        10,
        (),
        "Every territory you hold earns +{income}/day, and you can pay {bump}eb "
        "to raise Surveillance by 1 in any district you hold that isn't already at "
        f"{MODIFIER_MAX}.",
        None,
    ),
    (
        PANOPTICON_GRID_ID,
        "Panopticon Grid",
        20,
        (WORKER_SURVEILLANCE_ID,),
        "Every territory you hold earns another +{panopticon_income}/day on top "
        "of Worker Surveillance's bonus.",
        None,
    ),
    (
        SHADOW_ECONOMY_ID,
        "Shadow Economy",
        35,
        (PANOPTICON_GRID_ID,),
        "Every territory you hold earns another +{shadow_income}/day on top of "
        "Worker Surveillance and Panopticon Grid's bonuses.",
        None,
    ),
    (
        BRAINS_2_ID,
        "Brains 2",
        10,
        (),
        "Every working scientist produces {scientist2}rp/day instead of "
        "{base_scientist}, and every working research assistant {assistant2}rp/day "
        "instead of {base_assistant}.",
        None,
    ),
    (
        BRAINS_3_ID,
        "Brains 3",
        20,
        (BRAINS_2_ID,),
        "Every working scientist produces {scientist3}rp/day and every working "
        "research assistant {assistant3}rp/day, replacing Brains 2's rates.",
        None,
    ),
    (
        COGNITIVE_UPLINK_ID,
        "Cognitive Uplink",
        35,
        (BRAINS_3_ID,),
        "Every working scientist produces {scientist4}rp/day and every working "
        "research assistant {assistant4}rp/day, replacing Brains 3's rates.",
        None,
    ),
    (
        TOTAL_INFORMATION_AWARENESS_ID,
        "Total Information Awareness",
        50,
        (SHADOW_ECONOMY_ID,),
        "Every sighting your Surveillance network catches generates {sighting_rp}rp, "
        "detection chance rises by {detection_bonus_pct} at every level, and you "
        "can spend {investigation_cost}eb to investigate a sighting (costs 1 AP).",
        None,
    ),
    (
        COUNTER_INTELLIGENCE_ID,
        "Counter-Intelligence",
        20,
        (),
        "Raising Surveillance costs {discounted_surveillance_cost}eb instead of "
        "{base_surveillance_cost}, your sightings log holds "
        "{extended_sightings} entries instead of {base_sightings}, and "
        "sightings name the detected runner's faction.",
        None,
    ),
    (
        DEEP_SURVEILLANCE_ID,
        "Deep Surveillance",
        30,
        (COUNTER_INTELLIGENCE_ID,),
        "You can raise Surveillance one level beyond the normal cap (to "
        "{extended_max}), costing {extended_surveillance_cost}eb for that final "
        "level. Detection chance at that level: {extended_detection_pct}.",
        None,
    ),
    (
        OPERATION_INTERCEPT_ID,
        "Operation Intercept",
        45,
        (DEEP_SURVEILLANCE_ID,),
        "When your Surveillance network detects a runner, there is a "
        "{interception_pct} chance their current activity is disrupted "
        "(they go to ground).",
        None,
    ),
    # --- 3 public techs (all-faction, no faction gate) ------------------------
    (
        CONSOLIDATED_HOLDINGS_ID,
        "Consolidated Holdings",
        20,
        (),
        "Emergency fundraising yields {consolidated_rate}eb per territory instead "
        "of {base_rate}eb.",
        None,
    ),
    (
        FORTIFIED_DEFENSES_ID,
        "Fortified Defenses",
        25,
        (),
        "Your districts get +1 on the defense contest die when a rival attacks "
        "them.",
        None,
    ),
    (
        RESEARCH_EXPANSION_ID,
        "Research Expansion",
        20,
        (),
        "Each research lab seats {expanded_scientist} additional scientist, "
        "raising capacity from {base_capacity} to {expanded_capacity} per lab.",
        None,
    ),
    # --- Ironclad Dynamics (WEAPONS) -------------------------------------------
    (
        "hardened_garrison",
        "Hardened Garrison",
        25,
        (),
        "Each operative garrisoned in a district you hold counts as 2 operatives "
        "for defense strength (garrison × 2 + Security).",
        "faction_ironclad",
    ),
    (
        "shock_assault",
        "Shock Assault",
        40,
        ("hardened_garrison",),
        "Your attack rolls get +1 on the contest die, making every assault hit "
        "harder.",
        "faction_ironclad",
    ),
    (
        TOTAL_WAR_ID,
        "Total War",
        60,
        (SHOCK_ASSAULT_ID,),
        "Your attack rolls get +{total_war_bonus} on the contest die (replaces "
        "Shock Assault's +1).",
        "faction_ironclad",
    ),
    # --- Ghostwire Collective (HACKING) ----------------------------------------
    (
        "signal_intercept",
        "Signal Intercept",
        25,
        (),
        "Every sighting in your Surveillance log generates {sighting_rp}rp/day "
        "on its own (stacks with Total Information Awareness when both are "
        "researched).",
        "faction_ghostwire",
    ),
    (
        "ice_cracked_networks",
        "ICE-Cracked Networks",
        40,
        ("signal_intercept",),
        "Surveillance detection chance rises by {ghostwire_detection_bonus_pct} "
        "at every level (stacks with Total Information Awareness).",
        "faction_ghostwire",
    ),
    (
        DEEP_SURVEILLANCE_PROTOCOL_ID,
        "Deep Surveillance Protocol",
        55,
        (ICE_CRACKED_NETWORKS_ID,),
        "Surveillance detection chance bonus rises to "
        "{deep_protocol_detection_bonus_pct} (replaces ICE-Cracked Networks' "
        "{ghostwire_detection_bonus_pct}).",
        "faction_ghostwire",
    ),
    # --- Meridian Biochem (PHARMA) ---------------------------------------------
    (
        "combat_stims",
        "Combat Stims",
        25,
        (),
        "Training a batch of operatives costs {stims_operative_cost}eb instead of "
        "{base_operative_cost}eb.",
        "faction_meridian",
    ),
    (
        "rapid_deployment",
        "Rapid Deployment",
        35,
        ("combat_stims",),
        "Operative training completes in {stims_operative_days} days instead of "
        "{base_operative_days}.",
        "faction_meridian",
    ),
    (
        ACCELERATED_METABOLISM_ID,
        "Accelerated Metabolism",
        50,
        (RAPID_DEPLOYMENT_ID,),
        "Operative training costs {accelerated_operative_cost}eb and completes in "
        "{accelerated_operative_days} day (replaces Combat Stims and Rapid "
        "Deployment's rates).",
        "faction_meridian",
    ),
    # --- Prometheus Cybernetics (CYBERNETICS) ----------------------------------
    (
        "optimized_workforce",
        "Optimized Workforce",
        20,
        (),
        "Every territory you hold earns +{workforce_income}/day in base income.",
        "faction_prometheus",
    ),
    (
        "supply_chain",
        "Supply Chain",
        35,
        ("optimized_workforce",),
        "Expanding into neutral territory costs half as much (base cost "
        "{base_expansion}eb → {supply_chain_expansion}eb).",
        "faction_prometheus",
    ),
    (
        MARKET_MONOPOLY_ID,
        "Market Monopoly",
        55,
        (SUPPLY_CHAIN_ID,),
        "Every territory you hold earns +{market_monopoly_income}eb/day in base "
        "income (replaces Optimized Workforce's +{workforce_income}eb).",
        "faction_prometheus",
    ),
    # --- Sanctuary Holdings (FAITH) --------------------------------------------
    (
        TITHES_ID,
        "Tithes",
        25,
        (),
        "Every territory you hold earns +{tithes_income}eb/day in base income.",
        "faction_sanctuary",
    ),
    (
        CRUSADE_ID,
        "Crusade",
        45,
        (TITHES_ID,),
        "Your attack rolls get +1 on the contest die.",
        "faction_sanctuary",
    ),
)

# What Worker Surveillance is worth, in the two places it lands. The income bonus
# is per *territory* (it exactly doubles TERRITORY_INCOME_BASE), so the tech keeps
# paying as the corp expands rather than becoming a rounding error. Panopticon
# Grid and Shadow Economy stack more of the same on top rather than replacing it
# (unlike the Brains chain below) — collect_income sums whichever of the three
# are researched.
WORKER_SURVEILLANCE_INCOME_BONUS = 10
PANOPTICON_GRID_INCOME_BONUS = 15
SHADOW_ECONOMY_INCOME_BONUS = 25
# Cash per Surveillance bump. Deliberately NOT on the action_points slot —
# unlike expand/train/build, this is repeatable within a day and cash is its only
# gate, so the tech's own income bonus partly funds its use.
SURVEILLANCE_BUMP_COST = 400

# Development is raised as a *purchase*, not re-derived (see raise_development):
# capital only lands where the block is already both policed and watched, so a
# district has to clear both thresholds before it can be built up at all. This
# mirrors _development()'s own "rises with Security and Surveillance" logic
# without turning it back into an automatic re-derivation. Same cash-gated,
# repeatable shape as SURVEILLANCE_BUMP_COST, priced steeper because Development
# is the modifier that actually does something today (it prices runner-side
# lodging and safehouses — see corpmap.lodging_cost/safehouse_price).
# First-slice numbers, not balance-simulated.
DEVELOPMENT_MIN_SECURITY = 3
DEVELOPMENT_MIN_SURVEILLANCE = 3
DEVELOPMENT_BUMP_COST = 800

# --- Free actions -------------------------------------------------------------
# Everything above spends cash as well as AP, so a broke corp used to have both
# its action points and nothing to put them on: the operative moves are the only
# cash-free ones, and operatives themselves are bought at the Academy. The three
# below cost 1 AP and 0eb.

# Emergency fundraising: eb per district held, offered only while the corp's cash
# is under FUNDRAISE_CASH_CEILING. The ceiling is what keeps this an emergency
# valve instead of a second income stream — a solvent corp can't call it at all,
# so it never competes with collect_income as a way to make money.
FUNDRAISE_PER_TERRITORY = 25
FUNDRAISE_CASH_CEILING = STARTING_CASH

# Levy: eb per point of a district's value, paid for with a point of its
# Development. Deliberately lossy against DEVELOPMENT_BUMP_COST in both
# directions — selling a built-up block back off is a bad trade, just a
# survivable one.
LEVY_PER_VALUE = 100

# Each Brains tier replaces both per-head research rates outright rather than
# adding to them — a flat better rate, not a stacking bonus, so there's one
# number in effect at a time and scientist_base_rate/assistant_rate just pick
# the highest tier researched. Efficiency upgrades still stack on top of the
# scientist rate (see research_rate), so the building path and this tech chain
# compose rather than compete. Unlike the surveillance chain's cash payoff this
# compounds — it makes research itself faster, which is why Brains 2 costs the
# same 10 RP as Worker Surveillance despite looking smaller on paper.
# First-slice numbers, not balance-simulated.
BRAINS_2_RESEARCH_PER_SCIENTIST = 1.25
BRAINS_2_RESEARCH_PER_ASSISTANT = 0.75
BRAINS_3_RESEARCH_PER_SCIENTIST = 1.5
BRAINS_3_RESEARCH_PER_ASSISTANT = 0.9
COGNITIVE_UPLINK_RESEARCH_PER_SCIENTIST = 2.0
COGNITIVE_UPLINK_RESEARCH_PER_ASSISTANT = 1.2

# --- Total Information Awareness (tier 4 of income/surveillance chain) ---------
# RP generated per sighting caught in the corp's own territory, added by
# collect_research alongside the facility output. Flat rather than scaled so a
# heavily-watched corp in a busy territory (many rival runners passing through)
# gets a steady trickle, not a fountain, from a source that already feeds
# informational pressure.
SIGHTING_RESEARCH_BONUS = 5
# Flat detection-chance bonus applied at every Surveillance level — stacks on
# top of SURVEILLANCE_DETECTION_CHANCE in surveillance.py.
DETECTION_CHANCE_BONUS = 0.05
# Cost in eb to investigate a single sighting. Costs 1 AP.
# (unlike raise_surveillance), so it competes with expand/attack/train.
INVESTIGATION_COST = 600

# --- Counter-Intelligence chain -------------------------------------------------
# Discounted Surveillance bump cost. Worker Surveillance still gates the ability
# itself; this is purely the price break.
COUNTERINTEL_SURVEILLANCE_COST = 250
# Extended sightings-log cap. Still pruned most-recent-first; just holds more.
COUNTERINTEL_SIGHTINGS_LOG = 20

# --- Deep Surveillance ----------------------------------------------------------
# One level beyond MODIFIER_MAX, gated behind the Deep Surveillance technology.
EXTENDED_SURVEILLANCE_MAX = 6
# Cost of the final bump (level 5→6), steeper because it's beyond the normal cap.
EXTENDED_SURVEILLANCE_COST = 800
# Detection chance at the extended level. Indexed directly rather than extending
# the SURVEILLANCE_DETECTION_CHANCE tuple (which is sized to MODIFIER_MAX).
EXTENDED_SURVEILLANCE_DETECTION = 0.80

# --- Operation Intercept --------------------------------------------------------
# Chance that a successful detection disrupts the target's current activity.
INTERCEPTION_CHANCE = 0.25

# --- Three more public techs: Consolidated Holdings, Fortified Defenses, Research
# Expansion.
# Fundraising yield per territory when Consolidated Holdings is researched.
CONSOLIDATED_FUNDRAISE_PER_TERRITORY = 35
# Bonus on the defense contest die when Fortified Defenses is researched.
FORTIFIED_DEFENSES_BONUS = 1
# Extra scientist capacity per lab when Research Expansion is researched.
RESEARCH_EXPANSION_BONUS = 1

# --- Ironclad Dynamics: Hardened Garrison / Shock Assault --------------------
# Multiplier applied to garrison in defense_strength when Hardened Garrison is
# researched. Normally garrison counts 1:1; this makes garrisoned operatives
# worth double.
HARDENED_GARRISON_MULTIPLIER = 2
# Bonus added to the attacker's contest die in attack_territory when Shock
# Assault is researched. Makes a 1-oper assault still win vs defense of 1
# (1+d6+1 > 1+d6 on ties goes to attacker).
SHOCK_ASSAULT_BONUS = 1

# --- Ghostwire Collective: Signal Intercept / ICE-Cracked Networks ------------
# RP per sighting when Signal Intercept is researched, independent of Total
# Information Awareness. Same value as SIGHTING_RESEARCH_BONUS — the two stack,
# so a corp with both earns 10 RP per sighting.
SIGNAL_INTERCEPT_RP = 5
# Additional detection chance bonus from ICE-Cracked Networks, stacking with
# Total Information Awareness's DETECTION_CHANCE_BONUS.
GHOSTWIRE_DETECTION_BONUS = 0.10

# --- Meridian Biochem: Combat Stims / Rapid Deployment ------------------------
# Discounted operative training cost with Combat Stims researched.
STIMS_OPERATIVE_COST = 100
# Shortened operative training days with Rapid Deployment researched.
STIMS_OPERATIVE_DAYS = 3

# --- Prometheus Cybernetics: Optimized Workforce / Supply Chain ----------------
# Extra base income per territory with Optimized Workforce researched.
WORKFORCE_INCOME_BONUS = 5
# Halved expansion base cost with Supply Chain researched.
SUPPLY_CHAIN_EXPANSION_BASE = EXPANSION_COST_BASE // 2

# --- Ironclad: Total War ---------------------------------------------------
TOTAL_WAR_BONUS = 2

# --- Ghostwire: Deep Surveillance Protocol ----------------------------------
DEEP_PROTOCOL_DETECTION_BONUS = 0.20

# --- Meridian: Accelerated Metabolism ---------------------------------------
ACCELERATED_OPERATIVE_COST = 50
ACCELERATED_OPERATIVE_DAYS = 1

# --- Prometheus: Market Monopoly --------------------------------------------
MARKET_MONOPOLY_INCOME_BONUS = 20

# --- Sanctuary Holdings: Tithes / Crusade -----------------------------------
TITHES_INCOME_BONUS = 15
CRUSADE_BONUS = 1

# Descriptions are filled in from the constants above rather than repeating the
# numbers as prose, so a retune can't leave the shop text lying about the effect.
_TECHNOLOGY_DESCRIPTION_ARGS = dict(
    income=WORKER_SURVEILLANCE_INCOME_BONUS,
    panopticon_income=PANOPTICON_GRID_INCOME_BONUS,
    shadow_income=SHADOW_ECONOMY_INCOME_BONUS,
    bump=SURVEILLANCE_BUMP_COST,
    scientist2=BRAINS_2_RESEARCH_PER_SCIENTIST,
    assistant2=BRAINS_2_RESEARCH_PER_ASSISTANT,
    scientist3=BRAINS_3_RESEARCH_PER_SCIENTIST,
    assistant3=BRAINS_3_RESEARCH_PER_ASSISTANT,
    scientist4=COGNITIVE_UPLINK_RESEARCH_PER_SCIENTIST,
    assistant4=COGNITIVE_UPLINK_RESEARCH_PER_ASSISTANT,
    base_scientist=RESEARCH_PER_SCIENTIST,
    base_assistant=RESEARCH_PER_ASSISTANT,
    sighting_rp=SIGHTING_RESEARCH_BONUS,
    detection_bonus_pct=f"{DETECTION_CHANCE_BONUS:.0%}",
    investigation_cost=INVESTIGATION_COST,
    discounted_surveillance_cost=COUNTERINTEL_SURVEILLANCE_COST,
    base_surveillance_cost=SURVEILLANCE_BUMP_COST,
    extended_sightings=COUNTERINTEL_SIGHTINGS_LOG,
    base_sightings=MAX_SIGHTINGS_LOG,
    extended_max=EXTENDED_SURVEILLANCE_MAX,
    extended_surveillance_cost=EXTENDED_SURVEILLANCE_COST,
    extended_detection_pct=f"{EXTENDED_SURVEILLANCE_DETECTION:.0%}",
    interception_pct=f"{INTERCEPTION_CHANCE:.0%}",
    ghostwire_detection_bonus_pct=f"{GHOSTWIRE_DETECTION_BONUS:.0%}",
    stims_operative_cost=STIMS_OPERATIVE_COST,
    base_operative_cost=ACADEMY_TRAINING_COST[EmployeeCategory.OPERATIVE],
    stims_operative_days=STIMS_OPERATIVE_DAYS,
    base_operative_days=TRAINING_DAYS[EmployeeCategory.OPERATIVE],
    workforce_income=WORKFORCE_INCOME_BONUS,
    base_expansion=EXPANSION_COST_BASE,
    supply_chain_expansion=SUPPLY_CHAIN_EXPANSION_BASE,
    consolidated_rate=CONSOLIDATED_FUNDRAISE_PER_TERRITORY,
    base_rate=FUNDRAISE_PER_TERRITORY,
    expanded_scientist=RESEARCH_EXPANSION_BONUS,
    base_capacity=BASE_LAB_CAPACITY,
    expanded_capacity=BASE_LAB_CAPACITY + RESEARCH_EXPANSION_BONUS,
    total_war_bonus=TOTAL_WAR_BONUS,
    deep_protocol_detection_bonus_pct=f"{DEEP_PROTOCOL_DETECTION_BONUS:.0%}",
    accelerated_operative_cost=ACCELERATED_OPERATIVE_COST,
    accelerated_operative_days=ACCELERATED_OPERATIVE_DAYS,
    market_monopoly_income=MARKET_MONOPOLY_INCOME_BONUS,
    tithes_income=TITHES_INCOME_BONUS,
)

# A row's prereqs must already have been seen — i.e. defined earlier in
# _TECHNOLOGY_ROWS — both so the tree only ever points "backward" (no cycles)
# and so technology_tree_layout() can assume a prereq's own position is already
# known by the time a dependent asks for it.
_seen_ids: set[str] = set()
for _row in _TECHNOLOGY_ROWS:
    if any(prereq not in _seen_ids for prereq in _row[3]):
        raise ValueError(f"{_row[0]}'s prereqs must be defined earlier in _TECHNOLOGY_ROWS")
    _seen_ids.add(_row[0])
del _seen_ids, _row

TECHNOLOGIES = [
    Technology(
        id=tech_id,
        name=name,
        cost=cost,
        prereqs=prereqs,
        description=description.format(**_TECHNOLOGY_DESCRIPTION_ARGS),
        faction_id=faction_id,
    )
    for tech_id, name, cost, prereqs, description, faction_id in _TECHNOLOGY_ROWS
]
TECHNOLOGIES_BY_ID = {tech.id: tech for tech in TECHNOLOGIES}

if any(tech.cost <= 0 for tech in TECHNOLOGIES):
    raise ValueError("a Technology must cost research points to be worth researching")


def technology_tree_layout() -> dict[str, tuple[int, int]]:
    """(column, row) position for every Technology, for
    screens.corp_screen.ResearchTreeScreen's tiered display: column is prereq-
    chain depth (0 for a root technology), row keeps a technology in the same
    lane as its first prereq so a chain reads as one row all the way down. Every
    technology in the table today has at most one prereq, so "first prereq's
    row" is exact, not an approximation; a technology with two differently-laned
    prereqs would just inherit the first one's lane rather than something
    fancier, since nothing here needs more than that yet.

    Walks TECHNOLOGIES in order, which is safe because _TECHNOLOGY_ROWS is
    checked at import to list a prereq before anything that depends on it."""
    depth: dict[str, int] = {}
    row: dict[str, int] = {}
    next_root_row = 0
    for technology in TECHNOLOGIES:
        if not technology.prereqs:
            depth[technology.id] = 0
            row[technology.id] = next_root_row
            next_root_row += 1
        else:
            depth[technology.id] = 1 + max(depth[p] for p in technology.prereqs)
            row[technology.id] = row[technology.prereqs[0]]
    return {technology.id: (depth[technology.id], row[technology.id]) for technology in TECHNOLOGIES}


@dataclass
class Sighting:
    """One Surveillance hit: a known runner (the player, or a runners.RivalRunner)
    that surveillance.py caught inside this corp's own territory on a given day.

    Plain data, the same reason scene.Role holds no jobs.StageType rather than a
    real jobs.StageType field: corp_turn.py stays a leaf (imports corpmap only),
    so surveillance.py -- which does the actual detecting, and needs CorpState in
    turn -- can hold a list of these on CorpState without corp_turn.py importing
    surveillance.py back (that would be a cycle)."""

    kind: Literal["player", "runner"]
    actor_id: str  # "player", or a runners.RivalRunner.id
    territory_id: str
    day: int
    # Populated when Counter-Intelligence is researched: which faction the
    # detected runner belongs to (or "independent" when none). None otherwise.
    runner_faction_id: str | None = None
    # Whether Operation Intercept disrupted this runner's current activity.
    # Only ever True when the tech is researched and the interception roll hit.
    intercepted: bool = False


# Per-faction blog history, capped like Sighting/MAX_SIGHTINGS_LOG.
MAX_FACTION_EVENTS = 15


@dataclass
class FactionEvent:
    """One newsworthy thing a Faction did, for its corp website's blog
    (screens/info_screens.py's CorpWebsiteScreen): a territory claimed, a
    Technology researched, or a district seized off a rival. Populated for every
    Faction, not just the player's own — rivals.resolve_rival_day logs expansion,
    its own simplified per-faction research roll and every successful attack, and
    CorpScreen/ResearchTreeScreen log the player's own corp's
    expand_into/attack_territory/research_technology calls the same way, since
    resolve_rival_day skips whichever faction the player runs.

    "seizure" is deliberately its own kind rather than another "territory": both
    grow a corp's holdings, but only one of them took the ground off somebody, and
    from_faction_id is who. A corp's own site spins it as a win either way — the
    losing corp's site doesn't report it at all, which is the joke."""

    kind: Literal["territory", "technology", "seizure"]
    day: int
    territory_id: str | None = None  # kind == "territory" or "seizure"
    technology_id: str | None = None  # kind == "technology"
    from_faction_id: str | None = None  # kind == "seizure": who they took it from


def log_faction_event(
    events: dict[str, list[FactionEvent]], faction_id: str, event: FactionEvent
) -> None:
    """Prepend `event` to `faction_id`'s log (most-recent-first) and trim it back
    to MAX_FACTION_EVENTS, the same shape CorpState.sightings uses."""
    log = events.setdefault(faction_id, [])
    log.insert(0, event)
    del log[MAX_FACTION_EVENTS:]


def employee_plural(category: EmployeeCategory) -> str:
    """research_assistant -> "research assistants"; scientist/operative have no
    underscore to begin with, so this just adds the s."""
    return f"{category.replace('_', ' ')}s"


def operative_training_cost(corp_state: CorpState) -> int:
    """Base training cost for operatives, discounted by Meridian Biochem's
    Combat Stims or Accelerated Metabolism."""
    if has_technology(corp_state, ACCELERATED_METABOLISM_ID):
        return ACCELERATED_OPERATIVE_COST
    if has_technology(corp_state, COMBAT_STIMS_ID):
        return STIMS_OPERATIVE_COST
    return ACADEMY_TRAINING_COST[EmployeeCategory.OPERATIVE]


def operative_training_days(corp_state: CorpState) -> int:
    """Training duration for operatives, shortened by Meridian Biochem's
    Rapid Deployment or Accelerated Metabolism."""
    if has_technology(corp_state, ACCELERATED_METABOLISM_ID):
        return ACCELERATED_OPERATIVE_DAYS
    if has_technology(corp_state, RAPID_DEPLOYMENT_ID):
        return STIMS_OPERATIVE_DAYS
    return TRAINING_DAYS[EmployeeCategory.OPERATIVE]


def operative_max(_corp_state: CorpState) -> int:
    """The most untasked operatives this corp can hold in its pool at once.
    Garrisoned operatives (on Territory.garrison) don't count against this cap —
    it gates the pool size, not total headcount. Starts at STARTING_OPERATIVE_MAX;
    may be raised by a future technology."""
    return STARTING_OPERATIVE_MAX


def _add_operatives(corp_state: CorpState, count: int) -> int:
    """Add up to `count` operatives to the pool, clipped at operative_max — the
    shared mutator every path that grows the pool from outside it (training
    completing, a tasking operative returning) goes through, so the cap can't be
    missed by a future call site. Returns how many actually landed."""
    room = max(operative_max(corp_state) - corp_state.operatives, 0)
    added = min(count, room)
    corp_state.operatives += added
    return added


@dataclass
class PendingRecruit:
    """A training batch in progress at the Academy: which category is training,
    how many hires it yields (the Academy's tier when training began), and the
    day advance_training drops them into the pool. The Academy runs one batch at
    a time — CorpState.pending_recruit holds at most one."""

    category: EmployeeCategory
    count: int
    ready_day: int


@dataclass
class CorpState:
    """The player's own corp: which Faction they run, its cash/research points/
    scientists/operatives/research_assistants on hand, and how many action points
    remain today (2 per day)."""

    faction_id: str
    cash: int = STARTING_CASH
    research_points: float = 0
    scientists: int = 0
    operatives: int = 0
    research_assistants: int = 0
    action_points: int = 2
    # A training batch in progress at the Academy, or None when idle. The Academy
    # has a single training slot, so train_employees won't start a second batch
    # while this is set; advance_training clears it once its ready_day arrives.
    pending_recruit: PendingRecruit | None = None
    # Technology ids (TECHNOLOGIES_BY_ID) already researched. A set of ids, the
    # same shape Character.owned_programs/discovered_fixers use. Research is
    # permanent — nothing takes a tech back.
    researched: set[str] = field(default_factory=set)
    # Surveillance sightings logged against this corp's own territory,
    # most-recent-first, capped by MAX_SIGHTINGS_LOG (or COUNTERINTEL_SIGHTINGS_LOG
    # when Counter-Intelligence is researched). Stays empty
    # until surveillance.resolve_surveillance_day actually catches someone —
    # corp_turn.py never appends to this itself.
    sightings: list[Sighting] = field(default_factory=list)
    # Operatives currently out on a task (tail_runner / gather_intel). They leave
    # the pool when dispatched and return on the next day tick — see
    # return_tasking_operatives. Doesn't count against operative_max, so a corp at
    # cap can still send them out (they'll come back into a capped pool and be
    # lost above it — dispatch them wisely).
    tasking_operatives: int = 0


def has_technology(corp_state: CorpState, technology_id: str) -> bool:
    return technology_id in corp_state.researched


def prereqs_met(corp_state: CorpState, technology: Technology) -> bool:
    """Whether every one of a Technology's prereqs is already researched — True
    for a root technology (empty prereqs) for free, since all() of nothing is
    True."""
    return all(has_technology(corp_state, prereq) for prereq in technology.prereqs)


def research_technology(corp_state: CorpState, technology_id: str) -> bool:
    """Spend research points to unlock a Technology permanently. Fails closed (no
    charge, no mutation) if it's already researched, its prereqs aren't all
    researched yet, the corp can't afford it, or the technology is faction-gated
    to a different faction than the one the player is running.

    Deliberately NOT on the action_points slot: RP is its own pacing gate
    (10 RP is ~10 days of research at the base rate), and double-gating a
    purchase behind action points would make researching compete
    with expanding for no design reason. Same call the cash-gated territory
    bumps below make.
    """
    technology = TECHNOLOGIES_BY_ID[technology_id]
    if has_technology(corp_state, technology_id) or not prereqs_met(corp_state, technology):
        return False
    if technology.faction_id is not None and technology.faction_id != corp_state.faction_id:
        return False
    if technology.cost > corp_state.research_points:
        return False
    corp_state.research_points -= technology.cost
    corp_state.researched.add(technology_id)
    return True


def collect_income(corp_state: CorpState, corp_map: CorpMap) -> int:
    """Flat daily income from every territory the player's faction holds, plus
    whichever of the surveillance chain's per-territory bonuses are researched
    (WORKER_SURVEILLANCE_INCOME_BONUS, then PANOPTICON_GRID_INCOME_BONUS, then
    SHADOW_ECONOMY_INCOME_BONUS — summed, not replaced, unlike the Brains
    chain's research rates) — per territory, not once, so each tech keeps
    paying as the corp expands. Optimized Workforce (Prometheus) adds another
    per-territory bonus."""
    owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
    bonus = 0
    if has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        bonus += WORKER_SURVEILLANCE_INCOME_BONUS
    if has_technology(corp_state, PANOPTICON_GRID_ID):
        bonus += PANOPTICON_GRID_INCOME_BONUS
    if has_technology(corp_state, SHADOW_ECONOMY_ID):
        bonus += SHADOW_ECONOMY_INCOME_BONUS
    if has_technology(corp_state, MARKET_MONOPOLY_ID):
        bonus += MARKET_MONOPOLY_INCOME_BONUS
    elif has_technology(corp_state, OPTIMIZED_WORKFORCE_ID):
        bonus += WORKFORCE_INCOME_BONUS
    if has_technology(corp_state, TITHES_ID):
        bonus += TITHES_INCOME_BONUS
    return sum(TERRITORY_INCOME_BASE + bonus + TERRITORY_INCOME_PER_VALUE * t.value for t in owned)


def owned_research_facilities(corp_state: CorpState, corp_map: CorpMap) -> list[Location]:
    """Every research facility the corp holds, best first — highest research_rate,
    then most assistant seats, then id to break ties deterministically.

    A corp used to be able to hold exactly one (seeded by
    corpmap.add_research_facility; expand_into claims only neutral ground,
    which never carries one). attack_territory broke that: capture_territory
    hands over every Location standing on the district, so taking a rival's home
    bloc takes their labs with it. That is the case DESIGN.md flagged in advance —
    "if corps ever hold more than one facility, revisit collect_research's fill
    order" — and this ordering is that fill order restored.
    """
    return sorted(
        (
            location
            for territory in corp_map.territories.values()
            if territory.owner == corp_state.faction_id
            for location in territory.locations
            if location.kind == LocationKind.RESEARCH_FACILITY
        ),
        key=lambda f: (-research_rate(corp_state, f), -assistant_capacity(f, corp_state), f.id),
    )


def owned_research_facility(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    """The corp's *primary* research facility — the best one it holds, or None.

    This is where build_lab/build_efficiency_upgrade land, and it's a principled
    choice rather than an arbitrary one: collect_research fills scientists into
    this same facility first, so concentrating both capacity and efficiency on it
    is exactly what maximizes output. Upgrading anything else would be seating
    scientists at a worse rate while the best facility sat half empty.
    """
    facilities = owned_research_facilities(corp_state, corp_map)
    return facilities[0] if facilities else None


def lab_capacity(facility: Location, corp_state: CorpState | None = None) -> int:
    """How many scientists this facility can put to work: a free base seat plus
    one more per lab built there, plus one additional when Research Expansion is
    researched."""
    bonus = RESEARCH_EXPANSION_BONUS if corp_state is not None and has_technology(corp_state, RESEARCH_EXPANSION_ID) else 0
    return BASE_LAB_CAPACITY + (facility.labs_built or 0) + bonus


def next_lab_cost(facility: Location) -> int | None:
    """Cost of this facility's next lab, or None once MAX_LABS_BUILT is reached."""
    labs_built = facility.labs_built or 0
    if labs_built >= MAX_LABS_BUILT:
        return None
    return LAB_UPGRADE_COSTS[labs_built]


def scientist_base_rate(corp_state: CorpState) -> float:
    """RP/day one working scientist adds before any facility efficiency upgrade —
    RESEARCH_PER_SCIENTIST, or the best-researched Brains tier's rate. The chain
    replaces rather than stacks (see the Brains constants above), so this picks
    the highest tier held rather than summing them."""
    if has_technology(corp_state, COGNITIVE_UPLINK_ID):
        return COGNITIVE_UPLINK_RESEARCH_PER_SCIENTIST
    if has_technology(corp_state, BRAINS_3_ID):
        return BRAINS_3_RESEARCH_PER_SCIENTIST
    if has_technology(corp_state, BRAINS_2_ID):
        return BRAINS_2_RESEARCH_PER_SCIENTIST
    return RESEARCH_PER_SCIENTIST


def assistant_rate(corp_state: CorpState) -> float:
    """RP/day one working research assistant adds. Flat regardless of facility —
    efficiency upgrades boost scientists only — but the Brains chain raises it,
    same highest-tier-wins rule as scientist_base_rate."""
    if has_technology(corp_state, COGNITIVE_UPLINK_ID):
        return COGNITIVE_UPLINK_RESEARCH_PER_ASSISTANT
    if has_technology(corp_state, BRAINS_3_ID):
        return BRAINS_3_RESEARCH_PER_ASSISTANT
    if has_technology(corp_state, BRAINS_2_ID):
        return BRAINS_2_RESEARCH_PER_ASSISTANT
    return RESEARCH_PER_ASSISTANT


def research_rate(corp_state: CorpState, facility: Location) -> float:
    """RP/day one working scientist adds at this facility: the base rate (which
    Brains 2 raises) plus any efficiency upgrades built there. Takes corp_state
    because the rate is now a property of the corp's tech as well as the
    building — the two stack."""
    return scientist_base_rate(corp_state) + (facility.efficiency_upgrades or 0)


def next_efficiency_cost(facility: Location) -> int | None:
    """Cost of this facility's next efficiency upgrade, or None once
    MAX_EFFICIENCY_UPGRADES is reached."""
    efficiency_upgrades = facility.efficiency_upgrades or 0
    if efficiency_upgrades >= MAX_EFFICIENCY_UPGRADES:
        return None
    return EFFICIENCY_UPGRADE_COSTS[efficiency_upgrades]


def assistant_capacity(facility: Location, corp_state: CorpState | None = None) -> int:
    """How many research assistants this facility can put to work: each lab
    seats RESEARCH_ASSISTANTS_PER_LAB of them, same lab count as lab_capacity."""
    return RESEARCH_ASSISTANTS_PER_LAB * lab_capacity(facility, corp_state)


def collect_research(corp_state: CorpState, corp_map: CorpMap) -> float:
    """RP/day from the corp's research facility: its tier directly (1 RP at tier
    1), plus research_rate() for each scientist actually working it, plus
    assistant_rate() for each research assistant actually working it. Both
    per-head rates are raised by the Brains 2 technology.

    "Actually working" is the whole mechanic: lab_capacity/assistant_capacity
    cap how many of each count, so employees trained beyond the seats built for
    them produce nothing — headcount (train_employees) and capacity (build_lab)
    are two separate purchases.

    A corp holding more than one facility (only reachable by taking a rival's
    ground — see owned_research_facilities) fills them best-first: every scientist
    sits at the highest-rate facility with a seat free before any of them sits at
    a worse one. Each facility's own research_tier counts whether or not anyone is
    staffing it, exactly as it did in the single-facility case.

    Total Information Awareness (when researched) adds SIGHTING_RESEARCH_BONUS RP
    per sighting currently in the log — each detection feeds the research machine."""
    scientists_left = corp_state.scientists
    assistants_left = corp_state.research_assistants
    total = 0.0
    for facility in owned_research_facilities(corp_state, corp_map):
        total += facility.research_tier or 0
        working = min(scientists_left, lab_capacity(facility, corp_state))
        scientists_left -= working
        total += working * research_rate(corp_state, facility)
        aides = min(assistants_left, assistant_capacity(facility, corp_state))
        assistants_left -= aides
        total += aides * assistant_rate(corp_state)
    if has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        total += len(corp_state.sightings) * SIGHTING_RESEARCH_BONUS
    if has_technology(corp_state, SIGNAL_INTERCEPT_ID):
        total += len(corp_state.sightings) * SIGNAL_INTERCEPT_RP
    return total


def _owned_territories(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Sorted by id, so every list built off this renders in a stable order."""
    return sorted(
        (t for t in corp_map.territories.values() if t.owner == corp_state.faction_id),
        key=lambda t: t.id,
    )


def effective_surveillance_max(corp_state: CorpState) -> int:
    """Highest Surveillance level this corp can reach. Normally MODIFIER_MAX (5);
    Deep Surveillance raises it to EXTENDED_SURVEILLANCE_MAX (6)."""
    if has_technology(corp_state, DEEP_SURVEILLANCE_ID):
        return EXTENDED_SURVEILLANCE_MAX
    return MODIFIER_MAX


def surveillance_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds whose Surveillance isn't already at its effective
    maximum (MODIFIER_MAX, or EXTENDED_SURVEILLANCE_MAX with Deep Surveillance).
    Empty until Worker Surveillance is researched — the tech is what grants the
    ability at all, not just a discount on it."""
    if not has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        return []
    ceiling = effective_surveillance_max(corp_state)
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) < ceiling
    ]


def surveillance_bump_cost(corp_state: CorpState, territory: Territory) -> int:
    """Cost to raise this territory's Surveillance by 1. Counter-Intelligence
    drops the base cost; the extended level (5→6, Deep Surveillance required)
    costs EXTENDED_SURVEILLANCE_COST regardless."""
    level = territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0)
    if level >= MODIFIER_MAX:
        return EXTENDED_SURVEILLANCE_COST
    if has_technology(corp_state, COUNTER_INTELLIGENCE_ID):
        return COUNTERINTEL_SURVEILLANCE_COST
    return SURVEILLANCE_BUMP_COST


def raise_surveillance(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay the level-appropriate Surveillance bump cost to raise one held
    district's Surveillance by 1.

    Repeatable within a day (cash is the only gate), so unlike
    expand_into/train_employees this never touches action_points. Fails
    closed if the tech isn't researched, the district isn't a legal target
    (not held, or already at its effective max), or the corp can't afford it."""
    if territory_id not in {t.id for t in surveillance_targets(corp_state, corp_map)}:
        return False
    territory = corp_map.territories[territory_id]
    cost = surveillance_bump_cost(corp_state, territory)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    territory.modifiers[TerritoryModifier.SURVEILLANCE] = (
        territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) + 1
    )
    return True


def sightings_log_cap(corp_state: CorpState) -> int:
    """How many entries the sightings log holds. Counter-Intelligence doubles it."""
    if has_technology(corp_state, COUNTER_INTELLIGENCE_ID):
        return COUNTERINTEL_SIGHTINGS_LOG
    return MAX_SIGHTINGS_LOG


def investigate_sighting_targets(
    corp_state: CorpState, corp_map: CorpMap,
) -> list[Sighting]:
    """Sightings that are eligible for investigation. Only available after Total
    Information Awareness is researched, and only when action_points are still
    available (investigation costs 1 AP). Each sighting can be investigated
    exactly once — spotted runners don't exist after the event."""
    if corp_state.action_points < AP_COST:
        return []
    if not has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        return []
    if corp_state.cash < INVESTIGATION_COST:
        return []
    return list(corp_state.sightings)


def investigate_sighting(
    corp_state: CorpState, sighting: Sighting, rng: random.Random,
) -> bool:
    """Pay INVESTIGATION_COST, deduct 1 AP, and gather
    actionable intel on a sighted runner. The intel itself is surfaced by the
    caller (CorpScreen) — this function only deducts the cost and marks the
    slot. Fails closed if the tech isn't researched, the day's move is already
    spent, or the corp can't afford it.

    Returns True on success so the caller can display the intel. The intel is
    deterministic (it's just reading fields the sighting already carries), not a
    random roll — the cash + daily-move cost is the gate."""
    if corp_state.action_points < AP_COST:
        return False
    if not has_technology(corp_state, TOTAL_INFORMATION_AWARENESS_ID):
        return False
    if INVESTIGATION_COST > corp_state.cash:
        return False
    corp_state.cash -= INVESTIGATION_COST
    corp_state.action_points -= AP_COST
    # The intel itself — what the caller displays — is just what the sighting
    # already carries (runner id, faction, territory, day, intercepted flag).
    # The cash buys the story beat; nothing is rolled.
    corp_state.sightings = [s for s in corp_state.sightings if s is not sighting]
    return True


def development_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds that are ready to be built up: Development below
    MODIFIER_MAX, and both Security and Surveillance already at their thresholds.
    Needs no technology — a district seeded well enough can be developed from day
    one; Worker Surveillance is simply how a district that *isn't* gets there."""
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.DEVELOPMENT, 0) < MODIFIER_MAX
        and t.modifiers.get(TerritoryModifier.SECURITY, 0) >= DEVELOPMENT_MIN_SECURITY
        and t.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) >= DEVELOPMENT_MIN_SURVEILLANCE
    ]


def raise_development(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay DEVELOPMENT_BUMP_COST to raise one held district's Development by 1,
    once it's policed and watched enough to justify the capital (see
    development_targets). Same cash-gated, repeatable, no-daily-slot shape as
    raise_surveillance; fails closed on an illegal target or short cash.

    This is the first thing in Corp mode with a *runner-side* consequence:
    Development prices lodging and safehouses (corpmap.lodging_cost /
    safehouse_price), so building a block up makes it dearer to sleep in.
    """
    if territory_id not in {t.id for t in development_targets(corp_state, corp_map)}:
        return False
    if DEVELOPMENT_BUMP_COST > corp_state.cash:
        return False
    territory = corp_map.territories[territory_id]
    corp_state.cash -= DEVELOPMENT_BUMP_COST
    territory.modifiers[TerritoryModifier.DEVELOPMENT] = (
        territory.modifiers.get(TerritoryModifier.DEVELOPMENT, 0) + 1
    )
    return True


# --- Free actions: 1 AP, 0eb --------------------------------------------------


def fundraise_amount(corp_state: CorpState, corp_map: CorpMap) -> int:
    """What one round of emergency fundraising would raise: FUNDRAISE_PER_TERRITORY
    per district held (or CONSOLIDATED_FUNDRAISE_PER_TERRITORY when Consolidated
    Holdings is researched). 0 for a corp holding nothing (which is a lost run
    anyway — see corp_defeated)."""
    per_territory = CONSOLIDATED_FUNDRAISE_PER_TERRITORY if has_technology(corp_state, CONSOLIDATED_HOLDINGS_ID) else FUNDRAISE_PER_TERRITORY
    return per_territory * len(_owned_territories(corp_state, corp_map))


def can_fundraise(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Whether fundraising is on the table at all — only while the corp is under
    FUNDRAISE_CASH_CEILING and still holds ground. Separate from `fundraise` so the
    Corp screen can leave the row off entirely for a solvent corp rather than
    showing one that always refuses."""
    return corp_state.cash < FUNDRAISE_CASH_CEILING and fundraise_amount(corp_state, corp_map) > 0


def fundraise(corp_state: CorpState, corp_map: CorpMap) -> int | None:
    """Spend the day's action point to raise fundraise_amount() in cash, costing
    nothing. The way out of a corp that's broke, holding ground, and therefore
    locked out of every cash-gated move on the board.

    Returns the eb raised, or None (nothing mutated, no AP spent) if the corp is
    out of AP or isn't broke enough to qualify."""
    if corp_state.action_points < AP_COST or not can_fundraise(corp_state, corp_map):
        return None
    amount = fundraise_amount(corp_state, corp_map)
    corp_state.cash += amount
    corp_state.action_points -= AP_COST
    return amount


def levy_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds with Development left to strip — the mirror of
    development_targets, which lists the ones with room to build up."""
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.DEVELOPMENT, 0) > 0
    ]


def levy_amount(territory: Territory) -> int:
    """What levying this district would raise: LEVY_PER_VALUE per point of its value."""
    return LEVY_PER_VALUE * territory.value


def levy(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> int | None:
    """Spend the day's action point to strip a point of Development off a held
    district for cash, costing nothing up front. Bigger than fundraising and not
    gated on being broke — it's paid for out of the block itself, and Development
    is what prices runner-side lodging and safehouses (corpmap.lodging_cost /
    safehouse_price), so a levied district gets cheaper to live in.

    Returns the eb raised, or None (nothing mutated, no AP spent) on no AP or a
    district that isn't held or has no Development left to take."""
    if corp_state.action_points < AP_COST:
        return None
    if territory_id not in {t.id for t in levy_targets(corp_state, corp_map)}:
        return None
    territory = corp_map.territories[territory_id]
    amount = levy_amount(territory)
    territory.modifiers[TerritoryModifier.DEVELOPMENT] -= 1
    corp_state.cash += amount
    corp_state.action_points -= AP_COST
    return amount


def survey_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts a survey can read: everything bordering the corp's own ground that
    the corp doesn't hold, neutral and gang turf included. Wider than
    expansion_candidates/attack_candidates (which each list only the ground their
    own move can legally take) because looking at a block commits nothing."""
    if corp_state.action_points < AP_COST:
        return []
    owned = _owned_territories(corp_state, corp_map)
    seen = {
        conn_id
        for territory in owned
        for conn_id in territory.connections
        if corp_map.territories[conn_id].owner != corp_state.faction_id
    }
    return sorted((corp_map.territories[tid] for tid in seen), key=lambda t: t.id)


def survey(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> Territory | None:
    """Spend the day's action point to read a bordering district's garrison,
    Security and value from your own side of the line — gather_intel's job without
    the operative or the 50% roll, and correspondingly thinner (the caller reports
    no locations list). Returns the surveyed Territory for the caller to describe,
    or None on no AP or an illegal target."""
    if territory_id not in {t.id for t in survey_targets(corp_state, corp_map)}:
        return None
    corp_state.action_points -= AP_COST
    return corp_map.territories[territory_id]


def expansion_cost(territory: Territory, corp_state: CorpState | None = None) -> int:
    base = EXPANSION_COST_BASE
    if corp_state is not None and has_technology(corp_state, SUPPLY_CHAIN_ID):
        base = SUPPLY_CHAIN_EXPANSION_BASE
    return base + EXPANSION_COST_PER_VALUE * territory.value


def expand_into(corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random) -> bool:
    """Spend cash to claim a bordering neutral territory. Fails closed (no
    mutation, no charge) if the corp's already made its move today, the target
    isn't a legal candidate for this faction right now, or it can't afford it."""
    if corp_state.action_points < AP_COST:
        return False
    if territory_id not in expansion_candidates(corp_map, corp_state.faction_id):
        return False
    territory = corp_map.territories[territory_id]
    cost = expansion_cost(territory, corp_state)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    claim_territory(territory, corp_state.faction_id, rng)
    corp_state.action_points -= AP_COST
    return True


def defense_strength(territory: Territory, corp_state: CorpState | None = None) -> int:
    """What an attacker has to beat to take this district: the operatives
    stationed there plus its Security modifier.

    When the corp has researched Hardened Garrison, each garrisoned operative
    counts double (garrison × 2 + Security).

    Note the asymmetry with garrison: Security is bought once and keeps defending
    forever, while a garrison is spent by the fight that uses it, so a
    well-policed district is the durable half of a defense and troops are the
    half you have to keep replacing.
    """
    garrison = territory.garrison
    if corp_state is not None and has_technology(corp_state, HARDENED_GARRISON_ID):
        garrison *= HARDENED_GARRISON_MULTIPLIER
    return garrison + territory.modifiers.get(TerritoryModifier.SECURITY, 0)


def deployable_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp could station operatives on: simply everything it holds.
    Separate from _owned_territories only so the Corp screen reads in the same
    shape as surveillance_targets/development_targets."""
    return _owned_territories(corp_state, corp_map)


def deploy_operatives(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, count: int
) -> bool:
    """Station `count` of the corp's untasked operatives on a district it holds,
    moving them from CorpState.operatives onto Territory.garrison. Spends the
    day's action point budget, same slot as expand_into/train_employees — a redeploy
    is a real logistical decision, not a free click.

    One-way on purpose: there is no recall. Operatives committed to holding ground
    are committed, which is what stops a single stack from shuttling around the
    map defending everything in turn.

    Fails closed (no AP consumed, nothing mutated) if the corp has no AP,
    `count` isn't positive, it hasn't got that many operatives spare, or the
    district isn't one it holds.
    """
    if corp_state.action_points < AP_COST or count <= 0 or count > corp_state.operatives:
        return False
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner != corp_state.faction_id:
        return False
    corp_state.operatives -= count
    territory.garrison += count
    corp_state.action_points -= AP_COST
    return True


def return_tasking_operatives(corp_state: CorpState) -> int:
    """Bring operatives back from their task (tail_runner / gather_intel) at the
    start of a new day. Returns them to the pool, clipped to operative_max — any
    above the cap are lost (the corp can only field so many at once). Returns how
    many were actually returned (capped count), so the caller can report it."""
    if corp_state.tasking_operatives <= 0:
        return 0
    returning = corp_state.tasking_operatives
    corp_state.tasking_operatives = 0
    return _add_operatives(corp_state, returning)


def _dispatch_operative(corp_state: CorpState, rng: random.Random) -> bool:
    """Spend one operative from the pool on a tasking action: marks the day's
    move used and rolls the shared 50% success chance. Shared by
    tail_runner/gather_intel/sabotage, which differ only in target validation
    and in whether a failure still returns the operative to tasking_operatives
    (tail_runner/gather_intel always do; sabotage only on success) — that part
    stays with each caller."""
    corp_state.operatives -= 1
    corp_state.action_points -= AP_COST
    return rng.random() < 0.5


# --- Operative tasking: tail a sighted runner ---------------------------------


def tail_runner_targets(corp_state: CorpState) -> list[Sighting]:
    """Sightings of independent runners eligible for a tail. Only while the day's
    move is free, an operative is spare in the pool, and the sighting is of a
    runner (not the player)."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return []
    return [s for s in corp_state.sightings if s.kind == "runner"]


def tail_runner(corp_state: CorpState, sighting: Sighting, rng: random.Random) -> bool:
    """Dispatch one operative to tail a sighted runner. The operative leaves the
    pool immediately (to tasking_operatives), the day's move is spent, and a
    detection-style roll determines intel quality:

    - Success: the caller surfaces the intel (runner faction, last known activity,
      territory of origin). The sighting stays in the log — an investigated
      sighting is worth more RP with Total Information Awareness.
    - Failure: no intel; the operative simply returns next day via
      return_tasking_operatives.

    Returns True on success (a hit), so the caller can display the intel. Fails
    closed if the day's move is spent, no operatives are spare, or no runner
    sightings exist."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return False
    if sighting.kind != "runner":
        return False
    success = _dispatch_operative(corp_state, rng)
    corp_state.tasking_operatives += 1
    return success


# --- Operative tasking: gather intel on a territory ---------------------------


def intel_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Rival or neutral territories an operative can be sent to gather intel on.
    Only while the day's move is free and an operative is spare."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return []
    return [
        t for t in corp_map.territories.values()
        if t.owner != corp_state.faction_id
    ]


def gather_intel(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random,
) -> bool:
    """Dispatch one operative to scout a rival or neutral territory. The operative
    leaves the pool for tasking, the day's move is spent, and a roll determines
    intel quality:

    - Success (50%): the caller surfaces everything visible about the territory
      (owner, garrison count, modifiers, locations/buildings).
    - Failure: no intel; the operative returns next day.

    Returns True on success. Fails closed if the day's move is spent, no
    operatives are spare, or the territory is the corp's own."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return False
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner == corp_state.faction_id:
        return False
    success = _dispatch_operative(corp_state, rng)
    corp_state.tasking_operatives += 1
    return success


# --- Operative tasking: sabotage a rival territory ----------------------------


def sabotage_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Rival-held territories an operative can be sent to sabotage. Only while the
    day's move is free and an operative is spare. Neutral ground and the corp's own
    territory are excluded — sabotage is a hostile act."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return []
    return [
        t for t in corp_map.territories.values()
        if t.owner not in (corp_state.faction_id, "neutral")
    ]


def sabotage(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random,
) -> str | None:
    """Dispatch one operative to sabotage a rival-held territory. The operative
    leaves the pool for tasking (returns next day via return_tasking_operatives),
    the day's move is spent, and a roll determines the outcome:

    - Success (50%): the territory's Security drops by 1 (minimum 0). The
      operative returns alive. Returns the target territory's name for the
      caller to report.
    - Failure: the operative is captured or killed — they do NOT return (lost
      from tasking). Returns None.

    Fails closed (no move consumed, nothing mutated) if the day's move is spent,
    no operatives are spare, or the target isn't a rival-held territory."""
    if corp_state.action_points < AP_COST or corp_state.operatives <= 0:
        return None
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner in (corp_state.faction_id, "neutral"):
        return None
    if _dispatch_operative(corp_state, rng):
        # Success: the operative did the job and reports back.
        territory.modifiers[TerritoryModifier.SECURITY] = max(
            0, territory.modifiers.get(TerritoryModifier.SECURITY, 0) - 1,
        )
        corp_state.tasking_operatives += 1
        return territory.name
    else:
        # Failure: operative lost — no tasking return.
        return None


@dataclass
class AttackResult:
    """What one resolved attack did, for the caller to report. Returned by
    attack_territory (and built by rivals.py for the AI's own attacks) rather than
    notified from inside, the same split security.NightResult keeps: the resolver
    returns data, the screen writes the prose."""

    territory_id: str
    defender_id: str  # the faction that held it going in
    committed: int
    attack_power: int
    defense_power: int
    captured: bool
    attacker_losses: int
    defender_losses: int


def resolve_attack(
    territory: Territory,
    attacker_id: str,
    committed: int,
    rng: random.Random,
    *,
    attack_bonus: int = 0,
    defense_bonus: int = 0,
    defender_corp_state: CorpState | None = None,
) -> AttackResult:
    """The contest itself, taking no corp state for the attacker — so rivals.py's
    AI factions (which have no CorpState) settle an attack through exactly the
    same dice the player does, and a test can drive it without building a corp.

    Both sides roll one CONTEST_DIE on top of their strength; the attacker needs
    to strictly exceed the defender, so a tie holds the ground. Losses land the
    same way either way — the attacker bleeds one operative per point of defense
    they had to grind through (capped at what they brought), the defender loses
    their whole garrison if the district falls and one per attacker if it doesn't.

    attack_bonus is added to the attacker's roll — the player's Shock Assault
    technology feeds it; the AI never does. defense_bonus is added to the
    defender's roll — Fortified Defenses technology feeds it when the defender
    has researched it.

    defender_corp_state feeds defense_strength's Hardened Garrison bonus. It's
    the *defender's* state, not the attacker's — only ever non-None when the
    player is the one holding the territory, since AI factions carry no
    CorpState of their own to research the tech into.

    Mutates `territory` (ownership and garrison) and returns the record.
    """
    defender_id = territory.owner
    defense = defense_strength(territory, defender_corp_state)
    attack_power = committed + rng.randint(1, CONTEST_DIE) + attack_bonus
    defense_power = defense + rng.randint(1, CONTEST_DIE) + defense_bonus
    captured = attack_power > defense_power

    attacker_losses = min(committed, defense)
    survivors = committed - attacker_losses
    if captured:
        defender_losses = territory.garrison
        capture_territory(territory, attacker_id)
        # Whoever walked out of the fight holds the ground they took: survivors
        # become the new garrison rather than going back in the pool. Taking a
        # district and leaving it empty would just invite it straight back.
        territory.garrison = survivors
    else:
        defender_losses = min(territory.garrison, committed)
        territory.garrison -= defender_losses
    return AttackResult(
        territory_id=territory.id,
        defender_id=defender_id,
        committed=committed,
        attack_power=attack_power,
        defense_power=defense_power,
        captured=captured,
        attacker_losses=attacker_losses,
        defender_losses=defender_losses,
    )


def attack_territory(
    corp_state: CorpState, corp_map: CorpMap, territory_id: str, committed: int, rng: random.Random
) -> AttackResult | None:
    """Throw `committed` operatives at a rival-held district bordering your own
    ground. Costs 1 AP, same slot as expand_into.

    Costs no cash — operatives *are* the cost, and they were paid for at the
    Academy.

    Returns None (no AP consumed, nothing mutated) if the corp has no AP,
    the target isn't a legal attack candidate right now, or it can't field
    that many operatives. On a repel the survivors come home to the pool; on a
    capture they stay as the new garrison (see resolve_attack).
    """
    if corp_state.action_points < AP_COST:
        return None
    if committed < MIN_ATTACK_FORCE or committed > corp_state.operatives:
        return None
    if territory_id not in attack_candidates(corp_map, corp_state.faction_id):
        return None
    territory = corp_map.territories[territory_id]
    corp_state.operatives -= committed
    bonus = 0
    if has_technology(corp_state, TOTAL_WAR_ID):
        bonus = TOTAL_WAR_BONUS
    elif has_technology(corp_state, SHOCK_ASSAULT_ID):
        bonus = SHOCK_ASSAULT_BONUS
    elif has_technology(corp_state, CRUSADE_ID):
        bonus = CRUSADE_BONUS
    result = resolve_attack(territory, corp_state.faction_id, committed, rng, attack_bonus=bonus)
    if not result.captured:
        corp_state.operatives += committed - result.attacker_losses
    corp_state.action_points -= AP_COST
    return result


def corp_defeated(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Whether the corp has been broken up: it holds no territory at all.

    The one loss condition for Corp mode, checked on the day tick (app.py) after
    the rivals have had their turn. Territory is the right thing to key on rather
    than cash or headcount — every other corp system (income, research, training,
    attacking) is downstream of holding ground, so a corp with none of it has no
    move left to make.
    """
    return not any(t.owner == corp_state.faction_id for t in corp_map.territories.values())


def owned_academy(corp_state: CorpState, corp_map: CorpMap) -> Location | None:
    for territory in corp_map.territories.values():
        if territory.owner != corp_state.faction_id:
            continue
        for location in territory.locations:
            if location.kind == LocationKind.ACADEMY:
                return location
    return None


def train_employees(
    corp_state: CorpState, corp_map: CorpMap, category: EmployeeCategory, day: int
) -> bool:
    """Start one training batch at the corp's Academy: charge cash now and queue
    that many scientists, operatives or research assistants (whichever `category`
    picks, Academy-tier many) to land TRAINING_DAYS[category] days later, when
    advance_training completes it. Shares expand_into's once-a-day slot and the
    Academy's single training slot — fails closed if the corp's already made its
    move today, a batch is already training, holds no Academy (a rival can capture
    the one it was seeded — see build_academy), can't afford it, or (for operatives)
    the pool is already at or above the cap."""
    if corp_state.action_points < AP_COST or corp_state.pending_recruit is not None:
        return False
    academy = owned_academy(corp_state, corp_map)
    if academy is None:
        return False
    count = academy.academy_tier or 0
    if category is EmployeeCategory.OPERATIVE and corp_state.operatives + count > operative_max(corp_state):
        return False
    cost = (
        operative_training_cost(corp_state)
        if category is EmployeeCategory.OPERATIVE
        else ACADEMY_TRAINING_COST[category]
    )
    days = (
        operative_training_days(corp_state)
        if category is EmployeeCategory.OPERATIVE
        else TRAINING_DAYS[category]
    )
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    corp_state.pending_recruit = PendingRecruit(
        category=category,
        count=count,
        ready_day=day + days,
    )
    corp_state.action_points -= AP_COST
    return True


def advance_training(corp_state: CorpState, day: int) -> PendingRecruit | None:
    """Complete the Academy's training batch if `day` has reached its ready_day:
    add the trained hires to the matching pool, clear the slot, and return the
    finished batch for the caller to announce. Returns None while a batch is
    still training or the Academy is idle. Called once per day tick."""
    recruit = corp_state.pending_recruit
    if recruit is None or day < recruit.ready_day:
        return None
    if recruit.category is EmployeeCategory.SCIENTIST:
        corp_state.scientists += recruit.count
    elif recruit.category is EmployeeCategory.OPERATIVE:
        # Snapped to the cap rather than routed through _add_operatives' room-based
        # clip: unlike a tasking operative returning, the pool here may already sit
        # above operative_max (e.g. the cap dropping after the batch was queued), and
        # this is the point that resyncs it back down rather than adding on top.
        # The recruit's own count is trimmed to match what actually landed, so the
        # caller (app.py's completion toast) doesn't overstate the batch.
        before = corp_state.operatives
        corp_state.operatives = min(before + recruit.count, operative_max(corp_state))
        recruit.count = max(corp_state.operatives - before, 0)
    else:
        corp_state.research_assistants += recruit.count
    corp_state.pending_recruit = None
    return recruit


def next_academy_upgrade_cost(academy: Location) -> int | None:
    tier = academy.academy_tier or 0
    idx = tier - STARTING_ACADEMY_TIER
    if idx < 0 or idx >= len(ACADEMY_UPGRADE_COSTS):
        return None
    return ACADEMY_UPGRADE_COSTS[idx]


def upgrade_academy(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash and 1 AP to raise the academy's tier by one, training one more
    employee per batch. Fails closed if out of AP, no academy, already maxed, or
    can't afford the next upgrade."""
    if corp_state.action_points < AP_COST:
        return False
    academy = owned_academy(corp_state, corp_map)
    if academy is None:
        return False
    cost = next_academy_upgrade_cost(academy)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    academy.academy_tier = (academy.academy_tier or STARTING_ACADEMY_TIER) + 1
    corp_state.action_points -= AP_COST
    return True


def rebuild_academy_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp could stand a new academy on — empty while it still holds
    one anywhere, exactly like rebuild_facility_targets.

    An academy is the harsher of the two to lose. A corp with no research facility
    stops advancing; a corp with no academy stops producing operatives, and since
    operatives are the only way to attack *or* garrison, it has no counterplay left
    against the loss condition at all — just a slow slide. That's why this exists.
    """
    if owned_academy(corp_state, corp_map) is not None:
        return []
    return _owned_territories(corp_state, corp_map)


def build_academy(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Spend ACADEMY_REBUILD_COST to stand a new academy up on a held district, after
    a rival took the last one. Shares expand_into's daily slot.

    Fails closed if the corp has already moved today, still holds an academy, can't
    afford it, or names a district it doesn't hold.
    """
    if corp_state.action_points < AP_COST:
        return False
    if territory_id not in {t.id for t in rebuild_academy_targets(corp_state, corp_map)}:
        return False
    if ACADEMY_REBUILD_COST > corp_state.cash:
        return False
    corp_state.cash -= ACADEMY_REBUILD_COST
    add_academy(corp_map.territories[territory_id])
    corp_state.action_points -= AP_COST
    return True


def build_lab(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next lab, raising its
    scientist capacity by one. Shares expand_into/train_employees' daily slot;
    fails closed if the corp's already made its move today, holds no Research
    Facility, has already built out to MAX_LABS_BUILT, or can't afford it."""
    if corp_state.action_points < AP_COST:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_lab_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.labs_built = (facility.labs_built or 0) + 1
    corp_state.action_points -= AP_COST
    return True


def rebuild_facility_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp could stand a new research facility up on — **empty while
    it still holds one anywhere**.

    So this is a rebuild, not a second facility: capturing a rival's labs is the
    only way to run two (see owned_research_facilities), and a corp that has been
    stripped of its own isn't locked out of research for the rest of the run. It's
    also what keeps add_research_facility's id unique — a corp with a facility is
    never offered another.
    """
    if owned_research_facilities(corp_state, corp_map):
        return []
    return _owned_territories(corp_state, corp_map)


def build_research_facility(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Spend RESEARCH_FACILITY_REBUILD_COST to stand a new research facility up on a
    held district, after a rival took the last one. Shares expand_into's daily slot.

    The new facility starts bare — STARTING_RESEARCH_TIER, no labs, no efficiency
    upgrades — so whatever was built into the captured one is genuinely lost and has
    to be paid for again. *Where* is a real choice: a facility is captured with the
    district under it, so rebuilding on the border invites the same loss twice.

    Fails closed if the corp has already moved today, still holds a facility, can't
    afford it, or names a district it doesn't hold.
    """
    if corp_state.action_points < AP_COST:
        return False
    if territory_id not in {t.id for t in rebuild_facility_targets(corp_state, corp_map)}:
        return False
    if RESEARCH_FACILITY_REBUILD_COST > corp_state.cash:
        return False
    corp_state.cash -= RESEARCH_FACILITY_REBUILD_COST
    add_research_facility(corp_map.territories[territory_id])
    corp_state.action_points -= AP_COST
    return True


def build_efficiency_upgrade(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next efficiency upgrade,
    raising research_rate there by one. Shares expand_into/train_employees'
    daily slot; fails closed if the corp's already made its move today, holds
    no Research Facility, has already built out to MAX_EFFICIENCY_UPGRADES, or
    can't afford it."""
    if corp_state.action_points < AP_COST:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_efficiency_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.efficiency_upgrades = (facility.efficiency_upgrades or 0) + 1
    corp_state.action_points -= AP_COST
    return True
