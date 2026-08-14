"""Tests for corpmap.py's model — the runtime mutators over a Territory.

Laying a map out is corpmap_gen.py, and its (much larger) invariant sweep lives in
test_corpmap_gen.py.
"""

import random

import pytest

from shadowguy.corpmap import (
    BREAK_MARKER,
    LODGING_COST_PER_DEVELOPMENT,
    MODIFIER_MAX,
    CorpMap,
    Location,
    LocationKind,
    Territory,
    TerritoryModifier,
    add_safehouse,
    attack_candidates,
    build_workshop,
    capture_territory,
    claim_territory,
    lodging_cost,
    render_ascii_map,
    territory_distance,
    travel_path,
)
from shadowguy.corpmap_gen import generate_corp_map
from shadowguy.factions import FACTIONS

# The project's convention for anything asserting on a generated board: a wide seed
# sweep over invariants rather than one seed's exact layout. Render is ~1.2ms, so a
# full sweep costs less than a second.
SEEDS = range(150)


def test_add_safehouse_starts_without_a_workshop():
    territory = Territory(id="t1", name="Testville", x=0, y=0)
    add_safehouse(territory)
    safehouse = next(loc for loc in territory.locations if loc.kind == LocationKind.SAFEHOUSE)
    assert safehouse.workshop_built is False


def test_build_workshop_sets_the_flag():
    territory = Territory(id="t1", name="Testville", x=0, y=0)
    add_safehouse(territory)
    safehouse = next(loc for loc in territory.locations if loc.kind == LocationKind.SAFEHOUSE)
    build_workshop(safehouse)
    assert safehouse.workshop_built is True


def test_claim_territory_flips_owner_and_reseeds_modifiers():
    """claim_territory (rivals.py's expansion mutator) must overwrite the neutral
    modifier profile with a corp-shaped one, not just flip owner."""
    territory = Territory(
        id="t1",
        name="Testville",
        x=0,
        y=0,
        owner="neutral",
        value=2,
        modifiers={
            TerritoryModifier.SECURITY: 1,
            TerritoryModifier.SURVEILLANCE: 0,
            TerritoryModifier.UNREST: MODIFIER_MAX,
            TerritoryModifier.DEVELOPMENT: 1,
            TerritoryModifier.RESTRICTED: 0,
        },
        gang_id="gang_test",
    )
    claim_territory(territory, "faction_ironclad", random.Random(0))
    assert territory.owner == "faction_ironclad"
    assert territory.gang_id is None
    assert territory.value == 2  # left as-is
    # Corp-shaped modifiers: Restricted is squeezed (2..MODIFIER_MAX), unlike
    # neutral ground's flat 0 — the clearest tell the profile actually changed.
    assert territory.modifiers[TerritoryModifier.RESTRICTED] >= 2


def test_claim_territory_clears_any_garrison():
    """Neutral ground carries no garrison, so a claim has to zero whatever was
    stranded there — otherwise a district recaptured back to neutral would hand its
    next owner a free standing force."""
    territory = Territory(id="t1", name="Testville", x=0, y=0, garrison=4)
    claim_territory(territory, "faction_ironclad", random.Random(0))
    assert territory.garrison == 0


def test_claim_territory_clears_the_slum_flag():
    """is_slum describes *neutral* ground. A corp moving in reseeds the modifiers to
    corp values, so leaving the flag set would keep a garrisoned district labelled a
    slum on the map and resting there free forever."""
    territory = Territory(id="t1", name="Testville", x=0, y=0, is_slum=True)
    claim_territory(territory, "faction_ironclad", random.Random(0))
    assert territory.is_slum is False
    # The slum short-circuit in lodging_cost no longer fires: it charges by Development
    # like any other district now.
    assert lodging_cost(territory) == (
        LODGING_COST_PER_DEVELOPMENT * territory.modifiers[TerritoryModifier.DEVELOPMENT]
    )


def test_capture_territory_keeps_modifiers_and_locations():
    """The opposite of claim_territory: taking a district off a rival inherits what
    they built there. Only ownership and the beaten garrison change."""
    territory = Territory(
        id="t1",
        name="Testville",
        x=0,
        y=0,
        owner="faction_ghostwire",
        value=3,
        garrison=2,
        modifiers={TerritoryModifier.SECURITY: 4, TerritoryModifier.DEVELOPMENT: 3},
    )
    territory.locations.append(
        Location(id="lab", name="Lab", kind=LocationKind.RESEARCH_FACILITY, research_tier=1)
    )
    capture_territory(territory, "faction_ironclad")
    assert territory.owner == "faction_ironclad"
    assert territory.garrison == 0
    assert territory.value == 3
    assert territory.modifiers[TerritoryModifier.SECURITY] == 4
    assert [loc.kind for loc in territory.locations] == [LocationKind.RESEARCH_FACILITY]


def _linked(a, b):
    """Two territories wired to each other, so CorpMap's symmetry check passes."""
    a.connections.append(b.id)
    b.connections.append(a.id)


def test_attack_candidates_finds_only_bordering_rival_ground():
    mine = Territory(id="mine", name="Mine", x=0, y=0, owner="faction_ironclad")
    theirs = Territory(id="theirs", name="Theirs", x=1, y=0, owner="faction_ghostwire")
    neutral = Territory(id="neutral", name="Neutral", x=2, y=0)
    far = Territory(id="far", name="Far", x=3, y=0, owner="faction_meridian")
    _linked(mine, theirs)
    _linked(mine, neutral)
    _linked(neutral, far)
    corp_map = CorpMap(
        territories={t.id: t for t in (mine, theirs, neutral, far)}, player_start_id="neutral"
    )
    # theirs borders us; neutral isn't corp-held; far is corp-held but two hops out.
    assert attack_candidates(corp_map, "faction_ironclad") == ["theirs"]


def test_attack_candidates_excludes_your_own_ground():
    a = Territory(id="a", name="A", x=0, y=0, owner="faction_ironclad")
    b = Territory(id="b", name="B", x=1, y=0, owner="faction_ironclad")
    _linked(a, b)
    corp_map = CorpMap(territories={t.id: t for t in (a, b)}, player_start_id="a")
    assert attack_candidates(corp_map, "faction_ironclad") == []


def _chain(*ids: str) -> CorpMap:
    """A straight line a-b-c-d..., the shape CorpMapScreen's fast travel has to
    route across rather than just checking one bordering hop."""
    territories = [Territory(id=i, name=i, x=n, y=0) for n, i in enumerate(ids)]
    for left, right in zip(territories, territories[1:]):
        _linked(left, right)
    return CorpMap(territories={t.id: t for t in territories}, player_start_id=ids[0])


def test_travel_path_same_territory_is_empty():
    corp_map = _chain("a", "b", "c")
    assert travel_path(corp_map, "a", "a") == []


def test_travel_path_walks_the_shortest_route():
    corp_map = _chain("a", "b", "c", "d")
    assert travel_path(corp_map, "a", "d") == ["b", "c", "d"]


def test_travel_path_length_matches_territory_distance():
    corp_map = _chain("a", "b", "c", "d")
    assert len(travel_path(corp_map, "a", "d")) == territory_distance(corp_map, "a", "d")


def test_travel_path_hops_are_each_actually_connected():
    corp_map = _chain("a", "b", "c", "d")
    path = travel_path(corp_map, "a", "d")
    previous = "a"
    for step in path:
        assert step in corp_map.territories[previous].connections
        previous = step


# --- render_ascii_map: adjacency vs. connection --------------------------------
# A grid-adjacent pair with no connection used to render as blank space, which is
# exactly what a hole in the blob looks like — so the map couldn't distinguish
# "you can't get there from here" from "there's nothing there". BREAK_MARKER is
# the fix, and these pin both axes plus the spans the screen styles off.


def _grid(*rows: tuple[str, ...]) -> dict[str, Territory]:
    """Territories laid out on a grid from rows of ids, unconnected. `_linked` wires
    up whichever pairs a test wants joined."""
    return {
        id: Territory(id=id, name=id.upper(), x=x, y=y)
        for y, row in enumerate(rows)
        for x, id in enumerate(row)
    }


def test_render_marks_a_horizontal_gap_between_unconnected_neighbours():
    cells = _grid(("a", "b"))
    corp_map = CorpMap(territories=cells, player_start_id="a")
    rendered = render_ascii_map(corp_map)

    assert BREAK_MARKER * 2 in rendered.text
    assert "-" not in rendered.text
    assert [(b.territory_a, b.territory_b) for b in rendered.break_spans] == [("a", "b")]
    assert rendered.connector_spans == []
    # The span really covers the marker, which is what the screen dims.
    span = rendered.break_spans[0]
    assert rendered.text[span.offset : span.offset + 2] == BREAK_MARKER * 2


def test_render_marks_a_vertical_gap_between_unconnected_neighbours():
    cells = _grid(("a",), ("b",))
    corp_map = CorpMap(territories=cells, player_start_id="a")
    rendered = render_ascii_map(corp_map)

    assert BREAK_MARKER in rendered.text
    assert "|" not in rendered.text
    assert [(b.territory_a, b.territory_b) for b in rendered.break_spans] == [("a", "b")]
    span = rendered.break_spans[0]
    assert rendered.text[span.offset] == BREAK_MARKER


def test_render_draws_a_link_rather_than_a_break_where_one_exists():
    cells = _grid(("a", "b"), ("c", "d"))
    _linked(cells["a"], cells["b"])
    _linked(cells["a"], cells["c"])
    corp_map = CorpMap(territories=cells, player_start_id="a")
    rendered = render_ascii_map(corp_map)

    linked = {(c.territory_a, c.territory_b) for c in rendered.connector_spans}
    broken = {(b.territory_a, b.territory_b) for b in rendered.break_spans}
    assert linked == {("a", "b"), ("a", "c")}
    # The other two adjacencies (b|d vertically, c-d horizontally) are breaks.
    assert broken == {("b", "d"), ("c", "d")}
    assert not linked & broken


def test_render_leaves_a_hole_in_the_grid_blank():
    """A missing district is still empty space — the marker means "adjacent but
    unreachable", not "nothing here"."""
    cells = _grid(("a",))
    cells["c"] = Territory(id="c", name="C", x=2, y=0)
    corp_map = CorpMap(territories=cells, player_start_id="a")
    rendered = render_ascii_map(corp_map)

    assert BREAK_MARKER not in rendered.text
    assert rendered.break_spans == []


@pytest.mark.parametrize("seed", SEEDS)
def test_render_marks_every_unconnected_adjacency_on_a_generated_map(seed):
    """On a real board the two counts have to partition grid adjacency exactly:
    every neighbouring pair is either a link or a break, never both and never
    neither."""
    corp_map = generate_corp_map(FACTIONS, random.Random(seed))
    rendered = render_ascii_map(corp_map)
    by_pos = {(t.x, t.y): t for t in corp_map.territories.values()}

    adjacent = set()
    for (x, y), territory in by_pos.items():
        for neighbour in (by_pos.get((x + 1, y)), by_pos.get((x, y + 1))):
            if neighbour is not None:
                adjacent.add((territory.id, neighbour.id))

    linked = {(c.territory_a, c.territory_b) for c in rendered.connector_spans}
    broken = {(b.territory_a, b.territory_b) for b in rendered.break_spans}
    assert linked | broken == adjacent
    assert not linked & broken
    assert broken, "a generated board always has some adjacent-but-unlinked pairs"
    # Every span lands on the marker it claims to cover -- the offsets are computed
    # from column widths that vary with the longest label in each column, so this is
    # what catches a placement drifting off the glyph.
    for span in rendered.break_spans:
        width = span.end - span.start
        assert rendered.text[span.offset : span.offset + width] == BREAK_MARKER * width
