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
    matrix_readiness,
    player_attack_damage,
    player_integrity,
    roll_ice,
    start_matrix,
    take_matrix_turn,
)
from shadowguy.shops import InventoryItem

SEEDS = range(150)


def _char(intelligence=1, hack_rank=1, deck_id=None):
    c = Character(name="T", location_id="start")
    c.intelligence = intelligence
    c.skill_ranks["hack"] = hack_rank
    if deck_id is not None:
        c.inventory.append(InventoryItem(deck_id, equipped=True))
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
