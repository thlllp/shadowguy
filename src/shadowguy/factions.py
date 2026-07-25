"""Rival corp Factions that own map territory, plus the HQ officer ladder and dialogue."""

from dataclasses import dataclass
from enum import StrEnum


class FactionSpecialty(StrEnum):
    WEAPONS = "weapons"
    HACKING = "hacking"
    PHARMA = "pharma"
    CYBERNETICS = "cybernetics"


@dataclass
class Faction:
    id: str
    name: str
    specialty: FactionSpecialty
    description: str


FACTIONS = [
    Faction(
        id="faction_ironclad",
        name="Ironclad Dynamics",
        specialty=FactionSpecialty.WEAPONS,
        description="Arms manufacturer running the city's black-market hardware and muscle.",
    ),
    Faction(
        id="faction_ghostwire",
        name="Ghostwire Collective",
        specialty=FactionSpecialty.HACKING,
        description="Netrunner syndicate that trades in stolen data, backdoors, and ICE-breakers.",
    ),
    Faction(
        id="faction_meridian",
        name="Meridian Biochem",
        specialty=FactionSpecialty.PHARMA,
        description="Pharmaceutical conglomerate pushing combat stims and black-clinic wetware.",
    ),
    Faction(
        id="faction_prometheus",
        name="Prometheus Cybernetics",
        specialty=FactionSpecialty.CYBERNETICS,
        description="Augmentation conglomerate grafting grey-market chrome into anyone who can pay.",
    ),
]

FACTIONS_BY_ID = {faction.id: faction for faction in FACTIONS}

# The corporate officers you can reach inside a corp's HQ (corpmap.LocationKind.CORP_HQ,
# one per faction). A rank ladder: each higher officer needs BOTH more street rep AND
# more standing with that corp to get past — the exec suite is for a runner the corp both
# knows (rep) and trusts (standing). Rows are (role, min_rep, min_standing). The ground-floor
# reception has min_standing None — it doesn't care how this *corp* feels about you
# (negative standing still gets the cold shoulder rather than the door, see
# officer_dialogue) — but it does care whether the street knows your name at all: min_rep
# 0 means a runner whose rep has gone negative (a blown job or gig costs rep now — see
# character.REP_FLOOR) gets turned away even from the lobby. Talking is flavor only for now.
# Named because the takeover gate below keys off this specific rung — the exec suite is
# where a corp actually changes hands. Referenced in the table rather than repeated, so
# a rename can't leave the two disagreeing.
EXECUTIVE_ROLE = "executive"

CORP_OFFICER_TIERS = (
    ("receptionist", 0, None),
    ("operations manager", 5, 3),
    (EXECUTIVE_ROLE, 12, 8),
)

# Keyed by role rather than position: an HQ's officers (corpmap._make_officers) and this
# table line up by role, not list order, so a reorder of Location.characters can't
# silently hand one officer another's gate.
_CORP_OFFICER_TIERS_BY_ROLE = {role: (min_rep, min_standing) for role, min_rep, min_standing in CORP_OFFICER_TIERS}


def officer_unlocked(rep: int, standing: int, role: str) -> bool:
    """Whether an HQ officer of this role will see you. min_standing None is the public
    lobby — rep-gated only, open even when the corp is hostile (see CORP_OFFICER_TIERS)."""
    min_rep, min_standing = _CORP_OFFICER_TIERS_BY_ROLE[role]
    return rep >= min_rep and (min_standing is None or standing >= min_standing)


# What it takes for a runner to be handed a corp from the inside — the terms the
# executive names once they'll actually see you (screens/shop_screens.CorpHQScreen).
# Deliberately *above* the executive tier's own gate: reaching the exec suite buys the
# conversation, not the company.
#
# Rep and standing rise together for a runner working a corp's rivals — a completed job
# is +1 rep and, via factions.standing_shift, +1 standing with every rival of the corp it
# hit — so these two land at roughly the same point in a run by design rather than
# stacking into two separate grinds. The cash is the part you have to deliberately save
# for, and it's the gate most likely to need tuning: at roughly 30 completed jobs of gross
# income (jobs.REWARD_BASE) it sits an order of magnitude past the priciest thing in any
# shop (1400eb), which is the point — a corp is not a purchase you make in passing.
#
# CLAUDE.md: switching modes is "meant to be difficult — neither mode is a straight upgrade
# over the other". These are first-slice numbers, not balance-simulated.
TAKEOVER_MIN_REP = 20
TAKEOVER_MIN_STANDING = 15
TAKEOVER_COST = 15000


def can_take_over(rep: int, standing: int, cash: int) -> bool:
    """Whether this runner can buy a controlling stake in a corp today. Takes plain ints
    (like officer_unlocked) so factions.py stays a leaf — the caller reads them off the
    Character."""
    return rep >= TAKEOVER_MIN_REP and standing >= TAKEOVER_MIN_STANDING and cash >= TAKEOVER_COST


def takeover_gate(rep: int, standing: int, cash: int) -> str:
    """What's still standing between this runner and the corp, for the locked label —
    only the unmet requirements, so the line shrinks as they close in on it rather than
    restating the whole wall every time."""
    missing = []
    if rep < TAKEOVER_MIN_REP:
        missing.append(f"rep {TAKEOVER_MIN_REP} (you have {rep})")
    if standing < TAKEOVER_MIN_STANDING:
        missing.append(f"standing {TAKEOVER_MIN_STANDING:+d} (you have {standing:+d})")
    if cash < TAKEOVER_COST:
        missing.append(f"{TAKEOVER_COST}eb (you have {cash})")
    return ", ".join(missing)


def officer_gate(role: str) -> str:
    """What an officer's gate demands, for the locked label / wave-off line."""
    min_rep, min_standing = _CORP_OFFICER_TIERS_BY_ROLE[role]
    need = f"rep {min_rep}"
    if min_standing is not None:
        need += f", standing {min_standing:+d}"
    return need


def officer_dialogue(faction: Faction, role: str, standing: int) -> str:
    """A line from an HQ officer, themed on the corp and how it feels about you (standing).

    Flavor only — talking costs nothing and changes nothing yet; this is the hook the
    corp-side game will hang concrete interactions on.
    """
    if standing < 0:
        return (
            f'The {role} does not stand. "{faction.name} knows exactly who has been '
            f'working against us. You have nerve, walking in here. Say it and go."'
        )
    if standing == 0:
        return (
            f'The {role} looks you over. "{faction.name} does not do business with '
            f'strangers. Make a name for yourself and come back."'
        )
    return (
        f'The {role} waves you to a seat. "Always good to see a friend of '
        f'{faction.name}. We look after the people who look after us."'
    )


# Hitting a corp is a favour to its rivals: they move the opposite way, at half weight.
RIVAL_WEIGHT = 2


def standing_shift(target_faction_id: str, delta: int) -> dict[str, int]:
    """Standing change for every faction when `delta` is applied to the one you hit."""
    rival_delta = -delta // RIVAL_WEIGHT
    return {
        faction.id: delta if faction.id == target_faction_id else rival_delta
        for faction in FACTIONS
    }
