from copy import deepcopy
from types import SimpleNamespace

import pytest

from Business.runtime import host_runtime as host


def transition(match_id=7, delay=10):
    event = dict(type='timer', connection=0, scope='match', matchId=match_id, name='round-end')
    return dict(state=dict(matchId=match_id), send=[], logs=[], timers=[
        dict(id=f'match:{match_id}:round-end', delayMs=delay, event=event)])


def test_match_timer_does_not_require_any_live_player_connection():
    value = transition()
    before = deepcopy(value)
    host.validateTransition(value, dict(connections=[]))
    assert value == before


@pytest.mark.parametrize('mutation', ['connection', 'missing_scope', 'unknown_scope', 'match_id',
                                     'bool_id', 'namespace', 'missing_name', 'old_match'])
def test_match_timer_requires_explicit_scope_and_current_match(mutation):
    value = transition()
    timer, event = value['timers'][0], value['timers'][0]['event']
    if mutation == 'connection':
        event['connection'] = 1
    elif mutation == 'missing_scope':
        del event['scope']
    elif mutation == 'unknown_scope':
        event['scope'] = 'node'
    elif mutation == 'match_id':
        event['matchId'] = 0
    elif mutation == 'bool_id':
        event['matchId'] = True
    elif mutation == 'namespace':
        timer['id'] = '1:round-end'
    elif mutation == 'missing_name':
        del event['name']
    elif mutation == 'old_match':
        value['state']['matchId'] = 8
    with pytest.raises(ValueError):
        host.validateTransition(value, dict(connections=[1]))


def test_new_match_can_cancel_previous_match_timer():
    value = transition(delay=-1)
    value['state']['matchId'] = 8
    host.validateTransition(value, dict(connections=[]))


def test_connection_timer_keeps_existing_lifetime_validation():
    value = transition()
    value['timers'][0] = dict(id='1:login', delayMs=10,
                              event=dict(type='timer', connection=1, name='login'))
    host.validateTransition(value, dict(connections=[1]))
    with pytest.raises(ValueError, match='non-live'):
        host.validateTransition(value, dict(connections=[]))


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('event_match,expected_calls', [(7, 1), (6, 0)])
def test_stale_queued_match_timers_never_reach_business_handler(native, event_match, expected_calls):
    calls = []

    def handler(event, state, context):
        calls.append(event)
        return dict(state=state, send=[], timers=[], logs=[])

    runtime = object.__new__(host.Runtime)
    runtime.active = SimpleNamespace(module=SimpleNamespace(handleEvent=handler, validateState=lambda _: True))
    state = host.encode(dict(matchId=7))
    event = transition(event_match)['timers'][0]['event']
    context = dict(nowMs=10, connections=[])
    result = runtime.dispatchNative(event, state, context) if native else runtime.dispatch(
        host.encode(event), state, host.encode(context))
    assert len(calls) == expected_calls
    assert result[:4] == (state, [], [], [])
