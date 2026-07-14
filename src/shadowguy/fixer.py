"""Persistent Fixer roster and their procedurally generated job offers."""

import random
import uuid
from dataclasses import dataclass, field

from shadowguy.corpmap import CorpMap
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
    # A Territory id (corpmap.Territory.id) — where this fixer can be found in
    # person. Set once at run start (create_fixers), not moved around after: a
    # fixer is a fixture of their turf, not a roaming NPC.
    location_id: str = ""
    max_offers: int = 2
    offers: list[JobOffer] = field(default_factory=list)


def create_fixers(corp_map: CorpMap, rng: random.Random | None = None) -> list[Fixer]:
    """Seat every fixer in a distinct district on this run's map, so 'a fixer is in
    the area' (app.MainMenu's Local tab) means something different every run."""
    rng = rng or random.Random()
    territory_ids = rng.sample(list(corp_map.territories), len(FIXER_ROSTER))
    return [
        Fixer(id=fixer_id, name=name, specialty=specialty, location_id=territory_id)
        for (fixer_id, name, specialty), territory_id in zip(FIXER_ROSTER, territory_ids, strict=True)
    ]


def expire_offers(fixers: list[Fixer], day: int) -> None:
    for fixer in fixers:
        fixer.offers = [offer for offer in fixer.offers if not offer.timing.is_expired(day)]


def refresh_offers(
    fixers: list[Fixer], day: int, corp_map: CorpMap, rng: random.Random | None = None
) -> None:
    rng = rng or random.Random()
    for fixer in fixers:
        while len(fixer.offers) < fixer.max_offers:
            scene, timing = generate_job(day, corp_map, fixer.id, rng)
            fixer.offers.append(
                JobOffer(
                    id=f"offer_{uuid.uuid4().hex[:8]}",
                    fixer_id=fixer.id,
                    scene=scene,
                    timing=timing,
                    offered_day=day,
                )
            )
