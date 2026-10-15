import struct
import uuid

import pytest

from Business.scripts.codec import motion_wire
from Business.runtime.host_runtime import validateTransition


MATCH = '00000000-0000-4000-8000-000000000001'


def envelope():
    return dict(protocolVersion=1, requestId=str(2**64 - 1), command=dict(type='poseInput', payload=dict(
        matchId=MATCH, inputSeq=1, pose=dict(position=[1., 2., .5], heading=45, forwardBack=-1,
            leftRight=0, running=False, motion=dict(tick=100, actionTick=90, actionId=54,
                velocity=[1., 2., 3.], airborne=True, jumping=True)))),
        timeline=dict(epoch=MATCH, combatSeq=99, sampledMs=123, gatewayMs=125, source='nativePose'))


def test_fixed_input_preserves_jump_timestamps_and_u64_sequence():
    value = envelope()
    encoded = motion_wire.encode_input(value)
    assert len(encoded) == 243
    assert motion_wire.decode_input(encoded) == value
    validateTransition(dict(state={}, send=[], timers=[], logs=[], gateway=[dict(connection=1,
        op='snapshot', requestId=value['requestId'], request=encoded)]),
        dict(connections=[1], roomGateway={}))


def test_binary_motion_rejects_nonfinite_flags_truncation_and_correlation():
    encoded = motion_wire.encode_input(envelope())
    raw = bytes.fromhex(encoded[5:])
    for size in range(len(raw)):
        with pytest.raises(ValueError):
            motion_wire.decode_input(raw[:size])
    for offset, data in ((48, b'\x80'), (32, struct.pack('<f', float('nan'))), (4, bytes(8))):
        bad = bytearray(raw)
        bad[offset:offset + len(data)] = data
        with pytest.raises(ValueError):
            motion_wire.decode_input(bad)
    with pytest.raises(ValueError, match='correlation'):
        validateTransition(dict(state={}, send=[], timers=[], logs=[], gateway=[dict(connection=1,
            op='snapshot', requestId='1', request=encoded)]), dict(connections=[1], roomGateway={}))


def test_missing_motion_and_timeline_have_canonical_zero_fields():
    value = envelope()
    del value['timeline']
    del value['command']['payload']['pose']['motion']
    assert motion_wire.decode_input(motion_wire.encode_input(value)) == value


def test_binary_snapshot_enters_existing_motion_playback(monkeypatch, spawnPoints):
    from test_online_actions import running
    client = running(monkeypatch, spawnPoints, peer=True)
    # Native rooms use UUIDs, while this older scene fixture uses a symbolic room id.
    client.state['sessions']['1']['customLobby']['room']['roomId'] = MATCH
    frame = motion_wire.OUTPUT.pack(b'T7S1', uuid.UUID(MATCH).bytes, uuid.UUID(MATCH).bytes,
        2, 1, 1, 100, 1., 2., .5, 45, -1, 0, 14, 100, 90, 54, 1., 2., 3.)
    result = client.event('gateway', body=frame)
    assert motion_wire.OUTPUT.size == len(frame) == 107
    assert result['remoteMotion'][0]['airborne']
    assert result['remoteMotion'][0]['jumping']
