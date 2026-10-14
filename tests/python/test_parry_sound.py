from copy import deepcopy
import struct

import pytest

from Business.scripts import app, battle, combat, contracts as wire
from Business.scripts.codec import battle_flow
from blade_fixture import contact, publish
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_combat import advance, hp, states
from test_melee_multi_hit import attack
from test_parry_feedback import REACTIONS


def hitPackets(flow):
    return [packet for packet in flow.result['send'] if packet['command'] == battle_flow.BATTLE_COMMAND
            and packet['body'][:2] == struct.pack('>H', battle_flow.HIT_POINT_NOTIFY)]


def defensePackets(flow):
    return [packet for packet in flow.result['send'] if packet['command'] == battle_flow.BATTLE_COMMAND
            and packet['body'][:2] == b'\x00\x28']


@pytest.mark.parametrize('attacker,defender', [(1, 2), (2, 1), (2, 12), (12, 2)])
@pytest.mark.parametrize('intent,guard,blocked,success', [*REACTIONS, (battle.DASH_ATTACK, 300600, 443, 233)])
@pytest.mark.parametrize('journal', [False, True], ids=['single-frame', 'contact-journal'])
def test_parry_sends_dedicated_sound_and_weapon_hit_once_with_contact_time(
        multi, attacker, defender, intent, guard, blocked, success, journal):
    sample(multi, {attacker: ((0., 0., .2), 0), defender: ((1.5, 0., .2), 180)})
    battle.startAction(multi, guard, actorId=defender)
    advance(multi, battle.actionTimer(defender))
    attack(multi, attacker, intent)
    hit = contact(defender, position=(.75, .25, 1.4), direction=(.6, .8, 0.),
                  normal=(0., 0., 1.), material=4, defenceWeaponId=wire.WEAPON_ID)
    if journal:
        event = native(multi, attacker, defender)
        event.update(hit, targetState=battle.BLOCK_CHAINS[guard][1])
        batch = multi.context['botWorld']['contactJournal']
        batch['events'] = [event, dict(event)]
        multi.now = event['occurredAt'] + 700
        # Settle after the original guard has already been released in Business.
        battle.releaseAction(multi, battle.BLOCK_RELEASE, actorId=defender)
        expectedTime = int(event['occurredAt'])
    else:
        publish(multi, attacker, [hit])
        expectedTime = multi.now
    multi.result['send'].clear()

    combat.worldUpdated(multi)

    soundPackets = defensePackets(multi)
    assert len(soundPackets) == 1
    assert struct.unpack('>HqqbiiQ', soundPackets[0]['body']) == (
        40, attacker, defender, 2, wire.WEAPON_ID, wire.WEAPON_ID, expectedTime)
    packets = hitPackets(multi)
    assert len(packets) == 1
    body = packets[0]['body']
    decoded = battle_flow.decode_battle_hit_point_notify(body)
    assert (decoded.attacker_mid, decoded.target_mid) == (attacker, defender)
    assert decoded.attacker_weapon_id == decoded.target_weapon_id == wire.WEAPON_ID
    assert decoded.server_time_ms == expectedTime
    assert decoded.hit_pos == pytest.approx(hit['position'])
    assert struct.unpack_from('>4f', body, 15) == pytest.approx((*hit['direction'], 0.))
    assert struct.unpack_from('>4f', body, 31) == pytest.approx((*hit['normal'], 0.))
    assert struct.unpack_from('>i', body, 47)[0] == hit['material']
    assert body[87] == 1  # Weapon contact is feedback, not body damage.
    assert hp(multi, attacker) == hp(multi, defender) == combat.MAX_HEALTH
    assert states(multi) == ([(attacker, blocked)] if journal else [(attacker, blocked), (defender, success)])
    assert battle.actionState(multi, attacker)['reaction'] == 'parried'
    assert battle.actionState(multi, defender).get('reaction') == (None if journal else 'parry-success')
    assert not any(packet['reason'].startswith(('combat-health-', 'combat-damage'))
                   for packet in multi.result['send'])
    assert combat.hitTimer(attacker) not in multi.session['pending']

    sent, sounds = deepcopy(packets), deepcopy(soundPackets)
    combat.worldUpdated(multi)
    if journal:
        batch['batchId'] += 1
        combat.worldUpdated(multi)
    else:
        app.handleTimer(multi, combat.hitTimer(attacker))
    if not journal:
        advance(multi, battle.actionTimer(defender))
    advance(multi, battle.actionTimer(attacker))
    assert hitPackets(multi) == sent
    assert defensePackets(multi) == sounds


@pytest.mark.parametrize('case', ['wrong-direction', 'back-facing', 'released', 'miss', 'terrain', 'friendly'])
def test_unsuccessful_guards_never_send_weapon_parry_audio(multi, case):
    world = sample(multi, {1: ((0., 0., .2), 0), 2: ((1.5, 0., .2), 180)})
    battle.startAction(multi, 300600 if case == 'wrong-direction' else 300580, actorId=2)
    advance(multi, battle.actionTimer(2))
    if case == 'back-facing':
        world['actors']['2']['heading'] = 0
    elif case == 'released':
        battle.releaseAction(multi, battle.BLOCK_RELEASE, actorId=2)
    elif case == 'friendly':
        battle.actorData(multi, 2)['camp'] = multi.session['camp']
    contacts = [] if case == 'miss' else [contact(2)]
    if case == 'terrain':
        contacts.insert(0, contact(0, along=.1, material=4))
    publish(multi, 1, contacts)
    attack(multi)
    combat.worldUpdated(multi)
    assert defensePackets(multi) == []
    assert all(battle_flow.decode_battle_hit_point_notify(packet['body']).target_weapon_id == 0
               for packet in hitPackets(multi))
    assert battle.actionState(multi, 1).get('reaction') != 'parried'
