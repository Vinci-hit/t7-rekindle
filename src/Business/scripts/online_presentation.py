"""Present combat consequences when the swing that caused them is visible on this screen.

Remote actors are drawn through the playback clock (online_playback), behind their server timeline.
Rust stamps every contact verdict, and every reaction it causes, with the moment the attacker's
blade met the target on that timeline. Those consequences wait until the attacker's playback
reaches that moment, so damage, blood and recoil land with the blade the player sees. The local
player's own swing has no playback delay, so its consequences are never held.

Order is preserved per channel: combat verdicts carry whole-roster lives and apply in sequence;
actions keep their per-actor sequence. A held entry therefore holds later entries of its channel.
"""
from . import online_playback

MAX_HELD = 64
# Even a stalled attacker playback never holds a verdict longer than the playback delay itself.
MAX_HOLD_MS = online_playback.MAX_DELAY_MS
ENTRY_KEYS = frozenset(('channel', 'rid', 'cause', 'at', 'deadline', 'value'))


def valid(session):
    value = session.get('onlinePresentation')
    return value is None or (session['role'] == 'instance' and type(value) is dict
        and set(value) == {'held'} and type(value['held']) is list and len(value['held']) <= MAX_HELD
        and all(valid_entry(entry) for entry in value['held']))


def valid_entry(entry):
    return (type(entry) is dict and set(entry) == ENTRY_KEYS
        and entry['channel'] in ('combat', 'action') and type(entry['value']) is dict
        and (entry['rid'] is None) == (entry['channel'] == 'combat')
        and (entry['rid'] is None or type(entry['rid']) is int)
        and (entry['cause'] is None) == (entry['at'] is None)
        and (entry['cause'] is None or type(entry['cause']) is int and type(entry['at']) is int)
        and type(entry['deadline']) is int)


def ledger(session):
    return session.setdefault('onlinePresentation', {'held': []})


def pending(session):
    return bool(session.get('onlinePresentation', {}).get('held'))


def cause_rid(match, cause):
    """The remote actor whose playback shows the causing swing, or None to present now."""
    from . import online_scene
    if cause is None:
        return None
    rid = online_scene.actor_rid(match, cause[0])
    return None if rid in (None, 1) else rid


def due(session, entry, now):
    from . import online_pve
    if entry['cause'] is None or now >= entry['deadline']:
        return True
    buffer = session.get('onlineMotion', {}).get('buffers', {}).get(str(entry['cause']), {})
    if not buffer.get('samples') or 'renderAt' not in buffer or online_pve.held(session, entry['cause']):
        return True
    # Same server-to-playback conversion as queued remote actions.
    return buffer['renderAt'] >= entry['at'] - buffer.get('serverTickOffset', 0)


def order(entry):
    return 'combat' if entry['channel'] == 'combat' else f"action:{entry['rid']}"


def submit(flow, connection, session, channel, rid, cause, value):
    """Hold a sequenced event until its cause is visible. Returns False when it is due now.

    `cause` is `(actorId, serverTimeMs)` from Rust, or None for an event with no causing swing.
    """
    from . import online_motion
    state = ledger(session)
    attacker = cause_rid(flow.state['onlineScene'], cause)
    entry = dict(channel=channel, rid=rid, cause=attacker, at=None if attacker is None else cause[1],
                 deadline=flow.now + MAX_HOLD_MS, value=value)
    if order(entry) not in {order(held) for held in state['held']} and due(session, entry, flow.now):
        return False
    if len(state['held']) >= MAX_HELD:
        raise ValueError('combat presentation queue overflow')
    state['held'].append(entry)
    online_motion.schedule_render(flow, connection, session)
    return True


def release(flow):
    """Deliver every held event whose cause is now visible, in channel order."""
    state = flow.session.get('onlinePresentation')
    if not state or not state['held']:
        return
    blocked, kept, ready = set(), [], []
    for entry in state['held']:
        if order(entry) not in blocked and due(flow.session, entry, flow.now):
            ready.append(entry)
        else:
            blocked.add(order(entry))
            kept.append(entry)
    state['held'] = kept
    for entry in ready:
        deliver(flow, entry)


def deliver(flow, entry):
    from . import online_actions, online_combat
    if entry['channel'] == 'combat':
        online_combat.present(flow, entry['value']['effect'])
    else:
        online_actions.deliver(flow, flow.connection, flow.session, entry['rid'], entry['value'])


def clear(session):
    session.pop('onlinePresentation', None)
