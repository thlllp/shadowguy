import random
from collections import deque

import pytest

from shadowguy.character import Character
from shadowguy.combat import Drop
from shadowguy.matrix import (
    BARE_JACK_DAMAGE,
    EXTRACT_UNLIMITED_USES,
    ICE_BY_ID,
    ICE_TIERS,
    MATRIX_NETWORK_TIERS,
    MIN_READY_HACK,
    SECURITY_HOSTILE_THRESHOLD,
    SECURITY_PER_FAILED_EXTRACT,
    MatrixActionKind,
    MatrixNetwork,
    MatrixNode,
    MatrixNodeRole,
    MatrixOutcome,
    analyze_node,
    available_matrix_actions,
    connected_nodes,
    extract,
    firewall_defense,
    firewall_soak,
    generate_matrix_network,
    jack_out,
    matrix_readiness,
    move_to,
    player_attack_damage,
    player_integrity,
    render_matrix_network,
    roll_ice,
    start_matrix,
    start_matrix_run,
    take_matrix_turn,
    take_run_turn,
    usable_analyze_program,
)
from shadowguy.shops import PROGRAMS_BY_ID, STOLEN_DATASHARD_ID, InventoryItem, Program

SEEDS = range(150)

# Every program in today's catalog (sleaze/extract/analyze) is action-shaped, and each
# tests its own specific behavior further down. Where a test only cares about the
# generic *mechanism* (a passive bonus folding into a base formula, an action program
# with some other guaranteed effect) rather than any one program's flavor, it builds a
# synthetic Program and monkeypatches it into PROGRAMS_BY_ID for the test's duration --
# installed_programs is resolved by id through that dict (shops.installed_programs_for),
# so this is the real resolution path, not a mock of it.


class AlwaysSix(random.Random):
    def randint(self, a, b):
        return 6


class AlwaysOne(random.Random):
    def randint(self, a, b):
        return 1


class FixedChance(random.Random):
    """A Random whose random() is pinned to a fixed value -- for forcing Sleaze's flat
    three-way split into a specific branch -- while randint/choice keep using the real
    generator state (same technique as test_app_flows.ForcedChance)."""

    def __init__(self, value: float, seed: int = 0) -> None:
        super().__init__(seed)
        self._value = value

    def random(self) -> float:
        return self._value


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


# --- cyberdeck programs: generic mechanism (passive bonuses, action-program shape) ---


def test_passive_program_bonuses_fold_into_the_base_formulas(monkeypatch):
    program = Program(
        id="test_passive", name="Test Passive", price=0,
        integrity_bonus=3, firewall_bonus=2, soak_bonus=1, damage_bonus=4,
    )
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    bare = _char(intelligence=1, deck_id="burner_deck")
    equipped = _char(intelligence=1, deck_id="burner_deck", installed_programs=[program.id])
    assert player_integrity(equipped) == player_integrity(bare) + program.integrity_bonus
    assert firewall_defense(equipped) == firewall_defense(bare) + program.firewall_bonus
    assert firewall_soak(equipped) == firewall_soak(bare) + program.soak_bonus
    assert player_attack_damage(equipped) == player_attack_damage(bare) + program.damage_bonus


def test_passive_program_only_counts_on_the_active_deck(monkeypatch):
    """A program installed on a *stowed* deck instance doesn't count -- only the best
    equipped deck's programs matter (matrix.active_deck_entry)."""
    program = Program(id="test_passive", name="Test Passive", price=0, integrity_bonus=3)
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(intelligence=1, deck_id="burner_deck", installed_programs=[program.id])
    c.inventory[0].equipped = False
    assert player_integrity(c) == player_integrity(_char(intelligence=1))


def test_action_program_appears_in_available_actions_with_uses_remaining(monkeypatch):
    program = Program(id="test_action", name="Test Action", price=0, uses_per_fight=3, action_damage=5)
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    actions = available_matrix_actions(c)
    program_action = next(a for a in actions if a.kind is MatrixActionKind.PROGRAM)
    assert program_action.program is program
    assert str(program.uses_per_fight) in program_action.label


def test_action_program_hidden_once_charges_are_spent(monkeypatch):
    program = Program(id="test_action", name="Test Action", price=0, uses_per_fight=3, action_damage=5)
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    exhausted = {program.id: 0}
    actions = available_matrix_actions(c, exhausted)
    assert not any(a.kind is MatrixActionKind.PROGRAM for a in actions)


def test_unlimited_use_action_program_is_always_offered_regardless_of_uses(monkeypatch):
    """A negative uses_per_fight (matrix.EXTRACT_UNLIMITED_USES) is never charge-gated
    -- the real catalog example is Extract, exercised end-to-end further down; this
    checks the generic mechanism in available_matrix_actions directly."""
    program = Program(
        id="test_unlimited", name="Test Unlimited", price=0,
        uses_per_fight=EXTRACT_UNLIMITED_USES, action_damage=5,
    )
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    # No charges ever tracked for it (program_uses would be empty for a fresh fight),
    # and it still shows up.
    actions = available_matrix_actions(c, program_uses={})
    program_action = next(a for a in actions if a.kind is MatrixActionKind.PROGRAM)
    assert program_action.program is program
    assert "unlimited" in program_action.label.lower()


def test_passive_program_never_offered_as_an_action(monkeypatch):
    program = Program(id="test_passive", name="Test Passive", price=0, integrity_bonus=3)
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    actions = available_matrix_actions(c)
    assert not any(a.kind is MatrixActionKind.PROGRAM for a in actions)


def test_damage_program_deals_guaranteed_no_roll_damage(monkeypatch):
    program = Program(id="test_action", name="Test Action", price=0, uses_per_fight=3, action_damage=5)
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(intelligence=1, deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, roll_ice(0, random.Random(0)), Drop.NONE, random.Random(0))
    target = state.standing[0]
    before = target.integrity
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.kind is MatrixActionKind.PROGRAM)
    take_matrix_turn(state, action, random.Random(0))
    assert target.integrity == max(0, before - program.action_damage)
    assert state.program_uses[program.id] == 2


def test_skip_ice_program_grants_a_free_round_via_ice_skip_rounds(monkeypatch):
    program = Program(id="test_action", name="Test Action", price=0, uses_per_fight=3, action_skip_ice=True)
    monkeypatch.setitem(PROGRAMS_BY_ID, program.id, program)
    c = _char(intelligence=1, deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, roll_ice(0, random.Random(0)), Drop.NONE, random.Random(0))
    assert state.ice_skip_rounds == 0
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.kind is MatrixActionKind.PROGRAM)
    take_matrix_turn(state, action, random.Random(0))
    assert state.program_uses[program.id] == 2
    # take_matrix_turn runs the ICE phase in the same call, so the free round it granted
    # is spent by the time we check -- what proves the skip worked is that no ICE bite
    # landed this round: integrity is untouched.
    assert state.integrity == state.max_integrity


def test_deckless_runner_gets_no_program_actions():
    c = _char()  # no deck at all
    actions = available_matrix_actions(c)
    assert not any(a.kind is MatrixActionKind.PROGRAM for a in actions)


# --- Sleaze (Program.action_sleaze) ---------------------------------------------


def test_sleaze_success_clears_the_target_ice_and_can_seize_a_final_node():
    """_char()'s defaults give Hack skill_value 2 -- SLEAZE_MARGIN_FLOOR exactly -- and
    watchdog's defense converts to opposing pool 0, so margin sits right at the floor
    with zero shift: exactly the neutral 1/3-1/3-1/3 split. That's what lets
    0.1/0.5/0.99 deterministically land in each of the three branches below without
    needing to import the private odds formula."""
    program = PROGRAMS_BY_ID["sleaze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(0))
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    take_matrix_turn(state, action, FixedChance(0.1))
    assert state.ices[0].integrity == 0
    assert state.outcome is MatrixOutcome.SEIZED  # is_final_node defaults True


def test_sleaze_plain_fail_wastes_the_round_only():
    program = PROGRAMS_BY_ID["sleaze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(0))
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    before = state.ices[0].integrity
    take_matrix_turn(state, action, FixedChance(0.5))
    assert state.ices[0].integrity == before  # missed, ICE untouched
    assert state.security == 0  # only Extract's misses touch security, not Sleaze's


def test_sleaze_critical_fail_bites_twice_in_the_same_round():
    program = PROGRAMS_BY_ID["sleaze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(0))
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    take_matrix_turn(state, action, FixedChance(0.99))
    assert "snaps to alert" in state.log[0]
    # the alert line, plus two separate _ice_turn resolutions this round: the
    # immediate critical-fail bite and the ordinary end-of-round ICE phase.
    assert len(state.log) == 3


def test_sleaze_success_rate_improves_with_hack_skill_against_tougher_ice():
    program = PROGRAMS_BY_ID["sleaze"]
    weak = _char(deck_id="burner_deck", installed_programs=[program.id])
    strong = _char(intelligence=6, hack_rank=8, deck_id="burner_deck", installed_programs=[program.id])

    def success_rate(character, ice, seeds):
        successes = 0
        for seed in seeds:
            state = start_matrix(character, (ice,), Drop.NONE, random.Random(seed))
            action = next(a for a in available_matrix_actions(character, state.program_uses) if a.program is program)
            take_matrix_turn(state, action, random.Random(seed))
            if state.ices[0].integrity == 0:
                successes += 1
        return successes / len(seeds)

    weak_rate = success_rate(weak, ICE_BY_ID["watchdog"], SEEDS)
    strong_rate = success_rate(strong, ICE_BY_ID["black_ice"], SEEDS)
    assert abs(weak_rate - 1 / 3) < 0.08  # at the floor this should track the neutral third
    assert strong_rate > weak_rate  # more Hack against tougher ICE still comes out ahead


# --- Extract (Program.action_extract) ---------------------------------------------


def test_extract_has_unlimited_uses_per_the_catalog():
    assert PROGRAMS_BY_ID["extract"].uses_per_fight == EXTRACT_UNLIMITED_USES


def test_extract_wastes_the_attempt_off_a_non_extractable_node():
    program = PROGRAMS_BY_ID["extract"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(0))
    assert state.is_extractable is False  # a direct start_matrix() call has no node context
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    before = state.ices[0].integrity
    take_matrix_turn(state, action, random.Random(0))
    assert state.ices[0].integrity == before
    assert "nothing here worth extracting" in state.log[0]


def test_extract_ignores_soak_unlike_an_ordinary_attack():
    program = PROGRAMS_BY_ID["extract"]
    # Floor hack + a small deck keeps damage well under black_ice's integrity, so
    # neither roll clamps to a fully-cleared 0 -- the raw damage numbers stay
    # comparable rather than both bottoming out at "dead."
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    tough_ice = ICE_BY_ID["black_ice"]

    extract_state = start_matrix(c, (tough_ice,), Drop.NONE, random.Random(0))
    extract_state.is_extractable = True
    extract_action = next(a for a in available_matrix_actions(c, extract_state.program_uses) if a.program is program)
    take_matrix_turn(extract_state, extract_action, AlwaysSix())
    extract_damage = tough_ice.integrity - extract_state.ices[0].integrity

    attack_state = start_matrix(c, (tough_ice,), Drop.NONE, random.Random(0))
    attack_action = next(a for a in available_matrix_actions(c) if a.kind is MatrixActionKind.ATTACK)
    take_matrix_turn(attack_state, attack_action, AlwaysSix())
    attack_damage = tough_ice.integrity - attack_state.ices[0].integrity

    assert extract_damage == attack_damage + tough_ice.soak


def test_extract_stays_offered_and_untracked_across_repeated_uses():
    program = PROGRAMS_BY_ID["extract"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(0))
    state.is_extractable = True
    for _ in range(3):
        action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
        take_matrix_turn(state, action, random.Random(0))
        assert program.id not in state.program_uses  # no charge ever tracked for it
        if state.is_over or not state.standing:
            break


def test_extract_miss_raises_security_by_the_configured_amount():
    program = PROGRAMS_BY_ID["extract"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])  # floor hack: misses are easy to force
    state = start_matrix(c, (ICE_BY_ID["black_ice"],), Drop.NONE, random.Random(0))
    state.is_extractable = True
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    assert state.security == 0
    take_matrix_turn(state, action, AlwaysOne())
    assert state.security == SECURITY_PER_FAILED_EXTRACT


# --- MatrixState.security / Sentinel ICE ------------------------------------------


def test_sentinel_ice_drips_security_instead_of_damaging_integrity():
    sentinel = ICE_BY_ID["sentinel"]
    assert sentinel.security_per_round > 0
    c = _char(deck_id="burner_deck")
    state = start_matrix(c, (sentinel,), Drop.NONE, random.Random(0))
    attack = next(a for a in available_matrix_actions(c) if a.kind is MatrixActionKind.ATTACK)
    before_integrity = state.integrity
    take_matrix_turn(state, attack, random.Random(0))
    assert state.integrity == before_integrity  # never bitten -- only drained via security
    assert state.security == sentinel.security_per_round


def test_security_increases_the_ice_hit_rate_against_the_player():
    def hit_rate(security, seeds):
        hits = 0
        for seed in seeds:
            c = _char(deck_id="burner_deck")
            state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(seed))
            state.security = security
            harden = next(a for a in available_matrix_actions(c) if a.kind is MatrixActionKind.HARDEN)
            before = state.integrity
            take_matrix_turn(state, harden, random.Random(seed))
            if state.integrity < before:
                hits += 1
        return hits / len(seeds)

    assert hit_rate(15, SEEDS) > hit_rate(0, SEEDS)


# --- Icebreaker (Program.action_damage) -------------------------------------------


def test_icebreaker_has_unlimited_uses_per_the_catalog():
    assert PROGRAMS_BY_ID["icebreaker"].uses_per_fight == EXTRACT_UNLIMITED_USES


def test_icebreaker_deals_guaranteed_no_roll_damage_and_stays_offered_untracked():
    program = PROGRAMS_BY_ID["icebreaker"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["black_ice"],), Drop.NONE, random.Random(0))
    target = state.standing[0]
    before = target.integrity
    action = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    take_matrix_turn(state, action, random.Random(0))
    assert target.integrity == max(0, before - program.action_damage)
    assert program.id not in state.program_uses  # unlimited: no charge ever tracked
    # still offered afterward, with no charge count in its label
    action_again = next(a for a in available_matrix_actions(c, state.program_uses) if a.program is program)
    assert "unlimited" in action_again.label.lower()


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


# --- generate_matrix_network -------------------------------------------------

TIERS = sorted(MATRIX_NETWORK_TIERS)


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_network_is_connected_with_a_distinct_entry_and_data(tier, seed):
    network = generate_matrix_network(tier, random.Random(seed))
    assert network.entry_id in network.nodes
    assert network.data_id in network.nodes
    assert network.entry_id != network.data_id
    assert network.nodes[network.entry_id].role is MatrixNodeRole.ENTRY
    assert network.nodes[network.data_id].role is MatrixNodeRole.DATA

    seen = {network.entry_id}
    queue = deque([network.entry_id])
    while queue:
        current = queue.popleft()
        for neighbor in network.nodes[current].connections:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    assert seen == set(network.nodes)


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_network_connections_are_symmetric(tier, seed):
    network = generate_matrix_network(tier, random.Random(seed))
    for node in network.nodes.values():
        for other_id in node.connections:
            assert node.id in network.nodes[other_id].connections


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_network_node_count_is_in_tier_range_plus_optional_extras(tier, seed):
    (low, high), _ic_density, _cpu_chance, _cache_chance = MATRIX_NETWORK_TIERS[tier]
    network = generate_matrix_network(tier, random.Random(seed))
    extras = (MatrixNodeRole.CPU, MatrixNodeRole.CACHE)
    core = sum(1 for n in network.nodes.values() if n.role not in extras)
    assert low <= core <= high
    has_cpu = any(n.role is MatrixNodeRole.CPU for n in network.nodes.values())
    has_cache = any(n.role is MatrixNodeRole.CACHE for n in network.nodes.values())
    assert len(network.nodes) == core + (1 if has_cpu else 0) + (1 if has_cache else 0)


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_network_ice_only_on_guarded_roles(tier, seed):
    network = generate_matrix_network(tier, random.Random(seed))
    for node in network.nodes.values():
        if node.role in (MatrixNodeRole.ENTRY, MatrixNodeRole.SLAVE):
            assert node.ice is None
        else:
            assert node.ice is not None


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("seed", SEEDS)
def test_generated_network_cpu_only_ever_connects_to_data(tier, seed):
    network = generate_matrix_network(tier, random.Random(seed))
    cpu = next((n for n in network.nodes.values() if n.role is MatrixNodeRole.CPU), None)
    if cpu is not None:
        assert cpu.connections == (network.data_id,)


def test_cache_node_only_spawns_at_the_tier_it_is_configured_for():
    # Tier 0/2 have cache_chance 0.0 today -- assert that stays true rather than
    # assuming it, so a future retune of the table can't silently reintroduce cache
    # nodes on a tier nothing else in this test file expects them on.
    for tier in TIERS:
        _range, _ic_density, _cpu_chance, cache_chance = MATRIX_NETWORK_TIERS[tier]
        if cache_chance == 0.0:
            for seed in SEEDS:
                network = generate_matrix_network(tier, random.Random(seed))
                assert not any(n.role is MatrixNodeRole.CACHE for n in network.nodes.values())


@pytest.mark.parametrize("seed", SEEDS)
def test_cache_node_when_present_hangs_off_an_ordinary_waypoint_and_is_guarded(seed):
    network = generate_matrix_network(1, random.Random(seed))
    cache = next((n for n in network.nodes.values() if n.role is MatrixNodeRole.CACHE), None)
    if cache is None:
        return
    assert cache.ice is not None
    assert len(cache.connections) == 1
    (attach_point,) = cache.connections
    assert network.nodes[attach_point].role in (MatrixNodeRole.SLAVE, MatrixNodeRole.IC)


# --- MatrixRunState navigation ------------------------------------------------

WEAK_ICE = ICE_BY_ID["watchdog"]
TOUGH_ICE = ICE_BY_ID["black_ice"]


def _hand_built_network(data_ice=WEAK_ICE, with_cpu=False, with_cache=False):
    """entry -- slave -- ic -- data (-- cpu, optionally): a small, fixed network for
    testing navigation mechanics directly, rather than relying on generation. cache
    (optional) hangs off slave, same as a generated one hangs off any ordinary
    waypoint — not off the data/cpu end of the spine."""
    data_connections = ("ic", "cpu") if with_cpu else ("ic",)
    slave_connections = ("entry", "ic", "cache") if with_cache else ("entry", "ic")
    nodes = {
        "entry": MatrixNode(id="entry", role=MatrixNodeRole.ENTRY, connections=("slave",)),
        "slave": MatrixNode(id="slave", role=MatrixNodeRole.SLAVE, connections=slave_connections),
        "ic": MatrixNode(id="ic", role=MatrixNodeRole.IC, connections=("slave", "data"), ice=WEAK_ICE),
        "data": MatrixNode(id="data", role=MatrixNodeRole.DATA, connections=data_connections, ice=data_ice),
    }
    if with_cpu:
        nodes["cpu"] = MatrixNode(id="cpu", role=MatrixNodeRole.CPU, connections=("data",), ice=TOUGH_ICE)
    if with_cache:
        nodes["cache"] = MatrixNode(id="cache", role=MatrixNodeRole.CACHE, connections=("slave",), ice=WEAK_ICE)
    return MatrixNetwork(nodes=nodes, entry_id="entry", data_id="data")


def _ready_char():
    return _char(intelligence=6, hack_rank=6, deck_id="zetatech_rig")


def _clear_node(run, rng, max_rounds=50):
    """Always-attack the current node's guardian down (or bail after max_rounds, so a
    broken invariant fails the test instead of hanging it)."""
    rounds = 0
    while run.in_fight and rounds < max_rounds:
        attack = next(
            a for a in available_matrix_actions(run.character, run.fight.program_uses)
            if a.kind is MatrixActionKind.ATTACK
        )
        take_run_turn(run, attack, rng)
        rounds += 1


def test_start_matrix_run_enters_the_unguarded_entry_node():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    assert run.current_node_id == "entry"
    assert run.fight is None
    assert not run.in_fight


def test_move_to_refuses_a_non_adjacent_node():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    assert move_to(run, "data", random.Random(0)) is False
    assert run.current_node_id == "entry"


def test_move_through_a_slave_node_is_free_no_fight():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    assert move_to(run, "slave", random.Random(0)) is True
    assert run.current_node_id == "slave"
    assert run.fight is None


def test_moving_into_an_ic_node_opens_a_fight_via_the_existing_engine():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    move_to(run, "slave", random.Random(0))
    assert move_to(run, "ic", random.Random(0)) is True
    assert run.in_fight
    assert run.fight.ices[0].ice is WEAK_ICE


def test_move_to_refuses_while_a_guardian_is_up():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    move_to(run, "slave", random.Random(0))
    move_to(run, "ic", random.Random(0))
    assert move_to(run, "slave", random.Random(0)) is False
    assert run.current_node_id == "ic"


def test_clearing_a_non_final_node_does_not_end_the_run():
    run = start_matrix_run(_ready_char(), _hand_built_network(), Drop.NONE, random.Random(1))
    move_to(run, "slave", random.Random(1))
    move_to(run, "ic", random.Random(1))
    _clear_node(run, random.Random(1))
    assert not run.is_over
    assert "ic" in run.cleared_node_ids
    assert not run.in_fight


def test_integrity_carries_across_node_engagements_not_refilled():
    run = start_matrix_run(_ready_char(), _hand_built_network(), Drop.NONE, random.Random(2))
    move_to(run, "slave", random.Random(2))
    move_to(run, "ic", random.Random(2))
    _clear_node(run, random.Random(2))
    integrity_after_ic = run.fight.integrity
    max_integrity_after_ic = run.fight.max_integrity
    move_to(run, "data", random.Random(2))
    assert run.fight.integrity == integrity_after_ic
    assert run.fight.max_integrity == max_integrity_after_ic


def test_clearing_the_data_node_does_not_auto_win():
    run = start_matrix_run(_ready_char(), _hand_built_network(), Drop.NONE, random.Random(3))
    move_to(run, "slave", random.Random(3))
    move_to(run, "ic", random.Random(3))
    _clear_node(run, random.Random(3))
    move_to(run, "data", random.Random(3))
    _clear_node(run, random.Random(3))
    assert not run.is_over
    assert run.can_extract


def test_extract_refuses_before_data_is_cleared_and_succeeds_after():
    run = start_matrix_run(_ready_char(), _hand_built_network(), Drop.NONE, random.Random(4))
    assert extract(run) is False
    move_to(run, "slave", random.Random(4))
    move_to(run, "ic", random.Random(4))
    _clear_node(run, random.Random(4))
    move_to(run, "data", random.Random(4))
    _clear_node(run, random.Random(4))
    assert extract(run) is True
    assert run.outcome is MatrixOutcome.SEIZED


def test_cpu_node_is_reachable_and_optional_after_data_clears():
    run = start_matrix_run(_ready_char(), _hand_built_network(with_cpu=True), Drop.NONE, random.Random(5))
    move_to(run, "slave", random.Random(5))
    move_to(run, "ic", random.Random(5))
    _clear_node(run, random.Random(5))
    move_to(run, "data", random.Random(5))
    _clear_node(run, random.Random(5))
    assert move_to(run, "cpu", random.Random(5)) is True
    assert run.in_fight
    _clear_node(run, random.Random(5))
    assert not run.is_over  # visiting CPU never auto-wins either
    assert extract(run) is True


def test_clearing_a_cache_node_grants_a_sellable_item_and_never_gates_the_run():
    char = _ready_char()
    run = start_matrix_run(char, _hand_built_network(with_cache=True), Drop.NONE, random.Random(9))
    move_to(run, "slave", random.Random(9))
    assert move_to(run, "cache", random.Random(9)) is True
    assert run.in_fight
    assert not any(entry.item_id == STOLEN_DATASHARD_ID for entry in char.inventory)
    _clear_node(run, random.Random(9))
    assert not run.is_over  # a cache is side loot, not a stage in reaching/extracting data
    assert any(entry.item_id == STOLEN_DATASHARD_ID for entry in char.inventory)
    # unequipped: it's junk to sell, not gear -- and mustn't be picked up as "the"
    # active deck by shops.active_deck_entry (slot=None is that convention's whole
    # signal, so an equipped, bonus-less item would silently look like one).
    got = next(entry for entry in char.inventory if entry.item_id == STOLEN_DATASHARD_ID)
    assert got.equipped is False


def test_jacking_out_after_a_cache_still_keeps_the_loot():
    char = _ready_char()
    run = start_matrix_run(char, _hand_built_network(with_cache=True), Drop.NONE, random.Random(9))
    move_to(run, "slave", random.Random(9))
    move_to(run, "cache", random.Random(9))
    _clear_node(run, random.Random(9))
    jack_out(run)
    assert run.outcome is MatrixOutcome.EJECTED  # the job itself is blown...
    assert any(entry.item_id == STOLEN_DATASHARD_ID for entry in char.inventory)  # ...loot isn't


def test_ejection_from_a_non_final_node_ends_the_whole_run():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(6))
    move_to(run, "slave", random.Random(6))
    move_to(run, "ic", random.Random(6))
    assert run.in_fight
    run.fight.integrity = 0  # force it deterministically rather than chase the odds
    harden = next(
        a for a in available_matrix_actions(run.character, run.fight.program_uses)
        if a.kind is MatrixActionKind.HARDEN
    )
    take_run_turn(run, harden, random.Random(6))
    assert run.is_over
    assert run.outcome is MatrixOutcome.EJECTED


def test_jack_out_ends_the_run_even_with_no_active_fight():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(7))
    assert run.fight is None
    jack_out(run)
    assert run.is_over
    assert run.outcome is MatrixOutcome.EJECTED


def test_connected_nodes_reflects_current_position():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(8))
    assert {n.id for n in connected_nodes(run)} == {"slave"}
    move_to(run, "slave", random.Random(8))
    assert {n.id for n in connected_nodes(run)} == {"entry", "ic"}


# --- security-driven hostile engagement (SECURITY_HOSTILE_THRESHOLD) ---------------


def test_engage_node_stays_neutral_below_the_hostile_threshold():
    run = start_matrix_run(_ready_char(), _hand_built_network(), Drop.NONE, random.Random(1))
    move_to(run, "slave", random.Random(1))
    move_to(run, "ic", random.Random(1))
    _clear_node(run, random.Random(1))
    assert run.fight.security < SECURITY_HOSTILE_THRESHOLD
    move_to(run, "data", random.Random(1))
    # The log carries over the whole run (not reset per node), so check what this
    # specific hop appended rather than the log's full accumulated contents.
    assert run.fight.log[-1] == "ICE lights up ahead."


def test_engage_node_opens_hostile_once_security_crosses_the_threshold():
    run = start_matrix_run(_ready_char(), _hand_built_network(), Drop.NONE, random.Random(1))
    move_to(run, "slave", random.Random(1))
    move_to(run, "ic", random.Random(1))
    _clear_node(run, random.Random(1))
    run.fight.security = SECURITY_HOSTILE_THRESHOLD
    log_before = len(run.fight.log)
    move_to(run, "data", random.Random(1))
    new_lines = run.fight.log[log_before:]
    assert new_lines[0] == "ICE lights up ahead."
    assert "already briefed" in new_lines[1]  # an opening bite before the player acts


# --- node reveal (analyze_node / usable_analyze_program) ---------------------------


def test_run_starts_with_only_the_entry_node_revealed():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    assert run.revealed_node_ids == {"entry"}


def test_moving_into_a_node_reveals_it_without_needing_analyze():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    move_to(run, "slave", random.Random(0))
    assert "slave" in run.revealed_node_ids


def test_analyze_node_refuses_when_not_connected_to_current_node():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    assert analyze_node(run, "data", random.Random(0)) is False  # not adjacent to entry
    assert "data" not in run.revealed_node_ids


def test_usable_analyze_program_is_none_without_the_program_installed():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    assert usable_analyze_program(run) is None
    assert analyze_node(run, "slave", random.Random(0)) is False
    assert "slave" not in run.revealed_node_ids


def test_analyze_program_never_appears_as_an_in_fight_action():
    """action_analyze is navigation-mode only (analyze_node/usable_analyze_program) --
    it must never show up as a MatrixActionKind.PROGRAM row during a live fight, since
    _use_program has no branch for it and would silently waste the charge and round."""
    program = PROGRAMS_BY_ID["analyze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    state = start_matrix(c, (ICE_BY_ID["watchdog"],), Drop.NONE, random.Random(0))
    actions = available_matrix_actions(c, state.program_uses)
    assert not any(a.kind is MatrixActionKind.PROGRAM and a.program is program for a in actions)


def test_analyze_node_reveals_on_success_and_spends_a_charge():
    program = PROGRAMS_BY_ID["analyze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    run = start_matrix_run(c, _hand_built_network(), Drop.NONE, random.Random(0))
    assert usable_analyze_program(run) is program
    assert analyze_node(run, "slave", AlwaysSix()) is True
    assert "slave" in run.revealed_node_ids
    assert run.analyze_uses[program.id] == program.uses_per_fight - 1


def test_analyze_node_miss_does_not_reveal_but_still_spends_a_charge():
    program = PROGRAMS_BY_ID["analyze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    run = start_matrix_run(c, _hand_built_network(), Drop.NONE, random.Random(0))
    assert analyze_node(run, "slave", AlwaysOne()) is True
    assert "slave" not in run.revealed_node_ids
    assert run.analyze_uses[program.id] == program.uses_per_fight - 1


def test_analyze_node_refuses_once_already_revealed():
    program = PROGRAMS_BY_ID["analyze"]
    c = _char(deck_id="burner_deck", installed_programs=[program.id])
    run = start_matrix_run(c, _hand_built_network(), Drop.NONE, random.Random(0))
    analyze_node(run, "slave", AlwaysSix())
    charges_after_first = run.analyze_uses[program.id]
    assert analyze_node(run, "slave", AlwaysSix()) is False
    assert run.analyze_uses[program.id] == charges_after_first  # no charge spent on the refusal


# --- render_matrix_network -----------------------------------------------------


def test_render_marks_current_node_and_spine_connectors():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    text = render_matrix_network(run)
    lines = text.splitlines()
    assert len(lines) == 1  # no cpu: everything sits in one row
    assert "@[entry ENTRY]" in lines[0]
    # entry->slave->ic->data is the guaranteed chain, so every consecutive pair
    # must render as an adjacent, connected pair of columns.
    assert "[entry ENTRY]" in lines[0] and lines[0].index("[entry") < lines[0].index("[slave")
    assert lines[0].index("[slave") < lines[0].index("[ic")
    assert lines[0].index("[ic") < lines[0].index("[data")


def test_render_reflects_cleared_and_guarded_status():
    program = PROGRAMS_BY_ID["analyze"]
    c = _char(intelligence=6, hack_rank=6, deck_id="zetatech_rig", installed_programs=[program.id])
    run = start_matrix_run(c, _hand_built_network(), Drop.NONE, random.Random(1))
    move_to(run, "slave", random.Random(1))
    move_to(run, "ic", random.Random(1))
    _clear_node(run, random.Random(1))
    analyze_node(run, "data", AlwaysSix())  # reveal data from outside, without fighting it
    text = render_matrix_network(run)
    assert "@[ic IC clear]" in text
    assert "[data DATA guard]" in text  # revealed via analyze, not yet cleared


def test_render_hides_role_and_guard_status_for_unrevealed_nodes():
    run = start_matrix_run(_char(), _hand_built_network(), Drop.NONE, random.Random(0))
    text = render_matrix_network(run)
    assert "@[entry ENTRY]" in text  # entry is always known
    assert "[slave ???]" in text
    assert "[ic ???]" in text  # guarded, but that's part of the hidden "value" too
    assert "[data ???]" in text


def test_render_draws_a_connector_between_data_and_its_cpu_detour():
    # cpu's only neighbour is data, so the barycenter layout lines it up in data's
    # row one column over -- a straight connector, not floating disconnected.
    run = start_matrix_run(_char(), _hand_built_network(with_cpu=True), Drop.NONE, random.Random(0))
    text = render_matrix_network(run)
    data_line = next(line for line in text.splitlines() if "[data" in line and "[cpu" in line)
    assert data_line.index("[data") < data_line.index("[cpu")
    assert "-" in data_line[data_line.index("]", data_line.index("[data")) : data_line.index("[cpu")]


def test_render_spreads_a_branch_across_rows_in_the_same_column():
    network = MatrixNetwork(
        entry_id="entry",
        data_id="data",
        nodes={
            "entry": MatrixNode(id="entry", role=MatrixNodeRole.ENTRY, connections=("a", "b")),
            "a": MatrixNode(id="a", role=MatrixNodeRole.SLAVE, connections=("entry", "data")),
            "b": MatrixNode(id="b", role=MatrixNodeRole.SLAVE, connections=("entry", "data")),
            "data": MatrixNode(id="data", role=MatrixNodeRole.DATA, connections=("a", "b"), ice=WEAK_ICE),
        },
    )
    run = start_matrix_run(_char(), network, Drop.NONE, random.Random(0))
    text = render_matrix_network(run)
    lines = text.splitlines()
    # entry forks into two siblings one hop away -- they can't both sit in entry's
    # row, so the diagram must be more than one line tall to show the fork at all.
    assert len(lines) > 1
    a_line = next(line for line in lines if "[a " in line)
    b_line = next(line for line in lines if "[b " in line)
    assert a_line != b_line


@pytest.mark.parametrize("tier", MATRIX_NETWORK_TIERS.keys())
@pytest.mark.parametrize("seed", SEEDS)
def test_render_never_crashes_and_shows_every_node_on_a_generated_network(tier, seed):
    network = generate_matrix_network(tier, random.Random(seed))
    run = start_matrix_run(_char(), network, Drop.NONE, random.Random(seed))
    text = render_matrix_network(run)
    for node_id in network.nodes:
        assert f"[{node_id} " in text
    assert text.count("@[") == 1
