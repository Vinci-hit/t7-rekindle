"""Server-issued clock calibration; raw frame timestamps remain unchanged."""
import uuid


def identifier(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        raise ValueError('invalid combat clock identifier')
    return value


def challenge(payload):
    return identifier(payload['epoch']), identifier(payload['nonce'])


def valid(value):
    if value is None:
        return True
    try:
        if type(value) is not dict or set(value) != {'epoch', 'offsetMs', 'uncertaintyMs', 'roundTripMs', 'samples', 'at'}:
            return False
        identifier(value['epoch'])
        return all(type(value[key]) is int and low <= value[key] <= high for key, low, high in (
            ('offsetMs', -(1 << 63), (1 << 63) - 1), ('uncertaintyMs', 0, (1 << 64) - 1),
            ('roundTripMs', 0, 2000), ('samples', 1, 32), ('at', 0, (1 << 64) - 1)))
    except (ValueError, TypeError, AttributeError):
        return False


def accept(gateway, payload, expected_epoch, now):
    value = dict(payload, at=now)
    if not valid(value) or value['epoch'] != expected_epoch:
        raise ValueError('invalid combat clock estimate')
    if gateway.get('clock', {}).get('epoch') != value['epoch']:
        gateway['combatSeq'] = 0
    gateway['clock'] = value


def stamp(gateway, command, now, *, motion=False, received_at=None):
    clock = gateway.get('clock')
    if clock is None:
        return None
    kind = command['type']
    payload = command.get('payload', {})
    if motion:
        frame = payload.get('pose', {}).get('motion')
        if frame is None:
            return None
        sampled, source = frame['tick'], 'nativePose'
    elif kind == 'contactInput':
        sampled, source = payload['contact']['timing']['sampleTimeMs'], 'nativeContact'
    elif kind == 'actionInput':
        # The original wire input has no verified game-frame timestamp yet.
        sampled, source = now if received_at is None else received_at, 'gatewayInput'
    elif kind in ('clockEcho', 'combatHeartbeat'):
        sampled, source = now, 'heartbeat'
    else:
        return None
    sequence = gateway.get('combatSeq', 0)
    if not motion:
        if sequence == (1 << 64) - 1:
            raise ValueError('combat stream sequence exhausted')
        sequence += 1
        gateway['combatSeq'] = sequence
    return dict(epoch=clock['epoch'], combatSeq=sequence, sampledMs=int(sampled),
                gatewayMs=now, source=source)


def heartbeat(flow):
    from . import custom_lobby, online_limits
    gateway = flow.session.get('customLobby')
    if gateway is None or flow.session.get('leaving'):
        return
    scene = flow.state.get('onlineScene', {})
    if (scene.get('phase') == 3 and gateway.get('clock') is not None
            and len(gateway['pending']) < 4
            and sum(item['kind'] == 'combat-heartbeat' for item in gateway['pending'].values()) < 2):
        custom_lobby.send(flow, 'combat-heartbeat', dict(type='combatHeartbeat'))
    interval = (online_limits.COMBAT_HEARTBEAT_INTERVAL_MS if scene.get('phase') == 3
                else online_limits.SERVER_PING_INTERVAL_MS)
    flow.later('online-combat-heartbeat', interval)
