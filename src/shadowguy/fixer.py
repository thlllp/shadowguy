"""Persistent Fixer roster and their procedurally generated job offers."""

import random
import uuid
from dataclasses import dataclass, field

from shadowguy.jobs import JobTiming, generate_job
from shadowguy.scene import Scene

FIXER_ROSTER = [
    ("fixer_rook", "Rook", "Corp data & heists"),
    ("fixer_mama_wex", "Mama Wex", "Extractions & bodywork"),
    ("fixer_dolman", "Dolman", "Sabotage & wetwork"),
]


@dataclass
class JobOffer:
    id: str
    fixer_id: str
    scene: Scene
    timing: JobTiming
    offered_day: int


@dataclass
class Fixer:
    id: str
    name: str
    specialty: str
    max_offers: int = 2
    offers: list[JobOffer] = field(default_factory=list)


def create_fixers() -> list[Fixer]:
    return [Fixer(id=fixer_id, name=name, specialty=specialty) for fixer_id, name, specialty in FIXER_ROSTER]


def expire_offers(fixers: list[Fixer], day: int) -> None:
    for fixer in fixers:
        fixer.offers = [offer for offer in fixer.offers if not offer.timing.is_expired(day)]


def refresh_offers(fixers: list[Fixer], day: int, rng: random.Random | None = None) -> None:
    rng = rng or random.Random()
    for fixer in fixers:
        while len(fixer.offers) < fixer.max_offers:
            scene, timing = generate_job(day, rng)
            fixer.offers.append(
                JobOffer(
                    id=f"offer_{uuid.uuid4().hex[:8]}",
                    fixer_id=fixer.id,
                    scene=scene,
                    timing=timing,
                    offered_day=day,
                )
            )
