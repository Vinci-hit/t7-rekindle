from copy import deepcopy
import json
import struct

import pytest

from Business.scripts import online_scene, online_actions
from Business.scripts.codec import battle_flow
from test_custom_scene import start, load, instance, MATCH


def running(monkeypatch, spawnPoints, peer=False):
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start(peer)
    client.reply(4, 'clientView', view=view)
    load(client)
    client.reply(5, 'room', room={**loading, 'phase': 'countdown', 'revision': 3})
    client.reply(None, 'roomChanged', room={**loading, 'phase': 'running', 'revision': 4})
    return client


def action_event(actor=1, event=1, sequence=2, state=235, action_id=1):
    return dict(roomId='room-test', matchId=MATCH, actorId=actor, eventSeq=event,
                actionSeq=sequence, actionId=action_id, state=state, serverTimeMs=3000, weaponId=1030411, durationMs=0)


@pytest.mark.parametrize('intent', online_actions.INTENTS)
def test_inputs_predict_immediately_and_matching_authority_does_not_restart_animation(intent, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    result = instance(client, command=4, body=struct.pack('>HIib', 1, (1 << 26) | (1 << 21) if intent == 700250 else 0, intent, 0))
    predicted = result['send']
    assert bool(predicted) == (intent not in (700250, 300020, 300620, 300130, 300140))
    request = result['gateway'][0]
    assert request['connection'] == 1
    assert json.loads(request['request'])['command'] == {'type': 'actionInput', 'payload': {
        'matchId': MATCH, 'inputSeq': 1, 'intent': online_actions.INTENTS[intent]}}
    assert not client.reply(6, 'actionAccepted', matchId=MATCH, inputSeq=1)['send']
    expected = battle_flow.decode_battle_state_sync_simple(predicted[0]['body']).state if predicted else 235
    result = client.reply(None, 'actionChanged', action=action_event(state=expected))
    if predicted:
        assert not result['send']
        return
    assert len(result['send']) == 1 and result['send'][0]['connection'] == 2
    update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
    assert (update.instance_id, update.seq_no, update.state) == (1, 2, 235)
    assert 'battle-action-phase' not in client.state['sessions']['2']['pending']


def test_peer_action_mapping_and_actor_sequences_are_independent(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'actionChanged', action=action_event(actor=2, state=308))
    update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
    assert (update.instance_id, update.seq_no, update.state) == (2, 2, 308)
    result = client.reply(None, 'actionChanged', action=action_event(event=2))
    assert battle_flow.decode_battle_state_sync_simple(result['send'][0]['body']).instance_id == 1


@pytest.mark.parametrize('field,value', [('matchId', 'other'), ('roomId', 'other'), ('actorId', 2),
                                        ('eventSeq', 2), ('actionSeq', 3), ('state', 999)])
def test_mismatched_or_missing_events_do_not_commit(field, value, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    event = {**action_event(), field: value}
    before = deepcopy(client.state)
    with pytest.raises(ValueError):
        client.reply(None, 'actionChanged', action=event)
    assert client.state == before


def test_old_scene_input_and_events_cannot_resume_combat_after_exit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    instance(client, command=0x2c, body=struct.pack('>Hb', 2, 1))
    result = instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    assert not result.get('gateway') and not result['send']
    assert not client.reply(None, 'actionChanged', action=action_event())['send']


def test_pending_action_rejection_after_cancellation_preserves_exit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    instance(client, command=0x2c, body=struct.pack('>Hb', 2, 1))
    due = client.state['sessions']['2']['pending']['logout']
    result = client.reply(6, 'error', code='wrongPhase')
    assert result['gateway'] == [{'connection': 1, 'op': 'ack', 'requestId': '6', 'request': ''}]
    assert client.state['sessions']['2']['pending']['logout'] == due


def test_weapon_request_acknowledgement_and_broadcast_update_equipment(monkeypatch, spawnPoints):
    from Business.scripts.codec import weapon_flow
    client = running(monkeypatch, spawnPoints, peer=True)
    result = instance(client, command=5, body=struct.pack('>Hh', 1, 13))
    request = json.loads(result['gateway'][0]['request'])
    assert request['command']['payload']['intent'] == 'toggleFists'
    result = client.reply(6, 'actionAccepted', matchId=MATCH, inputSeq=1, weaponId=100000)
    assert result['send'][0]['body'] == weapon_flow.encode_change_response(0, 100000)
    event = dict(action_event(state=1), weaponId=100000)
    result = client.reply(None, 'actionChanged', action=event)
    assert result['send'][0]['command'] == 5
    assert client.state['sessions']['2']['weaponId'] == 100000
    event = dict(action_event(actor=2, event=2, state=1), weaponId=100000)
    client.reply(None, 'actionChanged', action=event)
    assert client.state['sessions']['2']['onlinePeers']['2']['weaponId'] == 100000


def test_dash_duration_is_forwarded_to_native_animation(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    event = dict(action_event(state=439), durationMs=2033)
    result = client.reply(None, 'actionChanged', action=event)
    update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
    assert update.state_time_ms == 2033
    assert client.state['sessions']['2']['onlineActions']['reacting'] == ['1']


def test_remote_guard_and_release_bypass_motion_queue_and_discard_stale_actions(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    session = client.state['sessions']['2']
    from Business.scripts import online_motion
    buffer = online_motion.ledger(session)['buffers'].setdefault('2', {})
    buffer.update(clockOffset=0, samples=[dict(at=3000, pose=dict(position=[1., 0., .5],
        heading=180, forwardBack=0, leftRight=0, running=False))],
        actions=[action_event(actor=2, state=235)])
    for index, state in enumerate((308, 224, 228, 2), 1):
        result = client.reply(None, 'actionChanged', action=action_event(
            actor=2, event=index, sequence=index + 1, state=state))
        update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
        assert update.state == state
        current = client.state['sessions']['2']['onlineMotion']['buffers']['2']
        assert not current.get('actions')
        assert current['guardPresented'] == (state != 2)


@pytest.mark.parametrize('actor', [1, 2])
def test_hurt_state_explicitly_presents_once_and_retains_recovery_bookkeeping(actor, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    outcome = client.reply(None, 'actionChanged', action=action_event(actor=actor, state=35))
    assert len(outcome['send']) == 1
    assert battle_flow.decode_battle_state_sync_simple(outcome['send'][0]['body']).state == 35
    assert str(actor) in client.state['sessions']['2']['onlineActions']['reacting']
    result = client.reply(None, 'actionChanged', action=action_event(actor=actor, event=2, sequence=3, state=2))
    assert len(result['send']) == 1
    assert battle_flow.decode_battle_state_sync_simple(result['send'][0]['body']).state == 2
