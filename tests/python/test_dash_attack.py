import math
import struct

import pytest

from Business.scripts import app, battle, combat
from Business.scripts.codec import battle_flow
from test_battle_actions import ATTACKS, BLOCKS, makeFlow, advanceAction, animationStates, sendAction
from test_melee_combat import arena, advance, hp, states
from test_terrain_contact import obstacle


DASH = 700250
# Held W belongs to the movement cache, not this combat-key bitmap.
READY_KEYS = (1 << 26) | (1 << 21)


def request(flow, keys=READY_KEYS):
    app.handleMessage(flow, {"command": 4, "body": struct.pack(">HIib", 1, keys, DASH, 0)})


def test_dash_starts_strike_without_sending_preparation_or_waiting_for_a_timer():
    flow = makeFlow()
    startedAt = flow.now
    request(flow)
    assert animationStates(flow) == [439]
    assert battle.actionState(flow)["index"] == battle.strikeIndex(DASH)
    assert battle.phaseMs(DASH, 0) == 0
    assert flow.session["pending"][battle.ACTION_TIMER] == startedAt + 2033
    phaseTimers = [item for item in flow.result["timers"]
                   if item["event"]["name"] == battle.ACTION_TIMER]
    assert [item["delayMs"] for item in phaseTimers] == [2033]


def test_dash_returns_directly_to_idle_after_the_full_strike():
    flow = makeFlow()
    request(flow)
    assert animationStates(flow) == [439]
    assert flow.session["pending"][battle.ACTION_TIMER] == flow.now + 2033
    request(flow)
    assert "buffered" not in flow.session["action"]
    assert animationStates(flow) == [439]
    strikeStartedAt = flow.now
    assert flow.session["pending"][battle.ACTION_TIMER] == strikeStartedAt + 2033
    sendAction(flow, battle.ATTACK_RELEASE)
    assert animationStates(flow) == [439]
    for elapsed in (650, 966, 967, 1400, 2032):
        flow.now = strikeStartedAt + elapsed
        app.handleTimer(flow, battle.ACTION_TIMER)
        assert animationStates(flow) == [439]
        assert flow.session["pending"][battle.ACTION_TIMER] == strikeStartedAt + 2033
    advanceAction(flow)
    assert animationStates(flow) == [439, 2]
    assert battle.ACTION_TIMER not in flow.session["pending"]


@pytest.mark.parametrize("requestDelayMs", [0, 250])
def test_dash_syncs_animation_duration_separately_from_state_start(requestDelayMs):
    flow = makeFlow()
    flow.now += requestDelayMs
    request(flow)
    strikeStartedAt = flow.now
    assert flow.session["pending"][battle.ACTION_TIMER] == strikeStartedAt + 2033
    advanceAction(flow)
    packets = [battle_flow.decode_battle_state_sync_simple(item["body"])
               for item in flow.result["send"]]
    assert [(packet.state, packet.state_time_ms) for packet in packets] == [
        (439, 2033), (2, 0),
    ]
    assert [packet.state_change_ms for packet in packets] == [
        strikeStartedAt - 100, strikeStartedAt + 2033 - 100,
    ]


@pytest.mark.parametrize("state,duration", [(439, 2033), (443, 0), (combat.HURT_STATE, 0), (235, 0)])
def test_targeted_sync_limits_dash_duration_to_the_strike(state, duration):
    flow = makeFlow()
    request(flow)
    battle.syncState(flow, state, targetId=2)
    body = flow.result["send"][-1]["body"]
    assert struct.unpack_from(">H", body)[0] == battle_flow.STATE_SYNC
    assert struct.unpack_from(">H", body, 6)[0] == state
    assert struct.unpack_from(">H", body, 12)[0] == duration


@pytest.mark.parametrize("keys", [0, 1 << 21, 1 << 7, 1 << 26,
                                 (1 << 15) | (1 << 21), (1 << 15) | (1 << 26)])
def test_dash_requires_native_running_and_attack_keys(keys):
    flow = makeFlow()
    request(flow, keys)
    assert not flow.result["send"]


def test_smart_attack_mode_uses_the_same_running_chain():
    flow = makeFlow()
    request(flow, (READY_KEYS & ~(1 << 21)) | (1 << 7) | (1 << 27))
    assert animationStates(flow) == [439]


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
    advanceAction(flow)
    assert animationStates(flow) == [439, 235]
    flow.session["leaving"] = True
    advanceAction(flow)
    assert battle.actionState(flow)["index"] == -1


@pytest.mark.parametrize("elapsed", [0, 966, 967, 2032])
@pytest.mark.parametrize("intent,chain", ATTACKS + BLOCKS)
@pytest.mark.parametrize("released", [False, True])
def test_dash_buffers_only_held_followup_actions(elapsed, intent, chain, released):
    flow = makeFlow()
    request(flow)
    flow.now += elapsed
    previous = animationStates(flow)
    deadline = flow.session["pending"][battle.ACTION_TIMER]
    sendAction(flow, intent)
    if released:
        release = battle.ATTACK_RELEASE if intent in battle.ATTACK_CHAINS else battle.BLOCK_RELEASE
        sendAction(flow, release)
    assert animationStates(flow) == previous
    assert flow.session["pending"][battle.ACTION_TIMER] == deadline
    advanceAction(flow)
    assert animationStates(flow) == [439, 2 if released else chain[0]]
    assert battle.actionState(flow).get("buffered") is None


def test_dash_contact_hits_once_with_offline_bonus_damage(arena):
    assert combat.hitTimer(1) not in arena.session["pending"]
    request(arena)
    assert arena.session["pending"][combat.hitTimer(1)] == arena.now
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 60
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 60
    advance(arena, battle.ACTION_TIMER)
    assert combat.hitTimer(1) not in arena.session["pending"]


@pytest.mark.parametrize("elapsed", [1134, 1500, 2032])
def test_full_dash_animation_does_not_extend_the_damage_window(arena, elapsed):
    request(arena)
    strikeStartedAt = arena.now
    assert arena.session["swing"]["until"] == strikeStartedAt + 1133
    arena.now = strikeStartedAt + elapsed
    arena.context["botWorld"]["sampledAt"] = arena.now
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert "swing" not in arena.session
    assert combat.hitTimer(1) not in arena.session["pending"]
    assert states(arena)[-1] == (1, 439)
    assert arena.session["pending"][battle.ACTION_TIMER] == strikeStartedAt + 2033


@pytest.mark.parametrize("elapsed", [0, 1, 119, 120, 550, 551, 966, 967, 1100, 1133])
def test_dash_can_hit_throughout_the_original_contact_window(arena, elapsed):
    request(arena)
    arena.now += elapsed
    arena.context["botWorld"]["sampledAt"] = arena.now
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 60


def test_dash_retries_until_the_contact_window_endpoint(arena):
    from blade_fixture import contact, publish
    arena.context["botWorld"]["bot"] = [4., 0., .2]
    publish(arena, 1, [])
    request(arena)
    strikeStartedAt = arena.now
    for elapsed in range(0, 1101, 50):
        assert arena.session["pending"][combat.hitTimer(1)] == strikeStartedAt + elapsed
        advance(arena, combat.hitTimer(1))
        assert hp(arena, 2) == 100
    assert arena.session["pending"][combat.hitTimer(1)] == strikeStartedAt + 1133
    arena.context["botWorld"]["bot"] = [1.5, 0., .2]
    publish(arena, 1, [contact()])
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 60
    assert combat.hitTimer(1) not in arena.session["pending"]


@pytest.mark.parametrize("heading", [0, 90, -90, 175])
@pytest.mark.parametrize("hit", [False, True])
def test_dash_follows_native_blade_contact_not_actor_sector(arena, heading, hit):
    from blade_fixture import contact, publish
    request(arena)
    arena.context["botWorld"].update(
        player=[5., 6., 0.], playerHeading=heading,
        bot=[8., 6., 2.])
    publish(arena, 1, [contact()] if hit else [])
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == (60 if hit else 100)


@pytest.mark.parametrize("obstructed", [False, True])
def test_dash_ignores_contact_sampled_before_the_strike(arena, obstructed):
    request(arena)
    arena.context["botWorld"]["sampledAt"] = arena.now - 1
    if obstructed:
        obstacle(arena)
    advance(arena, combat.hitTimer(1), fresh=False)
    assert hp(arena, 2) == 100
    assert states(arena)[-1] == (1, 439)
    assert battle.actionState(arena).get("reaction") is None


def test_dash_can_be_parried_and_interrupts_its_contact_window(arena):
    battle.startAction(arena, 300600, actorId=2)
    advance(arena, battle.actionTimer(2))
    request(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert (1, 443) in states(arena)
    assert combat.hitTimer(1) not in arena.session["pending"]
    assert arena.session["pending"][battle.ACTION_TIMER] == arena.now + 350


def test_dash_rebounds_on_terrain_without_damage_or_buffered_attack(arena):
    request(arena)
    sendAction(arena, 300560)
    obstacle(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert states(arena)[-1] == (1, 443)
    assert "buffered" not in battle.actionState(arena)
    assert combat.hitTimer(1) not in arena.session["pending"]
    assert arena.session["pending"][battle.ACTION_TIMER] == arena.now + 350
    advance(arena, battle.ACTION_TIMER)
    assert states(arena)[-1] == (1, 2)


def test_hurt_cancels_dash_timers_and_contact(arena):
    request(arena)
    combat.applyHit(arena, 2, 1, arena.context["botWorld"])
    assert battle.ACTION_TIMER not in arena.session["pending"]
    assert combat.hitTimer(1) not in arena.session["pending"]
    app.handleTimer(arena, battle.ACTION_TIMER)
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert states(arena)[-1] == (1, combat.HURT_STATE)
