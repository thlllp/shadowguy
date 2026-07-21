"""Procedural generation and nightly resolution of Security contracts offered by Fixers.

Unlike a job (jobs.py) — a Scene walked once in a single sitting — a Security contract
is a standing engagement: accept it, then be standing in its territory when a day
boundary ticks over to work a night's watch. There is deliberately no Scene/Stage/Choice
graph here: nothing in scene.py has any day-awareness (every Outcome resolves once,
synchronously), so this is a parallel data path rather than a shoehorned Scene.
resolve_security_night is called once per contract per day tick by ShadowguyApp's
_apply_day_tick, the same tick that calls Character.pay_crew_wages() — this is that
mechanic's inverse (the corp pays the runner) plus a lodging-waiver side effect the
caller applies separately.
"""

import random
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shadowguy.checks import CheckResult, CheckRoll, day_tier, resolve_check, resolve_rng
from shadowguy.corpmap import GENERATED_KINDS, CorpMap
from shadowguy.factions import FACTIONS_BY_ID, standing_shift
from shadowguy.skills import skill_for, skill_value

if TYPE_CHECKING:
    from shadowguy.character import Character

# Tier scaling shares checks.day_tier's cadence with jobs/gigs, but the curve itself
# mirrors gigs.GIG_DIFFICULTY rather than jobs.DIFFICULTY_BASE: a nightly watch is a
# recurring, lower-stakes check, the same shape as a gig's, not a job stage's. This
# also isn't cosmetic — checks.pool_for_difficulty caps the opposing pool at
# round((d-9)/2), and CRITICAL_FAILURE needs the player's margin at or below
# -CRITICAL_MARGIN (-3), which is impossible if the opposing pool can't reach 3 dice.
# A lower curve would make the contract un-blowable; this is the lowest one that isn't.
DIFFICULTY_BASE = (11, 13, 15)
NIGHTLY_PAY_BASE = (35, 50, 70)
if len(DIFFICULTY_BASE) != len(NIGHTLY_PAY_BASE):
    raise ValueError("DIFFICULTY_BASE and NIGHTLY_PAY_BASE must cover the same tiers")

NIGHTS_RANGE = (3, 5)
COMPLETION_BONUS_FRACTION = 0.5
# Same crit-pay convention as gigs.GIG_CRIT_MULT.
CRITICAL_SUCCESS_PAY_MULT = 1.6

# What a nightly watch rolls — picked once per contract at generation, same as a job's
# difficulty: what you're watching for varies (patrol gaps, camera blind spots, a fast
# talker at the gate), but a given contract rolls the same faculty every night.
WATCH_SKILLS = ("sight", "listening", "tactics", "read_the_room")
for _skill_id in WATCH_SKILLS:
    skill_for(_skill_id)  # unknown id: fail at import, not mid-generation

# A critical failure costs the same health as a plain failure — per jobs.py's
# convention, a critical failure isn't cheaper on the body than a plain one; the real
# punishment is elsewhere (there, a fight; here, the contract ending and the social
# cost below).
NIGHT_FAILURE_DAMAGE = 2

# Opposite sign convention from jobs.JOB_STANDING_HIT: a job robs the corp (standing
# drops on completion); a security contract works FOR the corp (standing rises on
# completion, drops if you blow it) — named distinctly so that's never ambiguous.
COMPLETION_STANDING_GAIN = 2
COMPLETION_FIXER_TRUST_GAIN = 2
COMPLETION_REP_GAIN = 1
BLOWN_STANDING_HIT = -2
BLOWN_FIXER_TRUST_HIT = -1
BLOWN_REP_HIT = -1


@dataclass
class SecurityContract:
    """An offered or accepted multi-night guard contract. Lives on Fixer.security_offers
    until accepted (Character.accept_security_contract), then on
    Character.security_contracts until it completes or is blown (see
    resolve_security_night) — mirrors JobOffer's move from Fixer.offers to
    Character.accepted_jobs, but there's no Scene here to carry."""

    id: str
    fixer_id: str
    faction_id: str
    # Gates presence: character.location_id is a Territory id (see character.py's
    # location_id comment), so territory_id, not location_id, is what a nightly
    # resolution checks the runner against.
    territory_id: str
    # The specific place being guarded, for flavor text only — nothing gates on it.
    location_id: str
    skill: str
    difficulty: int
    nightly_pay: int
    nights_total: int
    completion_bonus: int
    offered_day: int
    nights_completed: int = 0

    @property
    def is_complete(self) -> bool:
        return self.nights_completed >= self.nights_total


@dataclass
class NightResult:
    """What one resolve_security_night() call did, so the caller (MainMenu) can build
    its notification from data rather than re-deriving it from contract state after
    mutation (e.g. after nights_completed has already moved and the contract may have
    been removed)."""

    roll: CheckRoll
    pay: int  # cash actually paid tonight (0 on failure/blown)
    bonus: int  # completion_bonus paid tonight (0 unless completed tonight)
    blown: bool  # critical failure — contract terminated early
    completed: bool  # nights_completed reached nights_total tonight


def generate_security_contract(
    day: int, corp_map: CorpMap, fixer_id: str, rng: random.Random | None = None
) -> SecurityContract:
    rng = resolve_rng(rng)
    # The mark is a real corp, guarded at a district it actually holds this run —
    # same targeting as jobs.generate_job.
    held = sorted(
        (t for t in corp_map.territories.values() if t.owner in FACTIONS_BY_ID),
        key=lambda t: t.id,
    )
    territory = rng.choice(held)
    faction = FACTIONS_BY_ID[territory.owner]
    location = rng.choice([loc for loc in territory.locations if loc.kind in GENERATED_KINDS])
    tier = day_tier(day, len(DIFFICULTY_BASE))
    nightly_pay = NIGHTLY_PAY_BASE[tier]
    nights_total = rng.randint(*NIGHTS_RANGE)
    completion_bonus = round(nightly_pay * nights_total * COMPLETION_BONUS_FRACTION)

    return SecurityContract(
        id=f"security_{uuid.uuid4().hex[:8]}",
        fixer_id=fixer_id,
        faction_id=faction.id,
        territory_id=territory.id,
        location_id=location.id,
        skill=rng.choice(WATCH_SKILLS),
        difficulty=DIFFICULTY_BASE[tier] + rng.randint(-1, 2),
        nightly_pay=nightly_pay,
        nights_total=nights_total,
        completion_bonus=completion_bonus,
        offered_day=day,
    )


def _shift_standing(character: "Character", faction_id: str, delta: int) -> None:
    """standing_shift's rival fan-out, applied directly (mirrors scene.apply_outcome's
    loop) — there's no Scene here to route the fan-out through."""
    for shifted_faction_id, shifted_delta in standing_shift(faction_id, delta).items():
        character.adjust_standing(shifted_faction_id, shifted_delta)


def resolve_security_night(
    character: "Character", contract: SecurityContract, rng: random.Random | None = None
) -> NightResult:
    """One night's watch on an accepted contract. Called once per end-day while the
    runner is standing in contract.territory_id (the caller owns that check — this
    function assumes presence). Mutates character and contract in place; does not
    remove a blown or completed contract from character.security_contracts — that's
    the caller's call via Character.remove_security_contract."""
    rng = resolve_rng(rng)
    roll = resolve_check(stat_value=skill_value(character, contract.skill), difficulty=contract.difficulty, rng=rng)

    if roll.result is CheckResult.CRITICAL_FAILURE:
        character.adjust_health(-NIGHT_FAILURE_DAMAGE)
        _shift_standing(character, contract.faction_id, BLOWN_STANDING_HIT)
        character.adjust_fixer_trust(contract.fixer_id, BLOWN_FIXER_TRUST_HIT)
        character.adjust_rep(BLOWN_REP_HIT)
        return NightResult(roll=roll, pay=0, bonus=0, blown=True, completed=False)

    # Both FAILURE and a success advance nights_completed and can complete the
    # contract — a contract that fails its way to its last night still ends there
    # (you served the term, even if the last night was rough) rather than demanding
    # extra nights beyond nights_total. Only pay/bonus differ by branch.
    pay = 0
    if roll.result is CheckResult.FAILURE:
        character.adjust_health(-NIGHT_FAILURE_DAMAGE)
    else:
        pay = contract.nightly_pay
        if roll.result is CheckResult.CRITICAL_SUCCESS:
            pay = round(pay * CRITICAL_SUCCESS_PAY_MULT)
        character.cash += pay
    contract.nights_completed += 1

    bonus = 0
    completed = contract.is_complete
    if completed:
        bonus = contract.completion_bonus
        character.cash += bonus
        _shift_standing(character, contract.faction_id, COMPLETION_STANDING_GAIN)
        character.adjust_fixer_trust(contract.fixer_id, COMPLETION_FIXER_TRUST_GAIN)
        character.adjust_rep(COMPLETION_REP_GAIN)

    return NightResult(roll=roll, pay=pay, bonus=bonus, blown=False, completed=completed)
