"""Cyberware: persistent body modifications a runner can have installed.

First slice of a system, not the whole thing yet -- deliberately no ripperdoc
LocationKind/ShopScreen wiring (that's map-generation surface: corpmap.py's
name pools, LOCATION_SKILL, GENERATED_KINDS guards, jobs.LEGWORK_APPROACH_TEXT,
gigs._GIG_TEMPLATES all have to agree, the same reason a new shop kind isn't a
small change) -- install_cyberware just charges cash, plus a piece of
Character.humanity's capacity (below). No cyberpsychosis cost yet: Humanity is
only spent as install-time capacity today, nothing reads how much is left over
for anything else. What's real today: a catalog, one slot per CyberSlot
(install_cyberware/remove_cyberware enforce that), and it's load-bearing --
Character.stat()/skill_gear_bonus already fold installed_bonus/
installed_skill_bonus in alongside worn gear, so cyberware strengthens checks
the moment it's installed, the same as an equipped Item. The missing piece is
purely acquisition: nothing calls install_cyberware from a screen yet.

Humanity as capacity: every Cyberware carries a `humanity_cost`, and the sum
across everything installed can never exceed Character.humanity
(HUMANITY_BASELINE, 6) -- the same "capacity caps a purchase" shape
shops.free_program_slots enforces for a deck's RAM. Nothing lowers
Character.humanity itself yet (it's a fixed baseline, see character.py), so
today this is purely a loadout budget: how much of a runner's whole four-slot
frame they can afford to replace at once, not a resource that depletes over a
run. `humanity_cost` is a float (Smartlink costs 0.5) rather than an int --
same reason corp_turn.CorpState.research_points is a float once Brains 2's
fractional rates enter the picture -- so free_humanity can land on a
half-point remainder without rounding it away.

Smartlink (CyberSlot.OPTICS) is the one piece whose effect isn't a flat
bonus: it does nothing on its own, and only grants combat.smartlink_bonus's
to-hit dice when the equipped weapon is itself tagged shops.Item.smartlinked
-- gated on Cyberware.grants_smartlink (has_smartlink below) rather than an
id check, since a Tier 4 Smartlink (below) is a second piece that has to grant
the same thing.

Cyberware comes in four quality tiers. A tier changes nothing about what a
piece *does* -- same bonuses, skill_bonuses and slot as its Tier 1
counterpart -- only what it costs, via CYBERWARE_TIER_MULTIPLIERS (a
price/humanity_cost multiplier pair per tier, both relative to the Tier 1
row, not the tier below it): Tier 2 trades nothing on price for a small
humanity saving (-10% humanity_cost, a cleaner install at the same cost),
Tier 3 a modest step down (-25% price, +10% humanity_cost), and Tier 4 a
much cruder knockoff (-50% price, +60% humanity_cost) of the same implant --
cheaper chrome costs more of you. Generated from the Tier 1 rows via
dataclasses.replace rather than hand-duplicated, so a higher-tier piece can
never quietly drift from its Tier 1 twin's effect.

Cyberware is installed, not equipped -- there's no equipped=True/False toggle
the way shops.InventoryItem has one. Swapping a slot means removing the old
piece first (no refund; ripping out cyberware is surgery, not a sale) and then
installing the new one, rather than owning several and flipping a flag.

Leaf module like shops.py/runners.py: imports nothing from the package at
runtime (Character is TYPE_CHECKING-only), so character.py can import this
without a cycle.
"""

from dataclasses import dataclass, replace
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
    # How much of Character.humanity's capacity installing this piece spends --
    # the cyberware counterpart to shops.Program.ram_cost. Checked against what's
    # left free (see free_humanity) rather than against the baseline directly, so
    # several pieces stack against the one budget. A float: Smartlink costs 0.5.
    humanity_cost: float
    # Short flavor tag, same convention as shops.Item.tag.
    tag: str = ""
    # Quality grade, 1-4 (see VALID_CYBERWARE_TIERS and the module docstring). Tier 1
    # is the baseline catalog below; a higher tier is a dataclasses.replace of a Tier
    # 1 row with the same effect and a different price/humanity_cost -- never a
    # separate stat/skill profile.
    tier: int = 1
    # Whether installing this piece grants a smartlink interface (combat.smartlink_bonus's
    # gate, via has_smartlink) -- a flag rather than an id check because a Tier 4
    # Smartlink is a second row that has to grant the same thing.
    grants_smartlink: bool = False


# id, name, price, slot, bonuses, skill_bonuses, humanity_cost, tag. First-slice
# Tier 1 catalog, not balance-simulated, and not reachable from any shop screen
# yet -- see module docstring. Two pieces per slot, the same spread shops.py's
# weapon/armor catalog uses -- most are a flat stat piece plus a
# skill-specialized piece, except OPTICS, where Smartlink's whole effect is
# conditional (see above) rather than a flat skill_bonuses entry.
# humanity_cost is deliberately uneven: the cheap option in each slot sums to
# 5.5 of HUMANITY_BASELINE's 6 (Smartlink's 0.5 is the cheapest single piece
# in the catalog), so a runner can afford one simple piece per slot but has to
# give something up to fit any of the pricier, more invasive options in on top.
_TIER_1_CYBERWARE = [
    Cyberware(
        "cybereye_scanner", "Cybereye Scanner", 700, CyberSlot.OPTICS, {"perception": 1}, {}, humanity_cost=1
    ),
    # Grants nothing by itself -- see combat.smartlink_bonus and the module
    # docstring above: it only does something paired with a shops.Item.smartlinked
    # weapon (today, just the pipe pistol).
    Cyberware(
        "smartlink",
        "Smartlink",
        1500,
        CyberSlot.OPTICS,
        {},
        {},
        humanity_cost=0.5,
        tag="smartlinked",
        grants_smartlink=True,
    ),
    Cyberware(
        "neural_processor",
        "Neural Processor",
        1100,
        CyberSlot.NEURALWARE,
        {"intelligence": 1},
        {},
        humanity_cost=2,
    ),
    Cyberware(
        "reflex_coprocessor",
        "Reflex Coprocessor",
        1500,
        CyberSlot.NEURALWARE,
        {"agility": 1},
        {},
        humanity_cost=3,
    ),
    Cyberware(
        "hydraulic_cyberarm", "Hydraulic Cyberarm", 1000, CyberSlot.ARMS, {"strength": 2}, {}, humanity_cost=2
    ),
    Cyberware(
        "grapple_rig_cyberarm",
        "Grapple Rig Cyberarm",
        1400,
        CyberSlot.ARMS,
        {},
        {"grapple": 2},
        humanity_cost=3,
    ),
    Cyberware(
        "subdermal_plating", "Subdermal Plating", 850, CyberSlot.INTERNAL, {"body": 1}, {}, humanity_cost=1
    ),
    Cyberware(
        "synthetic_adrenal_gland",
        "Synthetic Adrenal Gland",
        750,
        CyberSlot.INTERNAL,
        {"cool": 1},
        {},
        humanity_cost=2,
    ),
]

VALID_CYBERWARE_TIERS = (1, 2, 3, 4)

# tier -> (price_mult, humanity_mult), both relative to the same piece's Tier 1 row
# (not the tier below it) -- see the module docstring.
CYBERWARE_TIER_MULTIPLIERS: dict[int, tuple[float, float]] = {
    2: (1.0, 0.9),  # same price, -10% humanity_cost
    3: (0.75, 1.10),  # -25% price, +10% humanity_cost
    4: (0.5, 1.6),  # -50% price, +60% humanity_cost
}


def _tier_variant(base: Cyberware, tier: int) -> Cyberware:
    """A higher-tier row derived from a Tier 1 one via dataclasses.replace, so
    it can never quietly drift from its Tier 1 twin's bonuses/skill_bonuses/
    slot -- only price and humanity_cost move."""
    price_mult, humanity_mult = CYBERWARE_TIER_MULTIPLIERS[tier]
    return replace(
        base,
        id=f"{base.id}_t{tier}",
        name=f"{base.name} (Tier {tier})",
        price=round(base.price * price_mult),
        humanity_cost=round(base.humanity_cost * humanity_mult, 2),
        tier=tier,
    )


CYBERWARE_CATALOG = _TIER_1_CYBERWARE + [
    _tier_variant(base, tier) for tier in sorted(CYBERWARE_TIER_MULTIPLIERS) for base in _TIER_1_CYBERWARE
]

CYBERWARE_BY_ID = {cyberware.id: cyberware for cyberware in CYBERWARE_CATALOG}

# The Tier 1 baseline id of the one piece that grants a smartlink interface today.
# has_smartlink checks Cyberware.grants_smartlink rather than this directly -- see
# the module docstring -- but tests/callers that want "the" Smartlink still want
# this one.
SMARTLINK_ID = "smartlink"

for _cyberware in CYBERWARE_CATALOG:
    for _skill_id in _cyberware.skill_bonuses:
        skill_for(_skill_id)
    if _cyberware.humanity_cost < 0:
        raise ValueError(f"{_cyberware.id}: humanity_cost must be >= 0")
    if _cyberware.tier not in VALID_CYBERWARE_TIERS:
        raise ValueError(f"{_cyberware.id}: tier must be one of {VALID_CYBERWARE_TIERS}")

if len(CYBERWARE_BY_ID) != len(CYBERWARE_CATALOG):
    raise ValueError("CYBERWARE_CATALOG has duplicate ids")


def installed_humanity_cost(installed: dict[CyberSlot, str]) -> float:
    """Total Humanity capacity spent by everything currently installed -- the
    cyberware counterpart to shops.equipped_bonus, and the `used` half of
    free_humanity below. Takes the raw dict rather than a Character to stay a
    leaf, same reason installed_bonus does."""
    return sum(CYBERWARE_BY_ID[cyberware_id].humanity_cost for cyberware_id in installed.values())


def free_humanity(character: "Character") -> float:
    """How much of Character.humanity's capacity is still unspent -- the
    cyberware counterpart to shops.free_program_slots."""
    return character.humanity - installed_humanity_cost(character.installed_cyberware)


def has_smartlink(installed: dict[CyberSlot, str]) -> bool:
    """Whether any installed piece grants a smartlink interface (Tier 1 or Tier
    4 Smartlink both do) -- the gate combat.py's smartlink_bonus checks before
    granting extra to-hit dice against a shops.Item.smartlinked weapon. Takes
    the raw dict to stay a leaf, same reason installed_bonus does."""
    return any(CYBERWARE_BY_ID[cyberware_id].grants_smartlink for cyberware_id in installed.values())


def install_cyberware(character: "Character", cyberware_id: str) -> bool:
    """Buy and surgically install one piece of cyberware. Fails closed -- no
    charge, no mutation -- if the runner can't afford it, the piece's CyberSlot
    is already occupied (remove_cyberware it first to swap), or it wouldn't
    fit in whatever Humanity capacity is left free."""
    cyberware = CYBERWARE_BY_ID[cyberware_id]
    if cyberware.slot in character.installed_cyberware:
        return False
    if cyberware.price > character.cash:
        return False
    if cyberware.humanity_cost > free_humanity(character):
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
