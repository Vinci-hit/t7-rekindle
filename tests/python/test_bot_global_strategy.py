from copy import deepcopy
import json
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_roaming, bot_tactics
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi, sample


def arena(flow, positions, *, profile='berserker', elapsed=0):
    flow.now += elapsed
    world = sample(flow, {rid: (position, 0) for rid, position in positions.items()})
    for rid, pose in world['actors'].items():
        pose['ready'] = int(rid) in positions
    actors.data(flow, 2).update(profile=profile, position=list(positions[2]), heading=0,
                                initialized=True, heightOffset=.2)
    return world


def sense(flow, world):
    return bot_roaming.sense(flow, bot.navigationFor(flow), world, 2)


@pytest.mark.parametrize('position', [(60, 0, .2), (-60, 0, .2), (0, 80, .2)])
def test_berserker_acquires_the_nearest_enemy_across_the_whole_map(multi, position):
    world = arena(multi, {2: (0, 0, .2), 1: position, 12: (90, 90, .2)})
    assert sense(multi, world)['targetId'] == 1
    assert app.validateState(multi.state)


def test_berserker_initial_choice_ignores_crowding_damage_and_enemy_health(multi):
    world = arena(multi, {2: (0, 0, .2), 1: (5, 0, .2), 12: (8, 0, .2),
                         3: (5, 2, .2), 4: (5, -2, .2), 5: (7, 2, .2)})
    for rid in (3, 4, 5):
        actors.data(multi, rid)['opponentId'] = 1
    actors.data(multi, 12)['health'] = 20
    bot_tactics.noticeDamage(multi, 2, 12, 80)
    assert sense(multi, world)['targetId'] == 1


def test_berserker_new_lock_chooses_nearest_instead_of_remembered_target(multi):
    world = arena(multi, {2: (0, 0, .2), 1: (8, 0, .2), 12: (7, 0, .2)})
    bot_roaming.remember(multi, 2, 1, world['actors']['1']['position'])
    actors.data(multi, 2)['targetSince'] = multi.now
    assert sense(multi, world)['targetId'] == 12


def test_berserker_skips_a_nearer_enemy_on_an_unreachable_island(multi, monkeypatch):
    nav = NavMesh.fromPolygons([
        [(-50, -4, 0), (50, -4, 0), (50, 4, 0), (-50, 4, 0)],
        [(-2, 9, 0), (2, 9, 0), (2, 13, 0), (-2, 13, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    world = arena(multi, {2: (0, 0, .2), 1: (0, 10, .2), 12: (30, 0, .2)})
    assert sense(multi, world)['targetId'] == 12


@pytest.mark.parametrize('profile', ['aggressive', 'balanced', 'defensive', 'berserker'])
def test_only_berserker_resists_a_moderately_closer_enemy_after_the_cooldown(multi, profile):
    world = arena(multi, {2: (0, 0, .2), 1: (10, 0, .2)}, profile=profile)
    assert sense(multi, world)['targetId'] == 1
    world = arena(multi, {2: (0, 0, .2), 1: (10, 0, .2), 12: (7, 0, .2)}, profile=profile, elapsed=4000)
    assert sense(multi, world)['targetId'] == (1 if profile == 'berserker' else 12)


def test_distant_berserker_target_is_not_replaced_for_a_small_relative_gain(multi):
    world = arena(multi, {2: (0, 0, .2), 1: (60, 0, .2)})
    assert sense(multi, world)['targetId'] == 1
    world = arena(multi, {2: (0, 0, .2), 1: (60, 0, .2), 12: (50, 0, .2)}, elapsed=4000)
    assert sense(multi, world)['targetId'] == 1


def test_berserker_has_a_longer_switch_cooldown_but_can_take_a_major_advantage(multi):
    world = arena(multi, {2: (0, 0, .2), 1: (12, 0, .2)})
    assert sense(multi, world)['targetId'] == 1
    positions = {2: (0, 0, .2), 1: (12, 0, .2), 12: (1.5, 0, .2)}
    assert sense(multi, arena(multi, positions, elapsed=1500))['targetId'] == 1
    assert sense(multi, arena(multi, positions, elapsed=1600))['targetId'] == 12


@pytest.mark.parametrize('invalid', ['dead', 'missing', 'unreachable'])
def test_berserker_reacquires_immediately_when_the_locked_target_is_invalid(multi, monkeypatch, invalid):
    world = arena(multi, {2: (0, 0, .2), 1: (3, 0, .2), 12: (30, 0, .2)})
    assert sense(multi, world)['targetId'] == 1
    battle.startAction(multi, 300550, released=True, actorId=2)
    action = deepcopy(actors.data(multi, 2)['action'])
    world = arena(multi, {2: (0, 0, .2), 1: (3, 0, .2), 12: (30, 0, .2)}, elapsed=100)
    if invalid == 'dead':
        multi.session['life'] = {'state': 'dead'}
    elif invalid == 'missing':
        world['actors']['1']['ready'] = False
    else:
        nav = NavMesh.fromPolygons([[(-50, -4, 0), (50, -4, 0), (50, 4, 0), (-50, 4, 0)]])
        monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
        world['actors']['1']['position'] = [3, 10, .2]
    assert sense(multi, world)['targetId'] == 12
    assert actors.data(multi, 2)['action'] == action


def test_berserker_extended_pursuit_survives_reload_but_expires_without_progress(multi):
    world = arena(multi, {2: (0, 0, .2), 1: (1.8, 0, .2)})
    assert sense(multi, world)['targetId'] == 1
    world = arena(multi, {2: (0, 0, .2), 1: (8, 0, .2), 12: (1.5, 0, .2)}, elapsed=2500)
    assert sense(multi, world)['targetId'] == 1
    state = json.loads(json.dumps(multi.state))
    assert app.validateState(state) and app.migrateState(app.STATE_VERSION, state) == state
    multi.state, multi.session = state, state['sessions']['1']
    world = arena(multi, {2: (0, 0, .2), 1: (8, 0, .2), 12: (1.5, 0, .2)}, elapsed=600)
    assert sense(multi, world)['targetId'] == 12


def test_berserker_pursuit_progress_still_has_a_hard_cap(multi):
    world = arena(multi, {2: (0, 0, .2), 1: (1.8, 0, .2)})
    assert sense(multi, world)['targetId'] == 1
    for elapsed, distance in ((400, 9.5), (2000, 9), (2000, 8.5), (1500, 8)):
        world = arena(multi, {2: (0, 0, .2), 1: (distance, 0, .2), 12: (1.5, 0, .2)}, elapsed=elapsed)
        assert sense(multi, world)['targetId'] == 1
    world = arena(multi, {2: (0, 0, .2), 1: (8, 0, .2), 12: (1.5, 0, .2)}, elapsed=bot_roaming.PERCEPTION_MS)
    assert sense(multi, world)['targetId'] == 12


def distantFight(flow, *, profile='balanced'):
    world = arena(flow, {2: (0, 0, .2), 1: (55, 0, .2), 3: (53, 1.5, .2)}, profile=profile)
    actors.data(flow, 3)['opponentId'] = 1
    bot_roaming.remember(flow, 3, 1, world['actors']['1']['position'])
    return world


@pytest.mark.parametrize('profile', ['aggressive', 'balanced', 'defensive'])
def test_other_profiles_assist_across_the_map_without_acquiring_hidden_enemies(multi, profile):
    world = distantFight(multi, profile=profile)
    bot.think(multi, 2)
    value = actors.data(multi, 2)
    assert value['opponentId'] is None and value['status'] == 'assist'
    assert value['assist']['allyId'] == 3 and value['assist']['targetId'] == 1
    assert value['path'] and bot.MOVE_TIMER in multi.session['pending']
    assert math.dist(value['assist']['goal'], world['actors']['3']['position']) < 2


def detourMesh():
    return NavMesh.fromPolygons([
        [(-3, -3, 0), (3, -3, 0), (3, 21, 0), (3, 27, 0), (-3, 27, 0)],
        [(3, 21, 0), (23, 21, 0), (23, 27, 0), (3, 27, 0)],
        [(23, -3, 0), (29, -3, 0), (29, 27, 0), (23, 27, 0), (23, 21, 0)]])


@pytest.mark.parametrize('profile', ['balanced', 'berserker'])
def test_global_strategy_accepts_reachable_long_detours_not_straight_wall_crossings(multi, monkeypatch, profile):
    nav = detourMesh()
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    world = arena(multi, {2: (0, 0, .2), 1: (26, 2.5, .2), 3: (26, 0, .2)}, profile=profile)
    actors.data(multi, 3)['opponentId'] = 1
    bot_roaming.remember(multi, 3, 1, world['actors']['1']['position'])
    bot.think(multi, 2)
    value = actors.data(multi, 2)
    assert value['opponentId'] == (1 if profile == 'berserker' else None)
    assert value['path']
    points = [value['position'], *value['path']]
    assert sum(math.dist(a, b) for a, b in zip(points, points[1:])) > math.dist(points[0], points[-1]) + 4


def test_global_assistance_uses_reports_and_releases_dead_or_missing_allies(multi):
    world = distantFight(multi)
    first = bot_tactics.assist(multi, bot.navigationFor(multi), 2)
    assert first is not None
    world['actors']['1']['position'] = [90, 90, .2]
    assert bot_tactics.assist(multi, bot.navigationFor(multi), 2).goal == first.goal
    world['actors']['3']['ready'] = False
    assert bot_tactics.assist(multi, bot.navigationFor(multi), 2) is None
    assert 'assist' not in actors.data(multi, 2)


def test_global_assistance_does_not_expand_local_formation_or_crowding_rules(multi):
    distantFight(multi)
    assert bot_tactics.nearbyAllies(multi, bot.navigationFor(multi), actors.world(multi), 2) == []
    assert bot_tactics.assist(multi, bot.navigationFor(multi), 2) is not None


@pytest.mark.parametrize('profile', ['balanced', 'berserker'])
def test_global_strategy_can_reach_higher_ground_via_a_ramp(multi, monkeypatch, profile):
    nav = NavMesh.fromPolygons([[(-5, -4, -.5), (100, -4, 10), (100, 4, 10), (-5, 4, -.5)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    world = arena(multi, {2: (0, 0, .2), 1: (55, 0, 5.7), 3: (53, 1.5, 5.5)}, profile=profile)
    actors.data(multi, 3)['opponentId'] = 1
    bot_roaming.remember(multi, 3, 1, world['actors']['1']['position'])
    bot.think(multi, 2)
    value = actors.data(multi, 2)
    assert value['opponentId'] == (1 if profile == 'berserker' else None)
    assert value['path'] and value['path'][-1][2] > 5
    if profile == 'balanced':
        assert value['assist']['allyId'] == 3


def test_global_assistance_does_not_cross_disconnected_navigation_islands(multi, monkeypatch):
    nav = NavMesh.fromPolygons([
        [(-4, -4, 0), (4, -4, 0), (4, 4, 0), (-4, 4, 0)],
        [(45, -4, 0), (65, -4, 0), (65, 4, 0), (45, 4, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    distantFight(multi)
    assert bot_tactics.assist(multi, nav, 2) is None
    assert 'assist' not in actors.data(multi, 2)
