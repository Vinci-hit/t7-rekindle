import struct

import pytest

from Business.scripts import app, battle, combat
from test_battle_actions import makeFlow, advanceAction, animationStates, sendAction
from test_melee_combat import arena, advance, hp, states
from test_terrain_contact import obstacle


DASH = 700250
# Held W belongs to the movement cache, not this combat-key bitmap.
READY_KEYS = (1 << 26) | (1 << 21)


def request(flow, keys=READY_KEYS):
    app.handleMessage(flow, {"command": 4, "body": struct.pack(">HIib", 1, keys, DASH, 0)})


def test_dash_runs_three_phases_without_waiting_for_key_up():
    flow = makeFlow()
    request(flow)
    assert animationStates(flow) == [438]
    assert flow.session["pending"][battle.ACTION_TIMER] == flow.now + 200
    request(flow)
    assert "buffered" not in flow.session["action"]
    advanceAction(flow)
    assert animationStates(flow) == [438, 439]
    assert flow.session["pending"][battle.ACTION_TIMER] == flow.now + 650
    sendAction(flow, battle.ATTACK_RELEASE)
    assert animationStates(flow) == [438, 439]
    advanceAction(flow)
    assert animationStates(flow) == [438, 439, 440]
    assert flow.session["pending"][battle.ACTION_TIMER] == flow.now + 350
    advanceAction(flow)
    assert animationStates(flow) == [438, 439, 440, 2]
    assert battle.ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("keys", [0, 1 << 21, 1 << 7, 1 << 26,
                                 (1 << 15) | (1 << 21), (1 << 15) | (1 << 26)])
def test_dash_requires_native_running_and_attack_keys(keys):
    flow = makeFlow()
    request(flow, keys)
    assert not flow.result["send"]


def test_smart_attack_mode_uses_the_same_running_chain():
    flow = makeFlow()
    request(flow, (READY_KEYS & ~(1 << 21)) | (1 << 7) | (1 << 27))
    assert animationStates(flow) == [438]


@pytest.mark.parametrize("condition", ["prepare", "leaving", "hurt", "attack", "block", "reaction"])
def test_dash_cannot_interrupt_or_queue_while_unavailable(condition):
    flow = makeFlow()
    if condition == "prepare": flow.session["groundEnabled"] = False
    if condition == "leaving": flow.session["leaving"] = True
    if condition == "hurt": flow.session["hurtUntil"] = 2000
    if condition == "attack": sendAction(flow, 300560)
    if condition == "block": sendAction(flow, 300580)
    if condition == "reaction": battle.actionState(flow)["reaction"] = "parried"
    flow.result["send"].clear()
    request(flow)
    assert not flow.result["send"]
    assert "buffered" not in battle.actionState(flow)


def test_dash_cancels_on_leave_and_can_be_followed_by_normal_attack():
    flow = makeFlow()
    request(flow)
    sendAction(flow, 300560)
    sendAction(flow, battle.ATTACK_RELEASE)
    for _ in range(3): advanceAction(flow)
    assert animationStates(flow) == [438, 439, 440, 235]
    flow.session["leaving"] = True
    advanceAction(flow)
    assert battle.actionState(flow)["index"] == -1


def test_dash_contact_hits_once_with_offline_bonus_damage(arena):
    request(arena)
    assert combat.hitTimer(1) not in arena.session["pending"]
    advance(arena, battle.ACTION_TIMER)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 60
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 60
    advance(arena, battle.ACTION_TIMER)
    assert combat.hitTimer(1) not in arena.session["pending"]


def test_dash_can_be_parried_and_interrupts_its_contact_window(arena):
    battle.startAction(arena, 300600, actorId=2)
    advance(arena, battle.actionTimer(2))
    request(arena)
    advance(arena, battle.ACTION_TIMER)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert (1, 443) in states(arena)
    assert combat.hitTimer(1) not in arena.session["pending"]
    assert arena.session["pending"][battle.ACTION_TIMER] == arena.now + 350


def test_dash_rebounds_on_terrain_without_damage_or_buffered_attack(arena):
    request(arena)
    sendAction(arena, 300560)
    advance(arena, battle.ACTION_TIMER)
    obstacle(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert states(arena)[-1] == (1, 443)
    assert "buffered" not in battle.actionState(arena)
    assert combat.hitTimer(1) not in arena.session["pending"]
    advance(arena, battle.ACTION_TIMER)
    assert states(arena)[-1] == (1, 2)


def test_hurt_cancels_dash_timers_and_contact(arena):
    request(arena)
    advance(arena, battle.ACTION_TIMER)
    combat.applyHit(arena, 2, 1, arena.context["botWorld"])
    assert battle.ACTION_TIMER not in arena.session["pending"]
    assert combat.hitTimer(1) not in arena.session["pending"]
    app.handleTimer(arena, battle.ACTION_TIMER)
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert states(arena)[-1] == (1, combat.HURT_STATE)
