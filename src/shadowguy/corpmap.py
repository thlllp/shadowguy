"""The territory map itself: what a district is, what stands in it, and how it reads.

The model and the queries over it — `Location`/`LocalCharacter`/`Territory`/`CorpMap`,
a district's modifier levers, lodging and safehouse pricing, whose ground borders
whose (`expansion_candidates`), how far apart two districts are, and the ASCII
renderer. Laying a *new* map out is `corpmap_gen.generate_corp_map`, which imports
this and is imported by almost nothing: one map is generated per run, so the ~650
lines of blob growth, bloc racing and location naming that do it have no business in
the module every other system reads a Territory through.
"""

import random
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.factions import FACTIONS, FACTIONS_BY_ID
from shadowguy.relations import Relations
from shadowguy.skills import skill_for

OWNER_NAMES = {"neutral": "Unclaimed"}

# No "player" owner: the runner starts standing on unclaimed ground, not holding it.
# The map marks where the runner *is* with @ (see _label), not with a corp tag.
OWNER_TAGS = {
    "neutral": "",
    **{faction.id: faction.name.split()[0][:3].upper() for faction in FACTIONS},
}

# Distinct terminal colors per corp, so a district's owner reads at a glance on the
# map without checking the 3-letter tag. Neutral ground gets no entry (and so no
# override) — unclaimed is meant to look unclaimed, not tagged bright anything.
# strict=True raises at import time if this list drifts out of sync with FACTIONS.
_OWNER_COLOR_VALUES = ["red", "cyan", "green", "yellow"]
OWNER_COLORS = dict(zip((faction.id for faction in FACTIONS), _OWNER_COLOR_VALUES, strict=True))


def owner_label(owner: str) -> str:
    if owner in OWNER_NAMES:
        return OWNER_NAMES[owner]
    return FACTIONS_BY_ID[owner].name


class LocationKind(StrEnum):
    DATA = "data"
    LAB = "lab"
    DEPOT = "depot"
    CYBER_CLINIC = "cyber_clinic"
    BAR = "bar"
    PAWN = "pawn"
    WEAPON_SHOP = "weapon_shop"
    AUTO_DEALER = "auto_dealer"
    PHARMACY = "pharmacy"
    COMPUTER_STORE = "computer_store"
    HOSPITAL = "hospital"
    APARTMENT = "apartment"
    SAFEHOUSE = "safehouse"
    REAL_ESTATE = "real_estate"
    CORP_HQ = "corp_hq"
    GANG_DEN = "gang_den"
    RESEARCH_FACILITY = "research_facility"
    ACADEMY = "academy"
    JUNKYARD = "junkyard"
    DOCKS = "docks"


# The runner's own places — their home, and any safehouse they come to hold. One
# concept, two consequences: a place the runner owns is injected into a territory
# rather than rolled onto the map (so it's excluded from GENERATED_KINDS below), and
# the runner sleeps in it for free (lodging_cost). Add a kind here and it gets both.
PLAYER_OWNED_KINDS = (LocationKind.APARTMENT, LocationKind.SAFEHOUSE)

# Kinds injected into specific districts rather than rolled onto the map, and carrying
# none of the per-kind world tables below (the injectors are all corpmap_gen's): the
# runner's own places, each corp's HQ (which has its own officers and screen — see
# _make_hq / app.CorpHQScreen), each gang's den (see _make_gang_den), each corp's
# research facility (see add_research_facility / corp_turn.collect_research), each
# corp's academy (see _make_academy / corp_turn.train_employees), a rare scavenging
# spot on unclaimed ground (see _make_junkyard / shops.scavenge), and a rare fishing
# spot on unclaimed ground (see _make_docks / fishing.generate_fishing_trip). None of
# these is player-owned, so they're a separate group from PLAYER_OWNED_KINDS.
UNROLLED_KINDS = (
    *PLAYER_OWNED_KINDS,
    LocationKind.CORP_HQ,
    LocationKind.GANG_DEN,
    LocationKind.RESEARCH_FACILITY,
    LocationKind.ACADEMY,
    LocationKind.JUNKYARD,
    LocationKind.DOCKS,
)

# Kinds the world generator gives the full per-kind treatment: everything with a real
# storefront/job surface. They're a job target, scouted on legwork and run by generic
# NPCs, so the per-kind tables (LOCATION_SKILL here, corpmap_gen.LOCATION_ROLES,
# gigs._GIG_TEMPLATES, jobs.LEGWORK_APPROACH_TEXT) carry exactly one entry each — every
# guard checks against GENERATED_KINDS, not the full enum, so the UNROLLED_KINDS above
# stay out of them.
GENERATED_KINDS = tuple(k for k in LocationKind if k not in UNROLLED_KINDS)

# Hospitals are placed to a fixed count (corpmap_gen's generate_corp_map /
# HOSPITAL_COUNT) rather than rolled in with everything else, so every map has about the
# same healing access instead of it swinging with the location lottery. So the random
# location pools draw from everything generated *except* the hospital. It still needs the
# per-kind world tables (it can be a job site, and gigs spawn there), so it stays in
# GENERATED_KINDS — that's what the import guards check against.
ROLLED_KINDS = tuple(k for k in GENERATED_KINDS if k is not LocationKind.HOSPITAL)


# Retail kinds: shops.py's business, but defined here (not there) since
# corpmap_gen._location_kinds needs them and corpmap.py must not import shops.py.
SHOP_KINDS = (
    LocationKind.PAWN,
    LocationKind.WEAPON_SHOP,
    LocationKind.AUTO_DEALER,
    LocationKind.PHARMACY,
    LocationKind.COMPUTER_STORE,
)

# The skill a location kind is scouted with, on legwork. jobs.py owns the flavor
# text for each kind (jobs.LEGWORK_APPROACH_TEXT) and reads the skill from here,
# so there is exactly one place that says "DATA is a Hack check" —
# corpmap_gen._location_kinds also needs it, to keep a district's filler slot from
# repeating its own specialty's stat (via location_stat() below).
#
# Legwork is scouting, so this table leans on the watching-and-casing skills:
# perception and agility mostly, logic on the wired places, cool where
# the read comes out of a conversation.
LOCATION_SKILL = {
    LocationKind.DATA: "hack",
    LocationKind.LAB: "pattern_seeking",
    LocationKind.DEPOT: "stealth",
    LocationKind.BAR: "intuition",
    LocationKind.PAWN: "negotiations",
    LocationKind.WEAPON_SHOP: "sight",
    LocationKind.AUTO_DEALER: "deception",
    LocationKind.PHARMACY: "infer",
    LocationKind.COMPUTER_STORE: "hack",
    LocationKind.HOSPITAL: "infer",
    LocationKind.REAL_ESTATE: "intuition",
    LocationKind.CYBER_CLINIC: "infer",
}
if set(LOCATION_SKILL) != set(GENERATED_KINDS):
    raise ValueError("LOCATION_SKILL must have exactly one entry per generated LocationKind")


def location_stat(kind: LocationKind) -> str:
    """The core stat behind a kind's scouting skill. Derived, never a second table."""
    return skill_for(LOCATION_SKILL[kind]).stat


# Catches a typo'd skill id at import instead of when a legwork Scene is built.
for _kind in GENERATED_KINDS:
    location_stat(_kind)


class TerritoryModifier(StrEnum):
    """The levers a corp pulls on ground it holds. Three of the five are load-bearing:
    SECURITY is half of corp_turn.defense_strength (and gates raise_development),
    SURVEILLANCE drives surveillance.py's detection rolls, DEVELOPMENT prices lodging
    and safehouses. UNREST and RESTRICTED are still display only."""

    SECURITY = "security"
    SURVEILLANCE = "surveillance"
    UNREST = "unrest"
    DEVELOPMENT = "development"
    RESTRICTED = "restricted"


MODIFIER_MAX = 5

MODIFIER_LABELS = {
    TerritoryModifier.SECURITY: "Security",
    TerritoryModifier.SURVEILLANCE: "Surveillance",
    TerritoryModifier.UNREST: "Unrest",
    TerritoryModifier.DEVELOPMENT: "Development",
    TerritoryModifier.RESTRICTED: "Restricted",
}


@dataclass
class LocalCharacter:
    """A person who runs or haunts a Location — a shop's owner, a bar's regular.

    Standing with them is tracked on Character.local_standing, keyed by this id
    (unique across the map by construction), moved by gigs and read by shop pricing.
    """

    id: str
    name: str
    role: str


@dataclass
class Location:
    """A concrete place inside a Territory — what a job actually hits."""

    id: str
    name: str
    kind: LocationKind
    # Who runs or haunts the place: 1 for a shop (its owner), 1–2 for anywhere else.
    characters: list[LocalCharacter] = field(default_factory=list)
    # REAL_ESTATE only: the territory ids this office has safehouses for sale in. Its
    # cross-map portfolio, sampled once at generation (see corpmap_gen.generate_corp_map).
    listings: list[str] = field(default_factory=list)
    # RESEARCH_FACILITY only: how many RP/day it generates for whichever corp holds
    # it (see corp_turn.collect_research). Starts at 1; nothing raises it yet.
    research_tier: int | None = None
    # ACADEMY only: how many employees one training session produces (see
    # corp_turn.train_employees). Starts at 1; nothing raises it yet.
    academy_tier: int | None = None
    # RESEARCH_FACILITY only: how many extra labs have been built there (see
    # corp_turn.build_lab) — each one seats another scientist doing research.
    # Starts at 0.
    labs_built: int | None = None
    # RESEARCH_FACILITY only: how many efficiency upgrades have been built there
    # (see corp_turn.build_efficiency_upgrade) — each one adds +1 RP/day to every
    # scientist working this facility. Starts at 0.
    efficiency_upgrades: int | None = None
    # PLAYER_OWNED_KINDS only (APARTMENT/SAFEHOUSE): whether this place has a
    # workshop (see shops.install_mod/craft_consumable, screens.SafehouseScreen).
    # None everywhere else, same convention as research_tier. The apartment is
    # injected already True (corpmap_gen.py); add_safehouse below starts a new
    # safehouse at False, built later via build_workshop.
    workshop_built: bool | None = None


@dataclass
class Territory:
    id: str
    name: str
    x: int
    y: int
    owner: str = "neutral"
    value: int = 1
    connections: list[str] = field(default_factory=list)
    locations: list[Location] = field(default_factory=list)
    modifiers: dict[TerritoryModifier, int] = field(default_factory=dict)
    # A gang's presence, unlike owner: it doesn't claim the ground (owner stays
    # "neutral" here), just operates on it. See corpmap_gen's GANG_TURF_MIN/MAX
    # and _place_gangs.
    gang_id: str | None = None
    # Operatives stationed here by whichever corp holds it — the defense half of
    # corp-vs-corp conflict (see corp_turn.attack_territory / defense_strength).
    # Lives on the Territory rather than on CorpState so the AI factions, which
    # have no CorpState at all, defend by exactly the same number the player does.
    # Always 0 on neutral ground: claim_territory/capture_territory both reset it,
    # and nothing garrisons ground nobody holds.
    garrison: int = 0


@dataclass
class CorpMap:
    territories: dict[str, Territory]
    player_start_id: str
    # Hand-built test fixtures that don't care about faction/gang standing can omit
    # this; corpmap_gen.generate_corp_map always fills it via generate_relations.
    relations: Relations = field(default_factory=dict)

    def __post_init__(self) -> None:
        for territory in self.territories.values():
            for conn_id in territory.connections:
                if conn_id not in self.territories:
                    raise ValueError(f"{territory.id}: unknown connection {conn_id!r}")
                if territory.id not in self.territories[conn_id].connections:
                    raise ValueError(
                        f"{territory.id} -> {conn_id} connection is not symmetric"
                    )

    def characters(self) -> list[tuple[Location, LocalCharacter]]:
        """Every LocalCharacter on the board, paired with the Location they belong to.
        The one place other systems (Contacts, gigs, shop pricing) enumerate them."""
        return [
            (location, character)
            for territory in self.territories.values()
            for location in territory.locations
            for character in location.characters
        ]


def has_home(territory: Territory) -> bool:
    """Whether the runner owns a place to sleep in this district — their apartment or a
    safehouse. Free lodging here, and a real estate office won't sell them another."""
    return any(loc.kind in PLAYER_OWNED_KINDS for loc in territory.locations)


# Nightly lodging when the runner rests in a district where they own no place to
# sleep: this much Cash per Development level, so a more developed district costs more
# to bed down in. Charged on rest() — see ShadowguyApp.rest().
LODGING_COST_PER_DEVELOPMENT = 5


def lodging_cost(territory: Territory) -> int:
    """What resting in this district costs the runner tonight. Free where they own a
    place (has_home); otherwise LODGING_COST_PER_DEVELOPMENT per Development level."""
    if has_home(territory):
        return 0
    return LODGING_COST_PER_DEVELOPMENT * territory.modifiers[TerritoryModifier.DEVELOPMENT]


# A safehouse's asking price scales with the district: a flat base, plus a premium for
# Development and for the territory's value — the nicer the block, the dearer the
# property, and the more lodging it saves. Bought through a REAL_ESTATE office's
# cross-map listing (see app.RealEstateScreen); once bought, has_home is true there.
SAFEHOUSE_BASE_PRICE = 200
SAFEHOUSE_PRICE_PER_DEVELOPMENT = 75
SAFEHOUSE_PRICE_PER_VALUE = 50


def safehouse_price(territory: Territory) -> int:
    return (
        SAFEHOUSE_BASE_PRICE
        + SAFEHOUSE_PRICE_PER_DEVELOPMENT * territory.modifiers[TerritoryModifier.DEVELOPMENT]
        + SAFEHOUSE_PRICE_PER_VALUE * territory.value
    )


def add_safehouse(territory: Territory) -> None:
    """Give the runner a safehouse here — a player-owned place, injected like the
    apartment (no owner NPC, never generated). Idempotent guard on the caller: a
    district that already has_home is never offered for sale, so this appends once."""
    territory.locations.append(
        Location(
            id=f"{territory.id}_safehouse",
            name="Your Safehouse",
            kind=LocationKind.SAFEHOUSE,
            workshop_built=False,
        )
    )


STARTING_RESEARCH_TIER = 1


def add_research_facility(territory: Territory) -> None:
    """Stand a research facility up on a corp-held district, at
    STARTING_RESEARCH_TIER with no labs or efficiency upgrades yet.

    Two callers, which is why this lives here rather than in corpmap_gen: the
    generator injects one per faction at layout time, and corp_turn's
    build_research_facility rebuilds one after a rival captured the last one a
    corp had. Same runtime-mutator shape as add_safehouse, cash gate on the
    caller.

    Names itself off territory.owner (owner_label) so nothing has to hand it a
    faction — that's what keeps corp_turn.py importing corpmap and nothing else.
    Callers guard uniqueness: the generator picks one district per faction, and
    rebuild_facility_targets is empty while the corp still holds a facility
    anywhere, so the {territory}_research id can't collide.
    """
    territory.locations.append(
        Location(
            id=f"{territory.id}_research",
            name=f"{owner_label(territory.owner)} Research Facility",
            kind=LocationKind.RESEARCH_FACILITY,
            research_tier=STARTING_RESEARCH_TIER,
            labs_built=0,
            efficiency_upgrades=0,
        )
    )


STARTING_ACADEMY_TIER = 1


def add_academy(territory: Territory) -> None:
    """Stand an academy up on a corp-held district, at STARTING_ACADEMY_TIER.

    Same two callers and same reasoning as add_research_facility above: the
    generator injects one per faction at layout time, and corp_turn's build_academy
    rebuilds one after a rival captured the last one a corp had. Names itself off
    territory.owner; callers guard the {territory}_academy id's uniqueness.

    No labs/efficiency equivalent — nothing has ever raised academy_tier — so unlike
    a research facility there is no accumulated investment inside one to lose.
    """
    territory.locations.append(
        Location(
            id=f"{territory.id}_academy",
            name=f"{owner_label(territory.owner)} Academy",
            kind=LocationKind.ACADEMY,
            academy_tier=STARTING_ACADEMY_TIER,
        )
    )


# One-time cash cost to build a workshop at a safehouse — flat, unlike the safehouse's
# own price, since a workshop doesn't get more useful in a nicer district. The apartment
# starts with one already built (corpmap_gen.py); this is what SafehouseScreen spends on
# a safehouse that doesn't have one yet.
WORKSHOP_BUILD_COST = 400


def build_workshop(location: Location) -> None:
    """Flip on a safehouse's workshop. Caller (SafehouseScreen) is the cash gate,
    same division of labor as add_safehouse/RealEstateScreen."""
    location.workshop_built = True


def claim_territory(territory: Territory, faction_id: str, rng: random.Random) -> None:
    """A faction moves onto previously-neutral ground: flips ownership and reseeds
    modifiers the way any corp-held district gets them (_corp_modifiers) — neutral
    ground's modifiers (flat Unrest MODIFIER_MAX, no Security/Surveillance) no longer
    describe it under new ownership. A gang's presence doesn't survive a corp moving in.
    territory.value is left as-is: the corp hasn't built the block up yet."""
    territory.owner = faction_id
    territory.modifiers = _corp_modifiers(territory.value, rng)
    territory.gang_id = None
    territory.garrison = 0


def capture_territory(territory: Territory, faction_id: str) -> None:
    """A faction takes a district off a *rival* corp: ownership flips and the
    garrison that just lost the fight is gone.

    Deliberately not claim_territory's twin. That one reseeds modifiers because
    neutral ground's (flat Unrest, no Security) stop describing the place once a
    corp moves in; here the district was already corp-run, so its modifiers — and
    every Location standing on it — carry over to the new owner intact. That is
    the point of taking ground off a rival rather than off nobody: you inherit
    what they built, up to and including a RESEARCH_FACILITY (which is why
    corp_turn.owned_research_facilities is plural).

    Takes no rng, unlike claim_territory: nothing here is randomized.
    """
    territory.owner = faction_id
    territory.garrison = 0


def expansion_candidates(corp_map: CorpMap, faction_id: str) -> list[str]:
    """Neutral territories bordering `faction_id`'s own ground, excluding gang turf
    and the player's start (corp_map.player_start_id) — the same reservation
    corpmap_gen._grow_blocs honors at generation time (a faction never seeds or expands onto
    start_cell), kept alive at runtime so the player's home turf is never swallowed.
    Shared by rivals.py's AI expansion and corp_turn.py's player-directed one."""
    owned = [t for t in corp_map.territories.values() if t.owner == faction_id]
    return sorted(
        {
            conn_id
            for territory in owned
            for conn_id in territory.connections
            if (neighbor := corp_map.territories[conn_id]).owner == "neutral"
            and neighbor.gang_id is None
            and neighbor.id != corp_map.player_start_id
        }
    )


def attack_candidates(corp_map: CorpMap, faction_id: str) -> list[str]:
    """Rival-corp-held territories bordering `faction_id`'s own ground — the
    conflict-layer mirror of expansion_candidates, and the only ground an attack
    can legally target.

    Needs none of expansion_candidates' three exclusions, because each one only
    ever describes ground no corp holds: neutral districts, gang turf (which sits
    on neutral ground by construction — see Territory.gang_id) and the player's
    start (reserved neutral at generation and never claimable, so it can never
    come to be owned by a rival either). Anything with a corp owner that isn't
    yours is fair game.
    """
    owned = [t for t in corp_map.territories.values() if t.owner == faction_id]
    return sorted(
        {
            conn_id
            for territory in owned
            for conn_id in territory.connections
            if corp_map.territories[conn_id].owner not in ("neutral", faction_id)
        }
    )


def territory_distance(corp_map: CorpMap, from_id: str, to_id: str) -> int:
    """Hop count between two territories over the connection graph (BFS), 0 if
    they're the same one. The map is fully connected by construction (every
    territory reaches every other), so a path always exists. Used by jobs.py to
    price a Smuggling job's travel by how far apart its pickup and drop actually are."""
    if from_id == to_id:
        return 0
    visited = {from_id}
    frontier = deque([(from_id, 0)])
    while frontier:
        territory_id, distance = frontier.popleft()
        for neighbor_id in corp_map.territories[territory_id].connections:
            if neighbor_id == to_id:
                return distance + 1
            if neighbor_id not in visited:
                visited.add(neighbor_id)
                frontier.append((neighbor_id, distance + 1))
    raise ValueError(f"no path between {from_id!r} and {to_id!r}")


def _owner_tag(owner: str) -> str:
    if owner in OWNER_TAGS:
        return OWNER_TAGS[owner]
    return owner.upper()[:3]


def _label(territory: Territory, selected_id: str | None, here_id: str | None = None) -> str:
    marker = "*" if territory.id == selected_id else " "
    parts = [territory.name]
    tag = _owner_tag(territory.owner)
    if tag:
        parts.append(tag)
    if territory.id == here_id:
        parts.append("@")
    return f"{marker}[{' '.join(parts)}]"


CONNECTOR_WIDTH = 4


@dataclass(frozen=True)
class NodeSpan:
    """Where one territory's label landed in the rendered text."""

    territory_id: str
    line: int
    start: int  # column within the line, inclusive
    end: int  # column within the line, exclusive
    offset: int  # absolute index into RenderedMap.text


@dataclass
class RenderedMap:
    text: str
    spans: list[NodeSpan]

    def territory_at(self, line: int, column: int) -> str | None:
        for span in self.spans:
            if span.line == line and span.start <= column < span.end:
                return span.territory_id
        return None


def render_ascii_map(
    corp_map: CorpMap, selected_id: str | None = None, here_id: str | None = None
) -> RenderedMap:
    territories = corp_map.territories
    by_pos = {(t.x, t.y): t for t in territories.values()}
    max_col = max(t.x for t in territories.values())
    max_row = max(t.y for t in territories.values())

    col_width = {}
    for col in range(max_col + 1):
        labels = [_label(t, selected_id, here_id) for (c, _), t in by_pos.items() if c == col]
        col_width[col] = (max(len(label) for label in labels) if labels else 0) + 2

    col_offset = {}
    offset = 0
    for col in range(max_col + 1):
        col_offset[col] = offset
        offset += col_width[col] + CONNECTOR_WIDTH
    total_width = offset - CONNECTOR_WIDTH

    lines: list[str] = []
    spans: list[NodeSpan] = []
    for row in range(max_row + 1):
        node_cells = []
        for col in range(max_col + 1):
            t = by_pos.get((col, row))
            label = _label(t, selected_id, here_id) if t else ""
            if t:
                start = col_offset[col]
                spans.append(
                    NodeSpan(
                        territory_id=t.id,
                        line=len(lines),
                        start=start,
                        end=start + len(label),
                        offset=0,
                    )
                )
            right = by_pos.get((col + 1, row))
            linked = bool(t and right and right.id in t.connections)
            # Pad with the connector char too, so the line reaches the label
            # instead of leaving a ragged gap after short names.
            connector = "-" * CONNECTOR_WIDTH if linked else " " * CONNECTOR_WIDTH
            is_last_col = col == max_col
            padded = label.ljust(col_width[col], "-" if linked else " ")
            node_cells.append(padded + ("" if is_last_col else connector))
        lines.append("".join(node_cells).rstrip())

        if row == max_row:
            continue
        connector_line = [" "] * total_width
        for col in range(max_col + 1):
            t = by_pos.get((col, row))
            below = by_pos.get((col, row + 1))
            if t and below and below.id in t.connections:
                connector_line[col_offset[col] + 1] = "|"
        lines.append("".join(connector_line).rstrip())

    line_start = {}
    cursor = 0
    for index, line in enumerate(lines):
        line_start[index] = cursor
        cursor += len(line) + 1  # +1 for the newline joining it to the next

    spans = [
        NodeSpan(
            territory_id=span.territory_id,
            line=span.line,
            start=span.start,
            end=span.end,
            offset=line_start[span.line] + span.start,
        )
        for span in spans
    ]

    return RenderedMap(text="\n".join(lines), spans=spans)

def _clamp(level: int) -> int:
    return max(0, min(MODIFIER_MAX, level))


def _development(security: int, surveillance: int, unrest: int) -> int:
    """Capital only lands where the block is policed, watched and quiet.

    Derived rather than rolled, so a holder's Development can never contradict
    the levers that produce it — you raise it by raising Security and
    Surveillance and putting the street down, not on its own. Governs held
    ground only; neutral ground rolls its own (see _neutral_modifiers).
    """
    return _clamp((security + surveillance - unrest + 1) // 2)


def _corp_modifiers(value: int, rng: random.Random) -> dict[TerritoryModifier, int]:
    """Corp turf: garrisoned and watched in proportion to what it earns."""
    security = _clamp(value + rng.randint(-1, 1))
    surveillance = _clamp(value + rng.randint(-1, 1))
    unrest = rng.randint(0, 2)
    return {
        TerritoryModifier.SECURITY: security,
        TerritoryModifier.SURVEILLANCE: surveillance,
        TerritoryModifier.UNREST: unrest,
        TerritoryModifier.DEVELOPMENT: _development(security, surveillance, unrest),
        TerritoryModifier.RESTRICTED: rng.randint(2, MODIFIER_MAX),
    }


def _neutral_modifiers(rng: random.Random) -> dict[TerritoryModifier, int]:
    """Ground nobody holds, and the whole profile of it, in one place.

    Nobody watches it, nobody polices its market, the street runs it (full
    unrest), and the token security is whoever happens to be holding the door.
    What little stands there got built without an owner investing in it, so
    Development is rolled outright rather than run through _development — which
    would pin every neutral node to 0. This is the one place it escapes that
    formula, on purpose.
    """
    return {
        TerritoryModifier.SECURITY: 1,
        TerritoryModifier.SURVEILLANCE: 0,
        TerritoryModifier.UNREST: MODIFIER_MAX,
        TerritoryModifier.DEVELOPMENT: rng.randint(1, 2),
        TerritoryModifier.RESTRICTED: 0,
    }


def make_modifiers(owner: str, value: int, rng: random.Random) -> dict[TerritoryModifier, int]:
    """Seed a district's levers. Held ground and open ground, one rule each.

    Public (not underscore-private) because corpmap_gen calls it for every district
    it lays out. The whole modifier cluster stays on this side of that split rather
    than moving with the generator: `claim_territory` is runtime gameplay (a rival's
    expansion, the player's own) and reseeds a district through `_corp_modifiers` the
    same way generation does, so these are the rules for what a district's levers
    *are*, not for where districts go.
    """
    if owner in FACTIONS_BY_ID:
        return _corp_modifiers(value, rng)
    return _neutral_modifiers(rng)
