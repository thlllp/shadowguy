"""Procedural generation of gig Scenes, one per Location, keyed to a local character.

A gig is a single-stage, self-selected activity for quick resources — the street-work
counterpart to jobs.py's fixer-issued contracts. Unlike a job, a gig is *attached to a
place and a person*: it spawns at a Location (corpmap), is owned by one of that
Location's LocalCharacters, and its reward includes standing with that character
(Outcome.local_standing_delta -> Scene.target_character_id). Doing the ripperdoc's chem
trial is how the ripperdoc comes to know you.

Content is generated from per-kind templates below rather than hand-authored per gig:
a DATA hub's gigs read as netrunning, a bar's as social hustle. Each gig offers a
subset of its kind's approaches, so which skills a given gig happens to want is part of
the draw — the same "every build has a way through, but not always the same way" spirit
as jobs, though gigs (being optional) aren't held to the cross-stat rule stages are.

Gigs are stored on the App as dict[location_id, Scene] (see app.ShadowguyApp), not on the
Location: corpmap is a leaf that must not import scene, and gigs live alongside the map
the way a fixer's offers do. refresh_gigs tops up empty slots on day advance; a completed
gig is cleared and a fresh one spawns next rest.
"""

import random
import uuid
from dataclasses import dataclass

from shadowguy.corpmap import CorpMap, LocalCharacter, Location, LocationKind, Territory
from shadowguy.scene import Choice, Outcome, Scene, SceneKind, Stage
from shadowguy.skills import skill_for

# Tier scales gig difficulty and pay with the day, same cadence as jobs (_tier_for_day).
# Kept local rather than imported so gigs.py stands on its own; the two tables are
# guarded against each other at import.
GIG_DIFFICULTY = (11, 13, 15)
GIG_CASH = (80, 110, 150)
if len(GIG_DIFFICULTY) != len(GIG_CASH):
    raise ValueError("GIG_DIFFICULTY and GIG_CASH must cover the same tiers")

# A crit pays ~1.6x and a point of rep; a plain success pays cash and one point of the
# character's standing. Botching costs health, and a critical botch sours the character
# on you (the one place a gig moves standing the wrong way).
GIG_CRIT_MULT = 1.6
GIG_STANDING_GAIN = 1
GIG_FAIL_DAMAGE = -3
GIG_CRIT_FAIL_DAMAGE = -6
# The most approaches one gig can offer; the real count is 1..this, drawn per gig.
GIG_MAX_APPROACHES = 3

_CRIT_SUCCESS_TAG = "They won't forget this."
_CRIT_FAIL_TAG = "Word gets around, and not the good kind."


@dataclass(frozen=True)
class _GigApproach:
    """One way through a gig: a skill to roll and the flavor for landing/blowing it."""

    skill: str
    label: str
    success: str
    failure: str


@dataclass(frozen=True)
class _GigTemplate:
    """The content pool for one LocationKind. Prompts take {who}/{role}/{place}."""

    titles: tuple[str, ...]
    prompts: tuple[str, ...]
    approaches: tuple[_GigApproach, ...]


_GIG_TEMPLATES: dict[LocationKind, _GigTemplate] = {
    LocationKind.DATA: _GigTemplate(
        titles=("Trace Job", "Data Scrub"),
        prompts=(
            "{who}, the {role} at {place}, needs a trace run down before a buyer gets cold feet.",
            "There's a corrupted ledger at {place} and {who} will pay to have it made clean.",
        ),
        approaches=(
            _GigApproach("hack", "Crack it open", "You're through the ICE clean and out again.", "The node bites back and you pull out with nothing."),
            _GigApproach("pattern_seeking", "Read the traffic", "The pattern gives it up before you even touch the system.", "The noise never resolves into anything you can use."),
            _GigApproach("deception", "Social-engineer a login", "One convincing call and the door's open.", "The mark hangs up halfway through and flags the account."),
        ),
    ),
    LocationKind.LAB: _GigTemplate(
        titles=("Chem Trial", "Test Subject"),
        prompts=(
            "{who} the {role} at {place} needs a body to run a batch on, no questions.",
            "{place} is short a volunteer and {who} is holding a fat envelope.",
        ),
        approaches=(
            _GigApproach("resist_poison", "Take the full dose", "Your liver files a complaint. Paid in full.", "You come out shaking and get docked for the mess."),
            _GigApproach("tinkering", "Calibrate the rig instead", "You fix their dosing gear and they pay for the save.", "You fry a sensor and eat the cost of it."),
            _GigApproach("read_face", "Watch for the tell", "You clock the bad batch before it hits you.", "You miss the warning and wear the reaction."),
        ),
    ),
    LocationKind.DEPOT: _GigTemplate(
        titles=("Load-Out", "Off the Books"),
        prompts=(
            "{who}, {role} at {place}, needs a crate moved before the manifest updates.",
            "A shipment at {place} has to walk, and {who} can't be seen doing it.",
        ),
        approaches=(
            _GigApproach("lift", "Haul it yourself", "Dead weight, moved. {who} counts out the fee.", "You blow out something in your back doing it."),
            _GigApproach("stealth", "Slip it past the cams", "Gone before the next camera sweep.", "A sweep catches you and you drop the crate running."),
            _GigApproach("negotiations", "Talk the dockhands quiet", "A cut buys their blindness. Clean.", "They hold out for more and it turns ugly."),
        ),
    ),
    LocationKind.SOCIAL: _GigTemplate(
        titles=("Work a Mark", "House Favor"),
        prompts=(
            "{who} the {role} at {place} points you at a mark who's had too much to drink.",
            "There's a read to be made across the bar at {place}, and {who} is buying the intel.",
        ),
        approaches=(
            _GigApproach("read_face", "Read them cold", "You know their whole hand before they fold it.", "You misread the room and they clam up."),
            _GigApproach("deception", "Run a story on them", "They buy every word and pay for the privilege.", "The story falls apart and so does the tip."),
            _GigApproach("sleight_of_hand", "Lift what they won't miss", "In and out, they never feel it.", "A hand closes on your wrist mid-lift."),
        ),
    ),
    LocationKind.PAWN: _GigTemplate(
        titles=("Appraisal", "Fenced Goods"),
        prompts=(
            "{who}, the {role} at {place}, needs a piece appraised before a seller lies about it.",
            "A lot came into {place} hot, and {who} wants it valued quiet.",
        ),
        approaches=(
            _GigApproach("negotiations", "Haggle the seller down", "You shave the price to nothing and take a cut.", "They walk, and {who} isn't pleased."),
            _GigApproach("read_face", "Spot the fake", "You call the forgery on sight.", "You vouch for a fake and it costs you."),
            _GigApproach("infer", "Trace the serials", "The numbers tell you exactly what it's worth.", "The trail goes cold before you can price it."),
        ),
    ),
    LocationKind.WEAPON_SHOP: _GigTemplate(
        titles=("Range Test", "Proof Work"),
        prompts=(
            "{who} the {role} at {place} needs a new piece proofed before it goes on the wall.",
            "A shipment of iron at {place} needs someone who can tell junk from gold.",
        ),
        approaches=(
            _GigApproach("firearms", "Run it on the range", "Tight groups all day. {who} marks it sold.", "It jams on you and the test's a wash."),
            _GigApproach("tinkering", "Strip and inspect it", "You find the flaw they'd have missed.", "You reassemble it wrong and eat the part."),
            _GigApproach("intimidation", "Lean on the supplier", "One look and they knock the price down.", "They call your bluff and it sours."),
        ),
    ),
    LocationKind.AUTO_DEALER: _GigTemplate(
        titles=("Test Drive", "Lot Work"),
        prompts=(
            "{who}, {role} at {place}, needs a repo checked over before it moves again.",
            "There's a rig at {place} with a story, and {who} wants it read.",
        ),
        approaches=(
            _GigApproach("tinkering", "Get under the hood", "You sort the fault and {who} pays for the save.", "You miss the fault and it seizes on the lot."),
            _GigApproach("deception", "Move it as clean", "The buyer never asks the right question.", "The buyer asks the right question."),
            _GigApproach("negotiations", "Close the sale", "You talk them into the upsell.", "They walk before the ink dries."),
        ),
    ),
    LocationKind.PHARMACY: _GigTemplate(
        titles=("Bad Batch", "Counter Work"),
        prompts=(
            "{who} the {role} at {place} needs a tainted lot sorted from the good.",
            "A customer at {place} is faking a script and {who} wants them made.",
        ),
        approaches=(
            _GigApproach("resist_disease", "Test it on yourself", "Your blood eats it and {who} pays for the proof.", "You run a fever for two days over it."),
            _GigApproach("infer", "Read the chem tags", "The labels give the bad lot up.", "The tags don't add up and you guess wrong."),
            _GigApproach("deception", "Draw out the faker", "You get the fake script in hand.", "They spook and bolt before you're sure."),
        ),
    ),
    LocationKind.COMPUTER_STORE: _GigTemplate(
        titles=("Bench Job", "Rig Repair"),
        prompts=(
            "{who}, the {role} at {place}, has a dead rig and a customer breathing down their neck.",
            "A bricked deck came into {place} and {who} needs it talking again.",
        ),
        approaches=(
            _GigApproach("hack", "Recover the drive", "You pull the data back from the dead.", "The drive stays dead and takes the fee with it."),
            _GigApproach("tinkering", "Reflow the board", "Clean solder, and it boots first try.", "You cook a trace and make it worse."),
            _GigApproach("sleight_of_hand", "Swap the failed part", "Quick hands, part swapped, done.", "You strip the socket doing it."),
        ),
    ),
}

if set(_GIG_TEMPLATES) != set(LocationKind):
    raise ValueError("_GIG_TEMPLATES must have exactly one entry per LocationKind")
for _template in _GIG_TEMPLATES.values():
    if not _template.approaches:
        raise ValueError("a gig template has no approaches")
    for _approach in _template.approaches:
        skill_for(_approach.skill)  # unknown skill id: fail at import, not mid-gig


def _gig_tier(day: int) -> int:
    return min(len(GIG_CASH) - 1, (day - 1) // 3)


def _build_choice(approach: _GigApproach, difficulty: int, cash: int) -> Choice:
    skill = skill_for(approach.skill)
    return Choice(
        label=f"{approach.label} ({skill.name})",
        skill=approach.skill,
        difficulty=difficulty,
        success=Outcome(
            text=approach.success,
            cash_delta=cash,
            local_standing_delta=GIG_STANDING_GAIN,
        ),
        failure=Outcome(text=approach.failure, health_delta=GIG_FAIL_DAMAGE),
        critical_success=Outcome(
            text=f"{approach.success} {_CRIT_SUCCESS_TAG}",
            cash_delta=int(cash * GIG_CRIT_MULT),
            rep_delta=1,
            local_standing_delta=GIG_STANDING_GAIN + 1,
        ),
        critical_failure=Outcome(
            text=f"{approach.failure} {_CRIT_FAIL_TAG}",
            health_delta=GIG_CRIT_FAIL_DAMAGE,
            local_standing_delta=-1,
        ),
    )


def generate_gig(
    day: int,
    location: Location,
    character: LocalCharacter,
    territory: Territory,
    rng: random.Random | None = None,
) -> Scene:
    """A single-stage gig at `location`, owned by `character`, whose reward moves that
    character's standing. Offers a random 1..GIG_MAX_APPROACHES subset of the kind's
    approaches, so two gigs of the same kind rarely want the same skill."""
    rng = rng or random.Random()
    template = _GIG_TEMPLATES[location.kind]
    tier = _gig_tier(day)
    difficulty = GIG_DIFFICULTY[tier]
    cash = GIG_CASH[tier]

    title = rng.choice(template.titles)
    prompt = rng.choice(template.prompts).format(
        who=character.name, role=character.role, place=location.name
    )
    count = rng.randint(1, min(GIG_MAX_APPROACHES, len(template.approaches)))
    approaches = rng.sample(template.approaches, count)
    # Approach flavor can name the character ({who}); the prompt already did the
    # placeholders, but a success/failure line might reference them too.
    choices = [
        _build_choice(
            _GigApproach(
                skill=a.skill,
                label=a.label,
                success=a.success.format(who=character.name, role=character.role, place=location.name),
                failure=a.failure.format(who=character.name, role=character.role, place=location.name),
            ),
            difficulty,
            cash,
        )
        for a in approaches
    ]

    return Scene(
        id=f"gig_{uuid.uuid4().hex[:8]}",
        title=title,
        kind=SceneKind.GIG,
        start_stage="start",
        stages={"start": Stage(id="start", prompt=prompt, choices=choices)},
        target_territory_id=territory.id,
        target_location_id=location.id,
        target_character_id=character.id,
    )


def refresh_gigs(
    corp_map: CorpMap, gigs: dict[str, Scene], day: int, rng: random.Random | None = None
) -> None:
    """Fill every location that has characters but no live gig with a fresh one, owned by
    a random one of its characters. Idempotent: a location that already has a gig is left
    alone, so this can run every day advance without churning existing offers."""
    rng = rng or random.Random()
    for territory in corp_map.territories.values():
        for location in territory.locations:
            if location.id in gigs or not location.characters:
                continue
            character = rng.choice(location.characters)
            gigs[location.id] = generate_gig(day, location, character, territory, rng)
