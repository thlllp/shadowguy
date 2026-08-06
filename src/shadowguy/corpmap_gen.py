"""Laying out one new territory map: `generate_corp_map`, run once per run.

Everything here is generation-time only — the grid the city is a blob on, growing
that blob (`_grow_region`), wiring it up (`_connect`), racing one contiguous bloc
per faction across it (`_grow_blocs`), scattering gang turf, planting the out-of-band
places every map needs exactly so many of (`_plan_injections`: hospitals, HQs,
research facilities, academies, gang dens, junkyards), and naming every district,
storefront and person in one.

The arrow points one way: this imports `corpmap` for the model it fills in, and
`corpmap` never imports back. Only `app.py` (and the test fixtures) need this module
at all, which is why it isn't in `corpmap.py` — a Territory is read everywhere, and
laying one out is read almost nowhere.

The import-time guards below are the load-bearing part to respect when tuning: every
table here is sampled without replacement, so a pool that runs short raises (or, for
`_unique_location_name`, *hangs*) mid-generation. The guards turn that into an import
failure instead. Only the faction count depends on the caller, and that one guard
lives in `generate_corp_map`.
"""

import random
from collections import Counter
from dataclasses import dataclass

from shadowguy.corpmap import (
    GENERATED_KINDS,
    ROLLED_KINDS,
    SHOP_KINDS,
    CorpMap,
    LocalCharacter,
    Location,
    LocationKind,
    Territory,
    add_academy,
    add_research_facility,
    location_stat,
    make_modifiers,
)
from shadowguy.factions import (
    CORP_OFFICER_TIERS,
    FACTIONS_BY_ID,
    Faction,
    FactionSpecialty,
)
from shadowguy.gangs import GANG_RANKS, GANGS, GANGS_BY_ID, Gang
from shadowguy.relations import generate_relations

# The grid is deliberately roomier than TERRITORY_COUNT: the leftover cells are
# the holes that keep _grow_region's blob from degenerating into a full rectangle.
GRID_COLS = 7
GRID_ROWS = 12
TERRITORY_COUNT = 65
TERRITORIES_PER_FACTION = 6

# Every faction is handed exactly this multiset of values, so equal territory
# count and equal total value are guaranteed by construction rather than found
# by searching for a fair partition. Must stay TERRITORIES_PER_FACTION long —
# that one-to-one is what makes the guarantee free.
FACTION_VALUE_SPREAD = (3, 3, 2, 2, 1, 1)

NEUTRAL_VALUES = (1, 2, 3)

# A gang doesn't grow a contiguous bloc like a corp faction (_grow_blocs) — it's a
# scattered presence, GANG_TURF_MIN..MAX unclaimed territories drawn at random, never
# the corp blocs and never the player's own start (see _place_gangs).
GANG_TURF_MIN = 2
GANG_TURF_MAX = 3

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

# Junkyards are rarer, and neutral-only (see _plan_injections): roughly one in this
# many *unclaimed* districts, not one in TERRITORY_COUNT overall — a corp bloc never
# rolls one no matter how this ratio is tuned.
TILES_PER_JUNKYARD = 10

# Chance that a grid-adjacent pair not already joined by the spanning tree gets
# an edge anyway. Higher = loopier map with more flanking routes.
EXTRA_EDGE_CHANCE = 0.35

# Must comfortably exceed TERRITORY_COUNT: names are sampled without replacement,
# and the surplus is what keeps two runs from drawing the same district list.
# Single words only — a territory's id is its lowercased name, and that id ends up
# inside Textual widget ids (see CorpMapScreen's "map_local_" rows), which cannot hold spaces.
DISTRICT_NAMES = [
    "Kabuki", "Northside", "Watson", "Pacifica", "Heywood", "Westbrook",
    "Rancho", "Arroyo", "Coastview", "Glen", "Vista", "Charter",
    "Downtown", "Japantown", "Badlands", "Autopia", "Dogtown", "Longshore",
    "Sunset", "Harbor", "Foundry", "Terminal", "Spire", "Ashgrove",
    "Riverside", "Steelyard", "Saltflats", "Greywater", "Lowline", "Highgate",
    "Ember", "Solace", "Quarry", "Blackstack", "Neon", "Drydock",
    "Junction", "Marrow", "Halberd", "Verdant", "Slagworks", "Prospect",
    "Kingsway", "Ravine", "Tannery", "Cathode", "Bracken", "Silo",
    "Underpass", "Wharf", "Millrace", "Chasm", "Vault", "Circuit",
    "Static", "Undercroft", "Bastion", "Redwire", "Cinderfield", "Lockstep",
    "Freeport", "Ashfall", "Crosscut", "Wellspring", "Backwater", "Fringeline",
    "Outpost", "Nightmarket", "Rustbelt", "Corridor", "Threshold", "Deadline",
    "Signal", "Causeway", "Greyline", "Stackyard", "Lowtide", "Highwater",
    "Farrow", "Cordon", "Switchback", "Blacktide", "Coldwater", "Overpass",
    "Trench",
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
    FactionSpecialty.CYBERNETICS: LocationKind.CYBER_CLINIC,
}

LOCATION_SUFFIXES = {
    LocationKind.DATA: ["Data Vault", "Server Stack", "Relay Hub", "Net Exchange"],
    LocationKind.LAB: ["Clinic", "Biolab", "Dispensary", "Trauma Ward"],
    LocationKind.DEPOT: ["Depot", "Armory", "Freight Yard", "Loading Dock"],
    LocationKind.BAR: ["Bar", "Noodle House", "Club", "Pachinko Parlor"],
    LocationKind.PAWN: ["Pawn Shop", "Loan & Trade", "Cash 4 Chrome", "Buy-Sell-Trade"],
    LocationKind.WEAPON_SHOP: ["Gun Shop", "Arms Dealer", "Ironmonger", "Ballistics Outlet"],
    LocationKind.AUTO_DEALER: ["Auto Dealer", "Motorpool", "Garage", "Chop Shop"],
    LocationKind.PHARMACY: ["Pharmacy", "Chemist", "Drug Store", "Apothecary"],
    LocationKind.COMPUTER_STORE: ["Computer Store", "Chip Shop", "Hardware Outlet", "Rig Emporium"],
    LocationKind.HOSPITAL: ["Hospital", "Trauma Center", "Emergency Room", "Med Center"],
    LocationKind.REAL_ESTATE: ["Realty", "Properties", "Holdings", "Estate Agency"],
    LocationKind.CYBER_CLINIC: ["Augment Clinic", "Chrome Den", "Grafting Parlor", "Wetware Bazaar"],
    LocationKind.JUNKYARD: ["Junkyard", "Scrapyard", "Wrecking Yard", "Salvage Yard"],
}

LOCATION_PREFIXES = [
    "Grayline", "Halcyon", "Pier 9", "Black Sun", "Kestrel", "Ninth Street",
    "Redline", "Verge", "Saint Lazarus", "Copperhead", "Mirage", "Low Tide",
    "Gantry", "Hollow Point", "Tin City", "Nightjar", "Sunken", "Vector",
    "Cinder", "Palisade", "Ashline", "Dead Man's",
    "Greywire", "Split Lip", "Backdraft", "Rustline", "Cold Front", "Deadbolt",
    "Riptide", "Foxfire", "Ninth Circuit", "Salt Line", "Chrome Row", "Widow's Walk",
    "Last Call", "Old Pier",
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
    LocationKind.BAR: ("bartender", "regular", "bouncer", "hustler"),
    LocationKind.PAWN: ("pawnbroker",),
    LocationKind.WEAPON_SHOP: ("gunsmith",),
    LocationKind.AUTO_DEALER: ("dealer",),
    LocationKind.PHARMACY: ("pharmacist",),
    LocationKind.COMPUTER_STORE: ("techie",),
    LocationKind.HOSPITAL: ("trauma surgeon", "triage nurse", "orderly"),
    LocationKind.REAL_ESTATE: ("realtor", "property broker", "landlord"),
    LocationKind.CYBER_CLINIC: ("augmetics doc", "chrome dealer", "grafter"),
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
FILLER_KINDS = (LocationKind.BAR, *SHOP_KINDS)


def _filler_pool(owned_kind: LocationKind) -> list[LocationKind]:
    """Filler kinds that don't repeat the specialty's own stat.

    A district is SPECIALTY_LOCATIONS of one kind plus filler, so a filler that
    shared the specialty's stat (e.g. COMPUTER_STORE, also logic, next to
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


def _characters_for_roles(location_id: str, roles: list[str], rng: random.Random) -> list[LocalCharacter]:
    """One LocalCharacter per role, in that order; ids (the standing key) follow the
    standard location-scoped scheme and are unique by construction. Shared by
    _make_characters (a random role subset) and _make_officers (CORP_OFFICER_TIERS'
    fixed order)."""
    names = rng.sample(CHARACTER_NAMES, len(roles))
    return [
        LocalCharacter(id=f"{location_id}_p{i}", name=names[i], role=role)
        for i, role in enumerate(roles)
    ]


def _make_characters(location_id: str, kind: LocationKind, rng: random.Random) -> list[LocalCharacter]:
    """One character for a shop (its owner), 1–2 for anywhere else. Names and roles are
    distinct within the location; ids (the standing key) are unique by construction."""
    count = 1 if kind in SHOP_KINDS else rng.randint(1, MAX_CHARACTERS_PER_LOCATION)
    roles = rng.sample(LOCATION_ROLES[kind], count)
    return _characters_for_roles(location_id, roles, rng)


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
    """The corporate officers manning an HQ: one per CORP_OFFICER_TIERS rank. app.CorpHQScreen
    gates each by its own role (factions.officer_unlocked), not by list position, so ids
    follow the standard location-scoped scheme though HQ standing isn't moved yet."""
    roles = [role for role, _min_rep, _min_standing in CORP_OFFICER_TIERS]
    return _characters_for_roles(location_id, roles, rng)


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




def _make_gang_members(location_id: str, rng: random.Random) -> list[LocalCharacter]:
    """The two ranks manning a gang's den: one per GANG_RANKS tier. app.py has no
    gate or screen for them yet (contrast _make_officers/CORP_OFFICER_TIERS) — they're
    just people you'll find there."""
    return _characters_for_roles(location_id, list(GANG_RANKS), rng)


def _make_gang_den(territory_id: str, gang: Gang, rng: random.Random) -> Location:
    """A gang's safehouse — one per gang, seated in one of its own turf districts (see
    generate_corp_map). Not a rolled kind: it's manned (GANG_RANKS) but has no screen of
    its own yet. A gang's den territory is unique to it (see _place_gangs), so the id
    can't collide.
    """
    location_id = f"{territory_id}_gang_den"
    return Location(
        id=location_id,
        name=f"{gang.name} Safehouse",
        kind=LocationKind.GANG_DEN,
        characters=_make_gang_members(location_id, rng),
    )


JUNKYARD_ROLE = "scrapper"


def _make_junkyard(territory_id: str, rng: random.Random, used_names: set[str]) -> Location:
    """A rare scavenging spot on unclaimed ground — one scrapper, no shop, no gig/job/
    legwork surface (see shops.scavenge for its one action). Placed out of band like the
    hospital (see TILES_PER_JUNKYARD / _plan_injections), never rolled as filler."""
    location_id = f"{territory_id}_junkyard"
    return Location(
        id=location_id,
        name=_unique_location_name(LocationKind.JUNKYARD, rng, used_names),
        kind=LocationKind.JUNKYARD,
        characters=_characters_for_roles(location_id, [JUNKYARD_ROLE], rng),
    )



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
    time budget that already has to cover gigs, jobs and legwork.
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
    for faction_id, seed in zip(faction_ids, seeds, strict=True):
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


def _place_gangs(neutral_ids: list[str], gangs: list[Gang], rng: random.Random) -> dict[str, str]:
    """Scatter each gang's presence across GANG_TURF_MIN..MAX unclaimed territories.

    Drawn without replacement across every gang, so a territory hosts at most one
    gang's presence and gangs can't crowd each other out of the same blocks. Unlike
    _grow_blocs, there's no contiguity requirement — a gang isn't a bloc, just a
    handful of places it operates.
    """
    pool = list(neutral_ids)
    rng.shuffle(pool)
    gang_ids: dict[str, str] = {}
    for gang in gangs:
        size = rng.randint(GANG_TURF_MIN, GANG_TURF_MAX)
        turf, pool = pool[:size], pool[size:]
        for tid in turf:
            gang_ids[tid] = gang.id
    return gang_ids


MAX_GENERATION_ATTEMPTS = 100


def _assign_values(owners: dict[Cell, str], region: list[Cell], faction_ids: list[str], rng: random.Random) -> dict[Cell, int]:
    values: dict[Cell, int] = {}
    for faction_id in faction_ids:
        bloc = sorted(cell for cell, owner in owners.items() if owner == faction_id)
        spread = list(FACTION_VALUE_SPREAD)
        rng.shuffle(spread)
        values.update(zip(bloc, spread, strict=True))
    for cell in region:
        if cell not in owners:
            values[cell] = rng.choice(NEUTRAL_VALUES)
    return values


@dataclass
class _InjectionPlan:
    hospital_ids: set[str]
    hq_ids: dict[str, str]
    research_ids: dict[str, str]
    academy_ids: dict[str, str]
    den_ids: dict[str, str]
    gang_ids: dict[str, str]
    junkyard_ids: set[str]


def _plan_injections(region: list[Cell], owners: dict[Cell, str],
                     values: dict[Cell, int], ids: dict[Cell, str], start_id: str,
                     faction_ids: list[str], rng: random.Random) -> _InjectionPlan:
    elsewhere = [ids[cell] for cell in region if ids[cell] != start_id]
    hospital_ids = set(rng.sample(elsewhere, HOSPITAL_COUNT))

    neutral_ids = [ids[cell] for cell in region if cell not in owners and ids[cell] != start_id]
    gang_ids = _place_gangs(neutral_ids, GANGS, rng)

    gang_turf: dict[str, list[str]] = {}
    for tid, gang_id in gang_ids.items():
        gang_turf.setdefault(gang_id, []).append(tid)
    den_ids = {rng.choice(tids): gang_id for gang_id, tids in gang_turf.items()}

    # Junkyards draw from neutral ground only, and skip any tile already reserved for
    # a hospital or a gang den: those two already stack to the reserved-slot ceiling a
    # neutral tile can carry (MAX_LOCATIONS_PER_TERRITORY - MIN_LOCATIONS_PER_TERRITORY
    # == 2) — a third reservation on the same tile would make generate_corp_map's
    # `MAX_LOCATIONS_PER_TERRITORY - reserved` floor drop below MIN and raise.
    junkyard_candidates = [tid for tid in neutral_ids if tid not in hospital_ids and tid not in den_ids]
    junkyard_count = min(len(junkyard_candidates), max(1, round(len(neutral_ids) / TILES_PER_JUNKYARD)))
    junkyard_ids = set(rng.sample(junkyard_candidates, junkyard_count))

    top_value = max(FACTION_VALUE_SPREAD)
    hq_ids: dict[str, str] = {}
    research_ids: dict[str, str] = {}
    academy_ids: dict[str, str] = {}
    for faction_id in faction_ids:
        owned_cells = sorted(cell for cell, owner in owners.items() if owner == faction_id)
        top_cells = [cell for cell in owned_cells if values[cell] == top_value]
        hq_cell = rng.choice(top_cells)
        hq_ids[ids[hq_cell]] = faction_id
        # Never the HQ's own district, nor each other's — TERRITORIES_PER_FACTION
        # guarantees enough other owned cells remain, so neither pick can come up empty.
        research_cell = rng.choice([cell for cell in owned_cells if cell != hq_cell])
        research_ids[ids[research_cell]] = faction_id
        academy_cell = rng.choice([cell for cell in owned_cells if cell not in (hq_cell, research_cell)])
        academy_ids[ids[academy_cell]] = faction_id

    return _InjectionPlan(
        hospital_ids=hospital_ids,
        hq_ids=hq_ids,
        research_ids=research_ids,
        academy_ids=academy_ids,
        den_ids=den_ids,
        gang_ids=gang_ids,
        junkyard_ids=junkyard_ids,
    )


def generate_corp_map(factions: list[Faction], rng: random.Random) -> CorpMap:
    faction_ids = [f.id for f in factions]
    if len(faction_ids) * TERRITORIES_PER_FACTION + 1 > TERRITORY_COUNT:
        raise ValueError("not enough territories to give every faction a full bloc")
    neutral_count = TERRITORY_COUNT - len(faction_ids) * TERRITORIES_PER_FACTION - 1
    if len(GANGS) * GANG_TURF_MAX > neutral_count:
        raise ValueError("not enough unclaimed territory to give every gang turf")

    for _ in range(MAX_GENERATION_ATTEMPTS):
        region = _grow_region(rng)
        edges = _connect(region, rng)
        start_cell = _player_start(region, edges, rng)
        owners = _grow_blocs(region, edges, start_cell, faction_ids, rng)
        if owners is not None:
            break
    else:
        raise RuntimeError("could not lay out contiguous faction blocs")

    values = _assign_values(owners, region, faction_ids, rng)
    names = rng.sample(DISTRICT_NAMES, k=len(region))
    ids = {cell: name.lower() for cell, name in zip(region, names, strict=True)}
    start_id = ids[start_cell]
    plan = _plan_injections(region, owners, values, ids, start_id, faction_ids, rng)

    territories = {}
    used_names: set[str] = set()
    for cell, name in zip(region, names, strict=True):
        x, y = cell
        tid = ids[cell]
        owner = owners.get(cell, "neutral")
        reserved = (
            (tid == start_id)
            + (tid in plan.hospital_ids)
            + (tid in plan.hq_ids)
            + (tid in plan.den_ids)
            + (tid in plan.research_ids)
            + (tid in plan.academy_ids)
            + (tid in plan.junkyard_ids)
        )
        count = rng.randint(MIN_LOCATIONS_PER_TERRITORY, MAX_LOCATIONS_PER_TERRITORY - reserved)
        territories[tid] = Territory(
            id=tid, name=name, x=x, y=y, owner=owner, value=values[cell],
            connections=sorted(ids[other] for other in region if frozenset((cell, other)) in edges),
            locations=_make_locations(tid, owner, rng, used_names, count),
            modifiers=make_modifiers(owner, values[cell], rng),
            gang_id=plan.gang_ids.get(tid),
        )

    start = territories[start_id]
    start.locations.insert(
        0,
        Location(
            id=f"{start.id}_apartment",
            name="Your Apartment",
            kind=LocationKind.APARTMENT,
            workshop_built=True,
        ),
    )

    for tid in plan.hospital_ids:
        territories[tid].locations.append(_make_hospital(tid, rng, used_names))
    for tid in plan.junkyard_ids:
        territories[tid].locations.append(_make_junkyard(tid, rng, used_names))
    for tid, faction_id in plan.hq_ids.items():
        territories[tid].locations.append(_make_hq(tid, FACTIONS_BY_ID[faction_id], rng))
    for tid, faction_id in plan.research_ids.items():
        # Named off the district's owner, which _assign_owners has already set.
        add_research_facility(territories[tid])
    for tid, faction_id in plan.academy_ids.items():
        add_academy(territories[tid])
    for tid, gang_id in plan.den_ids.items():
        territories[tid].locations.append(_make_gang_den(tid, GANGS_BY_ID[gang_id], rng))

    for_sale = [tid for tid in territories if tid != start.id]
    for territory in territories.values():
        for location in territory.locations:
            if location.kind is LocationKind.REAL_ESTATE:
                location.listings = rng.sample(for_sale, k=min(REAL_ESTATE_LISTING_COUNT, len(for_sale)))

    return CorpMap(
        territories=territories,
        player_start_id=ids[start_cell],
        relations=generate_relations(rng),
    )
