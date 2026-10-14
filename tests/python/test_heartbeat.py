import struct

import pytest

from Business.scripts import app, contracts


@pytest.mark.parametrize("role", ["login", "logic", "instance"])
@pytest.mark.parametrize("sequence,client_time_ms,delay_ms", [
    (1, 0, 0), (2, 123456789, 37), (0x7FFFFFFF, 0x123456789ABCDEF0, 0xFFFF),
])
def test_heartbeat_echo_preserves_client_rtt_origin(role, sequence, client_time_ms, delay_ms):
    state = app.createState({})
    state["sessions"]["7"] = {"role": role, "pending": {}, "authenticated": True}
    request = struct.pack(">HiQH", 1, sequence, client_time_ms, delay_ms)
    event = {"type": "message", "connection": 7, "command": 0x1D, "body": request}

    for now in (1000, 9000):
        result = app.handleEvent(event, state, {"nowMs": now})
        assert result["send"] == [{
            "connection": 7, "command": 0x1D,
            "body": struct.pack(">HiQ", 2, sequence, client_time_ms),
            "reason": "heart-beat-response",
        }]
        assert result["timers"] == []


@pytest.mark.parametrize("size", [0, 2, 14, 15, 17])
def test_heartbeat_rejects_incomplete_or_extended_requests(size):
    with pytest.raises(ValueError, match="heartbeat"):
        contracts.heartbeat(bytes(size))
