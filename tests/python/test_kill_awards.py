from damage_fixture import NORMAL_DAMAGE
from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, bot, bot_life, combat, roster, scene
from Business.scripts.codec import kill_flow
from test_bot_respawn import kill, respawnTimer, tick
from test_bot_roster import multi, sample
from test_match_score import scoreItems
from test_melee_combat import advance
from test_melee_multi_hit import attack, cluster


def awards(flow, messageId):
    return [packet for packet in flow.result['send'] if packet['command'] == 12
            and packet['body'][:2] in (b'\0\1', b'\0\2')
            and struct.unpack_from('>i', packet['body'], 18)[0] == messageId]


def lethalHit(flow, attacker, target):
    actors.data(flow, target)['health'] = NORMAL_DAMAGE
    combat.applyHit(flow, attacker, target, actors.pair(sample(flow), attacker, target))


def test_first_blood_uses_native_score_update_not_streak_message():
    body = kill_flow.encode_score_update_event(
        server_time_ms=123, entity_rid=12, previous_kills=0, kills=1,
        previous_points=0, points=100, stat_message_id=730000)
    assert body == struct.pack('>HQQii12i', 1, 123, 12, 730000, 4,
                              2, 0, 1, 44, 0, 1, 3, 0, 0, 1, 0, 100)


def test_butcher_killer_uses_native_stat_message_with_one_award():
    assert kill_flow.encode_butcher_killer_message(server_time_ms=123, entity_rid=12) == struct.pack(
        '>HQQii', 2, 123, 12, 730140, 1)


@pytest.mark.parametrize('value', [-1, 2**31, True, 1.5])
def test_invalid_score_message_id_is_rejected(value):
    with pytest.raises(ValueError):
        kill_flow.encode_score_update_event(server_time_ms=1, entity_rid=1, previous_kills=0, kills=1,
                                            previous_points=0, points=100, stat_message_id=value)


@pytest.mark.parametrize('fields', [{'server_time_ms': -1}, {'entity_rid': 2**64}, {'entity_rid': True}])
def test_invalid_butcher_killer_identity_or_time_is_rejected(fields):
    with pytest.raises(ValueError):
        kill_flow.encode_butcher_killer_message(**(dict(server_time_ms=1, entity_rid=1) | fields))


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1), (2, 12), (12, 2)])
def test_first_lethal_hit_awards_first_blood_once_for_the_whole_round(multi, camp, attacker, target):
    multi.session['camp'] = camp
    roster.assemble(multi)
    combat.applyHit(multi, attacker, target, actors.pair(sample(multi), attacker, target))
    assert not awards(multi, 730000) and not multi.session['firstBloodAwarded']
    lethalHit(multi, attacker, target)
    packet, = awards(multi, 730000)
    assert struct.unpack_from('>HQQii', packet['body']) == (1, multi.now, attacker, 730000, 4)
    assert list(struct.iter_unpack('>iii', packet['body'][26:])) == scoreItems((0, 0, 0), (1, 0, 100))
    deadIndex = next(i for i, p in enumerate(multi.result['send']) if p['reason'] in ('bot-dead', 'player-dead'))
    assert multi.result['send'].index(packet) < deadIndex
    assert multi.session['firstBloodAwarded'] and not awards(multi, 710110)
    combat.applyHit(multi, attacker, target, actors.pair(sample(multi), attacker, target))
    scene.message(multi, 0xE, 7, struct.pack('>HiQb', 7, 1, target, 1))
    # Both camps make further kills, without creating a second first blood.
    lethalHit(multi, 3, 13)
    lethalHit(multi, 14, 4)
    assert awards(multi, 730000) == [packet]
    assert app.validateState(multi.state)


def test_one_swing_with_multiple_kills_awards_only_one_first_blood(multi):
    _, targets, _ = cluster(multi)
    for rid in targets:
        actors.data(multi, rid)['health'] = NORMAL_DAMAGE
    attack(multi)
    advance(multi, combat.hitTimer(1))
    packet, = awards(multi, 730000)
    assert struct.unpack_from('>HQQii', packet['body'])[2:] == (1, 730000, 4)
    assert multi.session['kills'] == len(targets)


def test_first_blood_survives_reload_respawn_and_reimport_but_resets_for_new_instance(multi):
    kill(multi)
    saved = host_runtime.decode(host_runtime.encode(multi.state))
    restored = app.migrateState(app.STATE_VERSION, saved)
    flow = app.Flow({'connection': 1}, restored, multi.context)
    tick(flow, respawnTimer(2), flow.now + 5000)
    flow.now += 50
    sample(flow)
    assert bot_life.ready(flow, 2)
    bot.importMember(flow, 2)
    scene.message(flow, 0x23, 3, b'\0\3\0')
    kill(flow)
    assert not awards(flow, 730000) and flow.session['firstBloodAwarded']
    scene.begin(flow)
    assert not flow.session['firstBloodAwarded']
    lethalHit(flow, 12, 3)
    assert len(awards(flow, 730000)) == 1


@pytest.mark.parametrize('rid', [1, 2, 12])
@pytest.mark.parametrize('field', ['kills', 'deaths', 'killStreak'])
def test_saved_round_with_kill_evidence_does_not_reaward_first_blood(multi, rid, field):
    multi.session.pop('firstBloodAwarded', None)
    actors.data(multi, rid)[field] = 1
    before = deepcopy(multi.state)
    assert app.migrateState(app.STATE_VERSION, before) == before
    kill(multi)
    assert not awards(multi, 730000) and multi.session['firstBloodAwarded']


def test_legacy_round_without_kill_evidence_can_award_first_blood(multi):
    multi.session.pop('firstBloodAwarded', None)
    kill(multi)
    assert len(awards(multi, 730000)) == 1


@pytest.mark.parametrize('value', [0, 1, None, 'true'])
def test_invalid_first_blood_state_is_rejected(multi, value):
    multi.session['firstBloodAwarded'] = value
    assert not app.validateState(multi.state)


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1), (2, 12), (12, 2)])
@pytest.mark.parametrize('streak', [9, 10, 11, kill_flow.MAX_STREAK])
def test_only_defeating_a_current_butcher_announces_the_actual_killer(multi, camp, attacker, target, streak):
    multi.session['camp'] = camp
    roster.assemble(multi)
    actors.data(multi, target).update(killStreak=streak, kills=streak)
    lethalHit(multi, attacker, target)
    packets = awards(multi, 730140)
    assert len(packets) == (1 if streak >= 10 else 0)
    if packets:
        assert struct.unpack('>HQQii', packets[0]['body']) == (2, multi.now, attacker, 730140, 1)
        deadIndex = next(i for i, p in enumerate(multi.result['send']) if p['reason'] in ('bot-dead', 'player-dead'))
        assert multi.result['send'].index(packets[0]) < deadIndex
    assert actors.data(multi, target)['killStreak'] == 0
    assert actors.data(multi, attacker)['killStreak'] == 1
    combat.applyHit(multi, attacker, target, actors.pair(sample(multi), attacker, target))
    scene.message(multi, 0xE, 7, struct.pack('>HiQb', 7, 1, target, 1))
    assert awards(multi, 730140) == packets
    assert app.validateState(multi.state)


def test_ten_kills_grant_butcher_until_death_and_can_be_earned_again(multi):
    for rid in range(2, 12):
        lethalHit(multi, 12, rid)
    packet, = awards(multi, 710110)
    assert struct.unpack('>HQQii', packet['body']) == (2, multi.now, 12, 710110, 10)
    saved = host_runtime.decode(host_runtime.encode(multi.state))
    flow = app.Flow({'connection': 1}, app.migrateState(app.STATE_VERSION, saved), multi.context)
    for rid in range(2, 12):
        tick(flow, respawnTimer(rid), flow.now + (5000 if rid == 2 else 0))
    flow.now += 50
    sample(flow)
    for rid in range(2, 12):
        assert bot_life.ready(flow, rid)
    lethalHit(flow, 2, 12)
    assert len(awards(flow, 730140)) == 1
    # Killing the terminator does not count as defeating a butcher.
    lethalHit(flow, 13, 2)
    assert len(awards(flow, 730140)) == 1
    tick(flow, respawnTimer(12), flow.now + 5000)
    flow.now += 50
    sample(flow)
    assert bot_life.ready(flow, 12)
    lethalHit(flow, 3, 12)
    assert len(awards(flow, 730140)) == 1
    tick(flow, respawnTimer(12), flow.now + 5000)
    flow.now += 50
    sample(flow)
    assert bot_life.ready(flow, 12)
    actors.data(flow, 12)['killStreak'] = 9
    lethalHit(flow, 12, 4)
    lethalHit(flow, 5, 12)
    assert len(awards(flow, 710110)) == 1 and len(awards(flow, 730140)) == 2
    assert app.validateState(flow.state)


def test_tenth_kill_can_also_end_another_butcher_without_losing_either_announcement(multi):
    multi.session['killStreak'] = 9
    actors.data(multi, 2)['killStreak'] = 10
    kill(multi)
    assert len(awards(multi, 710110)) == len(awards(multi, 730140)) == 1
    assert multi.session['killStreak'] == 10
    assert actors.data(multi, 2)['killStreak'] == 0
    combat.initialize(multi)
    multi.result['send'].clear()
    lethalHit(multi, 2, 1)
    assert not awards(multi, 730140)
