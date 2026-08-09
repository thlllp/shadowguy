"""Preset builds for fast character creation.

An Archetype is just a canned allocation of the same 6 stat points and 20 skill
points the player would otherwise spend by hand on CharacterCreationScreen.
`apply()` spends them through Character.spend_stat_point/spend_skill_point rather
than assigning fields, so a preset is subject to the rank cap and the rank-cost
curve exactly like a hand-built runner — it cannot buy something the player
couldn't. Validation is deferred to first access of ARCHETYPES / ARCHETYPES_BY_ID,
so an unaffordable preset still fails early (the creation screen is the first thing
the game uses) but importing the module alone doesn't construct a Character.

(Not to be confused with jobs.JobArchetype, which is a template for a *job*.)
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shadowguy.character import Character


@dataclass(frozen=True)
class Archetype:
    id: str
    name: str
    description: str
    stats: dict[str, int]
    skills: dict[str, int]
    # shops item ids this build walks in carrying, bought with the skill points the rank
    # list deliberately leaves unspent (character.convert_skill_point_to_gear -- two
    # points at today's GEAR_EB_PER_POINT for most rows, three for anything pricier).
    # A preset is the *whole* build, so it picks the kit too rather than leaving the
    # player at a shop screen with a pool they may not know they have. Change a price
    # and the rank list that ships it has to move with it: _validate_preset fails the
    # moment a loadout costs more points than its ranks left over, which is the guard
    # that keeps the two in step.
    gear: tuple[str, ...] = ()
    # cybernetics.Cyberware ids installed at creation, drawn from the same gear_budget
    # as `gear` above (Character.buy_creation_cyberware) rather than cash -- only
    # Deltaware/Trashware (min_standing 0) are ever reachable here, same reason `gear`
    # never names a standing-gated Item.
    cyberware: tuple[str, ...] = ()
    # shops.Program ids bought (Character.buy_creation_program, same gear_budget swap)
    # and installed onto the first deck `gear` carries (inventory.install_program,
    # free once owned). Only meaningful for a preset whose gear includes a deck
    # (program_slots > 0) -- see the Hacker row, the only one that names any today.
    programs: tuple[str, ...] = ()

    def apply(self, character: "Character") -> None:
        from shadowguy.character import GEAR_EB_PER_POINT
        from shadowguy.cybernetics import CYBERWARE_BY_ID
        from shadowguy.inventory import install_program
        from shadowguy.shops import ITEMS_BY_ID, PROGRAMS_BY_ID

        for stat, points in self.stats.items():
            for _ in range(points):
                if not character.spend_stat_point(stat):
                    raise ValueError(f"{self.id}: ran out of stat points buying {stat}")
        for skill_id, target_rank in self.skills.items():
            while character.skill_rank(skill_id) < target_rank:
                if not character.spend_skill_point(skill_id):
                    raise ValueError(f"{self.id}: cannot afford {skill_id} rank {target_rank}")
        # Gear (and cyberware, and programs) last: the rank list above leaves exactly
        # the points this needs, so converting first would starve it. Buy in the order
        # written -- a preset that can't afford its own list is a bad row, and
        # _validate_preset says so.
        bill = (
            sum(ITEMS_BY_ID[item_id].price for item_id in self.gear)
            + sum(CYBERWARE_BY_ID[cyberware_id].price for cyberware_id in self.cyberware)
            + sum(PROGRAMS_BY_ID[program_id].price for program_id in self.programs)
        )
        while character.gear_budget < bill:
            if not character.convert_skill_point_to_gear():
                raise ValueError(
                    f"{self.id}: needs {bill}eb of gear but ran out of skill points to "
                    f"convert (one point is {GEAR_EB_PER_POINT}eb)"
                )
        for item_id in self.gear:
            if not character.buy_creation_gear(ITEMS_BY_ID[item_id]):
                raise ValueError(f"{self.id}: cannot buy {item_id}")
        for cyberware_id in self.cyberware:
            if not character.buy_creation_cyberware(cyberware_id):
                raise ValueError(f"{self.id}: cannot install {cyberware_id}")
        if self.programs:
            deck_index = next(
                (
                    i
                    for i, entry in enumerate(character.inventory)
                    if ITEMS_BY_ID[entry.item_id].program_slots > 0
                ),
                None,
            )
            if deck_index is None:
                raise ValueError(f"{self.id}: names programs but gear carries no deck")
            for program_id in self.programs:
                if not character.buy_creation_program(program_id):
                    raise ValueError(f"{self.id}: cannot buy {program_id}")
                message = install_program(character, deck_index, program_id)
                if not message.startswith("Installed "):
                    raise ValueError(f"{self.id}: cannot install {program_id} ({message})")


# id, name, description, stats, skills (id -> target rank)
#
# Every row spends exactly STARTING_STAT_POINTS (6) and STARTING_SKILL_POINTS (20) --
# _validate_preset enforces it, so a row that doesn't add up fails at first access
# rather than shipping a preset the player couldn't have built. Reaching rank R from
# STARTING_SKILL_RANK costs 1/2/3/5/7/9/12/15/19 points for R = 2..10 (the running sum
# of character.SKILL_RANK_COST), which is what makes a rank-7 signature skill cost
# nearly half the pool.
#
# Each preset names *one* thing it is good at and pays for it, rather than hedging --
# that narrowness is what makes an archetype bleed on job stages it doesn't suit. The
# Hacker is the deliberate exception: a matrix fight now rolls five different skills
# (see matrix.py's skill constants), so covering its own arena costs it breadth
# everywhere else.
_ARCHETYPE_ROWS = (
    (
        "enforcer",
        "Enforcer",
        "Muscle. Hits hard, soaks hits, and is no good at all at casing a place.",
        {"body": 3, "strength": 3},
        # Strength is bought for the damage, not the skill list: it adds to every melee
        # hit (combat.melee_damage_bonus), so a club in this build swings for its rating
        # plus 4 before the roll's margin. Clubs sits one rank lower than a pure skill
        # build would buy: the cyberarm below now eats the point instead.
        {"clubs": 6, "toughness": 6, "grapple": 3, "intimidation": 1},
        # Walks in wearing the heaviest armor the ungated catalog sells: this build wins
        # by still standing, and Body already feeds the soak roll the Hardsuit adds to.
        ("brass_knuckles", "combat_knife", "hardsuit", "reinforced_helmet", "steel_toe_boots"),
        # A Deltaware Grapple Rig Cyberarm: +2 Grapple stacked straight onto the skill
        # this build already carries, same "one signature stat, chrome doubles down on
        # it" shape the Street Samurai's cyberarm uses.
        ("grapple_rig_cyberarm",),
        (),
    ),
    (
        "hacker",
        "Hacker",
        "Breaks systems. Owns the wired half of the board, weak the moment it turns physical.",
        # Every point on logic: all five skills below sit on it, so the 2 points this
        # used to put in perception bought nothing the build ever rolled once
        # pattern_seeking gave way to the matrix skills.
        {"logic": 6},
        # One skill per matrix roll: Cybercombat fights the ICE, Hack gets in and runs
        # Sleaze, Computer pulls the file, Infer is both the Analyze action and the
        # firewall (matrix.firewall_defense), Tinkering is Harden. Cybercombat leads
        # because that is what a Data Heist's fights actually roll -- and it clears
        # MIN_READY_CYBERCOMBAT, so this preset never trips the readiness warning.
        # Infer sits one rank lower than the read above would otherwise buy: the
        # Zetatech Rig's price is the point of the build (see gear below), and it now
        # eats a third skill point rather than two, so this is the rank that gives up
        # the ground. Hack gives up a second rank on top of that -- to the Datajack
        # below and the two programs riding in the Rig's slots.
        {"cybercombat": 6, "hack": 4, "computer": 4, "infer": 2, "tinkering": 2},
        # The deck *is* the build -- a hacker with no cyberdeck cannot enter the matrix
        # at all, and the Zetatech Rig's 3 program slots are the most the ungated catalog
        # offers. It's also the priciest single item in the ungated catalog, which is
        # deliberate: a top-tier deck is meant to cost this build a real rank, not pocket
        # change. The pistol and jacket are so the walk to the job isn't fatal.
        ("zetatech_rig", "pipe_pistol", "leather_jacket", "kevlar_helmet"),
        # A Deltaware Datajack: +1 to every matrix action (cybernetics.Cyberware.
        # matrix_action_bonus), unconditional rather than tied to one roll the way the
        # skills above are -- the closest thing to a stat bump this build's whole arena
        # gets.
        ("datajack",),
        # Two of the Rig's three slots filled at creation, so this build walks into its
        # first matrix fight with real programs rather than an empty deck: Icebreaker
        # for guaranteed chip damage against the ICE (matrix.py rolls no skill for a
        # program's own action_damage), Sleaze as a second way past it that doesn't cost
        # a Cybercombat roll.
        ("icebreaker", "sleaze"),
    ),
    (
        "infiltrator",
        "Infiltrator",
        "Gets in unseen and talks their way out. Broad, but tops out lower than a specialist.",
        {"agility": 4, "perception": 2},
        # Blades rather than a gun: this build's whole premise is not being heard, and
        # a blade is the weapon it can carry concealed. Sight stays because Recon --
        # the Infiltrator's own job archetype -- leads every beat with a perception
        # skill, though it gives up a rank to the cybereye below.
        {"stealth": 7, "deception": 5, "sight": 2, "blades": 3},
        # Blades and nothing that bangs: Slippers carry a Stealth bonus of their own
        # (shops.Item.skill_bonuses), and light armor because being seen is the failure
        # state, not being shot.
        ("monoblade", "combat_knife", "leather_jacket", "slippers"),
        # A Deltaware Cybereye Scanner: +1 Perception stacked onto the same stat Sight
        # already rolls, so the rank Sight gave up above isn't a net loss.
        ("cybereye_scanner",),
        (),
    ),
    (
        "gunslinger",
        "Gunslinger",
        "Wins the fight at the far end of the room. Lethal at range, ordinary everywhere else.",
        {"agility": 3, "body": 3},
        # The counterpart to the Enforcer now that weapons are split by category: the
        # rifle is the build, Dodge and Toughness are what keep it standing while it
        # kites. Pistols is the *early game*, not an afterthought -- a runner starts on
        # STARTING_CASH (100) with an empty inventory, and the cheapest longarm is the
        # 650eb Pump Shotgun. Gear points are the fix: this preset now spends one of
        # them on the shotgun, so its signature skill has a weapon to roll from day one
        # rather than after a job or two of shooting a Pipe Pistol. Pistols stays as the
        # sidearm the second weapon slot carries. Longarms gives up a rank to the
        # reflex coprocessor below.
        {"longarms": 6, "dodge": 5, "toughness": 3, "pistols": 3},
        ("pump_shotgun", "pipe_pistol", "kevlar_vest", "reinforced_helmet", "steel_toe_boots"),
        # A Deltaware Reflex Coprocessor: +1 Agility feeds both Dodge and the ranged
        # to-hit roll, the same "one signature stat, chrome doubles down" shape the
        # Street Samurai's cyberarm uses.
        ("reflex_coprocessor",),
        (),
    ),
    (
        "street_samurai",
        "Street Samurai",
        "Blade-first mercenary muscle. Closes distance fast and hits harder than a duelist"
        " has any right to, but has nothing to fall back on at range.",
        # Strength is bought for melee_damage_bonus, same reasoning as the Enforcer, but
        # paired with Agility instead of Body: this build wins by not being where the hit
        # lands, not by soaking it.
        {"agility": 4, "strength": 2},
        # Blades is the signature; Dodge and Acrobatics are the agility half of "closes
        # distance and doesn't get hit doing it." Grapple is the one strength-tied skill
        # on the sheet -- pinning a mark once the blade's already in -- which is what
        # keeps the raised Strength from going idle (_validate_preset). It sits one rank
        # lower than a pure skill build would buy: the cyberarm below (Deltaware, min
        # standing 0 -- see Archetype.cyberware) now eats the point instead, and its own
        # +2 Strength partly covers what the rank gave up.
        {"blades": 7, "dodge": 5, "acrobatics": 3, "grapple": 2},
        # The same Monoblade the Infiltrator carries, but built to actually swing it: this
        # preset's Strength and rank-7 Blades turn the same weapon into a very different
        # fight. Combat Knife rides stowed as the backup once the Monoblade eats both
        # weapon slots (two_handed) -- light armor throughout, since the build's answer to
        # getting hit is Dodge, not soak.
        ("monoblade", "combat_knife", "leather_jacket", "reinforced_helmet", "steel_toe_boots"),
        # A Deltaware Hydraulic Cyberarm: +2 Strength stacked straight onto
        # melee_damage_bonus, which is what "hits harder than a duelist has any right
        # to" means mechanically. Deltaware over Trashware on purpose -- min_standing 0
        # either way, but Trashware's doubled humanity_cost (5.0 of the HUMANITY_BASELINE
        # 6) would leave this build one bad ripperdoc visit from cyberpsychosis before
        # its first job.
        ("hydraulic_cyberarm",),
        (),
    ),
    (
        "fixer",
        "Fixer",
        "Runs people, not jobs. Hires cheap, deals well, and loses any fight they can't talk out of.",
        {"cool": 4, "logic": 2},
        # The one preset built on cool, which no other archetype touches. Leadership is
        # the unusual buy: it is *read* rather than rolled (runners.recruit_wage/
        # recruit_cut), so it discounts every hire's wage and job cut for the whole run
        # -- this build fields a crew the others can't afford. Computer is what those 2
        # logic points are for: digging up information is the other half of the job, and
        # a stat this preset buys should be a stat it actually rolls. Deception gives up
        # a rank to the adrenal gland below.
        {"negotiations": 7, "leadership": 5, "deception": 2, "computer": 3},
        # The car is the buy nobody else makes: a Slot.VEHICLE item cuts TRAVEL_HOURS_COST
        # on every hop (shops.Item.travel_reduction), and this is the build that spends
        # its run moving between people rather than shooting them.
        ("armored_towncar", "pipe_pistol", "leather_jacket", "pawned_charm"),
        # A Deltaware Synthetic Adrenal Gland: +1 Cool, the one stat this whole build
        # is spent on and no other preset touches.
        ("synthetic_adrenal_gland",),
        (),
    ),
)

# Lazy init state: filled on first access by __getattr__ below.
_ARCHETYPES: list[Archetype] | None = None
_ARCHETYPES_BY_ID: dict[str, Archetype] | None = None


def _validate_preset(archetype: Archetype) -> None:
    from shadowguy.character import Character
    from shadowguy.skills import skill_for
    character = Character(name="_check")
    archetype.apply(character)
    if character.stat_points or character.skill_points:
        from shadowguy.character import STARTING_SKILL_POINTS, STARTING_STAT_POINTS
        raise ValueError(
            f"{archetype.id}: leaves {character.stat_points} stat / "
            f"{character.skill_points} skill points unspent; presets must spend "
            f"all {STARTING_STAT_POINTS} and {STARTING_SKILL_POINTS}"
        )
    # Spending the pools isn't enough: the two halves have to describe the same build.
    # A stat raised for skills the preset doesn't buy is silently wasted -- nothing
    # rolls a core stat directly (see skills.skill_value), so those points do nothing
    # at all. Both the Hacker (perception, after pattern_seeking gave way to the matrix
    # skills) and the Fixer (logic) shipped that way before this check existed.
    rolled_stats = {skill_for(skill_id).stat for skill_id in archetype.skills}
    idle = sorted(set(archetype.stats) - rolled_stats)
    if idle:
        raise ValueError(
            f"{archetype.id}: raises {', '.join(idle)} but buys no skill layered on "
            f"{'it' if len(idle) == 1 else 'them'}; those points can never be rolled"
        )


def _init() -> None:
    if _ARCHETYPES is not None:
        return
    import sys
    archetypes_list = [
        Archetype(
            id=id_,
            name=name,
            description=description,
            stats=stats,
            skills=skills,
            gear=gear,
            cyberware=cyberware,
            programs=programs,
        )
        for id_, name, description, stats, skills, gear, cyberware, programs in _ARCHETYPE_ROWS
    ]
    for archetype in archetypes_list:
        _validate_preset(archetype)
    # Use sys.modules to avoid the global statement with linting friction.
    mod = sys.modules[__name__]
    mod._ARCHETYPES = archetypes_list
    mod._ARCHETYPES_BY_ID = {archetype.id: archetype for archetype in archetypes_list}


def __getattr__(name: str):
    if name == "ARCHETYPES":
        _init()
        return _ARCHETYPES
    if name == "ARCHETYPES_BY_ID":
        _init()
        return _ARCHETYPES_BY_ID
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
