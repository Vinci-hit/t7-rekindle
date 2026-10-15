import json
from copy import deepcopy

import pytest

from Business.scripts import online_clock
from test_custom_lobby import Client

EPOCH = '00000000-0000-0000-0000-000000000001'
NONCE = '00000000-0000-0000-0000-000000000002'


def calibrated():
    client = Client()
    client.identify()
    client.context['nowMs'] = 1000
    client.event('timer', name='custom-lobby-ping')
    client.context['nowMs'] = 1050
    result = client.reply(2, 'clockSample', epoch=EPOCH, nonce=NONCE, serverTimeMs=500)
    request = json.loads(next(e['request'] for e in result['gateway'] if e['op'] == 'send'))
    assert request['command']['payload'] == dict(epoch=EPOCH, nonce=NONCE, receivedMs=1050, sentMs=1050)
    client.context['nowMs'] = 1100
    client.reply(3, 'clockReady', epoch=EPOCH, offsetMs=-550, uncertaintyMs=50, roundTripMs=100, samples=1)
    return client


def test_clock_challenge_echo_and_estimate_are_correlated():
    client = calibrated()
    clock = client.state['sessions']['1']['customLobby']['clock']
    assert clock['offsetMs'] == -550 and clock['at'] == 1100
    assert client.state['sessions']['1']['customLobby']['rttMs'] == 50


@pytest.mark.parametrize('change', [dict(epoch=NONCE), dict(samples=0), dict(roundTripMs=2001), dict(offsetMs=True)])
def test_invalid_estimate_does_not_replace_a_valid_clock(change):
    gateway = calibrated().state['sessions']['1']['customLobby']
    before = deepcopy(gateway)
    estimate = dict(epoch=EPOCH, offsetMs=-550, uncertaintyMs=50, roundTripMs=100, samples=1)
    with pytest.raises(ValueError):
        online_clock.accept(gateway, dict(estimate, **change), EPOCH, 1200)
    assert gateway == before


def test_actions_contacts_and_heartbeat_share_sequence_motion_only_observes_it():
    gateway = calibrated().state['sessions']['1']['customLobby']
    a = online_clock.stamp(gateway, dict(type='actionInput'), 1200)
    c = online_clock.stamp(gateway, dict(type='contactInput', payload=dict(contact=dict(timing=dict(sampleTimeMs=1190)))), 1210)
    m = online_clock.stamp(gateway, dict(type='poseInput', payload=dict(pose=dict(motion=dict(tick=1205)))), 1215, motion=True)
    h = online_clock.stamp(gateway, dict(type='clockEcho'), 1220)
    assert [s['combatSeq'] for s in (a, c, m, h)] == [1, 2, 2, 3]
    assert c['sampledMs'] == 1190 and c['gatewayMs'] == 1210
    assert a['source'] == 'gatewayInput' and m['source'] == 'nativePose'


def test_host_queue_stall_preserves_receive_time_in_action_observation():
    gateway = calibrated().state['sessions']['1']['customLobby']
    value = online_clock.stamp(gateway, dict(type='actionInput'), 1280, received_at=1200)
    assert value['sampledMs'] == 1200 and value['gatewayMs'] == 1280


def test_expired_clock_echo_retries_without_disconnect_or_claiming_calibration():
    client = Client()
    client.identify()
    client.context['nowMs'] = 1000
    client.event('timer', name='custom-lobby-ping')
    client.reply(2, 'clockSample', epoch=EPOCH, nonce=NONCE, serverTimeMs=500)
    result = client.reply(3, 'error', code='badRequest')
    assert any('retry' in entry['message'] for entry in result['logs'])
    assert 'clock' not in client.state['sessions']['1']['customLobby']
    client.context['nowMs'] = 2000
    result = client.event('timer', name='custom-lobby-ping')
    assert json.loads(result['gateway'][0]['request'])['command']['type'] == 'clockProbe'


def test_calibrated_heartbeat_passes_runtime_boundary_with_contiguous_timeline():
    client = calibrated()
    client.context['nowMs'] = 2000
    client.event('timer', name='custom-lobby-ping')
    client.context['nowMs'] = 2050
    result = client.reply(4, 'clockSample', epoch=EPOCH, nonce=NONCE, serverTimeMs=1500)
    request = json.loads(next(e['request'] for e in result['gateway'] if e['op'] == 'send'))
    assert request['timeline'] == dict(epoch=EPOCH, combatSeq=1, sampledMs=2050, gatewayMs=2050, source='heartbeat')


@pytest.mark.parametrize('change', [dict(source='nativePose'), dict(combatSeq=0), dict(sampledMs=9999), dict(epoch='bad')])
def test_runtime_rejects_invalid_timeline_fields(change):
    from Business.runtime.host_runtime import validateTransition
    client = calibrated()
    client.context['nowMs'] = 2000
    client.event('timer', name='custom-lobby-ping')
    result = client.reply(4, 'clockSample', epoch=EPOCH, nonce=NONCE, serverTimeMs=1500)
    send = next(e for e in result['gateway'] if e['op'] == 'send')
    request = json.loads(send['request'])
    request['timeline'].update(change)
    send['request'] = json.dumps(request)
    with pytest.raises(ValueError):
        validateTransition(result, client.context)


def test_combat_heartbeat_crosses_runtime_boundary_and_reserves_input_capacity():
    from types import SimpleNamespace
    from Business.runtime.host_runtime import validateTransition
    client = calibrated()
    gateway = client.state['sessions']['1']['customLobby']
    timers = []
    result = dict(state=client.state, send=[], timers=[], logs=[])
    flow = SimpleNamespace(session=client.state['sessions']['1'],
                           state=dict(onlineScene=dict(phase=3)), connection=1,
                           now=1200, receivedAt=1200, result=result,
                           later=lambda name, delay: timers.append((name, delay)))
    online_clock.heartbeat(flow)
    online_clock.heartbeat(flow)
    before = len(gateway['pending'])
    online_clock.heartbeat(flow)
    assert len(gateway['pending']) == before == 2
    requests = [json.loads(item['request']) for item in result['gateway']]
    assert [item['timeline']['combatSeq'] for item in requests] == [1, 2]
    assert all(item['command'] == dict(type='combatHeartbeat') for item in requests)
    assert all(item['timeline']['source'] == 'heartbeat' for item in requests)
    assert timers == [('online-combat-heartbeat', 50)] * 3
    validateTransition(result, client.context)
    client.reply(int(requests[0]['requestId']), 'pong', serverTimeMs=700)


def test_combat_heartbeat_does_not_send_in_lobby_and_stops_after_leave():
    from types import SimpleNamespace
    client = calibrated()
    timers = []
    result = dict(state=client.state, send=[], timers=[], logs=[])
    flow = SimpleNamespace(session=client.state['sessions']['1'], state={}, connection=1,
                           now=1200, receivedAt=1200, result=result,
                           later=lambda name, delay: timers.append((name, delay)))
    online_clock.heartbeat(flow)
    assert not result.get('gateway') and timers
    flow.session['leaving'] = True
    timers.clear()
    online_clock.heartbeat(flow)
    assert not result.get('gateway') and not timers


def test_running_client_dispatches_combat_heartbeat_and_accepts_reply(monkeypatch, spawnPoints):
    from test_online_actions import running
    client = running(monkeypatch, spawnPoints)
    gateway = client.state['sessions']['1']['customLobby']
    gateway['clock'] = dict(epoch=EPOCH, offsetMs=0, uncertaintyMs=5, roundTripMs=10, samples=1, at=3000)
    client.context['nowMs'] = 4000
    result = client.event('timer', name='online-combat-heartbeat')
    request = json.loads(next(item['request'] for item in result['gateway'] if item['op'] == 'send'))
    assert request['command']['type'] == 'combatHeartbeat'
    assert request['timeline']['sampledMs'] == 4000
    reply = client.reply(int(request['requestId']), 'pong', serverTimeMs=4005)
    assert not reply['send']
    assert reply['gateway'][0]['op'] == 'ack'
