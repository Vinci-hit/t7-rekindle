from copy import deepcopy
import json

import pytest

from Business.scripts.codec import move_flow
from test_online_actions import running
from test_custom_scene import MATCH, instance


def sample(client, position=(1.0, 2.0, 0.5), now=100):
    client.context['nowMs'] = now
    client.context['botWorld'] = dict(ready=True, sampledAt=now,
        instanceStartedAt=client.state['sessions']['2']['instanceStartedAt'],
        actors={'1': dict(ready=True, position=list(position), heading=45.0)})
    return client.event('world')


def event(actor=2, sequence=1, event_sequence=1, **pose):
    return dict(roomId='room-test', matchId=MATCH, actorId=actor, inputSeq=sequence,
        eventSeq=event_sequence, serverTimeMs=100,
        pose=dict(position=[1.0, 2.0, 0.5], heading=45, forwardBack=-1, leftRight=0, running=False, **pose))


@pytest.mark.parametrize('reaction', (35, 240, 231, 466, 42))
def test_peer_movement_cannot_overwrite_authoritative_reaction(reaction, monkeypatch, spawnPoints):
    from test_online_actions import action_event
    client = running(monkeypatch, spawnPoints, peer=True)
    client.reply(None, 'actionChanged', action=action_event(actor=2, state=reaction))
    result = client.reply(None, 'poseChanged', update=event())
    assert not any(item['reason'] == 'online-peer-movement' for item in result['send'])
    assert any(item['reason'] == 'online-pose-reset' for item in result['send'])
    client.reply(None, 'actionChanged', action=dict(action_event(actor=2, event=2, sequence=3, state=2), serverTimeMs=100))
    client.context['nowMs'] += 100
    value = event(sequence=2, event_sequence=2)
    value['serverTimeMs'] += 100
    client.reply(None, 'poseChanged', update=value)
    result = instance(client, kind='timer', name='online-motion-render')
    assert any(item['reason'] == 'online-peer-movement' for item in result['send'])


def test_sampling_keeps_twenty_hertz_without_waiting_for_any_reply(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    gateway = client.state['sessions']['1']['customLobby']
    pending = deepcopy(gateway['pending'])
    for sequence, now in enumerate(range(100, 601, 50), 1):
        result = sample(client, (sequence * .1, 2., .5), now)
        effect = result['gateway'][0]
        assert effect['op'] == 'snapshot'
        command = json.loads(effect['request'])['command']
        assert command['type'] == 'poseInput'
        assert command['payload']['inputSeq'] == sequence
        assert client.state['sessions']['1']['customLobby']['pending'] == pending
    assert not sample(client, (2., 2., .5), 620).get('gateway')


def test_peer_positions_and_heading_are_applied_but_local_echo_never_moves_the_player(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'poseChanged', update=event())
    move = move_flow.decode_move_bc_with_system_and_active(result['send'][0]['body'])
    correction = move_flow.decode_move_direct_bc(result['send'][1]['body'])
    assert (move.target_instance_id, move.forward_back, move.state) == (2, -1000, 2)
    assert (move.current_velocity, move.max_velocity) == (3000, 25000)
    assert (correction.target_instance_id, correction.position, correction.direction_yaw) == (2, (1.0, 2.0, 0.5), 45)
    result = client.reply(None, 'poseChanged', update=event(sequence=3, event_sequence=2))
    assert not result['send']
    result = client.reply(None, 'poseChanged', update=event(actor=1, event_sequence=3))
    assert not result['send']


def peer_sample(client, time, position, heading=45, sequence=1):
    client.context['nowMs'] = time
    value = event(sequence=sequence, event_sequence=sequence)
    value['serverTimeMs'] = time - 1000
    value['pose'].update(position=list(position), heading=heading)
    return client.reply(None, 'poseChanged', update=value)


def rendered(client, time):
    client.context['nowMs'] = time
    result = client.event('world')
    return [move_flow.decode_move_direct_bc(p['body']) for p in result['send'] if p['body'][:2] == b'\0\4']


def test_peer_interpolation_uses_server_timeline_and_shortest_yaw_arc(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0), heading=170)
    assert not peer_sample(client, 1050, (1, 0, 1), heading=-170, sequence=2)['send']
    assert rendered(client, 1100)[0].position == (0, 0, 0)
    correction = rendered(client, 1125)[0]
    assert correction.position == (.5, 0, .5) and correction.direction_yaw == -180
    assert not rendered(client, 1130)
    assert rendered(client, 1150)[0].position == (1, 0, 1)
    assert rendered(client, 1200)[0].position == (1, 0, 1)


def test_peer_stall_stops_without_extrapolation_and_large_jump_resets(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0))
    client.context['nowMs'] = 1700
    result = client.event('world')
    move = move_flow.decode_move_bc_with_system_and_active(result['send'][0]['body'])
    assert move.state == 1 and move.current_velocity == 0
    result = peer_sample(client, 1800, (15, 0, 0), sequence=2)
    assert move_flow.decode_move_direct_bc(result['send'][-1]['body']).position == (15, 0, 0)
    assert len(client.state['sessions']['2']['onlineMotion']['buffers']['2']['samples']) == 1


def test_interpolation_buffer_is_bounded_and_discarded_on_exit(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    for sequence in range(1, 50):
        peer_sample(client, 1000 + sequence * 50, (sequence / 10, 0, 0), sequence=sequence)
    assert len(client.state['sessions']['2']['onlineMotion']['buffers']['2']['samples']) == 32
    instance(client, command=0x2c, body=b'\0\2\1')
    assert not rendered(client, 4000)


@pytest.mark.parametrize('field,value', [('actorId', 3),
    ('inputSeq', 0), ('eventSeq', 0)])
def test_unrelated_replayed_and_out_of_order_poses_do_not_commit(field, value, monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    update = event(); update[field] = value
    before = deepcopy(client.state)
    with pytest.raises(ValueError): client.reply(None, 'poseChanged', update=update)
    assert client.state == before


def test_stale_world_and_exit_cannot_send_or_apply_poses(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    sample(client)
    client.context['nowMs'] = 1000
    assert not client.event('world').get('gateway')
    instance(client, command=0x2c, body=b'\0\2\1')
    assert not sample(client, now=1100).get('gateway')
    assert not client.reply(None, 'poseChanged', update=event())['send']


def test_pose_snapshot_does_not_consume_request_capacity(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    pending = client.state['sessions']['1']['customLobby']['pending']
    pending.update({str(i): {'kind': 'view'} for i in range(1, 6)})
    result = sample(client)
    assert result['gateway'][0]['op'] == 'snapshot'
    assert client.state['onlineScene']['phase'] == 3


def test_stationary_jump_keeps_correcting_height_when_the_buffer_briefly_runs_out(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    for seq, time, height in ((1, 1000, .5), (2, 1050, 1.0)):
        client.context['nowMs'] = time
        value = event(sequence=seq, event_sequence=seq)
        value['serverTimeMs'] = time - 1000
        value['pose'].update(position=[0., 0., height], forwardBack=0)
        client.reply(None, 'poseChanged', update=value)
    assert rendered(client, 1150)[0].position == (0, 0, 1)
    assert rendered(client, 1170)[0].position == (0, 0, 1)
    assert rendered(client, 1200)[0].position == (0, 0, 1)
    assert not rendered(client, 1700)


def test_latest_pose_accepts_sequence_gaps_after_transport_coalescing(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.reply(None, 'poseChanged', update=event())
    update = event(sequence=5, event_sequence=8)
    update['serverTimeMs'] = 150
    client.reply(None, 'poseChanged', update=update)
    assert client.state['sessions']['2']['onlineMotion']['eventSeq'] == 8
    before = deepcopy(client.state)
    assert not client.reply(None, 'poseChanged', update=update)['send']
    assert client.state == before


def test_native_motion_snapshot_carries_physical_jump_not_key_guess(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    sample(client)
    client.context['nowMs'] = 150
    world = client.context['botWorld']
    world['sampledAt'] = 150
    world['actors']['1'].update(airborne=True, jumping=True, state=237, phaseAt=125,
                               position=[1.25, 2., .75])
    result = client.event('world')
    frame = json.loads(result['gateway'][0]['request'])['command']['payload']['pose']['motion']
    assert frame['airborne'] and frame['jumping'] and frame['actionId'] == 237
    assert frame['velocity'] == [5., 0., 5.]
    assert frame['actionTick'] <= frame['tick']


def test_remote_playback_uses_sample_tick_despite_server_arrival_jitter(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    for seq, arrived in ((1, 1000), (2, 1150)):
        client.context['nowMs'] = arrived
        update = event(sequence=seq, event_sequence=seq)
        update['serverTimeMs'] = arrived
        update['pose']['motion'] = dict(tick=seq * 50, velocity=[0., 0., 0.],
            airborne=True, jumping=True, actionId=2, actionTick=0)
        client.reply(None, 'poseChanged', update=update)
    buffer = client.state['sessions']['2']['onlineMotion']['buffers']['2']
    assert [sample['at'] for sample in buffer['samples']] == [50, 100]
    assert buffer['delayMs'] == 180


def test_airborne_buffer_shortage_extrapolates_then_holds_without_snapping_back(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    for seq, time, height in ((1, 1000, 0.), (2, 1050, .25)):
        client.context['nowMs'] = time
        value = event(sequence=seq, event_sequence=seq)
        value['pose'].update(position=[0., 0., height], forwardBack=0,
            motion=dict(tick=time-1000, velocity=[0., 0., 5.], airborne=True,
                        jumping=True, actionId=2, actionTick=0))
        client.reply(None, 'poseChanged', update=value)
    assert native_rendered(client, 1150)[0].position == (0, 0, .25)
    assert native_rendered(client, 1200)[0].position == (0, 0, .5)
    assert native_rendered(client, 1300)[0].position == (0, 0, 1.)
    assert native_rendered(client, 1350)[0].position == (0, 0, 1.)


def test_parry_does_not_extrapolate_pre_contact_velocity(monkeypatch, spawnPoints):
    from test_online_actions import action_event
    client = running(monkeypatch, spawnPoints, peer=True)
    for seq, time, x in ((1, 1000, 0.), (2, 1050, .25)):
        client.context['nowMs'] = time
        value = event(sequence=seq, event_sequence=seq)
        value['pose'].update(position=[x, 0., 0.], motion=dict(tick=time-1000,
            velocity=[5., 0., 0.], airborne=False, jumping=False, actionId=2, actionTick=0))
        client.reply(None, 'poseChanged', update=value)
    client.reply(None, 'actionChanged', action=dict(action_event(actor=2, state=240), serverTimeMs=100))
    assert native_rendered(client, 1200)[0].position == (.25, 0, 0)


def test_remote_action_waits_for_the_same_playback_tick_as_position(monkeypatch, spawnPoints):
    from test_online_actions import action_event
    from Business.scripts.codec import battle_flow
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0))
    peer_sample(client, 1050, (.25, 0, 0), sequence=2)
    result = client.reply(None, 'actionChanged', action=dict(action_event(actor=2), serverTimeMs=50))
    assert not result['send']
    assert not any(p['command'] == 4 for p in instance(client, kind='timer', name='online-motion-render')['send'])
    client.context['nowMs'] = 1150
    result = instance(client, kind='timer', name='online-motion-render')
    updates = [p for p in result['send'] if p['command'] == 4]
    assert len(updates) == 1
    assert battle_flow.decode_battle_state_sync_simple(updates[0]['body']).state == 235


def test_native_peer_position_within_tolerance_avoids_competing_correction(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, .5))
    client.context['nowMs'] = 1100
    client.context['botWorld'] = dict(ready=True, sampledAt=1100,
        instanceStartedAt=client.state['sessions']['2']['instanceStartedAt'],
        actors={'2': dict(ready=True, position=[.04, 0, .5], heading=45)})
    assert not rendered(client, 1100)
    client.context['botWorld']['actors']['2']['position'][0] = .2
    client.context['botWorld']['sampledAt'] = 1120
    assert rendered(client, 1120)[0].position == (0, 0, .5)


def test_cross_socket_scene_overtaking_drops_replaceable_poses(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    before = deepcopy(client.state)
    assert not client.reply(None, 'poseChanged', update=dict(event(), matchId='finished-match'))['send']
    assert client.state == before
    client.state['onlineScene']['phase'] = 2
    assert not client.reply(None, 'poseChanged', update=event())['send']


def test_jump_landing_effects_follow_position_playback_and_do_not_target_self(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.context['nowMs'] = 1000
    motion = dict(tick=0, velocity=[0., 0., 5.], airborne=True,
                  jumping=True, actionId=2, actionTick=0)
    value = event(motion=motion)
    result = client.reply(None, 'poseChanged', update=value)
    assert result['remoteMotion'] == [dict(rid=2, instanceStartedAt=client.state['sessions']['2']['instanceStartedAt'],
                                         airborne=True, jumping=True, position=[1., 2., .5], velocity=[0., 0., 5.])]
    client.context['nowMs'] = 1050
    landing = event(sequence=2, event_sequence=2, motion=dict(motion, tick=50, airborne=False, jumping=False))
    assert not client.reply(None, 'poseChanged', update=landing).get('remoteMotion')
    client.context['nowMs'] = 1100
    assert instance(client, kind='timer', name='online-motion-render')['remoteMotion'][0]['jumping']
    client.context['nowMs'] = 1150
    assert not instance(client, kind='timer', name='online-motion-render')['remoteMotion'][0]['jumping']
    client.context['nowMs'] = 1200
    assert not client.reply(None, 'poseChanged', update=event(actor=1, sequence=3, event_sequence=3, motion=motion)).get('remoteMotion')


def test_recoil_keeps_following_direction_changes_and_recovers_without_old_offset(monkeypatch, spawnPoints):
    from test_online_actions import action_event
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0))
    client.reply(None, 'actionChanged', action=dict(action_event(actor=2, state=240), serverTimeMs=0))
    assert rendered(client, 1100)[0].position == (0, 0, 0)
    peer_sample(client, 1150, (0, 1, 0), sequence=2)
    assert rendered(client, 1250)[0].position == (0, 1, 0)
    client.reply(None, 'actionChanged', action=dict(action_event(actor=2, state=2, sequence=3, event=2), serverTimeMs=150))
    assert rendered(client, 1275)[0].position == (0, 1, 0)


def test_airborne_playback_never_restarts_ground_drive_and_landing_restores_it(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    motion = dict(tick=0, velocity=[0., 0., 5.], airborne=True,
                  jumping=True, actionId=2, actionTick=0)
    result = client.reply(None, 'poseChanged', update=event(motion=motion))
    assert not any(p['reason'] == 'online-peer-movement' for p in result['send'])
    assert result['remoteMotion'][0]['jumping']
    client.context['nowMs'] = 1700
    landing = event(sequence=2, event_sequence=2,
                    motion=dict(motion, tick=700, airborne=False, jumping=False))
    result = client.reply(None, 'poseChanged', update=landing)
    assert any(p['reason'] == 'online-peer-movement' for p in result['send'])


def test_remote_hurt_and_recovery_bypass_movement_playback_delay(monkeypatch, spawnPoints):
    from test_online_actions import action_event
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0))
    client.reply(None, 'actionChanged', action=dict(action_event(actor=2), serverTimeMs=0))
    buffer = client.state['sessions']['2']['onlineMotion']['buffers']['2']
    assert buffer['actions']
    result = client.reply(None, 'actionChanged', action=dict(
        action_event(actor=2, sequence=3, event=2, state=35), serverTimeMs=100))
    assert not any('state=35' in p['reason'] for p in result['send'])
    assert '2' in client.state['sessions']['2']['onlineActions']['reacting']
    buffer = client.state['sessions']['2']['onlineMotion']['buffers']['2']
    assert not buffer.get('actions')
    result = client.reply(None, 'actionChanged', action=dict(
        action_event(actor=2, sequence=4, event=3, state=2), serverTimeMs=200))
    assert any('state=2 ' in p['reason'] for p in result['send'])


def native_rendered(client, time):
    from types import SimpleNamespace
    client.context['nowMs'] = time
    result = instance(client, kind='timer', name='online-motion-render')
    assert not any(p['reason'] == 'online-pose-interpolated' for p in result['send'])
    return [SimpleNamespace(position=tuple(v['position'])) for v in result.get('remoteMotion', [])]
