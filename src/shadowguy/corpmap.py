import random
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.factions import (
    CORP_OFFICER_TIERS,
    FACTIONS,
    FACTIONS_BY_ID,
    Faction,
    FactionSpecialty,
)
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
_OWNER_COLOR_VALUES = ["red", "cyan", "green"]
OWNER_COLORS = dict(zip((faction.id for faction in FACTIONS), _OWNER_COLOR_VALUES, strict=True))


def owner_label(owner: str) -> str:
    if owner in OWNER_NAMES:
        return OWNER_NAMES[owner]
    return FACTIONS_BY_ID[owner].name


class LocationKind(StrEnum):
    DATA = "data"
    LAB = "lab"
    DEPOT = "depot"
    SOCIAL = "social"
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


# The runner's own places — their home, and any safehouse they come to hold. One
# concept, two consequences: a place the runner owns is injected into a territory
# rather than rolled onto the map (so it's excluded from GENERATED_KINDS below), and
# the runner sleeps in it for free (lodging_cost). Add a kind here and it gets both.
PLAYER_OWNED_KINDS = (LocationKind.APARTMENT, LocationKind.SAFEHOUSE)

# Kinds injected into specific districts rather than rolled onto the map, and carrying
# none of the per-kind world tables below: the runner's own places, and each corp's HQ
# (which has its own officers and screen — see _make_hq / app.CorpHQScreen). The HQ is a
# corp fixture, not player-owned, so it's a separate group from PLAYER_OWNED_KINDS.
UNROLLED_KINDS = (*PLAYER_OWNED_KINDS, LocationKind.CORP_HQ)

# Kinds the world generator gives the full per-kind treatment: everything with a real
# storefront/job surface. They're a job target, scouted on legwork and run by generic
# NPCs, so the per-kind tables (LOCATION_SKILL, LOCATION_ROLES, gigs._GIG_TEMPLATES,
# jobs.LEGWORK_APPROACH_TEXT) carry exactly one entry each — every guard checks against
# GENERATED_KINDS, not the full enum, so the UNROLLED_KINDS above stay out of them.
GENERATED_KINDS = tuple(k for k in LocationKind if k not in UNROLLED_KINDS)

# Hospitals are placed to a fixed count (generate_corp_map / HOSPITAL_COUNT) rather than
# rolled in with everything else, so every map has about the same healing access instead
# of it swinging with the location lottery. So the random location pools draw from
# everything generated *except* the hospital. It still needs the per-kind world tables
# (it can be a job site, and gigs spawn there), so it stays in GENERATED_KINDS — that's
# what the import guards check against.
ROLLED_KINDS = tuple(k for k in GENERATED_KINDS if k is not LocationKind.HOSPITAL)


# Retail kinds: shops.py's business, but defined here (not there) since
# _location_kinds below needs them and corpmap.py must not import shops.py.
SHOP_KINDS = (
    LocationKind.PAWN,
    LocationKind.WEAPON_SHOP,
    LocationKind.AUTO_DEALER,
    LocationKind.PHARMACY,
    LocationKind.COMPUTER_STORE,
)

# The skill a location kind is scouted with, on legwork. jobs.py owns the flavor
# text for each kind (jobs.LEGWORK_APPROACH_TEXT) and reads the skill from here,
# so there is exactly one place that says "DATA is a Hack check" — _location_kinds
# below also needs it, to keep a district's filler slot from repeating its own
# specialty's stat (via location_stat() below).
#
# Legwork is scouting, so this table leans on the watching-and-casing skills:
# perception and agility mostly, intelligence on the wired places, cool where
# the read comes out of a conversation.
LOCATION_SKILL = {
    LocationKind.DATA: "hack",
    LocationKind.LAB: "pattern_seeking",
    LocationKind.DEPOT: "stealth",
    LocationKind.SOCIAL: "read_the_room",
    LocationKind.PAWN: "negotiations",
    LocationKind.WEAPON_SHOP: "sight",
    LocationKind.AUTO_DEALER: "deception",
    LocationKind.PHARMACY: "infer",
    LocationKind.COMPUTER_STORE: "hack",
    LocationKind.HOSPITAL: "infer",
    LocationKind.REAL_ESTATE: "read_the_room",
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
    """The levers a corp pulls on ground it holds. Displayed only, so far."""

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
    # cross-map portfolio, sampled once at generation (see generate_corp_map).
    listings: list[str] = field(default_factory=list)


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


@dataclass
class CorpMap:
    territories: dict[str, Territory]
    player_start_id: str

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
# to bed down in. Charged on rest() — see app.MainMenu's end-of-day handler.
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
        Location(id=f"{territory.id}_safehouse", name="Your Safehouse", kind=LocationKind.SAFEHOUSE)
    )


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


CONNECTOR_WIDTH = 6


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


# The grid is deliberately roomier than TERRITORY_COUNT: the leftover cells are
# the holes that keep _grow_region's blob from degenerating into a full rectangle.
GRID_COLS = 8
GRID_ROWS = 6
TERRITORY_COUNT = 38
TERRITORIES_PER_FACTION = 6

# Every faction is handed exactly this multiset of values, so equal territory
# count and equal total value are guaranteed by construction rather than found
# by searching for a fair partition. Must stay TERRITORIES_PER_FACTION long —
# that one-to-one is what makes the guarantee free.
FACTION_VALUE_SPREAD = (3, 3, 2, 2, 1, 1)

NEUTRAL_VALUES = (1, 2, 3)

# The runner starts on unclaimed ground at the rim of the map. Demand a way out of
# it: a start with one connection makes every trip a there-and-back.
MIN_START_DEGREE = 2

# How many districts a single real estate office has safehouses for sale in. A short
# portfolio rather than the whole market, so offices differ and the list stays readable.
REAL_ESTATE_LISTING_COUNT = 4

# About one hospital per this many districts, placed to a fixed count (see
# generate_corp_map) so healing access is even across every map. round() keeps it close
# for a TERRITORY_COUNT the ratio doesn't divide evenly.
TILES_PER_HOSPITAL = 5
HOSPITAL_COUNT = round(TERRITORY_COUNT / TILES_PER_HOSPITAL)

# Chance that a grid-adjacent pair not already joined by the spanning tree gets
# an edge anyway. Higher = loopier map with more flanking routes.
EXTRA_EDGE_CHANCE = 0.35

# Must comfortably exceed TERRITORY_COUNT: names are sampled without replacement,
# and the surplus is what keeps two runs from drawing the same district list.
# Single words only — a territory's id is its lowercased name, and that id ends up
# inside Textual widget ids (see MainMenu's "local_" rows), which cannot hold spaces.
DISTRICT_NAMES = [
    "Kabuki", "Northside", "Watson", "Pacifica", "Heywood", "Westbrook",
    "Rancho", "Arroyo", "Coastview", "Glen", "Vista", "Charter",
    "Downtown", "Japantown", "Badlands", "Autopia", "Dogtown", "Longshore",
    "Sunset", "Harbor", "Foundry", "Terminal", "Spire", "Ashgrove",
    "Riverside", "Steelyard", "Saltflats", "Greywater", "Lowline", "Highgate",
    "Ember", "Solace", "Quarry", "Blackstack", "Neon", "Drydock",
    "Junction", "Marrow", "Halberd", "Verdant", "Slagworks", "Prospect",
    "Kingsway", "Ravine", "Tannery", "Cathode", "Bracken", "Silo",
]

# A district holds a variable number of locations — roomier now there are more kinds to
# draw from. A territory that also gets an injected place (the runner's apartment on the
# start node, or a hospital) rolls one fewer, so the total still caps at MAX (see
# generate_corp_map). Both bounds are inclusive.
MIN_LOCATIONS_PER_TERRITORY = 4
MAX_LOCATIONS_PER_TERRITORY = 6

# How many of a corp-held district's locations are the corp's own kind of place. The rest
# are random filler slots (see FILLER_KINDS below) — the bar everyone drinks in, or one of
# the shops, whoever owns the block.
SPECIALTY_LOCATIONS = 2

# The most filler a district can want: the biggest district (MAX locations) minus its
# specialty pair. _filler_pool must be able to supply this for every specialty (guarded
# below), or rng.sample would raise mid-generation.
MAX_FILLER_COUNT = MAX_LOCATIONS_PER_TERRITORY - SPECIALTY_LOCATIONS

LOCATION_KIND_FOR_SPECIALTY = {
    FactionSpecialty.WEAPONS: LocationKind.DEPOT,
    FactionSpecialty.HACKING: LocationKind.DATA,
    FactionSpecialty.PHARMA: LocationKind.LAB,
}

LOCATION_SUFFIXES = {
    LocationKind.DATA: ["Data Vault", "Server Stack", "Relay Hub", "Net Exchange"],
    LocationKind.LAB: ["Clinic", "Biolab", "Dispensary", "Trauma Ward"],
    LocationKind.DEPOT: ["Depot", "Armory", "Freight Yard", "Loading Dock"],
    LocationKind.SOCIAL: ["Bar", "Noodle House", "Club", "Pachinko Parlor"],
    LocationKind.PAWN: ["Pawn Shop", "Loan & Trade", "Cash 4 Chrome", "Buy-Sell-Trade"],
    LocationKind.WEAPON_SHOP: ["Gun Shop", "Arms Dealer", "Ironmonger", "Ballistics Outlet"],
    LocationKind.AUTO_DEALER: ["Auto Dealer", "Motorpool", "Garage", "Chop Shop"],
    LocationKind.PHARMACY: ["Pharmacy", "Chemist", "Drug Store", "Apothecary"],
    LocationKind.COMPUTER_STORE: ["Computer Store", "Chip Shop", "Hardware Outlet", "Rig Emporium"],
    LocationKind.HOSPITAL: ["Hospital", "Trauma Center", "Emergency Room", "Med Center"],
    LocationKind.REAL_ESTATE: ["Realty", "Properties", "Holdings", "Estate Agency"],
}

LOCATION_PREFIXES = [
    "Grayline", "Halcyon", "Pier 9", "Black Sun", "Kestrel", "Ninth Street",
    "Redline", "Verge", "Saint Lazarus", "Copperhead", "Mirage", "Low Tide",
    "Gantry", "Hollow Point", "Tin City", "Nightjar", "Sunken", "Vector",
    "Cinder", "Palisade", "Ashline", "Dead Man's",
]

# Street handles for the people who run/haunt locations. Sampled distinct within one
# location; repeats across the map are fine, since standing is keyed by LocalCharacter.id
# (which is location-scoped and unique), not by name.
CHARACTER_NAMES = [
    "Kite", "Mube", "Vesh", "Doc Aluko", "Sparrow", "Tallow", "Nix", "Rue",
    "Gethin", "Onyx", "Marisol", "Breaker", "Coil", "Suri", "Fenn", "Locke",
    "Amp", "Devi", "Praxis", "Wren", "Cutter", "Halo", "Jettison", "Mara",
    "Oki", "Rho", "Salt", "Torque", "Vandal", "Yara",
]

# The role each location kind's characters read as, for flavor and to tell two
# characters at one venue apart. Non-shop kinds can roll two characters, so they
# need at least two distinct roles (guarded below); shops need only their owner.
LOCATION_ROLES: dict[LocationKind, tuple[str, ...]] = {
    LocationKind.DATA: ("netrunner", "data broker", "sysop"),
    LocationKind.LAB: ("ripperdoc", "chemist", "lab tech"),
    LocationKind.DEPOT: ("quartermaster", "dockhand", "fixer's runner"),
    LocationKind.SOCIAL: ("bartender", "regular", "bouncer", "hustler"),
    LocationKind.PAWN: ("pawnbroker",),
    LocationKind.WEAPON_SHOP: ("gunsmith",),
    LocationKind.AUTO_DEALER: ("dealer",),
    LocationKind.PHARMACY: ("pharmacist",),
    LocationKind.COMPUTER_STORE: ("techie",),
    LocationKind.HOSPITAL: ("trauma surgeon", "triage nurse", "orderly"),
    LocationKind.REAL_ESTATE: ("realtor", "property broker", "landlord"),
}

# The most locations of one kind a map can want: the faction whose specialty it is
# takes SPECIALTY_LOCATIONS in each of its own districts, and every other district
# can still roll one (a district's kinds are a distinct sample, so at most one each).
# _unique_location_name retries forever on a name collision, so an undersized pool hangs
# generation rather than raising — hence the guard below.
MAX_SAME_KIND_LOCATIONS = TERRITORIES_PER_FACTION * SPECIALTY_LOCATIONS + (
    TERRITORY_COUNT - TERRITORIES_PER_FACTION
)

# Everything the generator needs is a module constant, so these are import-time
# facts. Only the faction count depends on the caller — that guard lives in
# generate_corp_map.
if TERRITORY_COUNT > GRID_COLS * GRID_ROWS:
    raise ValueError("grid is too small to hold TERRITORY_COUNT territories")
if TERRITORY_COUNT > len(DISTRICT_NAMES):
    raise ValueError("not enough DISTRICT_NAMES to name TERRITORY_COUNT territories")
if len(FACTION_VALUE_SPREAD) != TERRITORIES_PER_FACTION:
    raise ValueError("FACTION_VALUE_SPREAD must hold one value per faction territory")
if len(LOCATION_PREFIXES) * min(len(s) for s in LOCATION_SUFFIXES.values()) < (
    MAX_SAME_KIND_LOCATIONS
):
    raise ValueError("not enough LOCATION_PREFIXES/LOCATION_SUFFIXES to name every location")
# _make_characters samples distinct roles up to MAX_CHARACTERS_PER_LOCATION, so every
# kind that can roll two characters must offer at least two roles for rng.sample; shops
# roll one, so one role is enough for them. A short list would make rng.sample raise
# mid-generation, hence the import-time proof.
MAX_CHARACTERS_PER_LOCATION = 2
if set(LOCATION_ROLES) != set(GENERATED_KINDS):
    raise ValueError("LOCATION_ROLES must have exactly one entry per generated LocationKind")
for _kind, _roles in LOCATION_ROLES.items():
    _needed = 1 if _kind in SHOP_KINDS else MAX_CHARACTERS_PER_LOCATION
    if len(_roles) < _needed:
        raise ValueError(f"LOCATION_ROLES[{_kind}] needs at least {_needed} roles")
if len(CHARACTER_NAMES) < MAX_CHARACTERS_PER_LOCATION:
    raise ValueError("CHARACTER_NAMES too small to name a location's characters")
# An HQ's officers are one distinct name per CORP_OFFICER_TIERS rank (see _make_officers).
if len(CHARACTER_NAMES) < len(CORP_OFFICER_TIERS):
    raise ValueError("CHARACTER_NAMES too small to name an HQ's officers")

Cell = tuple[int, int]


# The non-specialty slots in a corp district: the bar everyone drinks in, or a
# shop — whoever owns the block, the storefront doesn't care.
FILLER_KINDS = (LocationKind.SOCIAL, *SHOP_KINDS)


def _filler_pool(owned_kind: LocationKind) -> list[LocationKind]:
    """Filler kinds that don't repeat the specialty's own stat.

    A district is SPECIALTY_LOCATIONS of one kind plus filler, so a filler that
    shared the specialty's stat (e.g. COMPUTER_STORE, also intelligence, next to
    a Hacking corp's DATA) would make that district's legwork three checks of one
    stat and no real choice.
    """
    owned_stat = location_stat(owned_kind)
    return [kind for kind in FILLER_KINDS if location_stat(kind) != owned_stat]


# rng.sample() below raises if the pool ever runs short, so prove at import that it
# can't: every specialty a faction can have must leave MAX_FILLER_COUNT fillers, enough
# to fill even the largest district off the specialty's own stat.
for _specialty_kind in LOCATION_KIND_FOR_SPECIALTY.values():
    if len(_filler_pool(_specialty_kind)) < MAX_FILLER_COUNT:
        raise ValueError(
            f"LOCATION_SKILL leaves too few filler kinds off {_specialty_kind}'s own stat"
        )


def _location_kinds(owner: str, rng: random.Random, count: int) -> list[LocationKind]:
    faction = FACTIONS_BY_ID.get(owner)
    if faction is None:
        # Neutral ground and the player's block carry no corp's stamp. Hospitals aren't
        # in ROLLED_KINDS — they're placed to a fixed density in generate_corp_map.
        return rng.sample(list(ROLLED_KINDS), k=count)
    owned_kind = LOCATION_KIND_FOR_SPECIALTY[faction.specialty]
    filler = rng.sample(_filler_pool(owned_kind), k=count - SPECIALTY_LOCATIONS)
    return [owned_kind] * SPECIALTY_LOCATIONS + filler


def _make_characters(location_id: str, kind: LocationKind, rng: random.Random) -> list[LocalCharacter]:
    """One character for a shop (its owner), 1–2 for anywhere else. Names and roles are
    distinct within the location; ids (the standing key) are unique by construction."""
    count = 1 if kind in SHOP_KINDS else rng.randint(1, MAX_CHARACTERS_PER_LOCATION)
    names = rng.sample(CHARACTER_NAMES, count)
    roles = rng.sample(LOCATION_ROLES[kind], count)
    return [
        LocalCharacter(id=f"{location_id}_p{i}", name=names[i], role=roles[i])
        for i in range(count)
    ]


def _unique_location_name(kind: LocationKind, rng: random.Random, used_names: set[str]) -> str:
    """A prefix+suffix name for this kind not yet used anywhere on the map."""
    while True:
        name = f"{rng.choice(LOCATION_PREFIXES)} {rng.choice(LOCATION_SUFFIXES[kind])}"
        if name not in used_names:
            used_names.add(name)
            return name


def _make_locations(
    territory_id: str, owner: str, rng: random.Random, used_names: set[str], count: int
) -> list[Location]:
    locations = []
    for index, kind in enumerate(_location_kinds(owner, rng, count)):
        location_id = f"{territory_id}_loc{index}"
        locations.append(
            Location(
                id=location_id,
                name=_unique_location_name(kind, rng, used_names),
                kind=kind,
                characters=_make_characters(location_id, kind, rng),
            )
        )
    return locations


def _make_hospital(territory_id: str, rng: random.Random, used_names: set[str]) -> Location:
    """A hospital placed on a district out of band from the location roll (see
    HOSPITAL_COUNT). At most one per territory, so the fixed id can't collide."""
    location_id = f"{territory_id}_hospital"
    return Location(
        id=location_id,
        name=_unique_location_name(LocationKind.HOSPITAL, rng, used_names),
        kind=LocationKind.HOSPITAL,
        characters=_make_characters(location_id, LocationKind.HOSPITAL, rng),
    )


def _make_officers(location_id: str, rng: random.Random) -> list[LocalCharacter]:
    """The corporate officers manning an HQ: one per CORP_OFFICER_TIERS rank, in that
    order, so app.CorpHQScreen can line each up with its rep/standing gate by index.
    Ids follow the standard location-scoped scheme, though HQ standing isn't moved yet."""
    names = rng.sample(CHARACTER_NAMES, len(CORP_OFFICER_TIERS))
    return [
        LocalCharacter(id=f"{location_id}_p{i}", name=names[i], role=role)
        for i, (role, _min_rep, _min_standing) in enumerate(CORP_OFFICER_TIERS)
    ]


def _make_hq(territory_id: str, faction: Faction, rng: random.Random) -> Location:
    """A corp's headquarters — one per faction, injected into a top-value district it owns
    (see generate_corp_map). Not a rolled kind: it has its own officers and screen rather
    than the gig/legwork/job treatment. At most one per territory, so the id can't collide."""
    location_id = f"{territory_id}_hq"
    return Location(
        id=location_id,
        name=f"{faction.name} HQ",
        kind=LocationKind.CORP_HQ,
        characters=_make_officers(location_id, rng),
    )


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


def _make_modifiers(owner: str, value: int, rng: random.Random) -> dict[TerritoryModifier, int]:
    """Seed a district's levers. Held ground and open ground, one rule each."""
    if owner in FACTIONS_BY_ID:
        return _corp_modifiers(value, rng)
    return _neutral_modifiers(rng)


def _neighbors(cell: Cell) -> list[Cell]:
    x, y = cell
    candidates = [(x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)]
    return [(cx, cy) for cx, cy in candidates if 0 <= cx < GRID_COLS and 0 <= cy < GRID_ROWS]


def _grow_region(rng: random.Random) -> list[Cell]:
    """Pick TERRITORY_COUNT grid cells forming one orthogonally-contiguous blob."""
    start = (rng.randrange(GRID_COLS), rng.randrange(GRID_ROWS))
    region = {start}
    frontier = set(_neighbors(start))
    while len(region) < TERRITORY_COUNT:
        cell = rng.choice(sorted(frontier))
        region.add(cell)
        frontier.discard(cell)
        frontier.update(n for n in _neighbors(cell) if n not in region)
    return sorted(region)


def _connect(region: list[Cell], rng: random.Random) -> set[frozenset[Cell]]:
    """Spanning tree over the region (so the map is always connected), plus some loops."""
    region_set = set(region)
    visited = {rng.choice(region)}
    edges: set[frozenset[Cell]] = set()
    while len(visited) < len(region):
        candidates = sorted(
            (cell, n)
            for cell in visited
            for n in _neighbors(cell)
            if n in region_set and n not in visited
        )
        cell, neighbor = rng.choice(candidates)
        edges.add(frozenset((cell, neighbor)))
        visited.add(neighbor)

    for cell in region:
        for neighbor in _neighbors(cell):
            if neighbor in region_set and rng.random() < EXTRA_EDGE_CHANCE:
                edges.add(frozenset((cell, neighbor)))
    return edges


def _on_grid_edge(cell: Cell) -> bool:
    x, y = cell
    return x in (0, GRID_COLS - 1) or y in (0, GRID_ROWS - 1)


def _player_start(region: list[Cell], edges: set[frozenset[Cell]], rng: random.Random) -> Cell:
    """Unclaimed ground out on the rim of the city — the runner is a nobody from nowhere.

    The rim is also where the dead ends are, so demand a way out: MIN_START_DEGREE
    connections. A degree-1 start makes every trip a there-and-back and taxes a
    stamina budget that already has to cover gigs, jobs and legwork.
    """
    degree = Counter(cell for edge in edges for cell in edge)
    candidates = [c for c in region if _on_grid_edge(c) and degree[c] >= MIN_START_DEGREE]
    if not candidates:
        candidates = [c for c in region if degree[c] >= MIN_START_DEGREE]
    return rng.choice(sorted(candidates))


def _grow_blocs(
    region: list[Cell],
    edges: set[frozenset[Cell]],
    start_cell: Cell,
    faction_ids: list[str],
    rng: random.Random,
) -> dict[Cell, str] | None:
    """Race one contiguous bloc per faction outward from random seeds.

    start_cell is reserved but never claimed: the runner's block has to still be
    unclaimed when the blocs stop growing, so no faction may seed or expand onto it.

    Returns None if a bloc gets boxed in before reaching its quota; the caller
    retries with fresh seeds.
    """
    graph: dict[Cell, set[Cell]] = {cell: set() for cell in region}
    for edge in edges:
        a, b = tuple(edge)
        graph[a].add(b)
        graph[b].add(a)

    available = [cell for cell in region if cell != start_cell]
    seeds = rng.sample(available, k=len(faction_ids))
    owners: dict[Cell, str] = {}
    blocs: dict[str, set[Cell]] = {}
    for faction_id, seed in zip(faction_ids, seeds):
        owners[seed] = faction_id
        blocs[faction_id] = {seed}

    for _ in range(TERRITORIES_PER_FACTION - 1):
        for faction_id in faction_ids:
            bloc = blocs[faction_id]
            frontier = sorted(
                {
                    n
                    for cell in bloc
                    for n in graph[cell]
                    if n not in owners and n != start_cell
                }
            )
            if not frontier:
                return None
            claimed = rng.choice(frontier)
            owners[claimed] = faction_id
            bloc.add(claimed)
    return owners


MAX_GENERATION_ATTEMPTS = 100


def generate_corp_map(factions: list[Faction], rng: random.Random) -> CorpMap:
    faction_ids = [f.id for f in factions]
    if len(faction_ids) * TERRITORIES_PER_FACTION + 1 > TERRITORY_COUNT:
        raise ValueError("not enough territories to give every faction a full bloc")

    for _ in range(MAX_GENERATION_ATTEMPTS):
        region = _grow_region(rng)
        edges = _connect(region, rng)
        start_cell = _player_start(region, edges, rng)
        owners = _grow_blocs(region, edges, start_cell, faction_ids, rng)
        if owners is not None:
            break
    else:
        raise RuntimeError("could not lay out contiguous faction blocs")

    # start_cell is left out of owners, so the loop below gives it a neutral value
    # just like any other unclaimed district.
    values: dict[Cell, int] = {}
    for faction_id in faction_ids:
        bloc = sorted(cell for cell, owner in owners.items() if owner == faction_id)
        spread = list(FACTION_VALUE_SPREAD)
        rng.shuffle(spread)
        values.update(zip(bloc, spread))
    for cell in region:
        if cell not in owners:
            values[cell] = rng.choice(NEUTRAL_VALUES)

    names = rng.sample(DISTRICT_NAMES, k=len(region))
    ids = {cell: name.lower() for cell, name in zip(region, names)}
    start_id = ids[start_cell]

    # Which districts get a hospital, picked up front (about one per TILES_PER_HOSPITAL)
    # so the count roll below can reserve a slot for it. The start is left out — it gets
    # the apartment instead.
    elsewhere = [ids[cell] for cell in region if cell != start_cell]
    hospital_ids = set(rng.sample(elsewhere, HOSPITAL_COUNT))

    # One HQ per corp, seated in one of that corp's highest-value districts — the seat of
    # power. Chosen up front (like the hospitals) so its district can reserve a slot for
    # the injected HQ. A district can be drawn for both a hospital and an HQ; the reserve
    # below counts each, and MAX - MIN (6 - 4) leaves room for the two together — the start
    # (neutral, so never an HQ; excluded from the hospital draw) never stacks past one.
    top_value = max(FACTION_VALUE_SPREAD)
    hq_ids: dict[str, str] = {}
    for faction_id in faction_ids:
        top_cells = sorted(
            cell for cell, owner in owners.items()
            if owner == faction_id and values[cell] == top_value
        )
        hq_ids[ids[rng.choice(top_cells)]] = faction_id

    territories = {}
    used_names: set[str] = set()
    for cell, name in zip(region, names):
        x, y = cell
        tid = ids[cell]
        owner = owners.get(cell, "neutral")
        reserved = (tid == start_id) + (tid in hospital_ids) + (tid in hq_ids)
        count = rng.randint(MIN_LOCATIONS_PER_TERRITORY, MAX_LOCATIONS_PER_TERRITORY - reserved)
        territories[tid] = Territory(
            id=tid,
            name=name,
            x=x,
            y=y,
            owner=owner,
            value=values[cell],
            connections=sorted(
                ids[other]
                for other in region
                if frozenset((cell, other)) in edges
            ),
            locations=_make_locations(tid, owner, rng, used_names, count),
            modifiers=_make_modifiers(owner, values[cell], rng),
        )

    # The runner's home: a fixed, player-owned place in the start district, injected
    # rather than rolled (see GENERATED_KINDS). No owner NPC — it's the runner's own.
    start = territories[start_id]
    start.locations.insert(
        0,
        Location(id=f"{start.id}_apartment", name="Your Apartment", kind=LocationKind.APARTMENT),
    )

    # Hospitals to a fixed density (about one per TILES_PER_HOSPITAL) rather than rolled
    # in, so healing access is even on every map — one added to each district chosen above.
    for tid in hospital_ids:
        territories[tid].locations.append(_make_hospital(tid, rng, used_names))

    # Seat each corp's HQ in the top-value district chosen above, injected like the hospital.
    for tid, faction_id in hq_ids.items():
        territories[tid].locations.append(_make_hq(tid, FACTIONS_BY_ID[faction_id], rng))

    # Hand every real estate office a portfolio of districts to sell safehouses in.
    # Anywhere the runner doesn't already own (i.e. not the start) is fair game; a
    # district is filtered back out of the listing once bought (see has_home).
    for_sale = [tid for tid in territories if tid != start.id]
    for territory in territories.values():
        for location in territory.locations:
            if location.kind is LocationKind.REAL_ESTATE:
                count = min(REAL_ESTATE_LISTING_COUNT, len(for_sale))
                location.listings = rng.sample(for_sale, k=count)

    return CorpMap(territories=territories, player_start_id=ids[start_cell])
