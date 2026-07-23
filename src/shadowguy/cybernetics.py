"""Cyberware: persistent body modifications a runner can have installed.

First slice of a system, not the whole thing yet -- deliberately no ripperdoc
LocationKind/ShopScreen wiring (that's map-generation surface: corpmap.py's
name pools, LOCATION_SKILL, GENERATED_KINDS guards, jobs.LEGWORK_APPROACH_TEXT,
gigs._GIG_TEMPLATES all have to agree, the same reason a new shop kind isn't a
small change), and no Humanity/cyberpsychosis cost -- install_cyberware just
charges cash. What's real today: a catalog, one slot per CyberSlot
(install_cyberware/remove_cyberware enforce that), and it's load-bearing --
Character.stat()/skill_gear_bonus already fold installed_bonus/
installed_skill_bonus in alongside worn gear, so cyberware strengthens checks
the moment it's installed, the same as an equipped Item. The missing piece is
purely acquisition: nothing calls install_cyberware from a screen yet.

Cyberware is installed, not equipped -- there's no equipped=True/False toggle
the way shops.InventoryItem has one. Swapping a slot means removing the old
piece first (no refund; ripping out cyberware is surgery, not a sale) and then
installing the new one, rather than owning several and flipping a flag.

Leaf module like shops.py/runners.py: imports nothing from the package at
runtime (Character is TYPE_CHECKING-only), so character.py can import this
without a cycle.
"""

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from shadowguy.skills import skill_for

if TYPE_CHECKING:
    from shadowguy.character import Character


class CyberSlot(Enum):
    NEURALWARE = "neuralware"
    OPTICS = "optics"
    ARMS = "arms"
    INTERNAL = "internal"


@dataclass(frozen=True)
class Cyberware:
    id: str
    name: str
    price: int
    slot: CyberSlot
    # stat name (character.CORE_STATS) -> bonus, same shape as shops.Item.bonuses.
    bonuses: dict[str, int]
    # skill id (skills.SKILLS_BY_ID) -> bonus, same shape as shops.Item.skill_bonuses.
    skill_bonuses: dict[str, int]
    # Short flavor tag, same convention as shops.Item.tag.
    tag: str = ""


# id, name, price, slot, bonuses, skill_bonuses, tag. First-slice catalog, not
# balance-simulated, and not reachable from any shop screen yet -- see module
# docstring. One stat piece and one skill-specialized piece per slot, the same
# spread shops.py's weapon/armor catalog uses.
CYBERWARE_CATALOG = [
    Cyberware("cybereye_scanner", "Cybereye Scanner", 700, CyberSlot.OPTICS, {"perception": 1}, {}),
    Cyberware(
        "smartgun_link",
        "Smartgun Link",
        1300,
        CyberSlot.OPTICS,
        {},
        {"firearms": 2},
        tag="smartgun-linked",
    ),
    Cyberware("neural_processor", "Neural Processor", 1100, CyberSlot.NEURALWARE, {"intelligence": 1}, {}),
    Cyberware("reflex_coprocessor", "Reflex Coprocessor", 1500, CyberSlot.NEURALWARE, {"agility": 1}, {}),
    Cyberware("hydraulic_cyberarm", "Hydraulic Cyberarm", 1000, CyberSlot.ARMS, {"strength": 2}, {}),
    Cyberware(
        "grapple_rig_cyberarm",
        "Grapple Rig Cyberarm",
        1400,
        CyberSlot.ARMS,
        {},
        {"grapple": 2},
    ),
    Cyberware("subdermal_plating", "Subdermal Plating", 850, CyberSlot.INTERNAL, {"body": 1}, {}),
    Cyberware("synthetic_adrenal_gland", "Synthetic Adrenal Gland", 750, CyberSlot.INTERNAL, {"cool": 1}, {}),
]

CYBERWARE_BY_ID = {cyberware.id: cyberware for cyberware in CYBERWARE_CATALOG}

for _cyberware in CYBERWARE_CATALOG:
    for _skill_id in _cyberware.skill_bonuses:
        skill_for(_skill_id)


def install_cyberware(character: "Character", cyberware_id: str) -> bool:
    """Buy and surgically install one piece of cyberware. Fails closed -- no
    charge, no mutation -- if the runner can't afford it or the piece's
    CyberSlot is already occupied (remove_cyberware it first to swap)."""
    cyberware = CYBERWARE_BY_ID[cyberware_id]
    if cyberware.slot in character.installed_cyberware:
        return False
    if cyberware.price > character.cash:
        return False
    character.cash -= cyberware.price
    character.installed_cyberware[cyberware.slot] = cyberware_id
    return True


def remove_cyberware(character: "Character", slot: CyberSlot) -> str | None:
    """Uninstall whatever occupies `slot`, freeing it. No refund. Returns the
    removed cyberware's id, or None if the slot was already empty."""
    return character.installed_cyberware.pop(slot, None)


def installed_bonus(installed: dict[CyberSlot, str], stat: str) -> int:
    """Every installed piece's contribution to `stat`, the cyberware
    counterpart to shops.equipped_bonus -- takes the raw dict rather than a
    Character to stay a leaf, same reason equipped_bonus takes a bare list."""
    return sum(CYBERWARE_BY_ID[cyberware_id].bonuses.get(stat, 0) for cyberware_id in installed.values())


def installed_skill_bonus(installed: dict[CyberSlot, str], skill_id: str) -> int:
    """The cyberware counterpart to shops.equipped_skill_bonus."""
    return sum(
        CYBERWARE_BY_ID[cyberware_id].skill_bonuses.get(skill_id, 0) for cyberware_id in installed.values()
    )
