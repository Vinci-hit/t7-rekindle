import struct

import pytest

from Business.scripts.codec import leave_flow, settlement_flow as codec


@pytest.mark.parametrize('result', [0, 1, 2, 3])
def test_no_reward_settle_layout_and_personal_result(result):
    body = codec.encode_settle_update(level=100, start_pattern=2, result=result)
    assert codec.SETTLE_COMMAND == 52 and len(body) == 31
    assert struct.unpack('>HiiiBiiii', body) == (1, 100, 0, 2, 0, 0, 0, 0, result)


def test_final_score_layout_and_independent_finish_qualifying_lists():
    body = codec.encode_final_score(server_time_ms=123, entity_rid=20, kills=3, deaths=2,
                                    points=300, result=1, rank=10)
    assert len(body) == 98 and struct.unpack_from('>HQQii', body) == (1, 123, 20, 0, 6)
    assert list(struct.iter_unpack('>iii', body[26:])) == [
        (2, 3, 3), (44, 3, 3), (3, 2, 2), (1, 300, 300), (9, 1, 1), (51, 10, 10)]
    finish = leave_flow.encode_si_finish_game(server_time_ms=123, game_result=2, local_user_id=10000)
    assert len(finish) == 118
    assert list(struct.iter_unpack('>i5I', finish[22:])) == [(1, 10000, 0, 0, 0, 0)] + [(0,) * 6] * 3


@pytest.mark.parametrize('key,value', [('kills', -1), ('deaths', 2**31), ('points', True),
                                      ('result', 4), ('rank', 0), ('rank', 21), ('entity_rid', -1)])
def test_final_score_rejects_invalid_wire_fields(key, value):
    values = dict(server_time_ms=123, entity_rid=1, kills=0, deaths=0, points=0, result=3, rank=1)
    with pytest.raises(ValueError):
        codec.encode_final_score(**dict(values, **{key: value}))


@pytest.mark.parametrize('key,value', [('level', 0), ('level', True), ('start_pattern', 8),
                                      ('result', -1), ('result', True)])
def test_settle_rejects_unsupported_layout_or_result(key, value):
    values = dict(level=100, start_pattern=2, result=3)
    with pytest.raises(ValueError):
        codec.encode_settle_update(**dict(values, **{key: value}))
