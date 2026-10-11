from copy import deepcopy

import pytest

from Business.scripts import actors, app, battle, bot, combat
from Business.scripts.codec import move_flow
from blade_fixture import contact, publish
from test_bot_roster import multi, sample
from test_combat_latency import frame
from test_melee_combat import states, swing


def walking(flow, actorId=2):
    value = actors.data(flow, actorId)
    value.update(position=[0., 0., .2], initialized=True, heightOffset=.2, heading=0,
                 path=[[10., 0., 0.]], step=0, lastMoveAt=flow.now, moving=True)
    flow.later(actors.timerName(bot.MOVE_TIMER, actorId), bot.MOVE_MS)
    return value


@pytest.mark.parametrize('actorId', [2, 12, 20])
def test_motion_follows_new_native_pose_before_the_ai_pulse(multi, actorId, monkeypatch):
    walking(multi, actorId)
    multi.later(actors.PULSE_TIMER, bot.MOVE_MS)
    pulseDue = multi.session['pending'][actors.PULSE_TIMER]
    monkeypatch.setattr(bot, 'think', lambda *args: pytest.fail('motion must not run AI decisions'))
    for x in (.04, .08, .12):
        sample(multi, {actorId: ((x, 0., .2), 0)})
        result = frame(multi, 10)
        moves = [move_flow.decode_move_bc_with_system_and_active(packet['body'])
                 for packet in result['send'] if packet['reason'] == 'bot-move']
        assert len(moves) == 1 and moves[0].target_instance_id == actorId
        assert moves[0].position == pytest.approx((x, 0., .2))
        assert actors.data(multi, actorId)['position'] == [x, 0., .2]
        assert multi.now < pulseDue == multi.session['pending'][actors.PULSE_TIMER]
        assert not result['timers']
    assert app.validateState(multi.state)


def test_ai_pulse_does_not_also_tick_in_flight_motion(multi, monkeypatch):
    walking(multi)
    sample(multi)
    multi.now += bot.MOVE_MS
    monkeypatch.setattr(bot, 'move', lambda *args: pytest.fail('motion belongs to the native frame event'))
    bot.timer(multi, actors.PULSE_TIMER)
    assert actors.timerName(bot.MOVE_TIMER, 2) in multi.session['pending']


def test_route_arrival_stops_on_the_next_pose_without_an_ai_tick(multi):
    walking(multi)
    sample(multi, {2: ((10., 0., .2), 0)})
    result = frame(multi)
    assert [packet['reason'] for packet in result['send']] == ['bot-stop']
    assert not actors.data(multi, 2)['moving']
    assert bot.MOVE_TIMER not in multi.session['pending']
    assert frame(multi)['send'] == []


def test_motion_rejects_a_missing_pose_without_extrapolating(multi):
    walking(multi)
    sample(multi)['actors']['2']['ready'] = False
    previous = deepcopy(actors.data(multi, 2)['position'])
    result = frame(multi)
    assert [packet['reason'] for packet in result['send']] == ['bot-stop']
    assert actors.data(multi, 2)['position'] == previous
    assert not actors.data(multi, 2)['moving']
    assert bot.MOVE_TIMER not in multi.session['pending']


def test_contact_changes_motion_speed_in_the_same_world_event(multi):
    value = walking(multi)
    value['forwardMs'] = bot.RUN_DELAY_MS
    sample(multi)
    swing(multi)
    sample(multi, {1: ((1.5, 0., .2), 180), 2: ((0., 0., .2), 0)})
    publish(multi, 1, [contact(2)])
    result = frame(multi)
    assert actors.data(multi, 2)['health'] == 80
    move = next(packet for packet in result['send'] if packet['reason'] == 'bot-move')
    assert move_flow.decode_move_bc_with_system_and_active(move['body']).current_velocity == 3000
    reasons = [packet['reason'] for packet in result['send']]
    assert reasons.index('bot-model-state-35') < reasons.index('bot-move')


@pytest.mark.parametrize('actorId', [1, 2, 12, 20])
def test_hurt_recovery_has_its_own_deadline(multi, actorId):
    sample(multi)
    actors.data(multi, actorId)['hurtUntil'] = multi.now + combat.HURT_MS
    battle.syncState(multi, combat.HURT_STATE, actorId)
    name = combat.hurtTimer(actorId)
    multi.later(name, combat.HURT_MS)
    assert multi.result['timers'][-1]['event']['name'] == name
    assert multi.result['timers'][-1]['delayMs'] == combat.HURT_MS
    multi.now += combat.HURT_MS
    sample(multi)
    app.handleTimer(multi, name)
    assert states(multi) == [(actorId, combat.HURT_STATE), (actorId, 2)]
    assert 'hurtUntil' not in actors.data(multi, actorId)


@pytest.mark.parametrize('actorId', [2, 12, 20])
def test_planned_guard_release_is_not_rounded_to_an_ai_pulse(multi, actorId):
    sample(multi)
    battle.startAction(multi, 300610, actorId=actorId)
    app.handleTimer(multi, battle.actionTimer(actorId))
    name = actors.timerName(bot.RELEASE_TIMER, actorId)
    multi.later(name, 437)
    assert multi.result['timers'][-1]['event']['name'] == name
    assert multi.result['timers'][-1]['delayMs'] == 437
    multi.now += 437
    sample(multi)
    app.handleTimer(multi, name)
    assert states(multi)[-1] == (actorId, 230)
    assert multi.result['timers'][-1]['event']['name'] == battle.actionTimer(actorId)
    assert multi.result['timers'][-1]['delayMs'] == 0
