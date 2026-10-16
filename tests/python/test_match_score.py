from damage_fixture import NORMAL_DAMAGE
from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, bot, bot_life, combat, player_life, roster, scene
from Business.scripts.codec import kill_flow
from test_bot_respawn import kill, respawnTimer, tick
from test_bot_roster import multi, sample
from test_melee_combat import advance
from test_melee_multi_hit import attack, cluster
from test_player_respawn import choose, play


def totals(flow, rid):
    value = actors.data(flow, rid)
    return value.get('kills', 0), value.get('deaths', 0), value.get('battlePoints', 0)


def updates(flow):
    return [packet for packet in flow.result['send']
            if packet['command'] == 12 and packet['body'][:2] == b'\0\1']


def decode(packet, *, messageId=0):
    body = packet['body']
    assert len(body) == 74
    selector, now, rid, reason, count = struct.unpack_from('>HQQii', body)
    assert (selector, reason, count) == (1, messageId, 4)
    fields = [struct.unpack_from('>iii', body, offset) for offset in (26, 38, 50, 62)]
    assert [item[0] for item in fields] == [2, 44, 3, 1]
    return now, rid, fields


def scoreItems(previous, current):
    return [(field, previous[index], current[index]) for field, index in ((2, 0), (44, 0), (3, 1), (1, 2))]


def test_score_update_wire_carries_previous_and_total_values():
    assert kill_flow.encode_score_update_event(
        server_time_ms=1234, entity_rid=12, previous_kills=4, kills=5,
        previous_points=400, points=500, previous_deaths=2, deaths=3) == struct.pack(
            '>HQQii12i', 1, 1234, 12, 0, 4, 2, 4, 5, 44, 4, 5, 3, 2, 3, 1, 400, 500)


@pytest.mark.parametrize('mode', [2, 7, 10, 18])
def test_client_mode_selects_one_cumulative_kill_field(mode):
    body = kill_flow.encode_score_update_event(
        server_time_ms=1, entity_rid=1, previous_kills=4, kills=5, previous_points=400, points=500)
    fields = list(struct.iter_unpack('>iii', body[26:]))
    # Native scoreboard modes 7/10/18 ignore KILL_NUM and read ALL_KILL_NUM instead.
    killField = 44 if mode in (7, 10, 18) else 2
    assert [new for field, old, new in fields if field == killField] == [5]
    assert [new - old for field, old, new in fields if field == 1] == [100]


@pytest.mark.parametrize('name', ['previous_kills', 'kills', 'previous_points', 'points',
                                  'previous_deaths', 'deaths'])
@pytest.mark.parametrize('value', [-1, 2**31, True, 1.5])
def test_score_update_rejects_invalid_counters(name, value):
    fields = dict(server_time_ms=1, entity_rid=1, previous_kills=0, kills=1, previous_points=0, points=100)
    fields[name] = value
    with pytest.raises(ValueError):
        kill_flow.encode_score_update_event(**fields)


@pytest.mark.parametrize('fields', [{'server_time_ms': -1}, {'entity_rid': 2**64}, {'entity_rid': True}])
def test_score_update_rejects_invalid_identity_and_time(fields):
    values = dict(server_time_ms=1, entity_rid=1, previous_kills=0, kills=1, previous_points=0, points=100)
    with pytest.raises(ValueError):
        kill_flow.encode_score_update_event(**(values | fields))


@pytest.mark.parametrize('camp', [1, 2])
@pytest.mark.parametrize('attacker,target', [(1, 2), (2, 1), (2, 12), (12, 2)])
def test_every_lethal_hit_credits_killer_and_victim_once(multi, camp, attacker, target):
    multi.session['camp'] = camp
    roster.assemble(multi)
    world = sample(multi)
    combat.applyHit(multi, attacker, target, actors.pair(world, attacker, target))
    assert totals(multi, attacker) == totals(multi, target) == (0, 0, 0) and not updates(multi)
    actors.data(multi, target)['health'] = NORMAL_DAMAGE
    combat.applyHit(multi, attacker, target, actors.pair(world, attacker, target))
    assert totals(multi, attacker) == (1, 0, 100) and totals(multi, target) == (0, 1, 0)
    assert all(totals(multi, rid) == (0, 0, 0) for rid in actors.ids(multi.session) if rid not in (attacker, target))
    winnerPacket, victimPacket = updates(multi)
    assert decode(winnerPacket, messageId=730000) == (multi.now, attacker, scoreItems((0, 0, 0), (1, 0, 100)))
    assert decode(victimPacket) == (multi.now, target, scoreItems((0, 0, 0), (0, 1, 0)))
    deadIndex = next(i for i, p in enumerate(multi.result['send']) if p['reason'] in ('bot-dead', 'player-dead'))
    assert all(multi.result['send'].index(packet) < deadIndex for packet in (winnerPacket, victimPacket))
    combat.applyHit(multi, attacker, target, actors.pair(world, attacker, target))
    if target == 1:
        player_life.die(multi, attacker, actors.data(multi, target)['spawnPosition'])
    else:
        bot_life.die(multi, target, attacker, actors.data(multi, target)['position'])
    scene.message(multi, 0xE, 7, struct.pack('>HiQb', 7, 1, target, 1))
    assert len(updates(multi)) == 2
    assert totals(multi, attacker) == (1, 0, 100) and totals(multi, target) == (0, 1, 0)
    assert app.validateState(multi.state)


def test_multi_kill_records_each_victim_with_monotonic_totals(multi):
    _, targets, _ = cluster(multi)
    for rid in targets:
        actors.data(multi, rid)['health'] = NORMAL_DAMAGE
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert totals(multi, 1) == (3, 0, 300)
    assert [decode(packet, messageId=730000 if index == 0 else 0)[1:]
            for index, packet in enumerate(updates(multi))] == [
        entry for i, rid in enumerate(targets) for entry in (
            (1, scoreItems((i, 0, i * 100), (i + 1, 0, (i + 1) * 100))),
            (rid, scoreItems((0, 0, 0), (0, 1, 0))))]
    assert all(totals(multi, rid) == (0, 1, 0) for rid in targets)
    assert app.validateState(multi.state)


def test_score_survives_death_respawn_refresh_and_hot_reload_but_not_new_round(multi):
    kill(multi, 12)
    kill(multi, 13)
    assert totals(multi, 2) == (2, 0, 200)
    kill(multi, 2)
    assert actors.data(multi, 2)['killStreak'] == 0 and totals(multi, 2) == (2, 1, 200)
    saved = host_runtime.decode(host_runtime.encode(multi.state))
    assert app.migrateState(app.STATE_VERSION, saved) == multi.state
    tick(multi, respawnTimer(2), multi.now + 5000)
    assert totals(multi, 2) == (2, 1, 200) and totals(multi, 1) == (1, 0, 100)
    bot.refresh(multi, 2)
    assert totals(multi, 2) == (2, 1, 200)
    multi.now += 50
    sample(multi)
    assert bot_life.ready(multi, 2)
    kill(multi, 2)
    assert totals(multi, 2) == (2, 2, 200) and totals(multi, 1) == (2, 0, 200)
    combat.initialize(multi)
    assert all(totals(multi, rid) == (0, 0, 0) for rid in actors.ids(multi.session))


@pytest.mark.parametrize('rid', [1, 2, 12])
@pytest.mark.parametrize('score', [(3, 2, 300), (0, 1, 0)])
def test_reimport_restores_scoreboard_totals_without_awarding_points_again(multi, rid, score):
    actors.data(multi, rid).update(zip(('kills', 'deaths', 'battlePoints'), score))
    if rid == 1:
        scene.message(multi, 0x23, 3, b'\0\3\0')
    else:
        bot.importMember(multi, rid)
    packet, = updates(multi)
    assert decode(packet) == (multi.now, rid, scoreItems(score, score))
    assert totals(multi, rid) == score


def test_zero_health_player_awards_one_kill_without_repeat_rewards(multi):
    world = sample(multi)
    actors.data(multi, 1)['health'] = NORMAL_DAMAGE
    for _ in range(3):
        combat.applyHit(multi, 2, 1, actors.pair(world, 2, 1))
    assert actors.data(multi, 1)['health'] == 0
    assert totals(multi, 2) == (1, 0, 100) and totals(multi, 1) == (0, 1, 0)
    assert len(updates(multi)) == 2


def test_player_repeated_deaths_accumulate_across_respawn(multi):
    multi.session.update(loaded=True, kills=4, battlePoints=400)
    for count in (1, 2):
        kill(multi, 1)
        tick(multi, player_life.SELECT_TIMER, multi.now + player_life.RESPAWN_MS)
        choose(multi)
        play(multi)
        multi.now += 50
        sample(multi)
        tick(multi, player_life.RESPAWN_TIMER, multi.now)
        assert actors.active(multi, 1)
        assert totals(multi, 1) == (4, count, 400) and totals(multi, 2) == (count, 0, count * 100)
    assert app.validateState(multi.state)


def test_saved_state_without_score_fields_starts_counting_from_next_kill(multi):
    for rid in actors.ids(multi.session):
        for field in ('kills', 'deaths', 'battlePoints'):
            actors.data(multi, rid).pop(field, None)
    multi.session['killStreak'] = 7  # A life streak is not the round's cumulative score.
    previous = deepcopy(multi.state)
    assert app.migrateState(app.STATE_VERSION, previous) == previous
    kill(multi)
    assert totals(multi, 1) == (1, 0, 100) and multi.session['killStreak'] == 8
    assert totals(multi, 2) == (0, 1, 0)


def test_old_state_without_deaths_preserves_existing_kills_and_points(multi):
    multi.session.update(kills=5, battlePoints=500)
    actors.data(multi, 2).update(kills=3, battlePoints=300)
    for rid in (1, 2):
        actors.data(multi, rid).pop('deaths', None)
    assert app.validateState(multi.state)
    kill(multi)
    assert totals(multi, 1) == (6, 0, 600) and totals(multi, 2) == (3, 1, 300)


@pytest.mark.parametrize('rid', [1, 2])
@pytest.mark.parametrize('name', ['kills', 'deaths', 'battlePoints'])
@pytest.mark.parametrize('value', [-1, 2**31, True, '1'])
def test_invalid_match_score_state_is_rejected(multi, rid, name, value):
    actors.data(multi, rid)[name] = value
    assert not app.validateState(multi.state)


def test_score_int32_ceiling_does_not_wrap_or_break_death_handling(multi):
    multi.session.update(kills=2**31 - 1, battlePoints=2**31 - 50)
    actors.data(multi, 2)['deaths'] = 2**31 - 1
    kill(multi)
    assert totals(multi, 1) == (2**31 - 1, 0, 2**31 - 1)
    assert totals(multi, 2) == (0, 2**31 - 1, 0)
    assert not actors.active(multi, 2) and app.validateState(multi.state)
