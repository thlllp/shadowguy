from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shadowguy.shops import InventoryItem, equipped_bonus, equipped_skill_bonus, equipped_travel_bonus
from shadowguy.skills import SKILLS, skill_for

if TYPE_CHECKING:
    from shadowguy.fixer import JobOffer

BASE_HEALTH = 10
HEALTH_PER_BODY = 5
BASE_STAMINA = 5

# Everything starts at 1 and is bought up from there. Both pools are spent at
# character creation (app.CharacterCreationScreen) and never refill — there is
# no XP system, so the build you walk in with is the build you have.
STARTING_STAT = 1
STARTING_SKILL_RANK = 1
STARTING_STAT_POINTS = 6
STARTING_SKILL_POINTS = 20
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
    cash: int = 0
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
    # Fixer ids (fixer.Fixer.id) whose location the runner has stood in at least
    # once. A fixer's presence on the corp map is hidden until discovered this way
    # — see fixer.discover_fixers_here(), the single place that reveals it.
    discovered_fixers: set[str] = field(default_factory=set)
    accepted_jobs: list["JobOffer"] = field(default_factory=list)
    # Owned items, ids from shops.ITEMS_BY_ID. Duplicates allowed (same item bought twice).
    # Only entries with equipped=True contribute their bonus via stat().
    inventory: list[InventoryItem] = field(default_factory=list)
    # Owned consumables, ids from shops.CONSUMABLES_BY_ID. Duplicates allowed.
    # Removed (via shops.use_consumable) once used, unlike persistent gear.
    consumables: list[str] = field(default_factory=list)
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

    def standing_with(self, faction_id: str) -> int:
        return self.standing.get(faction_id, 0)

    def adjust_standing(self, faction_id: str, delta: int) -> None:
        self.standing[faction_id] = self.standing_with(faction_id) + delta

    def trust_with(self, fixer_id: str) -> int:
        return self.fixer_trust.get(fixer_id, 0)

    def adjust_fixer_trust(self, fixer_id: str, delta: int) -> None:
        self.fixer_trust[fixer_id] = self.trust_with(fixer_id) + delta

    def local_standing_with(self, character_id: str) -> int:
        return self.local_standing.get(character_id, 0)

    def adjust_local_standing(self, character_id: str, delta: int) -> None:
        self.local_standing[character_id] = self.local_standing_with(character_id) + delta

    def discover_fixer(self, fixer_id: str) -> None:
        self.discovered_fixers.add(fixer_id)

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

    def rest(self) -> None:
        self.day += 1
        self.stamina = self.max_stamina
        self.free_travel_used = 0
        self.temp_bonuses = {}
        self.accepted_jobs = [job for job in self.accepted_jobs if not job.timing.is_expired(self.day)]

    def stat(self, name: str) -> int:
        if name not in STAT_NAMES:
            raise ValueError(f"unknown stat: {name!r}")
        value = getattr(self, name)
        if name in CORE_STATS:
            value += equipped_bonus(self.inventory, name)
            value += self.temp_bonuses.get(name, 0)
        return value
