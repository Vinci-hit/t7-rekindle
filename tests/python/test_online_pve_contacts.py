from types import SimpleNamespace
import pytest
from Business.scripts import online_combat, online_prediction, online_pve, online_pve_contacts


@pytest.mark.parametrize('height', [0.3, 1., 1.65])
def test_blade_contacts_preserve_contact_height(height):
    point = online_pve_contacts.body_contact([0., 0., height], [3., 0., height], [2., 0., 0.])
    assert point == pytest.approx([1.7, 0., height])


def test_nearby_enemy_is_not_a_hit_when_the_blade_misses():
    assert online_pve_contacts.body_contact([0., 0., 2.1], [3., 0., 2.1], [2., 0., 0.]) is None
    assert online_pve_contacts.body_contact([0., 1., 1.], [3., 1., 1.], [2., 0., 0.]) is None
    assert online_pve_contacts.body_contact([0., 0., -1.], [3., 0., -1.], [2., 0., 0.]) is None


def flow():
    monster = online_pve.FIRST_MONSTER_ID
    session = dict(instanceStartedAt=1000, onlinePve=dict(monsters={str(monster): dict(
        alive=True, position=[2., 0., 0.])}))
    online_prediction.ledger(session)['history'] = [[3, 238, 7]]
    return SimpleNamespace(session=session, now=1500, state=dict(onlineScene=dict(matchId='match')),
                           log=lambda *args: None)


def raw(height=1.65):
    return dict(ready=True, instanceStartedAt=1000, sampledAt=1500,
        actors={'1': dict(ready=True, seq=3, state=238, phaseAt=1200, position=[0., 0., 0.])},
        blades={'1': dict(ready=True, base=[0., 0., height], tip=[3., 0., height])})


def test_one_report_per_swing_target_and_multiple_targets_are_supported(monkeypatch):
    reports = []
    monkeypatch.setattr(online_combat, 'send', lambda flow, kind, command, **kwargs: reports.append(command) or True)
    f = flow()
    f.session['onlinePve']['monsters'][str(online_pve.FIRST_MONSTER_ID+1)] = dict(alive=True, position=[2.5, 0., 0.])
    online_pve_contacts.world_updated(f, raw())
    assert len(reports) == 2
    assert reports[0]['payload']['contact']['position'][2] == pytest.approx(1.65)
    assert reports[0]['payload']['contact']['actionId'] == 7
    f.now = 1516
    frame = raw(); frame['sampledAt'] = 1516
    online_pve_contacts.world_updated(f, frame)
    assert len(reports) == 2


def test_failed_send_can_retry_and_no_reports_from_stale_or_windup_frames(monkeypatch):
    sent = []
    monkeypatch.setattr(online_combat, 'send', lambda *args, **kwargs: False)
    f = flow()
    online_pve_contacts.world_updated(f, raw())
    assert not online_combat.ledger(f.session)['seen']
    monkeypatch.setattr(online_combat, 'send', lambda *args, **kwargs: sent.append(args) or True)
    f.now = 1516
    frame = raw(); frame['sampledAt'] = 1516
    online_pve_contacts.world_updated(f, frame)
    assert len(sent) == 1
    f = flow(); frame = raw(); frame['actors']['1']['state'] = 235
    online_pve_contacts.world_updated(f, frame)
    f.now = 2000
    online_pve_contacts.world_updated(f, raw())
    assert len(sent) == 1


def test_sweep_detects_a_contact_between_two_frames(monkeypatch):
    reports = []
    monkeypatch.setattr(online_combat, 'send', lambda flow, kind, command, **kwargs: reports.append(command) or True)
    f = flow(); frame = raw(0.3)
    frame['blades']['1'].update(base=[0., -0.5, .3], tip=[3., -0.5, .3])
    online_pve_contacts.world_updated(f, frame)
    assert not reports
    f.now = 1516; frame['sampledAt'] = 1516
    frame['blades']['1'].update(base=[0., 0.5, .3], tip=[3., 0.5, .3])
    online_pve_contacts.world_updated(f, frame)
    assert len(reports) == 1
    assert reports[0]['payload']['contact']['position'][2] == pytest.approx(.3)


def test_emerging_monster_is_damageable_and_uses_selected_weapon_window(monkeypatch):
    reports = []
    monkeypatch.setattr(online_combat, 'send', lambda flow, kind, command, **kwargs: reports.append(command) or True)
    monkeypatch.setattr(online_combat, 'contact_window', lambda session, state: (600, 900))
    f = flow()
    f.session['onlinePve']['monsters'][str(online_pve.FIRST_MONSTER_ID)]['birth'] = dict(animation='emerge1')
    # This contact was rejected by the old universal 540 ms cutoff.
    f.now = 1900
    frame = raw(); frame['sampledAt'] = 1900
    online_pve_contacts.world_updated(f, frame)
    assert len(reports) == 1
    assert reports[0]['payload']['contact']['elapsedMs'] == 700


def test_distant_monsters_do_not_consume_precise_sweep_tests(monkeypatch):
    f = flow()
    f.session['onlinePve']['monsters'] = {str(online_pve.FIRST_MONSTER_ID+i):
        dict(alive=True, position=[100.+i, 100., 0.]) for i in range(64)}
    monkeypatch.setattr(online_pve_contacts, 'body_contact',
        lambda *args: pytest.fail('distant body reached precise collision test'))
    online_pve_contacts.world_updated(f, raw())
    assert not online_combat.ledger(f.session)['seen']


def test_hit_cylinder_follows_presented_monster_position():
    from Business.scripts import online_pve_contacts as contacts
    assert contacts.presented_position(dict(position=[2., 0., 0.])) == [2., 0., 0.]
    assert contacts.presented_position(dict(position=[2., 0., 0.], nativeTransform=[[1.6, 0., 0.], 0])) == [1.6, 0., 0.]
