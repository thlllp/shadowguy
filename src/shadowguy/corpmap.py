import random
from dataclasses import dataclass, field
from enum import StrEnum

from shadowguy.factions import FACTIONS, FACTIONS_BY_ID, Faction, FactionSpecialty

OWNER_NAMES = {"player": "You", "neutral": "Unclaimed"}

OWNER_TAGS = {
    "player": "YOU",
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


GRID_COLS = 6
GRID_ROWS = 4
TERRITORY_COUNT = 18
TERRITORIES_PER_FACTION = 4

# Every faction is handed exactly this multiset of values, so equal territory
# count and equal total value are guaranteed by construction rather than found
# by searching for a fair partition.
FACTION_VALUE_SPREAD = (3, 2, 2, 1)

PLAYER_START_VALUE = 4
NEUTRAL_VALUES = (1, 2, 3)

# Chance that a grid-adjacent pair not already joined by the spanning tree gets
# an edge anyway. Higher = loopier map with more flanking routes.
EXTRA_EDGE_CHANCE = 0.35

DISTRICT_NAMES = [
    "Kabuki", "Northside", "Watson", "Pacifica", "Heywood", "Westbrook",
    "Rancho", "Arroyo", "Coastview", "Glen", "Vista", "Charter",
    "Downtown", "Japantown", "Badlands", "Autopia", "Dogtown", "Longshore",
    "Sunset", "Harbor", "Foundry", "Terminal", "Spire", "Ashgrove",
]

LOCATIONS_PER_TERRITORY = 3

# How many of a corp-held district's locations are the corp's own kind of place.
# The rest is the bar everyone drinks in, whoever owns the block.
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
}

LOCATION_PREFIXES = [
    "Grayline", "Halcyon", "Pier 9", "Black Sun", "Kestrel", "Ninth Street",
    "Redline", "Verge", "Saint Lazarus", "Copperhead", "Mirage", "Low Tide",
    "Gantry", "Hollow Point", "Tin City", "Nightjar", "Sunken", "Vector",
    "Cinder", "Palisade", "Ashline", "Dead Man's",
]

Cell = tuple[int, int]


def _location_kinds(owner: str, rng: random.Random) -> list[LocationKind]:
    faction = FACTIONS_BY_ID.get(owner)
    if faction is None:
        # Neutral ground and the player's block carry no corp's stamp.
        return rng.sample(list(LocationKind), k=LOCATIONS_PER_TERRITORY)
    owned_kind = LOCATION_KIND_FOR_SPECIALTY[faction.specialty]
    filler = [LocationKind.SOCIAL] * (LOCATIONS_PER_TERRITORY - SPECIALTY_LOCATIONS)
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


def _player_start(region: list[Cell], rng: random.Random) -> Cell:
    """The region cell nearest the middle of the grid, so the player starts boxed in."""
    center = ((GRID_COLS - 1) / 2, (GRID_ROWS - 1) / 2)
    return min(region, key=lambda c: (abs(c[0] - center[0]) + abs(c[1] - center[1]), c))


def _grow_blocs(
    region: list[Cell],
    edges: set[frozenset[Cell]],
    player_cell: Cell,
    faction_ids: list[str],
    rng: random.Random,
) -> dict[Cell, str] | None:
    """Race one contiguous bloc per faction outward from random seeds.

    Returns None if a bloc gets boxed in before reaching its quota; the caller
    retries with fresh seeds.
    """
    graph: dict[Cell, set[Cell]] = {cell: set() for cell in region}
    for edge in edges:
        a, b = tuple(edge)
        graph[a].add(b)
        graph[b].add(a)

    available = [cell for cell in region if cell != player_cell]
    seeds = rng.sample(available, k=len(faction_ids))
    owners: dict[Cell, str] = {player_cell: "player"}
    blocs: dict[str, set[Cell]] = {}
    for faction_id, seed in zip(faction_ids, seeds):
        owners[seed] = faction_id
        blocs[faction_id] = {seed}

    for _ in range(TERRITORIES_PER_FACTION - 1):
        for faction_id in faction_ids:
            bloc = blocs[faction_id]
            frontier = sorted(
                {n for cell in bloc for n in graph[cell] if n not in owners}
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
        player_cell = _player_start(region, rng)
        owners = _grow_blocs(region, edges, player_cell, faction_ids, rng)
        if owners is not None:
            break
    else:
        raise RuntimeError("could not lay out contiguous faction blocs")

    values: dict[Cell, int] = {player_cell: PLAYER_START_VALUE}
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
        )

    return CorpMap(territories=territories, player_start_id=ids[player_cell])
