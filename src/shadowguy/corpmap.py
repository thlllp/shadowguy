import random
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.factions import FACTIONS, FACTIONS_BY_ID, Faction, FactionSpecialty

OWNER_NAMES = {"neutral": "Unclaimed"}

# No "player" owner: the runner starts standing on unclaimed ground, not holding it.
# The map marks where the runner *is* with @ (see _label), not with a corp tag.
OWNER_TAGS = {
    "neutral": "",
    **{faction.id: faction.name.split()[0][:3].upper() for faction in FACTIONS},
}


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


# Retail kinds: shops.py's business, but defined here (not there) since
# _location_kinds below needs them and corpmap.py must not import shops.py.
SHOP_KINDS = (
    LocationKind.PAWN,
    LocationKind.WEAPON_SHOP,
    LocationKind.AUTO_DEALER,
    LocationKind.PHARMACY,
    LocationKind.COMPUTER_STORE,
)

# The check stat a location kind is scouted with. jobs.py owns the flavor text
# for each kind (jobs.LEGWORK_APPROACH_TEXT) and reads the stat from here, so
# there is exactly one place that says "DATA is an intelligence check" — _location_kinds
# below also needs it, to keep a district's filler slot from repeating its own
# specialty's stat (see FILLER_EXCLUDED_STATS).
LOCATION_STAT = {
    LocationKind.DATA: "intelligence",
    LocationKind.LAB: "intelligence",
    LocationKind.DEPOT: "body",
    LocationKind.SOCIAL: "cool",
    LocationKind.PAWN: "cool",
    LocationKind.WEAPON_SHOP: "body",
    LocationKind.AUTO_DEALER: "cool",
    LocationKind.PHARMACY: "intelligence",
    LocationKind.COMPUTER_STORE: "intelligence",
}
if set(LOCATION_STAT) != set(LocationKind):
    raise ValueError("LOCATION_STAT must have exactly one entry per LocationKind")


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
class Location:
    """A concrete place inside a Territory — what a job actually hits."""

    id: str
    name: str
    kind: LocationKind


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
        col_width[col] = (max(len(label) for label in labels) if labels else 0) + 1

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

LOCATIONS_PER_TERRITORY = 3

# How many of a corp-held district's locations are the corp's own kind of place.
# The rest is one random filler slot (see FILLER_KINDS below) — the bar
# everyone drinks in, or one of the shops, whoever owns the block.
SPECIALTY_LOCATIONS = 2

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
}

LOCATION_PREFIXES = [
    "Grayline", "Halcyon", "Pier 9", "Black Sun", "Kestrel", "Ninth Street",
    "Redline", "Verge", "Saint Lazarus", "Copperhead", "Mirage", "Low Tide",
    "Gantry", "Hollow Point", "Tin City", "Nightjar", "Sunken", "Vector",
    "Cinder", "Palisade", "Ashline", "Dead Man's",
]

# The most locations of one kind a map can want: the faction whose specialty it is
# takes SPECIALTY_LOCATIONS in each of its own districts, and every other district
# can still roll one. _make_locations retries forever on a name collision, so an
# undersized pool hangs generation rather than raising — hence the guard below.
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

Cell = tuple[int, int]


# The non-specialty slots in a corp district: the bar everyone drinks in, or a
# shop — whoever owns the block, the storefront doesn't care.
FILLER_KINDS = (LocationKind.SOCIAL, *SHOP_KINDS)


def _location_kinds(owner: str, rng: random.Random) -> list[LocationKind]:
    faction = FACTIONS_BY_ID.get(owner)
    if faction is None:
        # Neutral ground and the player's block carry no corp's stamp.
        return rng.sample(list(LocationKind), k=LOCATIONS_PER_TERRITORY)
    owned_kind = LOCATION_KIND_FOR_SPECIALTY[faction.specialty]
    filler_count = LOCATIONS_PER_TERRITORY - SPECIALTY_LOCATIONS
    # Keep the filler slot(s) off the specialty's own stat (see LOCATION_STAT)
    # — a shop that happens to share it (e.g. PHARMACY and COMPUTER_STORE are
    # both "intelligence", same as DATA/LAB) would otherwise give that district's
    # legwork three checks of one stat and no real choice.
    owned_stat = LOCATION_STAT[owned_kind]
    filler_pool = [kind for kind in FILLER_KINDS if LOCATION_STAT[kind] != owned_stat]
    filler = rng.sample(filler_pool, k=filler_count)
    return [owned_kind] * SPECIALTY_LOCATIONS + filler


def _make_locations(
    territory_id: str, owner: str, rng: random.Random, used_names: set[str]
) -> list[Location]:
    locations = []
    for index, kind in enumerate(_location_kinds(owner, rng)):
        while True:
            name = f"{rng.choice(LOCATION_PREFIXES)} {rng.choice(LOCATION_SUFFIXES[kind])}"
            if name not in used_names:
                break
        used_names.add(name)
        locations.append(Location(id=f"{territory_id}_loc{index}", name=name, kind=kind))
    return locations


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

    territories = {}
    used_names: set[str] = set()
    for cell, name in zip(region, names):
        x, y = cell
        owner = owners.get(cell, "neutral")
        territories[ids[cell]] = Territory(
            id=ids[cell],
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
            locations=_make_locations(ids[cell], owner, rng, used_names),
            modifiers=_make_modifiers(owner, values[cell], rng),
        )

    return CorpMap(territories=territories, player_start_id=ids[start_cell])
