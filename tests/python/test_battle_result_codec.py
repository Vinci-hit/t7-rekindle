import struct

import pytest

from Business.scripts.codec import battle_flow


# TDR refer counts serialize zero attacker results and one target result,
# not the ten-entry capacity of each decoded array.
DAMAGE_RESULT = bytes.fromhex(
    '0009'              # selector
    '0000000000000001'  # attacker RID
    '0000000000000002'  # target RID
    '0003'              # attacker sequence
    '00 00'             # body index, life state
    '0000'              # attacker result count (no entries)
    '0001'              # target result count
    '0002 ffffffec'     # DELTA_HP, -20
    '000000c8'          # weapon
    '00 00'             # attack power, defence ability
)


def test_battle_result_matches_count_referenced_wire_fixture():
    body = battle_flow.encode_battle_result(
        attacker_rid=1, target_rid=2, attacker_seq_no=3,
        target_life_state=battle_flow.LIFE_STATE_NONE, attack_weapon_id=200,
        target_result_type=battle_flow.RESULT_DATA_DELTA_HP, target_result_data=-20)
    assert body == DAMAGE_RESULT
    assert len(body) == 38


def test_battle_result_decodes_count_referenced_wire_fixture():
    assert battle_flow.decode_battle_result(DAMAGE_RESULT) == battle_flow.BattleResult(
        1, 2, 3, battle_flow.LIFE_STATE_NONE, 200, battle_flow.RESULT_DATA_DELTA_HP, -20)


@pytest.mark.parametrize('body', [DAMAGE_RESULT[:-1], DAMAGE_RESULT + b'\0',
                               DAMAGE_RESULT[:24] + bytes(60) + DAMAGE_RESULT[24:32]
                               + bytes(54) + DAMAGE_RESULT[32:]], ids=['truncated', 'trailing', 'capacity-padded'])
def test_decoder_rejects_truncated_trailing_and_capacity_padded_messages(body):
    with pytest.raises(ValueError, match='38 or 44 bytes'):
        battle_flow.decode_battle_result(body)


@pytest.mark.parametrize('offset,count', [(22, -1), (22, 1), (24, 0), (24, 2), (24, 11)])
def test_single_target_decoder_checks_wire_counts(offset, count):
    body = DAMAGE_RESULT[:offset] + struct.pack('>h', count) + DAMAGE_RESULT[offset + 2:]
    with pytest.raises(ValueError, match='counts'):
        battle_flow.decode_battle_result(body)
