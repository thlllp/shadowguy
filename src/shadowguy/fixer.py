"""Persistent Fixer roster and their procedurally generated job offers."""

import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from shadowguy.checks import resolve_rng
from shadowguy.corpmap import CorpMap
from shadowguy.factions import FACTIONS_BY_ID
from shadowguy.jobs import JobTiming, generate_job
from shadowguy.scene import Scene

if TYPE_CHECKING:
    from shadowguy.character import Character

# Rows are (id, name, specialty, faction_id). faction_id None is a street-level
# contact, seeded on neutral ground; a real faction_id is an inside contact, seeded
# on turf that corp actually owns this run (see create_fixers).
FIXER_ROSTER = [
    ("fixer_rook", "Rook", "Corp data & heists", None),
    ("fixer_mama_wex", "Mama Wex", "Extractions & bodywork", None),
    ("fixer_dolman", "Dolman", "Sabotage & wetwork", None),
    ("fixer_switchblade_sal", "Switchblade Sal", "Street muscle & quick cash", None),
    ("fixer_tallyman", "The Tallyman", "Debt collection & courier work", None),
    ("fixer_neon_choir", "Neon Choir", "Info brokering & blackmail", None),
    ("fixer_stitch", "Stitch", "Ironclad hardware & muscle", "faction_ironclad"),
    ("fixer_null", "Null", "Ghostwire backdoors & data runs", "faction_ghostwire"),
    ("fixer_doc_vex", "Doc Vex", "Meridian black-clinic contracts", "faction_meridian"),
]

# Catches a typo'd faction_id at import rather than a KeyError deep in create_fixers.
for _fixer_id, _name, _specialty, _faction_id in FIXER_ROSTER:
    if _faction_id is not None and _faction_id not in FACTIONS_BY_ID:
        raise ValueError(f"FIXER_ROSTER entry {_fixer_id!r} references unknown faction {_faction_id!r}")


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
    # None for a street-level contact; a Faction id for an inside contact seeded
    # on that corp's own turf (see create_fixers). Not consumed by job generation
    # today — a fixer's own affiliation is independent of which corp their offers
    # target — it's flavor plus a hook for a future "your corp contact" feature.
    faction_id: str | None = None


def _seat(
    roster: list[tuple[str, str, str, str | None]],
    candidates: list[str],
    rng: random.Random,
) -> list[Fixer]:
    """Place each roster entry in a distinct, randomly chosen candidate district."""
    territory_ids = rng.sample(candidates, len(roster))
    return [
        Fixer(id=fixer_id, name=name, specialty=specialty, location_id=territory_id, faction_id=faction_id)
        for (fixer_id, name, specialty, faction_id), territory_id in zip(roster, territory_ids, strict=True)
    ]


def create_fixers(corp_map: CorpMap, rng: random.Random | None = None) -> list[Fixer]:
    """Seat every fixer in a distinct district on this run's map, so 'a fixer is in
    the area' (app.MainMenu's Local tab) means something different every run.

    A street-level fixer (faction_id None) is seeded on neutral ground, never the
    player's own start tile — the start tile is guaranteed reachable day one either
    way. An inside fixer (faction_id set) is seeded on a district that faction
    actually owns this run, so which of their districts holds the contact varies
    with the map like everything else — a corp's own turf isn't off-limits to them,
    unlike a street-level fixer.
    """
    rng = resolve_rng(rng)
    candidates = [
        territory.id
        for territory in corp_map.territories.values()
        if territory.owner == "neutral" and territory.id != corp_map.player_start_id
    ]
    corp_candidates: dict[str, list[str]] = {}
    for territory in corp_map.territories.values():
        if territory.owner != "neutral":
            corp_candidates.setdefault(territory.owner, []).append(territory.id)

    neutral_roster = [entry for entry in FIXER_ROSTER if entry[3] is None]
    fixers = _seat(neutral_roster, candidates, rng)
    for faction_id, candidates in corp_candidates.items():
        corp_roster = [entry for entry in FIXER_ROSTER if entry[3] == faction_id]
        fixers += _seat(corp_roster, candidates, rng)
    return fixers


def discover_fixers_here(fixers: list[Fixer], character: "Character") -> None:
    """Standing in a fixer's district is what discovers them. The single chokepoint
    for that check — call this from anywhere character.location_id could have
    changed or be displayed, rather than re-checking fixer.location_id locally."""
    for fixer in fixers:
        if fixer.location_id == character.location_id:
            character.discover_fixer(fixer.id)


def expire_offers(fixers: list[Fixer], day: int) -> None:
    for fixer in fixers:
        fixer.offers = [offer for offer in fixer.offers if not offer.timing.is_expired(day)]


def refresh_offers(
    fixers: list[Fixer], day: int, corp_map: CorpMap, rng: random.Random | None = None
) -> None:
    rng = resolve_rng(rng)
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
