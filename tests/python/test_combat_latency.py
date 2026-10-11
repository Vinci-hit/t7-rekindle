from copy import deepcopy

import pytest

from Business.scripts import actors, app, battle, bot, combat
from blade_fixture import contact, publish
from test_bot_roster import multi, sample
from test_melee_combat import arena, hp, swing
from test_melee_multi_hit import attack


def frame(flow, elapsed=1):
    flow.now += elapsed
    flow.context.update(nowMs=flow.now, connections=[flow.connection])
    flow.context['botWorld']['sampledAt'] = flow.now
    result = app.handleEvent({'type': 'world', 'connection': 0}, flow.state, flow.context)
    flow.state = result['state']
    flow.session = flow.state['sessions'][str(flow.connection)]
    return result


@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
def test_strike_does_not_add_a_fixed_first_contact_delay(arena, intent):
    attack(arena, intent=intent)
    assert arena.session['pending'][combat.hitTimer(1)] == arena.now


@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1), (12, 2)])
def test_first_native_frame_hits_without_waiting_for_the_ai_pulse(multi, attacker, target):
    sample(multi)
    attack(multi, attacker)
    publish(multi, attacker, [contact(target)])
    before = deepcopy(multi.state)
    result = frame(multi)
    assert hp(multi, target) == 80
    assert before['sessions'][str(multi.connection)] != multi.session
    reasons = [packet['reason'] for packet in result['send']]
    assert reasons.count('combat-hit-point') == 1
    assert ('battle-model-state-35' if target == 1 else 'bot-model-state-35') in reasons
    assert all(packet['connection'] == multi.connection for packet in result['send'])


def test_new_contact_after_a_clear_frame_does_not_wait_for_retry_deadline(multi):
    sample(multi)
    attack(multi)
    publish(multi, 1, [])
    frame(multi)
    deadline = multi.session['pending'][combat.hitTimer(1)]
    publish(multi, 1, [contact(2)])
    frame(multi)
    assert multi.now < deadline
    assert hp(multi, 2) == 80
    frame(multi)
    assert hp(multi, 2) == 80
    publish(multi, 1, [contact(3)])
    frame(multi)
    assert hp(multi, 3) == 80


def test_ai_pulse_does_not_consume_contact_timers(multi):
    sample(multi)
    attack(multi)
    publish(multi, 1, [contact(2)])
    multi.now += 50
    multi.context['botWorld']['sampledAt'] = multi.now
    bot.timer(multi, actors.PULSE_TIMER)
    assert hp(multi, 2) == 100
    assert combat.hitTimer(1) in multi.session['pending']
    frame(multi)
    assert hp(multi, 2) == 80


@pytest.mark.parametrize('invalid', ['pre-strike', 'epoch', 'unavailable', 'expired', 'interrupted'])
def test_world_notification_keeps_contact_validity_guards(arena, invalid):
    swing(arena)
    if invalid == 'pre-strike':
        publish(arena, 1, [contact(time=0)], spanMs=20)
    elif invalid == 'epoch':
        arena.context['botWorld']['instanceStartedAt'] += 1
    elif invalid == 'unavailable':
        arena.context['botWorld']['ready'] = False
    elif invalid == 'expired':
        arena.now = arena.session['swing']['until']
    else:
        battle.resetAction(arena)
    result = frame(arena)
    assert hp(arena, 2) == 100
    assert not any(packet['reason'] == 'combat-hit-point' for packet in result['send'])


def test_world_notification_does_not_hit_while_charging(arena):
    battle.startAction(arena, 300560)
    frame(arena)
    assert hp(arena, 2) == 100


def test_world_notification_ignores_disconnected_or_non_instance_sessions(arena):
    swing(arena)
    arena.context.update(nowMs=arena.now + 1, connections=[])
    result = app.handleEvent({'type': 'world', 'connection': 0}, arena.state, arena.context)
    assert result['state'] == arena.state and not result['send']
    arena.session['role'] = 'logic'
    arena.context['connections'] = [arena.connection]
    result = app.handleEvent({'type': 'world', 'connection': 0}, arena.state, arena.context)
    assert result['state'] == arena.state and not result['send']
