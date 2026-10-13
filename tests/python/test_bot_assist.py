from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_life, bot_roaming, bot_tactics, bot_unstuck
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi, sample


def cornerFight(flow, monkeypatch):
    nav = NavMesh.fromPolygons([
        [(-6, -2, 0), (2, -2, 0), (2, 2, 0), (-6, 2, 0)],
        [(2, -2, 0), (6, -2, 0), (6, 6, 0), (2, 6, 0), (2, 2, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    placements = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(flow.session)}
    placements.update({1: ((3, 4, .2), 90), 2: ((0, 0, .2), 0), 3: ((3, 0, .2), -90)})
    world = sample(flow, placements)
    value = actors.data(flow, 2)
    value.update(position=[0., 0., .2], heading=0, initialized=True, heightOffset=.2)
    bot_roaming.remember(flow, 2, 1, (1.5, 0, .2))
    assert bot_roaming.sense(flow, nav, world, 3)['targetId'] == 1
    assert not bot_roaming.visible(nav, world['actors']['2'], world['actors']['1'], 18)
    return nav, world, value


def test_lost_target_prioritizes_helping_a_nearby_fighting_ally(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    bot.think(multi, 2)
    assert value['status'] == 'assist'
    assert value['opponentId'] is None
    assert value['assist']['allyId'] == 3
    assert value['assist']['targetId'] == 1
    goal = value['assist']['goal']
    assert math.dist(goal[:2], world['actors']['3']['position'][:2]) >= bot_tactics.SPACING
    assert nav.findPath(value['position'], goal).status == 'ready'
    assert all(nav.project(point) is not None for point in value['path'])
    assert actors.timerName(bot.MOVE_TIMER, 2) in multi.session['pending']
    assert value['position'] == [0., 0., .2]
    multi.now += 400
    world['sampledAt'] = multi.now
    world['actors']['2']['position'] = [*goal[:2], .2]
    bot.think(multi, 2)
    assert value['opponentId'] == 1
    assert 'assist' not in value


def test_assistance_uses_ally_report_not_hidden_enemy_live_position(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    first = bot_tactics.assist(multi, nav, 2)
    assert first is not None
    known = list(value['assist']['targetPosition'])
    world['actors']['1']['position'] = [90, 90, .2]
    second = bot_tactics.assist(multi, nav, 2)
    assert second.goal == first.goal
    assert bot_tactics.facingTarget(multi, 2) == known == [3, 4, .2]


def test_arriving_at_assistance_keeps_searching_when_enemy_is_not_visible(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    choice = bot_tactics.assist(multi, nav, 2)
    world['actors']['2']['position'] = [*choice.goal[:2], .2]
    value.update(position=list(world['actors']['2']['position']), lastTurnAt=multi.now)
    world['actors']['1']['position'] = [90, 90, .2]
    multi.now += 100
    world['sampledAt'] = multi.now
    bot.think(multi, 2)
    assert value['opponentId'] is None and value['status'] == 'assist'
    assert not value['path'] and not value['moving']
    assert value['heading'] != 0


@pytest.mark.parametrize('condition', ['idle', 'dead', 'far', 'missing', 'enemy-dead', 'stale-report'])
def test_invalid_or_inactive_allies_do_not_attract_assistance(multi, monkeypatch, condition):
    nav, world, value = cornerFight(multi, monkeypatch)
    assert bot_tactics.assist(multi, nav, 2) is not None
    ally = actors.data(multi, 3)
    if condition == 'idle':
        ally['opponentId'] = None
    elif condition == 'dead':
        ally['life'] = {'state': 'dead'}
    elif condition == 'far':
        world['actors']['3']['position'] = [30, 0, .2]
    elif condition == 'missing':
        world['actors']['3']['ready'] = False
    elif condition == 'enemy-dead':
        multi.session['life'] = {'state': 'dead'}
    else:
        ally['searchUntil'] = multi.now
    assert bot_tactics.assist(multi, nav, 2) is None
    assert 'assist' not in value


def test_no_free_assist_position_keeps_search_instead_of_forcing_a_path(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    monkeypatch.setattr(bot_tactics, 'groundGoal', lambda nav, desired: None)
    assert bot_tactics.assist(multi, nav, 2) is None
    bot.think(multi, 2)
    assert value['status'] == 'search'


def test_assisting_bots_reserve_distinct_positions(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    world['actors']['4'].update(position=[-.5, 1, .2], heading=0)
    actors.data(multi, 4).update(position=[-.5, 1, .2], heading=0, heightOffset=.2)
    first = bot_tactics.assist(multi, nav, 2)
    second = bot_tactics.assist(multi, nav, 4)
    assert first is not None and second is not None
    assert math.dist(first.goal[:2], second.goal[:2]) >= bot_tactics.SPACING


def test_visible_enemy_takes_over_from_assistance_without_resetting_attack(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    bot.think(multi, 2)
    assert 'assist' in value
    battle.startAction(multi, 300560, released=True, actorId=2)
    action = deepcopy(value['action'])
    multi.now += 400
    world['sampledAt'] = multi.now
    world['actors']['1']['position'] = [1.5, 0, .2]
    bot.think(multi, 2)
    assert value['opponentId'] == 1
    assert 'assist' not in value
    assert value['action'] == action


def test_fighting_human_teammate_can_receive_assistance(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    actors.data(multi, 3)['opponentId'] = None
    world['actors']['12'].update(position=[0, 0, .2], heading=0)
    world['actors']['1'].update(position=[3, 2.5, .2], targetId=2)
    world['actors']['2']['position'] = [3, 4, .2]
    actors.data(multi, 12).update(position=[0, 0, .2], heading=0, heightOffset=.2)
    battle.startAction(multi, 300560, released=True)
    choice = bot_tactics.assist(multi, nav, 12)
    assert choice is not None
    assert actors.data(multi, 12)['assist']['allyId'] == 1


def test_assistance_state_replays_and_clears_on_lifecycle_reset(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    before = deepcopy(multi.state)
    choice = bot_tactics.assist(multi, nav, 2)
    after = deepcopy(multi.state)
    assert app.validateState(after)
    multi.state, multi.session = before, before['sessions']['1']
    assert bot_tactics.assist(multi, nav, 2) == choice
    assert multi.state == after
    value = actors.data(multi, 2)
    bot_life.clearPerception(value)
    assert 'assist' not in value


def test_unstuck_releases_assistance_reservation(multi, monkeypatch):
    nav, world, value = cornerFight(multi, monkeypatch)
    assert bot_tactics.assist(multi, nav, 2) is not None
    bot_unstuck.plan(multi, nav, 2, heading=0)
    assert 'assist' not in value


@pytest.mark.parametrize('field,invalid', [('allyId', 0), ('targetId', True), ('until', -1),
                                         ('goal', [math.nan, 0, 0]), ('targetPosition', [0, 0])])
def test_invalid_assistance_state_is_rejected(multi, monkeypatch, field, invalid):
    nav, world, value = cornerFight(multi, monkeypatch)
    assert bot_tactics.assist(multi, nav, 2) is not None
    value['assist'][field] = invalid
    assert not app.validateState(multi.state)
