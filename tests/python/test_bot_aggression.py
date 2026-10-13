from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_footwork, bot_profiles, bot_roaming
from test_bot_roster import multi
from test_bot_rhythm import arena
from test_bot_tactics import fight


@pytest.mark.parametrize('profile', bot_profiles.PROFILES)
def test_attack_distance_holds_position_instead_of_orbiting(multi, profile):
    gap = bot_footwork.GAPS[profile]
    pair = arena(multi, enemy=(gap, 0, .2))
    value = actors.data(multi, 2)
    value['profile'] = profile
    assert bot_footwork.plan(multi, bot.navigationFor(multi), pair, 2) is None
    assert 'stepPlan' not in value


def test_saved_circling_step_is_discarded_on_the_next_decision(multi):
    arena(multi)
    value = actors.data(multi, 2)
    value['stepPlan'] = dict(targetId=1, mode='circle', goal=[.2, .5, 0],
                             targetPosition=[1.2, 0, .2], gap=1.2, side=1, until=multi.now + 900)
    value.update(path=[[.2, .5, 0]], step=0, moving=True, footwork=dict(targetId=1, mode='circle'))
    multi.later(bot.MOVE_TIMER, 50)
    assert app.validateState(multi.state)
    bot.think(multi, 2)
    assert 'stepPlan' not in value
    assert not value['path'] and not value['moving']
    assert bot.MOVE_TIMER not in multi.session['pending']


@pytest.mark.parametrize('profile', bot_profiles.PROFILES)
def test_clear_close_approach_does_not_wait_for_a_flanking_budget(multi, profile):
    placements = {1: ((0, 0, .2), 180), 2: ((-1, 0, .2), 0), 3: ((-3, 0, .2), 0)}
    value = actors.data(multi, 3)
    value.update(profile=profile, tempo=100, pressAt=multi.now + 2400)
    fight(multi, placements)
    bot.think(multi, 3)
    assert value['status'] == 'press'
    assert 'tactic' not in value
    assert math.dist(value['path'][-1][:2], (0, 0)) <= 1.3


@pytest.mark.parametrize('profile', bot_profiles.PROFILES)
def test_approaching_target_prepares_an_attack_before_entering_melee(multi, profile):
    arena(multi, enemy=(4, 0, .2))
    value = actors.data(multi, 2)
    value['profile'] = profile
    bot.think(multi, 2)
    assert value['attackCount'] == 0
    assert type(value['reactAt']) is int
    readyAt = value['reactAt']
    multi.now += 100
    arena(multi, enemy=(2.1, 0, .2))
    value['profile'] = profile
    bot.think(multi, 2)
    assert value['reactAt'] == readyAt
    multi.now = max(multi.now + 100, readyAt)
    arena(multi, enemy=(1.5, 0, .2))
    value['profile'] = profile
    bot.think(multi, 2)
    assert value['attackCount'] == 1


@pytest.mark.parametrize('profile', bot_profiles.PROFILES)
def test_weapon_recovery_does_not_add_another_attack_wait(multi, profile):
    arena(multi, enemy=(1.5, 0, .2))
    value = actors.data(multi, 2)
    value.update(profile=profile, reactAt=multi.now)
    bot.think(multi, 2)
    assert value['attackCount'] == 1
    action = deepcopy(value['action'])
    deadline = multi.session['pending'][bot.ACTION_TIMER]
    multi.now += 100
    arena(multi, enemy=(1.5, 0, .2))
    value['profile'] = profile
    bot.think(multi, 2)
    assert value['action'] == action
    assert multi.session['pending'][bot.ACTION_TIMER] == deadline
    while value['action']['index'] >= 0:
        multi.now = multi.session['pending'][bot.ACTION_TIMER]
        app.handleTimer(multi, bot.ACTION_TIMER)
    arena(multi, enemy=(1.5, 0, .2))
    value['profile'] = profile
    bot.think(multi, 2)
    assert value['attackCount'] == 2
    assert multi.session['pending'][bot.ACTION_TIMER] - multi.now == battle.phaseMs(value['action']['intent'], 0)


def test_patrol_arrival_selects_the_next_search_goal_without_idle_wait(multi):
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], patrolAnchor=[0., 0., .2], roamGoal=[0., 0., 0.], roamCount=1)
    goal, mode = bot_roaming.goal(multi, bot.navigationFor(multi), 2)
    assert mode == 'patrol' and goal is not None
    assert math.dist(goal[:2], value['position'][:2]) > bot_roaming.ARRIVAL_RADIUS
    assert value.get('roamWaitUntil', 0) <= multi.now


def test_patrol_retry_keeps_scanning_for_targets(multi):
    arena(multi, enemy=(50, 0, .2))
    value = actors.data(multi, 2)
    value.update(roamWaitUntil=multi.now + 1000)
    bot.think(multi, 2)
    assert not value['moving']
    assert value['heading'] != 0
    assert value['attackCount'] == 0
