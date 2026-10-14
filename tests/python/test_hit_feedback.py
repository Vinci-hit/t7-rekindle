from damage_fixture import NORMAL_DAMAGE, DASH_DAMAGE
from copy import deepcopy
import math
import struct

import pytest

from Business.scripts import actors, battle, body_collision, combat
from Business.scripts.codec import battle_flow
from test_bot_roster import multi
from test_melee_combat import arena, advance, hp
from test_melee_multi_hit import attack, sampleTargets
from blade_fixture import contact, publish


def placePair(flow, attacker, origin, target, yaw=0):
    sourceName, targetName = ('player', 'bot') if attacker == 1 else ('bot', 'player')
    world = flow.context['botWorld']
    world.update({sourceName: list(origin), targetName: list(target), sourceName + 'Heading': yaw,
                  targetName + 'Heading': (yaw + 360) % 360 - 180})
    # Use the existing proxy only to synthesize a known native query result in these wire tests.
    point, direction, normal = combat.bodyContact(world, attacker, 3 - attacker)
    publish(flow, attacker, [contact(3 - attacker, position=point, direction=direction, normal=normal)])
    return world


def hitBody(flow, target):
    packets = [packet['body'] for packet in flow.result['send'] if packet['reason'] == 'combat-hit-point'
               and battle_flow.decode_battle_hit_point_notify(packet['body']).target_mid == target]
    assert len(packets) == 1
    return packets[0]


def assertContact(body, position, direction, normal):
    hit = battle_flow.decode_battle_hit_point_notify(body)
    assert hit.hit_pos == pytest.approx(position)
    assert struct.unpack_from('>4f', body, 15) == pytest.approx((*direction, 0.))
    assert struct.unpack_from('>4f', body, 31) == pytest.approx((*normal, 0.))
    for offset in (15, 31):
        vector = struct.unpack_from('>fff', body, offset)
        assert all(math.isfinite(value) for value in vector)
        assert math.hypot(*vector) == pytest.approx(1.)


@pytest.mark.parametrize('attacker', [1, 2])
@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
@pytest.mark.parametrize('yaw', [0, 90, 180, -90])
def test_each_attack_places_feedback_on_the_targets_incoming_surface(arena, attacker, intent, yaw):
    dx, dy = math.cos(math.radians(yaw)), -math.sin(math.radians(yaw))
    origin = (4., -3., .2)
    target = (origin[0] + 1.5 * dx, origin[1] + 1.5 * dy, .2)
    world = placePair(arena, attacker, origin, target, yaw)
    before = deepcopy(world)
    attack(arena, attacker, intent)
    advance(arena, combat.hitTimer(attacker))
    body = hitBody(arena, 3 - attacker)
    assertContact(body, (origin[0] + 1.15 * dx, origin[1] + 1.15 * dy, 1.2),
                  (dx, dy, 0.), (-dx, -dy, 0.))
    hit = battle_flow.decode_battle_hit_point_notify(body)
    assert (hit.attacker_mid, hit.target_mid, hit.server_time_ms) == (attacker, 3 - attacker, arena.now)
    damage = DASH_DAMAGE if intent == battle.DASH_ATTACK else NORMAL_DAMAGE
    assert hp(arena, 3 - attacker) == 100 - damage
    before['sampledAt'] = world['sampledAt']
    assert world == before


@pytest.mark.parametrize('yaw', [0, -30])
def test_off_axis_hit_uses_the_target_bearing_not_only_the_attackers_heading(arena, yaw):
    placePair(arena, 1, (0., 0., .2), (1.2, .9, .2), yaw)
    attack(arena)
    advance(arena, combat.hitTimer(1))
    assertContact(hitBody(arena, 2), (.92, .69, 1.2), (.8, .6, 0.), (-.8, -.6, 0.))


@pytest.mark.parametrize('heightDelta', [-.9, .9])
def test_height_difference_moves_contact_along_the_torso_line(arena, heightDelta):
    placePair(arena, 1, (0., 0., .2), (1.5, 0., .2 + heightDelta))
    attack(arena)
    advance(arena, combat.hitTimer(1))
    length = math.hypot(1.5, heightDelta)
    assertContact(hitBody(arena, 2), (1.15, 0., 1.2 + heightDelta * 23 / 30),
                  (1.5 / length, 0., heightDelta / length), (-1., 0., 0.))


@pytest.mark.parametrize('origin,target,point,normal', [
    ((0., 0., 1.), (.4, 0., 0.), (.08, 0., 1.8), (0., 0., 1.)),
    ((0., 0., 0.), (.15, 0., .9), (-1 / 60, 0., .9), (0., 0., -1.)),
    ((0., 0., .2), (.1, 0., .2), (-.25, 0., 1.2), (-1., 0., 0.)),
])
def test_close_or_overlapping_bodies_keep_the_point_on_the_bounded_surface(arena, origin, target, point, normal):
    placePair(arena, 1, origin, target)
    attack(arena)
    advance(arena, combat.hitTimer(1))
    delta = tuple(b - a for a, b in zip(origin, target))
    length = math.hypot(*delta)
    body = hitBody(arena, 2)
    assertContact(body, point, tuple(value / length for value in delta), normal)
    position = battle_flow.decode_battle_hit_point_notify(body).hit_pos
    assert target[2] - 1e-6 <= position[2] <= target[2] + body_collision.HEIGHT + 1e-6
    assert math.dist(position[:2], target[:2]) <= body_collision.RADIUS + 1e-6


@pytest.mark.parametrize('yaw', [0, 90, 180, -90])
@pytest.mark.parametrize('heightDelta', [-.9, 0., .9])
@pytest.mark.parametrize('separation', [0., 1e-9])
def test_coincident_xy_uses_live_heading_instead_of_a_zero_or_vertical_direction(arena, yaw, heightDelta, separation):
    placePair(arena, 2, (0., 0., .2), (separation, 0., .2 + heightDelta), yaw)
    attack(arena, 2)
    advance(arena, combat.hitTimer(2))
    dx, dy = math.cos(math.radians(yaw)), -math.sin(math.radians(yaw))
    assertContact(hitBody(arena, 1), (separation - .35 * dx, -.35 * dy, 1.2 + heightDelta),
                  (dx, dy, 0.), (-dx, -dy, 0.))


def test_feedback_uses_the_hit_frames_pose_not_the_swing_start_or_session_pose(arena):
    attack(arena)
    placePair(arena, 1, (0., 0., .2), (0., 1.5, .2), -90)
    advance(arena, combat.hitTimer(1))
    assertContact(hitBody(arena, 2), (0., 1.15, 1.2), (0., 1., 0.), (0., -1., 0.))


@pytest.mark.parametrize('attacker', [1, 2])
@pytest.mark.parametrize('intent', battle.ATTACK_CHAINS)
@pytest.mark.parametrize('yaw', [0, 90, 180, -90])
def test_charged_hit_uses_the_targets_surface_at_impact(arena, attacker, intent, yaw):
    placePair(arena, attacker, (0., 0., .2), (1.5, 0., .2))
    battle.startAction(arena, intent, actorId=attacker)
    advance(arena, battle.actionTimer(attacker))
    arena.now += 1500
    assert battle.actionState(arena, attacker)['index'] == 1
    assert combat.hitTimer(attacker) not in arena.session['pending']
    assert not any(packet['reason'] == 'combat-hit-point' for packet in arena.result['send'])
    assert hp(arena, 3 - attacker) == combat.MAX_HEALTH

    battle.releaseAction(arena, battle.ATTACK_RELEASE, actorId=attacker)
    dx, dy = math.cos(math.radians(yaw)), -math.sin(math.radians(yaw))
    placePair(arena, attacker, (4., -3., .2), (4. + 1.5 * dx, -3. + 1.5 * dy, .2), yaw)
    advance(arena, combat.hitTimer(attacker))
    assertContact(hitBody(arena, 3 - attacker), (4. + 1.15 * dx, -3. + 1.15 * dy, 1.2),
                  (dx, dy, 0.), (-dx, -dy, 0.))
    assert hp(arena, 3 - attacker) == combat.MAX_HEALTH - NORMAL_DAMAGE


@pytest.mark.parametrize('attacker', [1, 2, 12])
def test_multi_hit_computes_a_separate_contact_for_each_target(multi, attacker):
    targets = (1, 12, 13) if attacker == 2 else (2, 3, 4)
    positions = ((1.5, 0., .2), (1.2, .9, .2), (1.2, -.9, .2))
    placements = {attacker: ((0., 0., .2), 0)}
    placements.update({rid: (position, 180) for rid, position in zip(targets, positions)})
    world = sampleTargets(multi, placements)
    hits = []
    for rid in targets:
        point, direction, normal = combat.bodyContact(actors.pair(world, attacker, rid), attacker, rid)
        hits.append(contact(rid, position=point, direction=direction, normal=normal))
    publish(multi, attacker, hits)
    attack(multi, attacker)
    advance(multi, combat.hitTimer(attacker))
    expected = [((1.15, 0., 1.2), (1., 0., 0.)),
                ((.92, .69, 1.2), (.8, .6, 0.)), ((.92, -.69, 1.2), (.8, -.6, 0.))]
    for rid, (point, direction) in zip(targets, expected):
        body = hitBody(multi, rid)
        assert battle_flow.decode_battle_hit_point_notify(body).attacker_mid == attacker
        assertContact(body, point, direction, tuple(-value for value in direction))
        assert hp(multi, rid) == 80


@pytest.mark.parametrize('attacker', [1, 2])
def test_lethal_feedback_does_not_replace_the_victims_ground_position(arena, attacker):
    victim = 3 - attacker
    position = (1.5, 0., .2)
    placePair(arena, attacker, (0., 0., .2), position)
    actors.data(arena, victim)['health'] = NORMAL_DAMAGE
    attack(arena, attacker)
    advance(arena, combat.hitTimer(attacker))
    assertContact(hitBody(arena, victim), (1.15, 0., 1.2), (1., 0., 0.), (-1., 0., 0.))
    value = actors.data(arena, victim)
    assert value['life']['state'] == 'dead'
    ground = value['ground'] if victim == 1 else value
    assert ground['position'] == list(position)
