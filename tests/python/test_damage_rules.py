from dataclasses import replace

import pytest

from Business.scripts import damage_rules as damage


def hit(**changes):
    return damage.calculate(replace(damage.Hit(base=100, damageType='slash'), **changes))


@pytest.mark.parametrize('kind,expected', [('slash', 50), ('pierce', 63), ('blunt', 74)])
def test_armor_by_damage_type(kind, expected):
    assert hit(armor=100, damageType=kind).hpDamage == expected


@pytest.mark.parametrize('region,expected,head', [('head', 150, True), ('torso', 100, False),
                                                ('legs', 75, False), ('unknown', 100, False)])
def test_regions_have_no_random_critical(region, expected, head):
    result = hit(region=region, health=1000)
    assert (result.hpDamage, result.head) == (expected, head)


@pytest.mark.parametrize('held,expected', [(0, 1), (300, 1.25), (600, 1.5), (800, 1.3),
                                          (1000, 1.1), (10000, 1.1)])
def test_charge_rises_then_decays(held, expected):
    assert damage.chargeMultiplier(held, 600) == pytest.approx(expected)


@pytest.mark.parametrize('phase,expected', [(0, .25), (.175, .625), (.35, 1), (.5, 1),
                                         (.65, 1), (.825, .625), (1, .25)])
def test_phase_is_normalized_to_contact_window(phase, expected):
    assert damage.phaseMultiplier(phase) == pytest.approx(expected)


@pytest.mark.parametrize('source,target,weapon,expected', [
    ((0, 0, 0), (0, 0, 0), (10, 0, 0), 1),
    ((5, 0, 0), (0, 0, 0), (15, 0, 0), 1.5),
    ((0, 0, 0), (5, 0, 0), (10, 0, 0), .5),
    ((0, 5, 0), (0, 0, 0), (10, 5, 0), 1),
    ((4, 0, 0), (4, 0, 0), (14, 0, 0), 1),
    ((10, 0, 0), (0, 0, 0), (10, 0, 0), 1),
])
def test_relative_speed_excludes_source_translation(source, target, weapon, expected):
    assert damage.speedMultiplier(source, target, weapon, True) == pytest.approx(expected)
    assert damage.speedMultiplier(source, target, weapon, False) == 1


def test_friendly_penetration_has_decay_and_never_interrupts():
    assert hit(friendly=True).hpDamage == 0
    first, second = hit(allies=1), hit(allies=2)
    assert (first.hpDamage, second.hpDamage) == (40, 5)
    assert first.decay and second.decay
    assert not first.interrupt and not second.interrupt


@pytest.mark.parametrize('cleave,expected', [('none', [100, 0, 0, 0, 0]),
    ('low', [100, 50, 0, 0, 0]), ('medium', [100, 65, 40, 0, 0]), ('high', [100, 80, 60, 40, 0])])
def test_cleave_limits(cleave, expected):
    assert [hit(cleave=cleave, enemyIndex=i).hpDamage for i in range(5)] == expected


def test_rounding_minimum_and_overkill():
    assert hit(base=1, armor=10000).hpDamage == 1
    assert hit(base=2.5).hpDamage == 3
    result = hit(base=1000, health=7)
    assert result.hpDamage == 7 and result.events == ('hit', 'kill')


def test_shield_absorbs_breaking_hit_without_armor_or_region_scaling():
    result = hit(armor=500, region='head', shieldHealth=30, traits=('shield_break',))
    assert (result.hpDamage, result.shieldDamage, result.events) == (0, 30, ('block', 'shield-break'))
    assert not result.head and not result.interrupt
    assert dict(result.factors)['shieldBreaking'] == 1.5


@pytest.mark.parametrize('trait,conditions,expected', [
    ('counter', dict(longPole=True, downAttack=True, targetAttacking=True), 120),
    ('counter', dict(longPole=False, downAttack=True, targetAttacking=True), 100),
    ('light', dict(airborne=True), 115), ('light', dict(airborne=False), 100),
    ('charge_mastery', dict(heldMs=600), 163),
    ('desperate', dict(healthRatio=.3), 120), ('desperate', dict(healthRatio=.31), 100),
    ('assassin_low', dict(rear=True), 110), ('assassin_medium', dict(rear=True), 120),
    ('assassin_high', dict(rear=True), 130), ('assassin_high', dict(rear=False), 100),
])
def test_only_bound_traits_apply(trait, conditions, expected):
    assert hit(traits=(trait,), health=1000, **conditions).hpDamage == expected


def test_distinct_buffs_multiply_but_duplicate_trait_does_not_stack():
    assert hit(traits=('light', 'light'), airborne=True, health=1000).hpDamage == 115
    assert hit(buffMultiplier=1.2 * 1.2, health=1000).hpDamage == 144


def test_immutable_inputs_and_explicit_invalid_data():
    value = damage.Hit(base=42, damageType='slash')
    assert damage.calculate(value) == damage.calculate(value)
    assert value.base == 42
    for changes in ({'base': float('nan')}, {'damageType': 'fire'}, {'phase': -1}, {'armor': -1}):
        with pytest.raises(ValueError):
            damage.calculate(replace(value, **changes))
