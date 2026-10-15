from copy import deepcopy
import json
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_life, bot_profiles, bot_tactics
from Business.scripts.navigation import NavMesh
from test_bot_targeting import engaged, hit, multi, scene, sense


@pytest.fixture
def pursuing(engaged):
    scene(engaged, current=(1.8, 0, .2))
    assert sense(engaged)['targetId'] == 1
    return engaged


@pytest.mark.parametrize('profile', list(bot_profiles.PROFILES))
def test_all_profiles_pursue_a_retreat_instead_of_immediately_picking_a_nearer_enemy(pursuing, profile):
    value = actors.data(pursuing, 2)
    value['profile'] = profile
    for elapsed, distance in ((400, 4.8), (600, 4.9), (600, 5.5)):
        scene(pursuing, elapsed=elapsed, current=(distance, 0, .2))
        assert sense(pursuing)['targetId'] == 1


@pytest.mark.parametrize('distance', [2.19, 2.21])
def test_target_bias_has_no_cliff_at_the_old_melee_boundary(engaged, distance):
    world = scene(engaged, current=(distance, 0, .2), challenger=(0, -.7, .2))
    actors.data(engaged, 2).pop('pursuit', None)
    assert bot_tactics.selectTarget(engaged, bot.navigationFor(engaged), world, 2, [1, 12], 1) == 1


def test_retreat_protection_continues_approaching_the_original_target(pursuing):
    scene(pursuing, elapsed=400, current=(4.8, 0, .2))
    bot.think(pursuing, 2)
    value = actors.data(pursuing, 2)
    assert value['opponentId'] == 1
    assert value['footwork'] == {'targetId': 1, 'mode': 'press'}
    assert value['path'] and bot.MOVE_TIMER in pursuing.session['pending']
    assert math.dist(value['path'][-1][:2], (4.8, 0)) <= 1.3


def test_protection_requires_previous_close_engagement(engaged):
    actors.data(engaged, 2).pop('pursuit', None)
    scene(engaged, current=(5, 0, .2))
    assert sense(engaged)['targetId'] == 12


def test_close_combat_still_allows_target_distribution_among_teammates(pursuing):
    world = scene(pursuing, elapsed=bot.STRATEGY_MS, current=(1.8, 0, .2))
    for rid, y in ((3, -1), (4, 0), (5, 1)):
        world['actors'][str(rid)]['position'] = [2.6, y, .2]
        actors.data(pursuing, rid)['opponentId'] = 1
    assert sense(pursuing)['targetId'] == 12


def test_no_progress_expires_protection_and_does_not_restart_it(pursuing):
    scene(pursuing, elapsed=400, current=(5, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    scene(pursuing, elapsed=1500, current=(5, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    scene(pursuing, elapsed=500, current=(5, 0, .2), challenger=(40, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    assert 'pursuit' not in actors.data(pursuing, 2)
    scene(pursuing, elapsed=bot.STRATEGY_MS, current=(4.5, 0, .2))
    assert sense(pursuing)['targetId'] == 12
    assert actors.data(pursuing, 2)['pursuit']['targetId'] == 12


def test_closing_the_gap_extends_protection_but_not_beyond_its_hard_cap(pursuing):
    for elapsed, distance in ((400, 5.8), (1200, 5.0), (1000, 4.5)):
        scene(pursuing, elapsed=elapsed, current=(distance, 0, .2))
        assert sense(pursuing)['targetId'] == 1
    scene(pursuing, elapsed=1500, current=(4.4, 0, .2))
    assert sense(pursuing)['targetId'] == 12


def test_widening_or_small_distance_jitter_does_not_count_as_progress(pursuing):
    for distance in (5.5, 5.7, 5.5, 5.6):
        scene(pursuing, elapsed=400, current=(distance, 0, .2))
        assert sense(pursuing)['targetId'] == 1
    scene(pursuing, elapsed=500, current=(5.4, 0, .2))
    assert sense(pursuing)['targetId'] == 12


def test_seeing_the_target_in_cached_scans_refreshes_close_engagement(pursuing):
    value = actors.data(pursuing, 2)
    scanAt = value['senseAt']
    scene(pursuing, elapsed=100, current=(2.1, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    assert value['senseAt'] == scanAt
    assert value['pursuit']['closeAt'] == pursuing.now


@pytest.mark.parametrize('damage', ['single', 'repeated', 'heavy'])
def test_retreat_protection_distinguishes_incidental_hits_from_serious_threats(pursuing, damage):
    scene(pursuing, elapsed=400, current=(4.8, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    if damage == 'heavy':
        battle.startAction(pursuing, battle.DASH_ATTACK, released=True, actorId=12)
    hit(pursuing)
    if damage == 'repeated':
        assert sense(pursuing)['targetId'] == 1
        scene(pursuing, elapsed=400, current=(4.8, 0, .2))
        hit(pursuing)
    assert sense(pursuing)['targetId'] == (1 if damage == 'single' else 12)


@pytest.mark.parametrize('invalid', ['dead', 'missing', 'wall', 'range'])
def test_protection_never_retains_an_invalid_or_distant_target(pursuing, monkeypatch, invalid):
    value = actors.data(pursuing, 2)
    value['targetSince'] = pursuing.now - 1300
    world = scene(pursuing, elapsed=bot.STRATEGY_MS if invalid == 'range' else 100,
                  current=(6.1 if invalid == 'range' else 5, 0, .2))
    if invalid == 'dead':
        pursuing.session['life'] = {'state': 'dead'}
    elif invalid == 'missing':
        world['actors']['1']['ready'] = False
    elif invalid == 'wall':
        nav = NavMesh.fromPolygons([
            [(-3, -4, 0), (2, -4, 0), (2, 4, 0), (-3, 4, 0)],
            [(3, -4, 0), (7, -4, 0), (7, 4, 0), (3, 4, 0)]])
        monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    assert sense(pursuing)['targetId'] == 12
    assert value['pursuit']['targetId'] == 12


@pytest.mark.parametrize('reset', ['stop', 'death', 'respawn', 'stale-think', 'stale-move'])
def test_pursuit_memory_clears_when_the_bot_leaves_combat(pursuing, reset):
    value = actors.data(pursuing, 2)
    assert 'pursuit' in value
    if reset == 'stop':
        bot.stop(pursuing, 2)
    elif reset == 'death':
        bot_life.die(pursuing, 2, 1, value['position'])
    elif reset == 'respawn':
        bot_life.clearPerception(value)
    else:
        pursuing.now += 300
        if reset == 'stale-think':
            bot.think(pursuing, 2)
        else:
            value.update(path=[[4., 0., 0.]], step=0)
            bot.move(pursuing, 2)
    assert 'pursuit' not in value


def test_pursuit_is_serializable_and_old_states_can_gain_it_without_a_new_round(pursuing):
    scene(pursuing, elapsed=400, current=(5, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    state = json.loads(json.dumps(pursuing.state))
    assert app.validateState(state)
    assert app.migrateState(app.STATE_VERSION, state) == state
    memory = deepcopy(actors.data(pursuing, 2)['pursuit'])
    pursuing.state, pursuing.session = state, state['sessions']['1']
    scene(pursuing, elapsed=400, current=(5, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    assert actors.data(pursuing, 2)['pursuit'] == memory
    actors.data(pursuing, 2).pop('pursuit')
    assert app.validateState(pursuing.state)
    scene(pursuing, elapsed=400, current=(2.1, 0, .2))
    assert sense(pursuing)['targetId'] == 1
    assert actors.data(pursuing, 2)['pursuit']['closeAt'] == pursuing.now


@pytest.mark.parametrize('field,invalid', [('targetId', 0), ('targetId', True), ('closeAt', -1),
                                        ('progressAt', True), ('progressAt', 0), ('distance', -1),
                                        ('distance', math.nan), ('distance', math.inf), ('distance', True)])
def test_invalid_pursuit_memory_is_rejected(pursuing, field, invalid):
    actors.data(pursuing, 2)['pursuit'][field] = invalid
    assert not app.validateState(pursuing.state)
