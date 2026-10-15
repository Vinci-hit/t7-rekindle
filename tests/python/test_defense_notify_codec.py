import struct

import pytest

from Business.scripts.codec import battle_flow


# DEFENSE_NOTIFY: two signed 64-bit MIDs, int8 defence type, two int32
# weapon IDs and uint64 server time; the selector is a separate uint16.
WEAPON_DEFENSE = bytes.fromhex(
    '0028'
    '0000000000000001'
    '0000000000000002'
    '02'
    '000000c8'
    '0000012c'
    '0102030405060708'
)
FIELDS = dict(attacker_mid=1, defender_mid=2, attacker_weapon_id=200,
              target_weapon_id=300, server_time_ms=0x0102030405060708)


def test_weapon_defense_matches_native_sound_notification_wire_layout():
    body = battle_flow.encode_battle_defense_notify(**FIELDS)
    assert body == WEAPON_DEFENSE
    assert len(body) == 35
    assert struct.unpack_from('>biiQ', body, 18) == (2, 200, 300, FIELDS['server_time_ms'])


def test_weapon_defense_preserves_full_width_ids_and_time():
    body = battle_flow.encode_battle_defense_notify(
        **dict(FIELDS, attacker_mid=0x7fffffffffffffff, defender_mid=0x123456789,
               server_time_ms=0xffffffffffffffff))
    assert struct.unpack('>HqqbiiQ', body) == (
        40, 0x7fffffffffffffff, 0x123456789, 2, 200, 300, 0xffffffffffffffff)


@pytest.mark.parametrize('field,value', [
    ('attacker_mid', -(1 << 63) - 1), ('attacker_mid', 1 << 63),
    ('defender_mid', -(1 << 63) - 1), ('defender_mid', 1 << 63),
    ('attacker_weapon_id', -(1 << 31) - 1), ('attacker_weapon_id', 1 << 31),
    ('target_weapon_id', -(1 << 31) - 1), ('target_weapon_id', 1 << 31),
    ('server_time_ms', -1), ('server_time_ms', 1 << 64),
])
def test_weapon_defense_rejects_fields_outside_wire_ranges(field, value):
    with pytest.raises(ValueError, match='defense-notify'):
        battle_flow.encode_battle_defense_notify(**dict(FIELDS, **{field: value}))
