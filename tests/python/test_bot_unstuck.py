from copy import deepcopy
import json
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_life, bot_unstuck
from Business.scripts.navigation import NavMesh
from test_bot_movement import lastMove, step, walking
from test_bot_player import snapshot, tick
from test_bot_roster import multi
from test_bot_tactics import fight


def stall(flow, frames=25, *, jitter=False, rethink=False):
    for index in range(frames):
        step(flow, position=(.02 * (-1) ** index, 0, .2) if jitter else None)
        if rethink and index % 2 == 0:
            tick(flow)


@pytest.mark.parametrize('jitter', [False, True])
def test_unchanged_native_position_triggers_escape_despite_regular_replanning(walking, jitter):
    stall(walking, jitter=jitter, rethink=True)
    value = walking.session['bot']
    assert value['unstuck']['attempt'] == 1
    assert value['path'][-1] == value['unstuck']['goal']
    assert abs(value['path'][-1][1]) > .5
    assert value['position'] == ([.02, 0, .2] if jitter else [0, 0, .2])
    assert lastMove(walking).current_velocity == 3000
    assert abs(lastMove(walking).left_right) > 500


def test_escape_route_survives_thinking_then_resumes_chasing_on_arrival(walking):
    stall(walking)
    value = walking.session['bot']
    goal = list(value['unstuck']['goal'])
    for _ in range(5):
        step(walking)
        tick(walking)
        assert value['path'][-1] == goal
    step(walking, position=(*goal[:2], goal[2] + .2))
    assert 'unstuck' not in value
    tick(walking)
    assert value['path'][-1] == [40., 0., 0.]
    assert value['position'] == [*goal[:2], goal[2] + .2]


def test_continuous_slow_progress_does_not_trigger_escape(walking):
    for index in range(100):
        step(walking, position=(.025 * index, 0, .2))
    assert 'unstuck' not in walking.session['bot']


def test_repeated_failed_escape_tries_another_direction_without_spamming(walking):
    stall(walking)
    value = walking.session['bot']
    previous = deepcopy(value['unstuck'])
    stall(walking, frames=30)
    assert value['unstuck']['attempt'] == 2
    assert value['unstuck']['goal'] != previous['goal']
    assert value['unstuck']['goal'][1] * previous['goal'][1] < 0
    assert len([line for line in walking.result['logs'] if line["message"].startswith('bot-stuck ')]) == 2


def test_progress_restarts_the_observation_window(walking):
    stall(walking, frames=24)
    assert 'unstuck' not in walking.session['bot']
    step(walking, position=(.3, 0, .2))
    stall(walking, frames=23)
    assert 'unstuck' not in walking.session['bot']
    step(walking)
    assert 'unstuck' in walking.session['bot']


def test_duplicate_samples_do_not_count_as_new_evidence(walking):
    stall(walking, frames=20)
    for _ in range(5):
        walking.now += 50
        tick(walking, bot.MOVE_TIMER)
        assert 'unstuck' not in walking.session['bot']
    walking.now += 50
    tick(walking, bot.MOVE_TIMER)
    assert walking.session['bot']['status'] == 'world-unavailable'
    assert 'motionProbe' not in walking.session['bot']


def test_timer_gap_restarts_observation_instead_of_immediately_recovering(walking):
    stall(walking, frames=24)
    step(walking, 5000)
    assert 'unstuck' not in walking.session['bot']
    assert walking.session['bot']['motionProbe']['since'] == walking.now


def test_turning_and_arrival_clear_the_probe(walking):
    stall(walking, frames=24)
    value = walking.session['bot']
    value['heading'] = 180
    for _ in range(7):
        step(walking)
        assert 'motionProbe' not in value and 'unstuck' not in value
    step(walking)
    assert 'motionProbe' in value and 'unstuck' not in value
    value.update(path=[[0, 0, 0]], step=0)
    step(walking)
    assert 'motionProbe' not in value and not value['moving']


def test_corner_escape_stays_inside_navigation_clearance(walking, monkeypatch):
    nav = NavMesh.fromPolygons([[(0, 0, 0), (8, 0, 0), (8, 8, 0), (0, 8, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    value = walking.session['bot']
    value.update(position=[7.6, 7.6, .2], heading=-45, path=[[7.9, 7.9, 0]], step=0)
    stall(walking)
    goal = value['unstuck']['goal']
    assert goal[0] < 7.6 and goal[1] < 7.6
    assert .35 < goal[0] < 7.65 and .35 < goal[1] < 7.65
    assert nav.findPath(value['position'], goal).status == 'ready'
    assert value['position'] == [7.6, 7.6, .2]


@pytest.mark.parametrize('degrees', [0, 45, -45, 22.5, -22.5])
def test_escape_checks_openings_in_front_of_the_blocked_heading(walking, degrees):
    angle = math.radians(degrees)
    polygon = [(x * math.cos(angle) - y * math.sin(angle),
                x * math.sin(angle) + y * math.cos(angle), 0)
               for x, y in ((-.4, -.4), (3, -.4), (3, .4), (-.4, .4))]
    nav = NavMesh.fromPolygons([polygon])
    recovery = bot_unstuck.plan(walking, nav, 2, heading=0)
    assert recovery.goal is not None
    assert recovery.route.status == 'ready'
    assert math.dist(recovery.goal[:2], (0, 0)) == pytest.approx(1.2)
    assert math.atan2(recovery.goal[1], recovery.goal[0]) == pytest.approx(angle)


def test_tight_space_uses_a_short_step_when_every_long_escape_is_blocked(walking):
    nav = NavMesh.fromPolygons([[(-.45, -.45, 0), (.95, -.45, 0), (.95, .45, 0), (-.45, .45, 0)]])
    recovery = bot_unstuck.plan(walking, nav, 2, heading=0)
    assert recovery.goal == pytest.approx((.5, 0, 0))
    assert recovery.route.status == 'ready'
    assert walking.session['bot']['position'] == [0, 0, .2]
    assert app.validateState(walking.state)


def test_waiting_recovery_can_resume_with_a_short_step_after_reload(walking, monkeypatch):
    nav = NavMesh.fromPolygons([[(-.45, -.45, 0), (.95, -.45, 0), (.95, .45, 0), (-.45, .45, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    value = walking.session['bot']
    value['unstuck'] = dict(attempt=7, heading=0, turn=4, goal=None, until=walking.now + 1000)
    assert app.validateState(json.loads(json.dumps(walking.state)))
    assert bot.recoverMotion(walking, 2) == 'unstuck-wait'
    assert not value['path'] and not value['moving']
    walking.now += 1000
    snapshot(walking)
    assert bot.recoverMotion(walking, 2) == 'unstuck'
    assert value['unstuck']['attempt'] == 8
    assert value['path'][-1] == pytest.approx((.5, 0, 0))
    step(walking)
    assert value['moving'] and lastMove(walking).current_velocity > 0
    assert value['position'] == [0, 0, .2]
    step(walking, position=(.5, 0, .2))
    assert 'unstuck' not in value


def test_short_forward_escape_does_not_cross_a_body(multi):
    fight(multi, {1: ((.8, 0, .2), 180), 2: ((0, 0, .2), 0)})
    nav = NavMesh.fromPolygons([[(-.4, -.4, 0), (3, -.4, 0), (3, .4, 0), (-.4, .4, 0)]])
    recovery = bot_unstuck.plan(multi, nav, 2, heading=0)
    assert recovery.goal is None
    assert actors.data(multi, 2)['position'] == [0, 0, .2]


def test_short_escape_does_not_select_a_disconnected_patch(walking):
    nav = NavMesh.fromPolygons([
        [(-.4, -.4, 0), (.4, -.4, 0), (.4, .4, 0), (-.4, .4, 0)],
        [(.8, -.4, 0), (3, -.4, 0), (3, .4, 0), (.8, .4, 0)],
    ])
    recovery = bot_unstuck.plan(walking, nav, 2, heading=0)
    assert recovery.goal is None
    assert not walking.session['bot']['path']


def test_body_blocked_intent_is_detected_even_when_move_packets_are_stops(multi):
    fight(multi, {1: ((10, 0, .2), 180), 2: ((0, 0, .2), 0),
                  3: ((.7, 0, .2), 180), 4: ((0, .7, .2), 0), 5: ((0, -.7, .2), 0)})
    value = actors.data(multi, 2)
    value.update(path=[[10, 0, 0]], step=0, lastMoveAt=multi.now)
    for index in range(25):
        multi.now += 50
        multi.context['botWorld']['sampledAt'] = multi.now
        bot.move(multi, 2)
        if index < 24:
            assert not value['moving']
    assert value['unstuck']['goal'][0] < -1
    assert abs(value['unstuck']['goal'][1]) < .01
    assert value['moving'] and value['position'] == [0, 0, .2]


def test_no_safe_escape_waits_then_retries_at_a_bounded_rate(walking, monkeypatch):
    openNav = bot.navigationFor(walking)
    narrow = NavMesh.fromPolygons([[(-.5, -.5, 0), (.5, -.5, 0), (.5, .5, 0), (-.5, .5, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: narrow)
    stall(walking)
    value = walking.session['bot']
    assert value['unstuck']['goal'] is None and not value['moving']
    assert bot.MOVE_TIMER not in walking.session['pending']
    for _ in range(9):
        walking.now += 100
        snapshot(walking, (.4, 0, .2))
        tick(walking)
        assert value['unstuck']['attempt'] == 1
        assert not value['path']
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: openNav)
    walking.now += 100
    snapshot(walking, (.4, 0, .2))
    tick(walking)
    assert value['unstuck']['attempt'] == 2 and value['path']
    assert bot.MOVE_TIMER in walking.session['pending']


def test_escape_preserves_active_weapon_phase_and_deadline(walking):
    stall(walking, frames=20)
    battle.startAction(walking, 300550, actorId=2)
    value = walking.session['bot']
    action = deepcopy(value['action'])
    deadline = walking.session['pending'][bot.ACTION_TIMER]
    stall(walking, frames=5)
    tick(walking)
    assert 'unstuck' in value and value['moving']
    assert value['action'] == action
    assert walking.session['pending'][bot.ACTION_TIMER] == deadline


def test_escape_does_not_prevent_starting_an_attack(walking):
    stall(walking)
    value = walking.session['bot']
    value['reactAt'] = walking.now
    snapshot(walking, (1.8, 0, .2))
    tick(walking)
    assert value['action']['index'] == 0
    assert 'unstuck' in value and value['path'][-1] == value['unstuck']['goal']


def test_escape_movement_does_not_churn_combat_wait_status(walking):
    stall(walking)
    walking.session['bot']['reactAt'] = walking.now + 10000
    for _ in range(5):
        snapshot(walking, (1.8, 0, .2))
        tick(walking)
        logs = list(walking.result['logs'])
        walking.now += 50
        snapshot(walking, (1.8, 0, .2))
        tick(walking, bot.MOVE_TIMER)
        assert walking.result['logs'] == logs


@pytest.mark.parametrize('reason', ['stop', 'stale', 'epoch', 'death', 'perception'])
def test_invalid_world_and_lifecycle_clear_recovery(walking, reason):
    stall(walking)
    value = walking.session['bot']
    if reason == 'stop':
        bot.stop(walking)
    elif reason == 'death':
        bot_life.die(walking, 2, 1, value['position'])
    elif reason == 'perception':
        bot_life.clearPerception(value)
    else:
        snapshot(walking, (40, 0, .2), age=1000 if reason == 'stale' else 0,
                 epoch=200 if reason == 'epoch' else 100)
        tick(walking, bot.MOVE_TIMER)
    assert 'unstuck' not in value and 'motionProbe' not in value


def test_probe_and_recovery_survive_json_roundtrip_and_hot_reload(walking):
    for frames in (20, 5):
        stall(walking, frames=frames)
        state = json.loads(json.dumps(walking.state))
        assert app.validateState(state)
        assert app.migrateState(app.STATE_VERSION, state) == state
    assert 'unstuck' in walking.session['bot']
    old = deepcopy(walking.state)
    old['sessions']['1']['bot'].pop('unstuck')
    assert app.validateState(old)


@pytest.mark.parametrize('key,field,invalid', [
    ('motionProbe', 'since', -1), ('motionProbe', 'sampledAt', '1000'),
    ('motionProbe', 'attemptedAt', True), ('motionProbe', 'position', [math.nan, 0, 0]),
    ('unstuck', 'attempt', 0), ('unstuck', 'attempt', True), ('unstuck', 'turn', len(bot_unstuck.TURNS)),
    ('unstuck', 'until', -1), ('unstuck', 'heading', math.inf), ('unstuck', 'goal', [1, 2]),
])
def test_invalid_probe_or_recovery_is_rejected(walking, key, field, invalid):
    stall(walking, frames=2 if key == 'motionProbe' else 25)
    value = walking.session['bot']
    value[key][field] = invalid
    assert not bot_unstuck.validState(value)
    assert not app.validateState(walking.state)
