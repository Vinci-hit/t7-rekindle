from damage_fixture import NORMAL_DAMAGE
from copy import deepcopy
import struct

import pytest

from Business.scripts import actors, app, combat, roster, scene
from Business.scripts.codec import kill_flow
from test_bot_respawn import kill, respawnTimer, tick
from test_bot_roster import multi, sample


def notifications(flow, reason):
    return [packet for packet in flow.result['send'] if packet['reason'] == reason]


@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 12)])
def test_audio_announcement_diagnostic_identifies_local_player_without_claiming_playback(multi, attacker, target):
    actors.data(multi, attacker)['killStreak'] = 3
    actors.data(multi, target)['health'] = NORMAL_DAMAGE
    combat.applyHit(multi, attacker, target, actors.pair(sample(multi), attacker, target))
    lines = [line for line in multi.result['logs'] if line['message'].startswith('Audio announcement;')]
    assert len(lines) == 1 and lines[0]['level'] == 'DEBUG'
    message = lines[0]['message']
    for field in (f'serverTimeMs={multi.now}', f'attacker={attacker}', f'localPlayer={int(attacker == 1)}',
                  'streak=4', 'streakMessageId=710040', 'stage=queued', 'clientPlayback=not-observed'):
        assert field in message
    assert len(notifications(multi, 'combat-kill-streak')) == 1


def test_kill_and_streak_wire_layouts():
    assert kill_flow.encode_kill_event(server_time_ms=1234, source_rid=12, target_rid=2,
                                       weapon_tid=200, hit_part=0) == struct.pack('>HQQQii', 6, 1234, 12, 2, 200, 0)
    assert kill_flow.encode_continuous_kill_event(entity_rid=12, count=3) == struct.pack('>HQi', 1, 12, 3)
    assert kill_flow.encode_score_stat_message(server_time_ms=1234, entity_rid=12, count=3) == struct.pack(
        '>HQQii', 2, 1234, 12, 710030, 3)


@pytest.mark.parametrize('count', [10, 11, 12, kill_flow.MAX_STREAK])
def test_ten_or_more_kills_use_battlefield_butcher_resource_and_preserve_count(count):
    assert struct.unpack('>HQQii', kill_flow.encode_score_stat_message(
        server_time_ms=100, entity_rid=2, count=count)) == (2, 100, 2, 710110, count)


@pytest.mark.parametrize('count', range(2, 10))
def test_lower_streak_tiers_keep_their_original_resource(count):
    assert struct.unpack('>HQQii', kill_flow.encode_score_stat_message(
        server_time_ms=100, entity_rid=2, count=count)) == (2, 100, 2, 710000 + count * 10, count)


@pytest.mark.parametrize('fields', [{'source_rid': -1}, {'target_rid': 2**64},
                                   {'server_time_ms': -1}, {'weapon_tid': 2**31}])
def test_kill_wire_rejects_out_of_range_fields(fields):
    values = dict(server_time_ms=1, source_rid=1, target_rid=2, weapon_tid=1, hit_part=0)
    with pytest.raises(ValueError):
        kill_flow.encode_kill_event(**(values | fields))


@pytest.mark.parametrize('count', [-1, 2**31, True, 1.5])
def test_streak_wire_rejects_invalid_count(count):
    with pytest.raises(ValueError):
        kill_flow.encode_continuous_kill_event(entity_rid=2, count=count)


@pytest.mark.parametrize('camp,attacker,target', [(1, 1, 2), (2, 1, 2), (1, 2, 12), (2, 12, 2)])
def test_only_lethal_hit_publishes_one_kill_with_actual_actor_ids(multi, camp, attacker, target):
    multi.session['camp'] = camp
    roster.assemble(multi)
    world = sample(multi)
    combat.applyHit(multi, attacker, target, actors.pair(world, attacker, target))
    assert not notifications(multi, 'combat-kill')
    actors.data(multi, target)['health'] = NORMAL_DAMAGE
    combat.applyHit(multi, attacker, target, actors.pair(world, attacker, target))
    packets = notifications(multi, 'combat-kill')
    assert len(packets) == 1 and packets[0]['command'] == 10
    assert struct.unpack('>HQQQii', packets[0]['body'])[:4] == (6, multi.now, attacker, target)
    # DEAD_NOTIFY unregisters the corpse's long RID; the HUD must resolve it first.
    assert multi.result['send'].index(packets[0]) < next(
        i for i, packet in enumerate(multi.result['send']) if packet['reason'] == 'bot-dead')
    assert actors.data(multi, attacker)['killStreak'] == 1
    assert actors.data(multi, target)['killStreak'] == 0
    assert not notifications(multi, 'combat-kill-streak')
    combat.applyHit(multi, attacker, target, actors.pair(world, attacker, target))
    scene.message(multi, 0xE, 7, struct.pack('>HiQb', 7, 1, target, 1))
    assert len(notifications(multi, 'combat-kill')) == 1
    assert actors.data(multi, attacker)['killStreak'] == 1


@pytest.mark.parametrize('targets,killer', [((2, 3, 4), 1), ((12, 13, 14), 2)])
def test_streak_uses_original_client_message_from_second_kill(multi, targets, killer):
    for target in targets:
        kill(multi, target)
    packets = notifications(multi, 'combat-kill-streak')
    assert [packet['command'] for packet in packets] == [12, 12]
    assert [struct.unpack('>HQQii', packet['body']) for packet in packets] == [
        (2, multi.now, killer, 710020, 2), (2, multi.now, killer, 710030, 3)]
    assert app.validateState(multi.state)


@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 12), (12, 2)])
@pytest.mark.parametrize('previous', [9, 10, 11])
def test_each_ten_plus_kill_announces_the_highest_tier(multi, attacker, target, previous):
    actors.data(multi, attacker)['killStreak'] = previous
    actors.data(multi, target)['health'] = NORMAL_DAMAGE
    combat.applyHit(multi, attacker, target, actors.pair(sample(multi), attacker, target))
    packets = notifications(multi, 'combat-kill-streak')
    assert len(packets) == 1 and packets[0]['command'] == 12
    assert struct.unpack('>HQQii', packets[0]['body']) == (2, multi.now, attacker, 710110, previous + 1)


def test_death_resets_only_victim_streak_and_respawn_preserves_it(multi):
    kill(multi, 12)
    kill(multi, 13)
    assert actors.data(multi, 2)['killStreak'] == 2
    kill(multi, 2)
    assert actors.data(multi, 2)['killStreak'] == 0
    assert multi.session['killStreak'] == 1
    before = deepcopy(multi.state)
    assert app.migrateState(app.STATE_VERSION, before) == before
    tick(multi, respawnTimer(2), multi.now + 5000)
    assert actors.data(multi, 2)['health'] == 100
    assert actors.data(multi, 2)['killStreak'] == 0 and multi.session['killStreak'] == 1
    combat.initialize(multi)
    assert all(actors.data(multi, rid)['killStreak'] == 0 for rid in actors.ids(multi.session))


@pytest.mark.parametrize('value', [-1, 2**31, True, '2'])
@pytest.mark.parametrize('rid', [1, 2])
def test_invalid_streak_state_is_rejected(multi, rid, value):
    actors.data(multi, rid)['killStreak'] = value
    assert not app.validateState(multi.state)


def test_saved_state_without_streak_defaults_to_zero(multi):
    for rid in actors.ids(multi.session):
        actors.data(multi, rid).pop('killStreak', None)
    assert app.migrateState(app.STATE_VERSION, multi.state) == multi.state
    kill(multi)
    assert multi.session['killStreak'] == 1
