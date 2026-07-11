import random
from dataclasses import dataclass, field, replace

from shadowguy.factions import FACTIONS, FACTIONS_BY_ID, Faction

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


@dataclass
class Territory:
    id: str
    name: str
    x: int
    y: int
    owner: str = "neutral"
    value: int = 1
    connections: list[str] = field(default_factory=list)


@dataclass
class CorpMap:
    territories: dict[str, Territory]

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


def _label(territory: Territory, selected_id: str | None) -> str:
    marker = "*" if territory.id == selected_id else " "
    tag = _owner_tag(territory.owner)
    name = f"{territory.name} {tag}" if tag else territory.name
    return f"{marker}[{name}]"


CONNECTOR_WIDTH = 4


def render_ascii_map(corp_map: CorpMap, selected_id: str | None = None) -> str:
    territories = corp_map.territories
    by_pos = {(t.x, t.y): t for t in territories.values()}
    max_col = max(t.x for t in territories.values())
    max_row = max(t.y for t in territories.values())

    col_width = {}
    for col in range(max_col + 1):
        labels = [_label(t, selected_id) for (c, _), t in by_pos.items() if c == col]
        col_width[col] = (max(len(label) for label in labels) if labels else 0) + 1

    col_offset = {}
    offset = 0
    for col in range(max_col + 1):
        col_offset[col] = offset
        offset += col_width[col] + CONNECTOR_WIDTH
    total_width = offset - CONNECTOR_WIDTH

    lines: list[str] = []
    for row in range(max_row + 1):
        node_cells = []
        for col in range(max_col + 1):
            t = by_pos.get((col, row))
            label = _label(t, selected_id) if t else ""
            right = by_pos.get((col + 1, row))
            connector = "----" if t and right and right.id in t.connections else " " * CONNECTOR_WIDTH
            is_last_col = col == max_col
            node_cells.append(label.ljust(col_width[col]) + ("" if is_last_col else connector))
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

    return "\n".join(lines)


PLAYER_TERRITORY_ID = "city_center"

NIGHT_CITY_SHAPE = [
    Territory(id="watson", name="Watson", x=1, y=0, value=3, connections=["city_center"]),
    Territory(id="pacifica", name="Pacifica", x=0, y=1, value=1, connections=["city_center", "westbrook"]),
    Territory(
        id="city_center", name="City Center", x=1, y=1, value=4,
        connections=["watson", "pacifica", "santo_domingo", "heywood"],
    ),
    Territory(id="santo_domingo", name="Santo Domingo", x=2, y=1, value=2, connections=["city_center", "badlands"]),
    Territory(id="westbrook", name="Westbrook", x=0, y=2, value=2, connections=["pacifica", "heywood"]),
    Territory(
        id="heywood", name="Heywood", x=1, y=2, value=2,
        connections=["city_center", "westbrook", "badlands"],
    ),
    Territory(id="badlands", name="Badlands", x=2, y=2, value=1, connections=["santo_domingo", "heywood"]),
]

# Groups of territories contested as a unit. On world generation, each cluster
# is handed to one randomly selected faction; leftover clusters (more clusters
# than available factions) stay neutral.
CORP_CLUSTERS = [
    ["watson"],
    ["pacifica", "westbrook"],
    ["santo_domingo", "badlands"],
]


def generate_corp_map(factions: list[Faction], rng: random.Random) -> CorpMap:
    territories = {t.id: replace(t) for t in NIGHT_CITY_SHAPE}
    territories[PLAYER_TERRITORY_ID].owner = "player"

    clusters = rng.sample(CORP_CLUSTERS, k=len(CORP_CLUSTERS))
    chosen_factions = rng.sample(factions, k=min(len(factions), len(clusters)))
    for faction, cluster in zip(chosen_factions, clusters):
        for territory_id in cluster:
            territories[territory_id].owner = faction.id

    return CorpMap(territories=territories)
