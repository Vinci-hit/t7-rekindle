"""Use the client's KEY_LEFT_MOUSE / KEY_RIGHT_MOUSE bit positions."""
from copy import deepcopy
import struct

import pytest

from Business.scripts import app, battle
from test_battle_actions import ATTACKS, BLOCKS, advanceAction, makeFlow


LEFT_MOUSE = 1 << 7
RIGHT_MOUSE = 1 << 8


def send(flow, intent, keys):
    app.handleMessage(flow, {'command': 4, 'body': struct.pack('>HIib', 1, keys, intent, 1)})


@pytest.mark.parametrize('guard', [intent for intent, _ in BLOCKS])
@pytest.mark.parametrize('attack', [intent for intent, _ in ATTACKS])
@pytest.mark.parametrize('phase', [0, 1])
def test_right_button_keeps_guard_and_release_starts_full_attack_prepare(guard, attack, phase):
    flow = makeFlow()
    send(flow, guard, LEFT_MOUSE | RIGHT_MOUSE)
    if phase == 1:
        advanceAction(flow)
    action = deepcopy(flow.session['action'])
    pending = deepcopy(flow.session['pending'])
    send(flow, attack, LEFT_MOUSE | RIGHT_MOUSE)
    assert flow.session['action'] == action
    assert flow.session['pending'] == pending

    # The native right-button release selects an attack, not necessarily BLOCK_RELEASE.
    flow.now += 10
    send(flow, attack, LEFT_MOUSE)
    assert flow.session['action']['intent'] == attack
    assert flow.session['action']['index'] == 0
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + battle.phaseMs(attack, 0)
    advanceAction(flow)
    assert flow.session['action']['index'] == 1
    send(flow, battle.ATTACK_RELEASE, 0)
    assert flow.session['action']['index'] == 2


@pytest.mark.parametrize('locked', ['strike', 'hurt', 'parried', 'terrain-rebound'])
def test_right_button_preserves_buffered_defence_during_locked_phase(locked):
    flow = makeFlow()
    send(flow, 300560, LEFT_MOUSE)
    send(flow, battle.ATTACK_RELEASE, 0)
    advanceAction(flow)
    if locked == 'hurt':
        flow.session['hurtUntil'] = flow.now + 400
    elif locked != 'strike':
        flow.session['action']['reaction'] = locked
    send(flow, 300580, RIGHT_MOUSE)
    action = deepcopy(flow.session['action'])
    send(flow, 300550, LEFT_MOUSE | RIGHT_MOUSE)
    assert flow.session['action'] == action
    send(flow, battle.ATTACK_RELEASE, RIGHT_MOUSE)
    assert flow.session['action']['buffered'] == {'intent': 300580, 'released': False}
    send(flow, battle.BLOCK_RELEASE, 0)
    assert flow.session['action']['buffered']['released']


def test_conflicting_attack_at_idle_does_not_invent_a_defence_direction():
    flow = makeFlow()
    before = deepcopy(flow.state)
    send(flow, 300560, LEFT_MOUSE | RIGHT_MOUSE)
    assert flow.state == before
    assert not flow.result['send']


@pytest.mark.parametrize('released', [False, True])
def test_native_left_held_handoff_waits_for_success_recovery_and_respects_left_release(released):
    flow = makeFlow()
    flow.context['botWorld'] = {'instanceStartedAt': flow.session['instanceStartedAt']}
    send(flow, 300580, RIGHT_MOUSE)
    advanceAction(flow)
    battle.parryReaction(flow, 1, 2, success=True, minimumMs=250)
    deadline = flow.session['pending'][battle.ACTION_TIMER]
    send(flow, 300560, LEFT_MOUSE | RIGHT_MOUSE)
    assert 'buffered' not in flow.session['action']
    send(flow, 300560, LEFT_MOUSE)
    assert flow.session['action']['reaction'] == 'parry-success'
    assert flow.session['pending'][battle.ACTION_TIMER] == deadline
    if released:
        send(flow, battle.ATTACK_RELEASE, 0)
    advanceAction(flow)
    assert flow.session['action']['index'] == (-1 if released else 0)
    assert flow.session['action']['intent'] == (0 if released else 300560)


@pytest.mark.parametrize('guard,chain', BLOCKS)
@pytest.mark.parametrize('released', [False, True])
def test_guard_repress_during_success_waits_for_recovery_and_respects_release(guard, chain, released):
    flow = makeFlow()
    flow.context['botWorld'] = {'instanceStartedAt': flow.session['instanceStartedAt']}
    send(flow, 300580, RIGHT_MOUSE)
    advanceAction(flow)
    battle.parryReaction(flow, 1, 2, success=True, minimumMs=250)
    deadline = flow.session['pending'][battle.ACTION_TIMER]
    previous = deepcopy(flow.result['send'])
    flow.now += 50
    send(flow, battle.BLOCK_RELEASE, 0)
    send(flow, guard, RIGHT_MOUSE)
    assert flow.session['action']['buffered'] == {'intent': guard, 'released': False}
    assert flow.result['logs'][-1]['message'].endswith('result=buffered reason=parry-success')
    send(flow, 300560, LEFT_MOUSE | RIGHT_MOUSE)
    send(flow, battle.ATTACK_RELEASE, RIGHT_MOUSE)
    if released:
        send(flow, battle.BLOCK_RELEASE, 0)
    assert flow.session['action']['buffered'] == {'intent': guard, 'released': released}
    assert flow.session['pending'][battle.ACTION_TIMER] == deadline
    flow.now = deadline - 1
    app.handleTimer(flow, battle.ACTION_TIMER)
    assert flow.session['action']['reaction'] == 'parry-success'
    assert flow.result['send'] == previous
    advanceAction(flow)
    action = flow.session['action']
    assert 'reaction' not in action and 'buffered' not in action
    if released:
        assert action['index'] == -1 and action['intent'] == 0
        assert battle.ACTION_TIMER not in flow.session['pending']
    else:
        assert action['intent'] == guard and action['state'] == chain[0]
        advanceAction(flow)
        assert action['index'] == 1 and action['state'] == chain[1]
        assert not action['released']


@pytest.mark.parametrize('guard,_', BLOCKS)
def test_success_feedback_keeps_guard_direction_locked_until_release(guard, _):
    flow = makeFlow()
    flow.context['botWorld'] = {'instanceStartedAt': flow.session['instanceStartedAt']}
    send(flow, 300580, RIGHT_MOUSE)
    advanceAction(flow)
    battle.parryReaction(flow, 1, 2, success=True, minimumMs=250)
    action = deepcopy(flow.session['action'])
    pending = deepcopy(flow.session['pending'])
    flow.now += 50
    send(flow, guard, RIGHT_MOUSE)
    assert flow.session['action'] == action
    assert flow.session['pending'] == pending
    advanceAction(flow)
    assert flow.session['action']['intent'] == 300580
    assert flow.session['action']['index'] == 1
