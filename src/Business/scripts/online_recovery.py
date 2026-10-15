"""Pause a disconnected transport and restore current authority without event replay."""
from . import controls, online_actions, online_combat, online_motion, online_prediction, online_scene
from .codec import battle_flow, custom_room as codec, move_flow


def instances(flow):
    return [(int(key), session) for key, session in flow.state['sessions'].items()
            if session['role'] == 'instance' and session.get('initialized')
            and not session.get('leaving') and int(key) in flow.context.get('connections', [])]


def pause(flow, payload):
    from . import custom_lobby
    gateway = flow.session['customLobby']
    gateway['paused'] = True
    gateway['pending'].clear()
    gateway.pop('clock', None)
    gateway['combatSeq'] = 0
    # Reset commits with the business state before the native worker reconnects.
    custom_lobby.effect(flow, 'reset', '1')
    connection, session = flow.connection, flow.session
    try:
        for flow.connection, flow.session in instances(flow):
            online_prediction.clear(flow.session)
            flow.session['groundEnabled'] = False
            controls.activate(flow)
            motion = online_motion.ledger(flow.session)
            for rid in online_scene.ids(flow)[1:]:
                peer = online_scene.peer_data(flow.session, rid)
                state = motion['buffers'].get(str(rid), {})
                pose = state.get('renderedPose', dict(position=peer.get('position', peer['spawnPosition']),
                    heading=peer.get('heading', peer['spawnHeading'])))
                frame = pose.get('motion')
                stopped = dict(pose, forwardBack=0, leftRight=0, running=False)
                if frame is not None:
                    stopped['motion'] = dict(frame, velocity=[0., 0., 0.])
                online_motion.emit(flow, flow.connection, flow.session, stopped,
                                   'online-recovery-peer-paused', rid, state)
            motion['buffers'].clear()
    finally:
        flow.connection, flow.session = connection, session
    reason = payload.get('reason', 'connection lost')
    if type(reason) is not str or len(reason) > 256:
        raise ValueError('invalid transport failure reason')
    flow.log('WARN', f'room transport lost; reconnecting with the existing session: {reason}')


def resume(flow, payload):
    from . import custom_lobby
    gateway = flow.session['customLobby']
    if not gateway.get('paused'):
        raise ValueError('unsolicited session recovery')
    profile = payload
    if custom_lobby.text(profile['displayName'], 32) != flow.playerName:
        raise ValueError('recovery changed the player identity')
    identified = gateway['identified']
    user_id = custom_lobby.integer(profile['userId'], 1, 0xffffffff)
    if identified and user_id != gateway['userId']:
        raise ValueError('recovery changed the player id')
    gateway.update(paused=False, identified=True, userId=user_id,
                   level=custom_lobby.integer(profile['level'], 1))
    custom_lobby.effect(flow, 'resume', '1')
    if not identified:
        flow.later('login', 1000)
    previous = gateway.get('room')
    room = payload['room']
    if room is None:
        if previous is not None:
            custom_lobby.cancel_scene(flow)
            flow.send(codec.COMMAND, codec.encode_dismiss_notify(custom_lobby.guid(previous['roomGuid'])),
                      'online-recovery-room-left')
            gateway.pop('room', None)
    else:
        custom_lobby.full_room(room, active=True)
        if not any(m['accountId'] == flow.context['roomGateway']['accountId'] and m['userId'] == user_id
                   for m in room['members']):
            raise ValueError('recovery room lacks the retained player')
        if previous is None:
            gateway['room'] = {**room, 'phase': 'waiting'}
            flow.send(codec.COMMAND, codec.encode_room_response(9, custom_lobby.full_room(room, active=True)),
                      'online-recovery-room-joined')
        custom_lobby.changed(flow, room, force=True)
        if room['phase'] == 'loading':
            gateway.pop('loadedMatch', None)
            custom_lobby.loaded(flow)
        if room['phase'] == 'running':
            restore_battle(flow, room, payload['recovery'])
    flow.later('custom-lobby-ping', 1)
    flow.later('online-combat-heartbeat', 1)
    flow.log('INFO', 'room session recovered; current authority restored')


def restore_battle(flow, room, recovery):
    from . import custom_lobby, online_pve
    match = flow.state.get('onlineScene')
    live = instances(flow)
    if match is None or len(live) != 1 or recovery is None:
        raise ValueError('running recovery requires the existing game instance')
    sequences = {key: custom_lobby.integer(recovery[key], 0, 0xffffffffffffffff)
                 for key in ('actionSeq', 'combatSeq', 'poseSeq', 'pveSeq')}
    connection, session = flow.connection, flow.session
    target_connection, target_session = live[0]
    if room.get('pve') is not None:
        pve = online_pve.ledger(target_session)
        if sequences['pveSeq']:
            pve['eventSeq'] = sequences['pveSeq'] - 1
            online_pve.receive(flow, dict(roomId=room['roomId'], matchId=match['matchId'],
                                        eventSeq=sequences['pveSeq'], snapshot=room['pve']))
    try:
        flow.connection, flow.session = target_connection, target_session
        online_prediction.clear(flow.session, blocked=False)
        combat = online_combat.ledger(flow.session)
        combat.update(eventSeq=sequences['combatSeq'], seen=[], resumeAt=flow.now)
        previous_health = {1: combat['health'], **{rid: online_scene.peer_data(flow.session, rid).get('health', 100)
                           for rid in online_scene.ids(flow)[1:]}}
        values = online_combat.health(flow, recovery['lives'])
        flow.session.pop('respawnPending', None)
        for rid, hp in values.items():
            if online_pve.monster_id(rid):
                continue
            if previous_health[rid] == 0 and hp:
                online_combat.respawn(flow, rid, recovery['lives'])
            elif previous_health[rid] and not hp:
                flow.send(4, battle_flow.encode_battle_dead_notify(entity_rid=rid,
                          server_time_ms=flow.now), 'online-recovery-dead')
                if rid == 1:
                    from . import contracts as wire
                    flow.session['heroChosen'] = False
                    flow.send(0x36, wire.actorState(flow.now, 9, userId=flow.userId), 'online-recovery-observer')
        online_combat.scores(flow, recovery['lives'])
        combat['ready'] = match['localActorId'] in recovery['respawnReady']
        if combat['ready']:
            from . import contracts as wire
            flow.send(0x36, wire.actorState(flow.now, 7, userId=flow.userId), 'online-recovery-respawn-ready')
        motion = online_motion.ledger(flow.session)
        motion.update(buffers={}, actors={}, eventSeq=sequences['poseSeq'], sentAt=-50)
        actions = online_actions.ledger(flow.session)
        actions.update(eventSeq=sequences['actionSeq'], actors={}, reacting=[])
        for action in recovery['actions']:
            rid = online_scene.actor_rid(match, action['actorId'])
            if online_pve.monster_id(action['actorId']):
                source = online_pve.ledger(flow.session)['monsters'].get(str(action['actorId']))
                if source is not None:
                    source['actionSeq'] = action['actionSeq'] % 65535 or 65535
                    flow.send(4, battle_flow.encode_battle_state_sync_simple(
                        instance_id=online_pve.instance_id(action['actorId']), seq_no=action['actionSeq'],
                        state=action['state'], state_change_ms=flow.now-flow.session['instanceStartedAt'],
                        state_time_ms=action['durationMs']), 'online-recovery-monster-action')
                continue
            if rid is None:
                raise ValueError('recovery action outside the roster')
            online_actions.validate_event(match, action)
            actions['actors'][str(rid)] = custom_lobby.integer(action['actionSeq'], 1, 65535)
            online_actions.present(flow, target_connection, flow.session, rid, action)
        for pose in recovery['poses']:
            actor = pose['actorId']
            rid = online_scene.actor_rid(match, actor)
            if rid is None:
                continue
            value = pose['pose']
            if not online_pve.monster_id(actor):
                motion['actors'][str(rid)] = pose['inputSeq']
                if rid != 1:
                    online_motion.emit(flow, target_connection, flow.session, value,
                        'online-recovery-peer-pose', rid, motion['buffers'].setdefault(str(rid), {}))
            else:
                online_pve.ledger(flow.session)['monsters'][str(actor)]['poseSeq'] = pose['inputSeq']
            # Own position is restored only at this explicit recovery boundary.
            flow.send(2, move_flow.encode_move_direct_bc(server_tick=flow.now-flow.session['instanceStartedAt'],
                target_instance_id=online_pve.instance_id(actor) if online_pve.monster_id(actor) else rid,
                direction_yaw=value['heading'], position=tuple(value['position'])), 'online-recovery-position')
        if combat['health']:
            controls.enableGround(flow)
    finally:
        flow.connection, flow.session = connection, session
