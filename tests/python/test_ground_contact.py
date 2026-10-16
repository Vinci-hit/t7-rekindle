from damage_fixture import NORMAL_DAMAGE, DASH_DAMAGE
from copy import deepcopy
import math
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, blade_contacts, combat, contracts as wire
from Business.scripts.codec import battle_flow
from blade_fixture import contact, publish
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_combat import hp
from test_melee_multi_hit import attack


ATTACKS = [*battle.ATTACK_CHAINS, battle.DASH_ATTACK]
GROUND_NORMALS = [(0., 0., 1.), (.6, 0., .8), (0., -.8, .6),
                  (math.sqrt(.75), 0., .5), (.594, 0., .792)]
GROUND_NORMALS += [tuple(-value for value in normal) for normal in GROUND_NORMALS]


def ground(flow, attacker, normal=(0., 0., -1.)):
    source = actors.pose(flow.context['botWorld'], attacker)['position']
    height = source[2] - .02 - (normal[0] * .7 + normal[1] * .3) / normal[2]
    return contact(0, along=.1, normal=normal, position=(source[0] + .7, source[1] + .3, height))


def settle(flow, attacker, hits, pipeline):
    raw = flow.context['botWorld']
    if pipeline == 'journal':
        batchId = raw.get('contactJournal', {}).get('batchId', 0) + 1
        event = native(flow, attacker, 3 - attacker,
                       phaseAt=actors.data(flow, attacker)['swing']['beganAt'], occurredAt=flow.now + 20)
        journal = raw['contactJournal']
        journal.update(batchId=batchId, events=[dict(event, **hit,
                       targetEntity=0 if hit['targetId'] == 0 else event['targetEntity']) for hit in hits])
        flow.now = journal['throughAt']
    else:
        flow.now += 20
        raw['sampledAt'] = flow.now
        publish(flow, attacker, hits)
    combat.worldUpdated(flow)


def sounds(flow):
    return [packet for packet in flow.result['send'] if packet['reason'] == 'combat-ground-hit']


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
@pytest.mark.parametrize('attacker', [1, 2])
@pytest.mark.parametrize('intent', ATTACKS)
@pytest.mark.parametrize('normal', GROUND_NORMALS)
def test_ground_and_slopes_keep_attack_window_animation_and_held_input(multi, pipeline, attacker, intent, normal):
    sample(multi)
    attack(multi, attacker, intent)
    action = battle.actionState(multi, attacker)
    action['buffered'] = dict(intent=300550, released=False)
    beforeAction = deepcopy(action)
    beforeSwing = deepcopy(actors.data(multi, attacker)['swing'])
    beforeSwing['groundSoundPlayed'] = True
    if pipeline == 'frame':
        beforeSwing['miss'] = 'blade-clear'
    deadline = multi.session['pending'][battle.actionTimer(attacker)]
    packets = deepcopy(multi.result['send'])
    for _ in range(2):
        settle(multi, attacker, [ground(multi, attacker, normal)], pipeline)
        assert battle.actionState(multi, attacker) == beforeAction
        assert actors.data(multi, attacker)['swing'] == beforeSwing
        assert multi.session['pending'][battle.actionTimer(attacker)] == deadline
        assert [packet for packet in multi.result['send'] if packet['reason'] != 'combat-ground-hit'] == packets
        assert len(sounds(multi)) == 1
        assert hp(multi, 1) == hp(multi, 2) == combat.MAX_HEALTH
    assert app.validateState(multi.state)


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
@pytest.mark.parametrize('attacker', [1, 2])
@pytest.mark.parametrize('intent', ATTACKS)
def test_ground_contact_does_not_block_later_body_contact_or_duplicate_damage(multi, pipeline, attacker, intent):
    sample(multi)
    attack(multi, attacker, intent)
    hits = [contact(3 - attacker, along=.8, normal=(0., 0., 1.)),
            ground(multi, attacker, (0., .8, -.6))]
    for _ in range(2):
        settle(multi, attacker, hits, pipeline)
        damage = DASH_DAMAGE if intent == battle.DASH_ATTACK else NORMAL_DAMAGE
        assert hp(multi, 3 - attacker) == combat.MAX_HEALTH - damage
        assert battle.actionState(multi, attacker).get('reaction') is None
    assert sum(packet['reason'] == 'combat-hit-point' for packet in multi.result['send']) == 1
    assert len(sounds(multi)) == 1
    assert not any(packet['reason'] == 'combat-terrain-hit' for packet in multi.result['send'])


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
@pytest.mark.parametrize('attacker', [1, 2])
@pytest.mark.parametrize('intent', ATTACKS)
@pytest.mark.parametrize('normal', [(-1., 0., 0.), (math.sqrt(1 - .4999 ** 2), 0., .4999),
                                   (0., 0., -1.), (0., 0., 1.)])
def test_ground_does_not_suppress_later_wall_or_ceiling_rebound(multi, pipeline, attacker, intent, normal):
    sample(multi)
    attack(multi, attacker, intent)
    floor = ground(multi, attacker)
    source = actors.pose(multi.context['botWorld'], attacker)['position']
    hits = [contact(3 - attacker, along=.9),
            contact(0, along=.6, normal=normal, material=4,
                    position=(source[0] + .6, source[1], source[2] + 1.))]
    hits.append(floor)
    settle(multi, attacker, hits, pipeline)
    assert hp(multi, 3 - attacker) == combat.MAX_HEALTH
    assert battle.actionState(multi, attacker)['reaction'] == 'terrain-rebound'
    assert sum(packet['reason'] == 'combat-terrain-hit' for packet in multi.result['send']) == 1
    assert len(sounds(multi)) == 1


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
@pytest.mark.parametrize('material', range(-1, 8))
def test_ground_sound_preserves_native_material_point_and_time(multi, pipeline, material):
    sample(multi)
    attack(multi)
    hit = dict(ground(multi, 1), material=material, direction=[.6, .8, 0.])
    occurredAt = multi.now + 20
    settle(multi, 1, [hit], pipeline)
    packet, = sounds(multi)
    assert packet['command'] == battle_flow.BATTLE_COMMAND
    body = packet['body']
    decoded = battle_flow.decode_battle_hit_point_notify(body)
    assert (decoded.attacker_mid, decoded.target_mid) == (1, 0)
    assert (decoded.attacker_weapon_id, decoded.target_weapon_id) == (wire.WEAPON_ID, 0)
    assert decoded.server_time_ms == occurredAt
    assert decoded.hit_pos == pytest.approx(hit['position'])
    assert struct.unpack_from('>4f', body, 15) == pytest.approx((*hit['direction'], 0.))
    assert struct.unpack_from('>4f', body, 31) == pytest.approx((*hit['normal'], 0.))
    assert struct.unpack_from('>i', body, 47)[0] == material
    assert body[87] == 1


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
def test_ground_sound_is_once_per_swing_across_reload_and_resets_next_swing(multi, pipeline):
    sample(multi)
    attack(multi)
    settle(multi, 1, [ground(multi, 1)], pipeline)
    saved = host_runtime.decode(host_runtime.encode(multi.state))
    assert app.migrateState(app.STATE_VERSION, saved) == multi.state
    multi.session['swing'] = deepcopy(saved['sessions']['1']['swing'])
    settle(multi, 1, [ground(multi, 1)], pipeline)
    assert len(sounds(multi)) == 1
    attack(multi)
    settle(multi, 1, [ground(multi, 1)], pipeline)
    assert len(sounds(multi)) == 2


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
def test_wall_before_ground_stops_later_ground_sound(multi, pipeline):
    sample(multi)
    attack(multi)
    settle(multi, 1, [dict(ground(multi, 1), along=.8), contact(0, along=.05)], pipeline)
    assert battle.actionState(multi, 1)['reaction'] == 'terrain-rebound'
    assert sounds(multi) == []


@pytest.mark.parametrize('target', ['body', 'wall', 'ground'])
def test_single_frame_still_rejects_duplicate_contact_slots(multi, target):
    raw = sample(multi)
    hit = ground(multi, 1) if target == 'ground' else contact(2 if target == 'body' else 0)
    publish(multi, 1, [hit, deepcopy(hit)])
    assert blade_contacts.contacts(raw, 1, multi.now) is None


def test_late_ground_sound_uses_contact_pose_and_time_without_interrupting_recovery(multi):
    raw = sample(multi)
    attack(multi)
    hit = ground(multi, 1)
    event = native(multi, attacker=1, target=2)
    event.update(hit, targetEntity=0)
    # The latest pose is below the old floor; classify against the retained contact-time pose.
    raw['actors']['1']['position'] = [0., 0., -5.]
    multi.now += 500
    battle.timer(multi, battle.actionTimer(1))
    action = deepcopy(battle.actionState(multi, 1))
    combat.worldUpdated(multi)
    packet, = sounds(multi)
    assert battle_flow.decode_battle_hit_point_notify(packet['body']).server_time_ms == event['occurredAt']
    assert battle.actionState(multi, 1) == action
    raw['contactJournal']['batchId'] += 1
    combat.worldUpdated(multi)
    assert len(sounds(multi)) == 1


@pytest.mark.parametrize('pipeline', ['frame', 'journal'])
@pytest.mark.parametrize('timing', ['before', 'after'])
def test_ground_outside_attack_window_does_not_play_sound(multi, pipeline, timing):
    raw = sample(multi)
    attack(multi)
    swing = actors.data(multi, 1)['swing']
    occurredAt = swing['beganAt'] - 1 if timing == 'before' else swing['until'] + 1
    hit = ground(multi, 1)
    if pipeline == 'journal':
        event = native(multi, attacker=1, target=2, phaseAt=swing['beganAt'], occurredAt=occurredAt)
        event.update(hit, targetEntity=0)
        multi.now = max(multi.now, raw['contactJournal']['throughAt'])
    else:
        raw['sampledAt'] = occurredAt
        multi.now = max(multi.now, occurredAt)
        publish(multi, 1, [hit])
    combat.worldUpdated(multi)
    assert sounds(multi) == []
    assert battle.actionState(multi, 1).get('reaction') is None


@pytest.mark.parametrize('flag', [None, 1, 'true'])
def test_ground_sound_marker_requires_a_boolean(flag):
    assert not combat.validSwing(dict(seq=1, epoch=1, until=100, groundSoundPlayed=flag))
