from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, bot, bot_decision as decision, bot_footwork, bot_profiles
from test_bot_decision import duel, nearBot, observe, choose
from test_bot_player import snapshot
from test_bot_roster import multi
from test_bot_tactics import fight


def test_berserker_has_no_extra_attack_delay_or_defence():
    for tempo in (85, 100, 115):
        settings = bot_profiles.get({'profile': 'berserker', 'tempo': tempo})
        assert settings.attackDelayMs == 0 and not settings.canDefend


@pytest.mark.parametrize('intent,phase', [(intent, phase) for intent in bot.PARRY_FOR_ATTACK
                                        for phase in range(battle.strikeIndex(intent) + 1)])
@pytest.mark.parametrize('elapsed', [0, 300])
def test_berserker_attacks_instead_of_observing_or_blocking_any_threat(duel, intent, phase, elapsed):
    duel.session['bot'].update(profile='berserker', tempo=100)
    battle.startAction(duel, intent)
    duel.session['action']['index'] = phase
    observe(duel)
    observe(duel, elapsed)
    duel.session['pending'][battle.ACTION_TIMER] = duel.now + 50
    assert decision.defend(duel, duel.context['botWorld']) is None
    choice = choose(duel)
    assert choice.kind == 'attack' and choice.intent in battle.ATTACK_CHAINS


@pytest.mark.parametrize('lock', ['attack', 'hurt', 'parried', 'terrain-rebound'])
def test_berserker_still_finishes_weapon_actions_and_hit_reactions(duel, lock):
    value = duel.session['bot']
    value['profile'] = 'berserker'
    if lock == 'attack':
        battle.startAction(duel, 300550, released=True, actorId=2)
    elif lock == 'hurt':
        value['hurtUntil'] = duel.now + 400
    else:
        value['action'] = {'index': -1, 'reaction': lock}
    observe(duel)
    previous = deepcopy(duel.state)
    assert choose(duel).reason == 'action-locked'
    assert duel.state == previous


@pytest.mark.parametrize('obstacle', ['range', 'height', 'terrain', 'unknown-terrain', 'facing'])
def test_berserker_still_requires_valid_attack_contact(duel, obstacle):
    duel.session['bot']['profile'] = 'berserker'
    observe(duel)
    world = duel.context['botWorld']
    if obstacle == 'range': world['player'][0] = 5
    if obstacle == 'height': world['player'][2] += 2
    if obstacle == 'facing': world['botHeading'] = 180
    if obstacle == 'terrain':
        world['terrain'][1]['target'].update(hit=True, material=4, position=[.7, 0, 1.2],
                                              normal=[-1, 0, 0], direction=[1, 0, 0])
    if obstacle == 'unknown-terrain': world['terrain'][1]['target']['ready'] = False
    assert choose(duel).reason == 'contact-unavailable'


def test_berserker_repeats_attacks_without_extra_wait_or_changing_weapon_timing(nearBot):
    flow = nearBot
    value = flow.session['bot']
    value.update(profile='berserker', tempo=100)
    battle.startAction(flow, 300560, released=False)
    for count in range(1, 4):
        snapshot(flow, (1.5, 0, .2))
        bot.think(flow)
        assert value['attackCount'] == count
        intent = value['action']['intent']
        assert intent in battle.ATTACK_CHAINS
        assert value['action']['index'] == 0
        assert flow.session['pending'][bot.ACTION_TIMER] - flow.now == battle.phaseMs(intent, 0)
        assert bot.RELEASE_TIMER not in flow.session['pending']
        while value['action']['index'] >= 0:
            flow.now = flow.session['pending'][bot.ACTION_TIMER]
            snapshot(flow, (1.5, 0, .2))
            app.handleTimer(flow, bot.ACTION_TIMER)


@pytest.mark.parametrize('condition', ['healthy', 'low-health', 'recovery', 'hurt', 'parried'])
def test_berserker_keeps_pressing_instead_of_defensive_backpedalling(multi, condition):
    world = fight(multi, {1: ((1.6, 0, .2), 180), 2: ((0, 0, .2), 0)})
    value = actors.data(multi, 2)
    value.update(profile='berserker', tempo=100)
    if condition == 'low-health': value['health'] = 20
    if condition == 'recovery': value['action'] = {'intent': 300550, 'index': 3}
    if condition == 'hurt': value['hurtUntil'] = multi.now + 400
    if condition == 'parried': value['action'] = {'reaction': 'parried'}
    pair = actors.pair(world, 2, 1)
    choice = bot_footwork.plan(multi, bot.navigationFor(multi), pair, 2)
    assert choice is not None and choice.mode == 'press'
    assert .8 < math.dist(choice.goal[:2], pair['player'][:2]) <= 1.1


@pytest.mark.parametrize('distance', [.75, 1.])
def test_berserker_does_not_retreat_or_circle_at_close_range(multi, distance):
    world = fight(multi, {1: ((distance, 0, .2), 180), 2: ((0, 0, .2), 0)})
    actors.data(multi, 2).update(profile='berserker', tempo=100, health=20)
    assert bot_footwork.plan(multi, bot.navigationFor(multi), actors.pair(world, 2, 1), 2) is None
