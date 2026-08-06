"""Tests for cybernetics.py: the cyberware catalog, install/remove, and its
Character.stat()/skill_value wiring (character.py folds installed_bonus/
installed_skill_bonus in alongside worn gear -- see the module docstring)."""

import pytest

from shadowguy.character import (
    HUMANITY_BASELINE,
    HUMANITY_PENALTY_THRESHOLDS,
    SURGERY_SCARRING,
    Character,
)
from shadowguy.cybernetics import (
    CYBERWARE_BY_ID,
    CYBERWARE_CATALOG,
    CYBERWARE_TIER_IDS,
    CYBERWARE_TIER_MIN_STANDING,
    SMARTLINK_ID,
    CyberSlot,
    _DELTAWARE_CYBERWARE,
    catalog_for_standing,
    free_humanity,
    has_smartlink,
    install_cyberware,
    installed_bonus,
    lost_to_cyberpsychosis,
    installed_defense,
    installed_humanity_cost,
    installed_matrix_action_bonus,
    installed_skill_bonus,
    remove_cyberware,
)
from shadowguy.skills import skill_value


def _first_for_slot(slot: CyberSlot, *, with_skill_bonus: bool = False):
    for cyberware in CYBERWARE_CATALOG:
        if cyberware.slot is not slot:
            continue
        if with_skill_bonus and not cyberware.skill_bonuses:
            continue
        if not with_skill_bonus and not cyberware.bonuses:
            continue
        return cyberware
    raise AssertionError(f"no catalog entry for {slot} matching with_skill_bonus={with_skill_bonus}")


def test_every_cyberslot_has_a_catalog_entry():
    assert {cyberware.slot for cyberware in CYBERWARE_CATALOG} == set(CyberSlot)


def test_install_cyberware_succeeds_and_charges_cash():
    cyberware = _first_for_slot(CyberSlot.OPTICS)
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, cyberware.id) is True
    assert character.cash == 10_000 - cyberware.price
    assert character.installed_cyberware[cyberware.slot] == cyberware.id


def test_install_cyberware_fails_when_unaffordable():
    cyberware = _first_for_slot(CyberSlot.OPTICS)
    character = Character(name="t", cash=0)
    assert install_cyberware(character, cyberware.id) is False
    assert character.installed_cyberware == {}


def test_install_cyberware_fails_when_slot_already_occupied():
    first = _first_for_slot(CyberSlot.NEURALWARE)
    second = next(c for c in CYBERWARE_CATALOG if c.slot is CyberSlot.NEURALWARE and c.id != first.id)
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, first.id) is True
    cash_after_first = character.cash
    assert install_cyberware(character, second.id) is False
    assert character.cash == cash_after_first
    assert character.installed_cyberware[CyberSlot.NEURALWARE] == first.id


def test_remove_cyberware_frees_the_slot_for_a_swap():
    first = _first_for_slot(CyberSlot.ARMS)
    second = next(c for c in CYBERWARE_CATALOG if c.slot is CyberSlot.ARMS and c.id != first.id)
    character = Character(name="t", cash=10_000)
    install_cyberware(character, first.id)
    assert remove_cyberware(character, CyberSlot.ARMS) == first.id
    assert CyberSlot.ARMS not in character.installed_cyberware
    assert install_cyberware(character, second.id) is True
    assert character.installed_cyberware[CyberSlot.ARMS] == second.id


def test_remove_cyberware_on_an_empty_slot_returns_none():
    character = Character(name="t")
    assert remove_cyberware(character, CyberSlot.INTERNAL) is None


def test_installed_bonus_sums_across_installed_slots():
    optics = _first_for_slot(CyberSlot.OPTICS)
    internal = _first_for_slot(CyberSlot.INTERNAL)
    stat = next(iter(optics.bonuses))
    installed = {CyberSlot.OPTICS: optics.id, CyberSlot.INTERNAL: internal.id}
    expected = optics.bonuses.get(stat, 0) + internal.bonuses.get(stat, 0)
    assert installed_bonus(installed, stat) == expected


def test_installed_bonus_is_zero_with_nothing_installed():
    assert installed_bonus({}, "body") == 0


def test_installed_skill_bonus_reads_the_right_piece():
    cyberware = _first_for_slot(CyberSlot.ARMS, with_skill_bonus=True)
    skill_id = next(iter(cyberware.skill_bonuses))
    installed = {CyberSlot.OPTICS: cyberware.id}
    assert installed_skill_bonus(installed, skill_id) == cyberware.skill_bonuses[skill_id]


def test_character_stat_folds_in_installed_cyberware_bonus():
    cyberware = _first_for_slot(CyberSlot.INTERNAL)
    stat = next(iter(cyberware.bonuses))
    character = Character(name="t", cash=10_000)
    before = character.stat(stat)
    install_cyberware(character, cyberware.id)
    assert character.stat(stat) == before + cyberware.bonuses[stat]


def test_character_skill_value_folds_in_installed_cyberware_skill_bonus():
    cyberware = _first_for_slot(CyberSlot.ARMS, with_skill_bonus=True)
    skill_id = next(iter(cyberware.skill_bonuses))
    character = Character(name="t", cash=10_000)
    before = skill_value(character, skill_id)
    install_cyberware(character, cyberware.id)
    assert skill_value(character, skill_id) == before + cyberware.skill_bonuses[skill_id]


def test_cyberware_ids_are_unique():
    assert len(CYBERWARE_BY_ID) == len(CYBERWARE_CATALOG)


def test_free_humanity_starts_at_the_baseline_with_nothing_installed():
    character = Character(name="t")
    assert character.humanity == HUMANITY_BASELINE
    assert free_humanity(character) == HUMANITY_BASELINE


def test_installing_cyberware_spends_free_humanity():
    cyberware = _first_for_slot(CyberSlot.OPTICS)
    character = Character(name="t", cash=10_000)
    install_cyberware(character, cyberware.id)
    assert installed_humanity_cost(character.installed_cyberware) == cyberware.humanity_cost
    # The implant's cost *and* the operation's permanent scar.
    assert free_humanity(character) == HUMANITY_BASELINE - cyberware.humanity_cost - SURGERY_SCARRING


def test_install_cyberware_fails_when_it_would_exceed_humanity_capacity():
    character = Character(name="t", cash=100_000, humanity=1)
    expensive = next(c for c in CYBERWARE_CATALOG if c.humanity_cost > 1)
    assert install_cyberware(character, expensive.id) is False
    assert character.installed_cyberware == {}
    assert character.cash == 100_000


def test_install_cyberware_succeeds_exactly_at_remaining_capacity():
    """Capacity is the gate, so a piece costing exactly what's left still goes in --
    but the operation's own scar then tips free Humanity under 0. That is the
    cyberpsychosis cliff, not a rejected install (see the dedicated test below)."""
    cyberware = _first_for_slot(CyberSlot.INTERNAL)
    character = Character(name="t", cash=10_000, humanity=cyberware.humanity_cost)
    assert install_cyberware(character, cyberware.id) is True
    assert free_humanity(character) == -SURGERY_SCARRING


def test_removing_cyberware_frees_its_humanity_cost():
    cyberware = _first_for_slot(CyberSlot.ARMS)
    character = Character(name="t", cash=10_000)
    install_cyberware(character, cyberware.id)
    remove_cyberware(character, CyberSlot.ARMS)
    # The implant's cost all comes back; the two operations' scarring does not.
    assert free_humanity(character) == HUMANITY_BASELINE - 2 * SURGERY_SCARRING


def test_smartlink_costs_half_a_point_of_humanity():
    assert CYBERWARE_BY_ID[SMARTLINK_ID].humanity_cost == 0.5


def test_installing_smartlink_leaves_a_fractional_remainder():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, SMARTLINK_ID)
    assert free_humanity(character) == HUMANITY_BASELINE - 0.5 - SURGERY_SCARRING


def test_has_smartlink_false_with_nothing_installed():
    assert has_smartlink({}) is False


def test_has_smartlink_false_with_a_different_optics_piece():
    assert has_smartlink({CyberSlot.OPTICS: "cybereye_scanner"}) is False


def test_has_smartlink_true_once_installed():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, SMARTLINK_ID)
    assert has_smartlink(character.installed_cyberware) is True


# --- tiers ---


def test_every_catalog_entry_has_a_valid_tier():
    assert {c.tier for c in CYBERWARE_CATALOG} <= set(CYBERWARE_TIER_IDS)


def test_every_deltaware_piece_has_a_trashware_betaware_and_alphaware_variant():
    deltaware = [c for c in CYBERWARE_CATALOG if c.tier == "deltaware"]
    for base in deltaware:
        for tier in ("trashware", "betaware", "alphaware"):
            assert f"{base.id}_{tier}" in CYBERWARE_BY_ID


def test_higher_tier_keeps_the_same_effect_as_deltaware():
    base = CYBERWARE_BY_ID["reflex_coprocessor"]
    for tier in ("trashware", "betaware", "alphaware"):
        variant = CYBERWARE_BY_ID[f"{base.id}_{tier}"]
        assert variant.slot is base.slot
        assert variant.bonuses == base.bonuses
        assert variant.skill_bonuses == base.skill_bonuses
        assert variant.tier == tier


@pytest.mark.parametrize(
    "tier,price_multiplier,humanity_multiplier",
    [("trashware", 0.5, 2.0), ("betaware", 1.2, 0.8), ("alphaware", 2.0, 0.5)],
)
def test_tier_price_and_humanity_multipliers(tier, price_multiplier, humanity_multiplier):
    base = CYBERWARE_BY_ID["neural_processor"]
    variant = CYBERWARE_BY_ID[f"neural_processor_{tier}"]
    assert variant.price == round(base.price * price_multiplier)
    assert variant.humanity_cost == round(base.humanity_cost * humanity_multiplier, 2)


def test_every_tier_smartlink_still_grants_smartlink():
    for tier in ("trashware", "betaware", "alphaware"):
        assert CYBERWARE_BY_ID[f"smartlink_{tier}"].grants_smartlink is True


def test_has_smartlink_true_for_a_higher_tier_smartlink():
    character = Character(name="t", cash=10_000)
    install_cyberware(character, "smartlink_trashware")
    assert has_smartlink(character.installed_cyberware) is True


def test_install_cyberware_works_with_a_trashware_id():
    character = Character(name="t", cash=10_000)
    variant = CYBERWARE_BY_ID["cybereye_scanner_trashware"]
    assert install_cyberware(character, variant.id) is True
    assert character.cash == 10_000 - variant.price
    assert character.installed_cyberware[CyberSlot.OPTICS] == variant.id


# --- bone lacing (defense) ---


def test_bone_lacing_catalog_values():
    steel = CYBERWARE_BY_ID["steel_bones"]
    titanium = CYBERWARE_BY_ID["titanium_bones"]
    adamantium = CYBERWARE_BY_ID["adamantium_bones"]
    assert (steel.price, steel.defense, steel.humanity_cost) == (1000, 1, 1)
    assert (titanium.price, titanium.defense, titanium.humanity_cost) == (3000, 2, 2)
    # 2.8, not 3.5: Trashware's 2x would put a humanity_cost of 3.5 at 7.0, past
    # HUMANITY_BASELINE, making adamantium_bones_trashware permanently uninstallable.
    assert (adamantium.price, adamantium.defense, adamantium.humanity_cost) == (6000, 4, 2.8)
    assert steel.slot is CyberSlot.INTERNAL
    assert titanium.slot is CyberSlot.INTERNAL
    assert adamantium.slot is CyberSlot.INTERNAL


def test_bone_lacing_gets_generated_tier_variants_too():
    for base_id in ("steel_bones", "titanium_bones", "adamantium_bones"):
        for tier in ("trashware", "betaware", "alphaware"):
            variant = CYBERWARE_BY_ID[f"{base_id}_{tier}"]
            assert variant.defense == CYBERWARE_BY_ID[base_id].defense
            assert variant.tier == tier


def test_installed_defense_sums_across_installed_slots():
    assert installed_defense({CyberSlot.INTERNAL: "titanium_bones"}) == 2


def test_installed_defense_is_zero_with_nothing_installed():
    assert installed_defense({}) == 0


def test_installing_bone_lacing_competes_with_other_internal_pieces():
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, "subdermal_plating") is True
    assert install_cyberware(character, "steel_bones") is False
    assert character.installed_cyberware[CyberSlot.INTERNAL] == "subdermal_plating"


# --- datajack ---


def test_datajack_catalog_values():
    datajack = CYBERWARE_BY_ID["datajack"]
    assert (datajack.price, datajack.humanity_cost) == (1000, 0.5)
    assert datajack.slot is CyberSlot.NEURALWARE


def test_datajack_has_no_stat_or_skill_bonus_or_defense():
    datajack = CYBERWARE_BY_ID["datajack"]
    assert datajack.bonuses == {}
    assert datajack.skill_bonuses == {}
    assert datajack.defense == 0
    assert datajack.grants_smartlink is False


def test_datajack_grants_a_small_matrix_action_bonus():
    assert CYBERWARE_BY_ID["datajack"].matrix_action_bonus == 1


def test_installed_matrix_action_bonus_sums_across_installed_slots():
    assert installed_matrix_action_bonus({CyberSlot.NEURALWARE: "datajack"}) == 1


def test_installed_matrix_action_bonus_is_zero_with_nothing_installed():
    assert installed_matrix_action_bonus({}) == 0


def test_installed_matrix_action_bonus_zero_for_a_different_neuralware_piece():
    assert installed_matrix_action_bonus({CyberSlot.NEURALWARE: "neural_processor"}) == 0


def test_install_datajack_succeeds_and_spends_humanity():
    character = Character(name="t", cash=10_000)
    assert install_cyberware(character, "datajack") is True
    assert character.cash == 10_000 - 1000
    assert free_humanity(character) == HUMANITY_BASELINE - 0.5 - SURGERY_SCARRING


# --- Standing gate ------------------------------------------------------------
# Cyberware is the last catalog to get shops.py's min_standing gate, and it's the
# one where the gate runs *opposite* to price: Trashware is the cheap knockoff
# anyone will fit, Alphaware the bespoke tailoring a doc keeps for regulars.


def test_deltaware_is_open_to_everyone():
    """No effect in the game is ever locked behind a relationship — a stranger can
    buy every baseline piece, just not the better trade-offs on it."""
    assert all(c.min_standing == 0 for c in CYBERWARE_CATALOG if c.tier == "deltaware")


def test_tier_min_standing_matches_the_table():
    for cyberware in CYBERWARE_CATALOG:
        assert cyberware.min_standing == CYBERWARE_TIER_MIN_STANDING[cyberware.tier]


def test_the_cheapest_tier_is_not_the_hardest_to_get():
    """Trashware is half price and costs 100% more humanity — the gate must not also
    make it exclusive, or it stops being the desperate option."""
    trashware = [c for c in CYBERWARE_CATALOG if c.tier == "trashware"]
    alphaware = [c for c in CYBERWARE_CATALOG if c.tier == "alphaware"]
    assert all(c.min_standing == 0 for c in trashware)
    assert all(c.min_standing > 0 for c in alphaware)
    assert all(c.price < base.price for c, base in zip(trashware, _DELTAWARE_CYBERWARE, strict=True))


def test_catalog_for_standing_widens_as_standing_rises():
    at_zero = catalog_for_standing(0)
    at_top = catalog_for_standing(max(CYBERWARE_TIER_MIN_STANDING.values()))
    assert len(at_zero) < len(at_top)
    assert len(at_top) == len(CYBERWARE_CATALOG)
    assert all(c.min_standing == 0 for c in at_zero)
    # Every deltaware effect is reachable from a standing of nothing.
    assert {c.id for c in _DELTAWARE_CYBERWARE} <= {c.id for c in at_zero}


def test_install_refuses_a_piece_above_your_standing():
    character = Character(name="t")
    character.cash = 100_000
    gated = next(c for c in CYBERWARE_CATALOG if c.min_standing > 0)
    assert install_cyberware(character, gated.id, standing=gated.min_standing - 1) is False
    assert character.installed_cyberware == {}
    assert character.cash == 100_000


def test_install_allows_it_at_exactly_the_required_standing():
    character = Character(name="t")
    character.cash = 100_000
    gated = next(c for c in CYBERWARE_CATALOG if c.min_standing > 0)
    assert install_cyberware(character, gated.id, standing=gated.min_standing) is True
    assert character.installed_cyberware[gated.slot] == gated.id


@pytest.mark.parametrize("kwargs", [{}, {"standing": -5}], ids=["default", "negative"])
def test_an_ungated_install_goes_through_at_or_below_zero_standing(kwargs):
    """Omitting standing keeps every pre-existing caller — and a clinic with no owner
    NPC — working, on the same default shops.buy_item uses; a negative one still clears,
    because _effective_standing floors it rather than hiding the whole catalog."""
    character = Character(name="t")
    character.cash = 100_000
    open_piece = next(c for c in CYBERWARE_CATALOG if c.min_standing == 0)
    assert install_cyberware(character, open_piece.id, **kwargs) is True


def test_every_catalog_row_is_installable_within_the_humanity_baseline():
    """A row nobody can ever install is a permanent dead line on a clinic's shelf,
    reading "not enough humanity left" forever. character.py guards this at import
    (it's the module that sees both HUMANITY_BASELINE and the catalog); this pins
    the intent from the cyberware side too.

    The escalating tier multipliers are what make it easy to break: a Tier 1 row
    priced right can still push its Tier 4 knockoff past the baseline."""
    over = [c.id for c in CYBERWARE_CATALOG if c.humanity_cost > HUMANITY_BASELINE]
    assert over == []


def test_a_negative_standing_still_leaves_deltaware_on_the_shelf():
    """A failed gig at the clinic (gigs.GIG_FAIL_STANDING_HIT) drives local standing
    below 0. Without the floor in _effective_standing that hid the *entire* catalog,
    not just the gated tiers -- breaking "Deltaware is open to everyone" outright."""
    at_negative = catalog_for_standing(-3)
    assert {c.id for c in _DELTAWARE_CYBERWARE} <= {c.id for c in at_negative}
    assert at_negative == catalog_for_standing(0)


def test_a_negative_standing_still_refuses_a_gated_tier():
    """The floor must not become a free pass — it restores Deltaware, nothing more."""
    character = Character(name="t")
    character.cash = 100_000
    gated = next(c for c in CYBERWARE_CATALOG if c.min_standing > 0)
    assert install_cyberware(character, gated.id, standing=-5) is False


def test_deltaware_min_standing_is_pinned_to_the_table():
    """_tier_variant only reads the other three grades, so a deltaware row takes
    min_standing from the dataclass default — the table's deltaware entry would
    otherwise be decorative. cybernetics.py guards this at import; asserting it
    here documents why."""
    assert all(
        c.min_standing == CYBERWARE_TIER_MIN_STANDING[c.tier] for c in CYBERWARE_CATALOG
    )


# --- Cyberpsychosis: surgery scars, removal rebounds --------------------------
# Humanity is a *ceiling* (Character.humanity) worn down permanently by every
# operation; free_humanity is what's left of the runner once installed chrome is
# subtracted. The ceiling only ever falls; free Humanity rebounds when chrome comes
# out, which is the way back out of the spiral.


def test_installing_scars_the_ceiling_permanently():
    character = Character(name="t")
    character.cash = 100_000
    install_cyberware(character, "datajack")  # humanity_cost 0.5
    assert character.humanity == HUMANITY_BASELINE - SURGERY_SCARRING
    assert free_humanity(character) == HUMANITY_BASELINE - SURGERY_SCARRING - 0.5


def test_removing_rebounds_free_humanity_but_scars_again():
    """The whole point of the rebound: pulling chrome gives the implant's cost back
    and charges only the operation, so it's always a clear net gain."""
    character = Character(name="t")
    character.cash = 100_000
    install_cyberware(character, "reflex_coprocessor")  # humanity_cost 2.5
    sunk = free_humanity(character)
    remove_cyberware(character, CyberSlot.NEURALWARE)
    assert free_humanity(character) > sunk
    # Two operations' worth of scarring, and nothing installed.
    assert character.humanity == HUMANITY_BASELINE - 2 * SURGERY_SCARRING
    assert free_humanity(character) == character.humanity


def test_removing_an_empty_slot_does_not_scar():
    """Nobody opened anyone up — a no-op removal must not cost a scar."""
    character = Character(name="t")
    before = character.humanity
    assert remove_cyberware(character, CyberSlot.ARMS) is None
    assert character.humanity == before


def test_churning_the_same_implant_grinds_the_ceiling_down():
    """Swapping loadouts repeatedly costs Humanity with nothing to show for it —
    the ratchet that stops surgery being a free undo."""
    character = Character(name="t")
    character.cash = 100_000
    for _ in range(5):
        install_cyberware(character, "datajack")
        remove_cyberware(character, CyberSlot.NEURALWARE)
    assert character.humanity == round(HUMANITY_BASELINE - 10 * SURGERY_SCARRING, 2)
    assert character.installed_cyberware == {}


def test_humanity_penalty_steps_with_the_thresholds():
    character = Character(name="t")
    assert character.humanity_penalty == 0
    for expected, threshold in enumerate(HUMANITY_PENALTY_THRESHOLDS, start=1):
        character.humanity = threshold - 0.1
        character.installed_cyberware = {}
        assert character.humanity_penalty == expected


def test_humanity_penalty_reads_installed_chrome_not_just_the_ceiling():
    """Chrome you're carrying is the part of you that's gone, so the penalty has to
    follow free_humanity — otherwise installing would cost nothing until scarring
    caught up."""
    character = Character(name="t")
    character.cash = 100_000
    assert character.humanity_penalty == 0
    # One implant is deliberately free of penalty (see HUMANITY_PENALTY_THRESHOLDS);
    # it takes a real loadout to start losing yourself.
    for piece in ("reflex_coprocessor", "hydraulic_cyberarm"):
        install_cyberware(character, piece)
    assert character.humanity_penalty > 0


def test_the_penalty_reaches_every_check_through_stat():
    """stat() is the one chokepoint gear/cyberware/fatigue already go through, so
    wiring the penalty there makes every roll in the game feel it for free."""
    character = Character(name="t")
    character.cash = 100_000
    character.body = 5
    before = character.stat("body")
    for piece in ("reflex_coprocessor", "hydraulic_cyberarm"):
        install_cyberware(character, piece)
    assert character.humanity_penalty > 0
    # Body gets no cyberware bonus from either piece, so the drop is the penalty alone.
    assert character.stat("body") == before - character.humanity_penalty


def test_pulling_chrome_back_out_lifts_the_penalty_again():
    character = Character(name="t")
    character.cash = 100_000
    for piece in ("reflex_coprocessor", "hydraulic_cyberarm"):
        install_cyberware(character, piece)
    assert character.humanity_penalty > 0
    remove_cyberware(character, CyberSlot.ARMS)
    assert character.humanity_penalty == 0


def test_an_install_that_exactly_fits_still_tips_you_under():
    """install_cyberware only refuses a piece that doesn't *fit*, so one costing
    exactly what's left is legal — and the operation's own scar takes free Humanity
    below 0. That's the cyberpsychosis cliff RipperdocScreen warns about and ends
    the run on; the model has to actually produce it."""
    character = Character(name="t")
    character.cash = 100_000
    piece = CYBERWARE_BY_ID["reflex_coprocessor"]  # humanity_cost 2.5
    character.humanity = piece.humanity_cost  # nothing to spare
    assert install_cyberware(character, piece.id) is True
    assert free_humanity(character) < 0


def test_a_piece_that_does_not_fit_is_still_refused():
    """The cliff must not become a free-for-all — capacity is still a gate."""
    character = Character(name="t")
    character.cash = 100_000
    character.humanity = 1.0
    assert install_cyberware(character, "reflex_coprocessor") is False
    assert character.installed_cyberware == {}
    assert character.humanity == 1.0  # no scar for an operation that never happened


def test_a_full_cheap_loadout_leaves_the_runner_barely_standing():
    """SURGERY_SCARRING is sized against the catalog's own 'one cheap piece per slot
    sums to 5.5 of 6' note: four installs scar 0.4, so the loadout still fits — but
    only just. Fully chromed should read as barely holding on, not as impossible."""
    character = Character(name="t")
    character.cash = 100_000
    cheapest = {}
    for cyberware in CYBERWARE_CATALOG:
        if cyberware.tier != "deltaware":
            continue
        current = cheapest.get(cyberware.slot)
        if current is None or cyberware.humanity_cost < current.humanity_cost:
            cheapest[cyberware.slot] = cyberware
    for cyberware in cheapest.values():
        assert install_cyberware(character, cyberware.id) is True
    assert len(character.installed_cyberware) == len(CyberSlot)
    # 4.0 of chrome plus 0.4 of scarring against a baseline of 6: diminished, but a
    # long way from the cap. Fully chromed must stay playable.
    assert free_humanity(character) == HUMANITY_BASELINE - 4.0 - 4 * SURGERY_SCARRING
    assert character.humanity_penalty == 1


def test_no_catalog_row_is_instant_death_for_a_fresh_runner():
    """The guard in character.py has to account for SURGERY_SCARRING, not just fit: a
    row costing HUMANITY_BASELINE - SURGERY_SCARRING or more passes a naive "does it
    fit" test and then takes a fresh runner's last sliver on the way in — a shelf row
    that is instant game over rather than an implant.

    Only 0.3 of slack separates the current catalog's priciest piece
    (adamantium_bones_trashware at 5.6) from that line, so this is a live constraint on
    anyone retuning bone lacing, not theoretical headroom."""
    character = Character(name="t")
    character.cash = 1_000_000
    for cyberware in CYBERWARE_CATALOG:
        assert cyberware.humanity_cost + SURGERY_SCARRING < HUMANITY_BASELINE, cyberware.id


def test_lost_to_cyberpsychosis_tracks_free_humanity():
    character = Character(name="t")
    assert lost_to_cyberpsychosis(character) is False
    character.humanity = SURGERY_SCARRING
    character.installed_cyberware = {}
    assert lost_to_cyberpsychosis(character) is False
    character.humanity = 0.0
    assert lost_to_cyberpsychosis(character) is True


def test_the_lethal_install_is_detectable_by_the_model_not_just_a_screen():
    """install_cyberware allows the install that ends you, so the condition has to be
    checkable from the model — otherwise the fail state depends on which screen
    happened to sell the chrome."""
    character = Character(name="t")
    character.cash = 100_000
    piece = CYBERWARE_BY_ID["reflex_coprocessor"]
    character.humanity = piece.humanity_cost
    assert install_cyberware(character, piece.id) is True
    assert lost_to_cyberpsychosis(character) is True
