"""Native non-combat mode and casual-state notifications."""
import struct

NO_FIGHT_STATE_NOTIFY = 30
CASUAL_STATE_NOTIFY = 29


def encode_no_fight(*, actor_id, enabled, server_time_ms):
    if type(enabled) is not bool:
        raise ValueError('non-combat mode must be boolean')
    return _encode('>HQBQ', NO_FIGHT_STATE_NOTIFY, actor_id, int(enabled), server_time_ms)


def encode_state(*, actor_id, state, server_time_ms):
    return _encode('>HQiQ', CASUAL_STATE_NOTIFY, actor_id, state, server_time_ms)


def _encode(fmt, selector, actor_id, value, server_time_ms):
    if (type(actor_id) is not int or actor_id <= 0 or type(server_time_ms) is not int
            or server_time_ms < 0 or type(value) is not int):
        raise ValueError('invalid non-combat notification')
    try:
        return struct.pack(fmt, selector, actor_id, value, server_time_ms)
    except struct.error as error:
        raise ValueError('non-combat field outside wire range') from error
