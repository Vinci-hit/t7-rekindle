"""Native rendering and entry for server-owned cooperative monsters."""
import os
from functools import lru_cache
import math
import struct
from . import actors, contracts as wire, controls
from .codec import attr_flow, battle_flow, casual_flow, move_flow, vision_flow, objective_flow

FIRST_MONSTER_ID = 0x40000001
FIRST_OBJECT_ID = 0x50000001
MAX_MONSTERS = 64
MAX_WAVES = 128
FIRST_MONSTER_INSTANCE = 1024
DEFAULT_MONSTER_SPEED = 2.0
TRACE = os.environ.get('T7_PVE_TRACE') == '1'
# While the native gait integrates between samples, position corrections are limited to this rate
# (60 Hz direct moves restart the locomotion graph and cost a packet per monster per frame).
DIRECT_CORRECTION_MS = 50
HEADING_CORRECTION_DEGREES = 8
HOLD_PIN_MS = 100
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
                flow.session.get('onlineMotion', {}).get('buffers', {}).pop(key, None)
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
                    flow.session.get('onlineMotion', {}).get('buffers', {}).pop(key, None)
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
    from . import online_actions, online_motion
    buffer = session.get('onlineMotion', {}).get('buffers', {}).get(str(value['actorId']), {})
    if source.get('removed', False):
        return
    if buffer.get('samples') and value['state'] not in online_actions.REACTION_STATES:
        queued = buffer.setdefault('actions', [])
        if len(queued) >= 64:
            raise ValueError('monster action playback queue overflow')
        queued.append(value)
        online_motion.schedule_render(flow, connection, session)
        return
    buffer.pop('actions', None)
    present_action(flow, connection, session, value['actorId'], value)


def present_action(flow, connection, session, actor, value):
    source = ledger(session)['monsters'].get(str(actor))
    if source is None or source.get('removed', False):
        return
    # Birth owns the native graph until completion. Keep consuming authoritative
    # action sequences, but do not replace a living monster's emergence with hurt/idle.
    if source.get('birth') is not None and value['state'] != 42:
        return
    seq = value['actionSeq']
    source['battleState'] = value['state']
    walking = source.get('nativeMovement')
    if walking and (walking[0] or walking[1]) and value['state'] != 42:
        # The gait now carries velocity, so it must be cancelled before the action starts; otherwise the
        # native intent keeps walking the proxy away from the server position during the animation.
        shown = source.get('presented') or [source['position'], source['heading']]
        flow.result['send'].append(dict(connection=connection, command=2, reason='online-pve-monster-stop',
            body=movement_body(source, dict(position=shown[0], heading=shown[1], forwardBack=0, leftRight=0,
                running=False), flow.now-session['instanceStartedAt'])))
    # An action can change the native locomotion graph independently of poses.
    source.pop('nativeMovement', None)
    source.pop('nativeTransform', None)
    if value['state'] == 42:
        finish_birth(flow, source, connection, session)
    body=battle_flow.encode_battle_state_sync_simple(instance_id=instance_id(value['actorId']),seq_no=seq,
        state=value['state'],state_change_ms=flow.now-session['instanceStartedAt'],state_time_ms=value['durationMs'])
    trace(flow, source, 'action', toState=value['state'], seq=seq)
    flow.result['send'].append(dict(connection=connection,command=4,body=body,reason='online-pve-monster-action'))


def movement_body(source, pose, tick):
    """Select the native gait and drive its locomotion graph.

    Every mover that animates correctly (player, bots, peers) sends non-zero axes and velocity with
    its gait: the walk graph is blended by velocity, so a zero-velocity gait slides the idle pose.
    Translation stays server-owned: the correction packets in `emit` re-anchor the proxy to the
    presented pose every DIRECT_CORRECTION_MS, which bounds any native integration drift.
    """
    axes = (pose['forwardBack'], pose['leftRight'])
    moving = source['alive'] and axes != (0, 0)
    gait = (controls.GROUND_RUN_STATES if pose['running'] else controls.GROUND_WALK_STATES).get(axes, 1) if moving else 1
    speed = source.get('kind', {}).get('speed', DEFAULT_MONSTER_SPEED)
    velocity = max(0, min(25000, round(1000 * speed))) if moving else 0
    return move_flow.encode_move_bc_with_system_and_active(
        server_tick=tick, target_instance_id=instance_id(source['actorId']),
        direction_yaw=pose['heading'], position=tuple(pose['position']), state=gait,
        forward_back=axes[0] * 1000 if moving else 0, left_right=axes[1] * 1000 if moving else 0,
        current_velocity=velocity, max_velocity=25000 if moving else 0,
        system_group=move_flow.MOVE_MESSAGE_GROUP_SERVER_INTERNAL, active=1)


def pose(flow, actor, pose, connection, session, server_time, sequence):
    from . import online_motion, online_playback
    source = ledger(session)['monsters'].get(str(actor))
    if source is None or sequence <= source['poseSeq']:
        return
    source['poseSeq'] = sequence
    source.update(pose)
    if source.get('removed', False) or source.get('birth') is not None:
        return
    buffer = online_motion.ledger(session)['buffers'].setdefault(str(actor), {})
    samples = buffer.setdefault('samples', [])
    if samples and server_time < samples[-1]['at']:
        return
    buffer['clockOffset'] = min(buffer.get('clockOffset', flow.now-server_time), flow.now-server_time)
    online_playback.observe(buffer, server_time, flow.now)
    sample = dict(at=server_time, pose=pose)
    if not samples or server_time-samples[-1]['at'] > online_motion.STALE_MS or math.dist(
            pose['position'], samples[-1]['pose']['position']) > 8:
        samples[:] = [sample]
        buffer.pop('renderAt', None)
        buffer.pop('playbackAt', None)
        emit(flow, connection, session, actor, pose, buffer, 'online-pve-monster-pose')
    else:
        if server_time == samples[-1]['at']:
            samples[-1] = sample
        else:
            samples.append(sample)
            del samples[:-online_motion.MAX_SAMPLES]
    online_motion.schedule_render(flow, connection, session)


def emit(flow, connection, session, actor, pose, state, reason):
    source = ledger(session)['monsters'].get(str(actor))
    if source is None or source.get('removed', False) or source.get('birth') is not None:
        return
    state['renderedAt'] = flow.now
    # Only transitions select a gait. Reissuing a composite on every AI sample
    # restarts native movement commands even when intent has not changed.
    movement = [pose['forwardBack'], pose['leftRight'], pose['running']]
    transform = [list(pose['position']), pose['heading']]
    # What the player sees: hit sampling uses this, whether or not a correction packet was needed.
    source['presented'] = transform
    idle = source.get('battleState', 2) == 2
    if idle and gait_due(source.get('nativeMovement'), movement, pose['heading']):
        # The composite carries direction and position: re-issuing it when the travel direction turns
        # is what peers do. Without it the native gait kept walking along the old heading while
        # corrections pulled it back, which read as side-to-side wobble.
        body = movement_body(source, pose, flow.now-session['instanceStartedAt'])
        source['nativeMovement'] = movement + [pose['heading']]
    elif (source.get('nativeTransform') != transform and correction_due(source, pose, transform, flow.now)) \
            or pin_due(source, flow.now):
        body = move_flow.encode_move_direct_bc(
            server_tick=flow.now-session['instanceStartedAt'],
            target_instance_id=instance_id(source['actorId']),
            direction_yaw=pose['heading'], position=tuple(pose['position']))
    else:
        return
    source['nativeTransform'] = transform
    source['nativeSentAt'] = flow.now
    trace(flow, source, 'write', kind='gait' if body[:2] != b'\0\4' else 'direct')
    flow.result['send'].append(dict(connection=connection,command=2,body=body,reason='online-pve-monster-pose'))


def trace(flow, source, what, **fields):
    # Opt-in (T7_PVE_TRACE=1): one line per native write with the server pose beside the presented pose,
    # so a recorded fight shows which writer moved or turned a monster and when.
    if TRACE:
        flow.log('DEBUG', 'pve-trace ' + ' '.join([f"t={flow.now}", f"actor={source['actorId']}", what,
            f"state={source.get('battleState', 2)}", f"server={[round(v, 2) for v in source['position']]}/{source['heading']}",
            f"shown={source.get('presented')}"] + [f"{k}={v}" for k, v in fields.items()]))


def gait_due(previous, movement, heading):
    if previous is None or previous[:3] != movement:
        return True
    moving = movement[0] != 0 or movement[1] != 0
    return moving and (len(previous) < 4
        or abs((heading - previous[3] + 180) % 360 - 180) > HEADING_CORRECTION_DEGREES)


def pin_due(source, now):
    """Re-anchor a monster whose pose is unchanged.

    Attack and hurt animations (and player body collision) move the native proxy by themselves. While the
    server pose is stationary nothing else is sent, so the model drifted or turned away from the position
    the server and the hit sampling use. A periodic anchor keeps model and hitbox together.
    """
    return source['alive'] and now - source.get('nativeSentAt', -HOLD_PIN_MS) >= HOLD_PIN_MS


def correction_due(source, pose, transform, now):
    """Rate-limit position corrections while the native gait is walking; settle immediately otherwise."""
    previous = source.get('nativeTransform')
    if previous is None or (pose['forwardBack'] == 0 and pose['leftRight'] == 0):
        return True
    if abs((transform[1] - previous[1] + 180) % 360 - 180) > HEADING_CORRECTION_DEGREES:
        return True
    return now - source.get('nativeSentAt', -DIRECT_CORRECTION_MS) >= DIRECT_CORRECTION_MS


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
