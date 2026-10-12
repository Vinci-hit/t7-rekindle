from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, body_collision, bot, bot_life, bot_roaming, bot_tactics
from Business.scripts.codec import move_flow
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi, sample


def fight(flow, placements, *, target=1):
    positions = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(flow.session)}
    positions.update(placements)
    world = sample(flow, positions)
    for rid, (position, heading) in placements.items():
        if rid != 1:
            actors.data(flow, rid).update(position=list(position), heading=heading,
                                         initialized=True, heightOffset=.2, opponentId=target)
    return world


def plan(flow, rid=2, target=1):
    return bot_tactics.plan(flow, bot.navigationFor(flow), actors.pair(actors.world(flow), rid, target), rid)


@pytest.mark.parametrize('cached', [False, True])
def test_attack_formation_closes_inside_the_old_edge_of_reach(multi, cached):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.65, 0, .2), 0), 3: ((0, -3, .2), 90)})
    value = actors.data(multi, 2)
    if cached:
        value['tactic'] = dict(targetId=1, until=multi.now + 1000, goal=[-1.65, 0., 0.],
                               targetPosition=[0., 0., .2], mode='approach')
    bot.think(multi, 2)
    assert value['status'] == 'approach'
    assert math.dist(value['tactic']['goal'][:2], (0, 0)) == pytest.approx(1.2)
    assert value['path'] and value.get('action', {}).get('index', -1) == -1
    assert bot_tactics.ARRIVAL_RADIUS <= .25


def test_team_uses_distinct_attack_positions_and_a_rear_route(multi):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0),
                  3: ((-4, .1, .2), 0), 4: ((-4, -.1, .2), 0), 5: ((-5, 0, .2), 0)})
    actors.data(multi, 5)['profile'] = 'aggressive'
    plans = [plan(multi, rid) for rid in (2, 5, 3, 4)]
    assert all(choice is not None for choice in plans)
    assert all(math.dist(a.goal[:2], b.goal[:2]) >= bot_tactics.SPACING
               for i, a in enumerate(plans) for b in plans[i + 1:])
    rear = plans[1]
    assert rear.mode == 'rear' and rear.goal[0] > 0
    assert len(rear.route.points) > 2
    for start, end in zip(rear.route.points, rear.route.points[1:]):
        assert math.dist(body_collision.clip(start, end, [[0, 0, 0]]), end) < .01


def test_crowded_bots_reposition_even_inside_melee_range(multi):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-1.7, .1, .2), 0)})
    bot.think(multi, 3)
    value = actors.data(multi, 3)
    assert value['status'] in ('spread', 'flank', 'rear')
    assert value['path'] and value.get('action', {}).get('index', -1) == -1
    assert actors.timerName(bot.MOVE_TIMER, 3) in multi.session['pending']


@pytest.mark.parametrize('reason', ['deadline', 'changed-route'])
def test_repath_keeps_the_flanking_arc_instead_of_chasing_through_target(multi, reason):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 5: ((-5, 0, .2), 0)}
    fight(multi, placements)
    actors.data(multi, 5)['profile'] = 'aggressive'
    bot.think(multi, 5)
    value = actors.data(multi, 5)
    assert value['status'] == 'rear'
    if reason == 'deadline':
        multi.now += bot.REPATH_MS
    else:
        value.update(routeGoal=[-3., 0, 0], path=[[-3., 0, 0]], step=0)
        multi.now += 100
    fight(multi, placements)
    bot.think(multi, 5)
    points = [value['position'], *value['path']]
    assert len(points) > 2
    for start, end in zip(points, points[1:]):
        assert math.dist(body_collision.clip(start, end, [[0, 0, .2]], slide=False), end) < .01


def test_sideways_flanking_faces_target_and_does_not_accumulate_run_speed(multi):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-2.4, 0, .2), 0)})
    value = actors.data(multi, 3)
    plan(multi, 3)
    value.update(path=[[-2.4, 2., 0.]], step=0, lastMoveAt=multi.now, forwardMs=bot.RUN_DELAY_MS)
    multi.now += bot.MOVE_MS
    multi.context['botWorld']['sampledAt'] = multi.now
    bot.move(multi, 3)
    packet = next(item for item in reversed(multi.result['send']) if item['reason'] == 'bot-move')
    move = move_flow.decode_move_bc_with_system_and_active(packet['body'])
    assert move.direction_yaw == 0 and abs(move.left_right) == 1000
    assert move.current_velocity == 3000 and value['forwardMs'] == 0
    assert value['position'] == [-2.4, 0, .2]


def test_flanker_takes_an_attack_opportunity_in_a_moving_body_simulation(multi):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 5: ((-5, 0, .2), 0)}
    value = actors.data(multi, 5)
    value['profile'] = 'aggressive'
    for tick in range(200):
        fight(multi, placements)
        multi.result['send'].clear()
        if tick % 2 == 0:
            bot.think(multi, 5)
        if value.get('action', {}).get('index', -1) >= 0:
            assert value['action']['intent'] in battle.ATTACK_CHAINS and value['attackCount'] == 1
            assert math.dist(placements[5][0], placements[1][0]) <= bot.MELEE_RANGE
            break
        if multi.session['pending'].get(actors.timerName(bot.MOVE_TIMER, 5), math.inf) <= multi.now:
            bot.move(multi, 5)
        packets = [item for item in multi.result['send'] if item['reason'] == 'bot-move']
        if packets:
            move = move_flow.decode_move_bc_with_system_and_active(packets[-1]['body'])
            yaw = math.radians(move.direction_yaw)
            dx = -move.forward_back * math.cos(yaw) + move.left_right * math.sin(yaw)
            dy = move.forward_back * math.sin(yaw) + move.left_right * math.cos(yaw)
            scale = move.current_velocity / 1000 * .05 / math.hypot(dx, dy)
            origin = placements[5][0]
            desired = (origin[0] + dx * scale, origin[1] + dy * scale, origin[2])
            actual = body_collision.clip(origin, desired, [placements[rid][0] for rid in (1, 2)])
            placements[5] = (actual, move.direction_yaw)
        else:
            placements[5] = (placements[5][0], value['heading'])
        multi.now += 50
    else:
        pytest.fail(f"flanker stalled at {placements[5][0]} with status {value['status']}")


def test_flanker_finishes_positioning_then_attacks_without_endless_circling(multi):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-4, 0, .2), 0)}
    fight(multi, placements)
    choice = plan(multi, 3)
    heading = round(math.degrees(math.atan2(choice.goal[1], -choice.goal[0])))
    placements[3] = ((*choice.goal[:2], .2), heading)
    fight(multi, placements)
    bot.think(multi, 3)
    value = actors.data(multi, 3)
    assert value['status'] == 'ready'
    multi.now = value['reactAt']
    fight(multi, placements)
    bot.think(multi, 3)
    assert value['action']['index'] >= 0
    assert value['action']['intent'] in battle.ATTACK_CHAINS


def test_human_teammate_can_hold_front_while_friendly_bot_flanks(multi):
    fight(multi, {2: ((0, 0, .2), 180), 1: ((-1.7, 0, .2), 0), 12: ((-4, 0, .2), 0)}, target=2)
    choice = plan(multi, 12, 2)
    assert choice is not None and choice.mode in ('flank', 'rear')
    assert math.dist(choice.goal[:2], (-1.7, 0)) >= bot_tactics.SPACING


@pytest.mark.parametrize('fault', ['dead', 'spawning', 'missing', 'height', 'distant'])
def test_inactive_or_unrelated_allies_do_not_create_a_team_plan(multi, fault):
    world = fight(multi, {1: ((0, 0, .2), 180), 2: ((-3, 0, .2), 0), 3: ((-1.7, 0, .2), 0)})
    if fault in ('dead', 'spawning'):
        actors.data(multi, 3)['life'] = {'state': fault}
    elif fault == 'missing':
        world['actors']['3']['ready'] = False
    elif fault == 'height':
        world['actors']['3']['position'][2] = 4
    else:
        world['actors']['3']['position'][0] = -30
    assert plan(multi) is None


def test_solo_duel_uses_close_pursuit_without_a_team_formation(multi):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-4, 0, .2), 0)})
    bot.think(multi, 2)
    assert actors.data(multi, 2)['status'] == 'press'
    assert 'tactic' not in actors.data(multi, 2)


def test_narrow_corridor_uses_reachable_spacing_instead_of_off_mesh_flank(multi, monkeypatch):
    nav = NavMesh.fromPolygons([[(-10, -.6, 0), (10, -.6, 0), (10, .6, 0), (-10, .6, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-4, 0, .2), 0)})
    choice = plan(multi, 3)
    assert choice is not None and choice.mode == 'support'
    assert choice.goal[0] < -bot.MELEE_RANGE
    assert all(nav.project(point) is not None for point in choice.route.points)


def test_target_selection_spreads_excess_attackers_but_keeps_melee_engagement(multi):
    world = fight(multi, {1: ((0, 0, .2), 180), 12: ((0, 4, .2), 180),
                         2: ((-5, 0, .2), 0), 3: ((-1.7, 0, .2), 0), 4: ((0, -1.7, .2), 90)})
    nav = bot.navigationFor(multi)
    assert bot_roaming.sense(multi, nav, world, 2)['targetId'] == 12
    assert bot_roaming.sense(multi, nav, world, 3)['targetId'] == 1


def test_hidden_enemy_is_never_selected_to_reduce_crowding(multi):
    world = fight(multi, {1: ((0, 0, .2), 180), 12: ((-15, 0, .2), 0),
                         2: ((-5, 0, .2), 0), 3: ((-1.7, 0, .2), 0), 4: ((0, -1.7, .2), 90)})
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2)['targetId'] == 1


def test_plan_is_stable_across_target_turns_and_replays_from_saved_state(multi):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-4, 0, .2), 0)}
    fight(multi, placements)
    before = deepcopy(multi.state)
    choice = plan(multi, 3)
    after = deepcopy(multi.state)
    multi.state = before
    multi.session = before['sessions']['1']
    assert plan(multi, 3) == choice
    assert multi.state == after and app.validateState(after)
    multi.now += 100
    placements[1] = ((0, 0, .2), 0)
    fight(multi, placements)
    assert plan(multi, 3).goal == choice.goal


def test_stale_world_stop_and_respawn_clear_tactical_reservations(multi):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-4, 0, .2), 0)})
    plan(multi, 3)
    value = actors.data(multi, 3)
    saved = deepcopy(value['tactic'])
    multi.context['botWorld']['sampledAt'] -= 1000
    bot.think(multi, 3)
    assert 'tactic' not in value
    value['tactic'] = deepcopy(saved)
    bot.stop(multi, 3)
    assert 'tactic' not in value
    value['tactic'] = deepcopy(saved)
    bot_life.clearPerception(value)
    assert 'tactic' not in value


def test_started_attack_is_not_cancelled_for_formation(multi):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-1.7, .1, .2), 0)})
    battle.startAction(multi, 300550, released=True, actorId=3)
    previous = deepcopy(actors.data(multi, 3)['action'])
    bot.think(multi, 3)
    assert actors.data(multi, 3)['action'] == previous


def test_incoming_threat_takes_priority_over_flanking(multi):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.7, .1, .2), 0), 3: ((-1.7, 0, .2), 0)}
    fight(multi, placements)
    value = actors.data(multi, 3)
    multi.context['botWorld']['actors']['1']['targetId'] = 3
    battle.startAction(multi, 300560, released=False)
    bot.think(multi, 3)
    assert value['status'] == 'observe-threat'
    multi.now += 300
    fight(multi, placements)
    multi.context['botWorld']['actors']['1']['targetId'] = 3
    bot.think(multi, 3)
    assert value['status'] == 'parry' and value['action']['intent'] == 300580
    assert not value['path'] or 'footwork' in value


def test_new_target_and_lost_target_discard_old_formation(multi):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-4, 0, .2), 0)}
    fight(multi, placements)
    plan(multi, 3)
    placements[1] = ((40, 0, .2), 180)
    placements[12] = ((0, 2, .2), 180)
    world = fight(multi, placements)
    value = actors.data(multi, 3)
    bot_roaming.sense(multi, bot.navigationFor(multi), world, 3)
    assert value['opponentId'] == 12 and 'tactic' not in value
    plan(multi, 3, 12)
    world['actors']['12']['ready'] = False
    bot.think(multi, 3)
    assert 'tactic' not in value


@pytest.mark.parametrize('field,value', [('until', -1), ('targetId', 0), ('goal', [math.nan, 0, 0]),
                                        ('mode', 'invalid'), ('targetPosition', [0, 0])])
def test_invalid_plan_is_rejected_without_changing_state_version(multi, field, value):
    fight(multi, {1: ((0, 0, .2), 180), 2: ((-1.7, 0, .2), 0), 3: ((-4, 0, .2), 0)})
    plan(multi, 3)
    assert app.validateState(multi.state)
    actors.data(multi, 3)['tactic'][field] = value
    assert not app.validateState(multi.state)
