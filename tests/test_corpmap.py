"""Tests for corpmap.py's model — the runtime mutators over a Territory.

Laying a map out is corpmap_gen.py, and its (much larger) invariant sweep lives in
test_corpmap_gen.py.
"""

import random

from shadowguy.corpmap import (
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
)


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
