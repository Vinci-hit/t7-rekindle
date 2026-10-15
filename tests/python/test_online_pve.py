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
    return dict(actorId=online_pve.FIRST_MONSTER_ID,kind=dict(resourceId=11001,heroId=11001,weaponId=11000002,health=60,speed=2.),
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


def test_pve_native_spawning_does_not_alias_player_instance_and_routes_monster_actions_and_poses():
    client,_=running()
    m=monster();value={**snapshot([m]),'phase':'active','wave':1,'nextWaveAtMs':None}
    result=client.reply(None,'pveChanged',update=update(value))
    assert result['send'][0]['reason']=='online-pve-monster-add'
    rid=m['actorId']
    assert online_pve.instance_id(rid)==1024
    assert online_scene.actor_rid(client.state['onlineScene'],rid)==rid
    assert online_scene.rid_actor(client.state['onlineScene'],rid)==rid
    action=dict(roomId='room-test',matchId=MATCH,actorId=rid,eventSeq=1,actionSeq=2,state=235,weaponId=11000002,durationMs=0,serverTimeMs=3500)
    result=client.reply(None,'actionChanged',action=action)
    assert battle_flow.decode_battle_state_sync_simple(result['send'][0]['body']).instance_id==1024
    pose=dict(position=[2.8,0.,.2],heading=180,forwardBack=-1,leftRight=0,running=False)
    result=client.reply(None,'poseChanged',update=dict(roomId='room-test',matchId=MATCH,actorId=rid,eventSeq=1,inputSeq=1,serverTimeMs=3500,pose=pose))
    assert move_flow.decode_move_direct_bc(result['send'][0]['body']).target_instance_id==1024
    removed={**snapshot(),'phase':'intermission','wave':1,'nextWaveAtMs':5000}
    result=client.reply(None,'pveChanged',update=update(removed,2))
    assert result['send'][0]['reason']=='online-pve-monster-remove'
    assert not client.state['sessions']['2']['onlinePve']['monsters']


def test_pve_damage_does_not_insert_monsters_into_player_scoreboard():
    client,_=running();m=monster()
    client.reply(None,'pveChanged',update=update({**snapshot([m]),'phase':'active','wave':1}))
    lives=[dict(actorId=1,health=100,kills=0,deaths=0,streak=0),dict(actorId=m['actorId'],health=40,kills=0,deaths=0,streak=0)]
    effect=dict(type='contact',attacker=1,target=m['actorId'],actionSeq=3,weaponId=1030411,targetWeaponId=11000002,
        parried=False,damage=20,hitPart='body',position=[3.,0.,1.2],normal=[-1.,0.,0.],direction=[1.,0.,0.],material=0,lives=lives)
    result=client.reply(None,'combatChanged',update=dict(roomId='room-test',matchId=MATCH,eventSeq=1,serverTimeMs=3500,effect=effect))
    assert any(v['reason']=='online-health-monster' for v in result['send'])
    assert client.state['sessions']['2']['onlinePve']['monsters'][str(m['actorId'])]['health']==40
    assert set(client.state['sessions']['2']['onlineCombat']['scores'])=={'1'}


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
