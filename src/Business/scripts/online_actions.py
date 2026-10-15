"""Translate melee intentions and Rust action events for the frozen scene."""
from . import online_limits, online_prediction
import struct

from . import controls
from .codec import battle_flow, weapon_flow

INTENTS = {
    300560: 'attackUp', 300550: 'attackRight', 300570: 'attackDown', 300540: 'attackLeft',
    300580: 'blockUp', 300590: 'blockRight', 300600: 'blockDown', 300610: 'blockLeft',
    700250: 'dashAttack',
    300020: 'attackRelease', 300620: 'blockRelease',
}
STATES = frozenset((1, 438, 439, 443, 2, 235, 237, 238, 239, 241, 243, 244, 245, 247, 249, 250, 251,
                    253, 255, 256, 257, 305, 223, 227, 308, 224, 228, 307, 225, 229, 306, 226, 230, 35, 42, 240, 246, 252, 258, 231, 232, 233, 234, 466, 468, 467, 469))
GUARD_STATES = frozenset((305, 308, 307, 306, 223, 224, 225, 226, 227, 228, 229, 230))
REACTION_STATES = frozenset((439, 443, 35, 42, 240, 246, 252, 258, 231, 232, 233, 234, 466, 468, 467, 469))


def valid(session):
    value = session.get('onlineActions')
    if value is None:
        return True
    return (session['role'] == 'instance' and type(value) is dict
            and type(value.get('inputSeq')) is int and 0 <= value['inputSeq'] <= 0xffffffff
            and type(value.get('eventSeq')) is int and 0 <= value['eventSeq'] <= 0xffffffffffffffff
            and type(value.get('actors')) is dict and len(value['actors']) <= online_limits.ROOM_CAPACITY
            and all(key in online_limits.ALL_RIDS and type(seq) is int and 1 <= seq <= 0xffff
                    for key, seq in value['actors'].items())
            and type(value.get('reacting', [])) is list
            and len(value.get('reacting', [])) <= online_limits.ROOM_CAPACITY
            and all(type(key) is str and key in online_limits.ALL_RIDS for key in value.get('reacting', []))
            and len(set(value.get('reacting', []))) == len(value.get('reacting', [])))


def ledger(session):
    return session.setdefault('onlineActions', {'inputSeq': 0, 'eventSeq': 0, 'actors': {}})


def message(flow, command, selector, body):
    weapon_slot = None
    if flow.session['role'] != 'instance':
        return False
    if command == 5 and selector == 1:
        weapon_slot = weapon_flow.decode_change_request(body)
        if weapon_slot not in (weapon_flow.MAIN_SLOT, weapon_flow.FIST_SLOT):
            from . import weapons
            flow.send(5, weapon_flow.encode_change_response(weapon_flow.INVALID_SLOT, weapons.equipped(flow.session)), 'online-weapon-invalid-slot')
            return True
        intent = 'equipMain' if weapon_slot == weapon_flow.MAIN_SLOT else 'toggleFists'
    elif command != 4 or selector != 1:
        return False
    elif len(body) != 11:
        raise ValueError('online action requires an eleven-byte body')
    if weapon_slot is None:
        keys, intent, auto_parry = struct.unpack_from('>Iib', body, 2)
        if auto_parry not in (0, 1):
            raise ValueError('invalid online auto-parry flag')
        intent = INTENTS.get(intent)
        if intent is None:
            return False
        if intent == 'dashAttack' and not (keys & (1 << 26) and keys & ((1 << 21) | (1 << 7))):
            return True
    match = flow.state['onlineScene']
    if match['phase'] != 3 or not controls.groundEnabled(flow) or flow.session.get('leaving'):
        return True
    from . import custom_lobby
    state = ledger(flow.session)
    if state['inputSeq'] == 0xffffffff:
        raise ValueError('online action input sequence exhausted')
    state['inputSeq'] += 1
    connection, session = flow.connection, flow.session
    gateways = [(int(key), value) for key, value in flow.state['sessions'].items()
                if value.get('customLobby', {}).get('identified')
                and int(key) in flow.context.get('connections', [])]
    if len(gateways) != 1:
        raise ValueError('online action requires the identified logic gateway')
    from . import online_motion
    payload = dict(matchId=match['matchId'], inputSeq=state['inputSeq'], intent=intent)
    pose_checkpoint = online_motion.checkpoint(session)
    if pose_checkpoint is not None:
        payload['pose'] = pose_checkpoint
    try:
        flow.connection, flow.session = gateways[0]
        custom_lobby.send(flow, 'action', {'type': 'actionInput', 'payload': payload},
            matchId=match['matchId'], inputSeq=state['inputSeq'], **({'weaponSlot': weapon_slot, 'previousWeapon': flow.state['sessions'][str(connection)].get('weaponId', 1030411)} if weapon_slot is not None else {}))
        flow.log('INFO', f'online-input input={state["inputSeq"]} intent={intent}')
    finally:
        flow.connection, flow.session = connection, session
    if weapon_slot is None:
        online_prediction.input(flow, next(key for key, name in INTENTS.items() if name == intent), state['inputSeq'])
    return True


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state.get('onlineScene')
    room = flow.session['customLobby'].get('room')
    if (match is None or value['matchId'] != match['matchId']
            or room is not None and value['roomId'] != room['roomId']):
        raise ValueError('online action belongs to another scene')
    if match['phase'] == 4:
        return
    if match['phase'] != 3 or room is None:
        raise ValueError('online action precedes Running')
    actor = integer(value['actorId'], 1, 0xffffffff)
    from . import online_scene
    rid = online_scene.actor_rid(match, actor)
    from . import online_pve
    if rid is None and not online_pve.monster_id(actor):
        raise ValueError('online action actor is outside the frozen roster')
    action_id = value['actionId']
    if action_id is not None:
        integer(action_id, 1, 0xffffffff)
    sequence = integer(value['actionSeq'], 1, 0xffff)
    event_sequence = integer(value['eventSeq'], 1, 0xffffffffffffffff)
    integer(value['serverTimeMs'], 0, 0xffffffffffffffff)
    state = integer(value['state'], 1, 0xffff)
    if state not in STATES:
        raise ValueError('unsupported online melee state')
    instances = [(int(key), session) for key, session in flow.state['sessions'].items()
                 if session['role'] == 'instance' and session.get('initialized') and not session.get('leaving')
                 and int(key) in flow.context.get('connections', [])]
    if len(instances) != 1:
        raise ValueError('online action requires one live instance')
    connection, session = instances[0]
    if online_pve.monster_id(actor):
        online_pve.action(flow,value,connection,session)
        return
    validate_event(match, value)
    actions = ledger(session)
    if event_sequence != actions['eventSeq'] + 1 or sequence != actions['actors'].get(str(rid), 1) % 0xffff + 1:
        raise ValueError('online action event sequence has a gap or replay')
    actions['eventSeq'] = event_sequence
    actions['actors'][str(rid)] = sequence
    if rid == 1 and online_prediction.reconcile(flow, session, sequence, state, action_id):
        return
    buffer = session.get('onlineMotion', {}).get('buffers', {}).get(str(rid), {})
    # Contact reactions must not wait behind the deliberate movement playback delay.
    # Their recovery follows the same immediate clock so recoil does not linger.
    if rid != 1 and (state in REACTION_STATES or state in GUARD_STATES or buffer.get('guardPresented', False) or str(rid) in actions.get('reacting', [])):
        buffer.pop('actions', None)
        present(flow, connection, session, rid, value)
        return
    if rid != 1 and buffer.get('samples'):
        queued = buffer.setdefault('actions', [])
        if len(queued) >= 64:
            raise ValueError('remote action playback queue overflow')
        queued.append(value)
        from . import online_motion
        previous_connection, previous_session = flow.connection, flow.session
        try:
            flow.connection, flow.session = connection, session
            if online_motion.RENDER_TIMER not in session['pending']:
                flow.later(online_motion.RENDER_TIMER, online_motion.RENDER_INTERVAL_MS)
        finally:
            flow.connection, flow.session = previous_connection, previous_session
        return
    present(flow, connection, session, rid, value)


def validate_event(match, value):
    from .custom_lobby import integer
    from . import online_scene, weapons, contracts as wire
    if integer(value['weaponId'], 1) not in (wire.WEAPON_ID, weapons.FIST_ID):
        raise ValueError('unsupported online equipment')
    integer(value['durationMs'], 0, 65535)
    target = value.get('targetActorId')
    if target is not None and (type(target) is not int or target != 0 and online_scene.actor_rid(match, target) is None):
        raise ValueError('online reaction target outside roster')


def present(flow, connection, session, rid, value):
    from .custom_lobby import integer
    from . import online_scene
    match = flow.state['onlineScene']
    actor = value['actorId']
    state, sequence, event_sequence = value['state'], value['actionSeq'], value['eventSeq']
    actions = ledger(session)
    buffer = session.get('onlineMotion', {}).get('buffers', {}).get(str(rid))
    if buffer is not None:
        buffer['guardPresented'] = state in GUARD_STATES
    reacting = actions.setdefault('reacting', [])
    if str(rid) in reacting:
        reacting.remove(str(rid))
    if state in REACTION_STATES:
        reacting.append(str(rid))
    from . import weapons, contracts as wire
    weapon = integer(value['weaponId'], 1)
    if weapon not in (wire.WEAPON_ID, weapons.FIST_ID):
        raise ValueError('unsupported online equipment')
    source = session if rid == 1 else online_scene.peer_data(session, rid)
    if source.get('weaponId', wire.WEAPON_ID) != weapon:
        source['weaponId'] = weapon
        flow.result['send'].append({'connection': connection, 'command': 5,
            'body': weapon_flow.encode_use_update(actor_id=rid, main_weapon_id=wire.WEAPON_ID,
                fist_weapon_id=weapons.FIST_ID, equipped_weapon_id=weapon, now_ms=flow.now),
            'reason': 'online-weapon-use-update'})
    # The native counter belongs to presentations, including corrections, not server phases.
    if rid == 1:
        sequence = online_prediction.remember(session, state, value['actionId'])
    fields = dict(instance_id=rid, seq_no=sequence, state=state,
        state_change_ms=flow.now - session['instanceStartedAt'], state_time_ms=integer(value['durationMs'], 0, 65535))
    target = value.get('targetActorId')
    if target is None:
        body = battle_flow.encode_battle_state_sync_simple(**fields)
    else:
        from . import online_scene
        target_rid = 0 if target == 0 else online_scene.actor_rid(match, target)
        if target_rid is None:
            raise ValueError('online reaction target outside roster')
        body = battle_flow.encode_battle_state_sync(**fields, target_rid=target_rid)
    flow.result['send'].append({'connection': connection, 'command': 4, 'body': body,
                                'reason': f'online-action actor={actor} state={state} event={event_sequence}'})


def weapon_reply(flow, payload, metadata):
    from . import contracts as wire, weapons
    from .custom_lobby import integer
    weapon = integer(payload['weaponId'], 1)
    if weapon not in (wire.WEAPON_ID, weapons.FIST_ID):
        raise ValueError('unsupported equipment acknowledgement')
    previous = metadata['previousWeapon']
    expected = weapons.FIST_ID if metadata['weaponSlot'] == weapon_flow.FIST_SLOT and previous != weapons.FIST_ID else wire.WEAPON_ID
    instances = [(int(k), s) for k, s in flow.state['sessions'].items()
                 if s['role'] == 'instance' and s.get('initialized') and not s.get('leaving')
                 and int(k) in flow.context.get('connections', [])]
    if len(instances) != 1:
        return
    flow.result['send'].append({'connection': instances[0][0], 'command': 5,
        'body': weapon_flow.encode_change_response(0 if weapon == expected else weapon_flow.CANNOT_CHANGE, weapon),
        'reason': 'online-weapon-change-response'})
