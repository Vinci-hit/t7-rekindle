from copy import deepcopy

import pytest

from Business.scripts import actors, app, battle, bot, combat
from Business.scripts.codec import battle_flow
from blade_fixture import contact, publish
from test_bot_roster import multi, sample
from test_melee_combat import hp
from test_melee_multi_hit import attack
from dash_fixture import profile


def native(flow, attacker=2, target=1, *, phaseAt=None, occurredAt=None):
    raw = flow.context['botWorld']
    phaseAt = flow.now if phaseAt is None else phaseAt
    occurredAt = phaseAt + 30 if occurredAt is None else occurredAt
    action = battle.actionState(flow, attacker)
    for rid, pose in raw['actors'].items():
        pose.update(entity=100 + int(rid), state=2, seq=1, phaseAt=phaseAt)
    raw['actors'][str(attacker)].update(state=battle.attackChain(action['intent'])[battle.strikeIndex(action['intent'])],
                                       seq=action['seq'])
    source, dest = raw['actors'][str(attacker)], raw['actors'][str(target)]
    event = dict(contact(target), actorId=attacker, entity=source['entity'], targetEntity=dest['entity'],
                 state=source['state'], seq=source['seq'], phaseAt=phaseAt, occurredAt=occurredAt,
                 sourcePosition=source['position'], targetPosition=dest['position'],
                 sourceHeading=source['heading'], targetHeading=dest['heading'],
                 targetState=2, targetSeq=1, targetPhaseAt=phaseAt)
    raw['contactJournal'] = dict(throughAt=occurredAt + 10, frames=12, dropped=0, batchId=1, events=[event])
    return event


@pytest.mark.parametrize('intent', [*battle.ATTACK_CHAINS, battle.DASH_ATTACK])
def test_native_contact_survives_late_business_dispatch_and_visual_start_delay(multi, intent):
    sample(multi)
    attack(multi, 2, intent=intent)
    start = multi.now + 230
    end = combat.contactEndMs(intent, dash=battle.actionState(multi, 2).get('dash'))
    event = native(multi, phaseAt=start, occurredAt=start + end - 5)
    multi.now = event['occurredAt'] + 350
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100 - (40 if intent == battle.DASH_ATTACK else 20)
    hit = next(packet for packet in multi.result['send'] if packet['reason'] == 'combat-hit-point')
    assert battle_flow.decode_battle_hit_point_notify(hit['body']).server_time_ms == event['occurredAt']
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100 - (40 if intent == battle.DASH_ATTACK else 20)


@pytest.mark.parametrize('nextIntent', [None, 300550, 300580])
def test_recovery_transition_or_buffered_followup_keeps_contacts_waiting_in_native_journal(multi, nextIntent):
    sample(multi)
    attack(multi, 2)
    native(multi, occurredAt=multi.now + 400)
    if nextIntent is not None:
        battle.bufferActionInput(battle.actionState(multi, 2), nextIntent)
    multi.now += 650
    app.handleTimer(multi, battle.actionTimer(2))
    assert battle.actionState(multi, 2)['index'] == (3 if nextIntent is None else 0)
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 80


@pytest.mark.parametrize('elapsed,hit', [(199, False), (200, True), (800, True), (801, False)])
def test_delayed_dash_uses_original_contact_window_after_profile_change(multi, elapsed, hit):
    sample(multi)
    multi.context['dashAttack'] = profile(durationMs=1500, moveMs=700, contactStartMs=200, contactEndMs=800)
    attack(multi, 2, battle.DASH_ATTACK)
    event = native(multi, phaseAt=multi.now + 230, occurredAt=multi.now + 230 + elapsed)
    multi.now += 1600
    app.handleTimer(multi, battle.actionTimer(2))
    multi.context['dashAttack'] = profile(contactEndMs=100)
    combat.worldUpdated(multi)
    assert hp(multi, 1) == (60 if hit else 100)
    assert event['seq'] != battle.actionState(multi, 2)['seq']


@pytest.mark.parametrize('elapsed,hit', [(199, False), (200, True), (800, True), (801, False)])
def test_single_frame_dash_contact_uses_native_phase_clock(multi, elapsed, hit):
    raw = sample(multi)
    multi.context['dashAttack'] = profile(durationMs=1500, moveMs=700, contactStartMs=200, contactEndMs=800)
    attack(multi, 2, battle.DASH_ATTACK)
    phaseAt = multi.now + 230
    raw['actors']['2'].update(state=439, seq=battle.actionState(multi, 2)['seq'], phaseAt=phaseAt)
    multi.now = raw['sampledAt'] = phaseAt + elapsed
    publish(multi, 2, [contact(1)])
    combat.worldUpdated(multi)
    assert hp(multi, 1) == (60 if hit else 100)


@pytest.mark.parametrize('invalid', ['before-strike', 'after-window', 'wrong-seq', 'respawn', 'epoch', 'after-interrupt'])
def test_journal_never_turns_stale_or_outside_window_contacts_into_hits(multi, invalid):
    sample(multi)
    attack(multi, 2)
    event = native(multi)
    if invalid == 'before-strike': event['occurredAt'] = event['phaseAt'] - 1
    elif invalid == 'after-window': event['occurredAt'] = event['phaseAt'] + 541
    elif invalid == 'wrong-seq': event['seq'] += 1
    elif invalid == 'respawn': multi.context['botWorld']['actors']['1']['entity'] += 1
    elif invalid == 'epoch': multi.context['botWorld']['instanceStartedAt'] += 1
    else: battle.resetAction(multi, 2)
    multi.now += 700
    multi.context['botWorld']['contactJournal']['throughAt'] = multi.now
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100


def test_contact_before_interrupt_is_settled_even_when_delivered_after_interrupt(multi):
    sample(multi)
    attack(multi, 2)
    native(multi)
    multi.now += 50
    battle.resetAction(multi, 2)
    multi.now += 100
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 80


def test_early_clear_does_not_close_the_last_partial_contact_interval(multi):
    sample(multi)
    attack(multi, 2)
    end = actors.data(multi, 2)['swing']['until']
    multi.now = end - 30
    sample(multi)
    publish(multi, 2, [])
    combat.checkHit(multi, 2)
    assert 'swing' in actors.data(multi, 2)
    multi.now = end + 20
    multi.context['botWorld']['sampledAt'] = end - 5
    publish(multi, 2, [contact(1)])
    combat.checkHit(multi, 2)
    assert hp(multi, 1) == 80


@pytest.mark.parametrize('kind', ['action', 'release', 'hurt'])
def test_ai_pulse_never_consumes_fresh_native_animation_timers(multi, kind):
    sample(multi)
    if kind == 'action':
        battle.startAction(multi, 300560, released=True, actorId=2)
        name = battle.actionTimer(2)
    elif kind == 'release':
        battle.startAction(multi, 300580, actorId=2)
        battle.timer(multi, battle.actionTimer(2))
        name = bot.RELEASE_TIMER
        multi.later(name, 50)
    else:
        actors.data(multi, 2)['hurtUntil'] = multi.now + 50
        name = combat.hurtTimer(2)
        multi.later(name, 50)
    multi.now = multi.session['pending'][name] + 1
    sample(multi)
    before = deepcopy(actors.data(multi, 2))
    bot.timer(multi, actors.PULSE_TIMER)
    assert name in multi.session['pending']
    assert actors.data(multi, 2) == before


def test_ai_decisions_yield_between_actors_and_leave_overdue_action_deadline_alone(multi, monkeypatch):
    sample(multi)
    calls = []
    monkeypatch.setattr(bot, 'think', lambda flow, actorId: calls.append(actorId))
    for rid in (2, 3, 4):
        multi.later(actors.timerName(bot.THINK_TIMER, rid), 0)
    battle.startAction(multi, 300580, actorId=20)
    bot.timer(multi, actors.PULSE_TIMER)
    assert calls == []
    app.handleTimer(multi, bot.DECISION_TIMER)
    assert calls == [2]
    app.handleTimer(multi, battle.actionTimer(20))
    assert battle.actionState(multi, 20)['index'] == 1
    app.handleTimer(multi, bot.DECISION_TIMER)
    assert calls == [2, 3]
    app.handleTimer(multi, bot.DECISION_TIMER)
    assert calls == [2, 3, 4]
    assert bot.DECISION_TIMER not in multi.session['pending']


def test_wall_order_uses_contact_time_not_mailbox_order(multi):
    sample(multi)
    attack(multi, 2)
    event = native(multi, occurredAt=multi.now + 40)
    wall = dict(event, targetId=0, targetEntity=0, occurredAt=multi.now + 20)
    multi.context['botWorld']['contactJournal']['events'] = [event, wall]
    multi.now += 100
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100
    assert battle.actionState(multi, 2)['reaction'] == 'terrain-rebound'


def test_native_hold_at_contact_time_can_parry_after_business_release(multi):
    sample(multi, {1: ((0., 0., .2), 0), 2: ((1., 0., .2), 180)})
    battle.startAction(multi, 300580)
    battle.timer(multi, battle.actionTimer(1))
    attack(multi, 2)
    event = native(multi)
    event['targetState'] = battle.BLOCK_CHAINS[300580][1]
    multi.now += 50
    battle.releaseAction(multi, battle.BLOCK_RELEASE)
    combat.worldUpdated(multi)
    assert hp(multi, 1) == 100
    assert any('combat-parry' in line["message"] for line in multi.result['logs'])


def test_global_contact_order_interrupts_later_swing_even_if_input_batch_is_reversed(multi):
    sample(multi)
    attack(multi, 2)
    attack(multi, 1)
    first = native(multi, attacker=1, target=2, occurredAt=multi.now + 10)
    later = native(multi, attacker=2, target=1, occurredAt=multi.now + 30)
    multi.context['botWorld']['contactJournal']['events'] = [later, first]
    multi.now += 100
    combat.worldUpdated(multi)
    assert hp(multi, 2) == 80
    assert hp(multi, 1) == 100
