from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, body_collision as bodies, bot
from Business.scripts.codec import move_flow
from test_bot_roster import multi, sample


def test_sweep_stops_before_a_body_instead_of_tunneling():
    result = bodies.clip([0, 0, 0], [4, 0, 0], [[2, 0, 0]])
    assert result[0] == pytest.approx(2 - bodies.DIAMETER, abs=.002)
    assert result[1:] == [0, 0]


def test_sweep_slides_and_respects_a_second_body():
    obstacles = [[1, 0, 0], [1, 1.4, 0]]
    result = bodies.clip([0, 0, 0], [2, .6, 0], obstacles)
    assert result[1] > 0
    assert all(math.dist(result[:2], point[:2]) >= bodies.DIAMETER - .002 for point in obstacles)


def test_vertical_separation_and_jump_over_body():
    assert bodies.clip([0, 0, 3], [4, 0, 3], [[2, 0, 0]]) == [4, 0, 3]
    result = bodies.clip([0, 0, 3], [0, 0, 0], [[0, 0, 0]])
    assert result[2] == pytest.approx(bodies.HEIGHT, abs=.002)


def test_initial_overlap_can_move_out_but_not_deeper():
    assert bodies.clip([0, 0, 0], [-.2, 0, 0], [[.5, 0, 0]]) == [-.2, 0, 0]
    assert bodies.clip([0, 0, 0], [.2, 0, 0], [[.5, 0, 0]])[0] <= .001
    assert bodies.clip([0, 0, 0], [.2, 0, .01], [[.5, 0, 0]])[0] <= .001


def test_native_player_contact_preserves_input_and_action_without_correction(multi):
    world = sample(multi, {1: ((0, 0, .2), 0), 2: ((2, 0, .2), 180)})
    actors.data(multi, 2).update(position=[2, 0, .2], initialized=True, heightOffset=.2)
    multi.session['ground'].update(mask=1, heading=0)
    multi.session['action'] = {'intent': 300550, 'index': 0, 'seq': 1}
    bot.timer(multi, actors.PULSE_TIMER)
    before = deepcopy(multi.session['action'])
    multi.now += 50
    world['sampledAt'] = multi.now
    world['actors']['1']['position'] = [4, 0, .2]
    bot.timer(multi, actors.PULSE_TIMER)
    assert not multi.result['send']
    assert multi.session['ground']['mask'] == 1
    assert multi.session['action'] == before


def test_missing_or_stale_snapshot_never_corrects_player(multi):
    world = sample(multi)
    world['sampledAt'] -= 1000
    before = deepcopy(multi.session['ground'])
    bot.timer(multi, actors.PULSE_TIMER)
    assert multi.session['ground'] == before
    assert not multi.result['send']


def test_unobstructed_player_movement_has_no_position_echo(multi):
    world = sample(multi, {1: ((-10, -10, .2), 0)})
    bot.timer(multi, actors.PULSE_TIMER)
    multi.now += 50
    world['sampledAt'] = multi.now
    world['actors']['1']['position'] = [-9.8, -10, .2]
    bot.timer(multi, actors.PULSE_TIMER)
    assert not any(item['reason'] == 'body-player-correction' for item in multi.result['send'])
    assert 'bodyPlayer' not in multi.session


@pytest.mark.parametrize('neighbor', [1, 3, 12])
def test_bot_avoids_player_friendly_and_enemy_bodies(multi, neighbor):
    world = sample(multi, {2: ((0, 0, .2), 0), neighbor: ((1, 0, .2), 180)})
    value = actors.data(multi, 2)
    value.update(position=[0, 0, .2], initialized=True, heightOffset=.2)
    if neighbor != 1:
        actors.data(multi, neighbor).update(position=[1, 0, .2], initialized=True, heightOffset=.2)
    result = bodies.moveBot(multi, bot.navigationFor(multi), 2, [0, 0, .2], [1.5, 0, .2])
    assert math.dist(result[:2], [1, 0]) >= bodies.DIAMETER - .002
    assert bodies.clip([0, 0, .2], result, [[1, 0, .2]]) == pytest.approx(result)


def test_steering_uses_observed_bodies_not_old_planned_positions(multi):
    world = sample(multi, {2: ((0, 0, .2), 0), 3: ((2, 0, .2), 180)})
    for rid, x in [(2, 0), (3, 2)]:
        actors.data(multi, rid).update(position=[x, 0, .2], initialized=True, heightOffset=.2)
    actors.data(multi, 2)['position'] = [1, 0, .2]
    neighbors = bodies.obstacles(multi, 3, world)
    assert [0, 0, .2] in neighbors
    assert [1, 0, .2] not in neighbors


def test_avoidance_never_steps_outside_navigation(multi, monkeypatch):
    from Business.scripts.navigation import NavMesh
    nav = NavMesh.fromPolygons([[(-2, -.3, 0), (2, -.3, 0), (2, .3, 0), (-2, .3, 0)]])
    sample(multi, {2: ((0, 0, .2), 0), 1: ((1, 0, .2), 180)})
    actors.data(multi, 2).update(position=[0, 0, .2], initialized=True, heightOffset=.2)
    result = bodies.moveBot(multi, nav, 2, [0, 0, .2], [1.5, 0, .2])
    assert nav.project(result) is not None
    assert result[0] <= 1 - bodies.DIAMETER + .01


def test_overlapping_idle_bots_do_not_receive_business_separation(multi):
    sample(multi, {2: ((0, 0, .2), 0), 3: ((0, 0, .2), 0)})
    for rid in (2, 3):
        actors.data(multi, rid).update(position=[0, 0, .2], initialized=True, heightOffset=.2)
    before = deepcopy(actors.data(multi, 2).get('action'))
    bot.timer(multi, actors.PULSE_TIMER)
    assert not multi.result['send']
    assert actors.data(multi, 2)['position'] == actors.data(multi, 3)['position']
    assert actors.data(multi, 2).get('action') == before


def test_body_memory_validation_and_reload(multi):
    multi.session['bodyPlayer'] = dict(position=[0, 0, .2], sampledAt=multi.now)
    assert app.validateState(multi.state)
    assert app.migrateState(app.STATE_VERSION, multi.state) == multi.state
    multi.session['bodyPlayer']['position'][0] = float('nan')
    assert not app.validateState(multi.state)


@pytest.mark.parametrize('heading,state', [(90, 8), (-90, 4), (180, 6), (135, 7)])
def test_body_detour_uses_matching_side_or_back_walk_state(multi, heading, state):
    value = actors.data(multi, 2)
    value.update(heading=0, forwardMs=2000)
    bot.broadcast(multi, True, heading, actorId=2)
    packet = move_flow.decode_move_bc_with_system_and_active(multi.result['send'][-1]['body'])
    assert (packet.state, packet.current_velocity) == (state, 3000)


def test_side_step_clears_run_and_sends_jog_intent_without_displacement(multi, monkeypatch):
    sample(multi, {2: ((0, 0, .2), 0)})
    value = actors.data(multi, 2)
    value.update(position=[0, 0, .2], initialized=True, heightOffset=.2, heading=0,
                 forwardMs=2000, lastMoveAt=multi.now - 50, path=[[10, 0, 0]], step=0)
    monkeypatch.setattr(bodies, 'moveBot', lambda *args: [0, .25, .2])
    bot.move(multi, 2)
    assert value['position'] == [0, 0, .2]
    assert value['forwardMs'] == 0
    packet = move_flow.decode_move_bc_with_system_and_active(multi.result['send'][-1]['body'])
    assert (packet.state, packet.current_velocity) == (4, 3000)
