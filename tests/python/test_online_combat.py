from copy import deepcopy
import json
import struct
import pytest
from Business.scripts.codec import battle_flow
from test_online_actions import running as start_running
from Business.scripts import online_prediction


def running(*args, **kwargs):
    client = start_running(*args, **kwargs)
    online_prediction.remember(client.state['sessions']['2'], 235, 1)
    online_prediction.remember(client.state['sessions']['2'], 238, 1)
    return client

from test_custom_scene import MATCH, instance


def lives(local=100, peer=80):
    return [dict(actorId=1, health=local, kills=int(peer == 0), deaths=int(local == 0), streak=0),
            dict(actorId=2, health=peer, kills=int(local == 0), deaths=int(peer == 0), streak=0)]


def event(effect, sequence=1):
    return dict(roomId='room-test', matchId=MATCH, eventSeq=sequence, serverTimeMs=3500, effect=effect)


def effect(attacker=1, target=2, parried=False, health=None):
    return dict(type='contact', attacker=attacker, target=target, actionSeq=3, weaponId=1030411, targetWeaponId=1030411,
        hitPart='body', parried=parried, damage=0 if parried else 20, position=[1.,0.,1.5], normal=[-1.,0.,0.],
        direction=[1.,0.,0.], material=4, lives=health or lives())


def native_evidence(value, defence_weapon=0):
    return dict(value, defenceWeaponId=defence_weapon, bodyIndex=0, region='torso',
        sourceVelocity=[0., 0., 0.], targetVelocity=[0., 0., 0.], weaponVelocity=[0., 0., 0.],
        velocityReady=False, airborne=False, targetAirborne=False)


@pytest.mark.parametrize('weapon_contact', [False, True])
def test_native_contact_only_reports_local_actor_and_never_locally_awards_damage(monkeypatch, spawnPoints, weapon_contact):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.context['nowMs'] = 1000
    epoch = client.state['sessions']['2']['instanceStartedAt']
    hit = dict(ready=True, hit=True, actorId=1, targetId=2, entity=101, targetEntity=102,
        state=238, seq=3, targetState=2, targetSeq=1, phaseAt=900, targetPhaseAt=800,
        occurredAt=950., along=.5, sourcePosition=[0.,0.,.5], targetPosition=[1.2,0.,.5],
        sourceHeading=0., targetHeading=180., position=[.9,0.,1.5], normal=[-1.,0.,0.], direction=[1.,0.,0.], material=4)
    hit = native_evidence(hit, 1030411 if weapon_contact else 0)
    client.context['botWorld'] = dict(ready=True, sampledAt=1000, instanceStartedAt=epoch,
        actors={'1':dict(ready=True, entity=101, position=[0.,0.,.5], heading=0.)},
        contactJournal=dict(throughAt=1000, batchId=1, frames=10, dropped=0,
            events=[hit, {**hit, 'actorId':2, 'targetId':1, 'entity':102, 'targetEntity':101}]))
    result = client.event('world')
    request = next(json.loads(v['request']) for v in result['gateway'] if 'contactInput' in v['request'])
    contact = request['command']
    assert contact['payload']['contact']['targetActorId'] == 2
    assert contact['payload']['contact']['elapsedMs'] == 50
    assert contact['payload']['contact']['timing'] == dict(sampleTimeMs=950, phaseStartMs=900, gatewayTimeMs=1000)
    assert contact['payload']['pose']['inputSeq'] == 1
    assert contact['payload']['pose']['pose']['position'] == [0., 0., .5]
    assert contact['payload']['contact'].get('weaponContact', False) == weapon_contact
    assert not result['send']
    assert client.state['sessions']['2']['onlineCombat']['health'] == 100
    assert client.state['sessions']['2']['onlinePeers']['2'].get('health', 100) == 100
    client.context['botWorld']['contactJournal']['batchId'] = 2
    assert not client.event('world').get('gateway')
    if not weapon_contact:
        # The same body overlap may be a guard hit. No speculative flesh audio
        # may precede the server's exclusive guard feedback.
        result = client.reply(None, 'combatChanged', update=event(effect(parried=True)))
        assert not any(p['reason'] == 'online-contact-effect' for p in result['send'])
        assert sum(p['reason'] == 'online-parry-sound' for p in result['send']) == 1
    if weapon_contact:
        client.reply(int(request['requestId']), 'contactAccepted', matchId=MATCH, inputSeq=1, accepted=False)
        client.context['botWorld']['contactJournal'].update(batchId=3, events=[{**hit, 'defenceWeaponId': 0}])
        result = client.event('world')
        body = next(json.loads(v['request'])['command'] for v in result['gateway'] if 'contactInput' in v['request'])
        assert body['payload']['inputSeq'] == 2 and not body['payload']['contact'].get('weaponContact', False)


def test_authority_health_and_damage_float_use_each_clients_local_mapping(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    result = client.reply(None, 'combatChanged', update=event(effect()))
    assert any(p['reason']=='online-health-peer' for p in result['send'])
    feedback = next(p for p in result['send'] if p['reason']=='online-contact-effect')
    assert battle_flow.decode_battle_hit_point_notify(feedback['body']).target_mid == 2
    assert any(p['reason']=='online-damage' for p in result['send'])
    assert client.state['sessions']['2']['onlinePeers']['2']['health'] == 80
    before=deepcopy(client.state)
    with pytest.raises(ValueError):client.reply(None, 'combatChanged', update=event(effect()))
    assert client.state==before


@pytest.mark.parametrize('attacker', [1, 2])
def test_real_parry_uses_native_weapon_effects_and_one_dedicated_sound(monkeypatch, spawnPoints, attacker):
    client = running(monkeypatch, spawnPoints, peer=True)
    result=client.reply(None,'combatChanged',update=event(effect(attacker=attacker, target=3-attacker,
        parried=True,health=lives(peer=100))))
    assert not any(p['body'][:2] == struct.pack('>H', battle_flow.HIT_POINT_NOTIFY)
                   for p in result['send'] if p['command'] == 4)
    assert sum(p['body'][:2]==b'\0\x28' and p['reason']=='online-parry-sound' for p in result['send']) == 1
    assert not any(p['reason']=='online-damage' for p in result['send'])


@pytest.mark.parametrize('target', [1, 2])
@pytest.mark.parametrize('contact_first', [False, True])
def test_hurt_has_one_explicit_animation_regardless_of_notification_order(
        monkeypatch, spawnPoints, target, contact_first):
    from test_online_actions import action_event
    client = running(monkeypatch, spawnPoints, peer=True)
    attacker = 3 - target
    hurt = dict(action_event(actor=target, state=35, action_id=None),
                targetActorId=attacker, durationMs=400)
    outcome = effect(attacker=attacker, target=target,
                     health=lives(local=80, peer=100) if target == 1 else lives())
    events = [('actionChanged', dict(action=hurt)),
              ('combatChanged', dict(update=event(outcome)))]
    if contact_first:
        events.reverse()
    packets = []
    for kind, payload in events:
        packets.extend(client.reply(None, kind, **payload)['send'])
    reactions = [p for p in packets if p['reason'].startswith('online-action ')]
    assert len(reactions) == 1
    body = reactions[0]['body']
    assert struct.unpack_from('>HHHH', body) == (battle_flow.STATE_SYNC, target, 4 if target == 1 else 2, 35)
    assert struct.unpack_from('>Q', body, 15)[0] == attacker
    feedback = [p for p in packets if p['reason'] == 'online-contact-effect']
    assert len(feedback) == 1 and feedback[0]['body'][87] == 0
    assert not any(p['reason'] == 'online-parry-sound' for p in packets)


@pytest.mark.parametrize('target,damage', [(None, 0), (2, 0), (2, 20)])
def test_native_body_effects_require_authoritative_damage(monkeypatch, spawnPoints, target, damage):
    client = running(monkeypatch, spawnPoints, peer=True)
    outcome = dict(effect(target=target), damage=damage)
    result = client.reply(None, 'combatChanged', update=event(outcome))
    feedback = [p for p in result['send'] if p['reason'] == 'online-contact-effect']
    assert len(feedback) == 1
    assert feedback[0]['body'][87] == (0 if target and damage else 1)


def test_death_waits_for_rust_ready_selection_and_play_then_recreates_actor(monkeypatch, spawnPoints):
    client=running(monkeypatch,spawnPoints,peer=True)
    result=client.reply(None,'combatChanged',update=event(effect(attacker=2,target=1,health=lives(local=0,peer=100))))
    assert any(p['reason']=='online-observer' for p in result['send'])
    assert not client.state['sessions']['2']['groundEnabled']
    result=instance(client,command=0x36,body=struct.pack('>HHb',0x64,1,0))
    assert any(p['reason']=='online-respawn-not-ready' for p in result['send'])
    client.reply(None,'combatChanged',update=event(dict(type='respawnReady',actorId=1),2))
    result=instance(client,command=0x36,body=struct.pack('>Hi',0x32,1))
    assert any(p['reason']=='actor-choose-hero-result-zero' for p in result['send'])
    result=instance(client,command=0x36,body=struct.pack('>HHb',0x64,1,0))
    assert json.loads(result['gateway'][0]['request'])['command']['type']=='respawn'
    client.reply(6,'respawnAccepted',matchId=MATCH)
    result=client.reply(None,'combatChanged',update=event(dict(type='respawned',actorId=1,lives=lives(peer=100)),3))
    assert any(p['reason']=='online-respawn-add' for p in result['send'])
    assert client.state['sessions']['2']['groundEnabled'] and client.state['sessions']['2']['onlineCombat']['health']==100


def test_scores_first_blood_camp_counts_and_streaks_follow_authoritative_kills(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    first = lives(peer=0)
    first[0]['streak'] = 1
    result = client.reply(None, 'combatChanged', update=event(effect(health=first)))
    updates = [p for p in result['send'] if p['reason'] == 'online-score']
    assert len(updates) == 2
    assert sum(struct.unpack_from('>i', p['body'], 18)[0] == 730000 for p in updates) == 1
    assert any(p['reason'] == 'online-camp-score' for p in result['send'])
    assert not any(p['reason'] == 'online-kill-streak' for p in result['send'])
    restored = deepcopy(first)
    restored[1]['health'] = 100
    client.reply(None, 'combatChanged', update=event(dict(type='respawned', actorId=2, lives=restored), 2))
    second = deepcopy(first)
    second[0].update(kills=2, streak=2)
    second[1]['deaths'] = 2
    result = client.reply(None, 'combatChanged', update=event(effect(health=second), 3))
    assert all(struct.unpack_from('>i', p['body'], 18)[0] == 0 for p in result['send'] if p['reason'] == 'online-score')
    banner = next(p for p in result['send'] if p['reason'] == 'online-kill-streak')
    assert struct.unpack_from('>ii', banner['body'], 18) == (710020, 2)


@pytest.mark.parametrize('field,value',[('actorId',9),('matchId','other'),('eventSeq',2)])
def test_combat_events_cannot_cross_the_frozen_roster_or_sequence(field,value,monkeypatch,spawnPoints):
    client=running(monkeypatch,spawnPoints,peer=True);update=event(effect())
    if field=='actorId':update['effect']['attacker']=value
    else:update[field]=value
    before=deepcopy(client.state)
    with pytest.raises(ValueError):client.reply(None,'combatChanged',update=update)
    assert client.state==before


@pytest.mark.parametrize('part,index', [('head', battle_flow.HIT_PART_HEAD),
    ('body', battle_flow.HIT_PART_BODY), ('legs', battle_flow.HIT_PART_LEGS)])
def test_authority_body_region_reaches_hit_icon_and_damage_result(monkeypatch, spawnPoints, part, index):
    client = running(monkeypatch, spawnPoints, peer=True)
    outcome = effect()
    outcome['hitPart'] = part
    result = client.reply(None, 'combatChanged', update=event(outcome))
    feedback = next(p for p in result['send'] if p['reason'] == 'online-contact-effect')
    contact = battle_flow.decode_battle_hit_point_notify(feedback['body'])
    assert contact.hit_part == index
    assert feedback['body'][87] == 0
    damage = next(p for p in result['send'] if p['reason'] == 'online-damage')
    assert battle_flow.decode_battle_result(damage['body']).target_rb_index == index


@pytest.mark.parametrize('weapon_first', [False, True])
def test_contacts_follow_sweep_time_instead_of_collection_order(monkeypatch, spawnPoints, weapon_first):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.context['nowMs'] = 1000
    epoch = client.state['sessions']['2']['instanceStartedAt']
    body = dict(ready=True, hit=True, actorId=1, targetId=2, entity=101, targetEntity=102,
        state=238, seq=3, targetState=223, targetSeq=2, phaseAt=900, targetPhaseAt=800,
        occurredAt=950., along=.5, sourcePosition=[0.,0.,.5], targetPosition=[1.2,0.,.5],
        sourceHeading=0., targetHeading=180., position=[.9,0.,1.5], normal=[-1.,0.,0.],
        direction=[1.,0.,0.], material=4)
    body = native_evidence(body)
    weapon = {**body, 'defenceWeaponId': 1030411, 'occurredAt': 949. if weapon_first else 951.}
    client.context['botWorld'] = dict(ready=True, sampledAt=1000, instanceStartedAt=epoch,
        actors={'1':dict(ready=True, entity=101, position=[0.,0.,.5], heading=0.)},
        contactJournal=dict(throughAt=1000, batchId=1, frames=10, dropped=0, events=[weapon, body]))
    result = client.event('world')
    reports = [json.loads(v['request'])['command']['payload']['contact']
        for v in result['gateway'] if 'contactInput' in v['request']]
    assert [r.get('weaponContact', False) for r in reports] == [weapon_first, not weapon_first]
    assert [r['elapsedMs'] for r in reports] == sorted(r['elapsedMs'] for r in reports)
    assert not result['send']


def test_native_reaction_trace_distinguishes_restart_from_repeated_sampling(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    session = client.state['sessions']['2']
    client.context['nowMs'] = 1000
    client.context['botWorld'] = dict(ready=True, sampledAt=1000,
        instanceStartedAt=session['instanceStartedAt'], actors={
            '2': dict(ready=True, position=[1., 0., .5], state=35, seq=8, phaseAt=950)})
    result = client.event('world')
    traces = lambda result: [entry['message'] for entry in result['logs']
                            if 'online-native-reaction' in entry['message']]
    assert len(traces(result)) == 1
    assert not traces(client.event('world'))
    # A restart with the same state/sequence but a new native phase is observable.
    client.context['botWorld']['actors']['2']['phaseAt'] = 980
    result = client.event('world')
    assert len(traces(result)) == 1
    assert 'phaseAt=980' in traces(result)[0]
    client.context['botWorld']['actors']['2']['state'] = 2
    assert len(traces(client.event('world'))) == 1
    assert not traces(client.event('world'))


def test_thrust_windup_does_not_consume_later_valid_contact(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    client.context['nowMs'] = 1000
    epoch = client.state['sessions']['2']['instanceStartedAt']
    hit = dict(ready=True, hit=True, actorId=1, targetId=2, entity=101, targetEntity=102,
        state=250, seq=3, targetState=2, targetSeq=1, phaseAt=900, targetPhaseAt=800,
        occurredAt=950., along=.5, sourcePosition=[0.,0.,.5], targetPosition=[1.2,0.,.5],
        sourceHeading=0., targetHeading=180., position=[.9,0.,1.5], normal=[-1.,0.,0.], direction=[1.,0.,0.], material=4)
    hit = native_evidence(hit)
    client.context['botWorld'] = dict(ready=True, sampledAt=1000, instanceStartedAt=epoch,
        actors={'1':dict(ready=True, entity=101, position=[0.,0.,.5], heading=0.)},
        contactJournal=dict(throughAt=1000, batchId=1, frames=10, dropped=0,
            events=[hit, {**hit, 'actorId':2, 'targetId':1, 'entity':102, 'targetEntity':101}]))
    hit['seq'] = online_prediction.remember(client.state['sessions']['2'], 250, 1)
    assert not any('contactInput' in v['request'] for v in client.event('world').get('gateway', []))
    client.context['nowMs'] = 1100
    client.context['botWorld']['sampledAt'] = 1100
    client.context['botWorld']['contactJournal'].update(
        throughAt=1100, batchId=2, events=[{**hit, 'occurredAt': 1050.}])
    result = client.event('world')
    request = next(json.loads(v['request']) for v in result['gateway'] if 'contactInput' in v['request'])
    assert request['command']['payload']['contact']['elapsedMs'] == 150
