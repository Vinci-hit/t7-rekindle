from copy import deepcopy
import struct

import pytest

from Business.scripts import app, battle, bot, combat
from Business.scripts.codec import battle_flow
from test_melee_combat import arena, advance, hp, states, swing
from blade_fixture import contact as nativeContact, publish


def obstacle(flow, actor=1, *, probe="target", material=4):
    contact = nativeContact(0, material=material, position=(.7, 0., 1.2), along=.2)
    flow.context["botWorld"]["terrain"][actor - 1][probe] = dict(contact)
    publish(flow, actor, [contact, nativeContact(3 - actor, along=.8)])
    return contact


@pytest.mark.parametrize("actor", [1, 2])
@pytest.mark.parametrize("intent,state", [(300560, 466), (300550, 468), (300570, 467), (300540, 469)])
def test_terrain_interrupts_once_with_directional_rebound(arena, actor, intent, state):
    swing(arena, actor, intent)
    action = battle.actionState(arena, actor)
    action["buffered"] = {"intent": 300550, "released": True}
    obstacle(arena, actor)
    advance(arena, combat.hitTimer(actor))
    assert hp(arena, 1) == hp(arena, 2) == combat.MAX_HEALTH
    assert states(arena)[-1] == (actor, state)
    assert action["reaction"] == "terrain-rebound" and action["index"] == -1
    assert "buffered" not in action
    assert combat.hitTimer(actor) not in arena.session["pending"]
    packets = [p for p in arena.result["send"] if p["reason"] == "combat-terrain-hit"]
    assert len(packets) == 1
    body = packets[0]["body"]
    hit = battle_flow.decode_battle_hit_point_notify(body)
    assert hit.attacker_mid == actor and hit.target_mid == 0
    assert struct.unpack_from(">i", body, 47)[0] == 4
    assert struct.unpack_from(">fff", body, 2) == pytest.approx((.7, 0., 1.2))
    assert struct.unpack_from(">4f", body, 15) == (1., 0., 0., 0.)
    assert struct.unpack_from(">4f", body, 31) == (-1., 0., 0., 0.)
    assert struct.unpack_from(">B", body, 87)[0] == 1
    sent = deepcopy(arena.result["send"])
    app.handleTimer(arena, combat.hitTimer(actor))
    battle.startAction(arena, 300540, actorId=actor)
    assert arena.result["send"] == sent
    advance(arena, battle.actionTimer(actor))
    assert states(arena)[-1] == (actor, battle_flow.ACT_STATE_IDLE)
    assert app.validateState(arena.state)


@pytest.mark.parametrize("material", [-1, 0, 1, 2, 3, 4, 5, 6, 7])
def test_actual_material_is_preserved_not_replaced_with_stone(arena, material):
    swing(arena)
    obstacle(arena, material=material)
    advance(arena, combat.hitTimer(1))
    body = next(p["body"] for p in arena.result["send"] if p["reason"] == "combat-terrain-hit")
    assert struct.unpack_from(">i", body, 47)[0] == material


def test_free_swing_hits_forward_terrain_without_a_nearby_enemy(arena):
    arena.context["botWorld"]["bot"] = [10., 0., .2]
    obstacle(arena, probe="forward")
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert states(arena)[-1] == (1, 466)
    assert hp(arena, 2) == combat.MAX_HEALTH


def test_terrain_after_target_keeps_earlier_damage_and_then_interrupts(arena):
    obstacle(arena, probe="forward")["along"] = .9
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == combat.MAX_HEALTH - combat.DAMAGE
    assert any(p["reason"] == "combat-terrain-hit" for p in arena.result["send"])


@pytest.mark.parametrize("attacker", [1, 2])
@pytest.mark.parametrize("attack,defence,rebound", [
    (300560, 300580, 466), (300540, 300590, 469), (300540, 300610, 469),
    (300550, 300590, 468), (300550, 300610, 468),
])
def test_wall_takes_priority_over_enemy_parry(arena, attacker, attack, defence, rebound):
    defender = 3 - attacker
    battle.startAction(arena, defence, actorId=defender)
    advance(arena, battle.actionTimer(defender))
    obstacle(arena, attacker)
    swing(arena, attacker, attack)
    advance(arena, combat.hitTimer(attacker))
    assert states(arena)[-1] == (attacker, rebound)
    assert battle.actionState(arena, defender).get("reaction") is None
    assert hp(arena, 1) == hp(arena, 2) == combat.MAX_HEALTH
    assert not any("combat-parry" in line for line in arena.result["logs"])


@pytest.mark.parametrize("failure", ["missing", "unready", "nan", "stale", "epoch", "pre-swing"])
def test_unknown_or_stale_terrain_never_becomes_a_clear_hit(arena, failure):
    contact = obstacle(arena)
    swing(arena)
    if failure == "missing": arena.context["botWorld"].pop("blades")
    if failure == "unready": contact["ready"] = False
    if failure == "nan": contact["normal"][0] = float("nan")
    if failure == "stale": arena.context["botWorld"]["sampledAt"] -= 1000
    if failure == "epoch": arena.context["botWorld"]["instanceStartedAt"] += 1
    if failure == "pre-swing": arena.context["botWorld"]["sampledAt"] = arena.now - 1
    advance(arena, combat.hitTimer(1), fresh=False)
    assert hp(arena, 2) == combat.MAX_HEALTH
    assert battle.actionState(arena).get("reaction") is None


def test_bot_rebound_keeps_movement_but_cancels_scheduled_release(arena):
    swing(arena, 2)
    obstacle(arena, 2)
    arena.session["bot"]["moving"] = True
    arena.later(bot.MOVE_TIMER, 10)
    arena.later(bot.RELEASE_TIMER, 10)
    advance(arena, combat.hitTimer(2))
    assert bot.MOVE_TIMER in arena.session['pending']
    assert arena.session['bot']['moving']
    assert bot.RELEASE_TIMER not in arena.session["pending"]
    bot.think(arena)
    assert arena.session["bot"]["status"] == "terrain-rebound"


def test_rebound_hot_reload_preserves_deadline_and_epoch_change_discards_it(arena):
    swing(arena)
    obstacle(arena)
    advance(arena, combat.hitTimer(1))
    assert app.migrateState(app.STATE_VERSION, arena.state) == arena.state
    arena.context["botWorld"]["instanceStartedAt"] += 1
    arena.result["send"].clear()
    advance(arena, battle.ACTION_TIMER)
    assert arena.result["send"] == []
    assert "reaction" not in battle.actionState(arena)
