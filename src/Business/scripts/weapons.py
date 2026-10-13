"""The client's hand-toggle and primary-slot requests; no keyboard interception."""
from . import actors, contracts as wire
from .codec import weapon_flow

FIST_ID = 100000
CHANGE_STATE, CHANGE_MS = 1, 100


def equipped(data):
    return data.get('weaponId', wire.WEAPON_ID)


def validState(data):
    return type(equipped(data)) is int and equipped(data) in (wire.WEAPON_ID, FIST_ID)


def message(flow, command, selector, body):
    from . import battle
    if (command != weapon_flow.WEAPON_COMMAND or selector != weapon_flow.CHANGE_REQUEST
            or flow.session['role'] != 'instance'):
        return False
    slot = weapon_flow.decode_change_request(body)
    current = equipped(flow.session)
    action = flow.session.get('action', {})
    result = 0
    if not battle.enabled(flow) or not actors.active(flow, wire.ACTOR_ID):
        return True
    if slot not in (weapon_flow.MAIN_SLOT, weapon_flow.FIST_SLOT):
        result = weapon_flow.INVALID_SLOT
    elif 'hurtUntil' in flow.session or action.get('index', -1) >= 0 or action.get('reaction'):
        result = weapon_flow.CANNOT_CHANGE
    target = FIST_ID if slot == weapon_flow.FIST_SLOT and current != FIST_ID else wire.WEAPON_ID
    flow.send(command, weapon_flow.encode_change_response(result, current if result else target), 'weapon-change-response')
    if result or target == current:
        return True
    battle.resetAction(flow)
    flow.session['weaponId'] = target
    battle.actionState(flow).update(index=0, weaponChange=True)
    flow.send(command, weapon_flow.encode_use_update(actor_id=wire.ACTOR_ID, main_weapon_id=wire.WEAPON_ID,
              fist_weapon_id=FIST_ID, equipped_weapon_id=target, now_ms=flow.now), 'weapon-use-update')
    battle.syncState(flow, CHANGE_STATE)
    flow.later(battle.ACTION_TIMER, CHANGE_MS)
    return True
