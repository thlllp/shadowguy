from dataclasses import dataclass, field

BASE_HEALTH = 10
HEALTH_PER_BODY = 5
BASE_STAMINA = 3
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
    advantage: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.health is None:
            self.health = self.max_health
        if self.stamina is None:
            self.stamina = self.max_stamina

    def advantage_for(self, mission_id: str) -> int:
        return self.advantage.get(mission_id, 0)

    def add_advantage(self, mission_id: str, amount: int) -> None:
        self.advantage[mission_id] = self.advantage_for(mission_id) + amount

    def consume_advantage(self, mission_id: str) -> int:
        return self.advantage.pop(mission_id, 0)

    @property
    def max_health(self) -> int:
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

    def rest(self) -> None:
        self.day += 1
        self.stamina = self.max_stamina

    def stat(self, name: str) -> int:
        if name not in STAT_NAMES:
            raise ValueError(f"unknown stat: {name!r}")
        return getattr(self, name)
