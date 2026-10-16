"""Sweep the sampled local blade against server-owned zombie body volumes.

Monster RIDs do not fit the native player contact slots. This adapter uses the
native blade endpoints, rather than inventing a hit from attack direction.
"""
import math

from . import actors, online_prediction

BODY_RADIUS = 0.3
BODY_HEIGHT = 1.8
SWEEP_STEP = 0.08
MAX_SWEEP_STEPS = 64


def body_contact(base, tip, feet):
    """First intersection along a blade segment with an upright body cylinder."""
    delta = [tip[i] - base[i] for i in range(3)]
    low, high = 0., 1.
    if abs(delta[2]) < 1e-8:
        if not feet[2] <= base[2] <= feet[2] + BODY_HEIGHT:
            return None
    else:
        bounds = sorted(((feet[2] - base[2]) / delta[2],
                         (feet[2] + BODY_HEIGHT - base[2]) / delta[2]))
        low, high = max(low, bounds[0]), min(high, bounds[1])
    x, y = base[0] - feet[0], base[1] - feet[1]
    a = delta[0] ** 2 + delta[1] ** 2
    c = x*x + y*y - BODY_RADIUS ** 2
    if a < 1e-8:
        if c > 0:
            return None
    else:
        b = 2 * (x*delta[0] + y*delta[1])
        discriminant = b*b - 4*a*c
        if discriminant < 0:
            return None
        root = math.sqrt(discriminant)
        low, high = max(low, (-b-root)/(2*a)), min(high, (-b+root)/(2*a))
    if low > high:
        return None
    return [base[i] + delta[i] * low for i in range(3)]


def world_updated(flow, raw):
    from . import online_combat
    monsters = flow.session.get('onlinePve', {}).get('monsters', {})
    if not monsters or raw.get('ready') is not True:
        return
    actor = raw.get('actors', {}).get('1', {})
    blade = raw.get('blades', {}).get('1', {})
    sampled = raw.get('sampledAt')
    if (actor.get('ready') is not True or blade.get('ready') is not True
            or raw.get('instanceStartedAt') != flow.session['instanceStartedAt']
            or type(sampled) is not int or not 0 <= flow.now - sampled <= 100
            or not all(actors.validPosition(blade.get(k)) for k in ('base', 'tip'))
            or not actors.validPosition(actor.get('position'))
            or not .01 <= math.dist(blade['base'], blade['tip']) <= 8
            or math.dist(blade['base'], actor['position']) > 5):
        return
    state = online_combat.ledger(flow.session)
    previous = state.get('pveBlade')
    current = dict(at=sampled, seq=actor.get('seq'), state=actor.get('state'),
                   phaseAt=actor.get('phaseAt'), base=list(blade['base']), tip=list(blade['tip']))
    if previous is not None and sampled <= previous['at']:
        return
    state['pveBlade'] = current
    if (current['state'] not in online_combat.STRIKES or type(current['phaseAt']) is not int
            or current['phaseAt'] < state.get('resumeAt', 0)):
        return
    action_id = online_prediction.contact_action(flow.session, current['seq'], current['state'])
    if action_id is None:
        return
    lower, upper = online_combat.contact_window(flow.session, current['state'])
    if not lower <= sampled - current['phaseAt'] <= upper:
        return
    if (previous is None or previous['seq'] != current['seq'] or previous['state'] != current['state']
            or previous['phaseAt'] != current['phaseAt'] or sampled - previous['at'] > 100
            or previous['at'] - current['phaseAt'] < lower):
        previous = current
    travel = max(math.dist(previous[k], current[k]) for k in ('base', 'tip'))
    steps = max(1, math.ceil(travel / SWEEP_STEP))
    if steps > MAX_SWEEP_STEPS:
        return
    # Linear endpoint interpolation stays inside this conservative sweep box.
    # Reject distant monsters once, before doing per-step cylinder tests.
    endpoints = (previous['base'], previous['tip'], current['base'], current['tip'])
    low = [min(p[i] for p in endpoints) for i in range(3)]
    high = [max(p[i] for p in endpoints) for i in range(3)]
    for key, monster in monsters.items():
        identity = f"{current['seq']}:{key}:0"
        if not monster['alive'] or identity in state['seen']:
            continue
        feet = monster['position']
        if (any(high[i] < feet[i] - BODY_RADIUS or low[i] > feet[i] + BODY_RADIUS for i in (0, 1))
                or high[2] < feet[2] or low[2] > feet[2] + BODY_HEIGHT):
            continue
        for step in range(steps + 1):
            alpha = step / steps
            base, tip = ([previous[k][i] + (current[k][i] - previous[k][i]) * alpha
                          for i in range(3)] for k in ('base', 'tip'))
            point = body_contact(base, tip, monster['position'])
            if point is None:
                continue
            direction = [tip[i] - base[i] for i in range(3)]
            length = math.hypot(*direction)
            if length < .01:
                continue
            direction = [v / length for v in direction]
            occurred = round(previous['at'] + (sampled - previous['at']) * alpha)
            if state['inputSeq'] == 0xffffffff:
                raise ValueError('online contact sequence exhausted')
            sequence = state['inputSeq'] + 1
            report = dict(actionSeq=current['seq'], actionId=action_id, state=current['state'],
                elapsedMs=occurred-current['phaseAt'], targetActorId=int(key), position=point,
                direction=direction, normal=[-v for v in direction], material=0,
                timing=dict(sampleTimeMs=occurred, phaseStartMs=current['phaseAt'], gatewayTimeMs=int(flow.now)))
            from . import online_motion
            payload = dict(matchId=flow.state['onlineScene']['matchId'], inputSeq=sequence, contact=report)
            checkpoint = online_motion.checkpoint(flow.session)
            if checkpoint is not None:
                payload['pose'] = checkpoint
            if not online_combat.send(flow, 'contact', dict(type='contactInput', payload=payload),
                    matchId=payload['matchId'], inputSeq=sequence):
                return
            state['inputSeq'] = sequence
            state['seen'] = [*state['seen'], identity][-64:]
            flow.log('DEBUG', f'online-pve-blade-contact target={key} height={point[2]-monster["position"][2]:.3f}')
            break
