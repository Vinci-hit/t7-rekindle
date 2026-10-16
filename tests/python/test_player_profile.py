from copy import deepcopy
import json

import pytest

from Business.scripts import player_profile as profile
from Business.scripts.codec import loadout_flow as wire
from Business.runtime import profile_store
from loadout_fixture import catalogue


def test_default_four_capacity_and_per_hero_equipment():
    cat = catalogue()
    value = profile.create(cat)
    assert profile.validate(value, cat)
    assert value['formation'] == [1101]
    assert len(value['weapons']) == 2
    assert int.from_bytes(wire.formations(value, cat)[18:22], 'big') == 4
    items = profile.equipment(value, cat, 1101)
    assert profile.initialWeapon(items, cat) == 701
    assert profile.offhand(items, cat, 701) == 703
    assert profile.offhand(items, cat, 702) == 0


def test_changes_are_immutable_and_noop_does_not_bump_revision():
    cat = catalogue()
    value = profile.create(cat)
    changed = profile.change(value, cat, formation=[2102, 1101])
    assert value['formation'] == [1101]
    assert changed['revision'] == 1
    assert profile.change(changed, cat, formation=[2102, 1101]) == changed


@pytest.mark.parametrize('formation', [[1101, 1101], [9900], [True]])
def test_invalid_formations(formation):
    cat = catalogue()
    with pytest.raises(ValueError):
        profile.change(profile.create(cat), cat, formation=formation)


def test_backup_and_atomic_failure(tmp_path, monkeypatch):
    path = tmp_path / 'player-profile.json'
    before = profile.create(catalogue())
    after = deepcopy(before)
    after['revision'] += 1
    profile_store.save(path, before, None)
    original = profile_store.os.replace
    def failMain(source, target):
        if target == path:
            raise OSError('read-only destination')
        original(source, target)
    monkeypatch.setattr(profile_store.os, 'replace', failMain)
    with pytest.raises(OSError):
        profile_store.save(path, after, before)
    assert json.loads(path.read_text(encoding='utf-8')) == before
    assert not list(tmp_path.glob('*.tmp'))
    path.write_text('broken', encoding='utf-8')
    recovered, warnings = profile_store.load(path, profile.validate)
    assert recovered == before and warnings


def test_both_corrupt_profiles_are_preserved(tmp_path):
    path = tmp_path / 'player-profile.json'
    path.write_text('bad', encoding='utf-8')
    with pytest.raises(ValueError):
        profile_store.load(path, profile.validate)
    assert path.read_text(encoding='utf-8') == 'bad'
