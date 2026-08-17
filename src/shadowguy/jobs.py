"""Procedural generation of job Scenes offered by Fixers.

The *table* this generates from — the stage model, the nine archetypes, the risk
curve, and everything checked about them at import — is job_archetypes.py. This
module is the pass over it: pick a mark, draw a subset of each stage's pool, price
the result, and build the Scene.
"""

import random
import uuid
from dataclasses import dataclass

from shadowguy.checks import day_tier, resolve_rng
from shadowguy.combat import ENEMY_TIERS, roll_enemies
from shadowguy.corpmap import (
    GENERATED_KINDS,
    LOCATION_SKILL,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    corp_target_territories,
    territory_distance,
)
from shadowguy.factions import FACTIONS_BY_ID, Faction
from shadowguy.job_archetypes import (
    ARCHETYPES,
    FULL_POOL_CHANCE,
    OPTIONAL_STAGE_CHANCE,
    PARTIAL_POOL_SIZE,
    Approach,
    JobArchetype,
    JobStage,
    _role_for_stage,
    archetype_specialist,
)
from shadowguy.matrix import ICE_TIERS, generate_matrix_network
from shadowguy.scene import (
    BurglaryStage,
    Choice,
    Encounter,
    Entrance,
    MatrixStage,
    Outcome,
    Scene,
    SceneKind,
    Stage,
    TacticalStage,
)
from shadowguy.skills import skill_for
from shadowguy.buildings import BuildingKind, generate_building
from shadowguy.tactical_gen import generate_map

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


# Standing lost with the corp you just robbed, on a completed job.
JOB_STANDING_HIT = -2

# Security knocked off the district a completed job hit (scene.Outcome.security_delta,
# applied to Scene.target_territory_id). The third thing a finished job moves, alongside
# standing and fixer trust, and the first with a *corp-mode* consequence: Security is
# half of corp_turn.defense_strength, so working a district is how it gets softened
# before an attack goes in — your own corp's, or whoever else is circling it.
#
# Flat rather than scaled by _payout's multiplier: Security runs 0..MODIFIER_MAX (5), so
# -1 is already a fifth of a district's standing defense and a critical success shouldn't
# strip two. Clamped at 0 by apply_outcome, so grinding the same block forever bottoms
# out rather than going negative.
JOB_SECURITY_HIT = -1

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


# REWARD_BASE prices a job with no complication. One that has one is a longer job
# with an extra check's worth of blood in it, so it pays this much more per extra
# stage — otherwise the fixer board would quietly price identical-looking offers the
# same while one of them is strictly worse.
REWARD_PER_EXTRA_STAGE = 0.3


# A guard's sightline catching a burglary's interior walk (scene.BurglaryStage.spotted)
# costs a flat, modest hit — deliberately on the low end of DAMAGE_FOR_DELTA, since it
# stacks on top of whatever the entrance check already cost. Getting spotted is the
# real punishment (it routes to the job's fight stage, same as any critical failure);
# the health cost alone shouldn't also be as steep as a doubled entrance failure would be.
BURGLARY_SPOTTED_DAMAGE = 2

# A burglary/wetwork entrance's plain failure still pays, at this cut of the full
# reward (see jobs.generate_job's _entrance_failure) -- half of what a clean success
# pays, for getting in messily rather than cleanly.
MESSY_ENTRY_MULTIPLIER = 0.5



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
    the destination territory (CorpMapScreen's "Deliver" action) -- nothing about it
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


@dataclass(frozen=True)
class _JobPlan:
    """Everything about a job that is settled before any one stage is built: which
    archetype it is, who it's against and where, and what a clean run of it pays.

    Exists so the per-stage builders below can be top-level functions instead of
    closures. Every one of them needs the same handful of whole-job facts (the tier's
    reward, the mark's name for the prompt, whether this beat is the last one), and
    the stage loop used to carry them by closing over five nested defs, which is what
    made generate_job 295 lines with no seam in it.

    Frozen and index-addressed: the stage builders ask the plan for stage i's id, the
    id of the stage after it, and the id of the fight beside it, rather than being
    handed three pre-computed strings. That keeps "stage_0..n are contiguous and the
    last one carries the payout" a single rule stated here rather than arithmetic
    repeated in each builder.
    """

    rng: random.Random
    archetype: JobArchetype
    specialist: str | None
    faction: Faction
    territory: Territory
    location: Location
    target: str
    tier: int
    difficulty_base: int
    reward_base: int
    job_stages: tuple[JobStage, ...]

    def stage_id(self, index: int) -> str:
        return f"stage_{index}"

    def fight_id(self, index: int) -> str:
        return f"{self.stage_id(index)}_fight"

    def is_last(self, index: int) -> bool:
        return index == len(self.job_stages) - 1

    def next_stage(self, index: int) -> str | None:
        return None if self.is_last(index) else self.stage_id(index + 1)

    def prompt(self, template: str) -> str:
        """A stage's authored prose, filled in with this job's mark. The fields are
        the ones job_archetypes checks every prompt against at import."""
        return template.format(
            verb=self.archetype.verb,
            faction=self.faction.name,
            territory=self.territory.name,
            location=self.location.name,
            target=self.target,
        )

    def difficulty(self, index: int) -> int:
        """The stage's base difficulty, rolled once for the whole stage: every approach
        is offset from this same number, so an Approach's difficulty_delta means the
        same thing on every job. The ramp is spread across however many stages this job
        turned out to have (STAGE_DIFFICULTY_RAMP)."""
        ramp = (
            round(STAGE_DIFFICULTY_RAMP * index / (len(self.job_stages) - 1)) if index else 0
        )
        return self.difficulty_base + ramp + self.rng.randint(-1, 2)

    def payout(self, text: str, multiplier: float, rep: int, index: int) -> Outcome:
        """What passing stage `index` is worth. Only the last stage pays: everywhere
        else the Outcome just carries the job on to the next one."""
        last = self.is_last(index)
        return Outcome(
            text=text,
            next_stage=self.next_stage(index),
            cash_delta=int(self.reward_base * multiplier) if last else 0,
            experience_delta=int(JOB_XP_BASE[self.tier] * multiplier) if last else 0,
            rep_delta=rep if last else 0,
            standing_delta=JOB_STANDING_HIT if last else 0,
            fixer_trust_delta=FIXER_TRUST_GAIN if last else 0,
            security_delta=JOB_SECURITY_HIT if last else 0,
        )


def _pick_mark(corp_map: CorpMap, rng: random.Random) -> tuple[Territory, Faction, Location]:
    """Who this job is against and where. The mark is a real corp, hit in a district it
    actually holds on this run's map — and one with somewhere in it to actually hit. A
    corp that expands onto a slum holds a district generated with no locations but its
    encampment (see Slums & encampments in DESIGN.md), which would leave the site pick
    empty.

    Never the runner's own place either: if they've bought a safehouse in this corp
    district, it's not a job site (and carries none of the LOCATION_SKILL/legwork tables
    a site needs) — which is what GENERATED_KINDS filters both halves on."""
    territory = rng.choice(corp_target_territories(corp_map))
    location = rng.choice([loc for loc in territory.locations if loc.kind in GENERATED_KINDS])
    return territory, FACTIONS_BY_ID[territory.owner], location


def _approach_failure(plan: _JobPlan, index: int, approach: Approach, text: str) -> Outcome:
    return Outcome(
        text=text,
        health_delta=-approach.failure_damage,
        next_stage=plan.next_stage(index),
        # Only the last stage's plain failure ends the job with nothing to
        # show for it — everywhere else next_stage carries it on, so this
        # is 0 there, same as payout()'s cash/rep/standing.
        fixer_trust_delta=JOB_FAILURE_TRUST_HIT if plan.is_last(index) else 0,
        rep_delta=JOB_FAILURE_REP_HIT if plan.is_last(index) else 0,
    )


def _entrance_failure(plan: _JobPlan, index: int, approach: Approach, text: str) -> Outcome:
    """A burglary/wetwork Entrance's plain failure -- unlike _approach_failure,
    this still pays out (at a reduced rate), because APPROACH is this job's
    *only* stage: it's simultaneously the first check and the one payout()
    calls "last", and every other archetype's last-stage plain failure paying
    nothing is fine *because* it only happens after several earlier stages
    already went well. Here it would happen on the very first roll every time,
    making the walk that follows pointless regardless of how it goes -- and the
    text itself ("you're in") already says the break-in still succeeds, just
    messily, not that the job is blown. A critical failure is still the real
    botched case (_approach_critical_failure): no reward, straight to the fight."""
    outcome = plan.payout(text, MESSY_ENTRY_MULTIPLIER, 0, index)
    outcome.health_delta = -approach.failure_damage
    return outcome


def _approach_critical_failure(plan: _JobPlan, index: int, approach: Approach) -> Outcome:
    # The one branch that doesn't just cost health and carry on: you're
    # made, and they arrive holding the initiative. Note it deals the
    # *plain* failure damage, not the doubled hit a critical used to deal:
    # the fight is the critical failure's punishment, and charging both
    # stacked a double-damage hit under a squad that opens with a free
    # round — which is a nat-1 killing a light build outright.
    return Outcome(
        text="It goes bad, fast. Someone hits the alarm.",
        health_delta=-approach.failure_damage,
        next_stage=plan.fight_id(index),
    )


def _ambush_kwargs(plan: _JobPlan, index: int) -> dict:
    # The guaranteed way through, whatever the pool draw left you: forcing
    # your way in is always loud, so every result routes straight to the
    # fight — same door AMBUSH_LABEL opens on every other stage.
    fight_id = plan.fight_id(index)
    return {
        "label": f"{AMBUSH_LABEL} ({skill_for(AMBUSH_SKILL).name})",
        "skill": AMBUSH_SKILL,
        "difficulty": AMBUSH_DIFFICULTY,
        "success": Outcome(text="You pick your moment.", next_stage=fight_id),
        "failure": Outcome(text="You move too early.", next_stage=fight_id),
        "critical_failure": Outcome(text="You walk straight into them.", next_stage=fight_id),
    }


def _draw_approaches(plan: _JobPlan, job_stage: JobStage) -> list[Approach]:
    """Which ways through this job happens to leave open. Kept in pool order so
    the clean approach still reads before the bloody one."""
    pool = job_stage.approaches
    if plan.rng.random() < FULL_POOL_CHANCE:
        return list(pool)
    if plan.specialist is not None:
        # A specialist job promises its specialist a way through every beat, so the
        # lead — the approach that makes it their job at all — survives the draw and
        # only the rest is sampled. Same draw size as any other partial pool.
        return [
            pool[0],
            *sorted(plan.rng.sample(pool[1:], PARTIAL_POOL_SIZE - 1), key=pool.index),
        ]
    return sorted(plan.rng.sample(pool, PARTIAL_POOL_SIZE), key=pool.index)


def _quiet_stage(plan: _JobPlan, index: int) -> Stage:
    """A vigilance beat where nothing happens. Nothing to click, nothing to fail:
    most legs of an escort should pass without incident rather than forcing a check
    at every stop (see VIGILANCE_THREAT_CHANCE). Decided before any pool/difficulty
    work, since a quiet stage needs none of it -- and no fight beside this stage
    either, since with no roll nothing can ever route to one."""
    return Stage(
        id=plan.stage_id(index),
        prompt="",  # narration carries the prose; a narration stage has no choices
        choices=[],
        narration=plan.payout(VIGILANCE_QUIET_TEXT, 1.0, 1, index),
    )


def _burglary_stage(plan: _JobPlan, index: int, approaches: list[Approach], difficulty: int) -> Stage:
    """A Burglary or Wetwork APPROACH: each approach becomes an Entrance (a
    diagram node, not a list row), landing the runner at a distinct spawn
    inside a freshly generated building — several levels of it, with the
    score (or the target) somewhere inside.

    The building is generated *with the job* and lives inside its Scene: it is
    never a corpmap.Location, so a target adds nothing to the map the player
    walks around, and it goes away when the job is finished or expires.
    Burglary's structure comes from the site's own kind (BURGLARY_STRUCTURE),
    so breaking into a data haven doesn't hand you somebody's bedrooms; Wetwork
    always drops the runner at a COMPOUND (WETWORK_STRUCTURE) regardless of the
    site, since the target is holed up in their own place, not the job site's."""
    job_stage = plan.job_stages[index]
    kind = (
        WETWORK_STRUCTURE
        if plan.archetype.wetwork
        else BURGLARY_STRUCTURE[plan.location.kind]
    )
    building = generate_building(plan.rng, entrance_count=len(approaches), kind=kind)
    entrances = [
        Entrance(
            label=f"{approach.flavor} ({skill_for(approach.skill).name})",
            skill=approach.skill,
            difficulty=difficulty + approach.difficulty_delta,
            spawn=spawn,
            success=plan.payout("It goes clean.", 1.0, 1, index),
            failure=_entrance_failure(plan, index, approach, "It gets messy, but you're in."),
            critical_success=plan.payout(
                "Flawless. Nobody even looks up.", 1.5, 2, index,
            ),
            critical_failure=_approach_critical_failure(plan, index, approach),
        )
        for approach, spawn in zip(approaches, building.entrance_spawns, strict=True)
    ]
    entrances.append(Entrance(spawn=building.objective, **_ambush_kwargs(plan, index)))
    return Stage(
        id=plan.stage_id(index),
        prompt="",  # the BurglaryStage carries the prose; a burglary stage has no choices
        choices=[],
        burglary=BurglaryStage(
            prompt=plan.prompt(job_stage.prompt),
            entrances=tuple(entrances),
            building=building,
            bailed=Outcome(
                text="You back out the way you came, empty-handed.",
                health_delta=-BURGLARY_SPOTTED_DAMAGE,
                next_stage=plan.fight_id(index),
            ),
            guard=plan.rng.choice(roll_enemies(plan.tier, plan.rng)),
        ),
    )


def _choice_stage(plan: _JobPlan, index: int, approaches: list[Approach], difficulty: int) -> Stage:
    """The ordinary beat: the drawn pool as a list of Choices, plus the ambush."""
    choices = [
        Choice(
            label=f"{approach.flavor} ({skill_for(approach.skill).name})",
            skill=approach.skill,
            difficulty=difficulty + approach.difficulty_delta,
            success=plan.payout("It goes clean.", 1.0, 1, index),
            failure=_approach_failure(plan, index, approach, "It gets messy, but you push on."),
            critical_success=plan.payout(
                "Flawless. You walk out with more than you bargained for.", 1.5, 2, index,
            ),
            critical_failure=_approach_critical_failure(plan, index, approach),
        )
        for approach in approaches
    ]
    choices.append(Choice(**_ambush_kwargs(plan, index)))
    return Stage(
        id=plan.stage_id(index),
        prompt=plan.prompt(plan.job_stages[index].prompt),
        choices=choices,
    )


def _fight_stage(plan: _JobPlan, index: int) -> Stage:
    """The fight beside every stage, reached by the ambush choice or a critical
    failure. Both Outcomes are the same whether it's a grid set-piece or an ICE
    run — only where they're packaged (and who turns up) differs. A matrix job
    fields ICE and no gunmen, so roll_enemies isn't called for it (nobody's in
    the building), and its "escape" is being ejected."""
    fight_id = plan.fight_id(index)
    escape = Outcome(
        text="You get out with your skin. The job is blown.",
        fixer_trust_delta=JOB_FAILURE_TRUST_HIT,
        rep_delta=JOB_FAILURE_REP_HIT,
    )
    if plan.archetype.matrix:
        return Stage(
            id=fight_id,
            prompt="",  # the MatrixStage carries the prose; a fight stage has no choices
            choices=[],
            matrix=MatrixStage(
                prompt=MATRIX_FIGHT_PROMPT.format(
                    faction=plan.faction.name, location=plan.location.name
                ),
                network=generate_matrix_network(plan.tier, plan.rng),
                victory=plan.payout("You seize the data and the ICE goes dark.", 1.0, 1, index),
                escape=escape,
            ),
        )
    enemies = roll_enemies(plan.tier, plan.rng)
    tac = generate_map(
        plan.rng, len(enemies), cover_density=_cover_density(plan.location.kind)
    )
    return Stage(
        id=fight_id,
        prompt="",  # the TacticalStage carries the prose
        choices=[],
        tactical=TacticalStage(
            prompt=FIGHT_PROMPT.format(faction=plan.faction.name, location=plan.location.name),
            grid=tac.grid,
            player_start=tac.player_start,
            enemies=tuple(zip(enemies, tac.enemy_spawns, strict=True)),
            victory=plan.payout("They stop coming. You finish what you came for.", 1.0, 1, index),
            escape=escape,
            exits=tac.exits,
        ),
    )


def generate_job(
    day: int,
    corp_map: CorpMap,
    fixer_id: str,
    rng: random.Random | None = None,
    archetype: JobArchetype | None = None,
) -> tuple[Scene, JobTiming]:
    rng = resolve_rng(rng)
    # archetype is forced by the Test menu (screens/menu_screens.py) to play a
    # specific one on demand; every real caller (fixer.py) leaves it None and gets
    # the normal random draw.
    if archetype is None:
        archetype = rng.choice(ARCHETYPES)
    territory, faction, location = _pick_mark(corp_map, rng)
    # Drawn here rather than inside the _JobPlan below so the rng is consumed in the
    # same order it always was — the optional-stage rolls come after the mark.
    target = rng.choice(TARGETS)
    tier = _tier_for_day(day)
    # Which beats this job actually has. An optional stage that doesn't make the cut
    # is gone before any ids are handed out, so stage_0..n stay contiguous.
    job_stages = tuple(
        stage
        for stage in archetype.stages
        if stage.type not in OPTIONAL_STAGE_CHANCE
        or rng.random() < OPTIONAL_STAGE_CHANCE[stage.type]
    )
    mandatory = sum(1 for s in archetype.stages if s.type not in OPTIONAL_STAGE_CHANCE)
    extra_stages = len(job_stages) - mandatory
    plan = _JobPlan(
        rng=rng,
        archetype=archetype,
        specialist=archetype_specialist(archetype),
        faction=faction,
        territory=territory,
        location=location,
        target=target,
        tier=tier,
        difficulty_base=DIFFICULTY_BASE[tier],
        reward_base=int(REWARD_BASE[tier] * (1 + REWARD_PER_EXTRA_STAGE * extra_stages)),
        job_stages=job_stages,
    )

    stages: dict[str, Stage] = {}
    for index, job_stage in enumerate(job_stages):
        if archetype.vigilance and rng.random() >= VIGILANCE_THREAT_CHANCE:
            stages[plan.stage_id(index)] = _quiet_stage(plan, index)
            continue
        approaches = _draw_approaches(plan, job_stage)
        difficulty = plan.difficulty(index)
        build = _burglary_stage if job_stage.burglary else _choice_stage
        stages[plan.stage_id(index)] = build(plan, index, approaches, difficulty)
        stages[plan.fight_id(index)] = _fight_stage(plan, index)

    return (
        Scene(
            id=f"job_{uuid.uuid4().hex[:8]}",
            title=f"{archetype.name}: {faction.name} ({territory.name})",
            kind=SceneKind.JOB,
            hours_cost=archetype.hours_cost if archetype.hours_cost is not None else (8 if tier == 0 else 12),
            start_stage=plan.stage_id(0),
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
        ),
        _random_timing(day, rng),
    )


# How each kind of place is scouted, in flavor text. The skill itself lives in
# corpmap.LOCATION_SKILL — that's also what corpmap_gen._location_kinds reads to
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
