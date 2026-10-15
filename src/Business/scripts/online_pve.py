"""Native rendering and entry for server-owned cooperative monsters."""
import math
from functools import lru_cache
import struct
from . import actors, contracts as wire
from .codec import battle_flow, move_flow, vision_flow

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
                state['monsters'][key] = dict(actionSeq=1,poseSeq=0,**monster)
                flow.log('INFO', f'online-pve spawned actor={key} inst={instance_id(int(key))}')
            else:
                state['monsters'][key].update(monster)
        match['pve'] = snapshot
    finally:
        flow.connection, flow.session = connection, session


def action(flow, value, connection, session):
    from .custom_lobby import integer
    from .online_actions import ledger as actions
    state = actions(session)
    sequence = integer(value['eventSeq'],1,0xffffffffffffffff)
    if sequence != state['eventSeq']+1:
        raise ValueError('PVE action gap or replay')
    state['eventSeq'] = sequence
    source = ledger(session)['monsters'].get(str(value['actorId']))
    if source is None:
        return
    seq = integer(value['actionSeq'],1,65535)
    if seq != source['actionSeq']%65535+1 or value['weaponId'] != source['kind']['weaponId']:
        raise ValueError('PVE action changed monster sequence or weapon')
    source['actionSeq'] = seq
    body=battle_flow.encode_battle_state_sync_simple(instance_id=instance_id(value['actorId']),seq_no=seq,
        state=value['state'],state_change_ms=flow.now-session['instanceStartedAt'],state_time_ms=value['durationMs'])
    flow.result['send'].append(dict(connection=connection,command=4,body=body,reason='online-pve-monster-action'))


def pose(flow, value, connection, session):
    from .custom_lobby import integer
    from .online_motion import ledger as motion
    state = motion(session)
    sequence = integer(value['eventSeq'],1,0xffffffffffffffff)
    if sequence != state['eventSeq']+1:
        raise ValueError('PVE pose gap or replay')
    state['eventSeq'] = sequence
    source = ledger(session)['monsters'].get(str(value['actorId']))
    if source is None:
        return
    seq=integer(value['inputSeq'],1,0xffffffff)
    if seq <= source['poseSeq']:
        raise ValueError('PVE pose input replay')
    source['poseSeq'] = seq
    source.update(value['pose'])
    body=move_flow.encode_move_direct_bc(server_tick=flow.now-session['instanceStartedAt'],
        target_instance_id=instance_id(value['actorId']),direction_yaw=value['pose']['heading'],position=tuple(value['pose']['position']))
    flow.result['send'].append(dict(connection=connection,command=2,body=body,reason='online-pve-monster-pose'))


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
