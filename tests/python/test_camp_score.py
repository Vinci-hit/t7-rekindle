from damage_fixture import NORMAL_DAMAGE
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, bot, bot_life, combat, contracts as wire, player_life, roster, scene
from Business.scripts.codec import kill_flow
from test_bot_player import flowAt
from test_bot_respawn import kill, respawnTimer, tick
from test_bot_roster import multi, sample
from test_melee_combat import advance
from test_melee_multi_hit import attack, cluster


def updates(flow):
    return [packet for packet in flow.result['send']
            if packet['command'] == 23 and packet['body'][:2] == b'\0\1']


def totals(packet, *, target=None):
    body = packet['body']
    assert len(body) == 46 + 12 * (target is not None)
    selector, now, instanceId, count = struct.unpack_from('>HQQi', body)
    assert (selector, instanceId, count) == (1, 1, 2 + (target is not None))
    items = dict(struct.iter_unpack('>iq', body[22:]))
    if target is not None:
        assert items.pop(-1610395699) == target  # kill_total
    # The client binds hashed variable names, not the table's ordinal IDs 1/2.
    assert set(items) == {7346418, 206159}  # blue, red
    return items[206159], items[7346418]


def test_camp_kills_wire_updates_the_native_script_variables():
    assert kill_flow.encode_camp_kill_update(server_time_ms=1234, instance_id=1,
        red_kills=3, blue_kills=7) == struct.pack('>HQQiiqiq', 1, 1234, 1, 2, 7346418, 7, 206159, 3)
    assert kill_flow.encode_camp_kill_update(server_time_ms=0, instance_id=1,
        red_kills=0, blue_kills=2**31 - 1) == struct.pack('>HQQiiqiq', 1, 0, 1, 2, 7346418, 2**31 - 1, 206159, 0)


@pytest.mark.parametrize('field', ['red_kills', 'blue_kills'])
@pytest.mark.parametrize('value', [-1, 2**31, True, 1.5, '1'])
def test_camp_kill_update_rejects_invalid_totals(field, value):
    with pytest.raises(ValueError):
        kill_flow.encode_camp_kill_update(**(dict(server_time_ms=0, instance_id=1,
                                                 red_kills=0, blue_kills=0) | {field: value}))


@pytest.mark.parametrize('field', ['server_time_ms', 'instance_id'])
@pytest.mark.parametrize('value', [-1, 2**64, True])
def test_camp_kill_update_rejects_invalid_time_and_instance(field, value):
    with pytest.raises(ValueError):
        kill_flow.encode_camp_kill_update(**(dict(server_time_ms=0, instance_id=1,
                                                 red_kills=0, blue_kills=0) | {field: value}))


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1), (12, 2), (2, 12)])
def test_lethal_hit_updates_only_the_killers_camp_once(multi, camp, attacker, target):
    multi.session['camp'] = camp
    roster.assemble(multi)
    multi.result['send'].clear()
    world = sample(multi)
    pair = actors.pair(world, attacker, target)
    combat.applyHit(multi, attacker, target, pair)
    assert not updates(multi)
    actors.data(multi, target)['health'] = NORMAL_DAMAGE
    combat.applyHit(multi, attacker, target, pair)
    packet, = updates(multi)
    assert struct.unpack_from('>Q', packet['body'], 2)[0] == multi.now
    assert totals(packet) == ((0, 1) if actors.data(multi, attacker)['camp'] == 1 else (1, 0))
    assert not any(p['command'] == 10 and p['body'][:2] == b'\0\x6b' for p in multi.result['send'])
    death = next(p for p in multi.result['send'] if p['reason'] in ('bot-dead', 'player-dead'))
    assert multi.result['send'].index(packet) < multi.result['send'].index(death)
    combat.applyHit(multi, attacker, target, pair)
    if target == 1:
        player_life.die(multi, attacker, actors.data(multi, target)['spawnPosition'])
    else:
        bot_life.die(multi, target, attacker, actors.data(multi, target)['position'])
    assert updates(multi) == [packet]
    assert app.validateState(multi.state)


def test_players_and_bots_contribute_to_the_same_camp_total(multi):
    for attacker, target in ((1, 2), (12, 3), (4, 13), (4, 12)):
        actors.data(multi, target)['health'] = NORMAL_DAMAGE
        combat.applyHit(multi, attacker, target, actors.pair(sample(multi), attacker, target))
    assert [totals(packet) for packet in updates(multi)] == [(0, 1), (0, 2), (1, 2), (2, 2)]


def test_multi_kill_publishes_cumulative_camp_totals_per_victim(multi):
    _, targets, _ = cluster(multi)
    for rid in targets:
        actors.data(multi, rid)['health'] = NORMAL_DAMAGE
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert [totals(packet) for packet in updates(multi)] == [(0, 1), (0, 2), (0, 3)]


def test_death_respawn_and_hot_reload_preserve_camp_totals(multi):
    kill(multi, 12)
    kill(multi, 13)
    kill(multi, 2)
    assert totals(updates(multi)[-1]) == (2, 1)
    saved = host_runtime.decode(host_runtime.encode(multi.state))
    flow = app.Flow({'connection': 1}, app.migrateState(app.STATE_VERSION, saved), multi.context)
    tick(flow, respawnTimer(2), multi.now + 5000)
    bot.refresh(flow, 2)
    assert not updates(flow)
    flow.now += 50
    sample(flow)
    assert bot_life.ready(flow, 2)
    kill(flow, 2)
    assert [totals(packet) for packet in updates(flow)] == [(2, 2)]


@pytest.mark.parametrize('command,body', [(10, b'\0\x6a'), (35, b'\0\3\0')])
def test_instance_or_roster_refresh_restores_absolute_camp_totals(multi, command, body):
    multi.session.update(kills=3, killStreak=1, battlePoints=300)
    actors.data(multi, 12).update(kills=2, killStreak=0, battlePoints=200)
    actors.data(multi, 2).update(kills=7, killStreak=4, battlePoints=700)
    for _ in range(2):
        multi.result['send'].clear()
        scene.message(multi, command, int.from_bytes(body[:2], 'big'), body)
        packet, = updates(multi)
        assert totals(packet) == (7, 5)
        assert multi.result['send'].index(packet) > 0
        assert multi.session['kills'] == 3 and actors.data(multi, 2)['kills'] == 7


def test_new_instance_starts_both_camps_at_zero(multi):
    kill(multi)
    scene.begin(multi)
    multi.result['send'].clear()
    scene.message(multi, 10, 106, b'\0\x6a')
    packet, = updates(multi)
    assert totals(packet, target=wire.KILL_TARGET) == (0, 0)


def test_legacy_single_bot_state_uses_existing_kills_not_streak_or_points():
    flow = flowAt()
    flow.session.update(kills=4, killStreak=2, battlePoints=400)
    flow.session['bot'].update(kills=6, killStreak=3, battlePoints=600)
    scene.message(flow, 10, 106, b'\0\x6a')
    packet, = updates(flow)
    assert totals(packet) == (6, 4)


def test_missing_kill_counters_start_at_zero_without_guessing_from_streaks(multi):
    for rid in actors.ids(multi.session):
        actors.data(multi, rid).pop('kills')
    multi.session['killStreak'] = 7
    kill(multi)
    packet, = updates(multi)
    assert totals(packet) == (0, 1)


def test_camp_total_saturates_at_int32_ceiling_without_changing_actor_scores(multi):
    multi.session['kills'] = 2**31 - 1
    actors.data(multi, 12)['kills'] = 10
    kill(multi)
    packet, = updates(multi)
    assert totals(packet) == (0, 2**31 - 1)
    assert multi.session['kills'] == 2**31 - 1 and actors.data(multi, 12)['kills'] == 10
    assert app.validateState(multi.state)
