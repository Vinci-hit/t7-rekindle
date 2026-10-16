"""Physical interception, performance, chamber timing and reaction integration."""
from copy import deepcopy

import pytest

from Business.scripts import actors, app, battle, combat, damage_state, guard_state, hit_reactions, weapons
from Business.scripts.codec import move_flow
from blade_fixture import contact, publish
from test_melee_combat import arena, advance, hp, states, swing
from test_contact_journal import native
from test_bot_roster import multi, sample
from test_melee_multi_hit import attack


def weapon(flow, actorId):
    data = actors.data(flow, actorId)
    return damage_state.profile(data)['weapons'][str(weapons.equipped(data))]


def guard(flow, actorId=1):
    battle.startAction(flow, 300580, actorId=actorId)
    advance(flow, battle.actionTimer(actorId))


def collision(flow, *, bodyFirst=False, body=True):
    hits = [contact(1, defenceWeaponId=weapons.equipped(flow.session), along=.7 if bodyFirst else .2)]
    if body:
        hits.append(contact(1, along=.3 if bodyFirst else .8))
    publish(flow, 2, hits)
    combat.worldUpdated(flow)


@pytest.mark.parametrize('bodyFirst', [False, True])
def test_actual_contact_order_not_guard_pose_decides_interception(arena, bodyFirst):
    guard(arena)
    swing(arena, 2)
    collision(arena, bodyFirst=bodyFirst)
    assert hp(arena, 1) == (80 if bodyFirst else 100)
    assert bool(battle.actionState(arena, 2).get('reaction')) is not bodyFirst
    assert arena.session['guardMeter']['value'] == (5 if bodyFirst else 4)


@pytest.mark.parametrize('attackGrade,defenceGrade,loss,stun', [
    ('B', 'B', 0, 0), ('A', 'B', 0, 250), ('B', 'D', 5, 250),
    ('A', 'D', 10, 250), ('SS', 'D', 15, 250), ('E', 'F', 0, 250),
])
def test_general_performance_rules_and_break_are_not_meter_or_shield_exhaustion(arena, attackGrade, defenceGrade, loss, stun):
    weapon(arena, 2)['attackGrade'] = attackGrade
    weapon(arena, 1)['defenceGrade'] = defenceGrade
    guard(arena)
    swing(arena, 2)
    originalSwing = actors.data(arena, 2)['swing']
    collision(arena)
    assert hp(arena, 1) == 100 - loss
    if loss:
        assert arena.session['guardMeter']['value'] == 5
        assert originalSwing['hits'] == [1]
        assert battle.actionState(arena, 2)['index'] == 2
        assert (1, 360) in states(arena)
    else:
        assert arena.session['pending'][battle.ACTION_TIMER] == arena.now + stun
        assert arena.session['action']['reaction'] == 'parry-success'


@pytest.mark.parametrize('actorId', [1, 2])
@pytest.mark.parametrize('elapsed,minimumMs,remainingMs', [
    (50, 0, 200), (50, 100, 200), (50, 250, 250), (250, 0, 0), (300, 0, 0),
])
def test_repeated_guard_feedback_preserves_remaining_lock(arena, actorId, elapsed, minimumMs, remainingMs):
    guard(arena, actorId)
    battle.parryReaction(arena, actorId, 3 - actorId, success=True, minimumMs=250)
    arena.now += elapsed
    battle.parryReaction(arena, actorId, 3 - actorId, success=True, minimumMs=minimumMs)
    action = battle.actionState(arena, actorId)
    name = battle.actionTimer(actorId)
    assert arena.session['pending'][name] == arena.now + remainingMs
    assert action['durationMs'] == remainingMs
    assert action['reaction'] == 'parry-success'
    advance(arena, name)
    assert 'reaction' not in action
    assert action['index'] == 1


def test_exhaustion_absorbs_triggering_hit_locks_and_resets_meter(arena):
    arena.session['guardMeter']['value'] = 1
    guard(arena)
    swing(arena, 2)
    collision(arena)
    assert hp(arena, 1) == 100
    assert arena.session['guardMeter']['value'] == 2
    assert arena.session['hurtUntil'] == arena.now + 1000
    assert arena.session['movementUntil'] == arena.now + 1000
    assert (1, hit_reactions.EXHAUSTION_STATE) in states(arena)
    assert arena.session['action']['intent'] == 0
    assert actors.data(arena, 2)['guardMeter']['value'] == 5


@pytest.mark.parametrize('elapsed,blocked', [(49, False), (50, True), (200, True), (201, False)])
def test_chamber_only_blocks_during_prepare_and_preserves_own_attack(arena, elapsed, blocked):
    swing(arena, 2)
    battle.startAction(arena, 300560, actorId=1)
    original = deepcopy(arena.session['action'])
    arena.now += elapsed
    arena.context['botWorld']['sampledAt'] = arena.now
    collision(arena)
    assert hp(arena, 1) == (100 if blocked else 80)
    if blocked:
        assert arena.session['action'] == original
        assert arena.session['action']['index'] == 0


def test_holding_attack_is_not_sustained_chamber_and_wrong_weapon_is_not_a_body_hit(arena):
    battle.startAction(arena, 300560)
    advance(arena, battle.ACTION_TIMER)
    swing(arena, 2)
    collision(arena, body=False)
    assert hp(arena, 1) == 100
    assert not actors.data(arena, 2)['swing']['hits']
    publish(arena, 2, [contact(1)])
    combat.worldUpdated(arena)
    assert hp(arena, 1) == 80


@pytest.mark.parametrize('attackGrade,blocked,loss', [('S', True, 0), ('SS', False, 5)])
def test_chamber_compares_attack_grade_including_b_without_exception(arena, attackGrade, blocked, loss):
    weapon(arena, 2)['attackGrade'] = attackGrade
    weapon(arena, 1).update(attackGrade='C', defenceGrade='F')
    swing(arena, 2)
    battle.startAction(arena, 300560)
    arena.now += 100
    arena.context['botWorld']['sampledAt'] = arena.now
    collision(arena)
    assert hp(arena, 1) == 100 - loss
    assert bool(battle.actionState(arena, 2).get('reaction')) is blocked


@pytest.mark.parametrize('armor,airborne,state,movementMs', [
    ('light', False, 35, 250), ('medium', False, 35, 180), ('heavy', False, 35, 0), ('heavy', True, 40, 1000),
])
def test_b_performance_separates_movement_lock_from_action_interrupt(multi, armor, airborne, state, movementMs):
    sample(multi)
    weapon(multi, 2)['attackGrade'] = 'B'
    damage_state.profile(multi.session)['armorType'] = armor
    attack(multi, 2)
    event = native(multi)
    event['targetAirborne'] = airborne
    multi.now = int(event['occurredAt'] + 10)
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 80 and (1, state) in states(multi)
    assert multi.session.get('movementUntil', multi.now) == multi.now + movementMs
    packets = [p for p in multi.result['send'] if p['reason'] == 'combat-movement-lock']
    assert bool(packets) == bool(movementMs)
    if movementMs:
        assert move_flow.decode_move_notify_active(packets[-1]['body']).active == 0
        advance(multi, hit_reactions.movementTimer(1))
        assert 'movementUntil' not in multi.session
        assert move_flow.decode_move_notify_active(multi.result['send'][-1]['body']).active == 1


@pytest.mark.parametrize('armor,interrupt', [('light', True), ('medium', True), ('heavy', False)])
def test_f_performance_only_preserves_heavy_armor_actions(arena, armor, interrupt):
    weapon(arena, 2)['attackGrade'] = 'F'
    damage_state.profile(arena.session)['armorType'] = armor
    battle.startAction(arena, 300560)
    before = deepcopy(arena.session['action'])
    swing(arena, 2)
    publish(arena, 2, [contact(1)])
    combat.worldUpdated(arena)
    assert hp(arena, 1) == 80
    assert ('hurtUntil' in arena.session) is interrupt
    if not interrupt:
        assert arena.session['action'] == before


def test_late_chamber_uses_contact_phase_not_dispatch_and_does_not_rewind_current_attack(multi):
    sample(multi)
    attack(multi, 2)
    battle.startAction(multi, 300560)
    event = native(multi, occurredAt=multi.now + 100)
    event.update(targetState=235, targetPhaseAt=multi.now, defenceWeaponId=weapons.equipped(multi.session))
    multi.now += 600
    app.handleTimer(multi, battle.ACTION_TIMER)
    before = deepcopy(multi.session['action'])
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100 and multi.session['action'] == before


def test_attack_performance_and_weight_remain_frozen_after_weapon_change(arena):
    weapon(arena, 2).update(attackGrade='D', weight=2)
    guard(arena)
    swing(arena, 2)
    weapon(arena, 2).update(attackGrade='SS', weight=100)
    collision(arena)
    assert hp(arena, 1) == 100
    assert arena.session['guardMeter']['value'] == 3.75


def test_inactive_or_wrong_weapon_contact_alone_never_deals_body_damage(arena):
    guard(arena)
    swing(arena, 2)
    publish(arena, 2, [contact(1, defenceWeaponId=weapons.FIST_ID)])
    combat.worldUpdated(arena)
    assert hp(arena, 1) == 100 and arena.session['guardMeter']['value'] == 5


def test_released_historical_guard_blocks_only_contacts_before_release(multi):
    sample(multi)
    guard(multi)
    attack(multi, 2)
    event = native(multi)
    event.update(targetState=223, targetPhaseAt=multi.now - 200, defenceWeaponId=weapons.equipped(multi.session))
    multi.now = int(event['occurredAt'] + 5)
    battle.releaseAction(multi, battle.BLOCK_RELEASE)
    before = deepcopy(multi.session['action'])
    multi.now += 10
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100 and multi.session['action'] == before


def test_friendly_weapon_contact_does_not_spend_enemy_cleave_or_weaken_attack(multi):
    sample(multi)
    attack(multi, 1)
    ally = native(multi, 1, 12)
    ally.update(defenceWeaponId=weapons.equipped(actors.data(multi, 12)), targetState=223)
    enemy = native(multi, 1, 2, occurredAt=ally['occurredAt'] + 1)
    journal = multi.context['botWorld']['contactJournal']
    journal['events'] = [ally, enemy]
    multi.now = int(journal['throughAt'])
    combat.worldUpdated(multi)
    assert hp(multi, 12) == 100 and hp(multi, 2) == 80
    assert multi.session['swing']['allies'] == 0
