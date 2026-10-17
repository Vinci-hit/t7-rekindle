"""Regression coverage for catalogue-backed online heroes and action clocks."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import struct

import pytest
from Business.scripts import combat_resources, online_scene, online_prediction
from Business.scripts.combat_profile_data import HERO_WEAPONS
from test_online_actions import running, action_event
from test_custom_scene import instance
from test_online_prediction import input, states, tick


def test_rust_catalogue_matches_python_source():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location('export_online_combat_profiles', root / 'scripts/export_online_combat_profiles.py')
    exporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(exporter)
    assert (root / 'game-server/src/combat_profiles.json').read_text(encoding='utf-8') == exporter.render()


@pytest.mark.parametrize('hero', HERO_WEAPONS)
def test_every_online_weapon_predicts_using_its_own_profile(hero, monkeypatch, spawnPoints):
    for weapon, profile in combat_resources.loadCombatProfiles('', hero).items():
        if profile['shield']:
            continue
        for intent, strike in ((300560, 238), (300550, 244), (300570, 250), (300540, 256)):
            client = running(monkeypatch, spawnPoints)
            session = client.state['sessions']['2']
            session.update(heroId=hero, weaponId=weapon, mainWeaponId=weapon, offhandId=0,
                           equipment=[] if weapon == 100000 else [dict(slot=1, weaponId=weapon)],
                           weaponProfiles={str(t): deepcopy(combat_resources.loadCombatProfiles('', hero)[t])
                                           for t in {weapon, 100000}})
            assert states(input(client, intent))
            assert online_prediction.valid(client.state['sessions']['2'])
            input(client, 300020)
            duration = profile['attacks'][str(intent)]['phases'][0]
            assert states(tick(client, duration)) == [strike]
            active = client.state['sessions']['2']['onlinePrediction']['active']
            assert active['weapon'] == weapon


def test_peer_selection_replaces_default_model_and_equipment(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints, peer=True)
    room = deepcopy(client.state['sessions']['1']['customLobby']['room'])
    peer = next(m for m in room['members'] if m['actorId'] == 2)
    peer['loadout'] = dict(heroId=1102, equipment=[dict(slot=1, weaponId=1010911), dict(slot=3, weaponId=1030611)])
    room['revision'] += 1
    result = client.reply(None, 'roomChanged', room=room)
    data = client.state['sessions']['2']['onlinePeers']['2']
    assert (data['heroId'], data['weaponId']) == (1102, 1010911)
    assert any(p['reason'] == 'online-peer-loadout-add' for p in result['send'])
    response = client.reply(None, 'actionChanged', action={**action_event(actor=2), 'weaponId': 1030611})
    assert client.state['sessions']['2']['onlinePeers']['2']['weaponId'] == 1030611
    assert any(p['reason'] == 'online-weapon-use-update' for p in response['send'])


def test_selection_sends_ids_before_readiness(monkeypatch, spawnPoints):
    import json
    from Business.scripts import app, hero_resources, loadouts, player_profile, damage_resources
    from loadout_fixture import catalogue, combatProfiles, damageProfiles
    cat = catalogue()
    monkeypatch.setattr(hero_resources, 'loadCatalogue', lambda _: cat)
    monkeypatch.setattr(combat_resources, 'loadCombatProfiles', lambda _, hero: combatProfiles(hero))
    monkeypatch.setattr(damage_resources, 'loadProfile', damageProfiles)
    client = running(monkeypatch, spawnPoints)
    session = client.state['sessions']['2']
    session['loadoutProfile'] = player_profile.create(cat)
    client.context['clientDirectory'] = 'fixture'
    flow = app.Flow(dict(connection=2, receivedAtMs=20), client.state, client.context)
    assert loadouts.select(flow, 1)
    request = json.loads(flow.result['gateway'][0]['request'])['command']
    assert request['type'] == 'selectLoadout'
    assert request['payload']['loadout'] == dict(heroId=session['heroId'], equipment=session['equipment'])
    assert app.validateState(flow.state)


def test_shield_native_input_predicts_hold_and_release(monkeypatch, spawnPoints):
    client = running(monkeypatch, spawnPoints)
    profiles = combat_resources.loadCombatProfiles('', 1001)
    session = client.state['sessions']['2']
    session.update(heroId=1001, weaponId=1010811, mainWeaponId=1010811, offhandId=1090211,
        equipment=[dict(slot=1, weaponId=1010811), dict(slot=2, weaponId=1090211)],
        weaponProfiles={str(t): profiles[t] for t in (100000, 1010811, 1090211)})
    assert states(input(client, 300130)) == [95]
    assert online_prediction.valid(client.state['sessions']['2'])
    delay = profiles[1010811]['guard']['prepareMs']
    assert states(tick(client, delay)) == [309]
    assert states(input(client, 300140)) == [2]
    assert online_prediction.valid(client.state['sessions']['2'])
