from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shadowguy.runners import RUNNERS_BY_ID, recruit_wage
from shadowguy.shops import (
    InventoryItem,
    equipped_bonus,
    equipped_skill_bonus,
    equipped_travel_bonus,
)
from shadowguy.skills import SKILLS, skill_for, skill_value

if TYPE_CHECKING:
    from shadowguy.fixer import JobOffer
    from shadowguy.security import SecurityContract

BASE_HEALTH = 10


@dataclass
class CrewHire:
    """One runner engaged on your crew, and on what terms. `job_id` set = signed for that
    single accepted job (they take runners.RivalRunner.job_cut of its payout, and the
    engagement ends when the job does); `job_id` None = kept on indefinitely (charged
    runners.RivalRunner.daily_cost every rest). A runner has at most one live hire."""

    runner_id: str
    job_id: str | None = None
HEALTH_PER_BODY = 5
BASE_STAMINA = 5

# Everything starts at 1 and is bought up from there. Both pools are spent at
# character creation (app.CharacterCreationScreen) and never refill — there is
# no XP system, so the build you walk in with is the build you have.
STARTING_STAT = 1
STARTING_SKILL_RANK = 1
STARTING_STAT_POINTS = 6
STARTING_SKILL_POINTS = 20
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
CORE_STATS = ("body", "strength", "agility", "perception", "intelligence", "cool")
STAT_NAMES = frozenset(CORE_STATS) | {"cash", "rep"}

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


@dataclass
class Character:
    name: str
    body: int = STARTING_STAT
    strength: int = STARTING_STAT
    agility: int = STARTING_STAT
    perception: int = STARTING_STAT
    intelligence: int = STARTING_STAT
    cool: int = STARTING_STAT
    cash: int = STARTING_CASH
    rep: int = 0
    health: int | None = None
    stamina: int | None = None
    day: int = 1
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
    # Runner ids (runners.RUNNERS_BY_ID) the runner has hired at a bar. Assigning them to
    # a job's roles (with the one-remote-support cap) is a later increment.
    crew: list[CrewHire] = field(default_factory=list)
    accepted_jobs: list["JobOffer"] = field(default_factory=list)
    # Accepted multi-night guard contracts (security.py) — a standing engagement, not
    # a Scene: resolved one night at a time by MainMenu's end-day handler while
    # location_id matches a contract's territory_id, not by "running" it like a job.
    security_contracts: list["SecurityContract"] = field(default_factory=list)
    # Owned items, ids from shops.ITEMS_BY_ID. Duplicates allowed (same item bought twice).
    # Only entries with equipped=True contribute their bonus via stat().
    inventory: list[InventoryItem] = field(default_factory=list)
    # Owned consumables, ids from shops.CONSUMABLES_BY_ID. Duplicates allowed.
    # Removed (via shops.use_consumable) once used, unlike persistent gear.
    consumables: list[str] = field(default_factory=list)
    # Program ids (shops.PROGRAMS_BY_ID) bought into the runner's owned pool. Not
    # installed on any deck by itself — see shops.install_program/InventoryItem.
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
    # How many of today's free travel moves (shops.Item.travel_bonus, from the
    # equipped Slot.VEHICLE item) have already been spent. Tracked as usage rather
    # than a remaining count so re-equipping a different vehicle mid-day raises or
    # lowers the cap immediately instead of waiting for the next rest(). Reset to 0
    # on rest(), same as stamina.
    free_travel_used: int = 0
    # Whether a Health Kit has already been used today. A kit is a small emergency
    # top-up, not a stack you burn to full — one per day (shops.use_consumable enforces
    # it), cleared on rest() like stamina. Real recovery is time in a hospital ward.
    health_kit_used_today: bool = False

    def __post_init__(self) -> None:
        if self.health is None:
            self.health = self.max_health
        if self.stamina is None:
            self.stamina = self.max_stamina

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

    def on_crew(self, runner_id: str) -> bool:
        return any(hire.runner_id == runner_id for hire in self.crew)

    def hire_indefinite(self, runner_id: str) -> None:
        if not self.on_crew(runner_id):
            self.crew.append(CrewHire(runner_id=runner_id))

    def hire_for_job(self, runner_id: str, job_id: str) -> None:
        if not self.on_crew(runner_id):
            self.crew.append(CrewHire(runner_id=runner_id, job_id=job_id))

    def crew_for_job(self, job_id: str) -> list[CrewHire]:
        return [hire for hire in self.crew if hire.job_id == job_id]

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
    def max_stamina(self) -> int:
        return BASE_STAMINA

    @property
    def is_alive(self) -> bool:
        return self.health > 0

    def adjust_health(self, delta: int) -> None:
        self.health = max(0, min(self.max_health, self.health + delta))

    def adjust_rep(self, delta: int) -> None:
        self.rep = max(REP_FLOOR, self.rep + delta)

    def can_afford(self, cost: int) -> bool:
        return self.stamina >= cost

    def spend_stamina(self, amount: int) -> None:
        self.stamina -= amount

    def restore_stamina(self, amount: int) -> None:
        self.stamina = min(self.max_stamina, self.stamina + amount)

    def free_travel_remaining(self) -> int:
        return max(0, equipped_travel_bonus(self.inventory) - self.free_travel_used)

    def spend_free_travel(self) -> None:
        self.free_travel_used += 1

    def add_temp_bonus(self, stat: str, amount: int) -> None:
        self.temp_bonuses[stat] = self.temp_bonuses.get(stat, 0) + amount

    def skill_rank(self, skill_id: str) -> int:
        return self.skill_ranks.get(skill_id, STARTING_SKILL_RANK)

    def skill_gear_bonus(self, skill_id: str) -> int:
        return equipped_skill_bonus(self.inventory, skill_id)

    def at_max_rank(self, skill_id: str) -> bool:
        skill_for(skill_id)
        return self.skill_rank(skill_id) >= MAX_SKILL_RANK

    def next_rank_cost(self, skill_id: str) -> int | None:
        """Points to buy this skill's next rank, or None if it's already maxed."""
        if self.at_max_rank(skill_id):
            return None
        return SKILL_RANK_COST[self.skill_rank(skill_id) + 1]

    def spend_skill_point(self, skill_id: str) -> bool:
        skill_for(skill_id)  # unknown id: raise rather than burn points on a junk rank
        cost = self.next_rank_cost(skill_id)
        if cost is None or cost > self.skill_points:
            return False
        self.skill_points -= cost
        self.skill_ranks[skill_id] = self.skill_rank(skill_id) + 1
        return True

    def spend_stat_point(self, name: str) -> bool:
        if name not in CORE_STATS:
            raise ValueError(f"not a core stat: {name!r}")
        if self.stat_points <= 0:
            return False
        self.stat_points -= 1
        setattr(self, name, getattr(self, name) + 1)
        if name == "body":
            # max_health is derived from body, so buying Body raises the ceiling.
            # Carry current health up with it, or the run starts already wounded.
            self.health += HEALTH_PER_BODY
        return True

    def reset_build(self) -> None:
        """Undo every point spent at creation. The creation screen's only way back."""
        for stat in CORE_STATS:
            setattr(self, stat, STARTING_STAT)
        self.skill_ranks = {skill.id: STARTING_SKILL_RANK for skill in SKILLS}
        self.stat_points = STARTING_STAT_POINTS
        self.skill_points = STARTING_SKILL_POINTS
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

    def _discharge_orphan_crew(self) -> None:
        """Drop any for-job hire whose job is no longer accepted (completed, blown, expired).
        An indefinite hire (job_id None) is untouched."""
        active = {job.scene.id for job in self.accepted_jobs}
        self.crew = [hire for hire in self.crew if hire.job_id is None or hire.job_id in active]

    def rest(self) -> None:
        self.day += 1
        self.stamina = self.max_stamina
        self.free_travel_used = 0
        self.health_kit_used_today = False
        self.temp_bonuses = {}
        self.accepted_jobs = [job for job in self.accepted_jobs if not job.timing.is_expired(self.day)]
        self._discharge_orphan_crew()

    def stat(self, name: str) -> int:
        if name not in STAT_NAMES:
            raise ValueError(f"unknown stat: {name!r}")
        value = getattr(self, name)
        if name in CORE_STATS:
            value += equipped_bonus(self.inventory, name)
            value += self.temp_bonuses.get(name, 0)
        return value
