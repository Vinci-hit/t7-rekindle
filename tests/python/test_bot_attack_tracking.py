from copy import deepcopy

import pytest

from Business.scripts import actors, app, battle, bot
from Business.scripts.codec import move_flow
from test_bot_roster import multi, sample
from test_combat_latency import frame


def stationaryAttack(flow, actorId=2, intent=300550, phase=0):
    targetId = 1 if actorId < 12 else 2
    value = actors.data(flow, actorId)
    value.update(position=[0., 0., .2], initialized=True, heightOffset=.2, heading=0,
                 moving=False, path=[], step=0, opponentId=targetId)
    sample(flow, {actorId: ((0, 0, .2), 0), targetId: ((0, 1.2, .2), 0)})
    battle.startAction(flow, intent, released=True, actorId=actorId)
    while value['action']['index'] < phase:
        flow.now = flow.session['pending'][battle.actionTimer(actorId)]
        flow.context['botWorld']['sampledAt'] = flow.now
        app.handleTimer(flow, battle.actionTimer(actorId))
    value['lastTurnAt'] = flow.now
    flow.result['send'].clear()
    return value, targetId


@pytest.mark.parametrize('actorId', [2, 12, 20])
@pytest.mark.parametrize('intent', battle.ATTACK_CHAINS)
@pytest.mark.parametrize('phase', [0, 1, 2])
def test_stationary_attack_tracks_new_pose_before_ai_without_restarting_animation(multi, actorId, intent, phase, monkeypatch):
    value, _ = stationaryAttack(multi, actorId, intent, phase)
    action = deepcopy(value['action'])
    deadline = multi.session['pending'][battle.actionTimer(actorId)]
    multi.later(actors.PULSE_TIMER, 50)
    monkeypatch.setattr(bot, 'think', lambda *args: pytest.fail('tracking must not run AI decisions'))
    result = frame(multi, 20)
    turns = [packet['body'] for packet in result['send'] if packet['reason'] == 'bot-face-target']
    assert turns == [move_flow.encode_move_in_air_direct_bc(
        server_tick=multi.now - multi.session['instanceStartedAt'], target_instance_id=actorId,
        direction_yaw=-7, left_right=0, forward_back=0)]
    value = actors.data(multi, actorId)
    assert value['heading'] == -7 and not value['moving'] and not value['path']
    assert value['action'] == action
    assert multi.session['pending'][battle.actionTimer(actorId)] == deadline
    assert actors.timerName(bot.MOVE_TIMER, actorId) not in multi.session['pending']
    assert app.validateState(multi.state)


@pytest.mark.parametrize('fault', ['idle', 'recovery', 'guard', 'hurt', 'reaction', 'dead',
                                  'source-missing', 'target-missing', 'target-dead', 'target-lost',
                                  'target-distant', 'target-height', 'stale', 'epoch'])
def test_stationary_tracking_respects_action_and_fresh_target_lifetime(multi, fault):
    value, targetId = stationaryAttack(multi)
    world = multi.context['botWorld']
    if fault == 'idle':
        battle.resetAction(multi, 2)
    elif fault == 'recovery':
        value['action']['index'] = 3
    elif fault == 'guard':
        battle.startAction(multi, 300610, actorId=2)
    elif fault == 'hurt':
        value['hurtUntil'] = multi.now + 400
    elif fault == 'reaction':
        value['action']['reaction'] = 'parried'
    elif fault == 'dead':
        value['life'] = dict(state='dead')
    elif fault in ('source-missing', 'target-missing'):
        world['actors'][str(2 if fault == 'source-missing' else targetId)]['ready'] = False
    elif fault == 'target-dead':
        actors.data(multi, targetId)['life'] = dict(state='dead')
    elif fault == 'target-lost':
        value.pop('opponentId')
    elif fault == 'target-distant':
        world['actors'][str(targetId)]['position'][1] = 8
    elif fault == 'target-height':
        world['actors'][str(targetId)]['position'][2] = 4
    elif fault == 'stale':
        world['sampledAt'] -= 1000
    elif fault == 'epoch':
        world['instanceStartedAt'] += 1
    multi.result['send'].clear()
    multi.now += 20
    before = deepcopy(value)
    bot.worldUpdated(multi)
    assert value == before and not multi.result['send']


def test_stationary_attack_aims_from_observed_position_not_old_route_position(multi):
    value, targetId = stationaryAttack(multi)
    sample(multi, {2: ((1, 1.2, .2), 0), targetId: ((1, 2.4, .2), 0)})
    result = frame(multi, 20)
    value = actors.data(multi, 2)
    assert value['position'] == [1, 1.2, .2]
    assert value['heading'] == -7
    assert any(packet['reason'] == 'bot-face-target' for packet in result['send'])


def test_route_arrival_hands_attack_turning_to_the_next_frame(multi):
    value, targetId = stationaryAttack(multi)
    value.update(path=[[.1, 0., 0.]], step=0, moving=True,
                 footwork=dict(targetId=targetId, mode='press'))
    multi.later(bot.MOVE_TIMER, bot.MOVE_MS)
    action = deepcopy(value['action'])
    result = frame(multi, 20)
    assert [packet['reason'] for packet in result['send']] == ['bot-stop']
    assert bot.MOVE_TIMER not in multi.session['pending']
    result = frame(multi, 20)
    assert any(packet['reason'] == 'bot-face-target' for packet in result['send'])
    assert actors.data(multi, 2)['heading'] < 0
    assert actors.data(multi, 2)['action'] == action


def test_moving_attack_does_not_turn_twice_in_one_frame(multi, monkeypatch):
    value, targetId = stationaryAttack(multi)
    value.update(path=[[10., 0., 0.]], step=0, moving=True,
                 footwork=dict(targetId=targetId, mode='press'))
    multi.later(bot.MOVE_TIMER, bot.MOVE_MS)
    monkeypatch.setattr(bot, 'trackStationaryAttack', lambda flow, rid: (
        pytest.fail('moving attack already tracks its target') if rid == 2 else None))
    frame(multi, 20)
    assert actors.data(multi, 2)['heading'] == -7
