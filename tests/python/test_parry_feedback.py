from damage_fixture import NORMAL_DAMAGE
from copy import deepcopy
import math
import struct

import pytest

from Business.scripts import app, battle, bot, combat, scene
from test_melee_combat import advance, arena, hp, states, swing
from blade_fixture import contact, publish
from Business.scripts import weapons

REACTIONS = [(300560, 300580, 240, 231), (300550, 300610, 246, 234),
             (300570, 300600, 252, 233), (300540, 300590, 258, 232),
             (300550, 300590, 246, 232), (300540, 300610, 258, 234)]


def block(flow, attacker=1, attack=300560, defence=300580):
    defender = 3 - attacker
    battle.startAction(flow, defence, actorId=defender)
    advance(flow, battle.actionTimer(defender))
    swing(flow, attacker, attack)
    publish(flow, attacker, [contact(defender, defenceWeaponId=weapons.equipped(battle.actorData(flow, defender)))])
    battle.actionState(flow, attacker)["buffered"] = {"intent": 300550, "released": True}
    flow.result["send"].clear()
    advance(flow, combat.hitTimer(attacker))


@pytest.mark.parametrize("attacker", [1, 2])
@pytest.mark.parametrize("attack,defence,blocked,success", REACTIONS)
def test_success_interrupts_both_phases_with_direction_specific_reactions(
        arena, attacker, attack, defence, blocked, success):
    defender = 3 - attacker
    block(arena, attacker, attack, defence)
    assert states(arena) == [(attacker, blocked), (defender, success)]
    assert hp(arena, 1) == hp(arena, 2) == 100
    assert combat.hitTimer(attacker) not in arena.session["pending"]
    assert arena.session["pending"][battle.actionTimer(defender)] == arena.now
    assert arena.session["pending"][battle.actionTimer(attacker)] == arena.now + battle.phaseMs(attack, -1)
    for actorId in (attacker, defender):
        action = battle.actionState(arena, actorId)
        assert action["index"] == -1 and "buffered" not in action
        assert "reaction" in action
    for packet in arena.result["send"]:
        if packet["command"] == 4 and packet["body"][:2] == b"\0\4":
            values = struct.unpack(">HHHHIHBQb", packet["body"])
            assert values[0] == 4 and values[-2] == 3 - values[1]
    # The obsolete attack timer must not play the original swing/recovery again.
    advance(arena, battle.actionTimer(attacker))
    assert states(arena)[-1] == (attacker, 2)
    assert battle.actionState(arena, attacker)["index"] == -1
    assert combat.hitTimer(attacker) not in arena.session["pending"]


@pytest.mark.parametrize("attacker", [1, 2])
@pytest.mark.parametrize("attack", [300540, 300550])
@pytest.mark.parametrize("defence", [300590, 300610])
@pytest.mark.parametrize("phase", ["prepare", "released", "idle"])
def test_lateral_parry_requires_an_unreleased_hold(arena, attacker, attack, defence, phase):
    defender = 3 - attacker
    if phase != "idle":
        battle.startAction(arena, defence, actorId=defender)
    if phase == "released":
        advance(arena, battle.actionTimer(defender))
        battle.releaseAction(arena, battle.BLOCK_RELEASE, actorId=defender)
    swing(arena, attacker, attack)
    advance(arena, combat.hitTimer(attacker))
    assert hp(arena, defender) == combat.MAX_HEALTH - NORMAL_DAMAGE
    assert battle.actionState(arena, attacker).get("reaction") is None
    assert (defender, combat.HURT_STATE) in states(arena)


@pytest.mark.parametrize("released", [False, True])
def test_defender_keeps_hold_or_finishes_after_success_without_cutting_feedback(arena, released):
    block(arena, attacker=2)
    deadline = arena.session["pending"][battle.ACTION_TIMER]
    assert deadline == arena.now
    before = list(arena.result["send"])
    if released:
        battle.releaseAction(arena, battle.BLOCK_RELEASE)
        assert arena.result["send"] == before
        assert arena.session["pending"][battle.ACTION_TIMER] == deadline
    advance(arena, battle.ACTION_TIMER)
    assert states(arena)[-1] == (1, 2 if released else 223)
    assert battle.actionState(arena)["index"] == (-1 if released else 1)


def test_attack_interrupts_hold_only_after_parry_success_finishes(arena):
    block(arena, attacker=2)
    before = deepcopy(arena.result['send'])
    deadline = arena.session['pending'][battle.ACTION_TIMER]
    body = struct.pack(">HIib", 1, 0, 300560, 0)
    assert battle.message(arena, 4, 1, body)
    assert arena.result['send'] == before
    assert arena.session['pending'][battle.ACTION_TIMER] == deadline
    assert arena.session['action']['reaction'] == 'parry-success'
    advance(arena, battle.ACTION_TIMER)
    assert states(arena)[-1] == (1, 235)
    assert battle.actionState(arena)["index"] == 0


def test_bot_think_and_key_spam_do_not_overwrite_block_reactions(arena):
    block(arena)
    original = deepcopy(arena.session["action"])
    arena.result["send"].clear()
    bot.think(arena)
    for intent in (300540, battle.ATTACK_RELEASE, 300580, battle.BLOCK_RELEASE):
        body = struct.pack(">HIib", 1, 0, intent, 0)
        assert battle.message(arena, 4, 1, body)
    battle.startAction(arena, 300560, actorId=2)
    assert not arena.result["send"]
    assert arena.session["action"] == {**original, "buffered": {"intent": 300580, "released": True}}
    assert arena.session["bot"]["status"] == "parry-success"
    advance(arena, battle.ACTION_TIMER)
    assert states(arena)[-1] == (1, 2)
    assert "buffered" not in battle.actionState(arena)


def test_bot_auto_release_before_success_timer_preserves_feedback_state(arena):
    block(arena)
    arena.later(bot.RELEASE_TIMER, 0)
    advance(arena, bot.RELEASE_TIMER)
    assert battle.actionState(arena, 2)["released"] is True
    assert states(arena)[-1] == (2, 231)
    advance(arena, battle.actionTimer(2))
    assert states(arena)[-1] == (2, 2)


def test_leave_and_epoch_change_discard_reactions_without_replaying_them(arena):
    block(arena)
    arena.context["botWorld"]["instanceStartedAt"] += 1
    arena.result["send"].clear()
    advance(arena, battle.ACTION_TIMER)
    assert not arena.result["send"]
    assert "reaction" not in battle.actionState(arena)
    scene.leave(arena, struct.pack(">Hb", 2, 0))
    assert set(arena.session["pending"]) == {"logout"}


@pytest.mark.parametrize("degrees", [90., -90., 180.])
def test_contact_uses_current_snapshot_facing_not_old_heading_report(arena, degrees):
    yaw = math.radians(degrees)
    arena.session["bot"]["position"] = [1.5 * math.cos(yaw), -1.5 * math.sin(yaw), .2]
    arena.context["botWorld"].update(bot=list(arena.session["bot"]["position"]),
                                    playerHeading=degrees, botHeading=(degrees + 360) % 360 - 180)
    assert arena.session["ground"]["heading"] == 0
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 80


def test_clear_native_blade_does_not_hit_even_when_actor_is_close(arena):
    arena.context["botWorld"]["playerHeading"] = 180.
    arena.context["botWorld"]["blades"]["1"]["contacts"] = []
    swing(arena)
    while combat.hitTimer(1) in arena.session["pending"]:
        advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    misses = [log for log in arena.result["logs"] if log["message"].startswith("combat-miss")]
    assert len(misses) == 1 and "reason=blade-clear" in misses[0]["message"]


@pytest.mark.parametrize("value", [None, math.nan, math.inf, 181.])
def test_missing_or_invalid_facing_is_not_replaced_with_zero(arena, value):
    arena.context["botWorld"]["playerHeading"] = value
    swing(arena)
    while combat.hitTimer(1) in arena.session["pending"]:
        advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == 100
    assert any("reason=blade-unavailable" in log["message"] for log in arena.result["logs"])
