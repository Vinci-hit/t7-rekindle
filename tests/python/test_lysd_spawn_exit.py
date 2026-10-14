import math
import struct

import pytest

from Business.scripts import actors, app, battle, bot, bot_roaming, lysd_spawn_exit
from Business.scripts.codec import move_flow
from Business.scripts.navigation import NavMesh
from test_bot_respawn import kill, respawnTimer, tick
from test_bot_roster import multi, sample


@pytest.fixture
def ledge(multi, monkeypatch):
    nav = NavMesh.fromPolygons([[(0, 0, 0), (60, 0, 0), (60, 60, 0), (0, 60, 0)]])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    for index, point in enumerate(multi.session['respawnPoints']['2']):
        point[0] = [-3., 5. + index * 2, .2]
    # Existing in-flight platform exits remain valid across script reloads.
    multi.session['resourceId'] = 9999
    kill(multi)
    tick(multi, respawnTimer(2), multi.now + 5000)
    multi.session['resourceId'] = 1028
    lysd_spawn_exit.begin(multi, 2)
    return multi


def observe(flow, position, *, heading=0, blocked=False, sampled=True, placements=None):
    flow.now += 100
    if sampled:
        world = sample(flow, {**(placements or {}), 2: (position, heading)})
        world['actors']['2']['terrain']['forward']['hit'] = blocked
    bot.think(flow)


def test_direction_only_wire_has_no_position_or_height():
    body = move_flow.encode_move_in_air_direct_bc(server_tick=123, target_instance_id=2,
                                                 direction_yaw=90, left_right=0, forward_back=-1000)
    assert body == struct.pack('>HIBHhhh', 56, 123, 1, 2, 90, 0, -1000)


@pytest.mark.parametrize('fields', [{'server_tick': -1}, {'target_instance_id': 65536},
                                   {'direction_yaw': 181}, {'left_right': True}, {'forward_back': -1001}])
def test_direction_only_wire_rejects_invalid_values(fields):
    values = dict(server_tick=123, target_instance_id=2, direction_yaw=90, left_right=0, forward_back=-1000)
    with pytest.raises(ValueError):
        move_flow.encode_move_in_air_direct_bc(**(values | fields))


def test_respawn_keeps_authored_platform_and_accepts_native_surface_height(ledge):
    value = actors.data(ledge, 2)
    original = list(value['position'])
    assert original in [point[0] for point in ledge.session['respawnPoints']['2']]
    # The native controller may place the character on the platform's actual top.
    observe(ledge, [original[0], original[1], 6.2])
    assert 'life' not in value and 'spawnExit' in value
    assert value['position'] == [original[0], original[1], 6.2]
    assert app.validateState(ledge.state)


def test_exit_uses_native_gravity_without_repeated_position_corrections(ledge):
    origin = list(actors.data(ledge, 2)['position'])
    observe(ledge, [origin[0], origin[1], 6.2])
    ledge.result['send'].clear()
    for x, z in [(-2.5, 6.2), (-2, 5.5), (-1.5, 3.5), (-1, 1.2)]:
        observe(ledge, [x, origin[1], z])
    packets = [p for p in ledge.result['send'] if p['command'] == 2]
    assert packets and all(p['body'][:2] == b'\0\x38' for p in packets)
    assert actors.data(ledge, 2)['position'][2] == 1.2
    assert not actors.data(ledge, 2)['initialized']


def test_falling_through_navigation_height_is_not_treated_as_landing(ledge):
    y = actors.data(ledge, 2)['position'][1]
    for x, z in [(-3, 6.2), (-2, 4), (-1, 1.3), (.8, .5), (.8, -.3)]:
        observe(ledge, [x, y, z])
    assert 'spawnExit' in actors.data(ledge, 2)


def test_stable_landing_rejoins_navigation_and_normal_decision(ledge):
    y = actors.data(ledge, 2)['position'][1]
    for x, z in [(-3, 6.2), (-2, 4), (-1, 1.3), (1, .2), (1, .2), (1, .2), (1, .2)]:
        observe(ledge, [x, y, z])
    value = actors.data(ledge, 2)
    assert 'spawnExit' not in value and value['initialized']
    assert value['heightOffset'] == pytest.approx(.2)
    assert bot.MOVE_TIMER in ledge.session['pending']
    assert any('spawn-exit-complete' in log["message"] for log in ledge.result['logs'])


@pytest.fixture
def lowerGround(ledge, monkeypatch):
    nav = NavMesh.fromPolygons([
        [(0, 0, 0), (60, 0, 0), (60, 60, 0), (0, 60, 0)],
        [(-10, 0, -5), (-.1, 0, -5), (-.1, 60, -5), (-10, 60, -5)],
    ])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    value = actors.data(ledge, 2)
    value['heading'] = 0
    y = value['position'][1]
    for z in (.2, -1, -3, -4.8, -4.8, -4.8, -4.8):
        observe(ledge, [-3, y, z])
    return ledge


def enemyNearby(flow):
    position = list(actors.data(flow, 2)['position'])
    position[0] += 1.5
    return {1: (position, 180)}


def test_local_landing_restores_attack_before_reaching_main_navigation(lowerGround):
    value = actors.data(lowerGround, 2)
    assert bot.navigationFor(lowerGround).findPath(value['position'], [5, 5, .2]).status == 'no-path'
    for _ in range(bot_roaming.PERCEPTION_MS // 100 + 1):
        observe(lowerGround, value['position'], placements=enemyNearby(lowerGround))
    assert value['opponentId'] == 1
    lowerGround.result['send'].clear()
    for _ in range(8):
        observe(lowerGround, value['position'], placements=enemyNearby(lowerGround))
    assert value['initialized'] and value['opponentId'] == 1
    assert value['action']['intent'] in battle.ATTACK_CHAINS
    assert value['action']['index'] >= 0
    assert not any(packet['reason'] == 'bot-spawn-exit-steer' for packet in lowerGround.result['send'])


def test_local_landing_restores_defence_even_after_exit_deadline(lowerGround):
    value = actors.data(lowerGround, 2)
    lowerGround.now = value['spawnExit']['startedAt'] + lysd_spawn_exit.EXIT_TIMEOUT_MS + 100
    battle.startAction(lowerGround, 300560, actorId=1)
    for _ in range(6):
        observe(lowerGround, value['position'], placements=enemyNearby(lowerGround))
    assert value['opponentId'] == 1
    assert value['action']['intent'] == 300580
    assert value['status'] == 'parry'


def test_landed_actor_without_enemy_keeps_low_priority_return_route(lowerGround):
    value = actors.data(lowerGround, 2)
    assert value['spawnExit']['landed'] and value['initialized']
    assert value.get('opponentId') is None
    assert value['status'].startswith('spawn-return-')
    assert any('bot-spawn-landed actor=2' in log["message"] for log in lowerGround.result['logs'])


def test_search_and_current_action_take_priority_over_return_route(lowerGround):
    value = actors.data(lowerGround, 2)
    for _ in range(bot_roaming.PERCEPTION_MS // 100 + 1):
        observe(lowerGround, value['position'], placements=enemyNearby(lowerGround))
    assert value['opponentId'] == 1
    battle.startAction(lowerGround, 300560, actorId=2)
    lowerGround.result['send'].clear()
    observe(lowerGround, value['position'])
    assert value['status'] == 'attack'
    assert not any(packet['reason'] in ('bot-spawn-exit-start', 'bot-spawn-exit-steer')
                   for packet in lowerGround.result['send'])
    battle.resetAction(lowerGround, 2)
    observe(lowerGround, value['position'])
    assert value['status'] == 'search'
    lowerGround.now += bot_roaming.SEARCH_MS
    observe(lowerGround, value['position'])
    assert value['status'].startswith('spawn-return-')
    assert bot.MOVE_TIMER not in lowerGround.session['pending']


def test_landing_flag_survives_reload_and_rejects_invalid_type(lowerGround):
    assert app.migrateState(app.STATE_VERSION, lowerGround.state) == lowerGround.state
    value = actors.data(lowerGround, 2)
    assert value['spawnExit']['landed']
    value['spawnExit']['landed'] = 'true'
    assert not app.validateState(lowerGround.state)


def test_return_timeout_on_local_ground_falls_back_to_local_patrol(lowerGround):
    value = actors.data(lowerGround, 2)
    lowerGround.now = value['spawnExit']['startedAt'] + lysd_spawn_exit.EXIT_TIMEOUT_MS + 100
    observe(lowerGround, value['position'])
    assert 'spawnExit' not in value and value['initialized']
    assert value['patrolAnchor'] == value['position']
    assert value['status'] in ('advance', 'patrol', 'patrol-wait')
    assert bot.MOVE_TIMER in lowerGround.session['pending']


def test_missing_snapshot_or_obstruction_stops_forward_input(ledge):
    value = actors.data(ledge, 2)
    position = list(value['position'])
    observe(ledge, position, blocked=True)
    assert struct.unpack('>HIBHhhh', ledge.result['send'][-1]['body'])[-1] == 0
    ledge.now += 300
    observe(ledge, position, sampled=False)
    assert not value['moving']
    assert all(p['reason'] != 'body-bot-separation' for p in ledge.result['send'])


def test_repeated_snapshot_does_not_fake_a_stable_landing(ledge):
    y = actors.data(ledge, 2)['position'][1]
    for x, z in [(-3, 6.2), (1, .2), (1, .2)]:
        observe(ledge, [x, y, z])
    for _ in range(3):
        observe(ledge, [1, y, .2], sampled=False)
    assert 'spawnExit' in actors.data(ledge, 2)


def test_respawn_exit_only_applies_to_registered_luoyang_map_family(multi):
    assert lysd_spawn_exit.enabled(multi)
    multi.session['resourceId'] = 9999
    assert not lysd_spawn_exit.enabled(multi)
    kill(multi)
    tick(multi, respawnTimer(2), multi.now + 5000)
    assert 'spawnExit' not in actors.data(multi, 2)


@pytest.mark.parametrize('resourceId', [True, '1028', -1])
def test_invalid_map_identifier_is_rejected(multi, resourceId):
    multi.session['resourceId'] = resourceId
    assert not app.validateState(multi.state)


def test_exit_requires_its_start_time(ledge):
    actors.data(ledge, 2)['spawnExit'].pop('startedAt')
    assert not app.validateState(ledge.state)


def test_exit_state_survives_reload_but_is_cleared_on_death(ledge):
    value = actors.data(ledge, 2)
    observe(ledge, value['position'])
    assert app.migrateState(app.STATE_VERSION, ledge.state) == ledge.state
    kill(ledge)
    assert 'spawnExit' not in value
    assert app.validateState(ledge.state)


@pytest.mark.parametrize('mutation', [{'origin': [math.nan, 0, 0]}, {'startedAt': -1}, {'seeded': 'yes'}])
def test_invalid_exit_state_is_rejected(ledge, mutation):
    actors.data(ledge, 2)['spawnExit'].update(mutation)
    assert not app.validateState(ledge.state)
