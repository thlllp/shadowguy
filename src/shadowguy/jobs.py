"""Procedural generation of job Scenes offered by Fixers."""

import random
import uuid
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.character import CORE_STATS
from shadowguy.checks import day_tier, resolve_rng
from shadowguy.combat import ENEMY_TIERS, roll_enemies
from shadowguy.corpmap import GENERATED_KINDS, LOCATION_SKILL, CorpMap, LocationKind, territory_distance
from shadowguy.factions import FACTIONS_BY_ID
from shadowguy.matrix import ICE_TIERS, generate_matrix_network
from shadowguy.scene import (
    BurglaryStage,
    Choice,
    Encounter,
    Entrance,
    MatrixStage,
    Outcome,
    Posture,
    Role,
    Scene,
    SceneKind,
    Stage,
    TacticalStage,
)
from shadowguy.skills import skill_for
from shadowguy.buildings import BuildingKind, generate_building
from shadowguy.tactical import generate_map

TARGETS = [
    "a corp exec's private files",
    "a rival fixer's stash",
    "a defector's biochip",
    "a black-market weapons cache",
]

DIFFICULTY_BASE = (10, 13, 16)
REWARD_BASE = (250, 450, 700)
# Character.experience paid by a completed job, same tier domain as REWARD_BASE and
# scaled by the same success/critical-success multiplier _payout applies to cash —
# jobs are the only activity that pays XP at all (gigs/legwork/security don't, on
# purpose: a job is the thing worth grinding for growth, not a gig's quick cash).
# First-slice numbers, not balance-simulated.
JOB_XP_BASE = (10, 15, 20)

# One tier domain, four tables: a job's difficulty, its cash pay, its XP pay, and who
# turns up to its fights (combat.ENEMY_TIERS) are all indexed by the tier
# _tier_for_day yields. The last one lives in another module that can't import this
# one, so the drift is caught here — extending the tiers in one table without the
# others should fail on import, not KeyError inside a fixer's offer refresh.
if (
    len(DIFFICULTY_BASE) != len(REWARD_BASE)
    or len(DIFFICULTY_BASE) != len(JOB_XP_BASE)
    or set(ENEMY_TIERS) != set(range(len(DIFFICULTY_BASE)))
):
    raise ValueError("DIFFICULTY_BASE, REWARD_BASE, JOB_XP_BASE and combat.ENEMY_TIERS must cover the same tiers")
# matrix.ICE_TIERS is the third fight-population table (see combat.ENEMY_TIERS above) — a
# matrix job's fights draw from it by the same day tier, so it has to span the same tiers.
if set(ICE_TIERS) != set(range(len(DIFFICULTY_BASE))):
    raise ValueError("matrix.ICE_TIERS must cover the same tiers as DIFFICULTY_BASE")

# How much harder the last stage of a job is than the first. Spread across however
# many stages the job turned out to have, so the arc is the same shape whether it
# runs 3 stages or 4 — a longer job is more *checks*, not a steeper climb. (A flat
# +1 per stage index, which is what this replaces, quietly made 4-stage jobs harder
# to finish than 3-stage ones for the same money.)
STAGE_DIFFICULTY_RAMP = 2

# A stage offers a subset of its pool, not the whole thing: how many ways in this
# particular job happens to have is part of what makes one offer better than another.
# FULL_POOL_CHANCE of the time you get every approach; otherwise you get exactly
# PARTIAL_POOL_SIZE of them — an exact count, not a floor, so widening a pool adds
# approaches the full-pool roll can reach but does not make the partial draw any
# wider. It doubles as the minimum pool size, since a pool must have at least this
# many to draw from (guarded at import).
PARTIAL_POOL_SIZE = 2
FULL_POOL_CHANCE = 0.35

# Standing lost with the corp you just robbed, on a completed job.
JOB_STANDING_HIT = -2

# Trust gained with the fixer who sent you, on a completed job — the other half of
# JOB_STANDING_HIT: the corp you hit likes you less, the fixer who profits off it
# likes you more. Same trigger (the final stage's success/critical-success) as standing.
FIXER_TRUST_GAIN = 2

# The other direction: a job that ends without paying out — the last stage's plain
# failure, or fleeing any fight the job routed you into — costs the fixer's trust and a
# point of street rep. A merely costly stage doesn't trigger this (failure carries the
# job on to its next stage, see DAMAGE_FOR_DELTA); only a job that ends with nothing to
# show for it does. Kept separate from FIXER_TRUST_GAIN/JOB_STANDING_HIT rather than
# just negating them: this is a flat penalty for wasting the fixer's and the street's
# time, not a reversal of the completed-job reward.
JOB_FAILURE_TRUST_HIT = -1
JOB_FAILURE_REP_HIT = -1

# Every stage carries a fight beside it, reachable two ways — and which way you got
# there is the whole difference between the two (combat.drop_for_result reads it off
# the check that routed you in):
#
# - You *chose* it. AMBUSH_LABEL is appended to every stage's choices on top of the
#   drawn pool, so a job can never withhold every approach your build can pass: there
#   is always a way through, and it is always the one that bleeds. Make the Tactics
#   check and you open with a free round; miss it and the fight starts even.
# - You *botched into* it. A critical failure on any normal approach goes loud, and
#   they get the free round instead. Only a critical failure — a plain failure still
#   costs health and advances, which is the property the whole damage curve is tuned
#   around (see DAMAGE_FOR_DELTA). Routing every failure into a fight is how you get
#   a job that is mostly fighting and a death rate to match.
#
# The ambush deliberately isn't held to the "approaches must sit on different stats"
# rule the pools are: it doesn't *pass* the stage, it replaces passing it with a
# fight, so it isn't a second bite at the same gate.
AMBUSH_SKILL = "tactics"
AMBUSH_DIFFICULTY = 12
AMBUSH_LABEL = "Take them first"

# How often a JobStage.vigilance beat (currently only Bodyguard) actually rolls a
# check at all: the rest of the time it's a scene.Stage.narration beat instead --
# no roll, nothing to click, just prose and a Continue. Escorting a client
# shouldn't force a check at every single stop; most of them should just pass.
VIGILANCE_THREAT_CHANCE = 0.25
VIGILANCE_QUIET_TEXT = "Nothing catches your eye. The stop passes without incident."

# Fighting through a stage is a way *past* it, not a way to skip the job: winning
# rejoins the job at the next stage, and winning the last stage pays it out like any
# other success. Running, though, ends the run of the job entirely (next_stage None) —
# the contract is blown, and the fixer keeps the money.
FIGHT_PROMPT = "{faction} security comes down on you at {location}. No more talking."

# The matrix counterpart of FIGHT_PROMPT, for a Data Heist (archetype.matrix): the fight
# beside every stage is ICE in {faction}'s architecture, not muscle in the hallway,
# because a remote hack never puts a body in the building. Same routing (the ambush and a
# critical failure both point at fight_id); only what sits in the stage differs.
MATRIX_FIGHT_PROMPT = "{faction}'s ICE closes on your signal in the {location} node. Breach or burn."

# Some jobs play their fights out on a grid (tactical.TacticalStage) instead of the
# abstract round-by-round Encounter — a whole job is one or the other, decided once, so a
# job reads as either "a contract with muscle on standby" or "a set-piece infiltration"
# rather than flip-flopping mid-run. The routing is identical either way (the ambush
# choice and critical failures both point at fight_id); only what sits in that stage differs.
TACTICAL_FIGHT_CHANCE = 0.35

# A soft theming knob, not a table that must cover every kind: how much low cover a site's
# fight map gets, by what kind of place it is. A depot is racking and crates; a data floor
# is open sightlines. Anything unlisted gets the middling default — no import guard needed.
_TACTICAL_COVER_BY_KIND = {
    LocationKind.DEPOT: 0.16,
    LocationKind.WEAPON_SHOP: 0.14,
    LocationKind.BAR: 0.12,
    LocationKind.DATA: 0.05,
}
_DEFAULT_COVER_DENSITY = 0.09


def _cover_density(kind: LocationKind) -> float:
    return _TACTICAL_COVER_BY_KIND.get(kind, _DEFAULT_COVER_DENSITY)


class StageType(StrEnum):
    """What a stage *is*, not just what it rolls.

    Every archetype walks the same arc — get there, do the thing, get out — with
    its own pools and prose for each beat. The type is the semantic handle on a
    stage: it's what lets a job say "this one has a nasty exfil" rather than
    "this one has a stage_2", and it is the intended hook for hired support later
    (a netrunner covers your OBJECTIVE, muscle covers your EXFIL). Nothing reads
    it that way yet — today it carries the prompt and marks which stages are
    optional.
    """

    APPROACH = "approach"  # get to the job
    OBJECTIVE = "objective"  # do the thing you came to do
    COMPLICATION = "complication"  # it stops going to plan
    EXFIL = "exfil"  # be somewhere else


# Stage type -> the chance it shows up at all. A type in here is optional and rolled
# for at generation, so a job runs 3 or 4 stages; a type absent from it is mandatory.
# The chance lives *with* the type rather than beside it as a lone COMPLICATION_CHANCE:
# a second optional type would otherwise silently inherit the complication's odds.
# Membership is the "is this optional?" test, so there is one table here, not two that
# have to agree. The last stage of an archetype must be mandatory (the payout rides on
# the final stage) — guarded below.
OPTIONAL_STAGE_CHANCE = {StageType.COMPLICATION: 0.4}

# REWARD_BASE prices a job with no complication. One that has one is a longer job
# with an extra check's worth of blood in it, so it pays this much more per extra
# stage — otherwise the fixer board would quietly price identical-looking offers the
# same while one of them is strictly worse.
REWARD_PER_EXTRA_STAGE = 0.3


# The risk curve: how much health a failed approach costs, by how much easier than
# the stage's base difficulty it was. This is the *only* place job damage is set —
# an Approach's damage is derived from its difficulty_delta, never written next to
# it, so "the easy way in is the one that hurts" is structural and a row physically
# cannot be tuned out of the gradient.
#
# Calibrated against job *length*: a job runs 3-4 stages, and an off-stat specialist
# takes the bloody route on most of them, so a body-1 runner's 15 health is the
# budget these numbers spend. Doubling them is how you get a 13% death rate on a
# routine job — re-run the balance sim if you touch this.
DAMAGE_FOR_DELTA = {
    1: 1,  # hard and clean
    0: 2,
    -1: 3,
    -2: 4,  # easy and bloody
}

if sorted(DAMAGE_FOR_DELTA, reverse=True) != sorted(
    DAMAGE_FOR_DELTA, key=lambda d: DAMAGE_FOR_DELTA[d]
):
    raise ValueError("DAMAGE_FOR_DELTA must hurt more the easier the check gets")

# A guard's sightline catching a burglary's interior walk (scene.BurglaryStage.spotted)
# costs a flat, modest hit — deliberately on the low end of DAMAGE_FOR_DELTA, since it
# stacks on top of whatever the entrance check already cost. Getting spotted is the
# real punishment (it routes to the job's fight stage, same as any critical failure);
# the health cost alone shouldn't also be as steep as a doubled entrance failure would be.
BURGLARY_SPOTTED_DAMAGE = 2


@dataclass(frozen=True)
class Approach:
    """One way through a job stage: a skill, and how hard/bloody that way is.

    difficulty_delta shifts the stage's rolled difficulty, and it alone fixes the
    health cost (failure_damage; a critical failure deals the same and goes loud):
    the cheap check is always the one that hurts. A stage rolls its base difficulty
    *once* and every approach is offset from it, so a delta means the same thing on
    every job.
    """

    skill: str  # a skill id (skills.SKILLS_BY_ID)
    difficulty_delta: int
    flavor: str

    @property
    def failure_damage(self) -> int:
        return DAMAGE_FOR_DELTA[self.difficulty_delta]


@dataclass(frozen=True)
class JobStage:
    """One beat of a job: what kind of beat it is, how it reads, and the ways through.

    `approaches` is a *pool*, not the offer — generate_job draws a subset of it.
    `prompt` is a format string over verb/faction/territory/location/target; the
    fields are checked at import so a bad one can't KeyError mid-generation.
    """

    type: StageType
    prompt: str
    approaches: tuple[Approach, ...]
    # True only for Burglary's and Wetwork's APPROACH stages (set at ARCHETYPES
    # construction, see below, not authored per-row) -- tells generate_job to build
    # this stage as a scene.BurglaryStage (entrance diagram + interior walk) instead
    # of a plain Choice list. `approaches` stays meaningful either way: it still feeds
    # each Entrance's skill/difficulty/flavor and is still subject to the same
    # import-time pool-size/cross-stat guards below.
    burglary: bool = False


@dataclass
class JobArchetype:
    name: str
    verb: str
    stages: tuple[JobStage, ...]
    # True only for Data Heist (set at ARCHETYPES construction, not authored per-row) --
    # a whole-job property, unlike JobStage.burglary's per-stage one: it makes *every*
    # fight beside a stage a scene.MatrixStage (a fight against ICE, resolved in matrix.py)
    # instead of gunmen, and suppresses the tactical/abstract roll for the job. A remote
    # hack has no body in the building, so there's nobody to meet in meatspace.
    matrix: bool = False
    # Flat hours cost overriding the shared tier-based default (8/12, see
    # generate_job) -- None for every archetype except Bodyguard, which is meant to
    # be a low-time-commitment job regardless of tier (see that row's comment).
    hours_cost: int | None = None
    # True only for Bodyguard (set at ARCHETYPES construction, not authored per-row)
    # -- a whole-job property, unlike JobStage.burglary's per-stage one: every stage
    # rolls VIGILANCE_THREAT_CHANCE for whether anything even happens at all, rather
    # than always presenting its pool. Quiet legs become a scene.Stage.narration
    # beat: no roll, nothing to click, just prose and a Continue.
    vigilance: bool = False
    # True only for Wetwork (set at ARCHETYPES construction, not authored per-row) --
    # tells generate_job to pick its burglary stage's structure from WETWORK_STRUCTURE
    # (always a private COMPOUND) instead of BURGLARY_STRUCTURE[location.kind], since
    # the target is holed up in their own place rather than broken into as the job
    # site's own business.
    wetwork: bool = False
    # Roster caps for hiring crew onto this job, carried onto the generated Scene and
    # enforced by Character.hire_for_job. None means uncapped (every archetype but the
    # three set at ARCHETYPES construction below). max_on_site counts the player.
    max_on_site: int | None = None
    max_support: int | None = None


# name, verb, then one row per stage: (StageType, prompt, approach pool).
# Approach row: (skill id, difficulty delta, flavor). The damage is not written here
# — it falls out of the delta via DAMAGE_FOR_DELTA.
#
# Each pool holds a hard/clean, a middling, and an easy/bloody way through, sitting
# on three *different* core stats — checked at import below. That is the whole point
# of the table: a stage whose approaches share a stat is not a choice, it's a
# formality that one build passes twice and the rest fail once. It also means no
# build walks every stage of every job, and a runner who is wrong for a stage can
# still buy their way past it with health.
#
# A generated job offers a *subset* of each pool (see PARTIAL_POOL_SIZE), so two
# Heists are not the same Heist: one may leave the door open for your build and the
# next may not. Pools therefore want to stay wider than PARTIAL_POOL_SIZE — a pool of
# exactly two never varies.
#
# Every archetype walks APPROACH -> OBJECTIVE -> (COMPLICATION) -> EXFIL. The
# complication is optional (OPTIONAL_STAGE_CHANCE), so a job is 3 or 4 stages, and it
# is where the job turns on you rather than merely resisting you.
_ARCHETYPE_ROWS = (
    (
        "Heist",
        "break into",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("stealth", 1, "Slip past the perimeter unseen"),
                    ("forgery", 0, "Badge in on credentials you wrote yourself"),
                    ("toughness", -2, "Go straight through the fence and eat the hits"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're inside {location}. {target} sits behind the last real lock.",
                (
                    ("hack", 1, "Crack the ice around the prize"),
                    ("infiltration", 0, "Work the vault's locks by hand"),
                    ("blunt", -2, "Put the case through with a wrecking bar"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A patrol that shouldn't be on the roster doubles back down the corridor.",
                (
                    ("listening", 1, "Track them by sound and stay a room ahead"),
                    ("intimidation", 0, "Freeze the one who sees you"),
                    ("grapple", -2, "Put them on the floor before they can call it in"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have {target}. Now you have to be somewhere else.",
                (
                    ("dodge", 1, "Slip the cordon before it closes"),
                    ("deception", 0, "Walk out past the response team like you belong there"),
                    ("lift", -2, "Force the loading shutter and go"),
                ),
            ),
        ),
    ),
    (
        "Extraction",
        "extract a target from",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("deception", 1, "Walk in as staff nobody thinks to question"),
                    ("tactics", 0, "Time your approach to the shift change"),
                    ("acrobatics", -1, "Come in over the roofline"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You've found them. Getting them to move is a separate problem.",
                (
                    ("grapple", 1, "Put the target down and carry them out"),
                    ("intimidation", 0, "Make it very clear they are leaving with you"),
                    ("toughness", -2, "Take what the room does to you and keep hold of them"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "The target panics, and panic is loud.",
                (
                    ("intuition", 1, "See it coming in their eyes and get ahead of it"),
                    ("negotiations", 0, "Cut them a deal on the spot"),
                    ("sturdy", -2, "Take them off their feet and keep moving"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have them. Now get them off {faction}'s ground.",
                (
                    ("forgery", 1, "Badge the two of you through the checkpoint"),
                    ("running", 0, "Carry them, and don't stop"),
                    ("short_blade", -2, "Cut through the cordon"),
                ),
            ),
        ),
    ),
    (
        "Sabotage",
        "sabotage",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("stealth", 1, "Come in through the service ducts"),
                    ("infer", 0, "Read the plant's layout and walk straight to it"),
                    ("toughness", -2, "Come through the loading door and dare them to stop you"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "The machinery is in front of you. It has to fail, and not while you're stood here.",
                (
                    ("tinkering", 1, "Rig the hardware to fail hours from now"),
                    ("sleight_of_hand", 0, "Palm the charge onto it as you walk past"),
                    ("lift", -2, "Wreck the machinery by hand"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A coolant line lets go, and a tech comes to find out why.",
                (
                    ("pattern_seeking", 1, "Spot the cascade before it reaches you"),
                    ("fortitude", 0, "Ride out the chemical wash and keep working"),
                    ("grapple", -2, "Put the tech in a locker"),
                ),
            ),
            (
                StageType.EXFIL,
                "It's going to go, and you are still inside {location}.",
                (
                    ("running", 1, "Run, and keep running until the sirens fade"),
                    ("dodge", 0, "Slip the response team in the stairwell"),
                    ("short_blade", -2, "Cut your way out through whoever is closest"),
                ),
            ),
        ),
    ),
    (
        # A specialist job: every beat leads with an intelligence skill, which is what
        # archetype_specialist() reads to call it Netrunner work — and what makes
        # generate_job keep that lead through the partial draw. The other two approaches
        # on each beat still sit on different stats, so this is a job a Solo can take and
        # bleed through, not one they're locked out of.
        "Intrusion",
        "breach",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("recon", 1, "Map their netarch from the outside before you touch it"),
                    ("forgery", 0, "Spoof a contractor's credentials onto the access list"),
                    ("toughness", -2, "Splice the trunk line by hand and eat the feedback"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're in their architecture. {target} sits behind black ICE.",
                (
                    ("hack", 1, "Break the ICE around it"),
                    ("sleight_of_hand", 0, "Jack a physical tap straight into the terminal"),
                    ("blunt", -2, "Pull the drive out of the rack and take it with you"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A trace program wakes up and starts walking back down your connection.",
                (
                    ("infer", 1, "Read the trace's shape and stay ahead of it"),
                    ("listening", 0, "Catch the subroutine's rhythm and time your jumps"),
                    ("fortitude", -2, "Tank the neural feedback and keep working"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have {target}. Their logs still say you were ever here.",
                (
                    ("tinkering", 1, "Scrub the logs and back out the way you came"),
                    ("stealth", 0, "Pull the tap and walk before the sweep reaches you"),
                    ("intimidation", -2, "Let them watch you go, and dare them to follow"),
                ),
            ),
        ),
    ),
    (
        # A second specialist job: every beat leads with a strength or body skill —
        # both map to Solo in SPECIALIST_FOR_STAT, so archetype_specialist() still
        # reads it as one archetype's contract even though the lead skill itself
        # varies stage to stage. Where Intrusion is the Netrunner's quiet way through
        # a system, Wetwork is the Solo's loud way through people. Its APPROACH gets
        # the same treatment as Burglary's own (the `burglary` flag below, set at
        # ARCHETYPES construction) -- the target is always holed up in a private
        # COMPOUND regardless of the job site's own kind (WETWORK_STRUCTURE), so this
        # stage's flavor strings are short entrance captions, not sentences, same as
        # Burglary's. The other two approaches on each beat still sit on different
        # stats, same as every other archetype, so it's a job a Netrunner or
        # Infiltrator can take and bleed through rather than one they're locked out
        # of.
        "Wetwork",
        "strong-arm",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("grapple", 1, "Perimeter Wall"),
                    ("infer", 0, "Service Entrance"),
                    ("intimidation", -2, "Front Gate"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're standing between {target} and everyone paid to keep you from it.",
                (
                    ("toughness", 1, "Put them down before they finish reaching for the alarm"),
                    ("tactics", 0, "Time it to the gap in their coverage"),
                    ("firearms", -2, "Put them down loud and don't wait to see who noticed"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "The muscle they kept in reserve finally shows up.",
                (
                    ("lift", 1, "Put them through whatever's nearest and keep moving"),
                    ("negotiations", 0, "Buy the two seconds you need with a lie"),
                    ("dodge", -2, "Take the hit meant for you and keep going"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have {target}, and the whole floor knows it now.",
                (
                    ("toughness", 1, "Walk out the way you came in, like nothing happened"),
                    ("recon", 0, "You mapped three ways out before you went in; take the second"),
                    ("dodge", -2, "Break into a dead run and don't look back"),
                ),
            ),
        ),
    ),
    (
        # A generic archetype, same as Heist/Extraction/Sabotage (mixed stat leads,
        # no specialist). What's actually different is structural, not thematic: the
        # ARCHETYPES comprehension below flags this row's APPROACH stage burglary=True,
        # which tells generate_job to build it as a scene.BurglaryStage (an entrance
        # diagram, then an interior walk) instead of a Choice list -- see jobs.py's
        # generate_job and screens/burglary_screens.py. The APPROACH row's flavor
        # strings are deliberately short node captions ("Front Door"), not sentences
        # like every other row's -- they become the diagram's labels, not a line in a
        # list, and that's the one place this table departs from the others' voice.
        "Burglary",
        "burgle",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("forgery", 1, "Front Door"),
                    ("stealth", 0, "Back Window"),
                    ("lift", -2, "Loading Dock"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're inside. {target} sits behind the vault's last lock.",
                (
                    ("hack", 1, "Crack the electronic lock before it screams"),
                    ("infiltration", 0, "Pick the mechanical backup by hand"),
                    ("blunt", -2, "Break the vault open with a crowbar"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A motion sensor you missed trips somewhere in the building.",
                (
                    ("pattern_seeking", 1, "Spot the sensor's blind arc and thread it"),
                    ("tinkering", 0, "Kill the sensor's feed before it reports"),
                    ("grapple", -2, "Catch whoever comes to check it"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have {target}. Now you have to be somewhere else.",
                (
                    ("dodge", 1, "Slip out the way you came before anyone's the wiser"),
                    ("deception", 0, "Walk out like you belong there"),
                    ("short_blade", -2, "Cut past whoever's between you and the street"),
                ),
            ),
        ),
    ),
    (
        # A second Netrunner specialist (every beat leads with `hack`, so
        # archetype_specialist() reads Netrunner and pins the lead through the partial
        # draw, same as Intrusion). What's structurally different is the ARCHETYPES
        # comprehension below flags the *whole* archetype matrix=True: this is a remote
        # hack, so the fight beside every stage is ICE (a scene.MatrixStage, resolved in
        # matrix.py) rather than muscle, and losing one is ejection -> the contract
        # blown, never death (see generate_job and screens/matrix_screen.py). Where
        # Intrusion is netrunning that resolves as ordinary checks and meat fights, a
        # Data Heist's signature is that its fights *are* matrix combat. It's shown to
        # every build, with a cyberdeck/Hack warning (matrix.matrix_readiness) rather
        # than a lockout: a non-hacker can take it and bleed against the ICE.
        "Data Heist",
        "crack",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("hack", 1, "Slip through a seam in the perimeter ICE"),
                    ("stealth", 0, "Ghost past the watchdogs on a spoofed handshake"),
                    ("toughness", -2, "Brute-force the gateway and eat the feedback"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're inside their architecture. {target} sits behind the datastore's black ICE.",
                (
                    ("hack", 1, "Peel the black ICE apart, layer by layer"),
                    ("infiltration", 0, "Pick the datastore's logical locks by hand"),
                    ("blunt", -2, "Crash the node and rip the data as it falls"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "A tracer program wakes and starts walking back up your connection.",
                (
                    ("hack", 1, "Loop the tracer back on itself"),
                    ("dodge", 0, "Bounce your signal through a dozen dead relays"),
                    ("fortitude", -2, "Tank the neural feedback and keep working"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have {target}. Their logs still say you were never here.",
                (
                    ("hack", 1, "Scrub the logs and back out clean"),
                    ("deception", 0, "Leave a false trail pointing at a rival crew"),
                    ("acrobatics", -2, "Yank the jack and ride the dumpshock out"),
                ),
            ),
        ),
    ),
    (
        # A three-stage job, not four: no COMPLICATION row at all, so a Bodyguard
        # contract is always exactly APPROACH -> OBJECTIVE -> EXFIL -- meet the
        # client, the exposed stop, get them home. Every stage also carries
        # vigilance=True (set in the ARCHETYPES comprehension below), so most legs
        # pass with nothing to click at all (see VIGILANCE_THREAT_CHANCE) rather than
        # forcing a check at every single stop; when a threat does roll, it's a full
        # stage with the pool below. Leads with perception (a second Infiltrator
        # specialist alongside Recon -- "on the lookout" is what this job is), but
        # strength/combat skills get real weight too as the way to actually deal with
        # what you spotted, not just spot it.
        "Bodyguard",
        "guard",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("sight", 1, "Clock the meet point before they arrive"),
                    ("negotiations", 0, "Talk the client into moving on your timeline, not theirs"),
                    ("toughness", -2, "Push through the crowd at their side and eat the jostling"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You're at {location}, and your principal is exposed for exactly as long as you let them be.",
                (
                    ("pattern_seeking", 1, "Spot the one face that's shown up twice"),
                    ("tactics", 0, "Read the room's exits before you need one"),
                    ("grapple", -2, "Put a hand on the first person who gets too close"),
                ),
            ),
            (
                StageType.EXFIL,
                "Your principal is still breathing. Getting them clear is the last part of the job.",
                (
                    ("listening", 1, "Hear the follow before it closes the distance"),
                    ("intimidation", 0, "Make it very clear this isn't worth it"),
                    ("short_blade", -2, "Put down whoever's still following and keep walking"),
                ),
            ),
        ),
    ),
    (
        # The Infiltrator specialist (every beat leads with a perception skill, which
        # SPECIALIST_FOR_STAT maps to Infiltrator) -- Netrunner has two of these
        # (Intrusion, Data Heist), Bodyguard above is the other Infiltrator job.
        # Perception is also the most underused stat in the generic pool (see the
        # balance notes in DESIGN.md), so leading every beat with it does double duty:
        # it gives Infiltrator a contract of their own and it's the direct fix for that
        # gap. The other two approaches on each beat still sit on different stats, same
        # as every other specialist archetype.
        "Recon",
        "case",
        (
            (
                StageType.APPROACH,
                "You need to {verb} {faction} at {location}, in {territory}, to reach {target}.",
                (
                    ("sight", 1, "Pick your vantage before anyone knows you're watching"),
                    ("forgery", 0, "Walk in on a badge that says you belong"),
                    ("toughness", -2, "Push through wherever the crowd's thinnest"),
                ),
            ),
            (
                StageType.OBJECTIVE,
                "You've got eyes on {target}. Getting the read you actually need means holding position.",
                (
                    ("pattern_seeking", 1, "Read the rotation until you know it cold"),
                    ("infer", 0, "Piece together what the layout's telling you"),
                    ("lift", -2, "Force the one lock that's actually in your way"),
                ),
            ),
            (
                StageType.COMPLICATION,
                "Something moves that wasn't supposed to be there.",
                (
                    ("listening", 1, "Catch the change in the chatter before it catches you"),
                    ("stealth", 0, "Go still and let it pass you by"),
                    ("intimidation", -2, "Brazen it out before anyone thinks to ask"),
                ),
            ),
            (
                StageType.EXFIL,
                "You have what you came for. Getting clear without being made is the other half of the job.",
                (
                    ("intuition", 1, "Feel the exit clear before you take it"),
                    ("deception", 0, "Walk out looking like you belong"),
                    ("acrobatics", -2, "Take the fast way down and don't look back"),
                ),
            ),
        ),
    ),
)

ARCHETYPES = [
    JobArchetype(
        name=name,
        verb=verb,
        stages=tuple(
            JobStage(
                type=stage_type,
                prompt=prompt,
                approaches=tuple(Approach(*approach) for approach in approaches),
                burglary=(name in ("Burglary", "Wetwork") and stage_type is StageType.APPROACH),
            )
            for stage_type, prompt, approaches in stages
        ),
        matrix=(name == "Data Heist"),
        hours_cost=4 if name == "Bodyguard" else None,
        vigilance=(name == "Bodyguard"),
        wetwork=(name == "Wetwork"),
        # Burglary: you plus one support. Data Heist: solo, no crew at all -- the
        # netrunner works it alone. Wetwork: up to two hires beside you, plus support.
        max_on_site=1 if name in ("Burglary", "Data Heist") else 3 if name == "Wetwork" else None,
        max_support=0 if name == "Data Heist" else 1 if name in ("Burglary", "Wetwork") else None,
    )
    for name, verb, stages in _ARCHETYPE_ROWS
]

# Everything the table can get wrong, caught at import rather than mid-generation.
#
# A typo'd skill id fails here, not mid-roll, and so does a difficulty_delta that
# DAMAGE_FOR_DELTA doesn't price — the risk curve is the only source of job damage, so
# a delta off the end of it has no damage at all. A pool too small to draw
# PARTIAL_POOL_SIZE from would make rng.sample raise, and a one-approach stage is
# not a choice at all — it's the regression this table exists to prevent. Neither is
# a stage whose approaches share a core stat: a job stage is a gate every build has
# to pass, so two approaches on one stat hand that stat's runner a second bite and
# everyone else nothing. Checking the stat rule across the whole *pool* means it
# holds for every subset the generator can draw. (Gigs are optional and
# self-selected, so they're allowed to be themed on one stat; see
# gigs._GIG_TEMPLATES.)
_PROMPT_FIELDS = {
    "verb": "",
    "faction": "",
    "territory": "",
    "location": "",
    "target": "",
}

for _archetype in ARCHETYPES:
    if not _archetype.stages:
        raise ValueError(f"{_archetype.name}: a job needs at least one stage")
    # The cash, rep and standing all ride on whichever stage ends up last. If that
    # stage could be dropped as optional, the payout would silently move with it.
    if _archetype.stages[-1].type in OPTIONAL_STAGE_CHANCE:
        raise ValueError(
            f"{_archetype.name}: the last stage carries the payout and cannot be optional, "
            f"got {_archetype.stages[-1].type}"
        )
    for _stage in _archetype.stages:
        _stage.prompt.format(**_PROMPT_FIELDS)  # unknown field: fail here, not mid-job
        if len(_stage.approaches) < PARTIAL_POOL_SIZE:
            raise ValueError(
                f"{_archetype.name}/{_stage.type}: a stage pool needs at least "
                f"{PARTIAL_POOL_SIZE} approaches to draw from, got {len(_stage.approaches)}"
            )
        for _approach in _stage.approaches:
            # Approach.failure_damage only reads DAMAGE_FOR_DELTA when a job is being
            # generated, so an off-curve delta would KeyError at a fixer refresh.
            if _approach.difficulty_delta not in DAMAGE_FOR_DELTA:
                raise ValueError(
                    f"{_archetype.name}/{_stage.type}: {_approach.skill!r} has no damage on the "
                    f"risk curve for difficulty_delta {_approach.difficulty_delta}, "
                    f"which must be one of {sorted(DAMAGE_FOR_DELTA, reverse=True)}"
                )
        _stats = [skill_for(approach.skill).stat for approach in _stage.approaches]
        if len(set(_stats)) != len(_stats):
            raise ValueError(
                f"{_archetype.name}/{_stage.type}: a job stage's approaches must sit on "
                f"different core stats, got {_stats}"
            )


# The runner archetype (runners.py) that fits a beat, keyed by the core stat its lead
# approach rolls: the hack-and-data specialist, the muscle, the finesse operator. A job's
# roles (Scene.roles) are *derived* from this rather than hand-mapped per beat, so a beat's
# specialist is always whatever skill actually leads it — an Extraction's grab-the-target
# objective reads as muscle, a Heist's crack-the-ice one as a netrunner, from the same table.
SPECIALIST_FOR_STAT = {
    "intelligence": "Netrunner",
    "strength": "Solo",
    "body": "Solo",
    "agility": "Infiltrator",
    "perception": "Infiltrator",
    "cool": "Infiltrator",
}
if set(SPECIALIST_FOR_STAT) != set(CORE_STATS):
    raise ValueError("SPECIALIST_FOR_STAT must map every core stat to a runner archetype")

# Skills a specialist can work from afar — the netrunner in the car. A beat led by one of
# these is a REMOTE role; every other beat is worked ON_SITE (see scene.Posture).
REMOTE_SKILLS = frozenset({"hack"})


def archetype_specialist(archetype: JobArchetype) -> str | None:
    """The runner archetype this job is *for*, or None if it's generic work.

    Derived from the leads rather than tabulated, for the same reason Scene.roles is: a
    job every one of whose beats leads with the same specialist doesn't merely suit them,
    it *is* their contract, and a field saying otherwise could only ever drift from the
    approaches actually in the table.

    This is what buys the specialist their lane. A generated job draws a subset of each
    pool (PARTIAL_POOL_SIZE), so a lead can be withheld — fine for generic work, where
    "which ways in this job happens to have" is the point, but it would make a Netrunner
    job that offers no netrunning. generate_job keeps the lead for these; the rest of the
    pool is drawn as normal, so two Intrusions still aren't the same Intrusion.
    """
    specialists = {
        SPECIALIST_FOR_STAT[skill_for(stage.approaches[0].skill).stat]
        for stage in archetype.stages
    }
    return specialists.pop() if len(specialists) == 1 else None


def _role_for_stage(job_stage: JobStage) -> Role:
    """The crew position a beat offers, derived from its lead (cleanest) approach: the
    specialist is whoever that skill's stat points to, and the posture is remote if the
    skill can be worked over the net (REMOTE_SKILLS), else on-site. Derived from the full
    template pool's lead, not the offer's drawn subset, so a beat's role is the same
    regardless of which approaches this particular offer happens to include."""
    lead = job_stage.approaches[0].skill
    stat = skill_for(lead).stat
    posture = Posture.REMOTE if lead in REMOTE_SKILLS else Posture.ON_SITE
    return Role(beat=job_stage.type.value, specialist=SPECIALIST_FOR_STAT[stat], posture=posture)


@dataclass
class JobTiming:
    deadline_day: int | None = None
    scheduled_day: int | None = None

    @property
    def label(self) -> str:
        if self.scheduled_day is not None:
            return f"must run on day {self.scheduled_day}"
        if self.deadline_day is not None:
            return f"expires after day {self.deadline_day}"
        return "no deadline"

    def is_available(self, day: int) -> bool:
        if self.scheduled_day is not None:
            return day == self.scheduled_day
        return True

    def is_expired(self, day: int) -> bool:
        if self.scheduled_day is not None:
            return day > self.scheduled_day
        if self.deadline_day is not None:
            return day > self.deadline_day
        return False


def _tier_for_day(day: int) -> int:
    return day_tier(day, len(DIFFICULTY_BASE))


def _random_timing(day: int, rng: random.Random) -> JobTiming:
    kind = rng.choices(["none", "deadline", "scheduled"], weights=[0.4, 0.35, 0.25])[0]
    if kind == "deadline":
        return JobTiming(deadline_day=day + rng.randint(2, 5))
    if kind == "scheduled":
        return JobTiming(scheduled_day=day + rng.randint(1, 4))
    return JobTiming()


# Standing gained with the gang that hired you, on a successful delivery. A
# gang isn't robbed the way a job's corp target is (JOB_STANDING_HIT), it's the
# client, so completing its job moves standing the other direction. Missing the
# deadline costs the same amount instead of gaining it -- see
# Character.smuggling_job / app._apply_day_tick.
GANG_JOB_STANDING_GAIN = 2

# How many days out a Smuggling delivery's deadline sits, scaled by how far the
# destination actually is from the pickup (corpmap.territory_distance) -- a
# cross-map run gets more time than a next-door one. First-slice numbers, not
# balance-simulated.
SMUGGLING_BASE_DEADLINE_DAYS = 2
SMUGGLING_DEADLINE_DAYS_PER_HOP = 1


@dataclass
class SmugglingJob:
    """A gang delivery, tracked directly on Character rather than as a Scene --
    there's no staged approach/objective/complication/exfil to it (see the
    conversation this replaced: it used to be a JobArchetype). The runner picks
    it up in person at a gang's den (GangDenScreen) and delivers it in person at
    the destination territory (MainMenu's "Deliver" action) -- nothing about it
    resolves through checks."""

    gang_id: str
    item: str  # flavor text for the cargo, drawn from TARGETS
    destination_territory_id: str
    deadline_day: int
    reward_cash: int


def generate_smuggling_job(
    gang_id: str, pickup_territory_id: str, corp_map: CorpMap, day: int, rng: random.Random | None = None
) -> SmugglingJob:
    """Called when the runner takes the job in person at the gang's den
    (pickup_territory_id is wherever that den actually is -- there's no separate
    pickup roll, they're already standing there). The destination is any other
    territory on the map, regardless of owner."""
    rng = resolve_rng(rng)
    destination = rng.choice([t for t in corp_map.territories.values() if t.id != pickup_territory_id])
    hops = territory_distance(corp_map, pickup_territory_id, destination.id)
    return SmugglingJob(
        gang_id=gang_id,
        item=rng.choice(TARGETS),
        destination_territory_id=destination.id,
        deadline_day=day + SMUGGLING_BASE_DEADLINE_DAYS + SMUGGLING_DEADLINE_DAYS_PER_HOP * hops,
        reward_cash=REWARD_BASE[_tier_for_day(day)],
    )


# What a burglary target is shaped like, per the site you're breaking into. Splits
# GENERATED_KINDS on the question buildings.py can answer: does this place have a
# shop-front with somebody living over it (RESIDENTIAL -- cluttered, a basement,
# bedrooms upstairs, one guard), is it a floor of desks (OFFICE -- wider, barer,
# reception at street level, two guards), or is it run out of a private walled estate
# (COMPOUND -- bigger still, two storeys and an optional basement, three guards)? Same
# table shape and guard as corpmap.LOCATION_SKILL. First-slice assignments: a kind
# moving sides is a one-line change here, not a generator change.
BURGLARY_STRUCTURE = {
    LocationKind.DATA: BuildingKind.OFFICE,
    LocationKind.LAB: BuildingKind.OFFICE,
    LocationKind.DEPOT: BuildingKind.OFFICE,
    LocationKind.CYBER_CLINIC: BuildingKind.OFFICE,
    LocationKind.HOSPITAL: BuildingKind.OFFICE,
    LocationKind.REAL_ESTATE: BuildingKind.COMPOUND,
    LocationKind.AUTO_DEALER: BuildingKind.OFFICE,
    LocationKind.BAR: BuildingKind.RESIDENTIAL,
    LocationKind.PAWN: BuildingKind.RESIDENTIAL,
    LocationKind.WEAPON_SHOP: BuildingKind.RESIDENTIAL,
    LocationKind.PHARMACY: BuildingKind.RESIDENTIAL,
    LocationKind.COMPUTER_STORE: BuildingKind.RESIDENTIAL,
}
if set(BURGLARY_STRUCTURE) != set(GENERATED_KINDS):
    raise ValueError("BURGLARY_STRUCTURE must have exactly one entry per generated LocationKind")

# Wetwork never depends on the site's own kind the way Burglary does: the target is
# being hit wherever they're holed up, not broken into as a data haven or an office,
# so it always drops the runner at a private COMPOUND.
WETWORK_STRUCTURE = BuildingKind.COMPOUND


def generate_job(
    day: int, corp_map: CorpMap, fixer_id: str, rng: random.Random | None = None
) -> tuple[Scene, JobTiming]:
    rng = resolve_rng(rng)
    archetype = rng.choice(ARCHETYPES)
    specialist = archetype_specialist(archetype)
    # The mark is a real corp, hit in a district it actually holds on this run's map.
    held = sorted(
        (t for t in corp_map.territories.values() if t.owner in FACTIONS_BY_ID),
        key=lambda t: t.id,
    )
    territory = rng.choice(held)
    faction = FACTIONS_BY_ID[territory.owner]
    # Never the runner's own place: if they've bought a safehouse in this corp district,
    # it's not a job site (and carries none of the LOCATION_SKILL/legwork tables a site
    # needs). A held district always has its generated locations to pick from.
    location = rng.choice([loc for loc in territory.locations if loc.kind in GENERATED_KINDS])
    target = rng.choice(TARGETS)
    tier = _tier_for_day(day)
    difficulty_base = DIFFICULTY_BASE[tier]
    if archetype.hours_cost is not None:
        hours_cost = archetype.hours_cost
    else:
        hours_cost = 8 if tier == 0 else 12

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    # Which beats this job actually has. An optional stage that doesn't make the cut
    # is gone before any ids are handed out, so stage_0..n stay contiguous.
    job_stages = [
        stage
        for stage in archetype.stages
        if stage.type not in OPTIONAL_STAGE_CHANCE
        or rng.random() < OPTIONAL_STAGE_CHANCE[stage.type]
    ]
    mandatory = sum(1 for s in archetype.stages if s.type not in OPTIONAL_STAGE_CHANCE)
    extra_stages = len(job_stages) - mandatory
    reward_base = int(REWARD_BASE[tier] * (1 + REWARD_PER_EXTRA_STAGE * extra_stages))
    stage_ids = [f"stage_{i}" for i in range(len(job_stages))]
    stages: dict[str, Stage] = {}
    # Decided once for the whole job (see TACTICAL_FIGHT_CHANCE): every fight in it is
    # either a grid set-piece or the abstract Encounter, not a mix. A matrix job (Data
    # Heist) is neither — all its fights are ICE — so it doesn't roll this at all (and the
    # short-circuit keeps it from consuming an rng draw a non-matrix job would).
    is_tactical = not archetype.matrix and rng.random() < TACTICAL_FIGHT_CHANCE

    for i, job_stage in enumerate(job_stages):
        is_last = i == len(stage_ids) - 1
        next_stage = None if is_last else stage_ids[i + 1]
        fight_id = f"{stage_ids[i]}_fight"

        def _payout(text: str, multiplier: float, rep: int, ns: str | None, last: bool) -> Outcome:
            return Outcome(
                text=text,
                next_stage=ns,
                cash_delta=int(reward_base * multiplier) if last else 0,
                experience_delta=int(JOB_XP_BASE[tier] * multiplier) if last else 0,
                rep_delta=rep if last else 0,
                standing_delta=JOB_STANDING_HIT if last else 0,
                fixer_trust_delta=FIXER_TRUST_GAIN if last else 0,
            )

        if archetype.vigilance and rng.random() >= VIGILANCE_THREAT_CHANCE:
            # Nothing to click, nothing to fail: most legs of an escort should pass
            # without incident rather than forcing a check at every stop (see
            # VIGILANCE_THREAT_CHANCE). Decided before any pool/difficulty work below,
            # since a quiet stage needs none of it -- and no fight beside this stage
            # either, since with no roll nothing can ever route to one.
            stages[stage_ids[i]] = Stage(
                id=stage_ids[i],
                prompt="",  # narration carries the prose; a narration stage has no choices
                choices=[],
                narration=_payout(VIGILANCE_QUIET_TEXT, 1.0, 1, next_stage, is_last),
            )
            continue

        # Which ways through this job happens to leave open. Kept in pool order so
        # the clean approach still reads before the bloody one.
        pool = job_stage.approaches
        if rng.random() < FULL_POOL_CHANCE:
            approaches = list(pool)
        elif specialist is not None:
            # A specialist job promises its specialist a way through every beat, so the
            # lead — the approach that makes it their job at all — survives the draw and
            # only the rest is sampled. Same draw size as any other partial pool.
            approaches = [
                pool[0],
                *sorted(rng.sample(pool[1:], PARTIAL_POOL_SIZE - 1), key=pool.index),
            ]
        else:
            approaches = sorted(rng.sample(pool, PARTIAL_POOL_SIZE), key=pool.index)
        # Rolled once for the stage: every approach is offset from the same number,
        # so an Approach's difficulty_delta means the same thing on every job.
        ramp = round(STAGE_DIFFICULTY_RAMP * i / (len(job_stages) - 1)) if i else 0
        difficulty = difficulty_base + ramp + rng.randint(-1, 2)

        def _approach_failure(approach: Approach, text: str) -> Outcome:
            return Outcome(
                text=text,
                health_delta=-approach.failure_damage,
                next_stage=next_stage,
                # Only the last stage's plain failure ends the job with nothing to
                # show for it — everywhere else next_stage carries it on, so this
                # is 0 there, same as _payout()'s cash/rep/standing.
                fixer_trust_delta=JOB_FAILURE_TRUST_HIT if is_last else 0,
                rep_delta=JOB_FAILURE_REP_HIT if is_last else 0,
            )

        def _approach_critical_failure(approach: Approach) -> Outcome:
            # The one branch that doesn't just cost health and carry on: you're
            # made, and they arrive holding the initiative. Note it deals the
            # *plain* failure damage, not the doubled hit a critical used to deal:
            # the fight is the critical failure's punishment, and charging both
            # stacked a double-damage hit under a squad that opens with a free
            # round — which is a nat-1 killing a light build outright.
            return Outcome(
                text="It goes bad, fast. Someone hits the alarm.",
                health_delta=-approach.failure_damage,
                next_stage=fight_id,
            )

        def _ambush_kwargs() -> dict:
            # The guaranteed way through, whatever the pool draw left you: forcing
            # your way in is always loud, so every result routes straight to the
            # fight — same door AMBUSH_LABEL opens on every other stage.
            return {
                "label": f"{AMBUSH_LABEL} ({skill_for(AMBUSH_SKILL).name})",
                "skill": AMBUSH_SKILL,
                "difficulty": AMBUSH_DIFFICULTY,
                "success": Outcome(text="You pick your moment.", next_stage=fight_id),
                "failure": Outcome(text="You move too early.", next_stage=fight_id),
                "critical_failure": Outcome(text="You walk straight into them.", next_stage=fight_id),
            }

        if job_stage.burglary:
            # A Burglary or Wetwork APPROACH: each approach becomes an Entrance (a
            # diagram node, not a list row), landing the runner at a distinct spawn
            # inside a freshly generated building — several levels of it, with the
            # score (or the target) somewhere inside.
            #
            # The building is generated *with the job* and lives inside its Scene: it is
            # never a corpmap.Location, so a target adds nothing to the map the player
            # walks around, and it goes away when the job is finished or expires.
            # Burglary's structure comes from the site's own kind (BURGLARY_STRUCTURE),
            # so breaking into a data haven doesn't hand you somebody's bedrooms; Wetwork
            # always drops the runner at a COMPOUND (WETWORK_STRUCTURE) regardless of the
            # site, since the target is holed up in their own place, not the job site's.
            kind = WETWORK_STRUCTURE if archetype.wetwork else BURGLARY_STRUCTURE[location.kind]
            building = generate_building(rng, entrance_count=len(approaches), kind=kind)
            entrances = [
                Entrance(
                    label=f"{approach.flavor} ({skill_for(approach.skill).name})",
                    skill=approach.skill,
                    difficulty=difficulty + approach.difficulty_delta,
                    spawn=spawn,
                    success=_payout("It goes clean.", 1.0, 1, next_stage, is_last),
                    failure=_approach_failure(approach, "It gets messy, but you're in."),
                    critical_success=_payout(
                        "Flawless. Nobody even looks up.", 1.5, 2, next_stage, is_last,
                    ),
                    critical_failure=_approach_critical_failure(approach),
                )
                for approach, spawn in zip(approaches, building.entrance_spawns, strict=True)
            ]
            entrances.append(Entrance(spawn=building.objective, **_ambush_kwargs()))
            stages[stage_ids[i]] = Stage(
                id=stage_ids[i],
                prompt="",  # the BurglaryStage carries the prose; a burglary stage has no choices
                choices=[],
                burglary=BurglaryStage(
                    prompt=job_stage.prompt.format(
                        verb=archetype.verb,
                        faction=faction.name,
                        territory=territory.name,
                        location=location.name,
                        target=target,
                    ),
                    entrances=tuple(entrances),
                    building=building,
                    bailed=Outcome(
                        text="You back out the way you came, empty-handed.",
                        health_delta=-BURGLARY_SPOTTED_DAMAGE,
                        next_stage=fight_id,
                    ),
                    guard=rng.choice(roll_enemies(tier, rng)),
                ),
            )
        else:
            choices = [
                Choice(
                    label=f"{approach.flavor} ({skill_for(approach.skill).name})",
                    skill=approach.skill,
                    difficulty=difficulty + approach.difficulty_delta,
                    success=_payout("It goes clean.", 1.0, 1, next_stage, is_last),
                    failure=_approach_failure(approach, "It gets messy, but you push on."),
                    critical_success=_payout(
                        "Flawless. You walk out with more than you bargained for.",
                        1.5, 2, next_stage, is_last,
                    ),
                    critical_failure=_approach_critical_failure(approach),
                )
                for approach in approaches
            ]
            choices.append(Choice(**_ambush_kwargs()))

            stages[stage_ids[i]] = Stage(
                id=stage_ids[i],
                prompt=job_stage.prompt.format(
                    verb=archetype.verb,
                    faction=faction.name,
                    territory=territory.name,
                    location=location.name,
                    target=target,
                ),
                choices=choices,
            )
        # The fight beside every stage, reached by the ambush choice or a critical
        # failure. Both Outcomes are the same whether it's an abstract Encounter, a grid
        # set-piece, or an ICE run — only where they're packaged (and who turns up)
        # differs. A matrix job fields ICE and no gunmen, so roll_enemies isn't called
        # for it (nobody's in the building), and its "escape" is being ejected.
        fight_victory = _payout("They stop coming. You finish what you came for.", 1.0, 1, next_stage, is_last)
        fight_escape = Outcome(
            text="You get out with your skin. The job is blown.",
            fixer_trust_delta=JOB_FAILURE_TRUST_HIT,
            rep_delta=JOB_FAILURE_REP_HIT,
        )
        if archetype.matrix:
            fight = Stage(
                id=fight_id,
                prompt="",  # the MatrixStage carries the prose; a fight stage has no choices
                choices=[],
                matrix=MatrixStage(
                    prompt=MATRIX_FIGHT_PROMPT.format(faction=faction.name, location=location.name),
                    network=generate_matrix_network(tier, rng),
                    victory=_payout("You seize the data and the ICE goes dark.", 1.0, 1, next_stage, is_last),
                    escape=fight_escape,
                ),
            )
            stages[fight_id] = fight
            continue
        enemies = roll_enemies(tier, rng)
        fight_prompt = FIGHT_PROMPT.format(faction=faction.name, location=location.name)
        if is_tactical:
            tac = generate_map(rng, len(enemies), cover_density=_cover_density(location.kind))
            fight = Stage(
                id=fight_id,
                prompt="",  # the TacticalStage carries the prose
                choices=[],
                tactical=TacticalStage(
                    prompt=fight_prompt,
                    grid=tac.grid,
                    player_start=tac.player_start,
                    enemies=tuple(zip(enemies, tac.enemy_spawns, strict=True)),
                    victory=fight_victory,
                    escape=fight_escape,
                    exits=tac.exits,
                ),
            )
        else:
            fight = Stage(
                id=fight_id,
                prompt="",  # the Encounter carries the prose; a fight stage has no choices
                choices=[],
                combat=Encounter(
                    prompt=fight_prompt,
                    enemies=enemies,
                    victory=fight_victory,
                    escape=fight_escape,
                ),
            )
        stages[fight_id] = fight

    scene = Scene(
        id=job_id,
        title=f"{archetype.name}: {faction.name} ({territory.name})",
        kind=SceneKind.JOB,
        hours_cost=hours_cost,
        start_stage=stage_ids[0],
        stages=stages,
        target_faction_id=faction.id,
        target_territory_id=territory.id,
        target_location_id=location.id,
        target_fixer_id=fixer_id,
        # One crew position per beat this job actually has (job_stages, after the optional
        # complication is rolled), so the roles match the stages the runner will play.
        roles=[_role_for_stage(job_stage) for job_stage in job_stages],
        max_on_site=archetype.max_on_site,
        max_support=archetype.max_support,
    )
    return scene, _random_timing(day, rng)


# How each kind of place is scouted, in flavor text. The skill itself lives in
# corpmap.LOCATION_SKILL — that's also what corpmap._location_kinds reads to
# keep a district's filler slot off its own specialty's stat, so there is one
# place that says "DATA is a Hack check" rather than two that must agree.
LEGWORK_APPROACH_TEXT = {
    LocationKind.DATA: "Sift the traffic in and out of {name}",
    LocationKind.LAB: "Pull the intake records at {name}",
    LocationKind.DEPOT: "Tail a shift worker out of {name}",
    LocationKind.BAR: "Work the crowd at {name}",
    LocationKind.PAWN: "Work the counter for gossip at {name}",
    LocationKind.WEAPON_SHOP: "Tail a shipment out of {name}",
    LocationKind.AUTO_DEALER: "Chat up the lot staff at {name}",
    LocationKind.PHARMACY: "Pull the register logs at {name}",
    LocationKind.COMPUTER_STORE: "Sift the sales records at {name}",
    LocationKind.HOSPITAL: "Pull the admissions log at {name}",
    LocationKind.REAL_ESTATE: "Pose as a buyer at {name}",
    LocationKind.CYBER_CLINIC: "Pose as a client at {name}",
}
if set(LEGWORK_APPROACH_TEXT) != set(GENERATED_KINDS):
    raise ValueError("LEGWORK_APPROACH_TEXT must have exactly one entry per generated LocationKind")

# Casing the target itself is the hardest read to get, and the best one.
SITE_DIFFICULTY = 14
SITE_ADVANTAGE = 4
NEARBY_DIFFICULTY = 11
NEARBY_ADVANTAGE = 2

# Getting made while scouting used to be a flat -2 health. Now it's a fight — but a
# street-tier one: what catches you casing a block is a couple of locals who don't
# like being looked at, not the corp response team you'd meet inside on the job. Note
# there's no ambush option here, and no way to *win* your way to an advantage: legwork
# is scouting, so a fight means it went wrong. The best you get is out.
LEGWORK_FIGHT_TIER = 0
LEGWORK_FIGHT_STAGE = "made"
LEGWORK_FIGHT_PROMPT = "Two of {faction}'s people peel off the corner. They've seen enough."


def generate_legwork_for_job(
    job: Scene, corp_map: CorpMap, rng: random.Random | None = None
) -> Scene:
    rng = resolve_rng(rng)
    territory = corp_map.territories[job.target_territory_id]
    faction = FACTIONS_BY_ID[job.target_faction_id]

    choices = []
    for location in territory.locations:
        # Skip a safehouse the runner owns here — scouting isn't cased against your own
        # place, and it carries no LOCATION_SKILL entry to roll anyway.
        if location.kind not in GENERATED_KINDS:
            continue
        skill = skill_for(LOCATION_SKILL[location.kind])
        approach = LEGWORK_APPROACH_TEXT[location.kind]
        is_site = location.id == job.target_location_id
        label = f"Case {location.name} itself" if is_site else approach.format(name=location.name)
        choices.append(
            Choice(
                label=f"{label} ({skill.name})",
                skill=skill.id,
                difficulty=SITE_DIFFICULTY if is_site else NEARBY_DIFFICULTY,
                success=Outcome(
                    text=(
                        "You clock the pattern cold. You'll know exactly when to move."
                        if is_site
                        else "A shift roster, a few loose words. It adds up."
                    ),
                    advantage_delta=SITE_ADVANTAGE if is_site else NEARBY_ADVANTAGE,
                ),
                failure=Outcome(text="Nothing solid turns up. Wasted time."),
                critical_failure=Outcome(
                    text="Someone clocks you scoping the place.",
                    next_stage=LEGWORK_FIGHT_STAGE,
                ),
            )
        )

    return Scene(
        id=f"legwork_{job.id}",
        title=f"Case the job: {job.title}",
        kind=SceneKind.LEGWORK,
        prepares_for=job.id,
        start_stage="start",
        stages={
            "start": Stage(
                id="start",
                prompt=(
                    f"You have time to work {territory.name} before the job. "
                    f"{faction.name} holds the district through a handful of places."
                ),
                choices=choices,
            ),
            LEGWORK_FIGHT_STAGE: Stage(
                id=LEGWORK_FIGHT_STAGE,
                prompt="",
                choices=[],
                combat=Encounter(
                    prompt=LEGWORK_FIGHT_PROMPT.format(faction=faction.name),
                    enemies=roll_enemies(LEGWORK_FIGHT_TIER, rng),
                    # Winning the fight doesn't hand you the intel you failed to get —
                    # both ways out of here end the legwork with nothing banked.
                    victory=Outcome(text="They stay down. But you're burned here today."),
                    escape=Outcome(text="You lose them three streets over. Nothing learned."),
                ),
            ),
        },
    )
