from copy import deepcopy
import math

from Business.scripts import actors, app, bot, bot_roaming
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi, sample


def isolated(flow, origin=(0, 0, .2), enemy=(50, 0, .2), heading=0):
    placements = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(flow.session)}
    placements.update({2: (origin, heading), 1: (enemy, 180)})
    return sample(flow, placements)


def test_distant_enemy_is_not_a_target_and_bot_advances_its_own_lane(multi):
    isolated(multi)
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], heading=0, patrolAnchor=[10., 5., .2])
    bot.think(multi, 2)
    assert value.get('opponentId') is None
    assert value['status'] == 'advance'
    assert value['path'][-1][:2] == [10., 5.]
    multi.now += 50
    isolated(multi)
    bot.move(multi, 2)
    assert value['position'] == [0, 0, .2]
    assert value['moving']
    multi.now += 50
    isolated(multi, origin=(.1, .05, .2))
    bot.move(multi, 2)
    assert value['position'] == [.1, .05, .2]


def test_only_nearby_or_in_view_enemies_can_be_perceived(multi):
    nav = bot.navigationFor(multi)
    for position, expected in [((10, 0, .2), 1), ((-10, 0, .2), None), ((-2, 0, .2), 1), ((40, 0, .2), None)]:
        world = isolated(multi, enemy=position)
        multi.now += 400
        world['sampledAt'] = multi.now
        bot_roaming.sense(multi, nav, world, 2)
        assert actors.data(multi, 2).get('opponentId') == expected


def test_navigation_wall_blocks_acquisition(multi, monkeypatch):
    nav = NavMesh.fromPolygons([[(-5, -5, 0), (0, -5, 0), (0, 5, 0), (-5, 5, 0)],
                               [(1, -5, 0), (5, -5, 0), (5, 5, 0), (1, 5, 0)]])
    world = isolated(multi, origin=(-1, 0, .2), enemy=(2, 0, .2))
    bot_roaming.sense(multi, nav, world, 2)
    assert actors.data(multi, 2).get('opponentId') is None


def test_lost_target_search_uses_last_seen_position_not_live_enemy_position(multi):
    nav = bot.navigationFor(multi)
    world = isolated(multi, enemy=(10, 0, .2))
    bot_roaming.sense(multi, nav, world, 2)
    value = actors.data(multi, 2)
    value['position'] = [0., 0., .2]
    # Leaving sight also invalidates a target between candidate acquisition ticks.
    multi.now += 100
    world = isolated(multi, enemy=(50, 50, .2))
    bot_roaming.sense(multi, nav, world, 2)
    assert value['opponentId'] is None
    goal, mode = bot_roaming.goal(multi, nav, 2)
    assert (goal, mode) == ([10., 0., .2], 'search')
    multi.now += bot_roaming.SEARCH_MS
    goal, mode = bot_roaming.goal(multi, nav, 2)
    assert mode != 'search'
    assert 'lastSeenPosition' not in value


def test_patrol_routes_differ_and_replay_from_saved_state(multi):
    nav = bot.navigationFor(multi)
    previous = deepcopy(multi.state)
    goals = [bot_roaming.goal(multi, nav, rid)[0] for rid in range(2, 21)]
    assert len({tuple(goal) for goal in goals}) > 6
    assert all(nav.project(goal) is not None for goal in goals)
    after = deepcopy(multi.state)
    multi.state = previous
    multi.session = previous['sessions']['1']
    assert [bot_roaming.goal(multi, nav, rid)[0] for rid in range(2, 21)] == goals
    assert multi.state == after
    assert app.validateState(after)


def test_missing_own_pose_still_stops_patrol(multi):
    world = isolated(multi)
    world['actors']['2']['ready'] = False
    bot.think(multi, 2)
    assert actors.data(multi, 2)['status'] == 'world-unavailable'


def test_disconnected_nearby_faces_do_not_hide_a_reachable_patrol_goal():
    polygons = [[(-2, -3, 0), (6, -3, 0), (6, 3, 0), (-2, 3, 0)]]
    for y in (-1.5, -.9, -.3, .3, .9, 1.5):
        polygons.append([(9.8, y - .2, 0), (10.2, y - .2, 0),
                         (10.2, y + .2, 0), (9.8, y + .2, 0)])
    nav = NavMesh.fromPolygons(polygons)
    origin, desired = (0, 0, .2), (10, 0, .2)
    goal = bot_roaming.reachableGoal(nav, origin, desired)
    assert goal == [2., 0., 0.]
    assert nav.findPath(origin, goal).status == 'ready'


def test_patrol_goal_does_not_cross_a_portal_narrower_than_the_body():
    nav = NavMesh.fromPolygons([
        [(-4, -2, 0), (0, -2, 0), (0, -.3, 0), (0, .3, 0), (0, 2, 0), (-4, 2, 0)],
        [(0, -.3, 0), (4, -.3, 0), (4, .3, 0), (0, .3, 0)]])
    origin, desired = (-3, 0, .2), (2, 0, .2)
    assert nav.findPath(origin, desired, radius=0).status == 'ready'
    goal = bot_roaming.reachableGoal(nav, origin, desired)
    assert nav.locate(goal) == 0
    assert nav.findPath(origin, goal).status == 'ready'


def test_unreachable_anchor_falls_back_to_local_movement(multi, monkeypatch):
    nav = NavMesh.fromPolygons([[(-6, -6, 0), (6, -6, 0), (6, 6, 0), (-6, 6, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], heading=0, patrolAnchor=[50., 0., .2])
    isolated(multi)
    bot.think(multi, 2)
    assert value['status'] == 'advance'
    assert actors.timerName(bot.MOVE_TIMER, 2) in multi.session['pending']
    assert math.dist(value['path'][-1][:2], value['position'][:2]) > .65
    for _ in range(10):
        multi.now += 50
        isolated(multi)
        bot.move(multi, 2)
        if value['moving']:
            break
    assert value['moving']
    assert value['position'] == [0., 0., .2]


def test_local_fallback_does_not_choose_the_current_position_and_replays(multi):
    nav = NavMesh.fromPolygons([[(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)]])
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], patrolAnchor=[0., 0., .2])
    previous = deepcopy(value)
    first = bot_roaming.goal(multi, nav, 2)
    assert math.dist(first[0][:2], value['position'][:2]) > .65
    assert bot_roaming.goal(multi, nav, 2) == first
    after = deepcopy(value)
    value.clear()
    value.update(previous)
    assert bot_roaming.goal(multi, nav, 2) == first
    assert value == after


def test_no_local_route_waits_then_retries_without_fabricating_movement(multi):
    nav = NavMesh.fromPolygons([[(-.4, -.4, 0), (.4, -.4, 0), (.4, .4, 0), (-.4, .4, 0)]])
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], patrolAnchor=[50., 0., .2])
    assert bot_roaming.goal(multi, nav, 2) == (None, 'no-path')
    seed = value['routeSeed']
    multi.now += 999
    assert bot_roaming.goal(multi, nav, 2) == (None, 'patrol-wait')
    assert value['routeSeed'] == seed
    multi.now += 1
    assert bot_roaming.goal(multi, nav, 2) == (None, 'no-path')
    assert value['routeSeed'] != seed
    assert value['position'] == [0., 0., .2]
    assert 'roamGoal' not in value
    openNav = bot.navigationFor(multi)
    multi.now += 1000
    goal, mode = bot_roaming.goal(multi, openNav, 2)
    assert mode == 'advance'
    assert openNav.findPath(value['position'], goal).status == 'ready'
