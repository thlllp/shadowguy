import random

import pytest

from shadowguy.character import Character
from shadowguy.combat import Drop
from shadowguy.matrix import (
    BARE_JACK_DAMAGE,
    ICE_BY_ID,
    ICE_TIERS,
    MIN_READY_HACK,
    MatrixActionKind,
    MatrixOutcome,
    available_matrix_actions,
    firewall_defense,
    firewall_soak,
    matrix_readiness,
    player_attack_damage,
    player_integrity,
    roll_ice,
    start_matrix,
    take_matrix_turn,
)
from shadowguy.shops import PROGRAMS_BY_ID, InventoryItem

SEEDS = range(150)

PASSIVE_PROGRAM = next(p for p in PROGRAMS_BY_ID.values() if p.uses_per_fight == 0 and p.integrity_bonus)
DAMAGE_PROGRAM = next(p for p in PROGRAMS_BY_ID.values() if p.action_damage)
SKIP_ICE_PROGRAM = next(p for p in PROGRAMS_BY_ID.values() if p.action_skip_ice)


def _char(intelligence=1, hack_rank=1, deck_id=None, installed_programs=()):
    c = Character(name="T", location_id="start")
    c.intelligence = intelligence
    c.skill_ranks["hack"] = hack_rank
    if deck_id is not None:
        c.inventory.append(InventoryItem(deck_id, equipped=True, installed_programs=list(installed_programs)))
    return c


# --- pure helpers -----------------------------------------------------------


def test_integrity_scales_off_intelligence_gear_included():
    assert player_integrity(_char(intelligence=1)) == 7
    # zetatech_rig adds +3 Intelligence, so a stat-6 hacker fights at Int 9.
    assert player_integrity(_char(intelligence=6, deck_id="zetatech_rig")) == 5 + 2 * 9


def test_attack_damage_comes_from_the_deck_not_the_skill():
    # No deck: the bare-jack floor, however much Hack you have.
    assert player_attack_damage(_char(intelligence=6, hack_rank=8)) == BARE_JACK_DAMAGE
    # Deck rating (its Intelligence bonus) drives it: burner +1, zetatech +3.
    assert player_attack_damage(_char(deck_id="burner_deck")) == 3
    assert player_attack_damage(_char(deck_id="zetatech_rig")) == 5


def test_readiness_flags_missing_deck_and_low_hack():
    assert matrix_readiness(_char()) == ["a cyberdeck", "more Hack skill"]
    # A deck alone isn't enough if Hack is still weak.
    assert matrix_readiness(_char(deck_id="burner_deck")) == ["more Hack skill"]
    # Hack skill alone isn't enough without a deck.
    ready_hack = _char(intelligence=MIN_READY_HACK)  # skill_value = Int + rank(1) >= MIN
    assert matrix_readiness(ready_hack) == ["a cyberdeck"]
    # Both: no warning.
    assert matrix_readiness(_char(intelligence=6, hack_rank=4, deck_id="zetatech_rig")) == []


def test_actions_always_offer_attack_and_jack_out():
    actions = available_matrix_actions(_char())
    kinds = {a.kind for a in actions}
    assert MatrixActionKind.ATTACK in kinds
    assert MatrixActionKind.JACK_OUT in kinds
    attack = next(a for a in actions if a.kind is MatrixActionKind.ATTACK)
    assert attack.skill == "hack"
    jack = next(a for a in actions if a.kind is MatrixActionKind.JACK_OUT)
    assert jack.skill is None  # the one action that isn't a check


# --- cyberdeck programs --------------------------------------------------


def test_passive_program_bonuses_fold_into_the_base_formulas():
    bare = _char(intelligence=1, deck_id="burner_deck")
    equipped = _char(intelligence=1, deck_id="burner_deck", installed_programs=[PASSIVE_PROGRAM.id])
    assert player_integrity(equipped) == player_integrity(bare) + PASSIVE_PROGRAM.integrity_bonus
    firewall_program = next(p for p in PROGRAMS_BY_ID.values() if p.uses_per_fight == 0 and p.firewall_bonus)
    with_firewall = _char(deck_id="burner_deck", installed_programs=[firewall_program.id])
    assert firewall_defense(with_firewall) == firewall_defense(bare) + firewall_program.firewall_bonus
    soak_program = next((p for p in PROGRAMS_BY_ID.values() if p.uses_per_fight == 0 and p.soak_bonus), None)
    if soak_program is not None:
        with_soak = _char(deck_id="burner_deck", installed_programs=[soak_program.id])
        assert firewall_soak(with_soak) == firewall_soak(bare) + soak_program.soak_bonus
    damage_program = next(p for p in PROGRAMS_BY_ID.values() if p.uses_per_fight == 0 and p.damage_bonus)
    with_damage = _char(deck_id="burner_deck", installed_programs=[damage_program.id])
    assert player_attack_damage(with_damage) == player_attack_damage(bare) + damage_program.damage_bonus


def test_passive_program_only_counts_on_the_active_deck():
    """A program installed on a *stowed* deck instance doesn't count -- only the best
    equipped deck's programs matter (matrix.active_deck_entry)."""
    c = _char(intelligence=1, deck_id="burner_deck", installed_programs=[PASSIVE_PROGRAM.id])
    c.inventory[0].equipped = False
    assert player_integrity(c) == player_integrity(_char(intelligence=1))


def test_action_program_appears_in_available_actions_with_uses_remaining():
    c = _char(deck_id="burner_deck", installed_programs=[DAMAGE_PROGRAM.id])
    actions = available_matrix_actions(c)
    program_action = next(a for a in actions if a.kind is MatrixActionKind.PROGRAM)
    assert program_action.program is DAMAGE_PROGRAM
    assert str(DAMAGE_PROGRAM.uses_per_fight) in program_action.label


def test_action_program_hidden_once_charges_are_spent():
    c = _char(deck_id="burner_deck", installed_programs=[DAMAGE_PROGRAM.id])
    exhausted = {DAMAGE_PROGRAM.id: 0}
    actions = available_matrix_actions(c, exhausted)
    assert not any(a.kind is MatrixActionKind.PROGRAM for a in actions)


def test_passive_program_never_offered_as_an_action():
    c = _char(deck_id="burner_deck", installed_programs=[PASSIVE_PROGRAM.id])
    actions = available_matrix_actions(c)
    assert not any(a.kind is MatrixActionKind.PROGRAM for a in actions)


def test_damage_program_deals_guaranteed_no_roll_damage():
    c = _char(intelligence=1, deck_id="burner_deck", installed_programs=[DAMAGE_PROGRAM.id])
    state = start_matrix(c, roll_ice(0, random.Random(0)), Drop.NONE, random.Random(0))
    target = state.standing[0]
    before = target.integrity
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.kind is MatrixActionKind.PROGRAM)
    take_matrix_turn(state, action, random.Random(0))
    assert target.integrity == max(0, before - DAMAGE_PROGRAM.action_damage)
    assert state.program_uses[DAMAGE_PROGRAM.id] == 0


def test_skip_ice_program_grants_a_free_round_via_ice_skip_rounds():
    c = _char(intelligence=1, deck_id="burner_deck", installed_programs=[SKIP_ICE_PROGRAM.id])
    state = start_matrix(c, roll_ice(0, random.Random(0)), Drop.NONE, random.Random(0))
    assert state.ice_skip_rounds == 0
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.kind is MatrixActionKind.PROGRAM)
    take_matrix_turn(state, action, random.Random(0))
    assert state.program_uses[SKIP_ICE_PROGRAM.id] == 0
    # take_matrix_turn runs the ICE phase in the same call, so the free round it granted
    # is spent by the time we check -- what proves the skip worked is that no ICE bite
    # landed this round: integrity is untouched.
    assert state.integrity == state.max_integrity


def test_deckless_runner_gets_no_program_actions():
    c = _char()  # no deck at all
    actions = available_matrix_actions(c)
    assert not any(a.kind is MatrixActionKind.PROGRAM for a in actions)


# --- roster / tiers ---------------------------------------------------------


@pytest.mark.parametrize("tier", sorted(ICE_TIERS))
@pytest.mark.parametrize("seed", SEEDS)
def test_roll_ice_draws_from_the_tier_pool_in_count_range(tier, seed):
    pool, (low, high) = ICE_TIERS[tier]
    ice = roll_ice(tier, random.Random(seed))
    assert low <= len(ice) <= high
    assert all(program.id in pool for program in ice)
    assert all(program.id in ICE_BY_ID for program in ice)


# --- fight resolution -------------------------------------------------------


def test_jack_out_ejects_immediately_without_a_roll():
    state = start_matrix(_char(), roll_ice(0, random.Random(0)), Drop.NONE, random.Random(0))
    jack = next(a for a in available_matrix_actions(state.character) if a.kind is MatrixActionKind.JACK_OUT)
    take_matrix_turn(state, jack, random.Random(0))
    assert state.outcome is MatrixOutcome.EJECTED
    assert state.integrity > 0  # you bailed with integrity to spare; the contract's just blown


def test_player_drop_buys_a_free_ice_round():
    state = start_matrix(_char(), roll_ice(0, random.Random(1)), Drop.PLAYER, random.Random(1))
    assert state.ice_skip_rounds == 1
    assert state.integrity == state.max_integrity  # nobody hit you before you acted


def test_enemy_drop_can_bite_before_you_act():
    # A strong-attacking tier and a paper-thin firewall: an ENEMY drop should land the
    # opening bite often enough that at least one of these seeds drops integrity.
    bitten = False
    for seed in SEEDS:
        state = start_matrix(_char(intelligence=1), roll_ice(2, random.Random(seed)), Drop.ENEMY, random.Random(seed))
        if state.integrity < state.max_integrity:
            bitten = True
            break
    assert bitten


def _run(character, seed, tier):
    rng = random.Random(seed)
    state = start_matrix(character, roll_ice(tier, rng), Drop.NONE, rng)
    while not state.is_over:
        attack = next(a for a in available_matrix_actions(character) if a.kind is MatrixActionKind.ATTACK)
        take_matrix_turn(state, attack, rng)
    return state.outcome


def test_the_matrix_belongs_to_the_hacker():
    """The design claim, stated as rates rather than absolutes (a deckless runner *can*
    fluke a win, a hacker *could* in principle whiff a whole fight — neither is the point):
    a decked hacker seizes nearly every tier-2 run on a bare always-attack policy, and a
    deckless Int-1 runner is ejected from nearly all of them. Ejection, never death."""
    hacker = _char(intelligence=6, hack_rank=4, deck_id="zetatech_rig")
    hacker_seized = sum(_run(hacker, seed, tier=2) is MatrixOutcome.SEIZED for seed in SEEDS)
    weakling_ejected = sum(_run(_char(), seed, tier=2) is MatrixOutcome.EJECTED for seed in SEEDS)
    assert hacker_seized >= 0.95 * len(SEEDS)
    assert weakling_ejected >= 0.90 * len(SEEDS)


@pytest.mark.parametrize("seed", SEEDS)
def test_matrix_fight_always_terminates_and_leaves_one_outcome(seed):
    outcome = _run(_char(intelligence=3, hack_rank=2, deck_id="cracked_cyberdeck"), seed, tier=1)
    assert outcome in (MatrixOutcome.SEIZED, MatrixOutcome.EJECTED)
