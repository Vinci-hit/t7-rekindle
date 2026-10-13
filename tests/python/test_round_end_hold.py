from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import app, contracts as wire, scene
from Business.scripts.codec import leave_flow
from test_round_settlement import start
from test_round_timeout import clear, finish


@pytest.fixture(params=['timeout', 'kill-target'])
def ending(request):
    flow = start(60000)
    if request.param == 'timeout':
        finish(flow)
    else:
        flow.now += 1000
        flow.session['kills'] = 60
        clear(flow)
        assert scene.checkKillTarget(flow, 1)
    return flow


@pytest.mark.parametrize('lateness', [0, 2500])
def test_native_end_holds_for_five_seconds_before_settlement(ending, lateness):
    flow = ending
    endedAt = flow.now
    snapshot = deepcopy(flow.state['settlement'])
    reasons = [p['reason'] for p in flow.result['send']]
    assert reasons[-2:] == ['instance-round-result', 'instance-round-state-end']
    assert 'instance-settlement' not in reasons and 'instance-finish-game' not in reasons
    assert flow.result['send'][-2]['body'] == struct.pack('>HQQii', 0x68, endedAt, 1, snapshot['result'], 1)
    assert flow.result['send'][-1]['body'] == wire.roundState(endedAt, 5, 5000)
    assert flow.session['pending'] == {'round-finish': endedAt + 5000}
    assert [t for t in flow.result['timers'] if t['delayMs'] >= 0] == [{
        'id': '1:round-finish', 'delayMs': 5000,
        'event': {'type': 'timer', 'connection': 1, 'name': 'round-finish'},
    }]

    clear(flow)
    flow.now = endedAt + 4999
    app.handleTimer(flow, 'round-finish')
    scene.finishRound(flow)
    assert not flow.result['send'] and not flow.result['timers']
    assert flow.session['pending'] == {'round-finish': endedAt + 5000}

    flow.now = endedAt + 5000 + lateness
    app.handleTimer(flow, 'round-finish')
    assert [p['reason'] for p in flow.result['send']] == ['instance-settlement', 'instance-finish-game']
    assert flow.result['send'][-1]['body'] == leave_flow.encode_si_finish_game(
        server_time_ms=flow.now, game_result=snapshot['result'], local_user_id=wire.USER_ID)
    assert flow.state['settlement'] == snapshot and not flow.session['pending']
    assert app.validateState(flow.state)
    clear(flow)
    app.handleTimer(flow, 'round-finish')
    scene.finishRound(flow)
    assert not flow.result['send'] and not flow.result['timers']


def test_reload_preserves_remaining_hold_and_does_not_replay_finish(ending):
    deadline = ending.now + 5000
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(ending.state)))
    flow = app.Flow({'connection': 1}, restored, {'nowMs': deadline - 3000})
    assert flow.session['pending'] == {'round-finish': deadline}
    app.handleTimer(flow, 'round-finish')
    assert not flow.result['send'] and not flow.result['timers']
    flow.now = deadline
    app.handleTimer(flow, 'round-finish')
    assert [p['reason'] for p in flow.result['send']] == ['instance-settlement', 'instance-finish-game']
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(flow.state)))
    result = app.handleEvent({'type': 'timer', 'connection': 1, 'name': 'round-finish'}, restored,
                             {'nowMs': deadline + 1})
    assert not result['send'] and not result['timers']


@pytest.mark.parametrize('elapsed', [0, 4999, 5000, 8000])
def test_manual_leave_during_hold_flushes_result_once_without_changing_winner(ending, elapsed):
    flow = ending
    snapshot = deepcopy(flow.state['settlement'])
    deadline = flow.now + 5000
    flow.now += elapsed
    clear(flow)
    scene.leave(flow, b'\0\2\xff')
    assert [p['reason'] for p in flow.result['send']] == [
        'instance-settlement', 'instance-finish-game', 'instance-leave-instance']
    assert flow.state['settlement'] == snapshot
    assert flow.session['pending'] == {'logout': flow.now + 250}
    assert any(t['id'] == '1:round-finish' and t['delayMs'] == -1 for t in flow.result['timers'])
    ack = flow.session['pending']['logout']
    clear(flow)
    flow.now += 100
    scene.leave(flow, b'\0\2\0')
    assert flow.session['pending'] == {'logout': ack}
    assert not flow.result['send'] and not flow.result['timers']
    flow.now = max(ack, deadline)
    app.handleTimer(flow, 'round-finish')
    app.handleTimer(flow, 'logout')
    assert [p['body'] for p in flow.result['send']] == [
        leave_flow.encode_sync_logout_response(server_time_ms=flow.now, back_data=255)]


def test_new_instance_cancels_old_finish_timer(ending):
    flow = ending
    deadline = flow.now + 5000
    scene.begin(flow)
    assert 'round-finish' not in flow.session['pending']
    assert 'settlement' not in flow.state and 'roundEnded' not in flow.session
    clear(flow)
    flow.now = deadline
    app.handleTimer(flow, 'round-finish')
    assert not flow.result['send'] and not flow.result['timers']


def test_manual_quit_before_round_end_does_not_wait():
    flow = start(60000)
    clear(flow)
    scene.leave(flow, b'\0\2\0')
    assert flow.state['settlement']['reason'] == 'quit'
    assert [p['reason'] for p in flow.result['send']][-4:] == [
        'instance-round-state-end', 'instance-settlement', 'instance-finish-game', 'instance-leave-instance']
    assert flow.session['pending'] == {'logout': flow.now + 250}
    assert not any(t['id'] == '1:round-finish' for t in flow.result['timers'])
