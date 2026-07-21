"""Tests for relations.py: seeded standing between every Faction/Gang pair."""

import random
from itertools import combinations

import pytest

from shadowguy.relations import (
    ENTITY_IDS,
    RELATION_MAX,
    RELATION_MIN,
    generate_relations,
    relation,
)

SEEDS = range(150)


@pytest.mark.parametrize("seed", SEEDS)
def test_generate_relations_covers_every_pair_exactly_once(seed):
    relations = generate_relations(random.Random(seed))
    expected = {frozenset((a, b)) for a, b in combinations(ENTITY_IDS, 2)}
    assert set(relations) == expected


@pytest.mark.parametrize("seed", SEEDS)
def test_generate_relations_stays_within_the_neutral_band(seed):
    relations = generate_relations(random.Random(seed))
    assert all(RELATION_MIN <= value <= RELATION_MAX for value in relations.values())


def test_relation_is_symmetric():
    relations = generate_relations(random.Random(0))
    a, b = ENTITY_IDS[0], ENTITY_IDS[1]
    assert relation(relations, a, b) == relation(relations, b, a)
