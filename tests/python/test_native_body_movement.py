"""Business chooses intent; the native controller owns contact and position."""
from copy import deepcopy
import struct

import pytest

from Business.scripts import actors, bot, contracts as wire
from Business.scripts.codec import move_flow
from test_bot_roster import multi, sample


@pytest.mark.parametrize('actorId', [1, 2, 3, 12, 20])
def test_vision_assigns_each_actor_its_own_collision_group(actorId):
    body = wire.actorVision(1, runtimeMovement=True, actorId=actorId)
    assert struct.unpack_from('>i', body, 75)[0] == actorId


def test_movement_keeps_actor_collision_group(multi):
    for actorId in (2, 3, 12, 20):
        bot.broadcast(multi, True, actorId=actorId)
        packet = move_flow.decode_move_bc_with_system_and_active(multi.result['send'][-1]['body'])
        assert packet.system_group == actorId


def test_pulse_leaves_player_and_overlapping_bots_to_native_contact(multi):
    sample(multi, {1: ((1, 0, .2), 0), 2: ((1, 0, .2), 0), 3: ((1, 0, .2), 0)})
    multi.session['bodyPlayer'] = dict(position=[0, 0, .2], sampledAt=multi.now - 50)
    for actorId in (2, 3):
        actors.data(multi, actorId).update(position=[1, 0, .2], initialized=True, heightOffset=.2)
    before = deepcopy(multi.session)
    bot.timer(multi, actors.PULSE_TIMER)
    assert not multi.result['send']
    assert actors.data(multi, 2)['position'] == before['bots']['2']['position']
    assert multi.session.get('ground') == before.get('ground')


def test_blocked_bot_does_not_advance_its_position_or_path(multi):
    value = actors.data(multi, 2)
    value.update(position=[0, 0, .2], initialized=True, heightOffset=.2, heading=0,
                 path=[[2, 0, 0]], step=0, lastMoveAt=multi.now)
    for _ in range(10):
        multi.now += 50
        sample(multi, {2: ((0, 0, .2), 0)})
        bot.move(multi, 2)
        assert value['position'] == [0, 0, .2]
        assert value['step'] == 0
    assert value['moving']


def test_small_native_contact_displacement_is_authoritative(multi):
    value = actors.data(multi, 2)
    value.update(position=[0, 0, .2], initialized=True, heightOffset=.2, heading=0,
                 path=[[10, 0, 0]], step=0, lastMoveAt=multi.now - 50)
    sample(multi, {2: ((.08, .04, .2), 0)})
    bot.move(multi, 2)
    assert value['position'] == [.08, .04, .2]


def test_facing_target_does_not_write_coordinates(multi):
    value = actors.data(multi, 2)
    value.update(position=[0, 0, .2], heading=90, lastTurnAt=multi.now - 100)
    bot.faceTarget(multi, [2, 0, .2], 2)
    packet = multi.result['send'][-1]
    assert packet['body'][:2] == struct.pack('>H', move_flow.MOVE_IN_AIR_DIRECT_BC)
