"""The corp's base numbers: what a district earns and costs, what an expansion
or a training batch is priced at, what a research facility and an academy can be
built up to, and the two dice constants a territory contest settles on.

Split out of corp_turn.py so the research tree (technologies.py) can quote a base
rate in a technology's description — "{scientist2}rp/day instead of
{base_scientist}" — without importing the module that imports *it*. The arrow is
corp_rules -> technologies -> corp_turn, one way at every step.

Rules only, no behaviour and no state: nothing here reads a CorpState or a
CorpMap. The functions that spend these numbers all live in corp_turn.py, and
every name here is re-exported from there, so `from shadowguy.corp_turn import
TERRITORY_UPKEEP` still resolves.
"""

from enum import StrEnum

from shadowguy.corpmap import STARTING_ACADEMY_TIER

# First-slice numbers, not balance-simulated.
STARTING_CASH = 2000

# The most untasked operatives a corp can hold in its pool at once (garrisoned
# operatives don't count against this cap). Enforced by train_employees and
# advance_training. Low on purpose — the first few bodies are a real decision
# between defense, offense, and the new tasking options (tail_runner / gather_intel).
# May be raised by a future technology.
STARTING_OPERATIVE_MAX = 2

AP_COST = 1
# What CorpState.action_points is refilled to on every day boundary
# (corp_turn.advance_corp_day) and starts a run at. Two directed moves a day is the
# whole pacing gate on corp mode — see the two-budgets note in corp_turn's docstring.
DAILY_ACTION_POINTS = 2

TERRITORY_INCOME_BASE = 10
TERRITORY_INCOME_PER_VALUE = 15

# What holding a district costs per day, subtracted from gross income in
# collect_income -- so income is net, and a district can be a bad buy rather than
# free money. corpmap_gen only ever rolls value 1..3, so the three rows a player
# actually sees are 25/40/55 gross and 10/25/40 net: the poorest ground pays back
# an expansion in ~35 days and the richest in ~9, which is what makes *which*
# district to take a real decision instead of a formality.
#
# Net income can go negative for a corp holding a lot of poor ground; that is
# deliberate (fundraise costs only AP, so a broke corp can always dig out).
# Measured in tools/corp_econ_sim.py -- see Corp economy in DESIGN.md.
TERRITORY_UPKEEP = 15

# --- Logistics ---------------------------------------------------------------
# Upkeep prices a district; logistics prices the *supply line* to it. A corp
# supports LOGISTICS_BASE_CAPACITY districts outright, plus one more for every
# LOGISTICS_DEVELOPMENT_PER_SLOT points of Development standing on the ground it
# already holds (plus whatever the logistics technologies add). Districts past
# that capacity are the sprawl, and each one costs more than the one before it:
# the first district over capacity costs LOGISTICS_STRAIN_COST/day, the second
# twice that, the nth n times, so the total strain is triangular in the overage.
#
# The quadratic is the whole point, and it's the same argument
# EXPANSION_SPRAWL_DIVISOR makes about price: income scales linearly with
# districts, so any linear penalty just shifts the intercept and a corp still
# out-earns it by taking more ground. Growing faster than income is what makes
# holding ground you haven't developed an actual loss.
#
# Development is what buys the capacity back, which is what aims this at the
# behaviour it's meant to discourage: corp-held Development lands at roughly the
# district's value (corpmap._development), so value-1 ground contributes ~1 of
# the 4 points it costs to support itself while value-3 ground nearly pays its
# own way, and raise_development (DEVELOPMENT_BUMP_COST) is the lever for the
# rest. Grabbing weak neutral blocks fast is exactly the play this taxes.
LOGISTICS_BASE_CAPACITY = 12
LOGISTICS_DEVELOPMENT_PER_SLOT = 4
LOGISTICS_STRAIN_COST = 3

# Mirrors corpmap.safehouse_price's base + per-value shape: a richer neutral
# territory costs more to move into.
EXPANSION_COST_BASE = 150
EXPANSION_COST_PER_VALUE = 100
# Sprawl: an expansion costs (1 + held / this) times its base price, so the
# twentieth district costs three times what the first did. Without it, income and
# expansion cost both scale linearly with holdings, which means the *rate* a corp
# can buy ground never slows -- the sim had a corp taking 214 of 260 districts by
# day 120 with cash never binding after day 10. The brake has to grow faster than
# income does, and this is the cheapest way to make it. Not a hard cap: a big corp
# still expands, just slowly enough that building is worth the action point.
EXPANSION_SPRAWL_DIVISOR = 10


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
