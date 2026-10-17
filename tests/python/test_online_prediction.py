import struct

import pytest

from Business.scripts import online_prediction as prediction
from Business.scripts.codec import battle_flow
from test_custom_scene import instance
from test_online_actions import running, action_event
from test_online_combat import effect, event as combat_event


def input(client, intent):
    keys = (1 << 26) | (1 << 21) if intent == 700250 else 0
    return instance(client, command=4, body=struct.pack('>HIib', 1, keys, intent, 0))


def states(result):
    return [battle_flow.decode_battle_state_sync_simple(p['body']).state for p in result['send']
            if p['reason'] == 'online-action-predicted']


def tick(client, now):
    client.context['nowMs'] = now + 20
    return instance(client, kind='timer', name=prediction.TIMER)


def test_released_attack_runs_without_round_trip_and_echo_does_not_restart(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    assert states(input(client, 300560)) == [235]
    assert not states(input(client, 300020))
    assert states(tick(client, 360)) == [238]
    assert not client.reply(None, 'actionChanged', action=action_event())['send']
    assert not client.reply(None, 'actionChanged', action=action_event(event=2, sequence=3, state=238))['send']
    assert states(tick(client, 1000)) == [239]
    assert states(tick(client, 1500)) == [2]
    assert client.state['sessions']['2'].get('health', 100) == 100


def test_hold_release_and_guard_key_up_predict_once(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    assert states(tick(client, 360)) == [237]
    assert states(input(client, 300020)) == [238]
    assert not states(input(client, 300020))
    client = running(monkeypatch, spawnPoints)
    assert states(input(client, 300580)) == [305]
    assert states(tick(client, 1)) == [223]
    assert states(input(client, 300620)) == [2]


def test_buffered_tap_is_a_complete_following_attack(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    input(client, 300020)
    tick(client, 360)
    input(client, 300550)
    input(client, 300020)
    assert states(tick(client, 1000)) == [241]
    assert states(tick(client, 1360)) == [244]


def test_authority_reaction_discards_speculation_and_cancels_timer(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    response = client.reply(None, 'actionChanged', action=action_event(state=35))
    updates = [battle_flow.decode_battle_state_sync_simple(p['body']) for p in response['send'] if p['command'] == 4]
    assert [update.state for update in updates] == [35]
    session = client.state['sessions']['2']
    assert session['onlinePrediction']['active'] is None and prediction.TIMER not in session['pending']
    assert not states(input(client, 300550))
    client.reply(None, 'actionChanged', action=action_event(event=2, sequence=3, state=2))
    assert states(input(client, 300550)) == [241]


def test_dash_prediction_uses_resource_duration(monkeypatch, spawnPoints):
    from dash_fixture import profile
    client = running(monkeypatch, spawnPoints)
    client.context['dashAttack'] = profile()
    result = input(client, 700250)
    update = battle_flow.decode_battle_state_sync_simple(result['send'][0]['body'])
    assert (update.state, update.state_time_ms) == (439, 2033)
    assert not states(input(client, 700250))
    assert states(tick(client, 2033)) == [2]


def test_prediction_is_bounded_without_wrapping_over_unconfirmed_states(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    session = client.state['sessions']['2']
    session['onlinePrediction'] = dict(seq=65535, pending=[], active=None, blocked=False)
    input(client, 300580)
    assert client.state['sessions']['2']['onlinePrediction']['seq'] == 1
    for _ in range(40):
        for intent in (300620, 300580):
            pending = client.state['sessions']['1']['customLobby']['pending']
            for request, metadata in list(pending.items()):
                if metadata['kind'] == 'action':
                    client.reply(int(request), 'actionAccepted', matchId=metadata['matchId'], inputSeq=metadata['inputSeq'])
            input(client, intent)
    assert len(client.state['sessions']['2']['onlinePrediction']['pending']) == prediction.MAX_PENDING


def test_scene_exit_cannot_emit_later_predicted_attack(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    instance(client, command=0x2c, body=b'\0\2\1')
    assert client.state['sessions']['2']['onlinePrediction']['active'] is None
    assert not client.state['sessions']['2']['onlinePrediction']['pending']
    assert not states(tick(client, 500))


@pytest.mark.parametrize('parried', (False, True))
def test_contact_outcome_is_server_gated_and_duplicate_cannot_replay(monkeypatch, spawnPoints, parried):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'combatChanged', update=combat_event(effect(parried=parried)))
    assert sum(p['reason'] == 'online-contact-effect' for p in result['send']) == int(not parried)
    assert sum(p['reason'] == 'online-parry-sound' for p in result['send']) == int(parried)
    assert any(p['reason'] == 'online-damage' for p in result['send']) == (not parried)
    from copy import deepcopy
    before = deepcopy(client.state)
    with pytest.raises(ValueError):
        client.reply(None, 'combatChanged', update=combat_event(effect(parried=parried)))
    assert client.state == before


def test_action_attaches_the_latest_pose_checkpoint(monkeypatch, spawnPoints):
    import json
    from test_online_motion import sample
    from test_online_actions import running, instance
    from Business.scripts import online_motion
    client = running(monkeypatch, spawnPoints, peer=True)
    sample(client)
    expected = online_motion.checkpoint(client.state['sessions']['2'])
    assert expected['inputSeq'] == 1
    result = instance(client, command=4, body=struct.pack('>HIib', 1, 0, 300560, 0))
    command = json.loads(result['gateway'][0]['request'])['command']
    assert command['payload']['pose'] == expected


def test_corrected_guard_does_not_disable_subsequent_attack_prediction(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    client.reply(None, 'actionChanged', action=action_event(state=226))
    assert not client.state['sessions']['2']['onlinePrediction']['blocked']
    assert states(input(client, 300540)) == [253]


def test_authoritative_parry_closes_both_body_and_weapon_reports(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.reply(None, 'combatChanged', update=combat_event(effect(parried=True)))
    seen = client.state['sessions']['2']['onlineCombat']['seen']
    assert '3:2:0' in seen and '3:2:1' in seen


def test_skipped_server_idle_confirms_guard_without_replaying(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    input(client, 300020)
    tick(client, 360)
    tick(client, 1000)
    tick(client, 1500)
    input(client, 300580)
    tick(client, 1501)
    for event, sequence, state, origin in ((1, 2, 235, 1), (2, 3, 238, 1),
            (3, 4, 239, 1), (4, 5, 305, 3), (5, 6, 223, 3)):
        response = client.reply(None, 'actionChanged', action=action_event(
            event=event, sequence=sequence, state=state, action_id=origin))
        assert not response['send']
    value = client.state['sessions']['2']['onlinePrediction']
    assert value['seq'] == 7  # Native presentation included idle; authority did not.
    assert value['pending'] == []
    assert value['active']['actionId'] == 3
    assert states(input(client, 300620)) == [2]
    assert client.state['sessions']['2']['onlinePrediction']['pending'] == [[8, 2, 3]]


def test_same_phase_from_another_input_is_not_a_prediction_echo(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    response = client.reply(None, 'actionChanged', action=action_event(action_id=2))
    assert response['send']
    assert client.state['sessions']['2']['onlinePrediction']['active'] is None


def test_buffered_release_preserves_original_input_identity(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    input(client, 300020)
    tick(client, 360)
    input(client, 300550)
    input(client, 300020)
    tick(client, 1000)
    tick(client, 1360)
    value = client.state['sessions']['2']['onlinePrediction']
    assert value['active']['actionId'] == 3
    assert [item[2] for item in value['pending']] == [1, 1, 3, 3]


def test_contact_identity_survives_prediction_confirmation(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    input(client, 300020)
    tick(client, 360)
    client.reply(None, 'actionChanged', action=action_event())
    client.reply(None, 'actionChanged', action=action_event(event=2, sequence=3, state=238))
    session = client.state['sessions']['2']
    assert session['onlinePrediction']['pending'] == []
    assert prediction.contact_action(session, 3, 238) == 1
    assert prediction.contact_action(session, 3, 244) is None


def test_correction_uses_fresh_native_sequence(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    input(client, 300560)
    input(client, 300020)
    tick(client, 360)
    response = client.reply(None, 'actionChanged', action=action_event(state=226, action_id=4))
    packet = next(p for p in response['send'] if p['reason'].startswith('online-action actor='))
    update = battle_flow.decode_battle_state_sync_simple(packet['body'])
    assert update.seq_no == 4
    session = client.state['sessions']['2']
    assert prediction.contact_action(session, 3, 238) == 1
    assert prediction.contact_action(session, 4, 226) == 4


def test_presentation_history_is_bounded_and_wrap_replaces_old_identity():
    session = {}
    for index in range(prediction.MAX_PRESENTATIONS + 1):
        prediction.remember(session, 238, index + 1)
    assert len(prediction.ledger(session)['history']) == prediction.MAX_PRESENTATIONS
    assert prediction.contact_action(session, 2, 238) is None
    prediction.ledger(session)['seq'] = 65535
    assert prediction.remember(session, 238, 500) == 1
    prediction.ledger(session)['seq'] = 65535
    prediction.remember(session, 244, 501)
    assert prediction.contact_action(session, 1, 238) is None
    assert prediction.contact_action(session, 1, 244) == 501
    assert prediction.valid(session)
