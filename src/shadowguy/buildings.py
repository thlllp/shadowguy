"""What the place a Burglary breaks into actually looks like: rooms, levels, links.

A burglary building is **job-scoped and invisible**. It is generated with the job
(jobs.generate_job), lives inside that job's Scene (scene.BurglaryStage), and goes away
when the job is finished or expires -- it is never a corpmap.Location, so a target adds
nothing to the map the player walks around and no UI noise unless they hold the job.
That is why BuildingKind lives here rather than beside corpmap.LocationKind: that enum
answers "what does this place sell" for a permanent fixture of the world, this one
answers "what is this building shaped like" for something temporary.

Three ideas, in the order they nest: a **Room** is a rectangle with a RoomKind, a
**Level** is one Grid plus the rooms carved into it, and a **Building** is levels plus
**links** between them. Each documents itself below; the one thing worth saying here is
that a Building is deliberately a *graph*, not a stack -- an office tower is a line of
links and a compound is several ground-level grids side by side with a couple below, and
neither needs a special case.

Leaf above grid.py: imports Grid/Tile and the pathing helper to carve and verify, and
is imported in turn by tactical.py (which walks a Building), scene.py (BurglaryStage
holds one) and jobs.py. It must never import scene -- same rule, same reason, as
tactical.py and combat.py.
"""

import random
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum

from shadowguy.grid import Coord, Grid, Tile, path_between


class BuildingKind(StrEnum):
    """What sort of building a burglary breaks into -- the tag a BurglaryStage carries
    and the thing generate_building reads its whole shape off (BUILDING_PROFILES).

    Adding a kind is adding a profile row and a level program, not a branch in the
    generator."""

    RESIDENTIAL = "residential"
    OFFICE = "office"
    COMPOUND = "compound"


class RoomKind(StrEnum):
    """What a room is for. Drives placement (see each profile's `objective_rooms` /
    `guard_rooms`) and the name the walk shows, so a runner always knows whether they're
    standing in a kitchen or somebody's bedroom."""

    HALL = "hall"  # circulation: entry hall, upstairs landing, corridor
    LIVING = "living"
    DINING = "dining"
    KITCHEN = "kitchen"
    BEDROOM = "bedroom"
    BATHROOM = "bathroom"
    BASEMENT = "basement"
    OFFICE = "office"
    CONFERENCE = "conference"
    RECEPTION = "reception"
    BREAK_ROOM = "break_room"
    STORAGE = "storage"
    SERVER_ROOM = "server_room"


ROOM_LABELS = {
    RoomKind.HALL: "Hall",
    RoomKind.LIVING: "Living room",
    RoomKind.DINING: "Dining room",
    RoomKind.KITCHEN: "Kitchen",
    RoomKind.BEDROOM: "Bedroom",
    RoomKind.BATHROOM: "Bathroom",
    RoomKind.BASEMENT: "Basement",
    RoomKind.OFFICE: "Office",
    RoomKind.CONFERENCE: "Conference room",
    RoomKind.RECEPTION: "Reception",
    RoomKind.BREAK_ROOM: "Break room",
    RoomKind.STORAGE: "Storage",
    RoomKind.SERVER_ROOM: "Server room",
}
if set(ROOM_LABELS) != set(RoomKind):
    raise ValueError("every RoomKind needs a ROOM_LABELS entry")


@dataclass(frozen=True)
class Room:
    """One rectangle of floor, in grid coords. `x`/`y` are its top-left *interior* cell:
    the wall ring around a room belongs to the level, not to either room it separates."""

    kind: RoomKind
    x: int
    y: int
    width: int
    height: int

    @property
    def center(self) -> Coord:
        return (self.x + self.width // 2, self.y + self.height // 2)

    @property
    def cells(self) -> list[Coord]:
        return [(x, y) for y in range(self.y, self.y + self.height) for x in range(self.x, self.x + self.width)]

    def label(self) -> str:
        return ROOM_LABELS[self.kind]


@dataclass
class Level:
    """One floor: a Grid, and the rooms carved into it. `name` is what a screen shows
    ("Ground floor"), and is the level's identity as far as the player is concerned.
    `doors` is every cell cut into a wall between two rooms (see _cut_door) -- generation's
    own record of where they ended up, since a Tile can't tell a doorway from open floor."""

    name: str
    grid: Grid
    rooms: tuple[Room, ...]
    doors: frozenset[Coord]

    def room_at(self, coord: Coord) -> Room | None:
        """Which room a cell is inside, if any -- None in a doorway or a wall."""
        x, y = coord
        return next(
            (r for r in self.rooms if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height),
            None,
        )


@dataclass(frozen=True)
class Link:
    """A way between two levels: a staircase, a lift, a door between two buildings of a
    compound. Undirected -- `a`/`b` are (level index, cell) on either side, and a walker
    standing on one cell can step to the other."""

    a: tuple[int, Coord]
    b: tuple[int, Coord]

    def other_side(self, level: int, coord: Coord) -> tuple[int, Coord] | None:
        """Where this link puts a walker standing at (level, coord), or None if they
        aren't standing on it."""
        if (level, coord) == self.a:
            return self.b
        if (level, coord) == self.b:
            return self.a
        return None


@dataclass(frozen=True)
class Lock:
    """A locked door: blocks its cell until picked (see tactical.attempt_lock). `skill` is
    hack for an electronic lock, infiltration for a mechanical one; `difficulty` is
    checks.resolve_check's own scale, same as every other difficulty in the game."""

    skill: str
    difficulty: int


@dataclass
class Building:
    """A generated burglary target: its levels, the links between them, where each
    entrance lands, where the objective is, who's watching, and what's locked.

    `entrance_spawns`, `objective`, `guards` and `cameras` are all (level index, cell) --
    a building is a graph of levels, so a bare coord would be ambiguous the moment there's
    more than one floor. `locks` is keyed the same way: every door still locked, cleared
    from the dict for good once picked (see tactical.attempt_lock)."""

    kind: BuildingKind
    levels: tuple[Level, ...]
    links: tuple[Link, ...]
    entrance_spawns: list[tuple[int, Coord]]  # one per requested entrance, input order
    objective: tuple[int, Coord]
    guards: tuple[tuple[int, Coord], ...]
    cameras: tuple[tuple[int, Coord], ...]
    locks: dict[tuple[int, Coord], Lock]

    def level(self, index: int) -> Level:
        return self.levels[index]

    def links_at(self, level: int, coord: Coord) -> tuple[int, Coord] | None:
        """Where stepping onto this cell takes the walker, if it's a link at all."""
        for link in self.links:
            side = link.other_side(level, coord)
            if side is not None:
                return side
        return None


@dataclass(frozen=True)
class LevelProgram:
    """What one level of a building is made of: its name and the rooms it must contain,
    as a list of RoomKinds (repeats allowed -- three BEDROOMs means three of them). The
    room *count* is what drives the partition, so this is also the level's granularity."""

    name: str
    rooms: tuple[RoomKind, ...]


@dataclass(frozen=True)
class BuildingProfile:
    """How a BuildingKind generates: footprint per level, how many people are watching,
    how cluttered it is, and the room program for each of its levels.

    Width/height are capped by what a screen can show at 80x24
    (tactical.TAC_MAP_WIDTH/TAC_MAP_HEIGHT are that ceiling).

    `objective_rooms` is where the thing worth stealing plausibly is for this sort of
    building, `guard_rooms`/`camera_rooms` where somebody (or something) plausibly watches.
    All three are preferences, not requirements -- a generated level that happens to have
    none of them falls back to any room that isn't already spoken for, so a new RoomKind
    (or a profile naming a room its program never builds) can never make generation fail.

    `locked_door_chance` is rolled independently per interior door (Level.doors) -- not a
    room preference, since a lock sits on the doorway itself, not in either room it joins."""

    width: int
    height: int
    cover_density: float
    guards: int
    objective_rooms: tuple[RoomKind, ...]
    guard_rooms: tuple[RoomKind, ...]
    cameras: int
    camera_rooms: tuple[RoomKind, ...]
    locked_door_chance: float


# Rooms need interior space plus the wall ring between them; below this a partition
# stops splitting, which is what caps how many rooms a given footprint can hold.
MIN_ROOM_SPAN = 3
# How many times a generated building is re-rolled before giving up. Same shape (and
# reason) as tactical._MAP_GEN_ATTEMPTS: hand back a playable building or raise, never
# an unplayable one.
_BUILD_ATTEMPTS = 60

# A locked door's skill is a coin flip between the two ways a door gets secured: hack for
# an electronic lock, infiltration for a mechanical one. LOCK_DIFFICULTY is a flat
# first-slice number (checks.resolve_check's own 9-21 scale), not yet balance-simulated.
LOCK_SKILLS = ("hack", "infiltration")
LOCK_DIFFICULTY = 12

# A house: 2-4 bedrooms and 1-2 bathrooms upstairs, the living half downstairs, and a
# basement under both. First-slice numbers, not balance-simulated.
RESIDENTIAL_BEDROOMS = (2, 4)
RESIDENTIAL_BATHROOMS = (1, 2)

BUILDING_PROFILES: dict[BuildingKind, BuildingProfile] = {
    BuildingKind.RESIDENTIAL: BuildingProfile(
        width=26,
        height=10,
        cover_density=0.10,  # furniture: a home is the most cluttered thing to cross
        guards=1,
        objective_rooms=(RoomKind.BEDROOM, RoomKind.BASEMENT, RoomKind.LIVING),
        guard_rooms=(RoomKind.HALL, RoomKind.LIVING, RoomKind.KITCHEN),
        cameras=0,  # a home has no monitored camera system to run into
        camera_rooms=(),
        locked_door_chance=0.05,  # rare -- a locked closet or a strongbox room, not a norm
    ),
    BuildingKind.OFFICE: BuildingProfile(
        width=30,
        height=10,
        cover_density=0.06,  # desks and partitions, but an open floor is barer than a home
        guards=2,
        objective_rooms=(RoomKind.OFFICE, RoomKind.SERVER_ROOM, RoomKind.CONFERENCE, RoomKind.STORAGE),
        # OFFICE is here as well as in objective_rooms (the way LIVING is for a house):
        # two guards and only reception/halls/a maybe-break-room to seat them sends ~9%
        # of rolls through the fallback, and a fallback that fires posts somebody in a
        # bathroom. The open floor is where a guard actually stands anyway.
        guard_rooms=(RoomKind.HALL, RoomKind.RECEPTION, RoomKind.BREAK_ROOM, RoomKind.OFFICE),
        # A camera system covers the whole building, not just where a guard is posted --
        # every room OFFICE's own program ever builds, so cameras=2 seats without a
        # fallback (a narrower list, mirroring guard_rooms, put ~8% of them in whatever
        # room was free; see the guard_rooms comment above for the same failure mode).
        cameras=2,
        camera_rooms=(
            RoomKind.HALL, RoomKind.RECEPTION, RoomKind.OFFICE, RoomKind.BATHROOM,
            RoomKind.CONFERENCE, RoomKind.BREAK_ROOM, RoomKind.SERVER_ROOM, RoomKind.STORAGE,
        ),
        locked_door_chance=0.15,  # records, server closets -- the doors worth locking
    ),
    BuildingKind.COMPOUND: BuildingProfile(
        width=30,
        height=10,
        cover_density=0.09,  # a wealthy house, furnished like RESIDENTIAL but with room to spare
        guards=3,  # a bigger private stake than an OFFICE floor, watched more closely
        objective_rooms=(RoomKind.BEDROOM, RoomKind.OFFICE, RoomKind.BASEMENT),
        guard_rooms=(RoomKind.HALL, RoomKind.LIVING, RoomKind.DINING, RoomKind.KITCHEN, RoomKind.OFFICE),
        # A private system, lighter than a corp's -- the guards do most of the watching --
        # but widened past just (HALL, OFFICE) the same way OFFICE's camera_rooms is: with
        # guards/objective/entrances already spoken for, a single camera still missed
        # both of those rooms more often than not.
        cameras=1,
        camera_rooms=(RoomKind.HALL, RoomKind.OFFICE, RoomKind.LIVING, RoomKind.BEDROOM),
        locked_door_chance=0.2,  # an owner's own study or vault is the most locked-up place in the game
    ),
}
if set(BUILDING_PROFILES) != set(BuildingKind):
    raise ValueError("every BuildingKind needs a BUILDING_PROFILES row")


# Street level, and so where every entrance lands. Levels are built bottom-up and a kind
# is free to put storeys under it or none at all, so the *name* is the only thing that
# says which one you walk in on -- an index can't, since a house has a basement below it
# and an office starts at it. Every LEVEL_PROGRAMS builder must name exactly one level
# this; generate_building raises if one doesn't.
GROUND_FLOOR = "Ground floor"


def _residential_program(rng: random.Random) -> list[LevelProgram]:
    """A house's levels, bottom to top: basement, ground floor, upstairs. Sizes vary by
    how many bedrooms and bathrooms this particular house turned out to have -- the
    second bathroom goes downstairs when there is one, which is where a house actually
    puts it."""
    bedrooms = rng.randint(*RESIDENTIAL_BEDROOMS)
    bathrooms = rng.randint(*RESIDENTIAL_BATHROOMS)
    ground = [RoomKind.HALL, RoomKind.LIVING, RoomKind.DINING, RoomKind.KITCHEN]
    if bathrooms > 1:
        ground.append(RoomKind.BATHROOM)
    upstairs = [RoomKind.HALL, *(RoomKind.BEDROOM,) * bedrooms, RoomKind.BATHROOM]
    return [
        LevelProgram("Basement", (RoomKind.BASEMENT, RoomKind.BASEMENT)),
        LevelProgram(GROUND_FLOOR, tuple(ground)),
        LevelProgram("Upstairs", tuple(upstairs)),
    ]


def _office_program(rng: random.Random) -> list[LevelProgram]:
    """An office building: reception on the ground floor, offices and a few amenity rooms
    upstairs, 2-4 storeys. No basement -- the prize is on somebody's desk or in a server
    closet, not under the building."""
    floors = rng.randint(2, 4)
    ground = [RoomKind.RECEPTION, RoomKind.HALL, RoomKind.OFFICE, RoomKind.BATHROOM]
    program = [LevelProgram(GROUND_FLOOR, tuple(ground))]
    for i in range(1, floors):
        name = "Upper floor" if floors == 2 else f"Floor {i + 1}"
        upper = [RoomKind.HALL, RoomKind.BATHROOM]
        n_offices = rng.randint(2, 3)
        upper.extend([RoomKind.OFFICE] * n_offices)
        if rng.random() < 0.5:
            upper.append(RoomKind.CONFERENCE)
        if rng.random() < 0.4:
            upper.append(RoomKind.BREAK_ROOM)
        if rng.random() < 0.2:
            upper.append(RoomKind.SERVER_ROOM)
        if rng.random() < 0.2:
            upper.append(RoomKind.STORAGE)
        program.append(LevelProgram(name, tuple(upper)))
    return program


def _compound_program(rng: random.Random) -> list[LevelProgram]:
    """A compound: a house too big to be just RESIDENTIAL. A ground floor and an upper
    floor are both always present -- that's the two storeys it tops out at, never
    OFFICE's stack of up to four -- and a basement underneath is an independent coin
    flip on top of them."""
    has_basement = rng.random() < 0.5
    ground = [
        RoomKind.HALL, RoomKind.LIVING, RoomKind.DINING, RoomKind.KITCHEN,
        RoomKind.OFFICE, RoomKind.BATHROOM, RoomKind.BEDROOM,
    ]
    program = []
    if has_basement:
        program.append(LevelProgram("Basement", (RoomKind.BASEMENT, RoomKind.BASEMENT)))
    program.append(LevelProgram(GROUND_FLOOR, tuple(ground)))
    upper = [RoomKind.HALL, *(RoomKind.BEDROOM,) * rng.randint(2, 4), RoomKind.BATHROOM]
    program.append(LevelProgram("Upper floor", tuple(upper)))
    return program


LEVEL_PROGRAMS = {
    BuildingKind.RESIDENTIAL: _residential_program,
    BuildingKind.OFFICE: _office_program,
    BuildingKind.COMPOUND: _compound_program,
}
if set(LEVEL_PROGRAMS) != set(BuildingKind):
    raise ValueError("every BuildingKind needs a LEVEL_PROGRAMS builder")


# ---------------------------------------------------------------------------
# Generation. A level is partitioned into as many rectangles as its program asks for,
# each kind is assigned to the rectangle that best fits what that room *is*, and doors
# are cut around whichever room turns out to be circulation. The result is verified
# walkable end to end before it's handed back.
# ---------------------------------------------------------------------------


def _split_rects(rng: random.Random, rect: tuple[int, int, int, int], count: int) -> list[tuple[int, int, int, int]]:
    """Chop one rectangle into exactly `count` sub-rectangles, always splitting the
    largest one so rooms come out roughly comparable rather than one hall and a row of
    slivers. Each split loses a cell to the wall between the halves. Returns fewer than
    `count` only when nothing left is big enough to divide (MIN_ROOM_SPAN)."""
    rects = [rect]
    while len(rects) < count:
        rects.sort(key=lambda r: r[2] * r[3], reverse=True)
        x, y, w, h = rects[0]
        # Split the longer side, so rooms trend square instead of ever-thinner strips.
        horizontal = w >= h
        span = w if horizontal else h
        if span < MIN_ROOM_SPAN * 2 + 1:
            break  # the biggest room can't divide, so none of the smaller ones can
        cut = rng.randint(MIN_ROOM_SPAN, span - MIN_ROOM_SPAN - 1)
        rects.pop(0)
        if horizontal:
            rects += [(x, y, cut, h), (x + cut + 1, y, w - cut - 1, h)]
        else:
            rects += [(x, y, w, cut), (x, y + cut + 1, w, h - cut - 1)]
    return rects


def _connected(grid: Grid, a: Coord, b: Coord, blocked: frozenset[Coord] = frozenset()) -> bool:
    """Whether a walker can get from one cell to the other on this level. Wraps
    path_between's one sharp edge: A* hands back an empty path both for "unreachable"
    and for "you are already standing there", and a room is always connected to itself.
    `blocked` is extra impassable cells on top of the grid's own walls -- generate_building
    uses it to ask "is this still true with every locked door treated as a wall"."""
    return a == b or bool(path_between(grid, a, b, blocked=blocked))


def _adjacent(a: Room, b: Room) -> list[Coord]:
    """The wall cells between two rooms that could become a door: cells in the one-thick
    wall separating them, with floor on both sides. Empty when they don't share a wall."""
    doors = []
    if a.x + a.width + 1 == b.x or b.x + b.width + 1 == a.x:  # vertical wall between
        wall_x = a.x + a.width if a.x < b.x else b.x + b.width
        overlap = range(max(a.y, b.y), min(a.y + a.height, b.y + b.height))
        doors = [(wall_x, y) for y in overlap]
    elif a.y + a.height + 1 == b.y or b.y + b.height + 1 == a.y:  # horizontal wall
        wall_y = a.y + a.height if a.y < b.y else b.y + b.height
        overlap = range(max(a.x, b.x), min(a.x + a.width, b.x + b.width))
        doors = [(x, wall_y) for x in overlap]
    return doors


def _assign_kinds(rects: list[tuple[int, int, int, int]], program: LevelProgram) -> tuple[Room, ...]:
    """Give each rectangle one of the program's kinds, by what the kind *is*: the
    rectangle touching the most others becomes the HALL (circulation is the room
    everything else opens off), the biggest of what's left takes the biggest social room,
    and the smallest takes the bathroom. That ordering is the whole difference between a
    floorplan and a chopped rectangle.

    Deterministic given the partition -- the variety comes from _split_rects upstream."""
    plain = [Room(RoomKind.HALL, *rect) for rect in rects]  # kind is a placeholder here
    wanted = list(program.rooms)

    def degree(room: Room) -> int:
        return sum(1 for other in plain if other is not room and _adjacent(room, other))

    assigned: dict[int, RoomKind] = {}
    if RoomKind.HALL in wanted:
        hub = max(plain, key=lambda room: (degree(room), room.width * room.height))
        assigned[plain.index(hub)] = RoomKind.HALL
        wanted.remove(RoomKind.HALL)

    # Biggest kinds to biggest rooms, smallest to smallest: a bathroom is never the
    # largest room in a house, and a living room is rarely the smallest.
    order = {kind: i for i, kind in enumerate(
        (RoomKind.LIVING, RoomKind.RECEPTION, RoomKind.BASEMENT, RoomKind.DINING,
         RoomKind.CONFERENCE, RoomKind.KITCHEN, RoomKind.BREAK_ROOM,
         RoomKind.BEDROOM, RoomKind.OFFICE, RoomKind.HALL,
         RoomKind.STORAGE, RoomKind.SERVER_ROOM, RoomKind.BATHROOM)
    )}
    wanted.sort(key=lambda kind: order.get(kind, len(order)))
    free = sorted(
        (i for i in range(len(plain)) if i not in assigned),
        key=lambda i: plain[i].width * plain[i].height,
        reverse=True,
    )
    for index, kind in zip(free, wanted, strict=False):
        assigned[index] = kind
    # More rectangles than the program asked for can't happen (the partition is asked for
    # exactly len(program.rooms)); fewer can, and those kinds simply don't appear.
    return tuple(
        Room(assigned.get(i, RoomKind.HALL), room.x, room.y, room.width, room.height)
        for i, room in enumerate(plain)
    )


def _carve_level(rng: random.Random, profile: BuildingProfile, program: LevelProgram) -> Level | None:
    """One floor: partition, assign kinds, carve rooms, cut doors, then furnish it.
    Returns None if the partition couldn't produce the program's rooms or the finished
    floor isn't walkable end to end -- the caller re-rolls.

    Order matters: furniture goes in *before* the connectivity check, because a chair in
    the wrong cell can seal a room as effectively as a wall. Doorways and the cells
    either side of them are kept clear so a door is never furnished shut."""
    interior = (1, 1, profile.width - 2, profile.height - 2)
    rects = _split_rects(rng, interior, len(program.rooms))
    if len(rects) < len(program.rooms):
        return None

    tiles = [[Tile.WALL] * profile.width for _ in range(profile.height)]
    rooms = _assign_kinds(rects, program)
    for room in rooms:
        for x, y in room.cells:
            tiles[y][x] = Tile.FLOOR
    grid = Grid(width=profile.width, height=profile.height, tiles=tiles)

    # Doors: every room that shares a wall with circulation opens onto it, which is how
    # a house works. Anything still cut off (a back room reachable only through another
    # back room) gets a door to the first neighbour that is already reachable.
    halls = [room for room in rooms if room.kind is RoomKind.HALL] or [rooms[0]]
    doors: set[Coord] = set()
    for room in rooms:
        for hall in halls:
            if room is not hall:
                _cut_door(tiles, doors, room, hall)
    grid._invalidate()

    for room in rooms:
        if _connected(grid, halls[0].center, room.center):
            continue
        for other in rooms:
            if other is not room and _connected(grid, halls[0].center, other.center):
                if _cut_door(tiles, doors, room, other):
                    grid._invalidate()
                    break
        if not _connected(grid, halls[0].center, room.center):
            return None

    _scatter_furniture(rng, tiles, rooms, profile.cover_density, doors)
    grid._invalidate()
    if any(not _connected(grid, halls[0].center, room.center) for room in rooms):
        return None  # furnished itself shut; re-roll rather than hand back a sealed room
    return Level(name=program.name, grid=grid, rooms=rooms, doors=frozenset(doors))


def _cut_door(tiles: list[list[Tile]], doors: set[Coord], a: Room, b: Room) -> bool:
    """Open a doorway in the middle of the wall two rooms share. False when they don't
    share one. Records the cell so furnishing knows to leave it (and its approaches) be."""
    shared = _adjacent(a, b)
    if not shared:
        return False
    x, y = shared[len(shared) // 2]
    tiles[y][x] = Tile.FLOOR
    doors.add((x, y))
    return True


def _scatter_furniture(
    rng: random.Random,
    tiles: list[list[Tile]],
    rooms: tuple[Room, ...],
    density: float,
    doors: set[Coord],
) -> None:
    """Sprinkle low cover through the rooms -- furniture to duck behind. Never on a
    room's centre (the cell everything else in generation places *on*), and never in a
    doorway or the cells leading to one, so no room can be furnished shut."""
    blocked = {room.center for room in rooms} | doors
    blocked |= {(x + dx, y + dy) for x, y in doors for dx, dy in ((0, -1), (0, 1), (-1, 0), (1, 0))}
    for room in rooms:
        for cell in room.cells:
            if cell not in blocked and rng.random() < density:
                tiles[cell[1]][cell[0]] = Tile.LOW_COVER


def _stair_between(
    rng: random.Random, lower: tuple[int, Level], upper: tuple[int, Level], used: set[tuple[int, Coord]]
) -> Link | None:
    """A staircase joining two levels: in the hall on each, since that's where stairs
    are. Falls back to any room when a level has no hall (a basement of two storerooms).

    Takes a cell nothing else has claimed -- a middle floor carries two staircases (one
    down, one up) and they are not the same stairs. Room centres are left alone where
    possible, since that's what entrances, guards and the objective are placed on.
    Returns None when a level has no free cell to put stairs in; the caller re-rolls."""
    sides = []
    for index, level in (lower, upper):
        halls = [room for room in level.rooms if room.kind is RoomKind.HALL] or list(level.rooms)
        room = rng.choice(halls)
        free = [
            cell for cell in room.cells
            if (index, cell) not in used and level.grid.is_walkable(cell) and cell != room.center
        ]
        if not free:
            free = [cell for cell in room.cells if (index, cell) not in used and level.grid.is_walkable(cell)]
        if not free:
            return None
        cell = rng.choice(free)
        used.add((index, cell))
        sides.append((index, cell))
    return Link(a=sides[0], b=sides[1])


def _place(
    rng: random.Random,
    levels: tuple[Level, ...],
    preferred: tuple[RoomKind, ...],
    taken: set[tuple[int, Coord]],
) -> tuple[int, Coord] | None:
    """One (level, cell) in a room of a preferred kind, avoiding cells already spoken
    for. Falls back to any free room, so placement can't fail on a building whose rooms
    happen not to include any preferred kind."""
    for kinds in (preferred, tuple(RoomKind)):
        spots = [
            (i, room.center)
            for i, level in enumerate(levels)
            for room in level.rooms
            if room.kind in kinds and (i, room.center) not in taken
        ]
        if spots:
            return rng.choice(spots)
    return None


def generate_building(
    rng: random.Random,
    entrance_count: int,
    kind: BuildingKind = BuildingKind.RESIDENTIAL,
) -> Building:
    """A burglary target: every level of it, the stairs between them, `entrance_count`
    distinct ways in (one per scene.Entrance, in input order), the objective somewhere it
    plausibly lives, and the profile's guards watching rooms people actually stand in.

    Everything about the shape -- how many levels, what rooms are on each, the footprint,
    the clutter, the guard count -- comes from `kind`'s BUILDING_PROFILES row and
    LEVEL_PROGRAMS builder, so a new sort of building to break into is data rather than
    another branch here.

    Entrances land on the level the program named GROUND_FLOOR, wherever that falls in the
    stack -- you come in at street level and find your own way down or up. Re-rolls up to
    _BUILD_ATTEMPTS times and raises rather than hand back a building whose objective can't
    be reached."""
    profile = BUILDING_PROFILES[kind]
    for _ in range(_BUILD_ATTEMPTS):
        programs = LEVEL_PROGRAMS[kind](rng)
        carved = [_carve_level(rng, profile, program) for program in programs]
        if any(level is None for level in carved):
            continue
        levels = tuple(carved)

        # Levels are generated bottom-up, so a stair joins each to the one above it. That
        # line of links is the simplest shape of the graph; a compound's side-by-side
        # buildings are the same Links pointed sideways.
        taken: set[tuple[int, Coord]] = set()
        stairs = [
            _stair_between(rng, (i, levels[i]), (i + 1, levels[i + 1]), taken)
            for i in range(len(levels) - 1)
        ]
        if any(link is None for link in stairs):
            continue
        links = tuple(stairs)

        ground = next((i for i, level in enumerate(levels) if level.name == GROUND_FLOOR), None)
        if ground is None:
            raise ValueError(f"{kind}'s level program names no {GROUND_FLOOR!r} level")
        rooms = levels[ground].rooms
        if len(rooms) < entrance_count:
            continue
        entrance_spawns = [(ground, room.center) for room in rng.sample(list(rooms), entrance_count)]

        taken |= set(entrance_spawns)
        objective = _place(rng, levels, profile.objective_rooms, taken)
        if objective is None:
            continue
        taken.add(objective)
        guards = []
        for _guard in range(profile.guards):
            spot = _place(rng, levels, profile.guard_rooms, taken)
            if spot is None:
                break
            taken.add(spot)
            guards.append(spot)

        if not _reachable(levels, links, entrance_spawns, objective):
            continue

        cameras = []
        for _camera in range(profile.cameras):
            spot = _place(rng, levels, profile.camera_rooms, taken)
            if spot is None:
                break
            taken.add(spot)
            cameras.append(spot)

        locks: dict[tuple[int, Coord], Lock] = {
            (level_index, door): Lock(skill=rng.choice(LOCK_SKILLS), difficulty=LOCK_DIFFICULTY)
            for level_index, level in enumerate(levels)
            for door in level.doors
            if rng.random() < profile.locked_door_chance
        }
        locked_by_level = defaultdict(set)
        for level_index, coord in locks:
            locked_by_level[level_index].add(coord)
        blocked = {level_index: frozenset(coords) for level_index, coords in locked_by_level.items()}
        if not _reachable(levels, links, entrance_spawns, objective, blocked=blocked):
            # A run of bad rolls locked every way to the score -- hand back a building
            # with no locked doors at all rather than one nothing can finish.
            locks = {}

        return Building(
            kind=kind,
            levels=levels,
            links=links,
            entrance_spawns=entrance_spawns,
            objective=objective,
            guards=tuple(guards),
            cameras=tuple(cameras),
            locks=locks,
        )
    raise RuntimeError(f"could not generate a playable {kind} building")


def _reachable(
    levels: tuple[Level, ...],
    links: tuple[Link, ...],
    entrance_spawns: list[tuple[int, Coord]],
    objective: tuple[int, Coord],
    blocked: dict[int, frozenset[Coord]] | None = None,
) -> bool:
    """Whether every entrance can actually get to the objective, walking floors and
    taking stairs.

    A flood over the level graph. The nodes are the cells that matter -- room centres,
    both ends of every link, the objective and the spawns -- because within a level
    "can I get there" is already answered by A*, and between levels it's answered by
    standing on a link. Anything else on the floor is scenery.

    `blocked` is extra impassable cells per level, on top of each grid's own walls --
    generate_building passes every locked door here to check the objective is still
    reachable with none of them ever pickable, so a run of bad rolls can never seal it
    off for good."""
    blocked = blocked or {}
    nodes: dict[int, set[Coord]] = {i: set() for i in range(len(levels))}
    for index, level in enumerate(levels):
        nodes[index].update(room.center for room in level.rooms)
    for link in links:
        nodes[link.a[0]].add(link.a[1])
        nodes[link.b[0]].add(link.b[1])
    nodes[objective[0]].add(objective[1])
    for level_index, coord in entrance_spawns:
        nodes[level_index].add(coord)

    for spawn in entrance_spawns:
        seen: set[tuple[int, Coord]] = set()
        frontier = [spawn]
        while frontier:
            current = frontier.pop()
            if current in seen:
                continue
            seen.add(current)
            level_index, coord = current
            grid = levels[level_index].grid
            level_blocked = blocked.get(level_index, frozenset())
            for link in links:
                side = link.other_side(level_index, coord)
                if side is not None and side not in seen:
                    frontier.append(side)
            frontier += [
                (level_index, cell)
                for cell in nodes[level_index]
                if (level_index, cell) not in seen and _connected(grid, coord, cell, blocked=level_blocked)
            ]
        if objective not in seen:
            return False
    return True
