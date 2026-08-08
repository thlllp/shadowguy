"""The player's own Corp turn: a parallel resolution module, like rivals.py/
security.py — not a Scene.

The player runs one of the 4 seeded Factions (CorpState.faction_id) rather than
founding a new one. Two ways in, both building a CorpState: a corp-only run picks
one at New Game (screens/menu_screens.py's CorpSelectScreen), and a runner earns
one mid-run by buying a controlling stake at that corp's own HQ
(screens/shop_screens.py's CorpHQScreen, gated on rep + standing +
factions.TAKEOVER_COST).

Corp mode shares the runner's own day clock rather than keeping a separate
calendar: ShadowguyApp's day tick (app._apply_day_tick) collects each day's
territory income into CorpState.cash and resets daily_action_used, right
alongside the AI factions' own resolve_rival_day (which skips the player's
faction_id once this is set).

**A corp turn has two independent budgets**, which is the one thing worth knowing
before reading any function here:

- **The day's one directed move**, gated on CorpState.daily_action_used (the same
  "_used_today flag reset each day" idiom Character.on_new_day() uses for
  health_kit_used_today). Mutually exclusive, and every one of them documents the
  slot it shares: expand_into, attack_territory, deploy_operatives,
  train_employees, build_lab, build_efficiency_upgrade, build_research_facility,
  build_academy.
- **Whatever cash/RP has piled up.** research_technology and the two territory
  bumps (raise_surveillance, raise_development) deliberately do NOT touch
  daily_action_used — RP and cash are their own pacing gates, and double-gating
  them behind the directed move would make researching compete with expanding for
  no design reason.

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
STARTING_CASH = 500

TERRITORY_INCOME_BASE = 10
TERRITORY_INCOME_PER_VALUE = 15

# Mirrors corpmap.safehouse_price's base + per-value shape: a richer neutral
# territory costs more to move into.
EXPANSION_COST_BASE = 150
EXPANSION_COST_PER_VALUE = 100

# ACADEMY_TRAINING_COST is defined further down, once EmployeeCategory/TRAINING_DAYS
# exist to key it by.

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

# The academy's equivalent (build_academy). Deliberately cheaper than a research
# facility's rebuild even though an academy is the worse loss: nothing has ever raised
# academy_tier, so there are no upgrade tracks inside one, and this single payment
# restores the *whole* building. The facility's 3000 only buys back a bare shell — its
# labs and efficiency upgrades cost another 17,000 on top. Pricing them the same would
# make the academy the strictly worse deal for restoring strictly more. Not
# balance-simulated.
ACADEMY_REBUILD_COST = 2000

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


WORKER_SURVEILLANCE_ID = "worker_surveillance"
PANOPTICON_GRID_ID = "panopticon_grid"
SHADOW_ECONOMY_ID = "shadow_economy"
BRAINS_2_ID = "brains_2"
BRAINS_3_ID = "brains_3"
COGNITIVE_UPLINK_ID = "cognitive_uplink"

# id, name, cost (RP), prereqs, description — two independent chains (income via
# surveillance, research rate via "brains"), each 3 deep. Worker Surveillance and
# Brains 2 are the two roots (empty prereqs, researchable from day one); every
# other row names the one tech directly below it in its own chain. A row's
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
    ),
    (
        PANOPTICON_GRID_ID,
        "Panopticon Grid",
        20,
        (WORKER_SURVEILLANCE_ID,),
        "Every territory you hold earns another +{panopticon_income}/day on top "
        "of Worker Surveillance's bonus.",
    ),
    (
        SHADOW_ECONOMY_ID,
        "Shadow Economy",
        35,
        (PANOPTICON_GRID_ID,),
        "Every territory you hold earns another +{shadow_income}/day on top of "
        "Worker Surveillance and Panopticon Grid's bonuses.",
    ),
    (
        BRAINS_2_ID,
        "Brains 2",
        10,
        (),
        "Every working scientist produces {scientist2}rp/day instead of "
        "{base_scientist}, and every working research assistant {assistant2}rp/day "
        "instead of {base_assistant}.",
    ),
    (
        BRAINS_3_ID,
        "Brains 3",
        20,
        (BRAINS_2_ID,),
        "Every working scientist produces {scientist3}rp/day and every working "
        "research assistant {assistant3}rp/day, replacing Brains 2's rates.",
    ),
    (
        COGNITIVE_UPLINK_ID,
        "Cognitive Uplink",
        35,
        (BRAINS_3_ID,),
        "Every working scientist produces {scientist4}rp/day and every working "
        "research assistant {assistant4}rp/day, replacing Brains 3's rates.",
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
# Cash per Surveillance bump. Deliberately NOT on the daily_action_used slot —
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
    )
    for tech_id, name, cost, prereqs, description in _TECHNOLOGY_ROWS
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


def employee_plural(category: EmployeeCategory) -> str:
    """research_assistant -> "research assistants"; scientist/operative have no
    underscore to begin with, so this just adds the s."""
    return f"{category.replace('_', ' ')}s"


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
    scientists/operatives/research_assistants on hand, and whether they've
    already spent today's one move (expand_into or train_employees — see
    module docstring)."""

    faction_id: str
    cash: int = STARTING_CASH
    research_points: float = 0
    scientists: int = 0
    operatives: int = 0
    research_assistants: int = 0
    daily_action_used: bool = False
    # A training batch in progress at the Academy, or None when idle. The Academy
    # has a single training slot, so train_employees won't start a second batch
    # while this is set; advance_training clears it once its ready_day arrives.
    pending_recruit: PendingRecruit | None = None
    # Technology ids (TECHNOLOGIES_BY_ID) already researched. A set of ids, the
    # same shape Character.owned_programs/discovered_fixers use. Research is
    # permanent — nothing takes a tech back.
    researched: set[str] = field(default_factory=set)
    # Surveillance sightings logged against this corp's own territory,
    # most-recent-first, capped by surveillance.MAX_SIGHTINGS_LOG. Stays empty
    # until surveillance.resolve_surveillance_day actually catches someone —
    # corp_turn.py never appends to this itself.
    sightings: list[Sighting] = field(default_factory=list)


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
    researched yet, or the corp can't afford it.

    Deliberately NOT on the daily_action_used slot: RP is its own pacing gate
    (10 RP is ~10 days of research at the base rate), and double-gating a
    purchase behind the day's one *directed move* would make researching compete
    with expanding for no design reason. Same call the cash-gated territory
    bumps below make.
    """
    technology = TECHNOLOGIES_BY_ID[technology_id]
    if has_technology(corp_state, technology_id) or not prereqs_met(corp_state, technology):
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
    paying as the corp expands."""
    owned = [t for t in corp_map.territories.values() if t.owner == corp_state.faction_id]
    bonus = 0
    if has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        bonus += WORKER_SURVEILLANCE_INCOME_BONUS
    if has_technology(corp_state, PANOPTICON_GRID_ID):
        bonus += PANOPTICON_GRID_INCOME_BONUS
    if has_technology(corp_state, SHADOW_ECONOMY_ID):
        bonus += SHADOW_ECONOMY_INCOME_BONUS
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
        key=lambda f: (-research_rate(corp_state, f), -assistant_capacity(f), f.id),
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


def lab_capacity(facility: Location) -> int:
    """How many scientists this facility can put to work: a free base seat plus
    one more per lab built there."""
    return BASE_LAB_CAPACITY + (facility.labs_built or 0)


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


def assistant_capacity(facility: Location) -> int:
    """How many research assistants this facility can put to work: each lab
    seats RESEARCH_ASSISTANTS_PER_LAB of them, same lab count as lab_capacity."""
    return RESEARCH_ASSISTANTS_PER_LAB * lab_capacity(facility)


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
    """
    scientists_left = corp_state.scientists
    assistants_left = corp_state.research_assistants
    total = 0.0
    for facility in owned_research_facilities(corp_state, corp_map):
        total += facility.research_tier or 0
        working = min(scientists_left, lab_capacity(facility))
        scientists_left -= working
        total += working * research_rate(corp_state, facility)
        aides = min(assistants_left, assistant_capacity(facility))
        assistants_left -= aides
        total += aides * assistant_rate(corp_state)
    return total


def _owned_territories(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Sorted by id, so every list built off this renders in a stable order."""
    return sorted(
        (t for t in corp_map.territories.values() if t.owner == corp_state.faction_id),
        key=lambda t: t.id,
    )


def surveillance_targets(corp_state: CorpState, corp_map: CorpMap) -> list[Territory]:
    """Districts the corp holds whose Surveillance isn't already at MODIFIER_MAX.
    Empty until Worker Surveillance is researched — the tech is what grants the
    ability at all, not just a discount on it."""
    if not has_technology(corp_state, WORKER_SURVEILLANCE_ID):
        return []
    return [
        t
        for t in _owned_territories(corp_state, corp_map)
        if t.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) < MODIFIER_MAX
    ]


def raise_surveillance(corp_state: CorpState, corp_map: CorpMap, territory_id: str) -> bool:
    """Pay SURVEILLANCE_BUMP_COST to raise one held district's Surveillance by 1.

    Repeatable within a day (cash is the only gate — see SURVEILLANCE_BUMP_COST),
    so unlike expand_into/train_employees this never touches daily_action_used.
    Fails closed if the tech isn't researched, the district isn't a legal target
    (not held, or already at MODIFIER_MAX), or the corp can't afford it.

    Deliberately does NOT re-derive TerritoryModifier.DEVELOPMENT, though
    corpmap._development() reads Surveillance: Development is raised as its own
    purchase here (raise_development), gated on Security and Surveillance rather
    than recomputed from them. So a district can sit at high Surveillance and low
    Development — that's the gap raise_development exists to let the player close,
    not an inconsistency to auto-correct.
    """
    if territory_id not in {t.id for t in surveillance_targets(corp_state, corp_map)}:
        return False
    if SURVEILLANCE_BUMP_COST > corp_state.cash:
        return False
    territory = corp_map.territories[territory_id]
    corp_state.cash -= SURVEILLANCE_BUMP_COST
    territory.modifiers[TerritoryModifier.SURVEILLANCE] = (
        territory.modifiers.get(TerritoryModifier.SURVEILLANCE, 0) + 1
    )
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


def expansion_cost(territory: Territory) -> int:
    return EXPANSION_COST_BASE + EXPANSION_COST_PER_VALUE * territory.value


def expand_into(corp_state: CorpState, corp_map: CorpMap, territory_id: str, rng: random.Random) -> bool:
    """Spend cash to claim a bordering neutral territory. Fails closed (no
    mutation, no charge) if the corp's already made its move today, the target
    isn't a legal candidate for this faction right now, or it can't afford it."""
    if corp_state.daily_action_used:
        return False
    if territory_id not in expansion_candidates(corp_map, corp_state.faction_id):
        return False
    territory = corp_map.territories[territory_id]
    cost = expansion_cost(territory)
    if cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    claim_territory(territory, corp_state.faction_id, rng)
    corp_state.daily_action_used = True
    return True


def defense_strength(territory: Territory) -> int:
    """What an attacker has to beat to take this district: the operatives
    stationed there plus its Security modifier.

    Note the asymmetry with garrison: Security is bought once and keeps defending
    forever, while a garrison is spent by the fight that uses it, so a
    well-policed district is the durable half of a defense and troops are the
    half you have to keep replacing.
    """
    return territory.garrison + territory.modifiers.get(TerritoryModifier.SECURITY, 0)


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
    day's one directed move, same slot as expand_into/train_employees — a redeploy
    is a real logistical decision, not a free click.

    One-way on purpose: there is no recall. Operatives committed to holding ground
    are committed, which is what stops a single stack from shuttling around the
    map defending everything in turn.

    Fails closed (no move consumed, nothing mutated) if the corp has already acted
    today, `count` isn't positive, it hasn't got that many operatives spare, or the
    district isn't one it holds.
    """
    if corp_state.daily_action_used or count <= 0 or count > corp_state.operatives:
        return False
    territory = corp_map.territories.get(territory_id)
    if territory is None or territory.owner != corp_state.faction_id:
        return False
    corp_state.operatives -= count
    territory.garrison += count
    corp_state.daily_action_used = True
    return True


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
    territory: Territory, attacker_id: str, committed: int, rng: random.Random
) -> AttackResult:
    """The contest itself, with no corp state on either side — so rivals.py's AI
    factions (which have no CorpState) settle an attack through exactly the same
    dice the player does, and a test can drive it without building a corp.

    Both sides roll one CONTEST_DIE on top of their strength; the attacker needs
    to strictly exceed the defender, so a tie holds the ground. Losses land the
    same way either way — the attacker bleeds one operative per point of defense
    they had to grind through (capped at what they brought), the defender loses
    their whole garrison if the district falls and one per attacker if it doesn't.

    Mutates `territory` (ownership and garrison) and returns the record.
    """
    defender_id = territory.owner
    defense = defense_strength(territory)
    attack_power = committed + rng.randint(1, CONTEST_DIE)
    defense_power = defense + rng.randint(1, CONTEST_DIE)
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
    ground. Spends the day's one directed move, same slot as expand_into.

    Costs no cash — operatives *are* the cost, and they were paid for at the
    Academy.

    Returns None (no move consumed, nothing mutated) if the corp has already acted
    today, the target isn't a legal attack candidate right now, or it can't field
    that many operatives. On a repel the survivors come home to the pool; on a
    capture they stay as the new garrison (see resolve_attack).
    """
    if corp_state.daily_action_used:
        return None
    if committed < MIN_ATTACK_FORCE or committed > corp_state.operatives:
        return None
    if territory_id not in attack_candidates(corp_map, corp_state.faction_id):
        return None
    territory = corp_map.territories[territory_id]
    corp_state.operatives -= committed
    result = resolve_attack(territory, corp_state.faction_id, committed, rng)
    if not result.captured:
        corp_state.operatives += committed - result.attacker_losses
    corp_state.daily_action_used = True
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
    the one it was seeded — see build_academy), or can't afford it."""
    if corp_state.daily_action_used or corp_state.pending_recruit is not None:
        return False
    academy = owned_academy(corp_state, corp_map)
    if academy is None or ACADEMY_TRAINING_COST[category] > corp_state.cash:
        return False
    corp_state.cash -= ACADEMY_TRAINING_COST[category]
    corp_state.pending_recruit = PendingRecruit(
        category=category,
        count=academy.academy_tier or 0,
        ready_day=day + TRAINING_DAYS[category],
    )
    corp_state.daily_action_used = True
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
        corp_state.operatives += recruit.count
    else:
        corp_state.research_assistants += recruit.count
    corp_state.pending_recruit = None
    return recruit


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
    if corp_state.daily_action_used:
        return False
    if territory_id not in {t.id for t in rebuild_academy_targets(corp_state, corp_map)}:
        return False
    if ACADEMY_REBUILD_COST > corp_state.cash:
        return False
    corp_state.cash -= ACADEMY_REBUILD_COST
    add_academy(corp_map.territories[territory_id])
    corp_state.daily_action_used = True
    return True


def build_lab(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next lab, raising its
    scientist capacity by one. Shares expand_into/train_employees' daily slot;
    fails closed if the corp's already made its move today, holds no Research
    Facility, has already built out to MAX_LABS_BUILT, or can't afford it."""
    if corp_state.daily_action_used:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_lab_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.labs_built = (facility.labs_built or 0) + 1
    corp_state.daily_action_used = True
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
    if corp_state.daily_action_used:
        return False
    if territory_id not in {t.id for t in rebuild_facility_targets(corp_state, corp_map)}:
        return False
    if RESEARCH_FACILITY_REBUILD_COST > corp_state.cash:
        return False
    corp_state.cash -= RESEARCH_FACILITY_REBUILD_COST
    add_research_facility(corp_map.territories[territory_id])
    corp_state.daily_action_used = True
    return True


def build_efficiency_upgrade(corp_state: CorpState, corp_map: CorpMap) -> bool:
    """Spend cash on the corp's Research Facility's next efficiency upgrade,
    raising research_rate there by one. Shares expand_into/train_employees'
    daily slot; fails closed if the corp's already made its move today, holds
    no Research Facility, has already built out to MAX_EFFICIENCY_UPGRADES, or
    can't afford it."""
    if corp_state.daily_action_used:
        return False
    facility = owned_research_facility(corp_state, corp_map)
    if facility is None:
        return False
    cost = next_efficiency_cost(facility)
    if cost is None or cost > corp_state.cash:
        return False
    corp_state.cash -= cost
    facility.efficiency_upgrades = (facility.efficiency_upgrades or 0) + 1
    corp_state.daily_action_used = True
    return True
