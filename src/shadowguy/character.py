from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shadowguy.cybernetics import (
    CYBERWARE_CATALOG,
    CyberSlot,
    free_humanity,
    installed_bonus,
    installed_skill_bonus,
)
from shadowguy.inventory import equipped_bonus, equipped_skill_bonus
from shadowguy.runners import RUNNERS_BY_ID, can_work_support, recruit_wage
from shadowguy.shops import ITEMS_BY_ID, InventoryItem, Item, fits_in_slot
from shadowguy.skills import SKILLS, skill_for, skill_value

if TYPE_CHECKING:
    from shadowguy.fixer import JobOffer
    from shadowguy.jobs import SmugglingJob
    from shadowguy.scene import Scene
    from shadowguy.security import SecurityContract

BASE_HEALTH = 10
HEALTH_PER_BODY = 5
# Length of a day for Character.day's derivation from elapsed_hours (below) — also
# how many hours a hospital stay spends, deterministically advancing the day by
# exactly one (see app.spend_time). Rest is shorter — see REST_HOURS_COST.
HOURS_PER_DAY = 24

# How long a Rest action spends (app.ShadowguyApp.rest) — one sleep "chunk". The
# runner is meant to take one of these every FATIGUE_GRACE_HOURS, wherever they are,
# not just at midnight — see Character.fatigue.
REST_HOURS_COST = 8

# How long the runner can go since their last rest before fatigue starts
# accumulating (Character.on_new_day). 24, not tied to HOURS_PER_DAY by name since
# they mean different things: this is "time since you last slept", not "a calendar
# day" — they happen to share a value.
FATIGUE_GRACE_HOURS = 24
# Fatigue compounds while overdue rather than climbing by a flat step each day: the
# growth added each overdue day-tick is 1 plus a fraction of the fatigue already
# built up, so a runner who's already run down burns out faster. A rest only halves
# the accumulated total (Character.rest note in app.py) rather than clearing it, so
# a bad stretch leaves the runner worse for a few more rests, not just one bad night.
FATIGUE_GROWTH_DIVISOR = 3
# Cap on how much fatigue can subtract from a stat (Character.stat) — the raw
# counter can still climb higher than this while overdue (and takes longer to
# fully work off via halving), but the felt penalty stops getting worse.
FATIGUE_STAT_PENALTY_CAP = 3

# Everything starts at 1 and is bought up from there. Both pools are spent at
# character creation (app.CharacterCreationScreen) and never refill — the build
# you walk in with is the build you have on day one. Later growth (Character.
# experience, gained from completed jobs) is a separate pool entirely, spent
# post-creation via spend_experience_on_stat/spend_experience_on_skill rather
# than topping either of these back up.
STARTING_STAT = 1
STARTING_SKILL_RANK = 1
STARTING_STAT_POINTS = 6
STARTING_SKILL_POINTS = 20
# What one creation skill point is worth as gear, via convert_skill_point_to_gear. A
# build's third spend: ranks, stats, or walking in already equipped. The catalog runs
# 80-1600 an item, so one point is roughly *one thing* -- a weapon or a torso piece, not
# a whole kit. Deliberately that granular: at a coarser rate a single point covered a
# runner head to toe and the choice stopped being a choice, and the leftover (which is
# burned, not banked -- see discard_gear_budget) was large enough to feel like a penalty
# for not min-maxing the shopping list.
GEAR_EB_PER_POINT = 1000

# A little walking-around money so the first nights' lodging (corpmap.lodging_cost)
# don't strand a fresh runner before their first payday. Not part of the build — the
# creation screen spends points, not cash — so reset_build() leaves it alone.
STARTING_CASH = 100
# Ceiling on a single skill's rank.
MAX_SKILL_RANK = 10

# Ranks get dearer the higher you climb: (lowest rank, highest rank, points per rank).
# Buying one skill all the way from STARTING_SKILL_RANK to MAX_SKILL_RANK costs
# 3*1 + 3*2 + 2*3 + 4 = 19 of the 20 points, so a specialist has almost nothing left
# over — that's the trade, not an accident.
_RANK_COST_ROWS = (
    (2, 4, 1),
    (5, 7, 2),
    (8, 9, 3),
    (10, 10, 4),
)
# rank -> points to *reach* that rank from the one below it.
SKILL_RANK_COST = {
    rank: cost for low, high, cost in _RANK_COST_ROWS for rank in range(low, high + 1)
}
if set(SKILL_RANK_COST) != set(range(STARTING_SKILL_RANK + 1, MAX_SKILL_RANK + 1)):
    raise ValueError("SKILL_RANK_COST must price exactly every rank above STARTING_SKILL_RANK")

# The stats a skill can be layered on, and what gear/chem bonuses apply to. Checks
# roll a *skill* (skills.skill_value), never one of these on its own.
CORE_STATS = ("body", "strength", "agility", "perception", "logic", "cool")
# Every runner's starting Humanity *ceiling*. Unlike the core stats it has no skills
# and no gear/temp bonus. It is no longer fixed: every operation at a ripperdoc
# permanently shaves SURGERY_SCARRING off it (cybernetics.install_cyberware /
# remove_cyberware), so a runner who churns their loadout ends the run with less room
# than they started with.
#
# Read `Character.humanity` for the ceiling and `cybernetics.free_humanity` for what's
# actually left of the person -- ceiling minus everything currently installed. That
# second number is the one that matters: it drives humanity_penalty below, and hitting
# 0 is cyberpsychosis (the run ends, see screens/shop_screens.py's RipperdocScreen).
HUMANITY_BASELINE = 6

# Permanent Humanity lost per trip under the knife -- charged on install *and* on
# removal, since both are surgery. Small on purpose relative to any single implant's
# humanity_cost (the cheapest is 0.5), which is what makes pulling chrome back out a
# real net *rebound* rather than a second punishment: you get the implant's whole cost
# back and pay only the scar. What it does mean is that swapping loadouts repeatedly
# grinds you down with nothing to show for it.
#
# 0.1 is sized so a full four-slot loadout still fits with room to breathe: the cheapest
# piece in every slot costs 4.0, four installs scar 0.4, so a fully chromed runner sits
# at 1.6 of a ceiling of 5.6 -- one penalty step, not the cap. Fully chromed should read
# as diminished, not as impossible. Not balance-simulated.
SURGERY_SCARRING = 0.1

# Lower bound of free Humanity for each stat penalty step: at or above the first entry
# there's no penalty, below the last the penalty caps. Mirrors FATIGUE_STAT_PENALTY_CAP's
# shape -- a felt penalty that stops getting worse -- except this one bottoms out in a
# run-ending threshold rather than merely a cap, since 0 free Humanity is nobody left.
#
# The band placement is load-bearing, not decorative: cyberware's whole point is that it
# makes a runner better, so a *first* implant must not cost more than it gives. An
# earlier (4.0, 3.0, 2.0) put a single 3-cost implant straight into -2, which turned a
# +1 Agility piece into a net -1 to Agility and -2 to everything else -- chrome as a
# strict downgrade. Starting the first band at 2.0 leaves the whole shallow end of the
# catalog free of penalty and reserves the drain for a runner who is genuinely most
# machine: the cheapest piece in every slot (4.0 total, plus 0.4 of scarring) lands at
# -1, and only a deep loadout reaches the cap. Not balance-simulated.
HUMANITY_PENALTY_THRESHOLDS = (2.0, 1.0, 0.5)
if list(HUMANITY_PENALTY_THRESHOLDS) != sorted(HUMANITY_PENALTY_THRESHOLDS, reverse=True):
    raise ValueError("HUMANITY_PENALTY_THRESHOLDS must descend")
if HUMANITY_PENALTY_THRESHOLDS[-1] <= 0:
    raise ValueError("HUMANITY_PENALTY_THRESHOLDS must stay above 0 -- 0 ends the run")
STAT_NAMES = frozenset(CORE_STATS) | {"cash", "rep", "humanity"}

# Unlike health (floored at 0 — there's no such thing as negative health), rep can go
# into the red: a blown job or gig now costs it (see scene.apply_outcome, jobs.py's
# fight_escape/last-stage failure, gigs._build_choice), so a runner who keeps failing
# can burn through their good name and come out the other side owing one. -10 is the
# bottom of that hole, not zero — the street still remembers you, just badly.
REP_FLOOR = -10

# The guard lives here, not in skills.py: skills.py has to stay import-free of
# this module (character -> shops -> corpmap -> skills), so this is the only
# place that can see both tables. A skill tied to a stat that doesn't exist
# would otherwise raise from stat() the first time something rolled it.
if any(skill.stat not in CORE_STATS for skill in SKILLS):
    raise ValueError("every Skill.stat must be one of CORE_STATS")

# Same reasoning as the guard above, applied to cybernetics.Cyberware.humanity_cost:
# cybernetics.py can't check its catalog against HUMANITY_BASELINE without importing
# this module back (a cycle), so the check lives here instead.
#
# Two things are checked, and the first is stricter than it used to be. It once only
# asked that every CyberSlot hold *some* affordable piece, which let a single
# unaffordable row hide in a slot that had other options -- exactly what happened to
# adamantium_bones_t4 (Tier 4's 1.6x on a humanity_cost of 4 put it at 6.4, past the
# baseline, so nobody could ever install it). That was invisible while nothing sold
# cyberware; now RipperdocScreen puts every row on a shelf, where an uninstallable
# one is a permanent dead line reading "not enough humanity left". So: every row
# must be installable on its own by a baseline-Humanity runner.
for _cyberware in CYBERWARE_CATALOG:
    # Note the + SURGERY_SCARRING: the operation that fits a piece also scars, so a
    # row costing anything from (HUMANITY_BASELINE - SURGERY_SCARRING) upward passes
    # the naive "does it fit" test and then takes a fresh runner's last 0.1 on the
    # way in -- a shelf row that is instant game over rather than an implant. The
    # bound is >= because landing free Humanity on exactly 0 is already death.
    if _cyberware.humanity_cost + SURGERY_SCARRING >= HUMANITY_BASELINE:
        raise ValueError(
            f"{_cyberware.id}: humanity_cost {_cyberware.humanity_cost} leaves nothing "
            f"after SURGERY_SCARRING against HUMANITY_BASELINE ({HUMANITY_BASELINE}) -- "
            f"installing it would end the run outright"
        )
for _slot in CyberSlot:
    if not any(c.slot is _slot for c in CYBERWARE_CATALOG):
        raise ValueError(f"no cyberware in {_slot}")


@dataclass
class CrewHire:
    """One runner engaged on your crew, and on what terms. `job_id` set = signed for that
    single accepted job (they take runners.RivalRunner.job_cut of its payout, and the
    engagement ends when the job does); `job_id` None = kept on indefinitely (charged
    runners.RivalRunner.daily_cost every day tick). A runner has at most one live hire."""

    runner_id: str
    job_id: str | None = None
    # Whether this hire works the job in person or as remote/support cover. Read against
    # that job's Scene.max_on_site/max_support at hire_for_job time; meaningless for an
    # indefinite hire (job_id None), which no job's roster cap ever applies to.
    on_site: bool = True


@dataclass
class Character:
    name: str
    body: int = STARTING_STAT
    strength: int = STARTING_STAT
    agility: int = STARTING_STAT
    perception: int = STARTING_STAT
    logic: int = STARTING_STAT
    cool: int = STARTING_STAT
    cash: int = STARTING_CASH
    rep: int = 0
    # The Humanity *ceiling*, worn down permanently by surgery (SURGERY_SCARRING).
    # Float rather than int since a scar is 0.1. Read cybernetics.free_humanity for
    # what's actually left of the runner -- this minus everything installed.
    humanity: float = HUMANITY_BASELINE
    health: int | None = None
    # Accumulated non-lethal stun damage (combat.py's weapon-side stun_damage lands
    # here via _stun_player). Persists across fights and screens, unlike the old
    # per-CombatState counter it replaced — a rest clears it outright (mark_rested),
    # not merely halved like fatigue, so it's a real meter but not a punishing one.
    stun: int = 0
    # The run's clock: a continuous count of in-game hours spent, never reset.
    # Character.day (below) is derived from this rather than stored — see
    # app.spend_time, the single chokepoint that advances it.
    elapsed_hours: float = 0.0
    # Which Territory of the corp map the runner is standing in.
    location_id: str = ""
    advantage: dict[str, int] = field(default_factory=dict)
    standing: dict[str, int] = field(default_factory=dict)
    # Trust with a specific Fixer (fixer.Fixer.id), separate from standing (which is
    # per-faction) and rep (which is global). Only completed jobs move it, same rule
    # as standing — see jobs.FIXER_TRUST_GAIN.
    fixer_trust: dict[str, int] = field(default_factory=dict)
    # Standing with a specific LocalCharacter (corpmap.LocalCharacter.id) — the people
    # who run/haunt a Location. Moved by that location's gigs (Outcome.local_standing_delta)
    # and read by shop pricing. Direct and one-person, like fixer_trust; no rival effect.
    local_standing: dict[str, int] = field(default_factory=dict)
    # Standing with a street Gang (gangs.Gang.id), separate from the corp `standing` above.
    # Nothing moves it into the red yet; when it's there, walking onto that gang's turf
    # (corpmap.Territory.gang_id) can cost a toll or a fight — see encounters.py.
    gang_standing: dict[str, int] = field(default_factory=dict)
    # Fixer ids (fixer.Fixer.id) whose location the runner has stood in at least
    # once. A fixer's presence on the corp map is hidden until discovered this way
    # — see fixer.discover_fixers_here(), the single place that reveals it.
    discovered_fixers: set[str] = field(default_factory=set)
    # Runner ids (runners.RUNNERS_BY_ID) whose number the player has actually gotten —
    # granted by shop_screens.BarScreen when the player runs into that runner drinking
    # at a bar (rivals.RunnerActivity.DRINKING), not just by knowing they exist.
    # Recruiting a runner onto the crew is gated on this.
    known_runners: set[str] = field(default_factory=set)
    # Runner ids (runners.RUNNERS_BY_ID) the runner has hired at a bar. Assigning them to
    # a job's roles (with the one-remote-support cap) is a later increment.
    crew: list[CrewHire] = field(default_factory=list)
    # Runners who went down on a job and didn't walk away from it (see
    # tactical.resolve_downed_crew). Killed is permanent for the run — the roster is
    # three people, and a hire dying because you left them bleeding should mean
    # something. Arrested holds the day they're back on the street, so they reappear at
    # bars on their own; both are read through runner_available(), the one place a
    # roster listing or a rival's daily turn asks whether a runner is around at all.
    dead_runners: set[str] = field(default_factory=set)
    arrested_runners: dict[str, int] = field(default_factory=dict)  # runner id -> day they're free
    accepted_jobs: list["JobOffer"] = field(default_factory=list)
    # Accepted multi-night guard contracts (security.py) — a standing engagement, not
    # a Scene: resolved one night at a time by MainMenu's end-day handler while
    # location_id matches a contract's territory_id, not by "running" it like a job.
    security_contracts: list["SecurityContract"] = field(default_factory=list)
    # Owned items, ids from shops.ITEMS_BY_ID. Duplicates allowed (same item bought twice).
    # Only entries with equipped=True contribute their bonus via stat().
    inventory: list[InventoryItem] = field(default_factory=list)
    # CyberSlot -> installed cyberware id (cybernetics.CYBERWARE_BY_ID), one piece per
    # slot. Unlike inventory there's no equipped flag -- cyberware is surgically
    # installed, not worn, so whatever's here is always active. Filled by
    # cybernetics.install_cyberware, sold by screens.RipperdocScreen's clinic. What's
    # in here is subtracted from the Humanity ceiling above to give free_humanity --
    # so this dict is also, directly, how much of the runner is left.
    installed_cyberware: dict[CyberSlot, str] = field(default_factory=dict)
    # Owned consumables, ids from shops.CONSUMABLES_BY_ID. Duplicates allowed.
    # Removed (via inventory.use_consumable) once used, unlike persistent gear.
    consumables: list[str] = field(default_factory=list)
    # Program ids (shops.PROGRAMS_BY_ID) bought into the runner's owned pool. Not
    # installed on any deck by itself — see inventory.install_program/InventoryItem.
    # installed_programs. Mirrors discovered_fixers' shape: a set of owned ids.
    owned_programs: set[str] = field(default_factory=set)
    # stat name -> bonus from a used Chem, active until the next rest().
    temp_bonuses: dict[str, int] = field(default_factory=dict)
    # skill id (shadowguy.skills.SKILLS_BY_ID) -> rank. Every skill starts at
    # STARTING_SKILL_RANK, so the dict is fully populated rather than sparse.
    skill_ranks: dict[str, int] = field(
        default_factory=lambda: {skill.id: STARTING_SKILL_RANK for skill in SKILLS}
    )
    # Unspent creation points. spend_stat_point()/spend_skill_point() draw these down;
    # nothing puts them back.
    stat_points: int = STARTING_STAT_POINTS
    skill_points: int = STARTING_SKILL_POINTS
    # Creation-only eb, bought with skill points (convert_skill_point_to_gear) and
    # spendable only on gear. Never cash: whatever is left over when the run starts is
    # burned (discard_gear_budget), so this can't be used to print starting money.
    gear_budget: int = 0
    # Item ids bought with that budget, so reset_build can take them back out of the
    # inventory again -- a point spent on a vest is a point spent, and undoing the build
    # has to undo it. Item ids rather than InventoryItem so a duplicate buy is two entries.
    creation_gear: list[str] = field(default_factory=list)
    # Earned through play (currently: completed jobs only, see jobs.JOB_XP_BASE and
    # Outcome.experience_delta) and spent post-creation via spend_experience_on_stat/
    # spend_experience_on_skill — a single pool that buys either, the player's choice
    # each time, unlike the two separate creation-time pools above.
    experience: int = 0
    # Runner ids (runners.RUNNERS_BY_ID) -> XP earned while on this character's crew for
    # a completed job. Not split with the player's own experience (see gain_experience) —
    # every crew member on the job gets the full amount too. No spend path exists for a
    # hired runner yet (they have no stat/skill sheet of their own to spend it on) — this
    # is the mechanism, waiting on a driver, the same pattern gang_standing predated
    # encounters.py.
    crew_experience: dict[str, int] = field(default_factory=dict)
    # Whether a Health Kit has already been used today. A kit is a small emergency
    # top-up, not a stack you burn to full — one per day (inventory.use_consumable enforces
    # it), cleared on on_new_day(). Real recovery is time in a hospital ward.
    health_kit_used_today: bool = False
    # elapsed_hours at the runner's last completed Rest (or hospital stay) — see
    # app.rest(). Starts at 0 so a fresh character is assumed to have just slept.
    last_rest_hour: float = 0.0
    # Accumulated burnout from skipping rest — see FATIGUE_GROWTH_DIVISOR and
    # Character.stat(). A stored, sticky counter rather than something derived fresh
    # from last_rest_hour each time, since a rest only halves it (app.rest()) instead
    # of clearing it outright.
    fatigue: int = 0
    # A gang delivery in progress (jobs.SmugglingJob) -- one at a time. Picked up in
    # person at a gang's den (screens.GangDenScreen), delivered in person at
    # smuggling_job.destination_territory_id (MainMenu's "Deliver" action). Missing
    # the deadline (checked in app._apply_day_tick) clears it and costs gang standing
    # instead of gaining it.
    smuggling_job: "SmugglingJob | None" = None
    # Hour of day (0-23) the Phone's Alarm Clock tab is set to wake at, or None when
    # no alarm is set -- see app.rest(), the only place this is read (and cleared
    # once used).
    alarm_hour: int | None = None

    def __post_init__(self) -> None:
        if self.health is None:
            self.health = self.max_health

    def advantage_for(self, job_id: str) -> int:
        return self.advantage.get(job_id, 0)

    def add_advantage(self, job_id: str, amount: int) -> None:
        self.advantage[job_id] = self.advantage_for(job_id) + amount

    def consume_advantage(self, job_id: str) -> int:
        return self.advantage.pop(job_id, 0)

    @staticmethod
    def _adjust_dict(d: dict[str, int], key: str, delta: int) -> None:
        d[key] = d.get(key, 0) + delta

    def standing_with(self, faction_id: str) -> int:
        return self.standing.get(faction_id, 0)

    def adjust_standing(self, faction_id: str, delta: int) -> None:
        self._adjust_dict(self.standing, faction_id, delta)

    def trust_with(self, fixer_id: str) -> int:
        return self.fixer_trust.get(fixer_id, 0)

    def adjust_fixer_trust(self, fixer_id: str, delta: int) -> None:
        self._adjust_dict(self.fixer_trust, fixer_id, delta)

    def local_standing_with(self, character_id: str) -> int:
        return self.local_standing.get(character_id, 0)

    def adjust_local_standing(self, character_id: str, delta: int) -> None:
        self._adjust_dict(self.local_standing, character_id, delta)

    def gang_standing_with(self, gang_id: str) -> int:
        return self.gang_standing.get(gang_id, 0)

    def adjust_gang_standing(self, gang_id: str, delta: int) -> None:
        self._adjust_dict(self.gang_standing, gang_id, delta)

    def discover_fixer(self, fixer_id: str) -> None:
        self.discovered_fixers.add(fixer_id)

    def knows_runner(self, runner_id: str) -> bool:
        return runner_id in self.known_runners

    def meet_runner(self, runner_id: str) -> None:
        self.known_runners.add(runner_id)

    def on_crew(self, runner_id: str) -> bool:
        return any(hire.runner_id == runner_id for hire in self.crew)

    def hire_indefinite(self, runner_id: str) -> None:
        if not self.on_crew(runner_id):
            self.crew.append(CrewHire(runner_id=runner_id))

    def hire_for_job(self, runner_id: str, job_id: str, on_site: bool = True) -> bool:
        """Sign a runner on for a single job, on-site or as support. Returns whether the
        hire actually went through -- False if already on the crew, if that job's roster
        (Scene.max_on_site/max_support) has no room left for that posture, or if they
        can't work support at all and that's the posture asked for.

        Support is a *role*, not merely a placement: a solo across the street does
        nothing, so runners.can_work_support gates it (Netrunners today, Riggers when
        they exist) rather than letting the player pay a cut for an empty comm link."""
        if self.on_crew(runner_id) or not self.job_roster_has_room(job_id, on_site):
            return False
        runner = RUNNERS_BY_ID.get(runner_id)
        if not on_site and not (runner and can_work_support(runner)):
            return False
        self.crew.append(CrewHire(runner_id=runner_id, job_id=job_id, on_site=on_site))
        return True

    def _job_scene(self, job_id: str) -> "Scene | None":
        for job in self.accepted_jobs:
            if job.scene.id == job_id:
                return job.scene
        return None

    def job_roster_has_room(self, job_id: str, on_site: bool) -> bool:
        """Whether hire_for_job(..., on_site) would succeed for this job right now -- what
        BarScreen reads to decide which postures to even offer. A job_id not among
        accepted_jobs (a test double, or a job that's since expired) is treated as
        uncapped, same as an archetype whose cap is None."""
        scene = self._job_scene(job_id)
        if scene is None:
            return True
        cap = scene.max_on_site if on_site else scene.max_support
        if cap is None:
            return True
        taken = sum(1 for hire in self.crew_for_job(job_id) if hire.on_site == on_site)
        reserved = 1 if on_site else 0  # the player already fills one on-site slot
        return taken + reserved < cap

    def crew_for_job(self, job_id: str) -> list[CrewHire]:
        return [hire for hire in self.crew if hire.job_id == job_id]

    def discharge_crew(self, runner_id: str) -> None:
        """Take a runner off the crew, whatever their terms were."""
        self.crew = [hire for hire in self.crew if hire.runner_id != runner_id]

    def record_runner_killed(self, runner_id: str) -> None:
        """They died on the job: off the crew and off the roster for the rest of the run."""
        self.discharge_crew(runner_id)
        self.dead_runners.add(runner_id)

    def record_runner_arrested(self, runner_id: str, days: int) -> None:
        """Picked up at the scene: off the crew, and off the roster until `days` from now."""
        self.discharge_crew(runner_id)
        self.arrested_runners[runner_id] = self.day + days

    def runner_available(self, runner_id: str) -> bool:
        """Whether this runner is on the street at all today — not dead, not still held.
        A released runner is dropped from the ledger here rather than on a day tick, so
        nothing has to remember to sweep it."""
        if runner_id in self.dead_runners:
            return False
        free_on = self.arrested_runners.get(runner_id)
        if free_on is None:
            return True
        if self.day < free_on:
            return False
        del self.arrested_runners[runner_id]
        return True

    def crew_working(self, job_id: str) -> list[CrewHire]:
        """Who is on this job at all: the hires signed for it, plus every indefinite hire
        (they're on retainer — they come to all of it). Wider than crew_for_job, which is
        specifically "who takes a cut of *this* payout", and wider than crew_on_site,
        which is who is physically there."""
        return [hire for hire in self.crew if hire.job_id in (None, job_id)]

    def crew_on_site(self, job_id: str) -> list[CrewHire]:
        """Of crew_working, the ones actually standing at the location — the only ones a
        fight can field as bodies.

        `on_site` was write-only until this existed: it's set at hire time, capped per
        job archetype (Scene.max_on_site/max_support) and saved (v49), but the fight
        opened with crew_working and put *everyone* on the map. So a netrunner hired
        explicitly as remote support turned up at the building holding a pistol, which is
        the one thing hiring them as support says they don't do.

        A support hire contributes nothing mechanically yet — they still take a cut
        (crew_for_job) and grant no remote effect. That's a gap to fill, not a reason to
        march them into the firefight.
        """
        return [hire for hire in self.crew_working(job_id) if hire.on_site]

    def crew_support(self, job_id: str) -> list[CrewHire]:
        """Of crew_working, the ones working it from somewhere else — the complement of
        crew_on_site. What they actually do is tactical.Support: the player directs them
        at the building from a side menu, on the hacker's own turn rather than theirs."""
        return [hire for hire in self.crew_working(job_id) if not hire.on_site]

    def pay_crew_wages(self) -> list[str]:
        """Charge each indefinitely-kept crew member their daily wage on a day turnover.
        A runner you can't cover walks off. Returns the names who left, for the caller to
        report. For-job hires aren't on a wage — their cost is the cut, taken at payout."""
        kept: list[CrewHire] = []
        left: list[str] = []
        leadership = skill_value(self, "leadership")
        for hire in self.crew:
            runner = RUNNERS_BY_ID[hire.runner_id]
            wage = recruit_wage(runner, leadership)
            if hire.job_id is not None or self.cash >= wage:
                if hire.job_id is None:
                    self.cash -= wage
                kept.append(hire)
            else:
                left.append(runner.name)
        self.crew = kept
        return left

    @property
    def max_health(self) -> int:
        # Deliberately self.body, not stat("body"): gear strengthens checks,
        # not survivability, so equipping/selling an item never moves max_health.
        return BASE_HEALTH + self.body * HEALTH_PER_BODY

    @property
    def day(self) -> int:
        """1-indexed day derived from elapsed_hours — hour 0-23 is day 1, 24-47 is
        day 2, etc. Not stored: app.spend_time is the only thing that moves the
        clock, and every day-boundary system reads this rather than a counter."""
        return int(self.elapsed_hours // HOURS_PER_DAY) + 1

    @property
    def hour_of_day(self) -> int:
        """The clock hour within the current day (0-23) — the other half of `day`'s
        derivation from elapsed_hours, kept beside it so nothing re-derives this
        independently (see screens.CharacterSheet)."""
        return int(self.elapsed_hours % HOURS_PER_DAY)

    @property
    def is_alive(self) -> bool:
        return self.health > 0

    def adjust_health(self, delta: int) -> None:
        self.health = max(0, min(self.max_health, self.health + delta))

    def adjust_stun(self, delta: int) -> None:
        self.stun = max(0, self.stun + delta)

    def adjust_rep(self, delta: int) -> None:
        self.rep = max(REP_FLOOR, self.rep + delta)

    def add_temp_bonus(self, stat: str, amount: int) -> None:
        self.temp_bonuses[stat] = self.temp_bonuses.get(stat, 0) + amount

    def skill_rank(self, skill_id: str) -> int:
        return self.skill_ranks.get(skill_id, STARTING_SKILL_RANK)

    def skill_gear_bonus(self, skill_id: str) -> int:
        return equipped_skill_bonus(self.inventory, skill_id) + installed_skill_bonus(
            self.installed_cyberware, skill_id
        )

    def at_max_rank(self, skill_id: str) -> bool:
        skill_for(skill_id)
        return self.skill_rank(skill_id) >= MAX_SKILL_RANK

    def next_rank_cost(self, skill_id: str) -> int | None:
        """Points to buy this skill's next rank, or None if it's already maxed."""
        if self.at_max_rank(skill_id):
            return None
        return SKILL_RANK_COST[self.skill_rank(skill_id) + 1]

    def next_stat_cost(self, name: str) -> int:
        """XP to raise `name` one point above its current value. Escalates the
        higher a stat already climbs -- the same "steadily pricier" shape
        next_rank_cost gives skills -- but has no ceiling: unlike a skill rank,
        a stat is never maxed out, so scarce XP is what does the limiting."""
        if name not in CORE_STATS:
            raise ValueError(f"not a core stat: {name!r}")
        return max(1, getattr(self, name) - STARTING_STAT + 1)

    def _raise_stat(self, name: str) -> None:
        setattr(self, name, getattr(self, name) + 1)
        if name == "body":
            # max_health is derived from body, so buying Body raises the ceiling.
            # Carry current health up with it, or the run starts already wounded.
            self.health += HEALTH_PER_BODY

    def _raise_skill_rank(self, skill_id: str) -> None:
        self.skill_ranks[skill_id] = self.skill_rank(skill_id) + 1

    def spend_skill_point(self, skill_id: str) -> bool:
        skill_for(skill_id)  # unknown id: raise rather than burn points on a junk rank
        cost = self.next_rank_cost(skill_id)
        if cost is None or cost > self.skill_points:
            return False
        self.skill_points -= cost
        self._raise_skill_rank(skill_id)
        return True

    def spend_stat_point(self, name: str) -> bool:
        if name not in CORE_STATS:
            raise ValueError(f"not a core stat: {name!r}")
        if self.stat_points <= 0:
            return False
        self.stat_points -= 1
        self._raise_stat(name)
        return True

    def convert_skill_point_to_gear(self) -> bool:
        """Trade one creation skill point for GEAR_EB_PER_POINT of gear budget.

        The third thing a build can spend its 20 points on, beside ranks: walk in with a
        weapon and a vest instead of knowing how to use them. Deliberately *not* capped —
        a runner may pour all 20 in — because the trade caps itself twice over. Ranks are
        the to-hit half of every fight ("weapons are the damage, skills are the hit"), so
        a runner who bought a katana and no Blades swings at skill_value 2 and misses
        nearly everything; and armor can't simply be stacked, since shops.SLOT_CAPACITY
        limits what can be worn at once. Gear with no ranks behind it is a bad build, not
        a strong one, which is what makes the exchange rate safe without a ceiling.

        `gear_budget` is not cash and never becomes cash — see discard_gear_budget.
        """
        if self.skill_points <= 0:
            return False
        self.skill_points -= 1
        self.gear_budget += GEAR_EB_PER_POINT
        return True

    def discard_gear_budget(self) -> int:
        """Drop whatever gear budget went unspent, at the moment the run begins.

        The rule that makes the conversion a *gear* purchase rather than a way to print
        starting money: leftover eb is burned, not banked. Without this a build could
        convert all 20 points and walk in with 50,000 cash, which is a different (and much
        worse) game than walking in with 50,000 of kit they can't shoot straight with.
        Returns what was lost, so the creation screen can say so out loud.
        """
        lost, self.gear_budget = self.gear_budget, 0
        return lost

    def buy_creation_gear(self, item: "Item") -> bool:
        """Spend gear budget (never cash) on one catalog item at creation.

        Mirrors shops.buy_item's auto-equip rule -- equipped if the slot has room, stowed
        otherwise -- so a runner who buys three torso pieces gets the same behaviour they
        would at a shop. Standing gates don't apply: there's nobody to have standing with
        before the run starts, so a min_standing item simply isn't offered.
        """
        if item.min_standing or item.price > self.gear_budget:
            return False
        self.gear_budget -= item.price
        self.inventory.append(InventoryItem(item.id, equipped=fits_in_slot(self.inventory, item)))
        self.creation_gear.append(item.id)
        return True

    def refund_creation_gear(self, item_id: str) -> bool:
        """Put one creation purchase back, at full price -- nothing has happened to it yet.

        Only reverses a *creation* buy (creation_gear), so it can never be used to sell
        off starting kit an archetype granted after the run has begun: the creation screen
        is the only thing that calls it, and discard_gear_budget zeroes the budget the
        moment the run starts.
        """
        if item_id not in self.creation_gear:
            return False
        entry = next((e for e in reversed(self.inventory) if e.item_id == item_id), None)
        if entry is None:
            return False
        self.inventory.remove(entry)
        self.creation_gear.remove(item_id)
        self.gear_budget += ITEMS_BY_ID[item_id].price
        return True

    def spend_experience_on_skill(self, skill_id: str) -> bool:
        """Post-creation counterpart to spend_skill_point: same cost curve
        (next_rank_cost), paid out of `experience` instead of the one-shot
        creation pool."""
        skill_for(skill_id)
        cost = self.next_rank_cost(skill_id)
        if cost is None or cost > self.experience:
            return False
        self.experience -= cost
        self._raise_skill_rank(skill_id)
        return True

    def spend_experience_on_stat(self, name: str) -> bool:
        """Post-creation counterpart to spend_stat_point: costed by
        next_stat_cost (escalating) instead of creation's flat 1 point, paid
        out of `experience`."""
        cost = self.next_stat_cost(name)
        if cost > self.experience:
            return False
        self.experience -= cost
        self._raise_stat(name)
        return True

    def gain_experience(self, amount: int) -> None:
        self.experience += amount

    def grant_crew_experience(self, runner_id: str, amount: int) -> None:
        self._adjust_dict(self.crew_experience, runner_id, amount)

    def reset_build(self) -> None:
        """Undo every point spent at creation. The creation screen's only way back."""
        for stat in CORE_STATS:
            setattr(self, stat, STARTING_STAT)
        self.skill_ranks = {skill.id: STARTING_SKILL_RANK for skill in SKILLS}
        self.stat_points = STARTING_STAT_POINTS
        self.skill_points = STARTING_SKILL_POINTS
        # Gear bought at creation goes back with the points that paid for it -- a reset
        # is "undo every point spent", and a point spent on a vest is one of them.
        self.gear_budget = 0
        self.inventory = [entry for entry in self.inventory if entry.item_id not in self.creation_gear]
        self.creation_gear = []
        self.health = self.max_health

    def accept_job(self, offer: "JobOffer") -> None:
        self.accepted_jobs.append(offer)

    def remove_job(self, job_scene_id: str) -> None:
        self.accepted_jobs = [job for job in self.accepted_jobs if job.scene.id != job_scene_id]
        self._discharge_orphan_crew()

    def accept_security_contract(self, contract: "SecurityContract") -> None:
        self.security_contracts.append(contract)

    def remove_security_contract(self, contract_id: str) -> None:
        self.security_contracts = [c for c in self.security_contracts if c.id != contract_id]

    def deliver_smuggling_job(self, standing_gain: int) -> "SmugglingJob":
        """Complete the active smuggling_job on a successful delivery: pay out and
        raise gang standing by `standing_gain` (jobs.GANG_JOB_STANDING_GAIN, passed
        in rather than imported -- jobs.py already imports character.py). Caller
        (MainMenu) has already checked location_id == destination_territory_id."""
        job = self.smuggling_job
        self.cash += job.reward_cash
        self.adjust_gang_standing(job.gang_id, standing_gain)
        self.smuggling_job = None
        return job

    def expire_smuggling_job(self, day: int, standing_loss: int) -> "SmugglingJob | None":
        """Called once per day tick (app._apply_day_tick): clears smuggling_job once
        its deadline has passed, dropping gang standing by `standing_loss` (the
        mirror of deliver_smuggling_job's gain) instead of raising it. Returns the
        job that expired, for the caller to notify about, or None if nothing did."""
        job = self.smuggling_job
        if job is None or day <= job.deadline_day:
            return None
        self.smuggling_job = None
        self.adjust_gang_standing(job.gang_id, -standing_loss)
        return job

    def _discharge_orphan_crew(self) -> None:
        """Drop any for-job hire whose job is no longer accepted (completed, blown, expired).
        An indefinite hire (job_id None) is untouched."""
        active = {job.scene.id for job in self.accepted_jobs}
        self.crew = [hire for hire in self.crew if hire.job_id is None or hire.job_id in active]

    def on_new_day(self, day: int, protect_job_id: str | None = None) -> None:
        """Everything that resets once per day boundary crossed, scoped to Character's
        own state — called once per day by app._apply_day_tick, which owns the parts
        needing corp_map/fixers/rng (crew wages, offer/gig refresh, rival AI, corp
        income) that Character can't reach without an import cycle.

        `day` is the specific day boundary being ticked (not necessarily self.day,
        which is already the final day once a multi-boundary spend has finished
        bumping elapsed_hours) — the same value _apply_day_tick passes to every
        other subsystem this tick.

        `protect_job_id` (a Scene.id) exempts one job from expiry pruning this tick —
        set when the day boundary was crossed by spending time on that very job (or
        its legwork), so running a job doesn't expire the job out from under itself
        the moment its own hours_cost pushes past midnight.
        """
        self.health_kit_used_today = False
        self.temp_bonuses = {}
        if self.elapsed_hours - self.last_rest_hour > FATIGUE_GRACE_HOURS:
            self.fatigue += 1 + self.fatigue // FATIGUE_GROWTH_DIVISOR
        self.accepted_jobs = [
            job
            for job in self.accepted_jobs
            if job.scene.id == protect_job_id or not job.timing.is_expired(day)
        ]
        self._discharge_orphan_crew()

    @property
    def fatigue_penalty(self) -> int:
        """The felt penalty from fatigue -- capped, unlike the raw counter itself
        (see `fatigue`'s field comment). The one place this clamp is computed;
        `stat()` and `screens.CharacterSheet` both read it rather than re-deriving it."""
        return min(FATIGUE_STAT_PENALTY_CAP, self.fatigue)

    @property
    def humanity_penalty(self) -> int:
        """The felt penalty from how little of the runner is left -- one step per
        HUMANITY_PENALTY_THRESHOLDS entry crossed on the way down.

        Reads *free* Humanity (ceiling minus everything installed), not the ceiling:
        chrome you are carrying is the part of you that's gone. So this rises as you
        install and falls back as you pull pieces out, which is the whole point of the
        rebound -- a runner can climb back out of the spiral by going under the knife
        again, paying only SURGERY_SCARRING for the trip.

        The one place this is computed; `stat()` and `screens.CharacterSheet` both read
        it. Deliberately does not raise at 0 free Humanity: that's cyberpsychosis and
        it ends the run, but ending it is the caller's job (RipperdocScreen), not a
        property's."""
        free = free_humanity(self)
        return sum(1 for threshold in HUMANITY_PENALTY_THRESHOLDS if free < threshold)

    def mark_rested(self) -> None:
        """Record a completed rest -- called by app.rest() and a hospital stay alike.
        Halves fatigue rather than clearing it (see `fatigue`'s field comment), but
        clears stun outright -- unlike burnout, a knock to the nerves is meant to
        walk off completely with one real rest."""
        self.last_rest_hour = self.elapsed_hours
        self.fatigue //= 2
        self.stun = 0

    def stat(self, name: str) -> int:
        if name not in STAT_NAMES:
            raise ValueError(f"unknown stat: {name!r}")
        value = getattr(self, name)
        if name in CORE_STATS:
            value += equipped_bonus(self.inventory, name)
            value += installed_bonus(self.installed_cyberware, name)
            value += self.temp_bonuses.get(name, 0)
            value -= self.fatigue_penalty
            value -= self.humanity_penalty
        return value
