"""Cyberware: persistent body modifications a runner can have installed.

Acquired in-run at a LocationKind.CYBER_CLINIC, through
screens/shop_screens.py's RipperdocScreen -- install_cyberware charges cash plus
a piece of Character.humanity's capacity (below), gated on standing with the
clinic's owner (Cyberware.min_standing). Clinics were already a fully generated
location kind long before anything sold from one; all that was ever missing was
the screen.

Load-bearing beyond the purchase: Character.stat()/skill_gear_bonus fold
installed_bonus/installed_skill_bonus in alongside worn gear, so cyberware
strengthens checks the moment it's installed, the same as an equipped Item.

Humanity is two numbers. Character.humanity is a *ceiling*, and it erodes:
_scar takes SURGERY_SCARRING off it permanently on every install and every
removal, because both are surgery. free_humanity is that ceiling minus
everything currently installed -- what's actually left of the runner -- and it
is the number that matters: Character.humanity_penalty grades a stat drain off
it, and reaching 0 is cyberpsychosis, which ends the run.

Every Cyberware carries a `humanity_cost`, and the sum across everything
installed can never exceed the ceiling -- the same "capacity caps a purchase"
shape inventory.free_program_slots enforces for a deck's RAM. `humanity_cost`
is a float (Smartlink costs 0.5) rather than an int -- same reason
corp_turn.CorpState.research_points is a float once Brains 2's fractional rates
enter the picture -- so free_humanity can land on a half-point remainder
without rounding it away.

Removal rebounds: the piece stops counting against free_humanity the moment
it's out, so pulling chrome is how a runner climbs back out of a spiral. It
costs only the scar, never the implant's own value, which is why there is no
therapy mechanic. What it isn't is a clean undo -- churning a loadout grinds
the ceiling down with nothing to show for it.

Smartlink (CyberSlot.OPTICS) is the one piece whose effect isn't a flat
bonus: it does nothing on its own, and only grants combat.smartlink_bonus's
to-hit dice when the equipped weapon is itself tagged shops.Item.smartlinked
-- gated on Cyberware.grants_smartlink (has_smartlink below) rather than an
id check, since an Alphaware Smartlink (below) is a second piece that has to grant
the same thing.

Cyberware comes in four quality grades. A grade changes nothing about what a
piece *does* -- same bonuses, skill_bonuses and slot as its Deltaware
counterpart -- only what it costs, via CYBERWARE_TIER_MULTIPLIERS (a
price/humanity_cost multiplier pair per grade, both relative to the Deltaware
row, the baseline catalog below): Deltaware is stock, off-the-shelf chrome
(100% price, regular humanity_cost); Trashware is secondhand -- half price,
but twice the humanity cost of a fresh install; Betaware is lightly tailored
to the buyer (1.2x price, -20% humanity_cost); Alphaware is bespoke, built
for this body alone (2x price, -50% humanity_cost) -- the priciest chrome
costs the least of you. Generated from the Deltaware rows via
dataclasses.replace rather than hand-duplicated, so a higher grade can
never quietly drift from its Deltaware twin's effect.

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
    # Soak-pool bonus, same shape and 1-8 bound as shops.Item.defense (folded into
    # combat.player_soak by installed_defense below, alongside equipped armor) --
    # unlike Item.defense there's no wearable-slot restriction, since every piece
    # of cyberware is always active.
    defense: int = 0
    # Quality grade (see CYBERWARE_TIER_IDS and the module docstring). "deltaware" is
    # the baseline catalog below; a higher grade is a dataclasses.replace of a
    # deltaware row with the same effect and a different price/humanity_cost -- never
    # a separate stat/skill profile.
    tier: str = "deltaware"
    # Whether installing this piece grants a smartlink interface (combat.smartlink_bonus's
    # gate, via has_smartlink) -- a flag rather than an id check because an Alphaware
    # Smartlink is a second row that has to grant the same thing.
    grants_smartlink: bool = False
    # Standing with the clinic's owner (corpmap.LocalCharacter, via
    # Character.local_standing_with) needed before a ripperdoc will sell this piece
    # at all -- the same gate shops.Item/Consumable/Program each carry, and
    # RipperdocScreen hides a row above it exactly the way ShopScreen does.
    #
    # Set per *grade* (CYBERWARE_TIER_MIN_STANDING), never per piece, because the
    # grade already says what it means: Trashware is the cut-price secondhand
    # knockoff that costs more of you, so any back-alley grafter will fit one to a
    # stranger, while Alphaware's bespoke tailoring is what a doc saves for people
    # they know well. The gate therefore runs *opposite* to price -- the cheapest
    # chrome is the most freely available, which is the point of it being the
    # cheapest.
    min_standing: int = 0
    # Extra advantage dice on matrix.py's dice-rolling actions (Breach/Extract via
    # _intrude, Harden, Analyze) -- unconditional, unlike Smartlink's weapon-gated
    # bonus, since Datajack (the first piece to set this) helps every matrix action
    # just by being installed. Summed across installed pieces the same way
    # bonuses/skill_bonuses/defense already are (installed_matrix_action_bonus below),
    # so a later, bigger interface can stack or replace it without a new mechanism.
    matrix_action_bonus: int = 0


# id, name, price, slot, bonuses, skill_bonuses, humanity_cost, tag. First-slice
# Deltaware catalog, not balance-simulated. Every row here is min_standing 0 (see
# CYBERWARE_TIER_MIN_STANDING): the baseline grade is what any clinic will sell a
# stranger, so no *effect* in the catalog is ever locked behind a relationship --
# only the better trade-offs on it. Two pieces per slot, the same spread shops.py's
# weapon/armor catalog uses -- most are a flat stat piece plus a
# skill-specialized piece, except OPTICS, where Smartlink's whole effect is
# conditional (see above) rather than a flat skill_bonuses entry.
# humanity_cost is deliberately uneven: the cheap option in each slot sums to
# 4.0 of HUMANITY_BASELINE's 6 (Smartlink and Datajack tie at 0.5, the cheapest
# single pieces in the catalog), so a runner can afford one simple piece per slot
# but has to give something up to fit any of the pricier, more invasive options in
# on top. (This comment used to say 5.5, which was simply wrong -- worth knowing,
# since character.SURGERY_SCARRING was originally sized against that bad figure.)
_DELTAWARE_CYBERWARE = [
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
        {"logic": 1},
        {},
        humanity_cost=2,
    ),
    # 2.5, not a rounder 3: Trashware's 2x humanity multiplier would put a
    # humanity_cost of 3 at 6.0 -- at HUMANITY_BASELINE, leaving nothing after
    # SURGERY_SCARRING and making reflex_coprocessor_trashware permanently
    # uninstallable (see the guard in character.py).
    Cyberware(
        "reflex_coprocessor",
        "Reflex Coprocessor",
        1500,
        CyberSlot.NEURALWARE,
        {"agility": 1},
        {},
        humanity_cost=2.5,
    ),
    # A small, unconditional edge in the matrix (matrix_action_bonus) rather than a
    # flat stat/skill bonus -- see the field's own comment above. First-slice; more
    # benefits are expected to land on this one later rather than a new implant.
    Cyberware(
        "datajack",
        "Datajack",
        1000,
        CyberSlot.NEURALWARE,
        {},
        {},
        humanity_cost=0.5,
        matrix_action_bonus=1,
    ),
    Cyberware(
        "hydraulic_cyberarm", "Hydraulic Cyberarm", 1000, CyberSlot.ARMS, {"strength": 2}, {}, humanity_cost=2
    ),
    # 2.5, same reason as reflex_coprocessor above: Trashware's 2x would otherwise
    # put this at 6.0, past HUMANITY_BASELINE.
    Cyberware(
        "grapple_rig_cyberarm",
        "Grapple Rig Cyberarm",
        1400,
        CyberSlot.ARMS,
        {},
        {"grapple": 2},
        humanity_cost=2.5,
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
    # Bone lacing: a soak-pool bonus (defense) rather than a stat/skill bonus, at an
    # escalating price *and* humanity_cost in lockstep with how much of the skeleton
    # is replaced -- unlike the rest of the catalog, where a piece's stat effect stays
    # fixed and only its tier moves the price/humanity trade-off (above).
    Cyberware(
        "steel_bones", "Steel Bones", 1000, CyberSlot.INTERNAL, {}, {}, humanity_cost=1, defense=1
    ),
    Cyberware(
        "titanium_bones", "Titanium Bones", 3000, CyberSlot.INTERNAL, {}, {}, humanity_cost=2, defense=2
    ),
    # 2.8 rather than a rounder 3.5 for a reason that isn't about this row: Trashware
    # multiplies humanity_cost by 2, and 3.5 would put the knockoff at 7.0 -- past
    # HUMANITY_BASELINE, so adamantium_bones_trashware could never be installed by
    # anyone and would sit on a clinic's shelf forever reading "not enough humanity
    # left". 2.8 lands its Trashware variant at 5.6, brutal but reachable.
    # character.py guards this for the whole catalog (it's the module that sees both
    # tables).
    Cyberware(
        "adamantium_bones", "Adamantium Bones", 6000, CyberSlot.INTERNAL, {}, {}, humanity_cost=2.8, defense=4
    ),
]

CYBERWARE_TIER_IDS = ("deltaware", "trashware", "betaware", "alphaware")

# grade -> (price_mult, humanity_mult), both relative to the same piece's deltaware
# row (the baseline, generated at neither multiplier) -- see the module docstring.
CYBERWARE_TIER_MULTIPLIERS: dict[str, tuple[float, float]] = {
    "trashware": (0.5, 2.0),  # -50% price, +100% humanity_cost (secondhand)
    "betaware": (1.2, 0.8),  # +20% price, -20% humanity_cost (lightly tailored)
    "alphaware": (2.0, 0.5),  # +100% price, -50% humanity_cost (bespoke)
}


# grade -> the standing a ripperdoc wants before selling that grade at all (see
# Cyberware.min_standing). Deliberately not ordered by price: Trashware is the cheap
# knockoff anyone will fit, Alphaware the bespoke tailoring a doc saves for regulars.
# Deltaware is the baseline catalog and stays open to everyone, so a runner who has
# never met a grafter can still buy every *effect* in the game -- standing buys a
# better trade-off on the same implant, never access to a bonus you couldn't get.
# First-slice numbers, not balance-simulated.
CYBERWARE_TIER_MIN_STANDING = {"deltaware": 0, "trashware": 0, "betaware": 1, "alphaware": 3}


def _tier_variant(base: Cyberware, tier: str) -> Cyberware:
    """A higher-grade row derived from a deltaware one via dataclasses.replace, so
    it can never quietly drift from its deltaware twin's bonuses/skill_bonuses/
    slot -- only price, humanity_cost and min_standing move."""
    price_mult, humanity_mult = CYBERWARE_TIER_MULTIPLIERS[tier]
    return replace(
        base,
        id=f"{base.id}_{tier}",
        name=f"{base.name} ({tier.capitalize()})",
        price=round(base.price * price_mult),
        humanity_cost=round(base.humanity_cost * humanity_mult, 2),
        tier=tier,
        min_standing=CYBERWARE_TIER_MIN_STANDING[tier],
    )


CYBERWARE_CATALOG = _DELTAWARE_CYBERWARE + [
    _tier_variant(base, tier) for tier in CYBERWARE_TIER_MULTIPLIERS for base in _DELTAWARE_CYBERWARE
]

CYBERWARE_BY_ID = {cyberware.id: cyberware for cyberware in CYBERWARE_CATALOG}

# The Deltaware baseline id of the one piece that grants a smartlink interface today.
# has_smartlink checks Cyberware.grants_smartlink rather than this directly -- see
# the module docstring -- but tests/callers that want "the" Smartlink still want
# this one.
SMARTLINK_ID = "smartlink"

for _cyberware in CYBERWARE_CATALOG:
    for _skill_id in _cyberware.skill_bonuses:
        skill_for(_skill_id)
    if _cyberware.humanity_cost < 0:
        raise ValueError(f"{_cyberware.id}: humanity_cost must be >= 0")
    if _cyberware.tier not in CYBERWARE_TIER_IDS:
        raise ValueError(f"{_cyberware.id}: tier must be one of {CYBERWARE_TIER_IDS}")
    if _cyberware.defense and not (1 <= _cyberware.defense <= 8):
        raise ValueError(f"{_cyberware.id}: defense must be 1-8")
    if _cyberware.matrix_action_bonus < 0:
        raise ValueError(f"{_cyberware.id}: matrix_action_bonus must be >= 0")

if len(CYBERWARE_BY_ID) != len(CYBERWARE_CATALOG):
    raise ValueError("CYBERWARE_CATALOG has duplicate ids")

# Deltaware rows take min_standing from Cyberware's own default rather than from
# CYBERWARE_TIER_MIN_STANDING (only _tier_variant reads that table, and only for
# the other three grades), so the table's deltaware entry would otherwise be
# decorative -- editable with no effect on anything in src/. Tie the two together
# here so they can't drift.
for _cyberware in CYBERWARE_CATALOG:
    if _cyberware.min_standing != CYBERWARE_TIER_MIN_STANDING[_cyberware.tier]:
        raise ValueError(
            f"{_cyberware.id}: min_standing must match CYBERWARE_TIER_MIN_STANDING"
            f"[{_cyberware.tier}]"
        )


def installed_humanity_cost(installed: dict[CyberSlot, str]) -> float:
    """Total Humanity capacity spent by everything currently installed -- the
    cyberware counterpart to inventory.equipped_bonus, and the `used` half of
    free_humanity below. Takes the raw dict rather than a Character to stay a
    leaf, same reason installed_bonus does."""
    return sum(CYBERWARE_BY_ID[cyberware_id].humanity_cost for cyberware_id in installed.values())


def _scar(character: "Character") -> None:
    """Permanently lower a runner's Humanity ceiling by one operation's worth.

    Charged by both install_cyberware and remove_cyberware -- both are surgery. The
    constant lives in character.py (SURGERY_SCARRING) alongside HUMANITY_BASELINE
    since that's where the ceiling itself is defined; imported lazily here so this
    module stays a runtime leaf, the same reason Character is TYPE_CHECKING-only.

    Rounded to 2dp each time, matching _tier_variant's own rounding, so repeated
    operations can't accumulate binary-float dust into the ceiling."""
    from shadowguy.character import SURGERY_SCARRING

    character.humanity = round(character.humanity - SURGERY_SCARRING, 2)


def free_humanity(character: "Character") -> float:
    """How much of the runner is actually left: the Humanity ceiling
    (Character.humanity, itself worn down by _scar) minus everything currently
    installed.

    This is *the* Humanity number -- what CharacterSheet shows, what
    Character.humanity_penalty reads, and what reaching 0 ends the run over. The
    ceiling on its own says only how much room a runner was born with and has since
    given up to surgery; this says how much of them is still theirs."""
    return round(
        character.humanity - installed_humanity_cost(character.installed_cyberware), 2
    )


def has_smartlink(installed: dict[CyberSlot, str]) -> bool:
    """Whether any installed piece grants a smartlink interface (Deltaware or
    Alphaware Smartlink both do) -- the gate combat.py's smartlink_bonus checks before
    granting extra to-hit dice against a shops.Item.smartlinked weapon. Takes
    the raw dict to stay a leaf, same reason installed_bonus does."""
    return any(CYBERWARE_BY_ID[cyberware_id].grants_smartlink for cyberware_id in installed.values())


def _effective_standing(standing: int) -> int:
    """Standing floored at 0 for gating purposes.

    Without this a *negative* standing (a failed gig at the clinic is enough --
    gigs.GIG_FAIL_STANDING_HIT) would hide the entire catalog rather than just the
    gated tiers, since every row's min_standing is >= 0. That would break the one
    thing CYBERWARE_TIER_MIN_STANDING promises: Deltaware is open to everyone, so no
    *effect* is ever unreachable. A doc you've annoyed doesn't cut you a deal on
    the good chrome; they still sell you the baseline.

    Note shops.py deliberately does NOT do this -- a shopkeeper who dislikes you
    can refuse their whole stock, because no shop catalog commits to an
    always-available tier the way this one does."""
    return max(standing, 0)


def catalog_for_standing(standing: int) -> list[Cyberware]:
    """Everything a clinic will sell someone at this standing with its owner --
    CYBERWARE_CATALOG filtered by min_standing, in catalog order (Deltaware first,
    then each higher tier as a block). The read side RipperdocScreen renders,
    mirroring ShopScreen's own `if item.min_standing > standing: continue`."""
    effective = _effective_standing(standing)
    return [cyberware for cyberware in CYBERWARE_CATALOG if cyberware.min_standing <= effective]


def lost_to_cyberpsychosis(character: "Character") -> bool:
    """Whether there is nothing left of the runner -- free Humanity at or below 0.

    install_cyberware deliberately allows the install that causes this (see its own
    note), so **every caller of install_cyberware must check this afterwards** and end
    the run if it's true. It lives here rather than inside install_cyberware because
    ending a run is an app-level act (app.exit), and cybernetics.py is a leaf that
    can't reach it -- but the *condition* belongs with the model, not duplicated in
    whichever screen happens to sell chrome. Today that's RipperdocScreen alone."""
    return free_humanity(character) <= 0


def install_cyberware(character: "Character", cyberware_id: str, standing: int = 0) -> bool:
    """Buy and surgically install one piece of cyberware. Fails closed -- no
    charge, no mutation -- if the clinic won't sell it at this standing, the
    runner can't afford it, the piece's CyberSlot is already occupied
    (remove_cyberware it first to swap), or it wouldn't fit in whatever Humanity
    capacity is left free.

    `standing` defaults to 0 so every pre-existing caller (and any clinic with no
    owner NPC) keeps working, the same default shops.buy_item uses.

    Note there is no standing *discount* here, unlike shops.buy_price: standing
    decides what a doc is willing to cut into you, not what they charge for it.
    Keeping price out of it is also what keeps this module a leaf -- a discount
    would mean importing shops.

    Only refuses a piece that doesn't *fit*. A piece costing exactly what's left is
    legal, and then this operation's own scar takes free Humanity to or below 0 --
    which is cyberpsychosis. That is deliberate (the player is allowed to walk off
    the cliff, and RipperdocScreen labels the row before they do), so **callers must
    check lost_to_cyberpsychosis afterwards** and end the run."""
    cyberware = CYBERWARE_BY_ID[cyberware_id]
    if _effective_standing(standing) < cyberware.min_standing:
        return False
    if cyberware.slot in character.installed_cyberware:
        return False
    if cyberware.price > character.cash:
        return False
    if cyberware.humanity_cost > free_humanity(character):
        return False
    character.cash -= cyberware.price
    character.installed_cyberware[cyberware.slot] = cyberware_id
    _scar(character)
    return True


def remove_cyberware(character: "Character", slot: CyberSlot) -> str | None:
    """Uninstall whatever occupies `slot`, freeing it. No cash refund. Returns the
    removed cyberware's id, or None if the slot was already empty.

    Humanity *does* come back: the piece stops counting against free_humanity the
    moment it's out, so pulling chrome is how a runner climbs back out of a
    cyberpsychosis spiral. It isn't free, though -- the operation scars like any
    other (_scar), so the rebound is the implant's whole humanity_cost minus
    SURGERY_SCARRING, always a clear net gain (the cheapest implant costs 0.5
    against a 0.1 scar) but never a clean undo.

    An empty slot is a no-op and is deliberately *not* scarred -- nobody opened
    anyone up."""
    removed = character.installed_cyberware.pop(slot, None)
    if removed is not None:
        _scar(character)
    return removed


def installed_bonus(installed: dict[CyberSlot, str], stat: str) -> int:
    """Every installed piece's contribution to `stat`, the cyberware
    counterpart to inventory.equipped_bonus -- takes the raw dict rather than a
    Character to stay a leaf, same reason equipped_bonus takes a bare list."""
    return sum(CYBERWARE_BY_ID[cyberware_id].bonuses.get(stat, 0) for cyberware_id in installed.values())


def installed_skill_bonus(installed: dict[CyberSlot, str], skill_id: str) -> int:
    """The cyberware counterpart to inventory.equipped_skill_bonus."""
    return sum(
        CYBERWARE_BY_ID[cyberware_id].skill_bonuses.get(skill_id, 0) for cyberware_id in installed.values()
    )


def installed_defense(installed: dict[CyberSlot, str]) -> int:
    """Every installed piece's contribution to the soak pool -- the cyberware
    counterpart to inventory.equipped_defense, folded into combat.player_soak
    alongside worn armor."""
    return sum(CYBERWARE_BY_ID[cyberware_id].defense for cyberware_id in installed.values())


def installed_matrix_action_bonus(installed: dict[CyberSlot, str]) -> int:
    """Every installed piece's contribution to matrix.py's dice-rolling actions --
    Datajack's small edge today, summed the same way installed_defense sums
    defense, in case a later, better interface stacks or replaces it."""
    return sum(CYBERWARE_BY_ID[cyberware_id].matrix_action_bonus for cyberware_id in installed.values())
