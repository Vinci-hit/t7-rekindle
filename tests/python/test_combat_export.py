"""The offline exporter clips contact windows without changing movement or damage."""
from copy import deepcopy
import importlib.util
from pathlib import Path

from Business.scripts import animation_timing
from dash_fixture import profile as dashProfile
from loadout_fixture import combatProfiles

spec = importlib.util.spec_from_file_location('export_combat_profiles',
    Path(__file__).resolve().parents[2] / 'scripts/export_combat_profiles.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def animation(monkeypatch, alias, seconds):
    value = animation_timing.AnimationTimings.__new__(animation_timing.AnimationTimings)
    value.directory, value.error = 'fixture', None
    value.aliases, value.generators = {alias: 'fixture.hkt'}, {alias: [{}]}
    monkeypatch.setattr(animation_timing, 'clipSeconds', lambda *_: seconds)
    return value


def test_dash_uses_animation_and_truncates_only_contact_end(monkeypatch):
    profile = combatProfiles(1101)[702]
    profile['dashAttack'] = dashProfile(weaponId=702)
    original = deepcopy(profile)
    result = exporter.applyTimings(profile, animation(monkeypatch, 'DashAttack', 1.0666667), 550)
    assert result['dashAttack']['durationMs'] == result['dashAttack']['contactEndMs'] == 1067
    assert result['dashAttack']['durationSource'] == 'animation'
    assert result['dashAttack']['moveMs'] == 967 and result['dashAttack']['speedMilli'] == 2585
    assert result['windowAdjustments']['DashAttack'] == (1133, 1067)
    assert profile == original


def test_melee_table_timing_is_not_overridden_by_shorter_animation(monkeypatch):
    profile = combatProfiles(1101)[702]
    result = exporter.applyTimings(profile, animation(monkeypatch, 'AttackUpProcess', .4), 550)
    attack = result['attacks']['300560']
    assert attack['phases'] == [350, 0, 600, 400]
    assert (attack['contactStartMs'], attack['contactEndMs']) == (70, 550)
    assert 'windowAdjustments' not in result


def test_full_process_contact_window_follows_table_without_changing_hold(monkeypatch):
    profile = combatProfiles(1101)[702]
    profile['attacks']['300560'].update(contactStartMs=0, contactEndMs=600)
    result = exporter.applyTimings(profile, animation(monkeypatch, 'AttackUpProcess', .7), 550)
    assert result['attacks']['300560']['contactEndMs'] == 600
    assert all(attack['phases'][1] == 0 for attack in result['attacks'].values())
    assert result['hurtMs'] == 550 and result['guard'] == profile['guard']


def test_render_is_standalone_python_and_preserves_static_references():
    references, templates = {1101: {701: 0}}, [{'attacks': {}, 'changeMs': 700}]
    namespace = {}
    exec(exporter.render(references, templates), namespace)
    assert namespace['HERO_WEAPONS'] == references
    assert list(namespace['PROFILES']) == templates
