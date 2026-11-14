"""A stable integer from a string, for everything seeded off a territory id.

Python's own hash() is salted per process, which would make a district's town square
and the people standing in it change between runs of the game -- the opposite of what
a fixed layout is for. zlib.crc32 is stable across processes and runs.

A leaf: imports nothing from the package, so both `townsquare` and `npcs` can use it.
"""

import zlib


def seed_from(text: str) -> int:
    return zlib.crc32(text.encode("utf-8"))
