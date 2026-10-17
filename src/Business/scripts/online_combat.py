"""Send local native contacts to Rust and render shared combat decisions."""
from . import online_limits, online_prediction
import math
from . import actors, contact_journal, contracts as wire, controls, online_scene, roster
from .codec import attr_flow, battle_flow, kill_flow, room_flow, vision_flow

STRIKES = frozenset((238, 244, 250, 256, 439))


def ledger(session):
    return session.setdefault('onlineCombat', dict(inputSeq=0, eventSeq=0, seen=[], health=100, ready=False))


def valid(session):
    value = session.get('onlineCombat')
    return value is None or (session['role'] == 'instance' and type(value) is dict
        and all(type(value.get(k)) is int and 0 <= value[k] <= limit for k, limit in (
            ('inputSeq', 0xffffffff), ('eventSeq', 0xffffffffffffffff), ('health', 100)))
        and type(value.get('ready')) is bool and type(value.get('seen')) is list and len(value['seen']) <= 64
        and all(type(key) is str and len(key) <= 64 for key in value['seen'])
        and valid_scores(value.get('scores', {})))


def valid_scores(values):
    return (type(values) is dict and len(values) <= online_limits.ROOM_CAPACITY
        and all(key in online_limits.ALL_RIDS and type(value) is dict and set(value) == {'kills', 'deaths', 'streak'}
            and all(type(n) is int and 0 <= n <= kill_flow.MAX_SCORE for n in value.values())
            for key, value in values.items()))


def send(flow, kind, command, **metadata):
    from . import custom_lobby
    gateways = [(int(k), s) for k, s in flow.state['sessions'].items()
        if s.get('customLobby', {}).get('identified') and int(k) in flow.context.get('connections', [])]
    if len(gateways) != 1:
        raise ValueError('online combat requires the identified gateway')
    if gateways[0][1]['customLobby'].get('paused') or len(gateways[0][1]['customLobby']['pending']) >= 6:
        return False
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = gateways[0]
        custom_lobby.send(flow, kind, command, **metadata)
    finally:
        flow.connection, flow.session = connection, session
    return True


def world_updated(flow):
    match = flow.state['onlineScene']
    if match['phase'] != 3 or flow.session.get('leaving') or not ledger(flow.session)['health']:
        return
    raw = flow.context.get('botWorld', {})
    # PVE monsters occupy enemy roster slots, so their hits are native blade contacts like players'.
    trace_reactions(flow, actors.world(flow))
    journal = raw.get('contactJournal')
    if type(journal) is not dict or raw.get('instanceStartedAt') != flow.session['instanceStartedAt']:
        return
    through = journal.get('throughAt')
    if (type(through) not in (int, float) or not math.isfinite(through) or not 0 <= through <= flow.now
            or type(journal.get('batchId')) is not int or journal['batchId'] < 0
            or type(journal.get('events')) is not list or len(journal['events']) > 8192
            or not all(contact_journal.valid(event, through) for event in journal['events'])):
        raise ValueError('invalid online native contact journal')
    state = ledger(flow.session)
    identity = [raw['instanceStartedAt'], journal['batchId']]
    if state.get('cursor') == identity:
        return
    local = raw.get('actors', {}).get('1', {})
    for event in sorted(journal['events'], key=lambda e: (e['occurredAt'], e['along'])):
        contact_start, contact_end = contact_window(flow.session, event['state'])
        # Ignore windup overlaps before deduplication so a later valid hit can be sent.
        if event['occurredAt'] - event['phaseAt'] < contact_start:
            continue
        if (event['occurredAt'] < state.get('resumeAt', 0) or event['actorId'] != 1 or event['state'] not in STRIKES or event['entity'] != local.get('entity')
                or event['targetId'] not in (0, *online_scene.ids(flow)[1:]) or event['occurredAt'] < event['phaseAt']
                or event['occurredAt'] - event['phaseAt'] > contact_end):
            continue
        target = 0 if event['targetId'] == 0 else online_scene.rid_actor(match, event['targetId'])
        if target is None:
            continue
        action_id = online_prediction.contact_action(flow.session, event['seq'], event['state'])
        if action_id is None:
            flow.log('WARN', 'online-contact-missing-action-identity')
            continue
        weapon_contact = event['defenceWeaponId'] != 0
        key = f"{event['seq']}:{target}:{int(weapon_contact)}"
        if key in state['seen']:
            continue
        if state['inputSeq'] == 0xffffffff:
            raise ValueError('online contact sequence exhausted')
        sequence = state['inputSeq'] + 1
        report = dict(actionSeq=event['seq'], actionId=action_id, state=event['state'],
            elapsedMs=int(event['occurredAt'] - event['phaseAt']), targetActorId=target,
            position=event['position'], direction=event['direction'], normal=event['normal'], material=event['material'],
            timing=dict(sampleTimeMs=int(event['occurredAt']), phaseStartMs=int(event['phaseAt']),
                        gatewayTimeMs=int(flow.now)))
        if weapon_contact:
            report['weaponContact'] = True
        from . import online_motion
        payload = dict(matchId=match['matchId'], inputSeq=sequence, contact=report)
        pose_checkpoint = online_motion.checkpoint(flow.session)
        if pose_checkpoint is not None:
            payload['pose'] = pose_checkpoint
        if not send(flow, 'contact', dict(type='contactInput', payload=payload),
                matchId=match['matchId'], inputSeq=sequence):
            return
        state['inputSeq'] = sequence
        state['seen'] = [*state['seen'], key][-64:]
        age = int(flow.now - event['occurredAt'])
        flow.log('INFO', f'online-contact-report input={sequence} action={event["seq"]} state={event["state"]} target={target} weapon={weapon_contact} targetState={event["targetState"]} sampleAgeMs={age}')
    state['cursor'] = identity


def trace_reactions(flow, snapshot):
    if snapshot is None:
        return
    state = ledger(flow.session)
    previous = state.setdefault('nativeReactions', {})
    for rid in online_scene.ids(flow):
        actor = actors.pose(snapshot, rid)
        if actor is None or type(actor.get('state')) is not int:
            continue
        sample = [actor['state'], actor.get('seq'), actor.get('phaseAt')]
        old = previous.get(str(rid))
        if sample != old:
            if sample[0] == 35 or old is not None and old[0] == 35:
                flow.log('DEBUG', f'online-native-reaction rid={rid} state={sample[0]} seq={sample[1]} phaseAt={sample[2]} previous={old}')
            previous[str(rid)] = sample


def contact_window(session, state):
    from . import weapons
    profile = weapons.profile(session)
    if profile:
        if state == 439:
            dash = profile.get('dashAttack') or {}
            return dash.get('contactStartMs', 0), dash.get('contactEndMs', 0)
        intent = {238: 300560, 244: 300550, 250: 300570, 256: 300540}.get(state)
        attack = profile['attacks'].get(str(intent))
        if attack:
            return attack['contactStartMs'], attack['contactEndMs']
    start = online_limits.THRUST_CONTACT_START_MS if state == 250 and weapons.equipped(session) != weapons.FIST_ID else 0
    return start, 1133 if state == 439 else 600


def can_choose(flow):
    return flow.session.get('onlineCombat', {}).get('ready') is True


def message(flow, command, selector, body):
    if flow.session['role'] != 'instance' or ledger(flow.session)['health']:
        return False
    if command == 0x36 and selector == 0x64:
        room_flow.decode_actor_play_request(body)
        if not can_choose(flow) or not flow.session.get('heroChosen'):
            flow.send(command, room_flow.encode_actor_play_response(result=1), 'online-respawn-not-ready')
        elif not flow.session.get('respawnPending'):
            match = flow.state['onlineScene']
            if send(flow, 'respawn', dict(type='respawn', payload=dict(matchId=match['matchId'])), matchId=match['matchId']):
                flow.session['respawnPending'] = True
        return True
    return False


def receive(flow, value):
    from .custom_lobby import integer
    match = flow.state['onlineScene']
    room = flow.session['customLobby'].get('room')
    if value['matchId'] != match['matchId'] or room is not None and value['roomId'] != room['roomId']:
        raise ValueError('online combat belongs to another scene')
    if match['phase'] == 4:
        return
    if match['phase'] != 3 or room is None:
        raise ValueError('online combat precedes Running')
    instances = [(int(k), s) for k, s in flow.state['sessions'].items() if s['role'] == 'instance'
        and s.get('initialized') and not s.get('leaving') and int(k) in flow.context.get('connections', [])]
    if len(instances) != 1:
        raise ValueError('online combat requires one instance')
    connection, session = flow.connection, flow.session
    try:
        flow.connection, flow.session = instances[0]
        state = ledger(flow.session)
        sequence = integer(value['eventSeq'], 1, 0xffffffffffffffff)
        if sequence != state['eventSeq'] + 1:
            raise ValueError('online combat event gap or replay')
        integer(value['serverTimeMs'], 0, 0xffffffffffffffff)
        state['eventSeq'] = sequence
        effect = value['effect']
        kind = effect['type']
        if kind == 'contact':
            contact(flow, effect)
        elif kind == 'respawnReady':
            if rid(flow, effect['actorId']) == 1:
                if state['health']:
                    raise ValueError('alive actor cannot select a respawn')
                state['ready'] = True
                flow.send(0x36, wire.actorState(flow.now, 7, userId=flow.userId), 'online-respawn-select-hero')
        elif kind == 'respawned':
            respawn(flow, rid(flow, effect['actorId']), effect['lives'])
        else:
            raise ValueError('unsupported combat effect')
    finally:
        flow.connection, flow.session = connection, session


def rid(flow, actor):
    match = flow.state['onlineScene']
    result = online_scene.actor_rid(match, actor)
    if result is None:
        raise ValueError('combat actor outside frozen roster')
    return result


def health(flow, lives):
    from .custom_lobby import integer
    from . import online_pve
    match = flow.state['onlineScene']
    if type(lives) is not list:
        raise ValueError('invalid combat roster')
    # Players are the frozen roster; monsters are those currently bound to a slot. A monster the
    # server already tracks but whose lifecycle event is still in flight has no slot yet.
    players = [life for life in lives if not online_pve.monster_id(life.get('actorId'))]
    bound = [life for life in lives if online_pve.monster_id(life.get('actorId'))
             and online_scene.actor_rid(match, life['actorId']) is not None]
    roster = [rid for rid in online_scene.ids(flow) if rid == 1 or not online_scene.monster(flow, rid)]
    if len(players) != len(roster):
        raise ValueError('invalid combat roster')
    values = {}
    for life in players + bound:
        actor = rid(flow, life['actorId'])
        if actor in values:
            raise ValueError('duplicate combat actor')
        monster = online_pve.by_rid(flow.session, actor) if actor != 1 else None
        values[actor] = integer(life['health'], 0, monster['kind']['health'] if monster else 100)
        for key in ('kills', 'deaths', 'streak'):
            integer(life[key], 0, kill_flow.MAX_SCORE)
    for actor, hp in values.items():
        monster = online_pve.by_rid(flow.session, actor) if actor != 1 else None
        if monster is not None:
            monster['health'] = hp
            online_scene.peer_data(flow.session, actor)['health'] = hp
            flow.send(15, attr_flow.encode_health_update(entity_rid=actor,health=hp,server_time_ms=flow.now), 'online-health-monster')
        elif actor == 1:
            ledger(flow.session)['health'] = hp
            flow.session['health'] = hp
            if not hp:
                from . import online_prediction
                online_prediction.clear(flow.session)
            flow.send(4, battle_flow.encode_battle_data_update_notify(
                result_type=battle_flow.RESULT_DATA_HP, result_data=hp), 'online-health-local')
        else:
            online_scene.peer_data(flow.session, actor)['health'] = hp
            flow.send(15, attr_flow.encode_health_update(entity_rid=actor, health=hp,
                server_time_ms=flow.now), 'online-health-peer')
    return values


def scores(flow, lives):
    from . import online_pve
    lives = [life for life in lives if not online_pve.monster_id(life['actorId'])]
    state = ledger(flow.session)
    previous = state.get('scores', {})
    current = {str(rid(flow, life['actorId'])): dict(kills=life['kills'], deaths=life['deaths'], streak=life['streak'])
        for life in lives}
    first_blood = not any(value['kills'] for value in previous.values())
    camp_kills = {1: 0, 2: 0}
    match = flow.state['onlineScene']
    for key, totals in current.items():
        actor = int(key)
        before = previous.get(key, dict(kills=0, deaths=0, streak=0))
        camp_kills[match['localCamp'] if actor == 1 else online_scene.peer_data(flow.session, actor)['camp']] += totals['kills']
        if totals['kills'] != before['kills'] or totals['deaths'] != before['deaths']:
            flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_update_event(server_time_ms=flow.now,
                entity_rid=actor, previous_kills=before['kills'], kills=totals['kills'],
                previous_deaths=before['deaths'], deaths=totals['deaths'],
                previous_points=min(kill_flow.MAX_SCORE, before['kills'] * 100),
                points=min(kill_flow.MAX_SCORE, totals['kills'] * 100),
                stat_message_id=kill_flow.FIRST_BLOOD_MESSAGE_ID if first_blood and totals['kills'] > before['kills'] else 0),
                'online-score')
        if totals['streak'] > before['streak'] and totals['streak'] >= 2:
            flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_stat_message(server_time_ms=flow.now,
                entity_rid=actor, count=totals['streak']), 'online-kill-streak')
        if before['streak'] >= kill_flow.BUTCHER_STREAK and totals['deaths'] > before['deaths']:
            winner = next(int(k) for k in current if current[k]['kills'] > previous.get(k, {}).get('kills', 0))
            flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_butcher_killer_message(server_time_ms=flow.now,
                entity_rid=winner), 'online-butcher-killer')
    if current != previous:
        flow.send(kill_flow.SCRIPT_COMMAND, kill_flow.encode_camp_kill_update(server_time_ms=flow.now,
            instance_id=1, blue_kills=min(kill_flow.MAX_SCORE, camp_kills[1]),
            red_kills=min(kill_flow.MAX_SCORE, camp_kills[2]), kill_target=flow.session['killTarget']), 'online-camp-score')
    state['scores'] = current


def contact(flow, effect):
    from .custom_lobby import integer
    from . import online_pve
    match = flow.state['onlineScene']
    # A monster removed (or not yet bound) by an in-flight lifecycle event has no slot to present on.
    if any(online_pve.monster_id(actor) and online_scene.actor_rid(match, actor) is None
           for actor in (effect['attacker'], effect['target'])):
        return
    attacker = rid(flow, effect['attacker'])
    target = 0 if effect['target'] is None else rid(flow, effect['target'])
    if attacker == target or type(effect['parried']) is not bool:
        raise ValueError('invalid combat outcome')
    sequence = integer(effect['actionSeq'], 1, 0xffff)
    weapon = integer(effect['weaponId'], 1)
    target_weapon = integer(effect['targetWeaponId'], 0)
    damage = integer(effect['damage'], 0, 100)
    part = effect['hitPart']
    parts = {'head': battle_flow.HIT_PART_HEAD, 'body': battle_flow.HIT_PART_BODY,
             'legs': battle_flow.HIT_PART_LEGS}
    if type(part) is not str or part not in parts:
        raise ValueError('invalid combat hit part')
    hit_part = parts[part]
    material = integer(effect['material'], -1, 7)
    for key in ('position', 'normal', 'direction'):
        if not actors.validPosition(effect[key]):
            raise ValueError('invalid combat contact vector')
    if any(not .9 <= math.hypot(*effect[key]) <= 1.1 for key in ('normal', 'direction')):
        raise ValueError('invalid combat contact normal')
    flow.log('DEBUG', f'online-contact-verdict event={ledger(flow.session)["eventSeq"]} attacker={attacker} target={target} swing={sequence} parried={effect["parried"]} damage={damage}')
    values = health(flow, effect['lives'])
    if attacker == 1:
        # The body and weapon probes may overlap on successive native frames.
        # Once adjudicated, this swing/target cannot report another outcome.
        seen = ledger(flow.session)['seen']
        seen.extend(f'{sequence}:{effect["target"] or 0}:{kind}' for kind in (0, 1))
        del seen[:-64]
    # Parry reaction states already play the native effects on both weapons.
    # Body and scene contacts still need their contact-point feedback.
    if not effect['parried']:
        from . import online_pve
        birth_active = bool(target) and online_pve.in_birth(flow.session, target) and values.get(target, 0) > 0
        # Native HurtType also gates blood and flesh effects. Enable the normal
        # hit path only for an authoritative damaging body contact.
        flow.send(4, battle_flow.encode_battle_hit_point_notify(hit_pos=tuple(effect['position']),
            hit_direction=tuple(effect['direction']), hit_normal=tuple(effect['normal']), material_id=material,
            hit_part=hit_part, ignore_hurt=birth_active or not (target and damage), attacker_mid=attacker, target_mid=target,
            attacker_weapon_id=weapon, target_weapon_id=0,
            server_time_ms=flow.now), 'online-contact-effect')
    if effect['parried']:
        flow.send(4, battle_flow.encode_battle_defense_notify(attacker_mid=attacker, defender_mid=target,
            attacker_weapon_id=weapon, target_weapon_id=target_weapon, server_time_ms=flow.now), 'online-parry-sound')
    if not effect['parried'] and target and damage:
        killed = values[target] == 0
        if attacker == 1:
            flow.send(4, battle_flow.encode_battle_result(attacker_rid=attacker, target_rid=target,
                attacker_seq_no=sequence, target_rb_index=hit_part, target_life_state=battle_flow.LIFE_STATE_KILL if killed else battle_flow.LIFE_STATE_NONE,
                attack_weapon_id=weapon, target_result_type=battle_flow.RESULT_DATA_DELTA_HP,
                target_result_data=-damage), 'online-damage')
        if killed:
            flow.send(kill_flow.INSTANCE_COMMAND, kill_flow.encode_kill_event(server_time_ms=flow.now,
                source_rid=attacker, target_rid=target, weapon_tid=weapon), 'online-kill')
            flow.send(4, battle_flow.encode_battle_dead_notify(entity_rid=target, server_time_ms=flow.now), 'online-dead')
            if target == 1:
                flow.session['heroChosen'] = False
                flow.session['groundEnabled'] = False
                ledger(flow.session)['ready'] = False
                controls.activate(flow)
                flow.send(0x36, wire.actorState(flow.now, 9, userId=flow.userId), 'online-observer')
    scores(flow, effect['lives'])
    flow.log('DEBUG', f'online-contact attacker={attacker} target={target} parried={effect["parried"]} damage={damage}'
        f' actionSeq={sequence} eventSeq={ledger(flow.session)["eventSeq"]} position={effect["position"]}')


def respawn(flow, actor, lives):
    reacting = flow.session.get('onlineActions', {}).get('reacting', [])
    if str(actor) in reacting:
        reacting.remove(str(actor))
    match = flow.state['onlineScene']
    source = flow.session if actor == 1 else online_scene.peer_data(flow.session, actor)
    position, heading = source['spawnPosition'], source['spawnHeading']
    health(flow, lives)
    scores(flow, lives)
    if actor == 1:
        from . import online_prediction
        online_prediction.clear(flow.session, blocked=False)
        flow.session.pop('respawnPending', None)
        ledger(flow.session).update(ready=False, seen=[])
        flow.session.update(spawnPosition=list(position), spawnHeading=heading)
        controls.groundState(flow).update(position=list(position), heading=heading, mask=0, moveStartedAt=None)
        flow.send(0x36, room_flow.encode_actor_play_response(), 'online-respawn-play')
        flow.send(0x36, wire.actorState(flow.now, 6, userId=flow.userId), 'online-respawn-in-scene')
    else:
        source.update(position=list(position), heading=heading)
        motion = flow.session.get('onlineMotion', {})
        motion = motion.get('buffers', {}).get(str(actor), {})
        for key in ('samples', 'renderedPose', 'peerMovement', 'renderAt'):
            motion.pop(key, None)
    flow.send(0xe, vision_flow.encode_vision_del_event(object_mid=actor), 'online-respawn-remove')
    flow.send(0xe, roster.playerVision(flow) if actor == 1 else online_scene.peerVision(flow, actor), 'online-respawn-add')
    weapon = source.get('weaponId', wire.WEAPON_ID)
    if weapon != wire.WEAPON_ID:
        from .codec import weapon_flow
        from . import weapons
        flow.send(5, weapon_flow.encode_equipment_update(actor_id=actor, items=weapons.inventory(source),
            equipped_weapon_id=weapon, offhand_id=source.get('offhandId', 0), now_ms=flow.now), 'online-respawn-equipment')
    if actor == 1:
        controls.enableGround(flow)
