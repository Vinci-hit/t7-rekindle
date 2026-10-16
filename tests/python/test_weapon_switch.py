from damage_fixture import NORMAL_DAMAGE
"""Native CMD_HAND / CMD_W1 packets, equipment synchronization and fist actions."""
from copy import deepcopy
import struct

import pytest

from Business.scripts import app, battle, combat, contracts as wire
from Business.scripts.codec import battle_flow
from test_battle_actions import makeFlow, sendAction, advanceAction
from test_melee_combat import arena, advance, swing
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_multi_hit import attack
from test_player_respawn import select, play

FIST_ID = 100000


def change(flow, slot=13):
    app.handleMessage(flow, {'command': 5, 'body': struct.pack('>Hh', 1, slot)})


def weapons(flow):
    body = next(p['body'] for p in reversed(flow.result['send'])
                if p['command'] == 5 and p['body'][:2] == b'\0\3')
    selector, rid, count = struct.unpack_from('>HQh', body)
    assert (selector, rid, count) == (3, 1, 2)
    assert len(body) == 12 + 29 * count + 10
    assert struct.unpack_from('>hQ', body, 12 + 29 * count) == (1, flow.now)
    return [struct.unpack_from('>iBBB', body, 12 + index * 29) for index in range(count)]


def states(flow):
    return [battle_flow.decode_battle_state_sync_simple(p['body']).state
            for p in flow.result['send'] if p['command'] == 4 and p['body'][:2] == b'\0\3']


@pytest.mark.parametrize('returnSlot', [13, 1])
def test_native_hand_toggle_and_main_slot_restore_weapon(returnSlot):
    flow = makeFlow()
    change(flow)
    assert flow.session['weaponId'] == FIST_ID
    assert struct.unpack('>Hiii', flow.result['send'][0]['body']) == (2, 0, FIST_ID, 0)
    assert weapons(flow) == [(wire.WEAPON_ID, 1, 0, 0), (FIST_ID, 13, 0, 1)]
    assert states(flow) == [1]
    advanceAction(flow)
    assert states(flow) == [1, 2]
    change(flow, returnSlot)
    assert flow.session['weaponId'] == wire.WEAPON_ID
    assert weapons(flow) == [(wire.WEAPON_ID, 1, 0, 1), (FIST_ID, 13, 0, 0)]
    advanceAction(flow)
    assert states(flow) == [1, 2, 1, 2]


def test_selecting_already_equipped_main_weapon_is_an_acknowledged_noop():
    flow = makeFlow()
    change(flow, 1)
    assert len(flow.result['send']) == 1
    assert struct.unpack('>Hiii', flow.result['send'][0]['body']) == (2, 0, wire.WEAPON_ID, 0)
    assert flow.result['timers'] == []


@pytest.mark.parametrize('values', [
    {'groundEnabled': False}, {'battleEntered': False}, {'leaving': True},
    {'life': {'state': 'dead'}}, {'controlBaseline': 'unknown'}, {'hurtUntil': 1400},
])
def test_switch_is_rejected_outside_live_unlocked_battle(values):
    flow = makeFlow(**values)
    before = deepcopy(flow.session)
    change(flow)
    assert flow.session == before
    assert not any(p['command'] == 4 or p['body'][:2] == b'\0\3' for p in flow.result['send'])


@pytest.mark.parametrize('slot', [0, 2, 4, 14, -1])
def test_unavailable_slots_do_not_change_equipment(slot):
    flow = makeFlow()
    change(flow, slot)
    assert struct.unpack('>Hiii', flow.result['send'][0]['body']) == (2, 620001, wire.WEAPON_ID, 0)
    assert len(flow.result['send']) == 1


@pytest.mark.parametrize('body', [b'\0\1', b'\0\1\0', b'\0\1\0\x0d\0'])
def test_malformed_native_switch_request_is_transactional(body):
    flow = makeFlow()
    before = deepcopy(flow.state)
    with pytest.raises(ValueError):
        app.handleMessage(flow, {'command': 5, 'body': body})
    assert flow.state == before
    assert flow.result['send'] == []


def test_attack_and_duplicate_switch_do_not_interrupt_equipping():
    flow = makeFlow()
    change(flow)
    deadline = flow.session['pending'][battle.ACTION_TIMER]
    sendAction(flow, 300560)
    change(flow)
    assert flow.session['weaponId'] == FIST_ID
    assert states(flow) == [1]
    assert flow.session['pending'][battle.ACTION_TIMER] == deadline
    advanceAction(flow)
    assert states(flow) == [1, 2]
    assert flow.session['action']['index'] == -1


def test_weapon_switch_does_not_cancel_an_active_attack():
    flow = makeFlow()
    sendAction(flow, 300560)
    before = deepcopy(flow.session)
    change(flow)
    assert flow.session == before
    assert struct.unpack('>Hiii', flow.result['send'][-1]['body']) == (2, 620000, wire.WEAPON_ID, 0)


@pytest.mark.parametrize('intent,finish', [(300560, 369), (300550, 264), (300570, 264), (300540, 264)])
def test_unarmed_attack_uses_fist_timings_and_native_directional_states(intent, finish):
    flow = makeFlow()
    change(flow)
    advanceAction(flow)
    sendAction(flow, intent)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + 369
    sendAction(flow, battle.ATTACK_RELEASE)
    advanceAction(flow)
    assert states(flow)[-1] == battle.ATTACK_CHAINS[intent][2]
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + 632
    advanceAction(flow)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + finish
    advanceAction(flow)
    assert states(flow)[-1] == 2


def test_fist_contact_uses_fist_weapon_in_damage_and_hit_feedback(arena):
    flow = arena
    change(flow)
    advance(flow, battle.ACTION_TIMER)
    swing(flow)
    assert flow.session['swing']['weaponId'] == FIST_ID
    advance(flow, combat.hitTimer(1))
    assert flow.session['bot']['health'] == 100 - NORMAL_DAMAGE
    result = next(p for p in flow.result['send'] if p['reason'] == 'combat-damage')
    hit = next(p for p in flow.result['send'] if p['reason'] == 'combat-hit-point')
    assert battle_flow.decode_battle_result(result['body']).attack_weapon_id == FIST_ID
    assert battle_flow.decode_battle_hit_point_notify(hit['body']).attacker_weapon_id == FIST_ID


def test_reload_retains_equipment_and_switch_timer():
    flow = makeFlow()
    change(flow)
    migrated = app.migrateState(app.STATE_VERSION, flow.state)
    assert app.validateState(migrated)
    assert migrated == flow.state
    assert migrated is not flow.state


@pytest.mark.parametrize('weaponId', [True, -1, 0, '100000', 12345])
def test_invalid_equipment_is_not_accepted_by_state_validation(weaponId):
    flow = makeFlow(weaponId=weaponId)
    assert not app.validateState(flow.state)


@pytest.mark.parametrize('intent,parried,terrain', [
    (300560, 520, 420), (300550, 450, 350), (300570, 560, 460), (300540, 450, 350),
])
def test_fist_reactions_use_their_own_timing(intent, parried, terrain):
    flow = makeFlow(weaponId=FIST_ID)
    sendAction(flow, intent)
    battle.parryReaction(flow, 1, 2, success=False)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + parried
    battle.terrainReaction(flow, 1)
    assert flow.session['pending'][battle.ACTION_TIMER] == flow.now + terrain


def test_unarmed_cannot_start_the_primary_weapon_dash():
    flow = makeFlow(weaponId=FIST_ID)
    body = struct.pack('>HIib', 1, (1 << 26) | (1 << 21), battle.DASH_ATTACK, 1)
    app.handleMessage(flow, {'command': 4, 'body': body})
    assert flow.result['send'] == []
    assert flow.result['timers'] == []


def test_switch_back_restores_sword_timings_without_changing_bots(arena):
    change(arena)
    advance(arena, battle.ACTION_TIMER)
    battle.startAction(arena, 300560, actorId=2)
    assert arena.session['pending'][battle.actionTimer(2)] == arena.now + 360
    change(arena, 1)
    advance(arena, battle.ACTION_TIMER)
    sendAction(arena, 300560)
    assert arena.session['pending'][battle.ACTION_TIMER] == arena.now + 360


def test_late_fist_contact_keeps_its_weapon_after_switching_back(multi):
    multi.session['weaponId'] = FIST_ID
    sample(multi)
    attack(multi, 1)
    native(multi, attacker=1, target=2)
    multi.now += 650
    app.handleTimer(multi, battle.ACTION_TIMER)
    multi.now += 400
    app.handleTimer(multi, battle.ACTION_TIMER)
    change(multi, 1)
    assert multi.session['weaponId'] == wire.WEAPON_ID
    combat.worldUpdated(multi)
    result = next(p for p in multi.result['send'] if p['reason'] == 'combat-damage')
    assert battle_flow.decode_battle_result(result['body']).attack_weapon_id == FIST_ID


def test_new_life_restores_primary_equipment(multi):
    multi.session['weaponId'] = FIST_ID
    select(multi)
    sample(multi)
    play(multi)
    assert multi.session.get('weaponId', wire.WEAPON_ID) == wire.WEAPON_ID
    assert multi.session['life']['state'] == 'spawning'


def test_leaving_during_weapon_change_cancels_the_timer():
    from Business.scripts import scene
    flow = makeFlow()
    change(flow)
    scene.leave(flow, struct.pack('>Hb', 2, 0))
    before = deepcopy(flow.result['send'])
    flow.now += 1000
    app.handleTimer(flow, battle.ACTION_TIMER)
    change(flow)
    assert flow.result['send'] == before
    assert battle.ACTION_TIMER not in flow.session['pending']
