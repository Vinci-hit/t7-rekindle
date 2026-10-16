"""A shield slot selects a compatible primary without changing the round loadout."""
from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import app, battle, loadouts, player_profile
from Business.scripts.codec import weapon_flow
from test_battle_actions import makeFlow, sendAction, advanceAction
from test_loadouts import configured, resources
from test_weapon_switch import change


@pytest.mark.parametrize('previousSlot', [2, 13])
def test_shield_slot_equips_one_handed_weapon_and_shield(resources, previousSlot):
    flow = configured(makeFlow(), resources)
    change(flow, previousSlot)
    advanceAction(flow)
    before = deepcopy(flow.session['loadoutProfile'])
    first = len(flow.result['send'])
    change(flow, 3)
    packets = flow.result['send'][first:]
    assert [packet['reason'] for packet in packets] == [
        'weapon-change-response', 'weapon-use-update', 'battle-model-state-1']
    assert struct.unpack('>Hiii', packets[0]['body']) == (2, 0, 701, 703)
    assert (flow.session['weaponId'], flow.session['mainWeaponId'], flow.session['offhandId']) == (701, 701, 703)
    update = packets[1]['body']
    assert struct.unpack_from('>HQh', update) == (3, 1, 4)
    assert [struct.unpack_from('>iBBB', update, 12 + i * 29) for i in range(4)] == [
        (701, 1, 0, 1), (702, 2, 0, 0), (703, 3, 0, 1), (100000, 13, 0, 0)]
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + 700
    assert flow.session['loadoutProfile'] == before
    assert app.validateState(flow.state)
    assert host_runtime.decode(host_runtime.encode(flow.state)) == flow.state
    advanceAction(flow)
    assert flow.session['action']['index'] == -1 and flow.session['offhandId'] == 703


@pytest.mark.parametrize('unarmed', [False, True])
def test_shield_slot_prefers_current_or_remembered_one_handed_weapon(resources, unarmed):
    flow = configured(makeFlow(), resources)
    flow.session['weaponProfiles']['702']['hand'] = 2
    change(flow, 2)
    advanceAction(flow)
    if unarmed:
        change(flow, 13)
        advanceAction(flow)
    before = deepcopy(flow.state)
    first, timerCount = len(flow.result['send']), len(flow.result['timers'])
    change(flow, 3)
    assert struct.unpack('>Hiii', flow.result['send'][first]['body']) == (2, 0, 702, 703)
    assert flow.session['weaponId'] == 702 and flow.session['offhandId'] == 703
    if not unarmed:
        assert flow.state == before
        assert len(flow.result['send']) == first + 1
        assert len(flow.result['timers']) == timerCount
    assert app.validateState(flow.state)


def test_shield_slot_falls_back_to_lowest_configured_one_handed_slot(resources):
    flow = configured(makeFlow(), resources)
    flow.session['weaponProfiles']['702']['hand'] = 2
    flow.session.update(weaponId=100000, mainWeaponId=100000, offhandId=0,
                        equipment=list(reversed(flow.session['equipment'])))
    change(flow, 3)
    assert (flow.session['weaponId'], flow.session['offhandId']) == (701, 703)
    assert app.validateState(flow.state)


@pytest.mark.parametrize('slots', [
    [dict(slot=2, group=1, index=0), dict(slot=3, group=2, index=0)],
    [dict(slot=3, group=2, index=0)],
])
def test_shield_slot_without_equipped_one_handed_weapon_is_silent(resources, slots):
    flow = configured(makeFlow(), resources)
    flow.state['playerProfile'] = player_profile.change(flow.state['playerProfile'], resources,
        heroId=1101, slots=slots)
    loadouts.begin(flow)
    before = deepcopy(flow.state)
    for _ in range(2):
        change(flow, 3)
        assert flow.state == before
        assert flow.result['send'] == flow.result['timers'] == flow.result['logs'] == []
    assert app.validateState(flow.state)


@pytest.mark.parametrize('lock', ['attack', 'hurt', 'drawing'])
def test_shield_slot_respects_existing_action_locks(resources, lock):
    flow = configured(makeFlow(), resources)
    change(flow, 2)
    advanceAction(flow)
    if lock == 'attack':
        sendAction(flow, 300560)
    elif lock == 'hurt':
        flow.session['hurtUntil'] = flow.now + 100
    else:
        change(flow, 13)
    before = deepcopy(flow.state)
    first, timerCount = len(flow.result['send']), len(flow.result['timers'])
    change(flow, 3)
    assert flow.state == before
    assert [packet['body'] for packet in flow.result['send'][first:]] == [
        weapon_flow.encode_change_response(weapon_flow.CANNOT_CHANGE,
            flow.session['weaponId'], flow.session['offhandId'])]
    assert len(flow.result['timers']) == timerCount
