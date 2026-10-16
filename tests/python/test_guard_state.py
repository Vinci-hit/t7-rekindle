import struct

import pytest

from Business.scripts import actors, battle, guard_state, damage_state
from Business.scripts.codec import battle_flow
from test_melee_combat import arena


def test_five_equal_blocks_exhaust_without_recovery_and_reset_to_two(arena):
    for index in range(5):
        assert guard_state.consume(arena, 1, 1, 1000 + index * 100) == (index == 4)
        assert arena.session['guardMeter']['value'] == (4 - index if index < 4 else 2)
    packets = [p for p in arena.result['send'] if p['reason'] == 'combat-guard-value']
    assert struct.unpack('>Hf', packets[-1]['body']) == (48, 2.)


def test_recovery_delay_is_measured_from_last_block_not_business_dispatch(arena):
    guard_state.consume(arena, 1, 2, 1000)
    guard_state.advance(arena.session, 3499)
    assert arena.session['guardMeter']['value'] == 3
    guard_state.advance(arena.session, 3750)
    assert arena.session['guardMeter']['value'] == 3.5
    guard_state.consume(arena, 1, 1, 3750)
    guard_state.advance(arena.session, 6250)
    assert arena.session['guardMeter']['value'] == 2.5
    guard_state.advance(arena.session, 7000)
    assert arena.session['guardMeter']['value'] == 4
    guard_state.advance(arena.session, 9000)
    assert arena.session['guardMeter']['value'] == 5


def test_attack_recovery_is_capped_and_new_life_resets_meter(arena):
    guard_state.consume(arena, 1, 2, 1000)
    guard_state.restore(arena, 1, 1100)
    guard_state.restore(arena, 1, 1200)
    assert arena.session['guardMeter']['value'] == 5
    guard_state.consume(arena, 1, 2, 1300)
    damage_state.reset(arena.session)
    assert arena.session['guardMeter'] == dict(value=5, at=0, lastBlockAt=0)


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True])
def test_guard_meter_wire_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        battle_flow.encode_parry_value_notify(value=value)
