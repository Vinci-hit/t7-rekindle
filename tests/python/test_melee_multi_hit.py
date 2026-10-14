from damage_fixture import NORMAL_DAMAGE, DASH_DAMAGE
from copy import deepcopy

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, bot_life, combat, contracts as wire
from test_bot_roster import multi, sample
from test_melee_combat import advance, hp
from blade_fixture import contact, publish


def sampleTargets(flow, placements):
    world = sample(flow, placements)
    for rid in actors.ids(flow.session):
        world['actors'][str(rid)]['targetTerrain'] = {
            str(target): dict(ready=True, hit=False) for target in actors.enemies(flow, rid, world)}
    return world


def attack(flow, attacker=1, intent=300560):
    battle.startAction(flow, intent, released=True, actorId=attacker)
    if intent != battle.DASH_ATTACK:
        advance(flow, battle.actionTimer(attacker))
    assert battle.actionState(flow, attacker)['index'] == battle.strikeIndex(intent)


def cluster(flow, attacker=1):
    targets = (1, 12, 13) if attacker == 2 else (2, 3, 4)
    ally = 3 if attacker == 2 else 12 if attacker == 1 else 1
    placements = {attacker: ((0, 0, .2), 0), ally: ((.5, 0, .2), 180)}
    placements.update({rid: ((1 + index * .5, 0, .2), 180) for index, rid in enumerate(targets)})
    world = sampleTargets(flow, placements)
    publish(flow, attacker, [contact(rid, along=(index + 1) / 5) for index, rid in enumerate(targets)]
            + [contact(ally, along=.1)])
    return world, targets, ally


@pytest.mark.parametrize('attacker', [1, 2, 12])
@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
def test_one_swing_hits_every_nearby_enemy_once_without_friendly_fire(multi, attacker, intent):
    _, targets, ally = cluster(multi, attacker)
    attack(multi, attacker, intent)
    advance(multi, combat.hitTimer(attacker))
    damage = DASH_DAMAGE if intent == battle.DASH_ATTACK else NORMAL_DAMAGE
    assert [hp(multi, rid) for rid in targets] == [100 - damage] * len(targets)
    assert hp(multi, ally) == hp(multi, attacker) == 100
    assert actors.data(multi, attacker)['swing']['hits'] == [ally, *targets]
    for _ in range(2):
        advance(multi, combat.hitTimer(attacker))
    assert [hp(multi, rid) for rid in targets] == [100 - damage] * len(targets)
    for rid in targets:
        assert sum(p['reason'] == f'combat-health-{rid}' for p in multi.result['send']) == 1
    assert app.validateState(multi.state)


def test_target_entering_later_in_the_same_contact_window_is_hit_once(multi):
    world = sampleTargets(multi, {1: ((0, 0, .2), 0), 2: ((1, 0, .2), 180), 3: ((4, 0, .2), 180)})
    publish(multi, 1, [contact(2)])
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert (hp(multi, 2), hp(multi, 3)) == (80, 100)
    world['actors']['3']['position'] = [1.5, 0, .2]
    publish(multi, 1, [contact(2), contact(3)])
    advance(multi, combat.hitTimer(1))
    assert (hp(multi, 2), hp(multi, 3)) == (80, 80)
    # Serialization/reload retains per-swing contacts instead of allowing a second hit.
    saved = host_runtime.decode(host_runtime.encode(multi.state))
    assert app.migrateState(app.STATE_VERSION, saved) == multi.state
    multi.session['swing'] = deepcopy(saved['sessions']['1']['swing'])
    advance(multi, combat.hitTimer(1))
    assert (hp(multi, 2), hp(multi, 3)) == (80, 80)
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert (hp(multi, 2), hp(multi, 3)) == (60, 60)


@pytest.mark.parametrize('failure', ['missing', 'unready', 'invalid'])
def test_each_target_requires_a_valid_native_blade_result(multi, failure):
    world, targets, _ = cluster(multi)
    trace = world['blades']['1']
    original = deepcopy(trace)
    if failure == 'missing': trace['contacts'] = [hit for hit in trace['contacts'] if hit['targetId'] != 2]
    if failure == 'unready': trace['ready'] = False
    if failure == 'invalid': trace['contacts'][0]['normal'] = [0, 0, 0]
    # Clear AI terrain rays do not replace absent or malformed blade contacts.
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert [hp(multi, rid) for rid in targets] == ([100, 80, 80] if failure == 'missing' else [100] * 3)
    world['blades']['1'] = original
    advance(multi, combat.hitTimer(1))
    assert [hp(multi, rid) for rid in targets] == [80, 80, 80]


@pytest.mark.parametrize('failure', ['no-contact', 'unready'])
def test_multi_hit_keeps_geometry_and_actor_availability_checks(multi, failure):
    world, targets, _ = cluster(multi)
    excluded = world['actors']['3']
    if failure == 'no-contact':
        world['blades']['1']['contacts'] = [hit for hit in world['blades']['1']['contacts'] if hit['targetId'] != 3]
    if failure == 'unready': excluded['ready'] = False
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert [hp(multi, rid) for rid in targets] == [80, 100, 80]


@pytest.mark.parametrize('blockedTarget', [2, 3])
@pytest.mark.parametrize('block', ['terrain', 'parry'])
def test_terrain_or_parry_interrupts_remaining_contacts_in_sweep_order(multi, blockedTarget, block):
    world, targets, _ = cluster(multi)
    if block == 'terrain':
        world['blades']['1']['contacts'].append(contact(0, material=4, along=(blockedTarget - 1) / 5 - .05))
    else:
        battle.startAction(multi, 300580, actorId=blockedTarget)
        advance(multi, battle.actionTimer(blockedTarget))
        world['blades']['1']['contacts'].append(contact(
            blockedTarget, defenceWeaponId=wire.WEAPON_ID, along=(blockedTarget - 1) / 5 - .05))
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert [hp(multi, rid) for rid in targets] == ([100, 100, 100] if blockedTarget == 2 else [80, 100, 100])
    assert battle.actionState(multi, 1)['reaction'] == ('terrain-rebound' if block == 'terrain' else 'parried')
    assert combat.hitTimer(1) not in multi.session['pending']
    before = [hp(multi, rid) for rid in targets]
    app.handleTimer(multi, combat.hitTimer(1))
    assert [hp(multi, rid) for rid in targets] == before


def test_one_swing_can_kill_multiple_enemies_and_announce_each_kill(multi):
    _, targets, _ = cluster(multi)
    for rid in targets:
        actors.data(multi, rid)['health'] = NORMAL_DAMAGE
    attack(multi)
    advance(multi, combat.hitTimer(1))
    assert multi.session['killStreak'] == len(targets)
    assert sum(p['reason'] == 'combat-kill' for p in multi.result['send']) == len(targets)
    for rid in targets:
        assert not actors.active(multi, rid)
        assert actors.timerName(bot_life.RESPAWN_TIMER, rid) in multi.session['pending']
    assert app.validateState(multi.state)


def test_successful_multi_hit_does_not_log_a_miss_when_window_ends(multi):
    cluster(multi)
    attack(multi)
    while combat.hitTimer(1) in multi.session['pending']:
        advance(multi, combat.hitTimer(1))
    assert not any('combat-miss attacker=1' in log["message"] for log in multi.result['logs'])


@pytest.mark.parametrize('hits', [None, '2', [True], [0], [21], [2, 2], ['2']])
def test_invalid_swing_hit_registry_is_rejected(hits):
    assert not combat.validState(dict(health=100, swing=dict(seq=1, epoch=1, until=100, hits=hits)))


def test_swing_without_hit_registry_requires_restart(multi):
    cluster(multi)
    attack(multi)
    multi.session['swing'].pop('hits', None)
    assert not app.validateState(multi.state)
