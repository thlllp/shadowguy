import random

import pytest

from shadowguy.corpmap import generate_corp_map
from shadowguy.factions import FACTIONS


@pytest.fixture(scope="module")
def corp_map():
    return generate_corp_map(FACTIONS, random.Random(0))
