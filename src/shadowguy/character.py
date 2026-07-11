from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shadowguy.shops import InventoryItem, equipped_bonus

if TYPE_CHECKING:
    from shadowguy.fixer import JobOffer

BASE_HEALTH = 10
HEALTH_PER_BODY = 5
BASE_STAMINA = 5
STAT_NAMES = frozenset({"body", "skill", "cool", "cash", "rep"})


@dataclass
class Character:
    name: str
    body: int = 3
    skill: int = 3
    cool: int = 3
    cash: int = 0
    rep: int = 0
    health: int | None = None
    stamina: int | None = None
    day: int = 1
    # Which Territory of the corp map the runner is standing in.
    location_id: str = ""
    advantage: dict[str, int] = field(default_factory=dict)
    standing: dict[str, int] = field(default_factory=dict)
    accepted_jobs: list["JobOffer"] = field(default_factory=list)
    # Owned items, ids from shops.ITEMS_BY_ID. Duplicates allowed (same item bought twice).
    # Only entries with equipped=True contribute their bonus via stat().
    inventory: list[InventoryItem] = field(default_factory=list)

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

    def accept_job(self, offer: "JobOffer") -> None:
        self.accepted_jobs.append(offer)

    def remove_job(self, job_scene_id: str) -> None:
        self.accepted_jobs = [job for job in self.accepted_jobs if job.scene.id != job_scene_id]

    def rest(self) -> None:
        self.day += 1
        self.stamina = self.max_stamina
        self.accepted_jobs = [job for job in self.accepted_jobs if not job.timing.is_expired(self.day)]

    def stat(self, name: str) -> int:
        if name not in STAT_NAMES:
            raise ValueError(f"unknown stat: {name!r}")
        value = getattr(self, name)
        if name in ("body", "skill", "cool"):
            value += equipped_bonus(self.inventory, name)
        return value
