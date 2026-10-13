from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, contracts as wire, scene
from test_round_timeout import game, finish, clear, settle


def start(duration):
    state = app.createState({})
    state['sessions']['1'] = {'role': 'instance', 'hydration': [], 'pending': {}, 'camp': 1}
    flow = app.Flow({'connection': 1}, state, {'nowMs': 1000, 'gameDurationMs': duration})
    scene.begin(flow)
    flow.session.update(battleEntered=True, heroChosen=True, loaded=True)
    scene.timer(flow, 'instance-round-info')
    flow.later('round-game', wire.START_MS)
    flow.now += wire.START_MS
    app.handleTimer(flow, 'round-game')
    return flow


@pytest.mark.parametrize('duration', [3000, 60000, 300000, 1800000])
def test_announced_duration_and_deadline_share_the_per_round_clock(duration):
    flow = start(duration)
    assert flow.session['battleClock'] == {'durationMs': duration, 'startedAt': flow.now}
    info = next(p['body'] for p in flow.result['send'] if p['reason'] == 'instance-round-info')
    assert info == wire.roundInfo(1000, duration)
    packet = next(p['body'] for p in flow.result['send'] if p['reason'] == 'instance-round-state-game')
    assert packet == wire.roundState(flow.now, 4, duration)
    assert flow.session['pending']['round-end'] == flow.now + duration
    assert next(t['delayMs'] for t in flow.result['timers'] if t['id'] == '1:round-end') == duration


def test_refresh_reload_and_new_config_do_not_restart_the_active_clock():
    flow = start(60000)
    clock = deepcopy(flow.session['battleClock'])
    deadline = flow.session['pending']['round-end']
    flow.now += 3000
    flow.context['gameDurationMs'] = 90000
    clear(flow)
    scene.message(flow, 10, 0x6A, b'\0j')
    states = [p['body'] for p in flow.result['send'] if p['reason'] == 'instance-round-state-refresh']
    assert states == [wire.roundState(flow.now, 4, 60000, startedAt=clock['startedAt'])]
    assert flow.session['pending']['round-end'] == deadline
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(flow.state)))
    assert restored['sessions']['1']['battleClock'] == clock
    scene.begin(flow)
    assert flow.session['battleClock'] == {'durationMs': 90000, 'startedAt': None}


@pytest.mark.parametrize('duration', [0, -1, True, 1.5, 86400001])
def test_invalid_round_duration_is_rejected_before_start(duration):
    with pytest.raises(ValueError, match='duration'):
        start(duration)


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('blue,red,result', [(4, 3, 1), (2, 5, 2), (0, 0, 3), (4, 4, 3)])
@pytest.mark.parametrize('game', [True], indirect=True)
def test_settlement_freezes_all_members_with_camp_and_personal_results(game, camp, blue, red, result):
    # Keep the roster's team assignment intact and choose the scoring actor by team.
    game.session['camp'] = camp
    from Business.scripts import roster
    roster.assemble(game)
    for team, kills in ((1, blue), (2, red)):
        rid = next(rid for rid in actors.ids(game.session) if actors.data(game, rid)['camp'] == team)
        actors.data(game, rid).update(kills=kills, battlePoints=kills * 100, deaths=2)
    finish(game)
    snapshot = game.state['settlement']
    assert snapshot['reason'] == 'timeout' and snapshot['result'] == result
    assert snapshot['campKills'] == {'1': blue, '2': red}
    assert len(snapshot['members']) == 20
    for member in snapshot['members']:
        expected = 3 if result == 3 else 1 if member['camp'] == result else 2
        assert member['result'] == expected
    roundBody = next(p['body'] for p in game.result['send'] if p['reason'] == 'instance-round-result')
    assert roundBody == struct.pack('>HQQii', 0x68, game.now, 1, result, 1)
    settle(game)
    finishBody = next(p['body'] for p in game.result['send'] if p['reason'] == 'instance-finish-game')
    assert struct.unpack_from('>i', finishBody, 18)[0] == result
    settleBody = next(p['body'] for p in game.result['send'] if p['reason'] == 'instance-settlement')
    assert struct.unpack_from('>i', settleBody, len(settleBody) - 4)[0] == (
        3 if result == 3 else 1 if result == camp else 2)
    assert app.validateState(game.state)


@pytest.mark.parametrize('game', [True], indirect=True)
def test_ranking_uses_points_kills_deaths_then_rid_and_includes_dead_members(game):
    for rid, points, kills, deaths in [(2, 400, 2, 4), (3, 300, 3, 1), (4, 300, 2, 1),
                                      (5, 300, 2, 2), (6, 300, 2, 2)]:
        actors.data(game, rid).update(battlePoints=points, kills=kills, deaths=deaths)
    from test_bot_respawn import kill
    kill(game, 6)
    actors.data(game, 6).update(battlePoints=300, kills=2, deaths=2)
    finish(game)
    members = [m for m in game.state['settlement']['members'] if m['camp'] == 2]
    assert [m['rid'] for m in members[:5]] == [2, 3, 4, 5, 6]
    assert [m['rank'] for m in members] == list(range(1, 11))
    scores = [p for p in game.result['send'] if p['reason'] == 'settlement-score']
    assert len(scores) == 20
    for packet in scores:
        body = packet['body']
        selector, now, rid, messageId, count = struct.unpack_from('>HQQii', body)
        assert (selector, messageId, count) == (1, 0, 6)
        fields = {key: (old, new) for key, old, new in struct.iter_unpack('>iii', body[26:])}
        member = next(m for m in game.state['settlement']['members'] if m['rid'] == rid)
        assert fields == {key: (value, value) for key, value in [
            (2, member['kills']), (44, member['kills']), (3, member['deaths']),
            (1, member['points']), (9, member['result']), (51, member['rank'])]}


@pytest.mark.parametrize('elapsed,reason', [(-1, 'quit'), (0, 'timeout'), (100, 'timeout')])
def test_leave_at_deadline_has_one_result_and_duplicate_leave_does_not_delay_ack(elapsed, reason):
    flow = start(60000)
    flow.now = flow.session['pending']['round-end'] + elapsed
    clear(flow)
    scene.leave(flow, b'\0\2\0')
    snapshot = deepcopy(flow.state['settlement'])
    assert snapshot['reason'] == reason
    assert snapshot['result'] == (0 if reason == 'quit' else 3)
    ack = flow.session['pending']['logout']
    flow.now += 100
    clear(flow)
    scene.leave(flow, b'\0\2\0')
    assert not flow.result['send'] and not flow.result['timers']
    assert flow.state['settlement'] == snapshot and flow.session['pending']['logout'] == ack
    flow.now = ack
    app.handleTimer(flow, 'logout')
    assert flow.session['phase'] == 'settlement'


def test_settlement_survives_instance_close_until_return_to_lobby():
    flow = start(3000)
    finish(flow)
    settle(flow)
    flow.state['sessions']['2'] = {'role': 'logic', 'hydration': [], 'pending': {}}
    result = app.handleEvent({'type': 'closed', 'connection': 1}, flow.state, {'nowMs': flow.now})
    state = result['state']
    assert state['settlement'] == flow.state['settlement'] and state['phase'] == 'settlement'
    logic = app.Flow({'connection': 2}, state, {'nowMs': flow.now + 10})
    scene.message(logic, 55, 1, struct.pack('>HQi', 1, 1, 3))
    assert 'settlement' not in state and state['phase'] == 'lobby-data-sent'


def test_old_state_migration_retains_deadline_without_fabricating_results():
    flow = start(wire.GAME_MS)
    deadline = flow.session['pending']['round-end']
    flow.session.pop('battleClock', None)
    restored = app.migrateState(12, flow.state)
    assert restored['sessions']['1']['battleClock'] == {
        'durationMs': 1200000, 'startedAt': deadline - 1200000}
    assert restored['sessions']['1']['pending']['round-end'] == deadline
    assert 'settlement' not in restored


@pytest.mark.parametrize('event', [
    {'type': 'world'},
    {'type': 'timer', 'name': 'time-sync'},
    {'type': 'message', 'command': 4, 'body': struct.pack('>HIib', 1, 0, 300550, 0)},
])
def test_expiry_precedes_a_late_world_timer_or_gameplay_event(event):
    flow = start(3000)
    deadline = flow.session['pending']['round-end']
    flow.later('time-sync', 100)
    result = app.handleEvent(dict(event, connection=1), flow.state,
                             {'nowMs': deadline, 'connections': [1]})
    state = result['state']
    assert state['phase'] == 'settlement' and state['settlement']['reason'] == 'timeout'
    assert state['sessions']['1']['pending'] == {'round-finish': deadline + 5000}
    assert [p['reason'] for p in result['send']][-2:] == [
        'instance-round-result', 'instance-round-state-end']
    assert not any(p['reason'] in ('instance-settlement', 'instance-finish-game') for p in result['send'])


def test_frozen_settlement_survives_reload_and_logic_hydration():
    flow = start(3000)
    finish(flow)
    saved = deepcopy(flow.state['settlement'])
    flow.session['kills'] = 20
    assert flow.state['settlement'] == saved
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(flow.state)))
    assert restored['settlement'] == saved
    restored['sessions']['2'] = {'role': 'logic', 'hydration': [], 'pending': {}}
    logic = app.Flow({'connection': 2}, restored, {'nowMs': flow.now})
    logic.phase('lobby-data-sent')
    assert restored['phase'] == 'settlement'
    scene.message(logic, 55, 1, struct.pack('>HQi', 1, 1, 1))
    assert restored['settlement'] == saved and restored['phase'] == 'settlement'
    scene.begin(flow)
    assert 'settlement' not in flow.state and 'roundEnded' not in flow.session


def test_old_finished_state_migration_does_not_replay_or_invent_a_settlement():
    flow = start(wire.GAME_MS)
    finish(flow)
    settle(flow)
    flow.state.pop('settlement')
    flow.session.pop('battleClock')
    restored = app.migrateState(12, flow.state)
    assert 'settlement' not in restored and not restored['sessions']['1']['pending']
    result = app.handleEvent({'type': 'world', 'connection': 1}, restored,
                             {'nowMs': flow.now, 'connections': [1]})
    assert not result['send'] and not result['timers']


@pytest.mark.parametrize('key,value', [
    ('result', True), ('result', 4), ('members', []), ('campKills', {'1': -1, '2': 0}),
    ('durationMs', 0), ('startedAt', -1), ('reason', 'unknown'),
])
def test_invalid_settlement_state_is_rejected(key, value):
    flow = start(3000)
    finish(flow)
    flow.state['settlement'][key] = value
    assert not app.validateState(flow.state)
