import pytest

from Business.scripts import guard_rules as guard


@pytest.mark.parametrize('attack', range(8))
@pytest.mark.parametrize('defence', range(8))
def test_all_grades_use_the_same_rule_without_b_exceptions(attack, defence):
    gap = attack - defence
    expected = 0 if gap < 2 else (.25, .5, .75)[min(gap - 2, 2)]
    assert guard.damageFraction(gap) == expected
    assert bool(guard.damageFraction(gap, chamber=True)) == (gap >= 4)


@pytest.mark.parametrize('gap,attackWeight,defenceWeight,cost', [
    (0, 1, 1, 1), (1, 1, 1, 1.25), (-1, 1, 1, .75),
    (0, 2, 1, 1.5), (0, 1, 2, .75), (-7, 1, 3, .25), (7, 3, 1, 2.5),
])
def test_experience_consumption_formula(gap, attackWeight, defenceWeight, cost):
    assert guard.consumption(gap, attackWeight, defenceWeight) == cost


@pytest.mark.parametrize('elapsed,valid', [(49, False), (50, True), (200, True), (201, False)])
def test_chamber_has_a_bounded_prepare_window(elapsed, valid):
    assert guard.chamberWindow(300560, 300570, elapsed) is valid
    assert not guard.chamberWindow(300560, 300540, elapsed)


@pytest.mark.parametrize('trait,up,side', [('heavy_low', 1, 0), ('heavy_medium', 2, 0),
                                        ('heavy_high', 3, 0), ('heavy_special', 3, 1)])
def test_heavy_charge_only_uses_bound_trait_at_full_hold(trait, up, side):
    context = dict(attackGrade='B', heldMs=600, traits=[trait], longPole=False)
    assert guard.attackPerformance(context, 300560) == 4 + up
    assert guard.attackPerformance(context, 300550) == 4 + side
    assert guard.attackPerformance(context, 300570) == 4
    assert guard.attackPerformance({**context, 'heldMs': 599}, 300560) == 4
    assert guard.attackPerformance(context, 300560, allies=1) == 0


def test_counter_requires_pole_thrust_against_strike():
    context = dict(attackGrade='C', heldMs=0, traits=['counter'], longPole=True)
    assert guard.attackPerformance(context, 300570, True) == 4
    assert guard.attackPerformance(context, 300570, False) == 3
    assert guard.attackPerformance(context, 300560, True) == 3
    assert guard.attackPerformance({**context, 'longPole': False}, 300570, True) == 3


def test_armor_reactions_separate_interrupt_movement_and_immunity():
    assert guard.bodyReaction(0, 'heavy') == guard.Reaction(False)
    assert guard.bodyReaction(0, 'light') == guard.Reaction(True)
    assert guard.bodyReaction(4, 'light') == guard.Reaction(True, 250)
    assert guard.bodyReaction(4, 'medium') == guard.Reaction(True, 180)
    assert guard.bodyReaction(4, 'heavy') == guard.Reaction(True)
    assert guard.bodyReaction(4, 'heavy', airborne=True) == guard.Reaction(True, 1000, True)
    assert guard.bodyReaction(4, 'light', airborne=True, immune=True) == guard.Reaction(True)
    assert guard.bodyReaction(3, 'heavy', fortified=True) == guard.Reaction(False)
