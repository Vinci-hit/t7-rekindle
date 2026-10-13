from copy import deepcopy
import json

import pytest

from Business.scripts import actors, app, battle, bot, bot_life, bot_roaming, bot_tactics, combat
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi, sample


def scene(flow, *, elapsed=0, current=(1.4, 0, .2), challenger=(0, -1.5, .2)):
    flow.now += elapsed
    placements = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(flow.session)}
    placements.update({2: ((0, 0, .2), 0), 1: (current, 180), 12: (challenger, -90)})
    return sample(flow, placements)


@pytest.fixture
def engaged(multi):
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], heading=0, initialized=True, heightOffset=.2, profile='balanced')
    world = scene(multi, challenger=(40, 0, .2))
    assert bot_roaming.sense(multi, bot.navigationFor(multi), world, 2)['targetId'] == 1
    scene(multi, elapsed=1300)
    return multi


def sense(flow):
    return bot_roaming.sense(flow, bot.navigationFor(flow), actors.world(flow), 2)


def hit(flow, attacker=12):
    combat.applyHit(flow, attacker, 2, actors.pair(actors.world(flow), attacker, 2))


def test_one_minor_hit_is_remembered_without_forcing_a_target_switch(engaged):
    hit(engaged)
    value = actors.data(engaged, 2)
    assert sense(engaged)['targetId'] == 1
    assert value['threats']['12']['damage'] == 20
    assert value['lastSeenPosition'] == [1.4, 0, .2]


def test_repeated_damage_switches_to_the_visible_attacker_and_releases_old_plans(engaged):
    hit(engaged)
    assert sense(engaged)['targetId'] == 1
    scene(engaged, elapsed=900)
    value = actors.data(engaged, 2)
    value.update(routeGoal=[4., 0., 0.], path=[[4., 0., 0.]], step=0, pressAt=engaged.now + 500)
    hit(engaged)
    action = deepcopy(value['action'])
    hurtDeadline = engaged.session['pending'][combat.hurtTimer(2)]
    assert sense(engaged)['targetId'] == 12
    assert value['targetSince'] == engaged.now
    assert 'routeGoal' not in value and 'pressAt' not in value and not value['path']
    assert value['action'] == action and engaged.session['pending'][combat.hurtTimer(2)] == hurtDeadline
    assert any('bot-target actor=2 from=1 to=12' in line["message"] for line in engaged.result['logs'])


def test_switch_cooldown_prevents_immediate_bounce_but_not_a_later_reassessment(engaged):
    hit(engaged)
    scene(engaged, elapsed=900)
    hit(engaged)
    assert sense(engaged)['targetId'] == 12
    scene(engaged, elapsed=400, challenger=(0, -5, .2))
    battle.startAction(engaged, battle.DASH_ATTACK, released=True)
    hit(engaged, 1)
    assert sense(engaged)['targetId'] == 12
    scene(engaged, elapsed=1000, challenger=(0, -5, .2))
    assert sense(engaged)['targetId'] == 1


def test_an_ongoing_weapon_action_finishes_before_retargeting(engaged):
    battle.startAction(engaged, battle.DASH_ATTACK, released=True, actorId=12)
    hit(engaged)
    scene(engaged, elapsed=combat.HURT_MS)
    combat.finishHurt(engaged, 2)
    battle.startAction(engaged, 300550, released=True, actorId=2)
    value = actors.data(engaged, 2)
    action = deepcopy(value['action'])
    deadline = engaged.session['pending'][bot.ACTION_TIMER]
    assert sense(engaged)['targetId'] == 1
    assert value['action'] == action and engaged.session['pending'][bot.ACTION_TIMER] == deadline
    battle.resetAction(engaged, 2)
    scene(engaged, elapsed=bot_roaming.PERCEPTION_MS)
    assert sense(engaged)['targetId'] == 12


@pytest.mark.parametrize('lost', ['dead', 'missing', 'hidden'])
def test_invalid_current_target_does_not_keep_the_switch_cooldown(engaged, lost):
    actors.data(engaged, 2)['targetSince'] = engaged.now
    world = scene(engaged, elapsed=100)
    if lost == 'dead':
        engaged.session['life'] = {'state': 'dead'}
    elif lost == 'missing':
        world['actors']['1']['ready'] = False
    else:
        world['actors']['1']['position'] = [40, 0, .2]
    assert sense(engaged)['targetId'] == 12


def test_a_nearly_defeated_melee_target_keeps_a_finishing_bias(engaged):
    engaged.session['health'] = 20
    hit(engaged)
    scene(engaged, elapsed=900)
    hit(engaged)
    assert sense(engaged)['targetId'] == 1


def test_a_much_closer_opponent_can_replace_a_distant_target_without_damage(engaged):
    scene(engaged, current=(9, 0, .2))
    assert sense(engaged)['targetId'] == 12


@pytest.mark.parametrize('unavailable', ['behind', 'wall', 'dead'])
def test_damage_memory_never_selects_an_unperceived_or_dead_attacker(engaged, monkeypatch, unavailable):
    battle.startAction(engaged, battle.DASH_ATTACK, released=True, actorId=12)
    hit(engaged)
    world = scene(engaged, elapsed=400)
    if unavailable == 'behind':
        world['actors']['12']['position'] = [-4, 0, .2]
    elif unavailable == 'dead':
        actors.data(engaged, 12)['life'] = {'state': 'dead'}
    else:
        nav = NavMesh.fromPolygons([
            [(-5, -1, 0), (5, -1, 0), (5, 5, 0), (-5, 5, 0)],
            [(-5, -5, 0), (5, -5, 0), (5, -1.2, 0), (-5, -1.2, 0)]])
        monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    assert sense(engaged)['targetId'] == 1


def test_expired_damage_does_not_keep_aggro_or_unbounded_memory(engaged):
    hit(engaged)
    scene(engaged, elapsed=5100)
    assert sense(engaged)['targetId'] == 1
    assert not actors.data(engaged, 2).get('threats')


def test_outgoing_incidental_damage_is_not_a_threat_to_the_attacker(engaged):
    combat.applyHit(engaged, 2, 12, actors.pair(actors.world(engaged), 2, 12))
    value = actors.data(engaged, 2)
    assert value['opponentId'] == 1
    assert not value.get('threats')


@pytest.mark.parametrize('reset', ['stop', 'death', 'respawn'])
def test_threat_and_switch_state_survive_reload_and_clear_on_lifecycle_reset(engaged, reset):
    hit(engaged)
    state = json.loads(json.dumps(engaged.state))
    assert app.validateState(state) and app.migrateState(app.STATE_VERSION, state) == state
    value = actors.data(engaged, 2)
    if reset == 'stop':
        bot.stop(engaged, 2)
    elif reset == 'death':
        bot_life.die(engaged, 2, 1, value['position'])
    else:
        bot_life.clearPerception(value)
    assert all(key not in value for key in ('threats', 'targetSince'))


@pytest.mark.parametrize('invalid', [None, {'21': {'damage': 20, 'at': 1000}},
                                    {'12': {'damage': float('nan'), 'at': 1000}},
                                    {'12': {'damage': 20, 'at': -1}},
                                    {'12': {'damage': True, 'at': 1000}}])
def test_invalid_damage_memory_is_rejected(engaged, invalid):
    actors.data(engaged, 2)['threats'] = invalid
    assert not app.validateState(engaged.state)


@pytest.mark.parametrize('invalid', [-1, True, '1000'])
def test_invalid_switch_timestamp_is_rejected(engaged, invalid):
    actors.data(engaged, 2)['targetSince'] = invalid
    assert not bot_tactics.validState(actors.data(engaged, 2))
