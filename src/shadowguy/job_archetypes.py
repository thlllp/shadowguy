"""What a job is made of, before any of it is rolled: the stage model
(StageType/Approach/JobStage/JobArchetype), the nine authored archetypes, the risk
curve an approach's damage falls out of, and everything checked about the table at
import.

Split out of jobs.py, which is now purely the *generation* pass over this — pick a
mark, draw a subset of each pool, price it, build the Scene. The table is a third of
what jobs.py was and changes for entirely different reasons than the generator does:
adding a Bodyguard row is authoring, changing PARTIAL_POOL_SIZE's draw is design.

Imports character/skills/scene and nothing else from the package; jobs.py imports
*it* and never the other way. `scene` is a legal edge here for the same reason it is
in jobs.py — Role/Posture are plain data and scene never imports back.
"""

from dataclasses import dataclass
from enum import StrEnum

from shadowguy.character import CORE_STATS
from shadowguy.scene import Posture, Role
from shadowguy.skills import skill_for

# A stage offers a subset of its pool, not the whole thing: how many ways in this
# particular job happens to have is part of what makes one offer better than another.
# FULL_POOL_CHANCE of the time you get every approach; otherwise you get exactly
# PARTIAL_POOL_SIZE of them — an exact count, not a floor, so widening a pool adds
# approaches the full-pool roll can reach but does not make the partial draw any
# wider. It doubles as the minimum pool size, since a pool must have at least this
# many to draw from (guarded at import).
PARTIAL_POOL_SIZE = 2
FULL_POOL_CHANCE = 0.35


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
# Every archetype walks APPROACH -> OBJECTIVE -> (COMPLICATION) -> EXFIL, except
# Burglary and Wetwork, whose only stage is APPROACH: the rest of the beat (the
# vault, the sensor, getting out) plays out inside the BurglaryStage's own building
# instead of as separate Choice stages after it (see their own rows' comments). The
# complication is optional (OPTIONAL_STAGE_CHANCE), so an ordinary job is 3 or 4
# stages, and it is where the job turns on you rather than merely resisting you.
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
                    ("lift", -2, "Put the case through with a wrecking bar"),
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
                    ("blades", -2, "Cut through the cordon"),
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
                    ("grapple", -2, "Put down whoever is closest and keep moving"),
                ),
            ),
        ),
    ),
    (
        # A specialist job: every beat leads with an logic skill, which is what
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
                    ("lift", -2, "Pull the drive out of the rack and take it with you"),
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
        # A second specialist job, and (like Burglary) a single-stage one: its only
        # beat is the APPROACH, played out as a BurglaryStage (the `burglary` flag
        # below, set at ARCHETYPES construction) -- entrance diagram, then an
        # interior walk to the target, always inside a private COMPOUND regardless
        # of the job site's own kind (WETWORK_STRUCTURE), so this stage's flavor
        # strings are short entrance captions, not sentences, same as Burglary's.
        # Reaching the target *is* the whole job -- there is no separate
        # objective/complication/exfil beat once you're in, the way there used to
        # be: the building's own guards, cameras and locked doors already carry
        # that risk, and a second helping of abstract choices after the walk read
        # as unrelated to the building you just broke into. Where Intrusion is the
        # Netrunner's quiet way through a system, Wetwork is the Solo's loud way
        # through people -- grapple's lead (strength) is what archetype_specialist()
        # reads as this job's contract. The other two approaches sit on different
        # stats, so it's a job a Netrunner or Infiltrator can take and bleed through
        # rather than one they're locked out of.
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
        ),
    ),
    (
        # A generic archetype, same as Heist/Extraction/Sabotage (mixed stat leads,
        # no specialist) and, like Wetwork, a single-stage archetype: the ARCHETYPES
        # comprehension below flags this row's lone APPROACH stage burglary=True,
        # which tells generate_job to build it as a scene.BurglaryStage (an entrance
        # diagram, then an interior walk) instead of a Choice list -- see jobs.py's
        # generate_job and screens/burglary_screens.py. Reaching the target inside
        # is the whole job; there is deliberately no separate objective/complication/
        # exfil beat after the walk (there used to be one of each) -- the vault lock,
        # the motion sensor, and getting back out are already what the building's
        # locked doors, cameras and guarded exits *are*, so a second round of
        # abstract choices once you're already inside just read as disconnected from
        # the building you broke into. archetype_specialist() special-cases this row
        # (see its own docstring): with only one stage left, its lead alone would
        # otherwise misread as a specialist's contract, when the pool is deliberately
        # mixed-stat. The APPROACH row's flavor strings are deliberately short node
        # captions ("Front Door"), not sentences like every other row's -- they
        # become the diagram's labels, not a line in a list, and that's the one place
        # this table departs from the others' voice.
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
                    ("toughness", -2, "Crash the node and rip the data as it falls"),
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
                    ("blades", -2, "Put down whoever's still following and keep walking"),
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
    "logic": "Netrunner",
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

    Burglary is special-cased ahead of the derivation below: it's down to a single
    stage (see its own row's comment), so "every stage's lead agrees" would otherwise
    trivially agree with itself and misread deliberately mixed-stat work (forgery's
    lead is on `cool`) as an Infiltrator contract. Wetwork needs no such carve-out —
    its own lone stage's lead (`grapple`, strength) already agrees with what every one
    of its stages led with before this collapsed to one.
    """
    if archetype.name == "Burglary":
        return None
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
