"""The object bridge shares business semantics with the serialized entrypoint."""
from copy import deepcopy
from types import SimpleNamespace

import pytest

from Business.runtime import host_runtime as host
from Business.scripts import app
from test_battle_actions import actionBody
from test_bot_roster import multi, sample


def runtimeFor(module=app):
    runtime = object.__new__(host.Runtime)
    runtime.active = SimpleNamespace(module=module)
    return runtime


@pytest.mark.parametrize('event', [
    dict(type='message', connection=1, command=4, body=actionBody(300610)),
    dict(type='timer', connection=1, name='absent-timer'),
    dict(type='timer', connection=1, name='actors-decision'),
    dict(type='timer', connection=1, name='actors-pulse'),
    dict(type='world', connection=0),
    dict(type='closed', connection=1),
])
def test_native_and_serialized_dispatch_are_equivalent_and_leave_inputs_unchanged(multi, event):
    world = sample(multi)
    world['contactJournal'] = dict(throughAt=multi.now, frames=0, dropped=0, batchId=1, events=[])
    context = dict(multi.context, connections=[1])
    state = host.encode(multi.state)
    before = deepcopy(context)
    runtime = runtimeFor()
    expected = runtime.dispatch(host.encode(event), state, host.encode(context))
    actual = runtime.dispatchNative(event, state, context)
    assert actual[:4] == expected
    assert actual[4] == runtime.phase(actual[0])
    assert set(actual[5]) == {'decodeMs', 'handlerMs', 'validationMs', 'encodeMs'}
    assert all(value >= 0 for value in actual[5].values())
    assert context == before
    assert host.decode(state) == multi.state


def test_native_dispatch_reuses_output_encoding_and_only_decodes_state(monkeypatch):
    state = dict(phase='ready')
    timer = dict(type='timer', connection=1, name='next')
    result = dict(state=state, send=[], timers=[dict(id='1:next', delayMs=0, event=timer)], logs=[])
    runtime = runtimeFor(SimpleNamespace(handleEvent=lambda *_: result, validateState=lambda _: True))
    encoded = host.encode(state)
    counts = {'encode': [], 'decode': []}
    originalEncode, originalDecode = host.encode, host.decode

    def encode(value):
        counts['encode'].append(value)
        return originalEncode(value)

    def decode(value):
        counts['decode'].append(value)
        return originalDecode(value)

    monkeypatch.setattr(host, 'encode', encode)
    monkeypatch.setattr(host, 'decode', decode)
    actual = runtime.dispatchNative(dict(type='timer', connection=1), encoded,
                                    dict(nowMs=1, connections=[1]))
    assert counts == {'encode': [state, timer], 'decode': [encoded]}
    assert actual[0] == encoded and actual[4] == 'ready'


def test_native_timer_snapshot_is_decoded_once(monkeypatch):
    event = dict(type='timer', connection=1, name='absent')
    context = dict(nowMs=1, connections=[])
    state = host.encode(app.createState(context))
    serialized = host.encode(event)
    values = []
    original = host.decode

    def decode(value):
        values.append(value)
        return original(value)

    monkeypatch.setattr(host, 'decode', decode)
    runtimeFor().dispatchNative(serialized, state, context)
    assert values == [serialized, state]


@pytest.mark.parametrize('failure', ['callback', 'state', 'validation', 'timer', 'send'])
def test_native_rejection_does_not_mutate_input_snapshot(failure):
    state = host.encode(dict(phase='ready', values=[]))

    def handle(event, value, context):
        value['values'].append(1)
        if failure == 'callback':
            raise ValueError('callback failed')
        result = dict(state=value, send=[], timers=[], logs=[])
        if failure == 'state':
            value['bad'] = object()
        elif failure == 'timer':
            result['timers'] = [dict(id='1:bad', delayMs=0, event=dict(type='timer', connection=2))]
        elif failure == 'send':
            result['send'] = [dict(connection=2, command=4, body=b'', reason='bad')]
        return result

    runtime = runtimeFor(SimpleNamespace(handleEvent=handle, validateState=lambda _: failure != 'validation'))
    with pytest.raises((ValueError, TypeError)):
        runtime.dispatchNative({}, state, dict(connections=[1]))
    assert host.decode(state) == dict(phase='ready', values=[])


def test_native_phase_preserves_legacy_truncation():
    state = dict(phase='x' * 200)
    runtime = runtimeFor(SimpleNamespace(
        handleEvent=lambda *_: dict(state=state, send=[], timers=[], logs=[]), validateState=lambda _: True))
    actual = runtime.dispatchNative({}, host.encode(state), dict(connections=[]))
    assert actual[4] == 'x' * 128
