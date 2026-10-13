from copy import deepcopy
import json
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_footwork, bot_life, bot_profiles, bot_roaming, bot_tactics, bot_unstuck
from test_bot_roster import multi, sample


def arena(flow, actorId=2, *, position=(0, 0, .2), enemy=(1.2, 0, .2), heading=0):
    value = actors.data(flow, actorId)
    targetId = 1 if value['camp'] == 2 else 2
    placements = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(flow.session)}
    placements.update({actorId: (position, heading), targetId: (enemy, 180)})
    value.update(position=list(position), heading=heading, initialized=True, heightOffset=.2,
                 profile='balanced', tempo=100, opponentId=targetId)
    return actors.pair(sample(flow, placements), actorId, targetId)


def stepPlan(flow, actorId=2, **kwargs):
    kwargs.setdefault('enemy', (1.8, 0, .2))
    return bot_footwork.plan(flow, bot.navigationFor(flow), arena(flow, actorId, **kwargs), actorId)


def test_random_rhythm_is_local_to_each_actor_and_replayable(multi):
    original = json.loads(json.dumps(multi.state))
    expected = {rid: [bot_profiles.rhythm(multi, rid, 10, 1000) for _ in range(5)] for rid in (2, 3)}
    multi.state, multi.session = original, original['sessions']['1']
    actual = {rid: [] for rid in (2, 3)}
    for _ in range(5):
        for rid in (3, 2):
            actual[rid].append(bot_profiles.rhythm(multi, rid, 10, 1000))
    assert actual == expected and expected[2] != expected[3]
    assert app.validateState(multi.state)


def test_old_states_lazily_gain_rhythm_without_requiring_a_new_round(multi):
    for value in actors.bots(multi.session).values():
        value.pop('rhythmSeed', None)
    assert app.validateState(multi.state)
    delays = [bot_profiles.rhythm(multi, rid, 350, 900) for rid in range(2, 21)]
    assert all(350 <= delay <= 900 for delay in delays)
    assert len(set(delays)) >= 12


def test_opening_departures_do_not_collapse_into_two_shared_pulses(multi):
    bot.start(multi)
    delays = [multi.session['pending'][actors.timerName(bot.THINK_TIMER, rid)] - multi.now for rid in range(2, 21)]
    assert all(100 <= delay <= 450 for delay in delays)
    assert len({math.ceil(delay / bot.THINK_MS) for delay in delays}) >= 4


def test_identical_bots_press_with_independent_replan_times(multi):
    changes = {rid: [] for rid in range(2, 21)}
    deadlines, firstDeadlines = {}, []
    for tick in range(25):
        for rid in changes:
            choice = stepPlan(multi, rid)
            assert choice is not None and choice.mode == 'press'
            assert choice.goal == pytest.approx((.6, 0, 0))
            deadline = actors.data(multi, rid)['stepPlan']['until']
            if tick == 0:
                firstDeadlines.append(deadline - multi.now)
            if deadlines.get(rid) != deadline:
                changes[rid].append(tick)
                deadlines[rid] = deadline
        multi.now += 100
    assert len(set(firstDeadlines)) >= 10
    assert all(350 <= delay <= 900 for delay in firstDeadlines)
    assert len({tuple(times) for times in changes.values()}) >= 8


def test_small_target_motion_keeps_the_committed_step_and_does_not_reroll(multi):
    first = stepPlan(multi)
    previous = deepcopy(actors.data(multi, 2))
    for elapsed in (50, 50, 50):
        multi.now += elapsed
        choice = stepPlan(multi, enemy=(1.8, .05, .2))
        assert choice.goal == first.goal
        assert actors.data(multi, 2)['stepPlan'] == previous['stepPlan']
        assert actors.data(multi, 2)['rhythmSeed'] == previous['rhythmSeed']


def test_arrival_stops_footwork_but_does_not_delay_an_available_attack(multi):
    choice = stepPlan(multi)
    value = actors.data(multi, 2)
    position = (*choice.goal[:2], .2)
    heading = round(math.degrees(math.atan2(position[1], 1.8 - position[0])))
    multi.now += 50
    arena(multi, position=position, enemy=(1.8, 0, .2), heading=heading)
    bot.think(multi, 2)
    assert not value['moving'] and not value['path']
    assert 'stepPlan' not in value
    multi.now = value['reactAt']
    arena(multi, position=position, enemy=(1.8, 0, .2), heading=value['heading'])
    bot.think(multi, 2)
    assert value['attackCount'] == 1


@pytest.mark.parametrize('reason', ['pursuit', 'hurt', 'blocked'])
def test_a_committed_step_yields_immediately_to_changed_combat_conditions(multi, reason):
    first = stepPlan(multi, enemy=(3 if reason == 'blocked' else 1.8, 0, .2))
    value = actors.data(multi, 2)
    multi.now += 50
    pair = arena(multi, enemy=({'pursuit': 4, 'hurt': 1.2, 'blocked': 3}[reason], 0, .2))
    if reason == 'hurt':
        value['hurtUntil'] = multi.now + 400
    elif reason == 'blocked':
        multi.context['botWorld']['actors']['3']['position'] = [*first.goal[:2], .2]
    choice = bot_footwork.plan(multi, bot.navigationFor(multi), pair, 2)
    assert choice is not None and choice.goal != first.goal
    if reason != 'blocked':
        assert choice.mode == ('press' if reason == 'pursuit' else 'retreat')


def test_approach_readiness_varies_without_rerolling_each_decision(multi):
    delays = []
    for rid in range(2, 21):
        arena(multi, rid, enemy=(3, 0, .2))
        bot.think(multi, rid)
        value = actors.data(multi, rid)
        readyAt = value['reactAt']
        delays.append(readyAt - multi.now)
        bot.think(multi, rid)
        assert value['reactAt'] == readyAt
        assert 'pressAt' not in value
    assert all(143 <= delay <= 264 for delay in delays)
    assert len(set(delays)) >= 12


@pytest.mark.parametrize('profile', [name for name, settings in bot_profiles.PROFILES.items() if settings.attackDelayMs])
def test_attack_delay_varies_per_exchange_without_restarting_weapon_phases(multi, profile):
    waits = []
    for _ in range(8):
        arena(multi, enemy=(1.6, 0, .2))
        value = actors.data(multi, 2)
        value['profile'] = profile
        battle.resetAction(multi, 2)
        value['reactAt'] = None
        bot.think(multi, 2)
        waits.append(value['reactAt'] - multi.now)
        multi.now = value['reactAt']
        arena(multi, enemy=(1.6, 0, .2))
        value['profile'] = profile
        bot.think(multi, 2)
        assert value['action']['intent'] in battle.ATTACK_CHAINS
        assert multi.session['pending'][bot.ACTION_TIMER] - multi.now == battle.phaseMs(value['action']['intent'], 0)
        multi.now += 100
    assert len(set(waits)) >= 4
    baseline = bot_profiles.get(value).attackDelayMs
    assert all(round(baseline * .65) <= wait <= round(baseline * 1.2) for wait in waits)


@pytest.mark.parametrize('profile', [name for name, settings in bot_profiles.PROFILES.items() if settings.guardMs])
def test_guard_release_varies_per_exchange_but_keeps_its_committed_deadline(multi, profile):
    waits = []
    value = actors.data(multi, 2)
    for _ in range(6):
        battle.resetAction(multi, 2)
        arena(multi, enemy=(1.6, 0, .2))
        value['profile'] = profile
        battle.startAction(multi, 300550, released=False)
        bot.think(multi, 2)
        multi.now += bot_profiles.get(value).observationMs
        arena(multi, enemy=(1.6, 0, .2))
        value['profile'] = profile
        bot.think(multi, 2)
        assert value['action']['intent'] in battle.BLOCK_CHAINS
        deadline = multi.session['pending'][bot.RELEASE_TIMER]
        waits.append(deadline - multi.now)
        assert multi.session['pending'][bot.ACTION_TIMER] == multi.now
        app.handleTimer(multi, bot.ACTION_TIMER)
        multi.now += 50
        arena(multi, enemy=(1.6, 0, .2))
        value['profile'] = profile
        bot.think(multi, 2)
        assert multi.session['pending'][bot.RELEASE_TIMER] == deadline
    baseline = bot_profiles.get(value).guardMs
    assert len(set(waits)) >= 4
    assert all(round(baseline * .8) <= wait <= round(baseline * 1.2) for wait in waits)


def test_sensing_uses_slow_cadence_while_tactical_commitments_keep_individual_deadlines(multi):
    sensing, replanning, formations = [], [], []
    for rid in (2, 3, 4, 5, 6, 7, 8):
        arena(multi, rid, enemy=(3, 0, .2))
        world = actors.world(multi)
        value = actors.data(multi, rid)
        bot_roaming.sense(multi, bot.navigationFor(multi), world, rid)
        sensing.append(value['senseAt'] - multi.now)
        bot.follow(multi, bot.navigationFor(multi), (2, 0, 0), 'approach', rid)
        replanning.append(value['repathAt'] - multi.now)
        allyId = 3 if rid == 2 else 2
        world['actors'][str(allyId)]['position'] = [1.8, 0, .2]
        actors.data(multi, allyId)['opponentId'] = 1
        choice = bot_tactics.plan(multi, bot.navigationFor(multi), actors.pair(world, rid, 1), rid)
        assert choice is not None
        formations.append(value['tactic']['until'] - multi.now)
    assert set(sensing) == {bot.STRATEGY_MS}
    assert len(set(replanning)) >= 4 and all(350 <= delay <= 650 for delay in replanning)
    assert len(set(formations)) >= 4 and all(800 <= delay <= 1600 for delay in formations)


@pytest.mark.parametrize('reason', ['stop', 'stale', 'lost', 'respawn', 'unstuck'])
def test_rhythm_survives_reload_but_transient_steps_clear_on_disengagement(multi, reason):
    stepPlan(multi)
    value = actors.data(multi, 2)
    state = json.loads(json.dumps(multi.state))
    assert app.validateState(state) and app.migrateState(app.STATE_VERSION, state) == state
    seed = value['rhythmSeed']
    if reason == 'stop':
        bot.stop(multi, 2)
    elif reason == 'stale':
        multi.now += 1000
        bot.think(multi, 2)
    elif reason == 'lost':
        arena(multi, enemy=(40, 0, .2))
        bot.think(multi, 2)
    elif reason == 'respawn':
        bot_life.clearPerception(value)
    else:
        bot_unstuck.plan(multi, bot.navigationFor(multi), 2, heading=0)
    assert 'stepPlan' not in value
    assert value['rhythmSeed'] == seed or reason == 'lost'


@pytest.mark.parametrize('field,invalid', [('until', -1), ('targetId', 0), ('goal', [math.nan, 0, 0]),
                                        ('targetPosition', [0, 0]), ('mode', 'invalid'), ('gap', 0), ('side', 0)])
def test_invalid_committed_step_is_rejected(multi, field, invalid):
    stepPlan(multi)
    actors.data(multi, 2)['stepPlan'][field] = invalid
    assert not app.validateState(multi.state)


@pytest.mark.parametrize('invalid', [-1, True, 2 ** 32, '1'])
def test_invalid_rhythm_seed_is_rejected(multi, invalid):
    actors.data(multi, 2)['rhythmSeed'] = invalid
    assert not app.validateState(multi.state)
