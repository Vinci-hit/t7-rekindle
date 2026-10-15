from copy import deepcopy
import struct
import pytest
from test_online_actions import running
from test_custom_scene import instance, MATCH


def final_room(client, reason='timeout', winner=3):
    room = client.state['sessions']['1']['customLobby']['room']
    return dict(room, phase='settling', revision=5, result=dict(reason=reason, result=winner,
        endedAtMs=5000, lives=[dict(actorId=m['actorId'], health=100, kills=0, deaths=0, streak=0)
                             for m in room['members']],
        ranks=[dict(actorId=m['actorId'], rank=1) for m in room['members']]))


def test_timeout_displays_frozen_scores_finish_then_returns_to_lobby(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    end = final_room(client)
    result = client.reply(None, 'roomChanged', room=end)
    assert [x['reason'] for x in result['send']][-2:] == ['online-round-result', 'online-round-end']
    assert client.state['settlement']['result'] == 3
    assert client.state['sessions']['2']['roundEnded']
    assert client.state['onlineScene']['phase'] == 4
    assert not instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0)).get('gateway')
    client.context['nowMs'] = client.state['sessions']['2']['pending']['round-finish']
    result = instance(client, kind='timer', name='round-finish')
    assert [x['reason'] for x in result['send']] == ['instance-settlement', 'instance-finish-game']
    result = client.reply(None, 'roomChanged', room=dict(end, phase='waiting', revision=6, currentMatch=None))
    assert any(x['reason'] == 'custom-scene-cancelled-leave' for x in result['send'])
    assert client.state['sessions']['2']['leaving']


def test_kill_target_uses_server_winner_and_rank(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    end = final_room(client, 'kill-target', 2)
    end['result']['lives'][1].update(kills=60)
    client.reply(None, 'roomChanged', room=end)
    members = client.state['settlement']['members']
    assert [m['result'] for m in members] == [2, 1]
    assert members[1]['points'] == 6000


def test_invalid_result_does_not_commit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    end = final_room(client, 'kill-target', 1)
    before = deepcopy(client.state)
    with pytest.raises(ValueError):
        client.reply(None, 'roomChanged', room=end)
    assert client.state == before


def test_early_lobby_notification_publishes_finish_before_leaving(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    end = final_room(client)
    client.reply(None, 'roomChanged', room=end)
    result = client.reply(None, 'roomChanged', room=dict(end, phase='waiting', revision=6, currentMatch=None))
    reasons = [x['reason'] for x in result['send']]
    assert reasons.index('instance-finish-game') < reasons.index('custom-scene-cancelled-leave')
    assert 'round-finish' not in client.state['sessions']['2']['pending']
