from copy import deepcopy
import math

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, bot, bot_decision, bot_footwork, bot_roaming, bot_tactics
from Business.scripts.navigation import NavMesh
from test_bot_roster import multi, sample
from test_bot_rhythm import arena


def pulse(flow):
    flow.later(actors.PULSE_TIMER, 0)
    app.handleTimer(flow, actors.PULSE_TIMER)


@pytest.mark.parametrize('actorId', [2, 12, 20])
def test_combat_pulse_uses_known_target_without_running_strategy(multi, monkeypatch, actorId):
    arena(multi, actorId, enemy=(1.5, 0, .2))
    value = actors.data(multi, actorId)
    value['reactAt'] = multi.now
    strategy = actors.timerName(bot.THINK_TIMER, actorId)
    multi.later(strategy, 500)

    def unexpected(*args, **kwargs):
        pytest.fail('100 ms combat must not run target scans or movement planning')

    for module, name in ((bot, 'world'), (bot, 'follow'), (bot_roaming, 'sense'),
                         (bot_roaming, 'goal'), (bot_tactics, 'plan'), (bot_tactics, 'assist'),
                         (bot_footwork, 'plan')):
        monkeypatch.setattr(module, name, unexpected)
    nav = bot.navigationFor(multi)
    findPath = nav.findPath
    queries = []

    def meleeCheck(start, target, *args, **kwargs):
        assert math.dist(start, target) <= bot.MELEE_RANGE
        queries.append((start, target))
        return findPath(start, target, *args, **kwargs)

    monkeypatch.setattr(nav, 'findPath', meleeCheck)
    pulse(multi)
    assert value['attackCount'] == 1
    assert len(queries) == 1
    assert multi.session['pending'][strategy] == multi.now + 500
    assert multi.session['pending'][actors.PULSE_TIMER] == multi.now + 100
    assert multi.session['pending'][battle.actionTimer(actorId)] == multi.now + battle.phaseMs(value['lastAttack'], 0)
    assert not value['path']


def test_combat_observes_and_parries_between_slow_planning_ticks(multi):
    arena(multi, enemy=(1.5, 0, .2))
    value = actors.data(multi, 2)
    value.update(profile='defensive', reactAt=multi.now + 400)
    multi.later(bot.THINK_TIMER, 500)
    battle.startAction(multi, 300550, released=True)
    pulse(multi)
    assert value['status'] == 'observe-threat'
    multi.now += 100
    sample(multi)
    # Keep the close-range snapshot while advancing only the combat clock.
    multi.context['botWorld']['actors']['1'].update(position=[1.5, 0, .2], heading=180)
    app.handleTimer(multi, actors.PULSE_TIMER)
    assert value['action']['intent'] in battle.BLOCK_CHAINS
    assert multi.session['pending'][bot.THINK_TIMER] == multi.now + 400


def test_fast_combat_is_batched_while_each_strategy_runs_every_500_ms(multi, monkeypatch):
    combatCalls, strategyCalls = [], []
    monkeypatch.setattr(bot, 'combatThink', lambda flow, rid: combatCalls.append((flow.now, rid)))

    def strategy(flow, actorId, *, combat):
        assert not combat
        actors.data(flow, actorId).update(initialized=True, heightOffset=.2)
        strategyCalls.append((flow.now, actorId))

    monkeypatch.setattr(bot, 'think', strategy)
    start = multi.now
    for rid in range(2, 21):
        multi.later(actors.timerName(bot.THINK_TIMER, rid), 500)
    multi.later(actors.PULSE_TIMER, 100)
    for elapsed in range(100, 1001, 100):
        multi.now = start + elapsed
        app.handleTimer(multi, actors.PULSE_TIMER)
        while multi.session['pending'].get(bot.DECISION_TIMER, math.inf) <= multi.now:
            before = len(strategyCalls)
            app.handleTimer(multi, bot.DECISION_TIMER)
            assert len(strategyCalls) == before + 1
    assert len(combatCalls) == 190
    assert len(strategyCalls) == 38
    for rid in range(2, 21):
        assert [time - start for time, actor in strategyCalls if actor == rid] == [500, 1000]
        assert multi.session['pending'][actors.timerName(bot.THINK_TIMER, rid)] == start + 1500
    assert {change['event']['name'] for change in multi.result['timers']} == {actors.PULSE_TIMER, bot.DECISION_TIMER}


def test_late_combat_pulse_does_not_replay_missed_ticks(multi, monkeypatch):
    calls = []
    monkeypatch.setattr(bot, 'combatThink', lambda flow, rid: calls.append(rid))
    multi.later(actors.PULSE_TIMER, 100)
    multi.now += 2000
    app.handleTimer(multi, actors.PULSE_TIMER)
    assert calls == list(range(2, 21))
    assert multi.session['pending'][actors.PULSE_TIMER] == multi.now + 100


@pytest.mark.parametrize('fault', ['dead', 'missing', 'camp', 'stale', 'epoch'])
def test_fast_combat_rejects_invalid_target_without_scanning(multi, monkeypatch, fault):
    arena(multi, enemy=(1.5, 0, .2))
    value = actors.data(multi, 2)
    value.update(reactAt=multi.now, path=[[.5, 0, 0]], step=0, moving=True)
    multi.later(bot.MOVE_TIMER, 50)
    multi.later(bot.THINK_TIMER, 500)
    world = multi.context['botWorld']
    if fault == 'dead':
        multi.session['life'] = dict(state='dead')
    elif fault == 'missing':
        world['actors']['1']['ready'] = False
    elif fault == 'camp':
        multi.session['camp'] = value['camp']
    elif fault == 'stale':
        world['sampledAt'] -= 251
    else:
        world['instanceStartedAt'] -= 1
    monkeypatch.setattr(bot_roaming, 'sense', lambda *args: pytest.fail('invalidating is not scanning'))
    pulse(multi)
    assert value['attackCount'] == 0
    assert value['reactAt'] is None and not value['moving'] and not value['path']
    if fault in ('dead', 'missing', 'camp'):
        assert value.get('opponentId') is None
        assert multi.session['pending'][bot.THINK_TIMER] == multi.now
        assert multi.session['pending'][bot.DECISION_TIMER] == multi.now


@pytest.mark.parametrize('reason', ['no-target', 'distant', 'not-ready', 'terrain-unknown', 'terrain-hit'])
def test_fast_wait_preserves_strategy_and_does_not_query_navigation(multi, monkeypatch, reason):
    arena(multi, enemy=(3 if reason == 'distant' else 1.5, 0, .2))
    value = actors.data(multi, 2)
    value.update(reactAt=multi.now + (400 if reason == 'not-ready' else 0),
                 path=[[.5, 0, 0]], step=0, moving=True)
    if reason == 'no-target':
        value.pop('opponentId')
    elif reason.startswith('terrain'):
        multi.context['botWorld']['actors']['2']['terrain']['target'].update(
            ready=reason != 'terrain-unknown', hit=reason == 'terrain-hit')
    multi.later(bot.THINK_TIMER, 500)
    monkeypatch.setattr(bot, 'navigationFor', lambda *args: pytest.fail('waiting needs no path query'))
    pulse(multi)
    assert value['attackCount'] == 0
    assert value['path'] == [[.5, 0, 0]] and value['moving']
    assert multi.session['pending'][bot.THINK_TIMER] == multi.now + 500


def test_fast_attack_still_requires_a_reachable_melee_route(multi, monkeypatch):
    arena(multi, enemy=(1.5, 0, .2))
    value = actors.data(multi, 2)
    value['reactAt'] = multi.now
    nav = NavMesh.fromPolygons([
        [(-2, -2, 0), (.5, -2, 0), (.5, 2, 0), (-2, 2, 0)],
        [(1, -2, 0), (3, -2, 0), (3, 2, 0), (1, 2, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda *args: nav)
    pulse(multi)
    assert value['attackCount'] == 0
    assert bot.ACTION_TIMER not in multi.session['pending']


def test_slow_strategy_keeps_detouring_when_a_nearby_enemy_is_behind_a_wall(multi, monkeypatch):
    arena(multi, position=(-.5, 0, .2), enemy=(1, 0, .2))
    value = actors.data(multi, 2)
    value['profile'] = 'berserker'
    multi.context['botWorld']['actors']['2']['terrain']['target']['hit'] = True
    nav = NavMesh.fromPolygons([
        [(-3, -3, 0), (0, -3, 0), (0, 2, 0), (-3, 2, 0)],
        [(-3, 2, 0), (0, 2, 0), (0, 4, 0), (-3, 4, 0)],
        [(0, 2, 0), (.5, 2, 0), (.5, 4, 0), (0, 4, 0)],
        [(.5, 2, 0), (3, 2, 0), (3, 4, 0), (.5, 4, 0)],
        [(.5, -3, 0), (3, -3, 0), (3, 2, 0), (.5, 2, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda *args: nav)
    bot.think(multi, 2, combat=False)
    assert value['opponentId'] == 1
    assert value['path'] and max(point[1] for point in value['path']) >= 2
    route = deepcopy(value['path'])
    pulse(multi)
    assert value['path'] == route and value['attackCount'] == 0


def test_locked_combat_preserves_weapon_deadline_and_skips_navigation(multi, monkeypatch):
    arena(multi, enemy=(1.5, 0, .2))
    battle.startAction(multi, 300550, released=True, actorId=2)
    before = deepcopy(actors.data(multi, 2)['action'])
    deadline = multi.session['pending'][bot.ACTION_TIMER]
    monkeypatch.setattr(bot, 'navigationFor', lambda *args: pytest.fail('locked weapon needs no path query'))
    for _ in range(3):
        multi.now += 100
        multi.context['botWorld']['sampledAt'] = multi.now
        pulse(multi)
        assert actors.data(multi, 2)['action'] == before
        assert multi.session['pending'][bot.ACTION_TIMER] == deadline


def test_full_roster_combat_stays_within_one_transition_and_reuses_state_schema(multi, monkeypatch):
    placements = {1: ((0, 0, .2), 180)}
    for rid in range(2, 21):
        value = actors.data(multi, rid)
        position, heading, target = ((-1.5, 0, .2), 0, 1) if value['camp'] == 2 else ((0, 0, .2), 180, 2)
        placements[rid] = position, heading
        value.update(position=list(position), heading=heading, initialized=True, heightOffset=.2,
                     opponentId=target, reactAt=multi.now)
    sample(multi, placements)
    monkeypatch.setattr(bot_decision, 'choose', lambda *args: bot_decision.Decision('attack', 'attack', 300560, 1))
    multi.result = dict(state=multi.state, send=[], timers=[], logs=[])
    pulse(multi)
    assert all(actors.data(multi, rid)['attackCount'] == 1 for rid in range(2, 21))
    assert all(len(multi.result[key]) <= 256 for key in ('send', 'timers', 'logs'))
    assert app.validateState(multi.state)
    assert host_runtime.decode(host_runtime.encode(multi.state)) == multi.state
