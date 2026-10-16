"""Native rendering and entry for server-owned cooperative monsters."""
import math
from functools import lru_cache
import struct
from . import actors, contracts as wire
from .codec import attr_flow, battle_flow, move_flow, vision_flow

FIRST_MONSTER_ID = 0x40000001
MAX_MONSTERS = 64
MAX_WAVES = 32
FIRST_MONSTER_INSTANCE = 1024


def monster_id(value):
    return type(value) is int and FIRST_MONSTER_ID <= value < FIRST_MONSTER_ID + MAX_WAVES * MAX_MONSTERS


def instance_id(actor):
    if not monster_id(actor):
        raise ValueError('invalid PVE monster identity')
    return FIRST_MONSTER_INSTANCE + actor - FIRST_MONSTER_ID


def valid_snapshot(value):
    if type(value) is not dict:
        return False
    try:
        from .custom_lobby import integer
        for key in ('scenarioId', 'instanceId', 'mapResourceId'):
            integer(value[key], 1)
        integer(value['totalWaves'], 1, MAX_WAVES)
        integer(value['wave'], 0, value['totalWaves'])
        if value['phase'] not in ('intermission', 'active', 'victory', 'defeat'):
            return False
        if (type(value['playerSpawns']) is not list or not 1 <= len(value['playerSpawns']) <= MAX_MONSTERS
                or not all(actors.validPosition(point) for point in value['playerSpawns'])
                or type(value['monsters']) is not list or len(value['monsters']) > MAX_MONSTERS):
            return False
        seen = set()
        for monster in value['monsters']:
            actor = monster['actorId']
            kind = monster['kind']
            if not monster_id(actor) or actor in seen or type(kind) is not dict:
                return False
            seen.add(actor)
            for key in ('resourceId', 'heroId', 'weaponId'):
                integer(kind[key], 1)
            integer(kind['health'], 1, 65535)
            integer(monster['health'], 0, kind['health'])
            integer(monster['heading'], -180, 180)
            if (not actors.validPosition(monster['position']) or type(monster['alive']) is not bool
                    or monster['alive'] != (monster['health'] > 0)):
                return False
        return True
    except (KeyError, TypeError, ValueError):
        return False


def valid(session):
    value = session.get('onlinePve')
    if value is None:
        return True
    return (session['role'] == 'instance' and type(value) is dict
        and type(value.get('eventSeq')) is int and 0 <= value['eventSeq'] <= 0xffffffffffffffff
        and type(value.get('monsters')) is dict and len(value['monsters']) <= MAX_MONSTERS
        and all(type(key) is str and key.isascii() and key.isdecimal() and monster_id(int(key))
            and type(entry) is dict and type(entry.get('actionSeq')) is int and 1 <= entry['actionSeq'] <= 65535
            and type(entry.get('poseSeq')) is int and 0 <= entry['poseSeq'] <= 0xffffffff
            for key, entry in value['monsters'].items()))


def ledger(session):
    return session.setdefault('onlinePve', {'eventSeq': 0, 'monsters': {}})


def request(flow, body):
    """Read the counted match selection; only explicit one-map PVE requests pass."""
    if type(body) is not bytes or not 37 <= len(body) <= 2100:
        raise ValueError('invalid native match request')
    match_type = struct.unpack_from('>h', body, 6)[0]
    if match_type != 4:
        return False
    count = struct.unpack_from('>i', body, 24)[0]
    if not 1 <= count <= 256 or len(body) < 37 + count*4:
        raise ValueError('PVE entry requires explicit selected instances')
    scenarios = struct.unpack_from('>'+'i'*count,body,28)
    excluded = struct.unpack_from('>i', body, 28+count*4)[0]
    if not 0 <= excluded <= 256 or len(body) != 37 + (count+excluded)*4 or any(id<=0 for id in scenarios):
        raise ValueError('invalid PVE match selection')
    scenario = scenarios[0]
    from . import custom_lobby
    gateway = flow.session['customLobby']
    if gateway.get('room') is not None or any(v['kind'] in ('pve-create', 'pve-start', 'view') for v in gateway['pending'].values()):
        flow.send(0x20, wire.matchStart(body, error=1), 'online-pve-entry-busy')
        return True
    custom_lobby.send(flow, 'pve-create', {'type': 'createPveRoom', 'payload': {
        'name': flow.state['playerName'][:24] + ' PVE', 'scenarioId': scenario}}, matchBody=body, scenarioId=scenario)
    return True


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state.get('onlineScene')
    room = flow.session['customLobby'].get('room')
    if match is None or 'pve' not in match or value['matchId'] != match['matchId'] or value['roomId'] != room['roomId']:
        raise ValueError('PVE event belongs to another scene')
    if match['phase'] == 4:
        return
    snapshot = value['snapshot']
    if not valid_snapshot(snapshot) or any(snapshot[k] != match['pve'][k] for k in ('scenarioId', 'instanceId', 'mapResourceId', 'playerSpawns', 'totalWaves')):
        raise ValueError('PVE event changed the allocated scenario')
    instances = [(int(k),s) for k,s in flow.state['sessions'].items() if s['role']=='instance'
        and s.get('initialized') and not s.get('leaving') and int(k) in flow.context.get('connections',[]) ]
    if len(instances) != 1 or match['phase'] != 3:
        raise ValueError('PVE event requires one running instance')
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = instances[0]
        state = ledger(flow.session)
        sequence = integer(value['eventSeq'],1,0xffffffffffffffff)
        if sequence != state['eventSeq']+1:
            raise ValueError('PVE lifecycle gap or replay')
        state['eventSeq'] = sequence
        current = {str(m['actorId']):m for m in snapshot['monsters']}
        for key in tuple(state['monsters']):
            if key not in current:
                flow.send(14, vision_flow.encode_vision_del_event(object_mid=int(key),object_type=vision_flow.VISION_OBJECT_MONSTER), 'online-pve-monster-remove')
                del state['monsters'][key]
        for key, monster in current.items():
            if key not in state['monsters']:
                kind = monster['kind']
                yaw = math.radians(monster['heading'])
                flow.send(14, vision_flow.encode_simple_monster_add_event(rid=int(key),
                    instance_id=instance_id(int(key)), resource_id=kind['resourceId'], hero_id=kind['heroId'],
                    weapon_id=kind['weaponId'], position=tuple(monster['position']), direction=(math.cos(yaw),-math.sin(yaw),0.),
                    camp=2,server_time_ms=flow.now,display_name=b'Zombie'), 'online-pve-monster-add')
                # The monster add carries no hit points: without an explicit
                # update the fresh native unit keeps the zero default.
                flow.send(15, attr_flow.encode_health_update(entity_rid=int(key),
                    health=monster['health'], server_time_ms=flow.now), 'online-pve-monster-health')
                state['monsters'][key] = dict(actionSeq=1,poseSeq=0,**monster)
                flow.log('INFO', f'online-pve spawned actor={key} inst={instance_id(int(key))}')
            else:
                state['monsters'][key].update(monster)
        match['pve'] = snapshot
    finally:
        flow.connection, flow.session = connection, session


def action(flow, value, connection, session):
    from .custom_lobby import integer
    from .online_actions import REACTION_STATES, ledger as actions
    state = actions(session)
    sequence = integer(value['eventSeq'],1,0xffffffffffffffff)
    if sequence != state['eventSeq']+1:
        raise ValueError('PVE action gap or replay')
    state['eventSeq'] = sequence
    actor = value['actorId']
    if ledger(session)['monsters'].get(str(actor)) is None:
        return
    buffer = session.get('onlineMotion', {}).get('buffers', {}).get(str(actor), {})
    # Ordinary monster actions share the pose playback clock so the animation
    # cannot run ahead of the interpolated body; reactions still play at once.
    if buffer.get('samples') and value['state'] not in REACTION_STATES:
        queued = buffer.setdefault('actions', [])
        if len(queued) >= 64:
            raise ValueError('monster action playback queue overflow')
        queued.append(value)
        from . import online_motion
        online_motion.schedule_render(flow, connection, session)
        return
    buffer.pop('actions', None)
    present_action(flow, connection, session, actor, value)


def present_action(flow, connection, session, actor, value):
    from .custom_lobby import integer
    source = ledger(session)['monsters'].get(str(actor))
    if source is None:
        return
    seq = integer(value['actionSeq'],1,65535)
    if seq != source['actionSeq']%65535+1 or value['weaponId'] != source['kind']['weaponId']:
        raise ValueError('PVE action changed monster sequence or weapon')
    source['actionSeq'] = seq
    body=battle_flow.encode_battle_state_sync_simple(instance_id=instance_id(actor),seq_no=seq,
        state=value['state'],state_change_ms=flow.now-session['instanceStartedAt'],state_time_ms=value['durationMs'])
    flow.result['send'].append(dict(connection=connection,command=4,body=body,reason='online-pve-monster-action'))


def pose(flow, actor, pose, connection, session, server_time, sequence):
    """Buffer one authoritative monster pose on the shared playback clock."""
    from . import online_playback
    from .online_motion import MAX_SAMPLES, STALE_MS, ledger as motion, schedule_render
    source = ledger(session)['monsters'].get(str(actor))
    if source is None or sequence <= source['poseSeq']:
        return
    source['poseSeq'] = sequence
    source.update(pose)
    buffer = motion(session).setdefault('buffers', {}).setdefault(str(actor), {})
    samples = buffer.setdefault('samples', [])
    if samples and server_time < samples[-1]['at']:
        return
    buffer['clockOffset'] = min(buffer.get('clockOffset', flow.now - server_time), flow.now - server_time)
    online_playback.observe(buffer, server_time, flow.now)
    sample = dict(at=server_time, pose=pose)
    if not samples or server_time - samples[-1]['at'] > STALE_MS or math.dist(
            pose['position'], samples[-1]['pose']['position']) > 8:
        samples[:] = [sample]
        buffer.pop('peerMovement', None)
        buffer.pop('renderAt', None)
        buffer.pop('playbackAt', None)
        emit(flow, connection, session, actor, pose, buffer, 'online-pve-monster-pose')
    else:
        samples.append(sample)
        del samples[:-MAX_SAMPLES]
    schedule_render(flow, connection, session)


def emit(flow, connection, session, actor, pose, state, reason):
    """Drive one native monster model from a pose on the playback clock."""
    from . import controls
    tick = flow.now - session['instanceStartedAt']
    if not 0 <= tick <= controls.MAX_MOVE_TICK:
        raise ValueError('online pose time outside instance')
    target = instance_id(actor)
    forward, lateral, running = pose['forwardBack'], pose['leftRight'], pose['running']
    heading = pose['heading']
    movement = (forward, lateral, running, heading)
    if state.get('peerMovement') != list(movement):
        state['peerMovement'] = list(movement)
        moving = forward != 0 or lateral != 0
        walk = (controls.GROUND_RUN_STATES if running else controls.GROUND_WALK_STATES).get((forward, lateral), 1)
        body = move_flow.encode_move_bc_with_system_and_active(server_tick=tick, target_instance_id=target,
            position=tuple(pose['position']), direction_yaw=heading, state=walk,
            forward_back=forward * 1000, left_right=lateral * 1000,
            current_velocity=(5000 if running else 3000) if moving else 0,
            max_velocity=25000 if moving else 0, system_group=2, active=1, acceleration=0)
        flow.result['send'].append(dict(connection=connection, command=2, body=body,
            reason='online-pve-monster-movement'))
    if state.get('renderedPose') == pose:
        return
    state['renderedPose'] = pose
    body = move_flow.encode_move_direct_bc(server_tick=tick, target_instance_id=target,
        direction_yaw=heading, position=tuple(pose['position']))
    flow.result['send'].append(dict(connection=connection, command=2, body=body, reason=reason))


STAGE_RESOURCE_PATH = 'data/table/s_res_pve_stage_cli.bin'


def parse_stage_ids(data):
    if type(data) is not bytes or len(data) < 140:
        raise ValueError('truncated PVE stage table')
    magic,version,width,count=struct.unpack_from('<4sIII',data)
    if (magic!=b'MSES' or version!=7 or not 4<=width<=4096 or not 1<=count<=64
            or struct.unpack_from('<I',data,132)[0]!=140 or len(data)!=140+width*count):
        raise ValueError('unsupported PVE stage table')
    ids=tuple(struct.unpack_from('<I',data,140+i*width)[0] for i in range(count))
    if len(set(ids))!=len(ids) or any(not 1<=id<=0x7fffffff for id in ids):
        raise ValueError('invalid PVE stage identities')
    return ids


@lru_cache(maxsize=1)
def load_stage_ids(client_directory):
    from .vfs_resource import readResource
    return readResource(client_directory,'data1.vfs',STAGE_RESOURCE_PATH,parse_stage_ids)


def unlocked_stages(flow):
    from .codec.pve_flow import StageProgress
    directory=flow.context.get('clientDirectory')
    if 'roomGateway' not in flow.context or not directory:
        return ()
    # This guest profile opens chapter selection; it does not award rewards or
    # pretend that original story objectives have been completed.
    return tuple(StageProgress(id,True) for id in load_stage_ids(directory))
