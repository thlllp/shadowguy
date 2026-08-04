"""Tests for corpmap.py's model — the runtime mutators over a Territory.

Laying a map out is corpmap_gen.py, and its (much larger) invariant sweep lives in
test_corpmap_gen.py.
"""

import random

from shadowguy.corpmap import (
    MODIFIER_MAX,
    LocationKind,
    Territory,
    TerritoryModifier,
    add_safehouse,
    build_workshop,
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
