import pytest

from Business.scripts import battle, damage_resources as damage
from Business.scripts.combat_profile_data import HERO_WEAPONS
from Business.scripts.damage_data import HEROES, WEAPONS


def test_static_export_covers_current_roster_without_reading_client(monkeypatch):
    def unexpected(*args, **kwargs):
        raise AssertionError('runtime damage must not read client resources')
    monkeypatch.setattr(damage, 'readSelected', unexpected)
    assert len(HEROES) == len(HERO_WEAPONS) == 36
    assert len(WEAPONS) == 120  # 119 loadout items plus fists.
    count = 0
    for heroId, weapons in HERO_WEAPONS.items():
        profile = damage.loadProfile(heroId, weapons)
        assert set(profile['weapons']) == {str(tid) for tid in weapons}
        assert profile['armorSource'] == 'client'
        for item in profile['weapons'].values():
            if item['shieldMax']:
                assert item['shieldMax'] > 0 and item['attacks'] == {}
            else:
                count += 1
                assert set(map(str, battle.ATTACK_CHAINS)) <= set(item['attacks'])
                assert all(value['base'] > 0 and value['damageId'] > 0 for value in item['attacks'].values())
    assert count == 246


def test_original_values_and_project_rules_are_distinguished():
    sword = damage.weaponProfile(1030411)
    assert sword['attacks']['300560'] == dict(base=46, damageType='slash', damageId=103040)
    assert sword['attacks']['300570'] == dict(base=23, damageType='pierce', damageId=103042)
    assert sword['attacks']['700250']['base'] == 46
    assert sword['cleave'] == 'medium'
    assert sword['baseSource'] == 'client' and sword['typeSource'] == 'project'
    fist = damage.weaponProfile(100000)
    assert fist['baseSource'] == 'project'
    assert all(value['base'] == 20 and value['damageType'] == 'blunt' for value in fist['attacks'].values())
    assert damage.loadProfile(1101, (100000, 1030411))['armor'] == 63
    assert damage.weaponProfile(1090111)['shieldMax'] == 125


def test_bound_traits_are_not_applied_globally():
    assert damage.loadProfile(1101, (100000, 1030411))['traits'] == ['light', 'combo']
    assert 'counter' in damage.loadProfile(1004, (100000,))['traits']
    assert 'riposte' not in damage.loadProfile(1004, (100000,))['traits']
    with pytest.raises(ValueError):
        damage.loadProfile(999, (100000,))
    with pytest.raises(ValueError):
        damage.loadProfile(1101, (999,))


def test_original_performance_letters_and_explicit_project_fallbacks():
    for tid, attack, defence in ((1010911, 'E', 'F'), (1030411, 'C', 'C'),
                                 (1040511, 'C', 'D'), (1090311, 'F', 'A')):
        item = damage.weaponProfile(tid)
        assert (item['attackGrade'], item['defenceGrade']) == (attack, defence)
        assert item['performanceSource'] == 'client'
        assert item['weight'] > 0 and item['weightSource'] == 'project'
    assert damage.weaponProfile(100000)['performanceSource'] == 'project'
    assert damage.loadProfile(1101, (100000,))['armorTypeSource'] == 'project'
