from test_custom_scene import instance
from test_online_actions import action_event
from test_online_combat import effect, event, running
from test_online_motion import peer_sample

from Business.scripts import online_presentation


def reasons(result):
    return [p['reason'] for p in result['send']]


def remote_peer(monkeypatch, spawnPoints):
    """Peer 2 is shown through playback: server time t is drawn at local time t + 1000 + 100."""
    client = running(monkeypatch, spawnPoints, peer=True)
    peer_sample(client, 1000, (0, 0, 0))
    peer_sample(client, 1050, (.25, 0, 0), sequence=2)
    render(client, 1060)
    return client


def render(client, now):
    client.context['nowMs'] = now
    return instance(client, kind='timer', name='online-motion-render')


def held(client):
    return client.state['sessions']['2'].get('onlinePresentation', {}).get('held', [])


def test_a_remote_swing_lands_with_its_visible_blade(monkeypatch, spawnPoints):
    client = remote_peer(monkeypatch, spawnPoints)
    hit = client.reply(None, 'combatChanged', update=event(dict(effect(attacker=2, target=1), causeAt=100)))
    hurt = client.reply(None, 'actionChanged', action=dict(action_event(state=35), targetActorId=2,
                                                           cause=dict(actorId=2, at=100)))
    assert not hit['send'] and not hurt['send']
    assert [entry['channel'] for entry in held(client)] == ['combat', 'action']
    # Still before the blade on this screen.
    assert not any('contact' in r or 'state=35' in r for r in reasons(render(client, 1150)))
    shown = reasons(render(client, 1220))
    assert 'online-contact-effect' in shown
    assert any('state=35' in r for r in shown)
    assert shown.index('online-contact-effect') < next(i for i, r in enumerate(shown) if 'state=35' in r)
    assert not held(client)


def test_the_local_swing_is_never_delayed(monkeypatch, spawnPoints):
    client = remote_peer(monkeypatch, spawnPoints)
    result = client.reply(None, 'combatChanged', update=event(dict(effect(attacker=1, target=2), causeAt=10**6)))
    assert 'online-contact-effect' in reasons(result)
    assert not held(client)


def test_events_without_a_cause_keep_the_current_timing(monkeypatch, spawnPoints):
    client = remote_peer(monkeypatch, spawnPoints)
    assert 'online-contact-effect' in reasons(client.reply(None, 'combatChanged', update=event(effect(attacker=2, target=1))))


def test_a_held_verdict_keeps_later_verdicts_in_order(monkeypatch, spawnPoints):
    client = remote_peer(monkeypatch, spawnPoints)
    client.reply(None, 'combatChanged', update=event(dict(effect(attacker=2, target=1), causeAt=100)))
    later = client.reply(None, 'combatChanged', update=event(
        dict(effect(attacker=1, target=2, health=online_lives(70, 60)), causeAt=0), sequence=2))
    # Applying the later roster first would let the older one roll health back.
    assert not later['send']
    shown = reasons(render(client, 1220))
    assert shown.count('online-contact-effect') == 2
    assert client.state['sessions']['2']['onlineCombat']['health'] == 70


def test_a_stalled_playback_never_holds_longer_than_the_playback_delay(monkeypatch, spawnPoints):
    client = remote_peer(monkeypatch, spawnPoints)
    client.reply(None, 'combatChanged', update=event(dict(effect(attacker=2, target=1), causeAt=10**6)))
    assert held(client)
    deadline = 1060 + online_presentation.MAX_HOLD_MS
    assert 'online-contact-effect' not in reasons(render(client, deadline - 20))
    assert 'online-contact-effect' in reasons(render(client, deadline))


def test_held_consequences_are_bounded_and_validated(monkeypatch, spawnPoints):
    client = remote_peer(monkeypatch, spawnPoints)
    client.reply(None, 'combatChanged', update=event(dict(effect(attacker=2, target=1), causeAt=100)))
    session = client.state['sessions']['2']
    assert online_presentation.valid(session)
    session['onlinePresentation']['held'][0]['cause'] = 'peer'
    assert not online_presentation.valid(session)


def online_lives(local, peer):
    from test_online_combat import lives
    return lives(local, peer)


def test_rust_publishes_the_fields_this_module_reads():
    from pathlib import Path
    root = Path(__file__).resolve().parents[2] / 'game-server' / 'src'
    battle = (root / 'battle.rs').read_text(encoding='utf-8')
    combat = (root / 'combat.rs').read_text(encoding='utf-8')
    # Serialized as `cause: {actorId, at}` on action events and `causeAt` on contact verdicts.
    assert 'pub cause: Option<Cause>,' in battle
    assert 'pub actor_id: ActorId,\n    pub at: u64,' in battle
    assert 'cause_at: u64,' in combat
