"""The people who stand in a district's town square.

Most are **standard locals**: a small, uniform roster of kinds (resident, punk,
vendor, drunk, courier) generated deterministically from the run seed and the
territory id. They are flavour and rumour, not progression.

A handful per run are **named contacts**: hand-authored people with their own
lines and a one-off favour. Their quest is gated on `Character.local_standing`
(already saved), so helping one is recorded without a new save field.

Nothing here is stored: the roster is a pure function of the seed and the
territory id, and the square rebuilds the same people after a reload.
"""

from __future__ import annotations

import random
import zlib
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.corpmap import CorpMap, Territory


class NpcKind(StrEnum):
    RESIDENT = "resident"
    PUNK = "punk"
    VENDOR = "vendor"
    DRUNK = "drunk"
    COURIER = "courier"
    NAMED = "named"


KIND_GLYPHS = {
    NpcKind.RESIDENT: "c",
    NpcKind.PUNK: "p",
    NpcKind.VENDOR: "v",
    NpcKind.DRUNK: "d",
    NpcKind.COURIER: "u",
    NpcKind.NAMED: "*",
}

KIND_STYLES = {
    NpcKind.RESIDENT: "white",
    NpcKind.PUNK: "bright_red",
    NpcKind.VENDOR: "bright_yellow",
    NpcKind.DRUNK: "bright_blue",
    NpcKind.COURIER: "bright_green",
    NpcKind.NAMED: "bold bright_magenta",
}

KIND_LABELS = {
    NpcKind.RESIDENT: "Resident",
    NpcKind.PUNK: "Street punk",
    NpcKind.VENDOR: "Vendor",
    NpcKind.DRUNK: "Drunk",
    NpcKind.COURIER: "Courier",
    NpcKind.NAMED: "Named contact",
}

# Standard kinds keep the plaza populated through the day; punks and drunks own
# the night shift. A square after midnight should feel different from noon.
DAY_START = 6
DAY_END = 20

STANDARD_LINES = {
    NpcKind.RESIDENT: (
        "Quiet day. Quiet week. I like it that way.",
        "You're new around here, aren't you?",
        "Keep your head down and your creds close.",
    ),
    NpcKind.PUNK: (
        "Watch the corners, runner. Some of them bite.",
        "You look like you're looking for something. Everything's for sale.",
        "The corps own the sky. We own the cracks.",
    ),
    NpcKind.VENDOR: (
        "Fresh stock. No questions, no receipts.",
        "Buy something or move along, friend.",
        "You break it, you bought it. That's the whole policy.",
    ),
    NpcKind.DRUNK: (
        "The city's got teeth, friend. Big ones.",
        "I used to run jobs too. Then I found the bottom of a bottle.",
        "You ever notice the rain tastes like batteries?",
    ),
    NpcKind.COURIER: (
        "Package for someone. Not you.",
        "Move. I'm on the clock.",
        "You didn't see me and I didn't see you.",
    ),
}

FIRST_NAMES = (
    "Ash", "Blue", "Cass", "Dex", "Echo", "Fen", "Gale", "Hex", "Iris", "Jax",
    "Kite", "Lux", "Moss", "Nix", "Onyx", "Pike", "Quill", "Rook", "Sable", "Tess",
)
LAST_NAMES = (
    "Ames", "Briggs", "Cinder", "Doyle", "Egan", "Frost", "Grey", "Hale", "Ives", "Juno",
    "Keller", "Lane", "Mercer", "Nash", "Oakes", "Pryce", "Quinn", "Reeve", "Stone", "Vale",
)


@dataclass(frozen=True)
class Npc:
    id: str
    name: str
    kind: NpcKind
    territory_id: str
    blurb: str = ""
    lines: tuple[str, ...] = ()
    line_index: int = 0
    quest_skill: str | None = None
    quest_difficulty: int = 0
    reward_cash: int = 0
    reward_standing: int = 0

    @property
    def glyph(self) -> str:
        return KIND_GLYPHS[self.kind]

    @property
    def style(self) -> str:
        return KIND_STYLES[self.kind]

    @property
    def label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def special(self) -> bool:
        return self.kind is NpcKind.NAMED

    def is_present(self, hour_of_day: int) -> bool:
        if self.special:
            return True
        if self.kind in (NpcKind.PUNK, NpcKind.DRUNK):
            return hour_of_day >= DAY_END or hour_of_day < 4
        return DAY_START <= hour_of_day < DAY_END


@dataclass(frozen=True)
class NamedTemplate:
    slug: str
    name: str
    blurb: str
    lines: tuple[str, ...]
    quest_skill: str
    quest_difficulty: int
    reward_cash: int
    reward_standing: int


NAMED_TEMPLATES: tuple[NamedTemplate, ...] = (
    NamedTemplate(
        "nyx",
        "Nyx",
        "A netrunner who never quite left the square.",
        (
            "The grid remembers everything. I remember the useful parts.",
            "You've got the look of someone who breaks things for money.",
        ),
        "computer",
        9,
        80,
        2,
    ),
    NamedTemplate(
        "archivist",
        "The Archivist",
        "Keeps a paper ledger of everyone who matters.",
        (
            "Paper can't be scraped. That's the point.",
            "I have a page for you already. It's mostly blank. For now.",
        ),
        "infer",
        9,
        70,
        2,
    ),
    NamedTemplate(
        "sable",
        "Sable",
        "A courier who knows every shortcut in the district.",
        (
            "Walk fast, look bored. That's the whole trick.",
            "I can get a parcel anywhere in this city. Almost anywhere.",
        ),
        "stealth",
        9,
        75,
        2,
    ),
    NamedTemplate(
        "marrow",
        "Marrow",
        "A back-alley medic with steady hands and a bad temper.",
        (
            "Sit still. I don't do second chances, only first aid.",
            "You're bleeding on my floor. Fix that.",
        ),
        "medicine",
        9,
        85,
        2,
    ),
    NamedTemplate(
        "kestrel",
        "Kestrel",
        "A fixer's fixer, always working three angles at once.",
        (
            "Everybody wants something. The trick is finding out what.",
            "You're useful. Don't let it go to your head.",
        ),
        "negotiations",
        9,
        95,
        2,
    ),
    NamedTemplate(
        "iris",
        "Iris",
        "Runs a repair bench and a quiet information habit.",
        (
            "Everything breaks. Most things can be fixed. People are the exception.",
            "Bring me something interesting and I'll tell you what it's worth.",
        ),
        "tinkering",
        9,
        80,
        2,
    ),
)


def _seed(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))


def standard_npcs(territory: Territory, seed: int | None) -> list[Npc]:
    """The uniform local roster for one district. Seed + territory id, never saved."""
    rng = random.Random(_seed(f"{seed}:{territory.id}:npcs"))
    count = min(4, 2 + max(0, territory.value - 1) // 2)
    kinds = [NpcKind.RESIDENT, NpcKind.PUNK, NpcKind.VENDOR, NpcKind.DRUNK, NpcKind.COURIER]
    rng.shuffle(kinds)
    roster: list[Npc] = []
    for index in range(count):
        kind = kinds[index]
        lines = STANDARD_LINES[kind]
        roster.append(
            Npc(
                id=f"npc:{territory.id}:{index}",
                name=f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}",
                kind=kind,
                territory_id=territory.id,
                lines=lines,
                line_index=rng.randrange(len(lines)),
            )
        )
    return roster


def named_assignment(corp_map: CorpMap, seed: int | None) -> dict[str, NamedTemplate]:
    """Which named contact stands where this run, unique and start-district aware."""
    ranked = sorted(
        corp_map.territories.values(),
        key=lambda territory: _seed(f"{seed}:{territory.id}:named"),
    )
    chosen = ranked[: len(NAMED_TEMPLATES)]
    start_id = corp_map.player_start_id
    if start_id not in {territory.id for territory in chosen}:
        start = corp_map.territories.get(start_id)
        if start is not None:
            chosen[-1] = start
    # Re-sort so the template assignment is stable regardless of the start swap.
    chosen.sort(key=lambda territory: territory.id)
    return {territory.id: NAMED_TEMPLATES[i] for i, territory in enumerate(chosen)}


def named_npcs(territory: Territory, seed: int | None, corp_map: CorpMap) -> list[Npc]:
    template = named_assignment(corp_map, seed).get(territory.id)
    if template is None:
        return []
    return [
        Npc(
            id=f"npc_named_{template.slug}",
            name=template.name,
            kind=NpcKind.NAMED,
            territory_id=territory.id,
            blurb=template.blurb,
            lines=template.lines,
            quest_skill=template.quest_skill,
            quest_difficulty=template.quest_difficulty,
            reward_cash=template.reward_cash,
            reward_standing=template.reward_standing,
        )
    ]


def npcs_for_territory(territory: Territory, seed: int | None, corp_map: CorpMap) -> list[Npc]:
    return standard_npcs(territory, seed) + named_npcs(territory, seed, corp_map)
