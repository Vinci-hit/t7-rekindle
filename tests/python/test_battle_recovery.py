from copy import deepcopy
import struct

import pytest

from Business.scripts import app, battle, combat, scene
from test_battle_actions import ATTACKS, BLOCKS, sendAction
from test_melee_combat import advance, arena, states, swing


RECOVERIES = ('hurt', 'parried', 'terrain-rebound')


def enterRecovery(flow, kind):
    if kind == 'hurt':
        combat.applyHit(flow, 2, 1, flow.context['botWorld'], intent=300560)
        return combat.hurtTimer(1)
    if kind == 'parried':
        battle.parryReaction(flow, 1, 2, success=False, intent=300560)
    else:
        battle.terrainReaction(flow, 1, intent=300560)
    return battle.ACTION_TIMER


def releaseFor(intent):
    return battle.ATTACK_RELEASE if intent in battle.ATTACK_CHAINS else battle.BLOCK_RELEASE


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('intent,chain', ATTACKS + BLOCKS)
@pytest.mark.parametrize('pressedBefore', [False, True])
@pytest.mark.parametrize('released', [False, True])
def test_recovery_keeps_held_input_and_only_starts_it_after_unlock(
        arena, kind, intent, chain, pressedBefore, released):
    swing(arena)
    if pressedBefore:
        sendAction(arena, intent)
    name = enterRecovery(arena, kind)
    deadline = arena.session['pending'][name]
    previous = states(arena)
    timers = deepcopy(arena.result['timers'])
    arena.now += 10
    if not pressedBefore:
        sendAction(arena, intent)
    if released:
        sendAction(arena, releaseFor(intent))
    assert battle.actionState(arena)['buffered'] == {'intent': intent, 'released': released}
    assert states(arena) == previous
    assert arena.result['timers'] == timers
    assert arena.session['pending'][name] == deadline
    arena.now = deadline - 1
    app.handleTimer(arena, name)
    assert states(arena) == previous

    advance(arena, name)
    action = battle.actionState(arena)
    assert 'hurtUntil' not in arena.session
    assert 'reaction' not in action and 'buffered' not in action
    assert states(arena) == [*previous, (1, 2 if released else chain[0])]
    if released:
        assert action['index'] == -1
        assert battle.ACTION_TIMER not in arena.session['pending']
        return
    assert action['intent'] == intent and action['index'] == 0 and not action['released']
    assert arena.session['pending'][battle.ACTION_TIMER] == deadline + battle.phaseMs(intent, 0)
    advance(arena, battle.ACTION_TIMER)
    assert states(arena)[-1] == (1, chain[1])
    assert battle.ACTION_TIMER not in arena.session['pending']
    sendAction(arena, releaseFor(intent))
    assert states(arena)[-1] == (1, chain[2] if intent in battle.ATTACK_CHAINS else 2)


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('intent,_', ATTACKS + BLOCKS)
def test_recovery_drops_input_released_before_the_interrupt(arena, kind, intent, _):
    swing(arena)
    sendAction(arena, intent)
    sendAction(arena, releaseFor(intent))
    name = enterRecovery(arena, kind)
    assert 'buffered' not in battle.actionState(arena)
    advance(arena, name)
    assert states(arena)[-1] == (1, 2)
    assert battle.actionState(arena)['index'] == -1


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('released', [False, True])
def test_recovery_last_press_wins_and_unrelated_release_does_not_cancel_it(arena, kind, released):
    name = enterRecovery(arena, kind)
    deadline = arena.session['pending'][name]
    previous = states(arena)
    for intent in (300550, 300600, 300540, battle.BLOCK_RELEASE, 300670, battle.DASH_ATTACK):
        sendAction(arena, intent)
    if released:
        sendAction(arena, battle.ATTACK_RELEASE)
    assert battle.actionState(arena)['buffered'] == {'intent': 300540, 'released': released}
    assert states(arena) == previous
    assert arena.session['pending'][name] == deadline
    advance(arena, name)
    assert states(arena) == [*previous, (1, 2 if released else 253)]
    assert 'buffered' not in battle.actionState(arena)


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('intent,chain', ATTACKS + BLOCKS)
def test_recovery_repress_after_release_queues_a_new_held_input(arena, kind, intent, chain):
    name = enterRecovery(arena, kind)
    sendAction(arena, intent)
    sendAction(arena, releaseFor(intent))
    sendAction(arena, intent)
    previous = states(arena)
    advance(arena, name)
    assert states(arena) == [*previous, (1, chain[0])]


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('intent,chain', ATTACKS + BLOCKS)
def test_another_hit_preserves_held_input_until_the_latest_hurt_deadline(arena, kind, intent, chain):
    originalTimer = enterRecovery(arena, kind)
    originalDeadline = arena.session['pending'][originalTimer]
    sendAction(arena, intent)
    arena.now = originalDeadline - 1
    name = enterRecovery(arena, 'hurt')
    latestDeadline = arena.session['pending'][name]
    assert latestDeadline == arena.now + combat.HURT_MS
    assert battle.actionState(arena)['buffered'] == {'intent': intent, 'released': False}
    assert 'reaction' not in battle.actionState(arena)
    assert battle.ACTION_TIMER not in arena.session['pending']
    previous = states(arena)
    arena.now = originalDeadline
    app.handleTimer(arena, originalTimer)
    assert states(arena) == previous
    assert arena.session['pending'][name] == latestDeadline
    advance(arena, name)
    assert states(arena) == [*previous, (1, chain[0])]
    assert 'hurtUntil' not in arena.session


@pytest.mark.parametrize('released', [False, True])
def test_hurt_preserves_input_while_waiting_for_a_fresh_pose(arena, released):
    name = enterRecovery(arena, 'hurt')
    sendAction(arena, 300580)
    previous = states(arena)
    advance(arena, name, fresh=False)
    assert states(arena) == previous
    assert 'hurtUntil' in arena.session
    assert battle.actionState(arena)['buffered'] == {'intent': 300580, 'released': False}
    assert arena.session['pending'][name] == arena.now + combat.CONTACT_MS
    if released:
        sendAction(arena, battle.BLOCK_RELEASE)
    advance(arena, name)
    assert states(arena) == [*previous, (1, 2 if released else 305)]


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('invalid', ['disabled', 'epoch'])
def test_recovery_discards_buffer_when_the_actor_or_instance_is_unavailable(arena, kind, invalid):
    name = enterRecovery(arena, kind)
    sendAction(arena, 300550)
    previous = states(arena)
    if invalid == 'disabled':
        arena.session['groundEnabled'] = False
    else:
        arena.context['botWorld']['instanceStartedAt'] += 1
    advance(arena, name)
    assert states(arena) == previous
    assert 'buffered' not in battle.actionState(arena)
    assert battle.actionState(arena)['index'] == -1
    assert name not in arena.session['pending']


@pytest.mark.parametrize('kind', RECOVERIES)
@pytest.mark.parametrize('transition', ['death', 'leave'])
def test_death_or_leave_discards_recovery_input_and_obsolete_timers(arena, kind, transition):
    name = enterRecovery(arena, kind)
    deadline = arena.session['pending'][name]
    sendAction(arena, 300550)
    if transition == 'death':
        arena.session['health'] = combat.DAMAGE
        combat.applyHit(arena, 2, 1, arena.context['botWorld'], intent=300560)
    else:
        scene.leave(arena, struct.pack('>Hb', 2, 0))
    assert 'buffered' not in battle.actionState(arena)
    assert name not in arena.session['pending']
    previous = deepcopy(arena.result['send'])
    arena.now = deadline
    app.handleTimer(arena, name)
    assert arena.result['send'] == previous
