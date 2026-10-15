from copy import deepcopy
import json
import struct

import pytest

from Business.scripts import online_actions
from Business.scripts.codec import battle_flow
from test_custom_scene import instance, MATCH
from test_online_actions import running, action_event
from test_online_combat import event, effect, lives


def payload(client):
    return dict(userId=700, displayName='Player', level=100,
                room=deepcopy(client.state['sessions']['1']['customLobby']['room']),
                recovery=dict(actionSeq=12, combatSeq=17, poseSeq=20, pveSeq=0,
                    actions=[action_event(actor=1, event=12, sequence=3, state=2, action_id=None),
                             action_event(actor=2, event=12, sequence=4, state=224, action_id=None)],
                    poses=[], lives=lives(local=80, peer=60), respawnReady=[]))


def test_disconnect_freezes_inputs_clears_pending_and_keeps_room(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    before = deepcopy(client.state['sessions']['1']['customLobby']['room'])
    instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    result = client.reply(None, 'transportPaused')
    gateway = client.state['sessions']['1']['customLobby']
    assert gateway['paused'] and gateway['pending'] == {} and gateway['room'] == before
    assert [item['op'] for item in result['gateway']] == ['reset']
    assert not client.state['sessions']['2']['groundEnabled']
    assert client.state['sessions']['2']['onlinePrediction']['active'] is None
    result = instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    assert not result.get('gateway') and not result['send']
    for _ in range(10):
        assert not client.event('timer', name='custom-lobby-ping').get('gateway')
    assert gateway['pending'] == {}


def test_recovery_restores_sequences_health_and_guard_without_replaying_contacts(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.reply(None, 'transportPaused')
    result = client.reply(None, 'sessionResumed', **payload(client))
    assert result['gateway'][0]['op'] == 'resume'
    session = client.state['sessions']['2']
    assert session['groundEnabled'] and session['onlineCombat']['health'] == 80
    assert session['onlineActions']['eventSeq'] == 12 and session['onlineCombat']['eventSeq'] == 17
    actions = [battle_flow.decode_battle_state_sync_simple(item['body']) for item in result['send']
               if item['reason'].startswith('online-action')]
    assert [a.state for a in actions] == [2, 224]
    assert not any('contact' in item['reason'] for item in result['send'])
    # The next ordinary stream events follow the recovered baseline, not the old one.
    client.reply(None, 'actionChanged', action=action_event(actor=2, event=13, sequence=5, state=2))
    client.reply(None, 'combatChanged', update=event(effect(), sequence=18))


def test_recovery_rejects_changed_identity_and_unrequested_resume(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    with pytest.raises(ValueError, match='unsolicited'):
        client.reply(None, 'sessionResumed', **payload(client))
    client.reply(None, 'transportPaused')
    value = payload(client); value['userId'] = 999
    before = deepcopy(client.state)
    with pytest.raises(ValueError, match='player id'):
        client.reply(None, 'sessionResumed', **value)
    assert client.state == before


def test_loading_recovery_resends_real_ready_confirmation(monkeypatch, spawnPoints):
    from Business.scripts import online_scene
    from test_custom_scene import start, load
    monkeypatch.setattr(online_scene, 'loadSpawns', lambda _: spawnPoints)
    client, loading, view = start(peer=True)
    client.reply(4, 'clientView', view=view)
    load(client)
    client.reply(None, 'transportPaused')
    result = client.reply(None, 'sessionResumed', userId=700, displayName='Player', level=100,
                          room=loading, recovery=None)
    requests = [json.loads(item['request'])['command']['type']
                for item in result['gateway'] if item['op'] == 'send']
    assert requests == ['loaded']


def test_recovery_applies_missed_death_without_replaying_hit_effects(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.reply(None, 'transportPaused')
    value = payload(client)
    value['recovery']['lives'] = lives(local=0, peer=60)
    value['recovery']['actions'][0]['state'] = 42
    value['recovery']['respawnReady'] = [1]
    result = client.reply(None, 'sessionResumed', **value)
    session = client.state['sessions']['2']
    assert not session['groundEnabled'] and not session['heroChosen']
    assert session['onlineCombat']['ready']
    assert [item['reason'] for item in result['send']].count('online-recovery-dead') == 1
    assert not any(item['reason'] in ('online-contact-effect', 'online-parry-sound') for item in result['send'])


def test_pve_recovery_restores_monsters_and_stream_baselines_without_old_contacts():
    from test_online_pve import running as pve_running, snapshot, monster, update
    from Business.scripts import online_pve
    client, _ = pve_running()
    client.reply(None, 'transportPaused')
    value = payload(client)
    m = monster()
    value['room']['pve'] = {**snapshot([m]), 'phase': 'active', 'wave': 1}
    recovery = value['recovery']
    recovery.update(pveSeq=3, actions=[recovery['actions'][0], dict(
        roomId='room-test', matchId=MATCH, actorId=m['actorId'], eventSeq=12,
        actionSeq=2, actionId=None, state=235, weaponId=m['kind']['weaponId'],
        durationMs=0, serverTimeMs=3500)], poses=[], lives=[
        dict(actorId=1, health=100, kills=0, deaths=0, streak=0),
        dict(actorId=m['actorId'], health=40, kills=0, deaths=0, streak=0)])
    client.reply(None, 'sessionResumed', **value)
    state = client.state['sessions']['2']
    assert state['onlinePve']['eventSeq'] == 3
    assert state['onlinePve']['monsters'][str(m['actorId'])]['health'] == 40
    assert state['onlinePve']['monsters'][str(m['actorId'])]['actionSeq'] == 2
    client.reply(None, 'pveChanged', update=update(value['room']['pve'], 4))
    assert online_pve.instance_id(m['actorId']) == 1024
