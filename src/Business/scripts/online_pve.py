"""Native rendering and entry for server-owned cooperative monsters."""
from functools import lru_cache
import struct
from . import actors, contracts as wire, controls
from .codec import attr_flow, battle_flow, casual_flow, move_flow, vision_flow, objective_flow

FIRST_MONSTER_ID = 0x40000001
FIRST_OBJECT_ID = 0x50000001
MAX_MONSTERS = 64
MAX_WAVES = 128
FIRST_MONSTER_INSTANCE = 1024
BIRTH_STATES = {'emerge1': (200, 3167), 'emerge2': (201, 3167), 'emerge3': (202, 3167),
                'vault1': (203, 1800), 'vault2': (204, 3433)}


def monster_id(value):
    return type(value) is int and FIRST_MONSTER_ID <= value < FIRST_MONSTER_ID + MAX_WAVES * MAX_MONSTERS


def instance_id(actor):
    if not monster_id(actor):
        raise ValueError('invalid PVE monster identity')
    return FIRST_MONSTER_INSTANCE + actor - FIRST_MONSTER_ID


def object_id(actor):
    return type(actor) is int and FIRST_OBJECT_ID <= actor < FIRST_OBJECT_ID + MAX_WAVES * 64


def object_instance_id(actor):
    if not object_id(actor):
        raise ValueError('invalid PVE scene object identity')
    return 20000 + actor - FIRST_OBJECT_ID


def valid_birth(birth):
    if type(birth) is not dict or set(birth) != {'animation', 'startedAtMs', 'endsAtMs'}:
        return False
    animation = birth['animation']
    return (type(animation) is str and animation in BIRTH_STATES
        and type(birth['startedAtMs']) is int and 0 <= birth['startedAtMs'] < 2**64
        and type(birth['endsAtMs']) is int and birth['endsAtMs'] < 2**64
        and birth['endsAtMs'] - birth['startedAtMs'] == BIRTH_STATES[animation][1])


def finish_birth(flow, source, connection, session=None):
    if source.get('birth') is None:
        return
    flow.result['send'].append(dict(connection=connection, command=4,
        body=casual_flow.encode_no_fight(actor_id=source['actorId'], enabled=False, server_time_ms=flow.now),
        reason='online-pve-birth-end'))
    source['birth'] = None
    flow.result['send'].append(dict(connection=connection, command=2,
        body=move_flow.encode_move_direct_bc(
            server_tick=flow.now-(flow.session if session is None else session)['instanceStartedAt'],
            target_instance_id=instance_id(source['actorId']),
            direction_yaw=source['heading'], position=tuple(source['position'])),
        reason='online-pve-birth-position'))


def valid_snapshot(value):
    if type(value) is not dict:
        return False
    try:
        from .custom_lobby import integer
        for key in ('scenarioId', 'instanceId', 'mapResourceId'):
            integer(value[key], 1)
        integer(value['totalWaves'], 1, MAX_WAVES)
        integer(value['wave'], 0, value['totalWaves'])
        objects = value.get('objects', [])
        if type(objects) is not list or len(objects) > 64:
            return False
        object_ids = set()
        for entry in objects:
            if (type(entry) is not dict or set(entry) != {'actorId', 'resourceId', 'position', 'health'}
                    or not object_id(entry['actorId']) or entry['actorId'] in object_ids
                    or not actors.validPosition(entry['position'])):
                return False
            object_ids.add(entry['actorId'])
            integer(entry['resourceId'], 1, 0x7fffffff)
            integer(entry['health'], 0, 65535)
        goal = value.get('goal')
        if goal is not None:
            if (type(goal) is not dict
                    or set(goal) not in ({'kind', 'current', 'total', 'targetPosition', 'complete'},
                        {'kind', 'current', 'total', 'targetPosition', 'complete', 'health', 'maximumHealth'})
                    or goal['kind'] not in ('reach', 'collect', 'escort', 'defend')
                    or not actors.validPosition(goal['targetPosition'])
                    or type(goal['complete']) is not bool
                    or value['phase'] not in ('active', 'defeat')):
                return False
            integer(goal['total'], 1, 600000 if goal['kind'] == 'defend' else 16384)
            integer(goal['current'], 0, goal['total'])
            if goal['complete'] != (goal['current'] == goal['total']):
                return False
            if goal['kind'] == 'defend':
                integer(goal['maximumHealth'], 1, 65535)
                integer(goal['health'], 0, goal['maximumHealth'])
            elif 'health' in goal:
                return False
        objective = value.get('objective')
        if objective is not None:
            if (type(objective) is not dict
                    or set(objective) not in ({'encounter', 'text', 'targetPosition', 'active'},
                        {'encounter', 'text', 'targetPosition', 'active', 'taskId'})
                    or type(objective['text']) is not str
                    or not objective['text'].strip() or len(objective['text']) > 128
                    or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in objective['text'])
                    or not actors.validPosition(objective['targetPosition'])
                    or type(objective['active']) is not bool):
                return False
            integer(objective['encounter'], 1, value['totalWaves'])
            if 'taskId' in objective:
                integer(objective['taskId'], 1, 0x7fffffff)
            if (value['phase'] not in ('intermission', 'active')
                    or objective['active'] != (value['phase'] == 'active')):
                return False
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
            if monster.get('birth') is not None and (not monster['alive'] or not valid_birth(monster['birth'])):
                return False
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


def present_objective(flow, state, snapshot):
    objective = snapshot.get('objective')
    task = objective.get('taskId', 0) if objective else 0
    goal = snapshot.get('goal')
    current = total = 0
    if task and goal:
        current, total = goal['current'], goal['total']
        if goal['kind'] == 'defend':
            current, total = current // 1000, (total + 999) // 1000
            if goal['complete']:
                current = total
    presentation = [task, current, total]
    previous = state.get('taskPresentation')
    if presentation == previous or (not task and previous is None):
        return
    flow.send(objective_flow.COMMAND, objective_flow.encode_task(task_id=task,
        current=current, total=total, server_time_ms=flow.now), 'online-pve-objective')
    state['taskPresentation'] = presentation


def present_objects(flow, state, snapshot):
    previous = state.setdefault('objects', {})
    current = {str(entry['actorId']): entry for entry in snapshot.get('objects', [])}
    for key in tuple(previous):
        if key not in current:
            flow.send(14, vision_flow.encode_vision_del_event(object_mid=int(key),
                object_type=vision_flow.VISION_OBJECT_DYNAMIC), 'online-pve-object-remove')
            del previous[key]
    for key, entry in current.items():
        actor = int(key)
        if key not in previous:
            flow.send(14, vision_flow.encode_dynamic_object_add_event(rid=actor,
                instance_id=object_instance_id(actor), resource_id=entry['resourceId'],
                position=tuple(entry['position']), health=entry['health'], server_time_ms=flow.now),
                'online-pve-object-add')
        elif previous[key]['resourceId'] != entry['resourceId']:
            raise ValueError('PVE scene object changed its resource')
        elif previous[key]['position'] != entry['position']:
            flow.send(14, vision_flow.encode_object_location(instance_id=object_instance_id(actor),
                position=tuple(entry['position']), server_tick=(flow.now-flow.session['instanceStartedAt']) & 0xffffffff),
                'online-pve-object-move')
        if key in previous and previous[key]['health'] != entry['health']:
            flow.send(attr_flow.ATTR_COMMAND, attr_flow.encode_health_update(entity_rid=actor,
                health=entry['health'], server_time_ms=flow.now), 'online-pve-object-health')
        previous[key] = entry


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
                if not state['monsters'][key].get('removed', False):
                    flow.send(14, vision_flow.encode_vision_del_event(object_mid=int(key),object_type=vision_flow.VISION_OBJECT_MONSTER), 'online-pve-monster-remove')
                del state['monsters'][key]
        for key, monster in current.items():
            if key not in state['monsters']:
                kind = monster['kind']
                flow.send(14, vision_flow.encode_monster_vision_add_event(rid=int(key),
                    instance_id=instance_id(int(key)), resource_id=kind['resourceId'], hero_id=kind['heroId'],
                    weapon_id=kind['weaponId'], position=tuple(monster['position']), heading=monster['heading'],
                    current_hp=monster['health'], maximum_hp=kind['health'],
                    camp=2,server_time_ms=flow.now,display_name=b'Zombie'), 'online-pve-monster-add')
                state['monsters'][key] = dict(actionSeq=1,poseSeq=0,**monster)
                if monster.get('birth') is not None:
                    flow.send(4, casual_flow.encode_no_fight(actor_id=int(key), enabled=True,
                        server_time_ms=flow.now), 'online-pve-birth-mode')
                    flow.send(4, casual_flow.encode_state(actor_id=int(key),
                        state=BIRTH_STATES[monster['birth']['animation']][0], server_time_ms=flow.now),
                        'online-pve-birth-start')
                flow.log('INFO', f'online-pve spawned actor={key} inst={instance_id(int(key))}')
                if not monster['alive']:
                    flow.send(14, vision_flow.encode_vision_del_event(object_mid=int(key),
                        object_type=vision_flow.VISION_OBJECT_MONSTER), 'online-pve-monster-remove')
                    state['monsters'][key]['removed'] = True
            else:
                source = state['monsters'][key]
                died = source['alive'] and not monster['alive']
                if source.get('birth') is not None and monster.get('birth') is None:
                    source.update(position=monster['position'], heading=monster['heading'])
                    finish_birth(flow, source, flow.connection)
                source.update(monster)
                source['birth'] = monster.get('birth')
                if died:
                    # Vision removal also removes the native collision proxy. An idle
                    # locomotion packet can otherwise interrupt the death graph.
                    flow.send(14, vision_flow.encode_vision_del_event(object_mid=int(key),
                        object_type=vision_flow.VISION_OBJECT_MONSTER), 'online-pve-monster-remove')
                    source['removed'] = True
        if match['pve'].get('objective') != snapshot.get('objective'):
            objective = snapshot.get('objective')
            flow.log('INFO', 'online-pve-objective ' + (
                f"encounter={objective['encounter']} active={objective['active']} text={objective['text']}"
                if objective is not None else 'cleared'))
        present_objects(flow, state, snapshot)
        present_objective(flow, state, snapshot)
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
    source['battleState'] = value['state']
    # An action can change the native locomotion graph independently of poses.
    source.pop('nativeMovement', None)
    source.pop('nativeTransform', None)
    if source.get('removed', False):
        return
    if value['state'] in (35, 42):
        finish_birth(flow, source, connection, session)
    body=battle_flow.encode_battle_state_sync_simple(instance_id=instance_id(value['actorId']),seq_no=seq,
        state=value['state'],state_change_ms=flow.now-session['instanceStartedAt'],state_time_ms=value['durationMs'])
    flow.result['send'].append(dict(connection=connection,command=4,body=body,reason='online-pve-monster-action'))


def movement_body(source, pose, tick):
    """Select the native gait without starting a second position integrator."""
    axes = (pose['forwardBack'], pose['leftRight'])
    moving = source['alive'] and axes != (0, 0)
    gait = (controls.GROUND_RUN_STATES if pose['running'] else controls.GROUND_WALK_STATES).get(axes, 1) if moving else 1
    return move_flow.encode_move_bc_with_system_and_active(
        server_tick=tick, target_instance_id=instance_id(source['actorId']),
        direction_yaw=pose['heading'], position=tuple(pose['position']), state=gait,
        # Rust owns translation; axes and velocity would move the native proxy
        # away from the same position used by blade-contact sampling.
        forward_back=0, left_right=0, current_velocity=0, max_velocity=0, system_group=move_flow.MOVE_MESSAGE_GROUP_SERVER_INTERNAL, active=1)


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
    if source.get('removed', False):
        return
    if source.get('birth') is not None:
        return  # The native birth graph owns the pose until the server ends it.
    # Only transitions select a gait. Reissuing a composite on every AI sample
    # restarts native movement commands even when intent has not changed.
    pose = value['pose']
    movement = [pose['forwardBack'], pose['leftRight'], pose['running']]
    transform = [list(pose['position']), pose['heading']]
    idle = source.get('battleState', 2) == 2
    if idle and source.get('nativeMovement') != movement:
        body = movement_body(source, pose, flow.now-session['instanceStartedAt'])
        source['nativeMovement'] = movement
    elif source.get('nativeTransform') != transform:
        body = move_flow.encode_move_direct_bc(
            server_tick=flow.now-session['instanceStartedAt'],
            target_instance_id=instance_id(source['actorId']),
            direction_yaw=pose['heading'], position=tuple(pose['position']))
    else:
        return
    source['nativeTransform'] = transform
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
