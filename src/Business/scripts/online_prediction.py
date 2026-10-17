"""Presentation-only local action prediction; authority reconciles every sequence."""
from . import battle, contracts as wire, weapons
from .codec import battle_flow

TIMER = 'online-action-prediction'
MAX_PENDING = 64
MAX_PRESENTATIONS = 128


def ledger(session):
    return session.setdefault('onlinePrediction', dict(seq=1, pending=[], active=None, blocked=False, actionId=None))


def valid(session):
    value = session.get('onlinePrediction')
    return value is None or (type(value) is dict and type(value.get('seq')) is int
        and 1 <= value['seq'] <= 65535 and type(value.get('pending')) is list
        and len(value['pending']) <= MAX_PENDING and type(value.get('blocked')) is bool
        and valid_history(value.get('history', []))
        and valid_active(value.get('active'))
        and valid_id(value.get('actionId'))
        and all(type(p) is list and len(p) == 3
                and all(type(n) is int and 1 <= n <= 65535 for n in p[:2]) and valid_id(p[2])
                for p in value['pending']))



def valid_history(history):
    return (type(history) is list and len(history) <= MAX_PRESENTATIONS
        and all(type(item) is list and len(item) == 3
            and all(type(n) is int and 1 <= n <= 65535 for n in item[:2])
            and valid_id(item[2]) for item in history)
        and len({item[0] for item in history}) == len(history))


def remember(session, state, action_id):
    value = ledger(session)
    value['seq'] = value['seq'] % 65535 + 1
    sequence = value['seq']
    # Retain acknowledged presentations for contacts drained later from the native journal.
    history = [item for item in value.get('history', []) if item[0] != sequence]
    value['history'] = [*history, [sequence, state, action_id]][-MAX_PRESENTATIONS:]
    return sequence


def contact_action(session, sequence, state):
    return next((item[2] for item in reversed(ledger(session).get('history', []))
                 if item[:2] == [sequence, state]), None)


def valid_id(value):
    return value is None or type(value) is int and 1 <= value <= 0xffffffff


def valid_active(value):
    if value is None:
        return True
    intents = {*battle.ATTACK_CHAINS, *battle.GUARD_CHAINS, battle.DASH_ATTACK}
    return (type(value) is dict and value.get('intent') in intents
        and type(value.get('index')) is int and 0 <= value['index'] <= 3
        and valid_id(value.get('actionId'))
        and type(value.get('released')) is bool
        and type(value.get('weapon')) is int and value['weapon'] > 0
        and (value.get('buffered') is None or type(value['buffered']) is list
             and len(value['buffered']) == 3 and value['buffered'][0] in intents
             and type(value['buffered'][1]) is bool and valid_id(value['buffered'][2])))


def emit(flow, state, duration=0):
    value = ledger(flow.session)
    if len(value['pending']) >= MAX_PENDING:
        # Stop speculation until the authority catches up; never drop unacknowledged states.
        return False
    remember(flow.session, state, value.get('actionId'))
    value['pending'].append([value['seq'], state, value.get('actionId')])
    flow.log('DEBUG', f'online-action-predicted seq={value["seq"]} state={state}')
    flow.send(4, battle_flow.encode_battle_state_sync_simple(instance_id=1,
        seq_no=value['seq'], state=state, state_change_ms=flow.now - flow.session['instanceStartedAt'],
        state_time_ms=duration), 'online-action-predicted')
    return True


def start(flow, intent, released=False, action_id=None):
    value = ledger(flow.session)
    dash = battle.dashProfile(flow) if intent == battle.DASH_ATTACK else None
    if intent == battle.DASH_ATTACK and (dash is None or weapons.equipped(flow.session) == weapons.FIST_ID):
        return
    chain = battle.attackChain(intent) or battle.GUARD_CHAINS[intent]
    index = 1 if intent == battle.DASH_ATTACK else 0
    duration = battle.phaseMs(intent, index, dash=dash, weaponId=weapons.equipped(flow.session), profile=weapons.profile(flow.session))
    value['actionId'] = action_id
    if not emit(flow, chain[index], duration):
        return
    value['active'] = dict(intent=intent, index=index, released=released or intent == battle.DASH_ATTACK,
        buffered=None, actionId=action_id, dash=dash, weapon=weapons.equipped(flow.session))
    flow.later(TIMER, max(1, duration))


def finish(flow, buffered=None):
    ledger(flow.session)['active'] = None
    flow.cancel(TIMER)
    if buffered:
        start(flow, *buffered)
    else:
        emit(flow, 2)


def input(flow, intent, action_id):
    if intent == battle.SHIELD_RELEASE:
        intent = battle.BLOCK_RELEASE
    if intent == battle.SHIELD_GUARD and not flow.session.get('offhandId'):
        return
    value = ledger(flow.session)
    if value['blocked'] or not flow.session.get('onlineCombat', {}).get('health', 100):
        return
    active = value['active']
    gesture = intent in battle.ATTACK_CHAINS or intent in battle.GUARD_CHAINS or intent == battle.DASH_ATTACK
    if active is None:
        if gesture:
            start(flow, intent, action_id=action_id)
        return
    if intent == battle.DASH_ATTACK:
        return
    if gesture:
        different_kind = (intent in battle.GUARD_CHAINS) != (active['intent'] in battle.GUARD_CHAINS)
        if active['index'] == 3 or active['index'] < 2 and different_kind and active['intent'] != battle.DASH_ATTACK:
            flow.cancel(TIMER)
            start(flow, intent, action_id=action_id)
        elif active['intent'] in battle.ATTACK_CHAINS or active['index'] >= 2 or active['intent'] == battle.DASH_ATTACK:
            active['buffered'] = [intent, False, action_id]
        return
    buffered = active['buffered']
    if buffered and intent == (battle.BLOCK_RELEASE if buffered[0] in battle.GUARD_CHAINS else battle.ATTACK_RELEASE):
        buffered[1] = True
    expected = battle.BLOCK_RELEASE if active['intent'] in battle.GUARD_CHAINS else battle.ATTACK_RELEASE
    if intent != expected or active['released']:
        return
    if expected == battle.BLOCK_RELEASE:
        finish(flow, buffered)
    else:
        active['released'] = True
        if active['index'] == 1 and active['intent'] in battle.ATTACK_CHAINS:
            advance(flow, active, 2)


def advance(flow, active, index):
    chain = battle.attackChain(active['intent']) or battle.GUARD_CHAINS[active['intent']]
    duration = battle.phaseMs(active['intent'], index, dash=active['dash'],
                             weaponId=active['weapon'], profile=weapons.profile(flow.session, active['weapon']))
    if not emit(flow, chain[index], duration):
        return
    active['index'] = index
    flow.cancel(TIMER)
    if index != 1 or active['released']:
        flow.later(TIMER, max(1, duration))


def timer(flow, name):
    if name != TIMER:
        return False
    value = ledger(flow.session)
    active = value['active']
    if (flow.state.get('onlineScene', {}).get('phase') != 3 or flow.session.get('leaving')
            or not flow.session.get('onlineCombat', {}).get('health', 100)):
        value.update(active=None, pending=[], blocked=True)
        return True
    if active is None:
        return True
    index = active['index']
    if active['intent'] == battle.DASH_ATTACK or index >= 3 or index == 2 and active['buffered']:
        finish(flow, active['buffered'])
    elif index == 0:
        advance(flow, active, 2 if active['released'] else 1)
    elif index == 2:
        advance(flow, active, 3)
    return True


def reconcile(flow, session, sequence, state, action_id):
    value = ledger(session)
    pending = value['pending']
    match = next((index for index, item in enumerate(pending)
                  if action_id is not None and item[1:] == [state, action_id]), None)
    if match is not None:
        # Authority may skip a predicted idle when the next input interrupts recovery.
        # Native sequence numbers count presentations, not authoritative action identity.
        del pending[:match + 1]
        flow.log('DEBUG', f'online-action-confirmed seq={sequence} state={state}')
        return True
    # A mismatch or a reaction invalidates speculative continuations, including their timer.
    if pending:
        flow.log('INFO', f'online-action-corrected seq={sequence} state={state} discarded={len(pending)}')
    value['actionId'] = action_id
    clear(session, blocked=state not in (1, 2))
    for intent, chain in battle.GUARD_CHAINS.items():
        if state == chain[1]:
            # A corrected held guard is actionable, not a hurt lock. Restoring it
            # lets release or an attack interrupt predict immediately again.
            value.update(blocked=False, active=dict(intent=intent, index=1,
                released=False, buffered=None, actionId=action_id, dash=None, weapon=weapons.equipped(session)))
            break
    return False


def clear(session, *, blocked=True):
    value = ledger(session)
    value.update(pending=[], active=None, blocked=blocked)
    session.get('pending', {}).pop(TIMER, None)
