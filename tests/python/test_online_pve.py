import json
import struct
from copy import deepcopy
import pytest
from Business.scripts import online_pve, contracts, online_scene
from Business.scripts.codec import battle_flow, move_flow, room_flow
from test_custom_lobby import Client, room
from test_custom_scene import MATCH, instance, load


def selection(scenario=2017):
    return struct.pack('>HihiiiiiiiiB',1,1,4,0,2,7,0,1,scenario,0,0,1)


def snapshot(monsters=None):
    return dict(scenarioId=2017,instanceId=2017,mapResourceId=10088,phase='intermission',wave=0,totalWaves=3,
        nextWaveAtMs=0,playerSpawns=[[0.,0.,.2]],monsters=monsters or [])


def monster():
    return dict(actorId=online_pve.FIRST_MONSTER_ID,localRid=2,kind=dict(resourceId=11001,heroId=11001,weaponId=11000002,health=60,speed=2.),
        position=[3.,0.,.2],heading=180,alive=True,health=60)


def running():
    client=Client(runtime_movement=True);client.identify()
    result=client.event('message',command=32,body=selection())
    assert json.loads(result['gateway'][0]['request'])['command']['payload']['scenarioId']==2017
    waiting={**room(),'mode':'pve','mapId':2017,'pve':snapshot()}
    client.reply(2,'room',room=waiting)
    loading={**waiting,'phase':'loading','revision':2,'currentMatch':dict(matchId=MATCH,loadDeadlineMs=60000,startsAtMs=None)}
    client.reply(3,'room',room=loading)
    view=dict(roomId=loading['roomId'],matchId=MATCH,mapId=2017,phase='loading',revision=2,mode='pve',pve=snapshot(),
        localActorId=1,actors=[dict(actorId=1,localRid=1,camp=1,isLocal=True)])
    result=client.reply(4,'clientView',view=view)
    assert [v['reason'] for v in result['send']]==['online-pve-match-started']
    assert result['send'][0]['body']==contracts.matchStart(selection())
    result=client.event('message',command=30,body=room_flow.encode_room_enter_request(room_id=1))
    assert struct.pack('>i',2017) in result['send'][0]['body']
    assert struct.pack('>i',10088) not in result['send'][0]['body']
    load(client)
    from Business.scripts import app, pve
    flow=app.Flow({'connection':2},client.state,client.context)
    assert pve.resourceId(flow)==2017
    assert pve.instanceStartPattern(flow)==6
    assert pve.startPatternData(flow)==6
    client.reply(5,'room',room={**loading,'phase':'countdown','revision':3})
    client.reply(None,'roomChanged',room={**loading,'phase':'running','revision':4})
    return client,loading


def update(value,sequence=1):
    return dict(roomId='room-test',matchId=MATCH,eventSeq=sequence,serverTimeMs=3500,snapshot=value)


@pytest.mark.parametrize('entering', [False, True])
def test_pve_damage_does_not_insert_monsters_into_player_scoreboard(entering):
    client,_=running();m=monster()
    if entering:
        m['birth'] = birth()
    client.reply(None,'pveChanged',update=update({**snapshot([m]),'phase':'active','wave':1}))
    lives=[dict(actorId=1,health=100,kills=0,deaths=0,streak=0),dict(actorId=m['actorId'],health=40,kills=0,deaths=0,streak=0)]
    effect=dict(type='contact',attacker=1,target=m['actorId'],actionSeq=3,weaponId=1030411,targetWeaponId=11000002,
        parried=False,damage=20,hitPart='body',position=[3.,0.,1.2],normal=[-1.,0.,0.],direction=[1.,0.,0.],material=0,lives=lives)
    result=client.reply(None,'combatChanged',update=dict(roomId='room-test',matchId=MATCH,eventSeq=1,serverTimeMs=3500,effect=effect))
    assert any(v['reason']=='online-health-monster' for v in result['send'])
    assert client.state['sessions']['2']['onlinePve']['monsters'][str(m['actorId'])]['health']==40
    assert set(client.state['sessions']['2']['onlineCombat']['scores'])=={'1'}
    feedback = next(item for item in result['send'] if item['reason'] == 'online-contact-effect')
    assert feedback['body'][87] == int(entering)
    # Monster bodies carry no native material; the flesh effect needs the body material.
    assert struct.unpack_from('>i', feedback['body'], 47)[0] == 4


@pytest.mark.parametrize('remaining', [40, 0])
def test_monster_hit_effect_precedes_health_and_death(remaining):
    client, _ = running()
    m = monster()
    client.reply(None, 'pveChanged', update=update({**snapshot([m]), 'phase': 'active', 'wave': 1}))
    lives = [dict(actorId=1, health=100, kills=int(remaining == 0), deaths=0, streak=0),
             dict(actorId=m['actorId'], health=remaining, kills=0, deaths=int(remaining == 0), streak=0)]
    effect = dict(type='contact', attacker=1, target=m['actorId'], actionSeq=3,
        weaponId=1030411, targetWeaponId=11000002, parried=False, damage=20,
        hitPart='body', position=[3., 0., 1.2], normal=[-1., 0., 0.],
        direction=[1., 0., 0.], material=0, lives=lives)
    result = client.reply(None, 'combatChanged', update=dict(roomId='room-test', matchId=MATCH,
        eventSeq=1, serverTimeMs=3500, effect=effect))
    packets = result['send']
    hit_index = next(i for i, p in enumerate(packets) if p['reason'] == 'online-contact-effect')
    assert packets[hit_index]['body'][87] == 0
    assert struct.unpack_from('>i', packets[hit_index]['body'], 47)[0] == 4
    assert sum(p['reason'] == 'online-contact-effect' for p in packets) == 1
    assert all(hit_index < i for i, p in enumerate(packets)
               if p['reason'] in ('online-health-monster', 'online-pve-death-stop', 'online-dead'))


def test_pve_failure_is_explicit_and_non_pve_selection_does_not_start_a_room():
    client=Client(runtime_movement=True);client.identify()
    client.event('message',command=32,body=selection())
    result=client.reply(2,'error',code='unsupportedScenario')
    assert result['send'][0]['body']==contracts.matchStart(selection(),error=1)
    body=bytearray(selection());struct.pack_into('>h',body,6,1)
    assert not client.event('message',command=32,body=bytes(body)).get('gateway')
    assert 'onlineScene' not in client.state


def test_pve_changed_resources_and_replayed_lifecycle_are_rejected():
    client,_=running();m=monster();value={**snapshot([m]),'phase':'active','wave':1}
    bad=deepcopy(value);bad['mapResourceId']=1028
    with pytest.raises(ValueError,match='scenario'):
        client.reply(None,'pveChanged',update=update(bad))
    client.reply(None,'pveChanged',update=update(value))
    with pytest.raises(ValueError,match='replay'):
        client.reply(None,'pveChanged',update=update(value))


def test_online_startup_reconnect_probe_is_not_decoded_as_room_entry():
    client=Client(runtime_movement=True);client.identify()
    result=client.event('message',command=30,body=struct.pack('>Hb',10,0))
    assert result['send'][0]['reason']=='room-reconnect-result-nonzero'
    assert 'onlineScene' not in client.state
    assert not result.get('gateway')


def test_guest_login_opens_chapters_without_claiming_rewards_or_completion(monkeypatch):
    from Business.scripts.codec import protocol
    monkeypatch.setattr(online_pve,'load_stage_ids',lambda _: (1,2,3))
    client=Client(runtime_movement=True);client.identify()
    client.context['clientDirectory']='selected-client'
    client.context['nowMs']=1000
    result=client.event('timer',name='login')
    identity=protocol.decode_minimal_login_success(result['send'][0]['body'])
    assert [s.stage_id for s in identity.pve_stages]==[1,2,3]
    assert all(s.unlocked and not s.rewarded and s.completion==0 for s in identity.pve_stages)


def test_pve_progress_codec_checks_counts_flags_and_duplicate_chapters():
    from Business.scripts.codec.pve_flow import StageProgress,encode_progress,decode_progress
    values=(StageProgress(1,True),StageProgress(2,True),StageProgress(3,True))
    encoded=encode_progress(values)
    assert decode_progress(encoded,0)==(values,len(encoded))
    for invalid in [(StageProgress(1,True),StageProgress(1,True)),(StageProgress(0,True),),(StageProgress(1,1),),(StageProgress(1,True,completion=-1),)]:
        with pytest.raises(ValueError):encode_progress(invalid)
    for invalid in [b'\xff\xff',b'\x00\x41',b'\x00\x01',struct.pack('>hibbi',1,1,2,0,0)]:
        with pytest.raises(ValueError):decode_progress(invalid,0)


def test_stage_resource_ids_require_a_complete_bounded_table():
    header=bytearray(140)
    struct.pack_into('<4sIII',header,0,b'MSES',7,4,3)
    struct.pack_into('<I',header,132,140)
    table=bytes(header)+struct.pack('<III',1,2,3)
    assert online_pve.parse_stage_ids(table)==(1,2,3)
    for bad in [table[:-1],table+b'\0',bytes(header)+struct.pack('<III',1,1,3),bytes(header)+struct.pack('<III',0,2,3)]:
        with pytest.raises(ValueError):online_pve.parse_stage_ids(bad)


def test_native_monster_payload_starts_rid_immediately_after_object_type():
    from Business.scripts.codec import vision_flow
    actor=online_pve.FIRST_MONSTER_ID
    body=vision_flow.encode_simple_monster_add_event(rid=actor,resource_id=1200313,hero_id=1200313,
        weapon_id=9010411,position=(1.,2.,3.),direction=(1.,0.,0.),camp=2,server_time_ms=10,
        display_name=b'Zombie',instance_id=1024)
    selector,count,kind=struct.unpack_from('>Hii',body)
    assert (selector,count,kind)==(9,1,3)
    assert struct.unpack_from('>Q',body,10)[0]==actor
    length=struct.unpack_from('>I',body,18)[0]
    assert body[22:22+length]==b'Zombie\0'
    offset=22+length
    assert struct.unpack_from('>Hiii',body,offset)==(1024,1200313,1200313,9010411)
    offset+=14
    assert struct.unpack_from('>6f',body,offset)==(1.,2.,3.,1.,0.,0.)
    assert struct.unpack_from('>iiiI',body,offset+24)==(2,0,0,10)
    assert len(body)==offset+40
    removed=vision_flow.encode_vision_del_event(object_mid=actor,object_type=vision_flow.VISION_OBJECT_MONSTER)
    assert struct.unpack_from('>HiiQ',removed)==(2,1,3,actor)


def test_full_monster_creation_initializes_model_weapon_pose_state_and_health():
    from Business.scripts.codec import vision_flow
    body = vision_flow.encode_monster_vision_add_event(
        rid=0x40000001, resource_id=1100311, hero_id=1100311, weapon_id=9010411,
        position=(3., 4., 5.), heading=90, camp=2, server_time_ms=2**32 + 1,
        instance_id=1024, current_hp=40, maximum_hp=60,
    )
    offset = 0

    def read(fmt):
        nonlocal offset
        value = struct.unpack_from('>' + fmt, body, offset)
        offset += struct.calcsize('>' + fmt)
        return value

    assert read('HiiQ') == (1, 1, 3, 0x40000001)
    name_size, = read('I')
    assert body[offset:offset + name_size] == b'Zombie\0'
    offset += name_size
    assert read('Hi') == (1024, 1100311)
    assert read('ibi8i') == (1100311, 0, 0, *([0] * 8))
    assert read('h') == (1,)
    assert read('iBBB') == (9010411, 1, 0, 1)
    offset += 22  # Bullet info and weapon mode data.
    move = read('bbB10hfffIfffih5b')
    assert move[1] == 1 and move[12] == 90
    assert move[13:16] == (3., 4., 5.)
    assert read('HQhiiQ') == (1, 0, 1, 2, 0, 0)
    assert read('iii') == (40, 60, 100)
    assert read('iii') == (2, 0, 0)
    assert read('ib' * 6) == (0,) * 12
    assert read('iQ') == (0, 2**32 + 1)
    assert offset == len(body)
    with pytest.raises(ValueError, match='health'):
        vision_flow.encode_monster_vision_add_event(
            rid=1, resource_id=1, hero_id=1, weapon_id=1, position=(0., 0., 0.),
            heading=0, camp=2, server_time_ms=0, instance_id=1, current_hp=61, maximum_hp=60,
        )


def birth(animation='emerge1', start=3500):
    return dict(animation=animation, startedAtMs=start, endsAtMs=start+online_pve.BIRTH_STATES[animation][1])


@pytest.mark.parametrize('bad', [dict(animation='unknown',startedAtMs=0,endsAtMs=100),
    dict(animation='emerge1',startedAtMs=0,endsAtMs=1),
    dict(animation='emerge1',startedAtMs=True,endsAtMs=3168)])
def test_invalid_birth_snapshot_is_rejected(bad):
    assert not online_pve.valid_snapshot(snapshot([{**monster(),'birth':bad}]))


def test_objective_snapshot_validation_and_transition_logging():
    client, _ = running()
    objective = dict(encounter=1, text='Reach the gate', targetPosition=[10., 0., .2], active=False)
    waiting = {**snapshot(), 'objective': objective}
    assert online_pve.valid_snapshot(waiting)
    client.reply(None, 'pveChanged', update=update(waiting))
    assert client.state['onlineScene']['pve']['objective'] == objective
    active = {**waiting, 'phase': 'active', 'wave': 1,
              'objective': {**objective, 'active': True, 'text': 'Clear the gate'}}
    client.reply(None, 'pveChanged', update=update(active, 2))
    assert client.state['onlineScene']['pve']['objective']['active'] is True
    for invalid in [
        {**objective, 'encounter': 0}, {**objective, 'encounter': 4},
        {**objective, 'active': True}, {**objective, 'text': ''},
        {**objective, 'text': 'x' * 129}, {**objective, 'text': 'line\nbreak'},
        {**objective, 'targetPosition': [float('nan'), 0., 0.]},
    ]:
        assert not online_pve.valid_snapshot({**waiting, 'objective': invalid})
    assert not online_pve.valid_snapshot({**waiting, 'phase': 'victory'})


def test_story_goal_snapshot_validation():
    value = {**snapshot(), 'phase': 'active', 'wave': 1, 'goal': dict(
        kind='collect', current=2, total=6, targetPosition=[1., 2., 0.], complete=False)}
    assert online_pve.valid_snapshot(value)
    for invalid in (
            {'current': 7}, {'complete': True}, {'kind': 'unknown'},
            {'targetPosition': [float('nan'), 0., 0.]}, {'current': True},
            {'health': 10, 'maximumHealth': 20}):
        assert not online_pve.valid_snapshot({**value, 'goal': {**value['goal'], **invalid}})
    defend = {**value['goal'], 'kind': 'defend', 'current': 100, 'total': 1000,
              'health': 80, 'maximumHealth': 100}
    assert online_pve.valid_snapshot({**value, 'goal': defend})
    assert not online_pve.valid_snapshot({**value, 'goal': {**defend, 'health': 101}})
    assert not online_pve.valid_snapshot({**value, 'phase': 'victory'})


def test_native_objective_is_displayed_once_and_progress_is_not_client_authority():
    client, _ = running()
    value = {**snapshot(), 'phase': 'active', 'wave': 1,
             'objective': dict(encounter=1, text='Collect supplies', targetPosition=[0.,0.,0.], active=True, taskId=123),
             'goal': dict(kind='collect', current=1, total=6, targetPosition=[0.,0.,0.], complete=False)}
    result = client.reply(None, 'pveChanged', update=update(value))
    packet = next(s for s in result['send'] if s['reason'] == 'online-pve-objective')
    assert packet['command'] == 11
    selector, clock, task, tip, current, total = struct.unpack('>HQiiii', packet['body'])
    assert (selector, task, tip, current, total) == (3, 123, 0, 1, 6)
    assert clock >= 0
    result = client.reply(None, 'pveChanged', update=update(value, 2))
    assert not any(s['reason'] == 'online-pve-objective' for s in result['send'])
    value = {**value, 'goal': {**value['goal'], 'current': 2}}
    result = client.reply(None, 'pveChanged', update=update(value, 3))
    assert sum(s['reason'] == 'online-pve-objective' for s in result['send']) == 1
    value = {**snapshot(), 'phase': 'victory', 'wave': 3}
    result = client.reply(None, 'pveChanged', update=update(value, 4))
    packet = next(s for s in result['send'] if s['reason'] == 'online-pve-objective')
    assert struct.unpack('>HQiiii', packet['body'])[2:] == (0, 0, 0, 0)


def test_scene_objects_have_separate_identity_and_idempotent_lifecycle():
    client, _ = running()
    object_actor = online_pve.FIRST_OBJECT_ID
    entry = dict(actorId=object_actor, resourceId=123, position=[1.,0.,0.], health=100)
    value = {**snapshot(), 'phase': 'active', 'wave': 1, 'objects': [entry]}
    result = client.reply(None, 'pveChanged', update=update(value))
    packet = next(s for s in result['send'] if s['reason'] == 'online-pve-object-add')
    assert struct.unpack_from('>HiiQ', packet['body']) == (1, 1, 4, object_actor)
    assert online_pve.object_instance_id(object_actor) == 20000
    assert not online_pve.monster_id(object_actor)
    result = client.reply(None, 'pveChanged', update=update(value, 2))
    assert not result['send']
    moved = {**value, 'objects': [{**entry, 'position': [2.,0.,0.]}]}
    result = client.reply(None, 'pveChanged', update=update(moved, 3))
    assert [p['reason'] for p in result['send']] == ['online-pve-object-move']
    assert struct.unpack_from('>HiH', result['send'][0]['body']) == (11, 1, 20000)
    result = client.reply(None, 'pveChanged', update=update({**value, 'objects': []}, 4))
    assert [p['reason'] for p in result['send']] == ['online-pve-object-remove']
    assert not online_pve.valid_snapshot({**value, 'objects': [entry, entry]})
    assert not online_pve.valid_snapshot({**value, 'objects': [{**entry, 'actorId': online_pve.FIRST_MONSTER_ID}]})


# ---- Monsters are enemy-roster actors on the remote-player pipeline ----

def pose_update(m, seq, at, position, forward=-1, heading=180):
    return dict(roomId='room-test', matchId=MATCH, actorId=m['actorId'], eventSeq=seq, inputSeq=seq,
        serverTimeMs=at, pose=dict(position=position, heading=heading, forwardBack=forward, leftRight=0, running=False))


def monster_action(m, event, seq, state, at=3600, duration=0):
    return dict(roomId='room-test', matchId=MATCH, actorId=m['actorId'], eventSeq=event, actionSeq=seq,
        actionId=None, state=state, weaponId=m['kind']['weaponId'], durationMs=duration, serverTimeMs=at)


def vision_rid_and_instance(body):
    rid, = struct.unpack_from('>Q', body, 10)
    length, = struct.unpack_from('>I', body, 18)
    instance, = struct.unpack_from('>H', body, 22 + length)
    return rid, instance


def test_monster_is_bound_to_its_enemy_slot_and_driven_by_the_peer_pipeline():
    from Business.scripts import online_scene
    client, _ = running(); m = monster()
    result = client.reply(None, 'pveChanged', update=update({**snapshot([m]), 'phase': 'active', 'wave': 1}))
    assert [s['reason'] for s in result['send']] == ['online-pve-monster-add']
    assert vision_rid_and_instance(result['send'][0]['body']) == (2, 2)
    match = client.state['onlineScene']
    assert online_scene.actor_rid(match, m['actorId']) == 2 and online_scene.rid_actor(match, 2) == m['actorId']
    assert online_scene.ids(type('F', (), {'state': client.state})()) == (1, 2)
    peer = client.state['sessions']['2']['onlinePeers']['2']
    assert peer['camp'] == 2 and peer['monster'] == m['actorId'] and peer['speed'] == 2.
    # Poses: the same playback and native gait as a remote player, integrating at the monster speed.
    result = client.reply(None, 'poseChanged', update=pose_update(m, 1, 3500, [2.8, 0., .2]))
    gait = next(s for s in result['send'] if s['reason'] == 'online-peer-movement')
    packet = move_flow.decode_move_bc_with_system_and_active(gait['body'])
    assert packet.target_instance_id == 2 and packet.forward_back == -1000
    assert (packet.current_velocity, packet.max_velocity) == (2000, 25000)
    # Actions: presented on the slot like any enemy player.
    result = client.reply(None, 'actionChanged', action=monster_action(m, 1, 2, 235))
    client.context['nowMs'] = 900
    result = result['send'] or instance(client, kind='timer', name='online-motion-render')['send']
    sync = next(s for s in result if s['command'] == 4)
    assert battle_flow.decode_battle_state_sync_simple(sync['body']).instance_id == 2


def test_freed_slot_is_reused_only_after_the_previous_monster_is_removed():
    client, _ = running(); m = monster()
    client.reply(None, 'pveChanged', update=update({**snapshot([m]), 'phase': 'active', 'wave': 1}))
    client.reply(None, 'poseChanged', update=pose_update(m, 1, 3500, [2.8, 0., .2]))
    nxt = {**monster(), 'actorId': m['actorId'] + 1, 'position': [5., 0., .2]}
    result = client.reply(None, 'pveChanged', update=update({**snapshot([nxt]), 'phase': 'active', 'wave': 1}, 2))
    assert [s['reason'] for s in result['send']] == ['online-pve-monster-remove', 'online-pve-monster-add']
    assert struct.unpack_from('>HiiQ', result['send'][0]['body'])[-1] == 2
    peers = client.state['onlineScene']['peers']
    assert [p['actorId'] for p in peers if p['localRid'] == 2] == [nxt['actorId']]
    # The new actor's pose sequence restarts without being rejected as a replay.
    assert client.reply(None, 'poseChanged', update=pose_update(nxt, 2, 3600, [4.8, 0., .2]))['send']


def test_monster_without_a_slot_is_ignored_rather_than_rejected():
    client, _ = running(); m = monster()
    client.reply(None, 'pveChanged', update=update({**snapshot([]), 'phase': 'active', 'wave': 1}))
    assert not client.reply(None, 'poseChanged', update=pose_update(m, 1, 3500, [2.8, 0., .2]))['send']
    assert not client.reply(None, 'actionChanged', action=monster_action(m, 1, 2, 235))['send']
    assert client.state['sessions']['2']['onlineActions']['eventSeq'] == 1


def test_birth_holds_poses_and_actions_then_hands_the_slot_to_the_peer_pipeline():
    client, _ = running(); m = {**monster(), 'birth': birth()}
    value = {**snapshot([m]), 'phase': 'active', 'wave': 1, 'nextWaveAtMs': None}
    result = client.reply(None, 'pveChanged', update=update(value))
    assert [s['reason'] for s in result['send']] == ['online-pve-monster-add', 'online-pve-birth-mode', 'online-pve-birth-start']
    assert struct.unpack('>HQBQ', result['send'][1]['body'])[:3] == (30, 2, 1)
    assert struct.unpack('>HQiQ', result['send'][2]['body'])[:3] == (29, 2, 200)
    assert not client.reply(None, 'poseChanged', update=pose_update(m, 1, 3600, m['position'], forward=0))['send']
    assert not client.reply(None, 'actionChanged', action=monster_action(m, 1, 2, 35, duration=550))['send']
    finished = {k: v for k, v in m.items() if k != 'birth'}
    result = client.reply(None, 'pveChanged', update=update({**value, 'monsters': [finished]}, 2))
    assert [s['reason'] for s in result['send']] == ['online-pve-birth-end', 'online-pve-birth-position']
    assert struct.unpack('>HQBQ', result['send'][0]['body'])[:3] == (30, 2, 0)
    assert move_flow.decode_move_direct_bc(result['send'][1]['body']).target_instance_id == 2
    assert client.reply(None, 'poseChanged', update=pose_update(m, 2, 6800, [2.8, 0., .2]))['send']


def test_corpse_stays_in_its_slot_until_removed_and_is_never_reanimated():
    client, _ = running(); m = {**monster(), 'birth': birth('vault1')}
    value = {**snapshot([m]), 'phase': 'active', 'wave': 1}
    client.reply(None, 'pveChanged', update=update(value))
    dead = {k: v for k, v in m.items() if k != 'birth'}; dead.update(health=0, alive=False)
    result = client.reply(None, 'pveChanged', update=update({**value, 'monsters': [dead]}, 2))
    from Business.scripts.codec import move_flow
    disabled = next(s for s in result['send'] if s['reason'] == 'online-pve-death-deactivate')
    assert move_flow.decode_move_notify_active(disabled['body']).active == 0
    # Death ends the emergence and plays the native death graph on the slot; the model stays.
    assert [s['reason'] for s in result['send']] == ['online-pve-birth-end', 'online-pve-birth-position', 'online-pve-death-stop', 'online-pve-death-deactivate', 'online-pve-monster-dead']
    assert not client.reply(None, 'poseChanged', update=pose_update(m, 1, 3600, [2.8, 0., .2]))['send']
    assert not client.reply(None, 'actionChanged', action=monster_action(m, 1, 2, 2))['send']
    result = client.reply(None, 'pveChanged', update=update({**value, 'monsters': []}, 3))
    assert [s['reason'] for s in result['send']] == ['online-pve-monster-remove']
    assert not any(p['localRid'] == 2 for p in client.state['onlineScene']['peers'])


def test_reconnect_corpse_is_not_presented():
    client, _ = running()
    m = {**monster(), 'alive': False, 'health': 0}
    result = client.reply(None, 'pveChanged', update=update({**snapshot([m]), 'phase': 'active', 'wave': 1}))
    assert not result['send'] and not client.state['onlineScene']['peers']


@pytest.mark.parametrize('combat_first', [False, True])
def test_death_cancels_buffered_walk_and_native_velocity(combat_first):
    client, _ = running(); m = monster()
    value = {**snapshot([m]), 'phase': 'active', 'wave': 1}
    client.reply(None, 'pveChanged', update=update(value))
    client.reply(None, 'poseChanged', update=pose_update(m, 1, 3500, [2.8, 0., .2]))
    assert '2' in client.state['sessions']['2']['onlineMotion']['buffers']
    if combat_first:
        lives = [dict(actorId=1, health=100, kills=1, deaths=0, streak=1),
                 dict(actorId=m['actorId'], health=0, kills=0, deaths=1, streak=0)]
        effect = dict(type='contact', attacker=1, target=m['actorId'], actionSeq=3,
            weaponId=1030411, targetWeaponId=11000002, parried=False, damage=60, hitPart='body',
            position=[2.8, 0., 1.2], normal=[-1., 0., 0.], direction=[1., 0., 0.], material=0, lives=lives)
        result = client.reply(None, 'combatChanged', update=dict(roomId='room-test', matchId=MATCH,
            eventSeq=1, serverTimeMs=3500, effect=effect))
    else:
        dead = {**m, 'health': 0, 'alive': False}
        result = client.reply(None, 'pveChanged', update=update({**value, 'monsters': [dead]}, 2))
    stop = next(s for s in result['send'] if s['reason'] == 'online-pve-death-stop')
    packet = move_flow.decode_move_bc_with_system_and_active(stop['body'])
    assert (packet.forward_back, packet.left_right, packet.current_velocity, packet.max_velocity) == (0, 0, 0, 0)
    assert result['remoteMotion'][0]['velocity'] == [0., 0., 0.]
    assert '2' not in client.state['sessions']['2']['onlineMotion']['buffers']
    assert not client.reply(None, 'poseChanged', update=pose_update(m, 2, 3550, [2., 0., .2]))['send']
    result = instance(client, kind='timer', name='online-motion-render')
    assert not result['send'] and not result.get('remoteMotion')
    if combat_first:
        dead = {**m, 'health': 0, 'alive': False}
        result = client.reply(None, 'pveChanged', update=update({**value, 'monsters': [dead]}, 2))
        assert not result['send']


def test_snapshot_rejects_monsters_outside_enemy_slots_or_sharing_one():
    assert not online_pve.valid_snapshot(snapshot([{**monster(), 'localRid': 12}]))
    assert not online_pve.valid_snapshot(snapshot([{**monster(), 'localRid': 1}]))
    twin = {**monster(), 'actorId': online_pve.FIRST_MONSTER_ID + 1}
    assert not online_pve.valid_snapshot(snapshot([monster(), twin]))
    assert online_pve.valid_snapshot(snapshot([monster(), {**twin, 'localRid': 11}]))
