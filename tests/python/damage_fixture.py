"""Explicit flat damage for geometry/action tests, independent of production balance."""
from dataclasses import replace

from Business.scripts import battle, damage_resources, damage_state

NORMAL_DAMAGE, DASH_DAMAGE = 20, 40


def install(monkeypatch):
    original = damage_resources.loadProfile

    def synthetic(heroId, weaponIds):
        result = original(heroId, weaponIds)
        result.update(armor=0, armorType='light', traits=[])
        for weapon in result['weapons'].values():
            weapon.update(traits=[], cleave='high', attackGrade='C', defenceGrade='C', weight=1.)
            for intent, attack in weapon['attacks'].items():
                attack['base'] = DASH_DAMAGE if int(intent) == battle.DASH_ATTACK else NORMAL_DAMAGE
        return result

    monkeypatch.setattr(damage_resources, 'loadProfile', synthetic)
    monkeypatch.setattr(damage_state, 'RULES', replace(damage_state.RULES,
        phasePoints=((0, 1), (1, 1)), chargeMax=1, chargeTail=1,
        friendlyScale=(1, 1, 1), cleaveScale=((1,), (1, 1), (1, 1, 1), (1, 1, 1, 1))))
