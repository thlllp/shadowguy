"""Street runners you can hire onto a job's crew.

A small hand-authored roster, like fixer.FIXER_ROSTER and factions.FACTIONS. These
started as rivals-only (identity, no relationship value); recruiting is the mechanic
that changes that -- you meet them at bars (corpmap.LocationKind.BAR) and pay to bring
them on. `archetype` is their specialist and matches jobs.SPECIALIST_FOR_STAT's values
exactly (Netrunner / Solo / Infiltrator), so a runner slots straight onto the crew role
their archetype fits. `rating` is how good they are at that specialty -- an effective
skill_value the run-time crew effect will roll once that increment lands.

Two ways to engage one (see Character.crew / app.BarScreen), each with its own price:
`daily_cost` is the per-day wage if you keep them on indefinitely (charged every rest),
and `job_cut` is the fraction of a single job's payout they take if you sign them for
just that job. Both are the *listed* terms; the recruiter's Leadership skill bends them
(recruit_wage / recruit_cut), the way standing bends shop prices in shops.py.

`RIVAL_RUNNERS` (Specter/Juncture/Mireille) are the three guaranteed every run, the
same way every FIXER_ROSTER entry is seated every run. `RUNNER_POOL` is a larger bench
of extras `select_active_runners` samples `RANDOM_RUNNER_COUNT` of at game start
(ShadowguyApp._new_run, stored as `app.runners`) -- the run's actual independent-runner
roster is `RIVAL_RUNNERS + those six`, so which extras are in a given run varies, the
way fixer.create_fixers varies *where* the fixed roster sits rather than *who's on it*.
`RUNNERS_BY_ID` still spans the whole universe (guaranteed three plus every pool
candidate), not just what got sampled -- a saved `CrewHire`/`JobOffer.taken_by` id must
resolve by id lookup regardless of whether this run happened to roll that runner in.

**A runner is no longer a constant.** `rating`/`deck_id` used to be authored once and
read forever; a runner who works now earns `experience` and `cash` and spends them
(complete_job -> gain_experience / buy_gear), so what someone is worth is a property of
*this run*, not of the roster table. Two consequences fall out of that and both are
load-bearing:

- `select_active_runners` hands a run **deep copies**. The tables above are templates,
  never a run's live state -- without the copy, a levelled Specter would still be
  levelled in the next run started in the same process, and a pickled save would
  disagree with the module either way.
- **Nothing outside this module should read `RUNNERS_BY_ID` for a live value.** Use
  `live_runner(id, roster)` (ShadowguyApp.runner) instead: the template's `rating` is
  what that runner was authored at, not what they're worth today.
"""

import copy
import random
from dataclasses import dataclass, field

from shadowguy.shops import ITEMS_BY_ID, Item, Slot


@dataclass
class RivalRunner:
    id: str
    name: str
    archetype: str  # specialist: "Netrunner" / "Solo" / "Infiltrator"
    description: str
    rating: int  # effective skill_value at their specialty (for the run-time crew effect)
    daily_cost: int  # per-day wage when kept on indefinitely (charged each rest)
    job_cut: float  # fraction of a single job's payout they take when hired for that job
    # The cyberdeck they own (shops.ITEMS_BY_ID, an item with program_slots), or None for
    # a runner who doesn't have one. **This, not `archetype`, is what makes someone
    # able to work remote support** -- see can_work_support. Gating on the gear rather
    # than the label means a solo who somehow ends up with a deck can work it, and a
    # netrunner who doesn't own one can't, which is the honest reading of both.
    #
    # Traded up by buy_gear, so this is authored *starting* kit rather than a fixture.
    deck_id: str | None = None
    # --- earned in play, never authored (see complete_job) ---------------------
    # Career experience, converted into `rating` a point at a time by gain_experience.
    # Kept as its own running total rather than being consumed, so a runner's sheet
    # says how much work they've done as well as how good it made them.
    experience: int = 0
    # Eb in hand, paid by the jobs they run and spent on GEAR_LADDERS.
    cash: int = 0
    # Item ids (shops.ITEMS_BY_ID) they've bought -- weapons and worn armor, read by
    # combat.crew_stats when they fight beside you. A bought *deck* isn't in here: it
    # replaces `deck_id` above, which is where every support check already looks.
    gear: list[str] = field(default_factory=list)


RIVAL_RUNNERS = [
    RivalRunner(
        id="runner_specter",
        name="Specter",
        archetype="Netrunner",
        description="Ghosts through ICE for whoever pays best, and burns a fixer the moment a better offer shows.",
        rating=8,
        daily_cost=60,
        job_cut=0.25,
        deck_id="zetatech_rig",
    ),
    RivalRunner(
        id="runner_juncture",
        name="Juncture",
        archetype="Solo",
        description="Muscle for hire, and the reason two fixers on the board stopped taking new jobs this month.",
        rating=8,
        daily_cost=55,
        job_cut=0.22,
    ),
    RivalRunner(
        id="runner_mireille",
        name="Mireille",
        archetype="Infiltrator",
        description="Works the same jobs you do, one step ahead or one step behind, never both.",
        rating=7,
        daily_cost=45,
        job_cut=0.18,
    ),
]

# The bench: not guaranteed, but a run's other RANDOM_RUNNER_COUNT independent runners
# are sampled from here. Spans budget/mid/elite tiers (rating 5/6/9) evenly across all
# three archetypes so no matter which six get rolled, a run still sees the same rough
# spread the guaranteed three already give it.
RUNNER_POOL = [
    RivalRunner(
        id="runner_null",
        name="Null",
        archetype="Netrunner",
        description="Cheap because the last three fixers who vouched for him are dead.",
        rating=5,
        daily_cost=30,
        job_cut=0.10,
        deck_id="burner_deck",
    ),
    RivalRunner(
        id="runner_convoy",
        name="Convoy",
        archetype="Solo",
        description="Rents out as backup, never as the plan -- and that's fine by him.",
        rating=5,
        daily_cost=32,
        job_cut=0.11,
    ),
    RivalRunner(
        id="runner_tourmaline",
        name="Tourmaline",
        archetype="Infiltrator",
        description="New in town, still learning which doors don't have alarms.",
        rating=5,
        daily_cost=28,
        job_cut=0.09,
    ),
    RivalRunner(
        id="runner_flatline",
        name="Flatline",
        archetype="Netrunner",
        description="Learned to crack ICE by bricking three decks and surviving the fourth.",
        rating=6,
        daily_cost=40,
        job_cut=0.15,
        deck_id="cracked_cyberdeck",
    ),
    RivalRunner(
        id="runner_riptide",
        name="Riptide",
        archetype="Solo",
        description="Doesn't threaten. Just shows up, and the job stops arguing.",
        rating=6,
        daily_cost=38,
        job_cut=0.14,
    ),
    RivalRunner(
        id="runner_vellum",
        name="Vellum",
        archetype="Infiltrator",
        description="Reads a room's cameras before she reads the room.",
        rating=6,
        daily_cost=36,
        job_cut=0.13,
    ),
    RivalRunner(
        id="runner_dominion",
        name="Dominion",
        archetype="Netrunner",
        description="Corp security still argues about whether he was ever really in their system.",
        rating=9,
        daily_cost=70,
        job_cut=0.28,
        deck_id="zetatech_rig",
    ),
    RivalRunner(
        id="runner_switchback",
        name="Switchback",
        archetype="Solo",
        description="Took a bullet meant for a client once, then billed them for the jacket.",
        rating=9,
        daily_cost=68,
        job_cut=0.27,
    ),
    RivalRunner(
        id="runner_glasswing",
        name="Glasswing",
        archetype="Infiltrator",
        description="Nobody's seen her leave a building; they've only noticed she's gone.",
        rating=9,
        daily_cost=65,
        job_cut=0.26,
    ),
]

# How many of RUNNER_POOL join the guaranteed three each run, picked once at game start.
RANDOM_RUNNER_COUNT = 6
if len(RUNNER_POOL) < RANDOM_RUNNER_COUNT:
    raise ValueError("RUNNER_POOL must hold at least RANDOM_RUNNER_COUNT candidates")

RUNNERS_BY_ID = {runner.id: runner for runner in RIVAL_RUNNERS + RUNNER_POOL}

# A deck_id has to name a real cyberdeck, or the support gate reads as "owns no deck"
# for a runner the roster meant to be a hacker -- a typo that fails silently by making
# somebody quietly unhireable. program_slots > 0 is what *makes* an item a deck (see
# shops.Item), so it's the same check as "is this actually a deck".
for _runner in RUNNERS_BY_ID.values():
    if _runner.deck_id is None:
        continue
    _deck = ITEMS_BY_ID.get(_runner.deck_id)
    if _deck is None or not _deck.program_slots:
        raise ValueError(f"{_runner.id}: deck_id must name a shops item with program_slots")
if len(RUNNERS_BY_ID) != len(RIVAL_RUNNERS) + len(RUNNER_POOL):
    raise ValueError("RivalRunner ids must be unique across RIVAL_RUNNERS and RUNNER_POOL")


def select_active_runners(rng: random.Random) -> list["RivalRunner"]:
    """The independent-runner roster for a run: the guaranteed three plus a random
    RANDOM_RUNNER_COUNT from RUNNER_POOL. Called once at game start
    (ShadowguyApp._new_run) and persisted as `app.runners`, the same "seeded once,
    lives for the run" treatment fixer.create_fixers gives the fixer roster.

    **Copies, not the table's own instances.** A runner levels up and buys gear over a
    run (complete_job), so handing back RIVAL_RUNNERS itself would have one run's
    progress waiting in the roster table for the next one started in the same process.
    """
    return copy.deepcopy(RIVAL_RUNNERS + rng.sample(RUNNER_POOL, RANDOM_RUNNER_COUNT))


def live_runner(runner_id: str, roster: list["RivalRunner"] | None = None) -> "RivalRunner | None":
    """The instance carrying *this run's* rating and gear for `runner_id`.

    `.get`-shaped (None for an id nobody has ever authored) because that's what the
    callers taking a bare id off a CrewHire already relied on.

    Every by-id lookup that reads a value play can move -- rating, deck, gear -- has to
    come through here rather than RUNNERS_BY_ID, which since select_active_runners
    started copying only ever holds the *authored* numbers. `roster` omitted falls back
    to those templates, which is right for a caller that has no run in hand (tests, and
    an id from a save this run's roster doesn't list).
    """
    if roster is not None:
        for runner in roster:
            if runner.id == runner_id:
                return runner
    return RUNNERS_BY_ID.get(runner_id)


# Leadership (a cool skill, skills.py) discounts recruiting terms, one-directionally: a
# runner's listed daily_cost/job_cut is what they charge anyone -- they're looking for work
# too, so a recruiter with no Leadership pays full price, never a markup. Each point of
# skill_value("leadership") above LEADERSHIP_BASE (the lowest a skill_value can be: cool 1 +
# rank 1) shaves LEADERSHIP_TERMS_STEP off both, up to LEADERSHIP_TERMS_CAP -- like
# shops._standing_discount but floored at zero on the penalty side. Leadership only moves
# with gear over a run (no XP), so callers pass a live skill_value rather than locking terms
# in at hire. Takes a plain int, like shops.buy_price(base, standing), to keep this a leaf.
LEADERSHIP_BASE = 2
LEADERSHIP_TERMS_STEP = 0.03
LEADERSHIP_TERMS_CAP = 0.20


def _leadership_discount(leadership: int) -> float:
    earned = max(0, leadership - LEADERSHIP_BASE) * LEADERSHIP_TERMS_STEP
    return min(LEADERSHIP_TERMS_CAP, earned)


def recruit_wage(runner: RivalRunner, leadership: int) -> int:
    """The daily wage to keep `runner` on, discounted by the recruiter's Leadership. At or
    below base it's the listed cost; higher Leadership is cheaper, never below 1eb."""
    return max(1, round(runner.daily_cost * (1 - _leadership_discount(leadership))))


def recruit_cut(runner: RivalRunner, leadership: int) -> float:
    """The fraction of a job's payout `runner` takes, discounted by the recruiter's
    Leadership. At or below base it's the listed cut; higher Leadership shrinks it."""
    return runner.job_cut * (1 - _leadership_discount(leadership))


# What an info-broker fixer (fixer.RUNNER_BROKER_FIXER_IDS) charges to introduce an
# independent runner directly -- a flat multiple of daily_cost so a better runner
# (already pricier to hire) is also pricier to get a number for.
RUNNER_INTRO_COST_MULT = 2


def intro_cost(runner: RivalRunner) -> int:
    return runner.daily_cost * RUNNER_INTRO_COST_MULT


# --- getting better at it: rating, and what the work pays for ---------------
#
# The world's runners are on the same treadmill the player is. Work pays experience and
# eb; experience becomes `rating` (their one competence number -- read as a crew fighter's
# skill by combat.crew_stats, as the pool a remote hacker rolls by support.py, and as the
# gate on which support programs they can run at all), and eb becomes gear.
#
# Deliberately *not* the player's own curve. Character.experience is a pool the player
# chooses where to spend, over 39 skills and 6 stats; a runner has one number and no
# choice to make, so a table would be ceremony. The next point costs `rating *
# RATING_XP_STEP` -- the same escalating shape as Character.next_stat_cost, cheap to read
# and steep enough that an elite runner improves slowly.
#
# First-slice numbers, not balance-simulated. At rivals.RUNNER_JOB_XP a rating-5 runner
# needs four completed jobs for their next point and a rating-9 runner needs eight.
MAX_RATING = 10
RATING_XP_STEP = 12

# What one runner spends their earnings on, in order, per archetype. Hand-authored like
# _CREW_PROFILES rather than derived from the catalog: what a runner *wants* is character,
# not arithmetic over prices, and a shopping AI that reads the whole catalog would spend a
# netrunner's savings on a katana.
#
# Two rules make this a ladder and not a shopping list -- see next_purchase: they buy
# strictly in order, and they *save* for the next thing rather than skipping down to
# something they can already afford. A runner who already owns better (Specter starts on
# the Zetatech Rig) skips that rung instead of trading down.
#
# The standing-2 rows (assault rifle, mono katana) are shops.py's back room, gated on the
# *player's* standing with a specific gunsmith. A runner buying one isn't a loophole:
# min_standing is a stock gate on one shop's counter, and these people have their own
# contacts.
GEAR_LADDERS: dict[str, tuple[str, ...]] = {
    # Decks first and in slot order: the deck is the only gear that changes what a
    # netrunner can *do* (can_work_support, support_programs_for), rather than how well.
    "Netrunner": ("burner_deck", "cracked_cyberdeck", "zetatech_rig", "leather_jacket"),
    "Solo": ("kevlar_vest", "assault_rifle", "hardsuit"),
    "Infiltrator": ("kevlar_vest", "mono_katana", "armored_suit_ii"),
}

for _archetype, _ladder in GEAR_LADDERS.items():
    for _item_id in _ladder:
        if _item_id not in ITEMS_BY_ID:
            raise ValueError(f"{_archetype} gear ladder names an unknown item: {_item_id}")
if {runner.archetype for runner in RUNNERS_BY_ID.values()} - set(GEAR_LADDERS):
    raise ValueError("every roster archetype needs a GEAR_LADDERS entry, or it never spends")


def experience_for_next_rating(runner: RivalRunner) -> int | None:
    """What the next rating point costs this runner, or None once they're at MAX_RATING.
    Same read-the-price-before-you-spend shape as Character.next_rank_cost."""
    if runner.rating >= MAX_RATING:
        return None
    return runner.rating * RATING_XP_STEP


def gain_experience(runner: RivalRunner, amount: int) -> int:
    """Bank `amount` experience and cash in whatever rating points it buys. Returns how
    many points they gained, which is 0 on most jobs and never more than one per call at
    today's numbers (the loop is what makes that a consequence of the constants rather
    than an assumption baked into the code)."""
    runner.experience += amount
    gained = 0
    while (cost := experience_for_next_rating(runner)) is not None and runner.experience >= cost:
        runner.experience -= cost
        runner.rating += 1
        gained += 1
    return gained


def _deck_slots(runner: RivalRunner) -> int:
    deck = support_deck(runner)
    return deck.program_slots if deck else 0


def _wants(runner: RivalRunner, item: Item) -> bool:
    """Whether this rung is still an upgrade. A deck is measured (more program_slots than
    the one they carry) rather than merely owned, so a runner authored with the best deck
    in the catalog can't be walked back down the ladder to a Burner."""
    if item.program_slots:
        return item.program_slots > _deck_slots(runner)
    return item.id not in runner.gear


def next_purchase(runner: RivalRunner) -> Item | None:
    """What this runner is saving for: the first rung of their ladder they don't already
    have covered, or None once they've bought it out.

    Note this is what they *want*, affordable or not -- buy_gear is the one that checks
    the price, and the fact that it doesn't then look further down the list is the whole
    "save up for it" rule.
    """
    for item_id in GEAR_LADDERS.get(runner.archetype, ()):
        item = ITEMS_BY_ID[item_id]
        if _wants(runner, item):
            return item
    return None


def buy_gear(runner: RivalRunner) -> Item | None:
    """Spend a runner's savings on the next thing they want, if they can cover it now.
    Returns what they bought, or None if they're still short (or done shopping).

    Called from two places: a completed job (complete_job, below) and an idle day
    spent on rivals.RunnerActivity.SHOPPING. Either way there's no shop, no location
    and no standing: an independent runner buying their own kit is modelled as the
    purchase, not as a trip into a COMPUTER_STORE.
    """
    item = next_purchase(runner)
    if item is None or item.price > runner.cash:
        return None
    runner.cash -= item.price
    if item.program_slots:
        runner.deck_id = item.id  # traded up: nobody keeps two decks
    else:
        runner.gear.append(item.id)
    return item


def complete_job(runner: RivalRunner, pay: int, experience: int) -> Item | None:
    """One finished job's worth of progress, and the one place a runner grows.

    Called from both jobs a runner can work: their own (rivals._runner_turn, off a
    fixer's board) and yours (scene_screen._take_crew_cut, where `pay` is the cut they
    just took). Buying happens here rather than on its own daily roll because the money
    only ever moves on a completed job -- checking any other day could never find
    anything new to afford.

    Returns whatever they bought with it, for a caller that wants to report it.
    """
    runner.cash += pay
    gain_experience(runner, experience)
    return buy_gear(runner)


def best_weapon_id(runner: RivalRunner) -> str | None:
    """The hardest-hitting weapon a runner has bought, or None if they're still on the
    kit combat._CREW_PROFILES issues them. Ties break on the ladder's own order (max
    keeps the first), so a later, pricier rung has to actually hit harder to displace an
    earlier one."""
    weapons = [
        ITEMS_BY_ID[item_id] for item_id in runner.gear if ITEMS_BY_ID[item_id].slot is Slot.WEAPON
    ]
    if not weapons:
        return None
    return max(weapons, key=lambda item: item.damage).id


def gear_defense(runner: RivalRunner) -> int:
    """The best armor they've bought, as a flat defense number -- combat.Enemy.armor's
    shape, since an enemy (a hire included) wears one armor value rather than an
    inventory. Best rather than summed, the same way inventory.equipped_defense doesn't
    stack two coats."""
    return max((ITEMS_BY_ID[item_id].defense for item_id in runner.gear), default=0)


# --- remote support: what a hire does from the far end of a comm link ---
#
# A support hire is a *role*, not merely a posture. `CrewHire.on_site` says where they
# are; whether "not there" is a job they can actually do comes down to one thing: do
# they own a cyberdeck (RivalRunner.deck_id). A runner with no deck has nothing to work
# the building's system *with*, so hiring them as support is refused
# (Character.hire_for_job) rather than silently paying for nothing.
#
# **Gated on the gear, not on the archetype.** An archetype is a label; a deck is the
# thing that actually does the work. Reading the label would mean a netrunner who owns
# nothing still counts and a solo holding a deck still doesn't, and neither is true.
#
# Riggers are the other half of this and don't exist yet -- when they land they gate the
# same way, on owning drones rather than on being called a rigger.


@dataclass(frozen=True)
class SupportProgram:
    """One thing a remote hacker can be told to do, and how well their software does it.

    Hand-built and not for sale, the same way combat.NPC_WEAPONS is: shops.Program is
    written entirely in matrix-fight terms (ICE damage, sleaze, extract) and has no field
    that could mean "scout that room". These are a different surface, so they get a
    different table rather than four ICE fields pressed into service.

    `min_rating` gates the task on the hire being good enough to run the software at all,
    which is what makes a better netrunner worth paying for beyond the roll itself: a
    rating-4 hire can only probe, a rating-8 hire can burn a guard's chrome.

    `difficulty` is checks.resolve_check's ordinary scale. `trace_on_failure` is what a
    miss costs -- see tactical.TRACE_CAP. Nothing costs trace on a success: getting in
    and out clean is the whole skill.
    """

    id: str
    name: str
    task: str  # tactical.SupportTaskKind's value -- the effect this program grants
    min_rating: int
    difficulty: int
    trace_on_failure: int
    description: str


# Ordered easiest-first, which is also the order the side menu lists them in.
SUPPORT_PROGRAMS = (
    SupportProgram(
        id="probe",
        name="Probe",
        task="scout",
        min_rating=0,
        difficulty=11,
        trace_on_failure=1,
        description="Read the building's own sensors back at it and map a room you haven't seen.",
    ),
    SupportProgram(
        id="wrench",
        name="Wrench",
        task="device",
        min_rating=5,
        difficulty=14,
        trace_on_failure=2,
        description="Talk a lock or a camera into believing you belong on the other side of it.",
    ),
    SupportProgram(
        id="ripper",
        name="Ripper",
        task="cyberware",
        min_rating=7,
        difficulty=17,
        trace_on_failure=3,
        description="Reach through a guard's own chrome and put them on the floor with it.",
    ),
)

SUPPORT_PROGRAMS_BY_ID = {program.id: program for program in SUPPORT_PROGRAMS}

if len(SUPPORT_PROGRAMS_BY_ID) != len(SUPPORT_PROGRAMS):
    raise ValueError("SUPPORT_PROGRAMS has a duplicate program id")


def can_work_support(runner: RivalRunner) -> bool:
    """Whether this runner can fill a support slot at all: do they own a deck. Someone
    with nothing to hack the building with contributes nothing from across the street,
    so the posture is refused rather than sold."""
    return runner.deck_id is not None


def support_deck(runner: RivalRunner):
    """The shops.Item this runner works from, or None if they own no deck."""
    return ITEMS_BY_ID[runner.deck_id] if runner.deck_id else None


def support_programs_for(runner: RivalRunner) -> tuple[SupportProgram, ...]:
    """The software this hire can actually run.

    Two gates, and the deck is both of them plus the skill. `min_rating` says whether
    they're good enough to run a program at all; the deck's `program_slots` says how many
    they can hold at once, cheapest-first -- so a Burner Deck runs one thing however good
    its owner is, and the rig is what lets a top netrunner bring everything.

    Empty for anyone with no deck, so one call answers "can they work support" and "what
    with" together.
    """
    deck = support_deck(runner)
    if deck is None:
        return ()
    affordable = tuple(p for p in SUPPORT_PROGRAMS if runner.rating >= p.min_rating)
    return affordable[: deck.program_slots]
