"""Sample the local native pose; apply only Rust-approved peer poses."""
from . import online_limits
import math

from . import actors, controls, online_playback, online_presentation
from .codec import move_flow

INTERVAL_MS = 50
RENDER_INTERVAL_MS = 16
INTERPOLATION_MS = 100
MAX_SAMPLES = 32
STALE_MS = 500
RENDER_TIMER = 'online-motion-render'


def actor_key(value):
    """Peer RID (PVE monsters are peers in enemy slots)."""
    return type(value) is str and value in online_limits.ALL_RIDS - {"1"}


def valid(session):
    value = session.get('onlineMotion')
    return value is None or (session['role'] == 'instance' and type(value) is dict
        and {'inputSeq', 'eventSeq', 'sentAt', 'actors', 'buffers'} <= set(value)
        and set(value) <= {'inputSeq', 'eventSeq', 'sentAt', 'actors', 'buffers', 'lastSent'}
        and type(value.get('inputSeq')) is int and 0 <= value['inputSeq'] <= 0xffffffff
        and type(value.get('eventSeq')) is int and 0 <= value['eventSeq'] <= 0xffffffffffffffff
        and type(value.get('sentAt')) is int and value['sentAt'] >= -INTERVAL_MS
        and type(value.get('actors')) is dict and len(value['actors']) <= online_limits.ROOM_CAPACITY
        and all(key in online_limits.ALL_RIDS and type(seq) is int and 0 < seq <= 0xffffffff
                for key, seq in value['actors'].items())
        and (type(value["buffers"]) is dict
            and len(value["buffers"]) <= online_limits.ROOM_CAPACITY - 1
            and all(actor_key(key) and type(buffer) is dict and valid_buffer(buffer)
                for key, buffer in value["buffers"].items())))


def valid_motion(value):
    if value is None:
        return True
    return (type(value) is dict and set(value) == {'tick', 'velocity', 'airborne', 'jumping', 'actionId', 'actionTick'}
        and type(value['tick']) is int and 0 <= value['tick'] <= 0xffffffffffffffff
        and type(value['actionTick']) is int and 0 <= value['actionTick'] <= value['tick']
        and type(value['actionId']) is int and 0 <= value['actionId'] <= 0xffff
        and type(value['airborne']) is bool and type(value['jumping']) is bool
        and (not value['jumping'] or value['airborne'])
        and type(value['velocity']) is list and len(value['velocity']) == 3
        and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 40 for v in value['velocity']))


def valid_buffer(value):
    samples = value.get('samples', [])
    if type(samples) is not list or len(samples) > MAX_SAMPLES:
        return False
    previous = -1
    for sample in samples:
        if type(sample) is not dict or type(sample.get('at')) is not int or sample['at'] < previous:
            return False
        pose = sample.get('pose')
        if (type(pose) is not dict or not valid_motion(pose.get('motion')) or not actors.validPosition(pose.get('position'))
                or type(pose.get('heading')) is not int or not -180 <= pose['heading'] <= 180
                or any(type(pose.get(k)) is not int or not -1 <= pose[k] <= 1 for k in ('forwardBack', 'leftRight'))
                or type(pose.get('running')) is not bool or pose['running'] and pose['forwardBack'] != -1):
            return False
        previous = sample['at']
    return (all(type(value[k]) is int for k in ('clockOffset', 'renderedAt') if k in value)
            and ('renderAt' not in value or type(value['renderAt']) in (int, float) and math.isfinite(value['renderAt']))
            and (not samples or 'clockOffset' in value))


def ledger(session):
    return session.setdefault('onlineMotion', {'inputSeq': 0, 'eventSeq': 0,
        'sentAt': -INTERVAL_MS, 'actors': {}, 'buffers': {}})


def schedule_render(flow, connection, session):
    """Arm the shared playback timer from any module's receive path."""
    previous_connection, previous_session = flow.connection, flow.session
    try:
        flow.connection, flow.session = connection, session
        if RENDER_TIMER not in session['pending']:
            flow.later(RENDER_TIMER, RENDER_INTERVAL_MS)
    finally:
        flow.connection, flow.session = previous_connection, previous_session


def world_updated(flow):
    from . import custom_lobby
    match = flow.state['onlineScene']
    if match['phase'] != 3 or flow.session.get('leaving') or not controls.groundEnabled(flow):
        return
    render(flow)
    snapshot = actors.world(flow)
    pose = actors.pose(snapshot, 1)
    if pose is None or type(pose.get('heading')) not in (int, float) or not math.isfinite(pose['heading']):
        return
    state = ledger(flow.session)
    if flow.now - state['sentAt'] < INTERVAL_MS:
        return
    gateways = [(int(key), value) for key, value in flow.state['sessions'].items()
                if value.get('customLobby', {}).get('identified') and int(key) in flow.context.get('connections', [])]
    if len(gateways) != 1:
        raise ValueError('online pose requires the identified logic gateway')
    gateway = gateways[0][1]['customLobby']
    ground = controls.groundState(flow)
    mask = max(0, ground['mask'])
    forward, lateral = ((mask >> 2) & 1) - (mask & 1), ((mask >> 1) & 1) - ((mask >> 3) & 1)
    if forward == 0 and lateral == 0:
        ground['moveStartedAt'] = None
    elif ground.get('moveStartedAt') is None:
        ground['moveStartedAt'] = flow.now
    running = (forward == -1 and ground.get('moveStartedAt') is not None
               and flow.now - ground['moveStartedAt'] >= controls.GROUND_RUN_DELAY_MS)
    value = dict(position=list(pose['position']), heading=round((pose['heading'] + 180) % 360 - 180),
                 forwardBack=forward, leftRight=lateral, running=running)
    if type(pose.get('airborne')) is bool and type(pose.get('jumping')) is bool:
        sampled_at = snapshot['sampledAt']
        velocity = [0., 0., 0.]
        previous = state.get('lastSent')
        elapsed = flow.now - state['sentAt']
        if previous is not None and 0 < elapsed <= 150:
            candidate = [(b - a) * 1000 / elapsed
                         for a, b in zip(previous['position'], value['position'])]
            if all(abs(v) <= 40 for v in candidate):
                velocity = candidate
        tick = max(0, sampled_at - flow.session['instanceStartedAt'])
        action_id = pose.get('state', 0)
        # Native state is u32; transient sentinels cannot identify a wire animation.
        if type(action_id) is not int or not 0 <= action_id <= 0xffff:
            action_id = 0
        value['motion'] = dict(tick=tick, velocity=velocity, airborne=pose['airborne'],
            jumping=pose['airborne'] and pose['jumping'], actionId=action_id,
            actionTick=min(tick, max(0, pose.get('phaseAt', sampled_at) - flow.session['instanceStartedAt'])))
    if state.get('lastSent') == value and flow.now - state['sentAt'] < 500:
        return
    if state['inputSeq'] == 0xffffffff:
        raise ValueError('online pose sequence exhausted')
    state['inputSeq'] += 1
    state.update(sentAt=flow.now, lastSent=value)
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = gateways[0]
        custom_lobby.snapshot(flow, {'type': 'poseInput', 'payload': {
            'matchId': match['matchId'], 'inputSeq': state['inputSeq'], 'pose': value}})
    finally:
        flow.connection, flow.session = connection, session


def checkpoint(session):
    value = session.get('onlineMotion', {})
    if value.get('lastSent') is None or not value.get('inputSeq'):
        return None
    return dict(inputSeq=value['inputSeq'], pose=value['lastSent'])


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state.get('onlineScene')
    room = flow.session['customLobby'].get('room')
    if match is None or value['matchId'] != match['matchId'] or room is not None and value['roomId'] != room['roomId']:
        # Independent sockets may deliver an obsolete scene's final snapshot late.
        return
    if match['phase'] == 4:
        return
    if match['phase'] != 3 or room is None:
        # A snapshot may overtake the reliable Running notification; the next replaces it.
        return
    actor = integer(value['actorId'], 1, 0xffffffff)
    from . import online_scene
    rid = online_scene.actor_rid(match, actor)
    from . import online_pve
    if rid is None and not online_pve.monster_id(actor):
        raise ValueError('online pose actor is outside the frozen roster')
    # A monster without a slot is outside its presented lifecycle (removed, or its spawn event is
    # still in flight); its poses carry no information for this client.
    event_sequence = integer(value['eventSeq'], 1, 0xffffffffffffffff)
    sequence = integer(value['inputSeq'], 1, 0xffffffff)
    server_time = integer(value['serverTimeMs'], 0, 0xffffffffffffffff)
    pose = value['pose']
    if not valid_motion(pose.get('motion')):
        raise ValueError('invalid online motion frame')
    if not actors.validPosition(pose['position']):
        raise ValueError('invalid online position')
    heading = integer(pose['heading'], -180, 180)
    forward, lateral = integer(pose['forwardBack'], -1, 1), integer(pose['leftRight'], -1, 1)
    running = pose['running']
    if type(running) is not bool or running and forward != -1:
        raise ValueError('invalid online movement gait')
    instances = [(int(key), session) for key, session in flow.state['sessions'].items()
        if session['role'] == 'instance' and session.get('initialized') and not session.get('leaving')
        and int(key) in flow.context.get('connections', [])]
    if len(instances) != 1:
        raise ValueError('online pose requires one live instance')
    connection, session = instances[0]
    state = ledger(session)
    if event_sequence <= state['eventSeq']:
        return
    if rid is None:
        state['eventSeq'] = event_sequence
        return
    if online_pve.held(session, rid):
        # Emergence or death owns the native graph; Rust holds the monster still meanwhile.
        state['eventSeq'], state['actors'][str(rid)] = event_sequence, max(sequence, state['actors'].get(str(rid), 0))
        return
    if sequence <= state['actors'].get(str(rid), 0):
        return  # Replaceable snapshots can arrive behind another socket's phase/event.
    state['eventSeq'], state['actors'][str(rid)] = event_sequence, sequence
    if rid == 1:
        return
    state = state.setdefault('buffers', {}).setdefault(str(rid), {})
    samples = state.setdefault('samples', [])
    if pose.get('motion') is not None:
        offset = server_time - pose['motion']['tick']
        state['serverTickOffset'] = min(state.get('serverTickOffset', offset), offset)
        server_time = pose['motion']['tick']
    if samples and server_time < samples[-1]['at']:
        raise ValueError('online pose clock moved backwards')
    state['clockOffset'] = min(state.get('clockOffset', flow.now - server_time), flow.now - server_time)
    online_playback.observe(state, server_time, flow.now)
    sample = dict(at=server_time, pose=pose)
    if samples and abs(pose['position'][2] - samples[-1]['pose']['position'][2]) > .01:
        # Continue correcting a vertical hold when input has no horizontal axes.
        state['verticalUntil'] = flow.now + STALE_MS
    if not samples or server_time - samples[-1]['at'] > STALE_MS or math.dist(
            pose['position'], samples[-1]['pose']['position']) > 8:
        samples[:] = [sample]
        state.pop('peerMovement', None)
        state.pop('renderAt', None)
        state.pop('playbackAt', None)
        emit(flow, connection, session, pose, 'online-pose-reset', rid, state)
    else:
        samples.append(sample)
        del samples[:-MAX_SAMPLES]
    schedule_render(flow, connection, session)


def timer(flow, name):
    if name != RENDER_TIMER:
        return False
    match = flow.state.get('onlineScene')
    if match is None or match['phase'] != 3 or flow.session.get('leaving'):
        return True
    render(flow)
    value = ledger(flow.session)
    buffers = value.get('buffers', {}).values()
    if online_presentation.pending(flow.session) or any(buffer.get('samples') and (buffer.get('catchUp') or buffer.get('actions') or buffer.get('renderAt', 0) < buffer['samples'][-1]['at']
            or flow.now - buffer['clockOffset'] - buffer['samples'][-1]['at'] <= STALE_MS + INTERPOLATION_MS
                and (buffer['samples'][-1]['pose']['forwardBack'] or buffer['samples'][-1]['pose']['leftRight'])
            or flow.now <= buffer.get('verticalUntil', -1)) for buffer in buffers):
        flow.later(RENDER_TIMER, RENDER_INTERVAL_MS)
    return True


def render(flow):
    value = ledger(flow.session)
    for key, buffer in value.get('buffers', {}).items():
        render_actor(flow, int(key), buffer)
    online_presentation.release(flow)


def render_actor(flow, rid, state):
    from . import online_pve
    if online_pve.held(flow.session, rid):
        return
    samples = state.get('samples', [])
    if not samples or flow.now - state.get('renderedAt', -RENDER_INTERVAL_MS) < RENDER_INTERVAL_MS:
        return
    at = online_playback.advance(state, flow.now, samples[0]['at'])
    from . import online_actions
    queued = state.setdefault('actions', [])
    due = None
    while queued and queued[0]['serverTimeMs'] - state.get('serverTickOffset', 0) <= at:
        due = queued.pop(0)
    if due is not None:
        online_actions.present(flow, flow.connection, flow.session, rid, due)
    while len(samples) > 2 and samples[1]['at'] <= at:
        samples.pop(0)
    before, after = samples[0], samples[1] if len(samples) > 1 else samples[0]
    if at >= after['at']:
        motion = after['pose'].get('motion')
        reacting = str(rid) in flow.session.get('onlineActions', {}).get('reacting', [])
        if motion is not None and not reacting:
            pose = online_playback.extrapolate(before, after, at, airborne=motion['airborne'])
        else:
            pose = dict(after['pose'])
            if pose.get('motion') is not None:
                pose['motion'] = dict(pose['motion'], velocity=[0., 0., 0.])
    else:
        span = after['at'] - before['at']
        fraction = max(0.0, min(1.0, (at - before['at']) / span)) if span else 1.0
        pose = dict(before['pose'])
        pose['position'] = [a + (b - a) * fraction for a, b in zip(before['pose']['position'], after['pose']['position'])]
        if pose.get('motion') is not None and span:
            pose['motion'] = dict(pose['motion'], velocity=[max(-40., min(40., (b - a) * 1000 / span))
                for a, b in zip(before['pose']['position'], after['pose']['position'])])
        delta = (after['pose']['heading'] - before['pose']['heading'] + 180) % 360 - 180
        pose['heading'] = round((before['pose']['heading'] + delta * fraction + 180) % 360 - 180)
    if at - samples[-1]['at'] > STALE_MS:
        pose.update(forwardBack=0, leftRight=0, running=False)
        if pose.get('motion') is not None:
            pose['motion'] = dict(pose['motion'], airborne=False, jumping=False, velocity=[0., 0., 0.])
    emit(flow, flow.connection, flow.session, pose, 'online-pose-interpolated', rid, state)


def gait_velocity(session, rid, running):
    """Native integration speed for a peer gait. Players walk 3 and run 5 u/s; a PVE monster
    integrates at the speed Rust moves it, so the native model and the server stay together."""
    from . import online_scene
    speed = session.get('onlinePeers', {}).get(str(rid), {}).get('speed')
    if speed is not None:
        return max(0, min(25000, round(1000 * speed)))
    return 5000 if running else 3000


def emit(flow, connection, session, pose, reason, rid, state):
    from . import online_scene
    tick = flow.now - session['instanceStartedAt']
    if not 0 <= tick <= controls.MAX_MOVE_TICK:
        raise ValueError('online pose time outside instance')
    motion = pose.get('motion')
    if motion is not None:
        effects = flow.result.setdefault('remoteMotion', [])
        effects[:] = [item for item in effects if item['rid'] != rid]
        effects.append(dict(rid=rid, instanceStartedAt=session['instanceStartedAt'],
            airborne=motion['airborne'], jumping=motion['jumping'],
            position=list(pose['position']), velocity=list(motion['velocity'])))
    forward, lateral, running = pose['forwardBack'], pose['leftRight'], pose['running']
    heading = pose['heading']
    movement = (forward, lateral, running, heading)
    reacting = str(rid) in session.get('onlineActions', {}).get('reacting', [])
    snapshot = actors.world(flow)
    actual = actors.pose(snapshot, rid)
    # Reactions own animation, while the sender remains the sole position authority.
    # Suspending snapshots here lets the previous native gait drift until recovery.
    state.pop('reactionOwned', None)
    state.pop('catchUp', None)
    if reacting:
        state.pop('peerMovement', None)
    airborne = motion is not None and motion['airborne']
    if airborne:
        axes = [heading, forward, lateral]
        if state.get('airMovement') != axes:
            state['airMovement'] = axes
            flow.result['send'].append(dict(connection=connection, command=2,
                body=move_flow.encode_move_in_air_direct_bc(server_tick=tick, target_instance_id=rid,
                    direction_yaw=heading, forward_back=forward * 1000, left_right=lateral * 1000),
                reason='online-peer-air-direction'))
        # Ground drive commands restart the grounded locomotion graph during a jump.
        # The snapshot owns translation; the native mailbox owns the airborne graph.
        state.pop('peerMovement', None)
    if not airborne:
        state.pop('airMovement', None)
    if not reacting and not airborne and state.get('peerMovement') != list(movement):
        state['peerMovement'] = list(movement)
        moving = forward != 0 or lateral != 0
        walk = (controls.GROUND_RUN_STATES if running else controls.GROUND_WALK_STATES).get((forward, lateral), 1)
        body = move_flow.encode_move_bc_with_system_and_active(server_tick=tick, target_instance_id=rid,
            position=tuple(pose['position']), direction_yaw=heading, state=walk,
            forward_back=forward * 1000, left_right=lateral * 1000,
            current_velocity=gait_velocity(session, rid, running) if moving else 0,
            max_velocity=25000 if moving else 0, system_group=2, active=1, acceleration=0)
        flow.result['send'].append(dict(connection=connection, command=2, body=body, reason='online-peer-movement'))
    state['renderedAt'] = flow.now
    if motion is not None:
        state['renderedPose'] = pose
        online_scene.peer_data(session, rid).update(position=list(pose['position']), heading=heading)
        return
    if (not reacting and state.get('renderedPose') == pose and forward == lateral == 0
            and flow.now > state.get('verticalUntil', -1)):
        return
    # Compare against observed native integration, not the previous correction target.
    state['renderedPose'] = pose
    if (not reacting and not airborne and actual is not None and snapshot.get('sampledAt', -1) >= flow.now - INTERVAL_MS
            and math.dist(actual['position'], pose['position']) <= .075
            and type(actual.get('heading')) in (int, float)
            and abs((actual['heading'] - heading + 180) % 360 - 180) <= 2):
        return
    body = move_flow.encode_move_direct_bc(server_tick=tick, target_instance_id=rid,
        direction_yaw=heading, position=tuple(pose['position']))
    flow.result['send'].append(dict(connection=connection, command=2, body=body,
        reason=reason))
    online_scene.peer_data(session, rid).update(position=list(pose['position']), heading=heading)
