"""The corp research tree: every Technology, what it costs in research points,
what it needs researched first, and the constants its effect is worth.

Split out of corp_turn.py, which was 2153 lines with this catalog making up a
third of them. The rule that made the split worth doing is the same one the
Technology docstring below states: **a tech's effect is not a field here**, it is
read wherever it applies, keyed off the id. So this module is a table and the
constants that table quotes, and the code reading them is somewhere else --
corp_turn.collect_income for the income chains, corp_turn.scientist_base_rate
for the brains chain, surveillance.py for the detection ones, rivals.py for
FORTIFIED_DEFENSES_BONUS.

Imports corp_rules.py for the base rates the descriptions compare against, and
corpmap for MODIFIER_MAX. Never imports corp_turn -- corp_turn imports this.
"""

from dataclasses import dataclass

from shadowguy.corpmap import MODIFIER_MAX
from shadowguy.corp_rules import (
    ACADEMY_TRAINING_COST,
    BASE_LAB_CAPACITY,
    EXPANSION_COST_BASE,
    LOGISTICS_BASE_CAPACITY,
    LOGISTICS_DEVELOPMENT_PER_SLOT,
    MAX_SIGHTINGS_LOG,
    RESEARCH_PER_ASSISTANT,
    RESEARCH_PER_SCIENTIST,
    TRAINING_DAYS,
    EmployeeCategory,
)

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
LOGISTICS_NETWORK_ID = "logistics_network"
PRIVATE_SECURITY_ID = "private_security"
RAPID_RESPONSE_ID = "rapid_response"
MARTIAL_LAW_ID = "martial_law"
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
    # --- 7 public techs (all-faction, no faction gate) ------------------------
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
    (
        PRIVATE_SECURITY_ID,
        "Private Security Force",
        15,
        (),
        "You can pay {security_cost}eb to raise Security by 1 in any district you "
        f"hold that isn't already at {MODIFIER_MAX} — the only way Security ever "
        "goes back *up* after a district is seeded, and what gets a block past "
        "the Development threshold.",
        None,
    ),
    (
        RAPID_RESPONSE_ID,
        "Rapid Response Teams",
        25,
        (PRIVATE_SECURITY_ID,),
        "Raising Security costs {rapid_response_security_cost}eb instead of "
        "{security_cost}eb.",
        None,
    ),
    (
        MARTIAL_LAW_ID,
        "Martial Law",
        40,
        (RAPID_RESPONSE_ID,),
        "You can raise Security one level beyond the normal cap (to "
        "{extended_security_max}), costing {extended_security_cost}eb for that "
        "final level.",
        None,
    ),
    (
        LOGISTICS_NETWORK_ID,
        "Logistics Network",
        20,
        (),
        "Your supply lines support {logistics_network_capacity} more districts "
        "before they strain (base {logistics_base_capacity}, plus 1 per "
        "{logistics_development_per_slot} Development across the ground you hold).",
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
        "{base_expansion}eb → {supply_chain_expansion}eb), and your supply lines "
        "support {supply_chain_capacity} more districts before they strain.",
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
        "Every territory you hold generates +{tithes_income}eb/day — "
        "donations wired directly from the faithful, no questions asked.",
        "faction_sanctuary",
    ),
    (
        CRUSADE_ID,
        "Crusade",
        45,
        (TITHES_ID,),
        "Your attack rolls get +1 on the contest die — the faithful march where "
        "the algorithm points.",
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
# cash-free ones, and operatives themselves are bought at the Academy. The two
# below cost 1 AP and 0eb.

# Emergency fundraising: eb per district held, offered only while the corp's cash
# is under FUNDRAISE_CASH_CEILING. The ceiling is what keeps this an emergency
# valve instead of a second income stream — a solvent corp can't call it at all,
# so it never competes with collect_income as a way to make money.
FUNDRAISE_PER_TERRITORY = 25
# Its own dial rather than STARTING_CASH, which it used to track: the two answer
# different questions (how much a corp opens with, versus how broke counts as an
# emergency), and tying them meant every starting-cash retune silently moved the
# valve too — raising the opening purse 1500 -> 2000 also let a corp fundraise
# 500eb further up than before. This is the value it had at that STARTING_CASH.
FUNDRAISE_CASH_CEILING = 1500

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

# --- Four more public techs: Consolidated Holdings, Fortified Defenses, Research
# Expansion, Logistics Network.
# Fundraising yield per territory when Consolidated Holdings is researched.
CONSOLIDATED_FUNDRAISE_PER_TERRITORY = 35
# Bonus on the defense contest die when Fortified Defenses is researched.
FORTIFIED_DEFENSES_BONUS = 1
# Extra scientist capacity per lab when Research Expansion is researched.
RESEARCH_EXPANSION_BONUS = 1
# --- Private Security Force / Rapid Response Teams / Martial Law --------------
# Security used to be write-once upward: _corp_modifiers seeded it and only
# sabotage and a runner's completed job (jobs.JOB_SECURITY_HIT) ever moved it,
# both downward — which left DEVELOPMENT_MIN_SECURITY as a wall a district either
# cleared at generation or never cleared at all. This chain is the lever,
# and it's public rather than faction-gated because Development (and so logistics
# capacity) hangs off it — every corp needs a route to building its ground up,
# not just Ironclad.
#
# Priced above SURVEILLANCE_BUMP_COST because Security pays twice: it's half of
# defense_strength *and* the gate on raise_development. Same repeatable,
# cash-only, no-AP shape as the Surveillance bump.
SECURITY_BUMP_COST = 500
# Discounted bump with Rapid Response Teams — the Counter-Intelligence of this
# chain, a price break on an ability Private Security Force already granted.
RAPID_RESPONSE_SECURITY_COST = 300
# Martial Law's level beyond MODIFIER_MAX, and what that final bump costs.
EXTENDED_SECURITY_MAX = 6
EXTENDED_SECURITY_COST = 900

# Districts of logistics capacity Logistics Network adds — the all-faction route
# to holding wide, worth ~40 Development points on the ground (see
# LOGISTICS_DEVELOPMENT_PER_SLOT) for 20 RP.
LOGISTICS_NETWORK_CAPACITY = 10

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
# Districts of logistics capacity Supply Chain adds on top of its discount. The
# expansion discount and the capacity are the same idea priced twice — cheaper to
# take ground, cheaper to keep it — which is what makes Prometheus the wide corp.
SUPPLY_CHAIN_LOGISTICS_CAPACITY = 5

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
# Priced against Optimized Workforce, the one directly comparable row: the other
# faction income root, 20 RP for +5eb. Tithes sits a little above it (income is
# Sanctuary's whole identity, and its chain is two deep where the others run
# three) without being the 3x outlier it opened at — +15eb for 25 RP made a
# day-one root worth more than Prometheus's entire 3-tier, 95 RP chain ending in
# Market Monopoly's +20.
TITHES_INCOME_BONUS = 8
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
    security_cost=SECURITY_BUMP_COST,
    rapid_response_security_cost=RAPID_RESPONSE_SECURITY_COST,
    extended_security_max=EXTENDED_SECURITY_MAX,
    extended_security_cost=EXTENDED_SECURITY_COST,
    logistics_network_capacity=LOGISTICS_NETWORK_CAPACITY,
    logistics_base_capacity=LOGISTICS_BASE_CAPACITY,
    logistics_development_per_slot=LOGISTICS_DEVELOPMENT_PER_SLOT,
    supply_chain_capacity=SUPPLY_CHAIN_LOGISTICS_CAPACITY,
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
