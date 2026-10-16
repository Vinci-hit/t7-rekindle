"""Wire clocks and server deadlines for every shipped hero/weapon profile."""
from copy import deepcopy
import struct

import pytest

from Business.scripts import battle, bot, combat, combat_resources, weapons
from Business.scripts.combat_profile_data import HERO_WEAPONS
from test_battle_actions import makeFlow, advanceAction, sendAction
from test_melee_combat import arena, advance, swing


def configured(profile):
    flow = makeFlow()
    flow.session.update(weaponId=profile['weaponId'], weaponProfiles={str(profile['weaponId']): deepcopy(profile)})
    return flow


def lastClock(flow):
    packet = next(p for p in reversed(flow.result['send']) if p['command'] == 4 and p['body'][:2] in (b'\0\3', b'\0\4'))
    return struct.unpack_from('>H', packet['body'], 12)[0]


@pytest.mark.parametrize('heroId', HERO_WEAPONS)
def test_all_supported_weapon_action_clocks_match_server_timers(heroId):
    for profile in combat_resources.loadCombatProfiles('', heroId).values():
        if profile['shield']:
            continue
        for intent in battle.ATTACK_CHAINS:
            flow = configured(profile)
            attack = profile['attacks'][str(intent)]
            battle.startAction(flow, intent)
            assert lastClock(flow) == attack['phases'][0] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            advanceAction(flow)
            assert lastClock(flow) == 0 and battle.ACTION_TIMER not in flow.session['pending']
            battle.releaseAction(flow, battle.ATTACK_RELEASE)
            assert lastClock(flow) == attack['phases'][2] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            advanceAction(flow)
            assert lastClock(flow) == attack['phases'][3] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            battle.parryReaction(flow, 1, 2, success=False, intent=intent)
            assert lastClock(flow) == attack['parried'] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            battle.terrainReaction(flow, 1, intent=intent)
            assert lastClock(flow) == attack['rebound'] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
        flow = configured(profile)
        battle.syncState(flow, weapons.CHANGE_STATE)
        assert lastClock(flow) == weapons.changeMs(flow.session) == profile['changeMs']
        dash = profile['dashAttack']
        if dash and dash['enabled']:
            battle.startAction(flow, battle.DASH_ATTACK)
            assert lastClock(flow) == dash['durationMs'] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            battle.terrainReaction(flow, 1)
            assert lastClock(flow) == dash['reboundMs'] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
        for intent in battle.BLOCK_CHAINS:
            flow = configured(profile)
            sendAction(flow, intent)
            assert lastClock(flow) == 0 == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            advanceAction(flow)
            assert lastClock(flow) == 0 and battle.ACTION_TIMER not in flow.session['pending']
            battle.parryReaction(flow, 1, 2, success=True, intent=intent)
            assert lastClock(flow) == 0 == flow.session['pending'][battle.ACTION_TIMER] - flow.now
        if profile['hand'] == 2:
            flow = configured(profile)
            flow.session['offhandId'] = 703
            battle.startAction(flow, battle.SHIELD_GUARD)
            assert lastClock(flow) == profile['guard']['prepareMs'] == flow.session['pending'][battle.ACTION_TIMER] - flow.now
            advanceAction(flow)
            assert lastClock(flow) == 0 and battle.ACTION_TIMER not in flow.session['pending']
            battle.parryReaction(flow, 1, 2, success=True)
            assert lastClock(flow) == profile['guard']['successMs'] == flow.session['pending'][battle.ACTION_TIMER] - flow.now


def test_hurt_uses_selected_profile_for_both_notification_and_deadline(arena):
    flow = arena
    profile = combat_resources.defaultProfile()
    flow.session.update(weaponId=profile['weaponId'], weaponProfiles={str(profile['weaponId']): profile})
    swing(flow, 2)
    advance(flow, combat.hitTimer(2))
    assert lastClock(flow) == profile['hurtMs'] == flow.session['pending'][combat.hurtTimer(1)] - flow.now
    assert flow.session['hurtUntil'] == flow.now + profile['hurtMs']


def test_bot_refresh_repeats_full_duration_not_elapsed_time(arena):
    flow = arena
    battle.syncState(flow, combat.HURT_STATE, 2, targetId=1, durationMs=550)
    flow.now += 123
    bot.refresh(flow)
    assert lastClock(flow) == 550
    battle.syncState(flow, 223, 2)
    flow.now += 100
    bot.refresh(flow)
    assert lastClock(flow) == 0
