import pytest

from Business.scripts import battle, combat, contracts as wire
from test_battle_actions import makeFlow, sendAction, advanceAction
from test_bot_roster import multi, sample
from test_contact_journal import native
from test_melee_multi_hit import attack
from test_melee_combat import hp


def messages(flow, prefix):
    return [item['message'] for item in flow.result['logs'] if item['message'].startswith(prefix)]


@pytest.mark.parametrize('targetState,heading,reason,health', [
    (308, 180, 'not-holding', 100),
    (224, 180, 'success', 100),
    (226, 180, 'success', 100),
    (223, 180, 'success', 100),
    (224, 0, 'success', 100),
])
def test_guard_diagnostic_uses_historical_contact_not_current_hold(multi, targetState, heading, reason, health):
    sample(multi, {1: ((1, 0, .2), heading), 2: ((0, 0, .2), 0)})
    battle.startAction(multi, 300590)
    battle.timer(multi, battle.ACTION_TIMER)
    attack(multi, 2, intent=300550)
    event = native(multi)
    event.update(targetState=targetState, targetSeq=7, defenceWeaponId=wire.WEAPON_ID)
    multi.now = event['occurredAt'] + 350
    combat.worldUpdated(multi)
    assert hp(multi, 1) == health
    logs = messages(multi, 'combat-guard ')
    assert len(logs) == 1
    assert f'reason={reason}' in logs[0]
    assert f'targetState={targetState}' in logs[0] and 'targetSeq=7' in logs[0]
    assert f"occurredAt={event['occurredAt']}" in logs[0] and 'contactAgeMs=350' in logs[0]
    combat.worldUpdated(multi)
    assert messages(multi, 'combat-guard ') == logs


def test_defence_input_reports_accept_ignore_release_and_state_sequence():
    flow = makeFlow()
    sendAction(flow, 300590)
    advanceAction(flow)
    sendAction(flow, 300610)
    sendAction(flow, battle.BLOCK_RELEASE)
    logs = messages(flow, 'battle-input ')
    assert len(logs) == 3
    assert 'result=accepted' in logs[0]
    assert 'result=ignored' in logs[1] and 'reason=action-active' in logs[1]
    assert 'result=released' in logs[2]
    states = messages(flow, 'battle-state ')
    assert len(states) == 3
    assert 'state=308' in states[0] and 'seq=' in states[0]
    assert 'state=224' in states[1] and 'state=2' in states[2]


def test_defence_buffer_diagnostic_does_not_advance_hurt_recovery():
    flow = makeFlow(hurtUntil=1400)
    sendAction(flow, 300610)
    assert 'result=buffered' in messages(flow, 'battle-input ')[0]
    assert 'reason=hurt' in messages(flow, 'battle-input ')[0]
    assert flow.session['hurtUntil'] == 1400
    assert not messages(flow, 'battle-state ')


def test_parry_check_distinguishes_missing_heading_and_released_guard():
    snapshot = dict(sourceId=2, targetId=1, bot=[0., 0., 0.], player=[1., 0., 0.])
    attack = dict(intent=300550)
    defence = dict(intent=300590, index=1, released=False)
    assert combat.parryCheck(attack, defence, snapshot, 2, 1) == ('heading-unavailable', None)
    assert combat.parryCheck(attack, dict(defence, released=True), snapshot, 2, 1) == ('not-holding', None)
    assert combat.facingCosine([0., 0., 0.], [0., 0., 1.], 180) == 1.
