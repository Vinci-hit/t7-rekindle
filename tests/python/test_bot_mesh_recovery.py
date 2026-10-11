from copy import deepcopy
import json
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_life, bot_unstuck
from Business.scripts.navigation import NavMesh
from test_bot_movement import lastMove
from test_bot_roaming import isolated
from test_bot_roster import multi


@pytest.fixture
def edge(multi, monkeypatch):
    nav = NavMesh.fromPolygons([[(-4, -4, 0), (2, -4, 0), (2, 4, 0), (-4, 4, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    value = actors.data(multi, 2)
    value.update(position=[2.02, 0., .2], heading=180, initialized=True, heightOffset=.2,
                 path=[[0., 0., 0.]], step=0, moving=True)
    isolated(multi, origin=value['position'], enemy=(0, 0, .2), heading=180)
    return multi


def observe(flow, position=None, *, elapsed=50, heading=None):
    value = actors.data(flow, 2)
    flow.now += elapsed
    return isolated(flow, origin=position or value['position'], enemy=(0, 0, .2),
                    heading=value['heading'] if heading is None else heading)


def test_reentry_selects_the_nearest_inset_point_not_the_face_center():
    nav = NavMesh.fromPolygons([[(-50, -20, 0), (2, -20, 0), (2, 20, 0), (-50, 20, 0)]])
    goals = bot_unstuck.meshGoals(nav, (2.02, 0, .2))
    assert goals[0] == pytest.approx((1.649, 0, 0))
    assert nav.project(goals[0]) is not None


def test_reentry_chooses_the_nearest_floor_instead_of_an_overlapping_floor():
    base = [(-4, -4, 0), (2, -4, 0), (2, 4, 0), (-4, 4, 0)]
    nav = NavMesh.fromPolygons([base, [(x, y, 3) for x, y, _ in base]])
    goals = bot_unstuck.meshGoals(nav, (2.02, 0, 3.2))
    assert goals and all(point[2] == 3 for point in goals)
    assert bot_unstuck.meshGoals(nav, (2.02, 0, 10)) == []


def test_reentry_searches_beyond_the_current_navigation_cell():
    nav = NavMesh.fromPolygons([[(12, -2, 0), (16, -2, 0), (16, 2, 0), (12, 2, 0)]])
    assert bot_unstuck.meshGoals(nav, (0, 0, .2))[0] == pytest.approx((12.351, 0, 0))


@pytest.mark.parametrize('entry', [bot.think, bot.move])
@pytest.mark.parametrize('initialized', [False, True])
def test_off_mesh_actor_keeps_native_movement_scheduled_without_snapping(edge, entry, initialized):
    value = actors.data(edge, 2)
    value['initialized'] = initialized
    if not initialized:
        value.pop('heightOffset')
    entry(edge, 2)
    assert value['status'] == 'mesh-return'
    assert value['meshRecovery']['goal'] == pytest.approx((1.649, 0, 0))
    assert value['position'] == [2.02, 0, .2]
    assert value['moving'] and lastMove(edge).current_velocity == 3000
    assert lastMove(edge).position == pytest.approx(value['position'])
    assert actors.timerName(bot.MOVE_TIMER, 2) in edge.session['pending']
    assert not value['path']


def test_near_edge_is_not_mistaken_for_a_completed_reentry(edge):
    bot.think(edge, 2)
    observe(edge, (1.99, 0, .2))
    bot.think(edge, 2)
    assert 'meshRecovery' in actors.data(edge, 2)
    assert actors.data(edge, 2)['moving']


def test_reentry_keeps_the_nearest_goal_until_the_body_clears_the_edge(edge):
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    goal = list(value['meshRecovery']['goal'])
    for x in (1.87, 1.72):
        observe(edge, (x, 0, .2))
        bot.move(edge, 2)
        assert value['meshRecovery']['goal'] == goal
        assert value['meshRecovery']['attempt'] == 1
        assert value['moving']
    observe(edge, (1.57, 0, .2))
    bot.move(edge, 2)
    assert 'meshRecovery' not in value


def test_native_reentry_clears_recovery_and_resumes_enemy_acquisition_and_attack(edge):
    value = actors.data(edge, 2)
    bot.think(edge, 2)
    for _ in range(5):
        observe(edge)
        bot.move(edge, 2)
        assert value['position'] == [2.02, 0, .2]
        assert 'meshRecovery' in value
    observe(edge, (1.55, 0, .2))
    bot.think(edge, 2)
    assert 'meshRecovery' not in value
    assert value['opponentId'] == 1
    observe(edge, elapsed=400)
    bot.think(edge, 2)
    assert value['action']['index'] >= 0
    assert value['attackCount'] == 1
    assert value['position'] == [1.55, 0, .2]


def test_failed_native_movement_retries_a_different_nearby_entry(edge):
    value = actors.data(edge, 2)
    bot.think(edge, 2)
    goal = list(value['meshRecovery']['goal'])
    for _ in range(25):
        observe(edge)
        bot.move(edge, 2)
    assert value['meshRecovery']['attempt'] == 2
    assert value['meshRecovery']['goal'] != goal
    assert value['position'] == [2.02, 0, .2]
    attempts = [line for line in edge.result['logs'] if line.startswith('bot-mesh-recovery ')]
    assert len(attempts) == 2
    assert all('position=' in line and 'goal=' in line for line in attempts)


def test_progress_toward_a_distant_mesh_does_not_rotate_the_entry(edge):
    observe(edge, (8, 0, .2))
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    goal = list(value['meshRecovery']['goal'])
    for index in range(50):
        observe(edge, (8 - .03 * (index + 1), 0, .2))
        bot.move(edge, 2)
    assert value['meshRecovery']['attempt'] == 1
    assert value['meshRecovery']['goal'] == goal


@pytest.mark.parametrize('obstacle', ['wall', 'unknown', 'not-facing'])
def test_reentry_waits_for_clear_terrain_and_observed_heading(edge, obstacle):
    world = edge.context['botWorld']
    pose = world['actors']['2']
    if obstacle == 'wall':
        pose['terrain']['forward']['hit'] = True
    elif obstacle == 'unknown':
        pose['terrain']['forward']['ready'] = False
    else:
        pose['heading'] = 0
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    assert not value['moving'] and value['status'] == 'mesh-return-wait'
    assert lastMove(edge).position == pytest.approx((2.02, 0, .2))
    assert lastMove(edge).current_velocity == 0
    assert actors.timerName(bot.MOVE_TIMER, 2) in edge.session['pending']
    observe(edge)
    bot.move(edge, 2)
    assert value['moving']


def test_a_forward_ray_hit_beyond_the_entry_does_not_prevent_a_clear_short_step(edge):
    edge.context['botWorld']['actors']['2']['terrain']['forward'].update(hit=True, position=[1., 0, 1.2])
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    assert value['moving']
    assert value['meshRecovery']['goal'] == pytest.approx((1.649, 0, 0))
    assert lastMove(edge).position == pytest.approx((2.02, 0, .2))


def test_occupied_entries_wait_then_retry_when_the_body_moves(edge):
    edge.context['botWorld']['actors']['3']['position'] = [1.4, 0, .2]
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    assert value['meshRecovery']['goal'] is None
    assert not value['moving']
    assert actors.timerName(bot.MOVE_TIMER, 2) not in edge.session['pending']
    observe(edge, elapsed=999)
    bot.think(edge, 2)
    assert value['meshRecovery']['attempt'] == 1
    observe(edge, elapsed=1)
    bot.think(edge, 2)
    assert value['meshRecovery']['attempt'] == 2
    assert value['moving']


def test_height_mismatch_uses_horizontal_steering_without_rewriting_height(edge):
    observe(edge, (0, 1, 1.2), heading=0)
    value = actors.data(edge, 2)
    value['heading'] = 0
    bot.think(edge, 2)
    assert value['meshRecovery']['goal'] is not None
    assert math.dist(value['position'][:2], value['meshRecovery']['goal'][:2]) > .25
    assert value['position'] == [0, 1, 1.2]
    assert lastMove(edge).position == pytest.approx((0, 1, 1.2))


def test_recovery_preserves_the_weapon_phase_and_deadline(edge):
    battle.startAction(edge, 300550, actorId=2)
    value = actors.data(edge, 2)
    action = deepcopy(value['action'])
    deadline = edge.session['pending'][actors.timerName(bot.ACTION_TIMER, 2)]
    bot.think(edge, 2)
    assert value['action'] == action
    assert edge.session['pending'][actors.timerName(bot.ACTION_TIMER, 2)] == deadline
    assert value['moving']


@pytest.mark.parametrize('reason', ['stop', 'stale', 'epoch', 'death', 'respawn'])
def test_lifecycle_clears_mesh_recovery(edge, reason):
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    if reason == 'stop':
        bot.stop(edge, 2)
    elif reason == 'death':
        bot_life.die(edge, 2, 1, value['position'])
    elif reason == 'respawn':
        bot_life.clearPerception(value)
    else:
        if reason == 'stale':
            edge.now += 1000
        else:
            edge.context['botWorld']['instanceStartedAt'] += 1
        bot.move(edge, 2)
    assert 'meshRecovery' not in value
    assert 'motionProbe' not in value


def test_mesh_recovery_survives_json_roundtrip_and_hot_reload(edge):
    bot.think(edge, 2)
    state = json.loads(json.dumps(edge.state))
    assert app.validateState(state)
    assert app.migrateState(app.STATE_VERSION, state) == state


@pytest.mark.parametrize('field,invalid', [('attempt', 0), ('attempt', True), ('retryAt', -1),
                                         ('goal', [1, 2]), ('goal', [math.nan, 0, 0])])
def test_invalid_mesh_recovery_is_rejected(edge, field, invalid):
    bot.think(edge, 2)
    value = actors.data(edge, 2)
    value['meshRecovery'][field] = invalid
    assert not bot_unstuck.validState(value)
    assert not app.validateState(edge.state)
