from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_life, bot_roaming, combat
from Business.scripts.navigation import NavMesh
from test_bot_roaming import isolated
from test_bot_roster import multi


def acquire(flow, enemy=(1.5, 0, .2), origin=(0, 0, .2)):
    value = actors.data(flow, 2)
    value.update(position=list(origin), heading=0, initialized=True, heightOffset=.2)
    world = isolated(flow, origin=origin, enemy=enemy)
    assert bot_roaming.sense(flow, bot.navigationFor(flow), world, 2)['targetId'] == 1
    return value


@pytest.mark.parametrize('elapsed', [100, 400])
def test_engaged_enemy_behind_bot_is_retained_without_interrupting_attack(multi, elapsed):
    value = acquire(multi)
    battle.startAction(multi, 300560, released=True, actorId=2)
    action = deepcopy(value['action'])
    multi.now += elapsed
    world = isolated(multi, enemy=(-3.1, 0, .2))
    pair = bot_roaming.sense(multi, bot.navigationFor(multi), world, 2)
    assert pair is not None and pair['targetId'] == 1
    assert value['action'] == action
    assert value['lastSeenPosition'] == [-3.1, 0, .2]


def test_unknown_enemy_behind_bot_does_not_use_engagement_perception(multi):
    world = isolated(multi, enemy=(-3.1, 0, .2))
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2) is None


@pytest.mark.parametrize('profile', ['aggressive', 'balanced', 'defensive'])
def test_engagement_has_a_bounded_distance_margin(multi, profile):
    value = actors.data(multi, 2)
    value['profile'] = profile
    limit = bot_roaming.SIGHT_RANGE[profile]
    acquire(multi, enemy=(limit - .1, 0, .2))
    multi.now += 400
    world = isolated(multi, enemy=(limit + 1, 0, .2))
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2) is not None
    multi.now += 400
    world = isolated(multi, enemy=(limit + 3, 0, .2))
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2) is None


def test_hidden_target_keeps_frozen_memory_and_can_be_reacquired_behind(multi, monkeypatch):
    nav = NavMesh.fromPolygons([
        [(-10, -5, 0), (0, -5, 0), (0, 5, 0), (-10, 5, 0)],
        [(1, -5, 0), (10, -5, 0), (10, 5, 0), (1, 5, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    value = acquire(multi, enemy=(-2, 0, .2), origin=(-1, 0, .2))
    until = value['searchUntil']
    for enemy in ((3, 0, .2), (4, 1, .2)):
        multi.now += 400
        world = isolated(multi, origin=(-1, 0, .2), enemy=enemy)
        assert bot_roaming.sense(multi, nav, world, 2) is None
        assert value['opponentId'] is None
        assert value['searchTargetId'] == 1
        assert value['lastSeenPosition'] == [-2, 0, .2]
        assert value['searchUntil'] == until
    multi.now += 100
    world = isolated(multi, origin=(-1, 0, .2), enemy=(-5, 0, .2))
    assert bot_roaming.sense(multi, nav, world, 2)['targetId'] == 1


def test_missing_target_sample_does_not_erase_engagement_memory(multi):
    value = acquire(multi)
    multi.now += 100
    world = isolated(multi)
    world['actors']['1']['ready'] = False
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2) is None
    assert value['searchTargetId'] == 1
    assert value['lastSeenPosition'] == [1.5, 0, .2]


@pytest.mark.parametrize('lostBeforeGap', [False, True])
def test_expired_memory_does_not_extend_rear_perception_indefinitely(multi, lostBeforeGap):
    value = acquire(multi)
    if lostBeforeGap:
        multi.now += 100
        bot_roaming.sense(multi, bot.navigationFor(multi), isolated(multi), 2)
    multi.now = value['searchUntil'] + 100
    world = isolated(multi, enemy=(-3.1, 0, .2))
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2) is None


def test_stale_own_pose_does_not_turn_or_refresh_search_memory(multi):
    value = acquire(multi)
    until = value['searchUntil']
    multi.now += 1000
    bot.think(multi, 2)
    assert value['status'] == 'world-unavailable'
    assert value['heading'] == 0
    assert value['searchUntil'] == until


def test_search_turns_at_last_seen_point_and_discovers_another_enemy(multi):
    value = acquire(multi)
    for _ in range(15):
        multi.now += 100
        world = isolated(multi, origin=(1.5, 0, .2), heading=value['heading'])
        world['actors']['12']['position'] = [-6.5, 0, .2]
        bot.think(multi, 2)
        if value.get('opponentId') == 12:
            break
        assert value['status'] == 'search'
        assert not value['moving']
        assert value['position'] == [1.5, 0, .2]
    assert value['heading'] != 0
    assert value['opponentId'] == 12


def test_search_reserves_time_for_scanning_even_if_last_seen_point_is_far(multi):
    value = acquire(multi, enemy=(10, 0, .2))
    multi.now = value['searchUntil'] - 1000
    isolated(multi, heading=value['heading'])
    bot.think(multi, 2)
    assert value['status'] == 'search'
    assert not value['moving']
    assert value['heading'] != 0
    assert actors.timerName(bot.MOVE_TIMER, 2) not in multi.session['pending']


def test_search_turn_does_not_cancel_an_ongoing_weapon_action(multi):
    value = acquire(multi)
    battle.startAction(multi, 300560, released=True, actorId=2)
    action = deepcopy(value['action'])
    multi.now += 100
    isolated(multi, origin=(1.5, 0, .2))
    bot.think(multi, 2)
    assert value['action'] == action
    assert battle.actionTimer(2) in multi.session['pending']
    assert value['heading'] != 0


def test_unreachable_search_point_triggers_turning_not_a_silent_stop(multi, monkeypatch):
    value = acquire(multi, enemy=(10, 0, .2))
    nav = NavMesh.fromPolygons([
        [(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)],
        [(9, -2, 0), (11, -2, 0), (11, 2, 0), (9, 2, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    multi.now += 100
    isolated(multi)
    bot.think(multi, 2)
    assert value['status'] == 'search'
    assert value['heading'] != 0
    assert not value['path'] and not value['moving']
    assert value['position'] == [0, 0, .2]


def test_search_expiry_patrols_near_combat_instead_of_resuming_old_route(multi):
    value = actors.data(multi, 2)
    value.update(patrolAnchor=[80., 0., .2], roamGoal=[80., 0., 0.], roamWaitUntil=multi.now + 9000)
    acquire(multi)
    assert 'roamGoal' not in value and 'roamWaitUntil' not in value
    multi.now += 100
    bot_roaming.sense(multi, bot.navigationFor(multi), isolated(multi), 2)
    multi.now = value['searchUntil']
    goal, mode = bot_roaming.goal(multi, bot.navigationFor(multi), 2)
    assert mode == 'patrol'
    assert math.dist(goal[:2], (1.5, 0)) <= 13.01
    assert value['patrolAnchor'] == [80., 0., .2]
    assert value.get('searchTargetId') is None
    assert 'lastSeenPosition' not in value


@pytest.mark.parametrize('replacement', [False, True])
def test_dead_target_is_forgotten_without_waiting_for_next_perception_tick(multi, replacement):
    value = acquire(multi)
    multi.session['life'] = {'state': 'dead'}
    multi.now += 100
    world = isolated(multi)
    if replacement:
        world['actors']['12']['position'] = [4, 0, .2]
    pair = bot_roaming.sense(multi, bot.navigationFor(multi), world, 2)
    if replacement:
        assert pair is not None and pair['targetId'] == 12
    else:
        assert pair is None
        assert 'lastSeenPosition' not in value
        assert bot_roaming.goal(multi, bot.navigationFor(multi), 2)[1] != 'search'


@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1)])
def test_actual_hit_refreshes_engagement_memory(multi, attacker, target):
    world = isolated(multi, enemy=(-1.5, 0, .2))
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], heading=0)
    combat.applyHit(multi, attacker, target, actors.pair(world, 2, 1))
    assert value['searchTargetId'] == 1
    assert value['lastSeenPosition'] == [-1.5, 0, .2]
    assert value['searchUntil'] == multi.now + bot_roaming.SEARCH_MS
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2)['targetId'] == 1


def test_incidental_hit_does_not_steal_another_active_target(multi):
    world = isolated(multi, enemy=(-1.5, 0, .2))
    value = actors.data(multi, 2)
    value.update(opponentId=12, searchTargetId=12, lastSeenPosition=[4., 0., .2], searchUntil=multi.now + 1000)
    combat.applyHit(multi, 1, 2, actors.pair(world, 2, 1))
    assert value['opponentId'] == value['searchTargetId'] == 12
    assert value['lastSeenPosition'] == [4., 0., .2]


def test_engagement_state_survives_reload_and_clears_on_lifecycle_reset(multi):
    value = acquire(multi)
    previous = deepcopy(multi.state)
    assert app.validateState(previous)
    multi.now += 400
    world = isolated(multi, enemy=(-3.1, 0, .2))
    expected = bot_roaming.sense(multi, bot.navigationFor(multi), world, 2)
    after = deepcopy(multi.state)
    multi.state, multi.session = previous, previous['sessions']['1']
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2) == expected
    assert multi.state == after
    value = actors.data(multi, 2)
    bot_life.clearPerception(value)
    assert all(key not in value for key in ('searchTargetId', 'lastSeenPosition', 'searchUntil', 'roamAnchor'))


def test_leaving_clears_active_and_remembered_targets(multi):
    value = acquire(multi)
    bot.stop(multi, 2)
    assert value.get('opponentId') is None
    assert all(key not in value for key in ('searchTargetId', 'lastSeenPosition', 'searchUntil', 'roamAnchor'))


@pytest.mark.parametrize('field,invalid', [('searchTargetId', 0), ('searchTargetId', True),
                                         ('searchTargetId', 21), ('searchTargetId', 2),
                                         ('roamAnchor', [math.nan, 0, 0])])
def test_invalid_engagement_state_is_rejected(multi, field, invalid):
    actors.data(multi, 2)[field] = invalid
    assert not app.validateState(multi.state)
