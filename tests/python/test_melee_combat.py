from copy import deepcopy
import math
from pathlib import Path
import struct

import pytest

from Business.scripts import app, battle, bot, combat, contracts as wire, controls, scene
from Business.scripts.codec import attr_flow, battle_flow
from Business.scripts.navigation import NavMesh
from test_bot_player import flowAt, snapshot


@pytest.fixture
def arena(monkeypatch):
    nav = NavMesh.fromPolygons([[(-10, -10, 0), (10, -10, 0), (10, 10, 0), (-10, 10, 0)]])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: nav)
    flow = flowAt()
    flow.session["bot"].update(position=[1.5, 0., .2], heading=180)
    controls.groundState(flow)["heading"] = 0
    combat.initialize(flow)
    snapshot(flow, (0., 0., .2))
    return flow


def advance(flow, name, *, fresh=True):
    flow.now = flow.session["pending"][name]
    if fresh:
        flow.context["botWorld"]["sampledAt"] = flow.now
    app.handleTimer(flow, name)


def swing(flow, actorId=wire.ACTOR_ID, intent=300560):
    battle.startAction(flow, intent, released=True, actorId=actorId)
    advance(flow, battle.actionTimer(actorId))
    assert battle.actionState(flow, actorId)["index"] == 2


def hp(flow, actorId):
    return battle.actorData(flow, actorId)["health"]


def states(flow):
    return [(struct.unpack_from(">H", p["body"], 2)[0], struct.unpack_from(">H", p["body"], 6)[0])
            for p in flow.result["send"] if p["command"] == 4 and p["body"][:2] in (b"\0\3", b"\0\4")]


@pytest.mark.parametrize("attacker,target", [(1, 2), (2, 1)])
def test_swing_hits_once_updates_only_target_and_recovers(arena, attacker, target):
    flow = arena
    swing(flow, attacker)
    advance(flow, combat.hitTimer(attacker))
    assert hp(flow, target) == combat.MAX_HEALTH - combat.DAMAGE
    assert hp(flow, attacker) == combat.MAX_HEALTH
    assert (target, combat.HURT_STATE) in states(flow)
    assert combat.hitTimer(attacker) not in flow.session["pending"]
    before = hp(flow, target)
    app.handleTimer(flow, combat.hitTimer(attacker))
    assert hp(flow, target) == before
    healthPackets = [p for p in flow.result["send"] if p["reason"] == f"combat-health-{target}"]
    assert len(healthPackets) == 1
    packet = healthPackets[0]
    if target == 2:
        assert packet["command"] == attr_flow.ATTR_COMMAND
        assert struct.unpack(">HQiiiiiQ", packet["body"])[1:4] == (2, 1, before)
    else:
        assert packet["command"] == 4
        assert battle_flow.decode_battle_data_update_notify(packet["body"]).result_data == before
    assert not any(p["command"] == 4 and p["body"][:2] in (b"\0\x09", b"\0\x0a")
                   for p in flow.result["send"])
    advance(flow, combat.hurtTimer(target))
    assert "hurtUntil" not in battle.actorData(flow, target)
    assert states(flow)[-1] == (target, battle_flow.ACT_STATE_IDLE)


@pytest.mark.parametrize("intent", battle.ATTACK_CHAINS)
def test_every_attack_direction_has_a_contact_window(arena, intent):
    swing(arena, intent=intent)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == combat.MAX_HEALTH - combat.DAMAGE


@pytest.mark.parametrize("position,heading", [((3., 0., .2), 180), ((1.5, 0., 2.), 180),
                                            ((-1.5, 0., .2), 0), ((0., 1.5, .2), 0)])
def test_out_of_range_height_or_facing_misses(arena, position, heading):
    arena.session["bot"].update(position=list(position), heading=heading)
    snapshot(arena, (0., 0., .2))
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == combat.MAX_HEALTH


def test_clear_weapon_segment_hits_despite_disconnected_navigation(arena, monkeypatch):
    nav = NavMesh.fromPolygons([
        [(-2, -2, 0), (.5, -2, 0), (.5, 2, 0), (-2, 2, 0)],
        [(1, -2, 0), (3, -2, 0), (3, 2, 0), (1, 2, 0)],
    ])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: nav)
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == combat.MAX_HEALTH - combat.DAMAGE


@pytest.mark.parametrize("attacker,defender", [(1, 2), (2, 1)])
@pytest.mark.parametrize("attack,acceptedDefences", [
    (300560, (300580,)), (300550, (300590, 300610)),
    (300570, (300600,)), (300540, (300590, 300610)),
], ids=["up", "right", "down", "left"])
@pytest.mark.parametrize("defence", [300580, 300590, 300600, 300610])
def test_held_parry_accepts_both_lateral_guards_but_keeps_vertical_pairing(
        arena, attacker, defender, attack, acceptedDefences, defence):
    battle.startAction(arena, defence, actorId=defender)
    advance(arena, battle.actionTimer(defender))
    swing(arena, attacker, attack)
    advance(arena, combat.hitTimer(attacker))
    blocked = defence in acceptedDefences
    assert hp(arena, defender) == combat.MAX_HEALTH - (0 if blocked else combat.DAMAGE)
    assert hp(arena, attacker) == combat.MAX_HEALTH
    assert any("combat-parry" in log for log in arena.result["logs"]) == blocked
    if blocked:
        assert battle.actionState(arena, attacker)["reaction"] == "parried"
        assert battle.actionState(arena, defender)["reaction"] == "parry-success"
        assert combat.hitTimer(attacker) not in arena.session["pending"]
    else:
        assert (defender, combat.HURT_STATE) in states(arena)
        assert "reaction" not in battle.actionState(arena, attacker)


@pytest.mark.parametrize("attacker", [1, 2])
@pytest.mark.parametrize("attack", [300540, 300550])
@pytest.mark.parametrize("defence", [300590, 300610])
@pytest.mark.parametrize("bearing,guardOffset,blocked", [
    (0, 0, True), (90, 59.9, True), (-90, -59.9, True),
    (175, 60.1, False), (-175, -60.1, False), (45, 180, False),
])
def test_lateral_parry_uses_relative_positions_and_current_facing(
        arena, attacker, attack, defence, bearing, guardOffset, blocked):
    origin = [3., 4., .2]
    yaw = math.radians(bearing)
    target = [origin[0] + 1.5 * math.cos(yaw), origin[1] - 1.5 * math.sin(yaw), .2]
    attackerKey, defenderKey = ("player", "bot") if attacker == 1 else ("bot", "player")
    arena.context["botWorld"].update({
        attackerKey: origin, defenderKey: target,
        attackerKey + "Heading": bearing,
        defenderKey + "Heading": (bearing + guardOffset) % 360 - 180,
    })
    defender = 3 - attacker
    battle.startAction(arena, defence, actorId=defender)
    advance(arena, battle.actionTimer(defender))
    swing(arena, attacker, attack)
    advance(arena, combat.hitTimer(attacker))
    assert hp(arena, defender) == combat.MAX_HEALTH - (0 if blocked else combat.DAMAGE)
    assert (battle.actionState(arena, defender).get("reaction") == "parry-success") == blocked
    assert any("combat-parry" in log for log in arena.result["logs"]) == blocked


@pytest.mark.parametrize("attacker", [1, 2])
@pytest.mark.parametrize("attack,defence", [(300540, 300610), (300550, 300590)])
@pytest.mark.parametrize("failure", ["range", "height", "attack-facing", "stale", "epoch"])
def test_lateral_guard_does_not_bypass_contact_requirements(arena, attacker, attack, defence, failure):
    defender = 3 - attacker
    battle.startAction(arena, defence, actorId=defender)
    advance(arena, battle.actionTimer(defender))
    swing(arena, attacker, attack)
    world = arena.context["botWorld"]
    world["sampledAt"] = arena.session["pending"][combat.hitTimer(attacker)]
    if failure == "range": world["bot"][0] = 2.3
    if failure == "height": world["bot"][2] += 1.1
    if failure == "attack-facing":
        key = "playerHeading" if attacker == 1 else "botHeading"
        world[key] = (world[key] + 270) % 360 - 180
    if failure == "stale": world["sampledAt"] -= 1000
    if failure == "epoch": world["instanceStartedAt"] += 1
    advance(arena, combat.hitTimer(attacker), fresh=False)
    assert hp(arena, 1) == hp(arena, 2) == combat.MAX_HEALTH
    assert not any("combat-parry" in log for log in arena.result["logs"])
    assert battle.actionState(arena, defender).get("reaction") is None


def test_back_facing_parry_does_not_block(arena):
    arena.context["botWorld"]["botHeading"] = 0
    battle.startAction(arena, 300580, actorId=2)
    advance(arena, battle.actionTimer(2))
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert hp(arena, 2) == combat.MAX_HEALTH - combat.DAMAGE


def test_hurt_interrupts_attack_motion_and_new_inputs(arena):
    battle.startAction(arena, 300550, actorId=2)
    arena.session["bot"]["moving"] = True
    arena.later(bot.MOVE_TIMER, 50)
    swing(arena)
    advance(arena, combat.hitTimer(1))
    assert battle.actionState(arena, 2)["index"] == -1
    assert bot.MOVE_TIMER not in arena.session["pending"]
    assert bot.ACTION_TIMER not in arena.session["pending"]
    arena.result["send"].clear()
    bot.think(arena)
    battle.startAction(arena, 300560, actorId=2)
    assert arena.session["bot"]["status"] == "hurt"
    assert arena.result["send"] == []
    advance(arena, combat.hurtTimer(2))
    swing(arena, 2)
    advance(arena, combat.hitTimer(2))
    arena.result["send"].clear()
    body = struct.pack(">HIib", 1, 0, 300550, 0)
    assert battle.message(arena, 4, 1, body)
    assert not arena.result["send"]


def test_charge_recovery_and_expired_swing_never_hit(arena):
    battle.startAction(arena, 300560)
    advance(arena, battle.ACTION_TIMER)
    assert combat.hitTimer(1) not in arena.session["pending"]
    battle.releaseAction(arena, battle.ATTACK_RELEASE)
    advance(arena, battle.ACTION_TIMER)
    assert combat.hitTimer(1) not in arena.session["pending"]
    assert hp(arena, 2) == combat.MAX_HEALTH
    advance(arena, battle.ACTION_TIMER)
    swing(arena)
    arena.now += 2000
    snapshot(arena, (0., 0., .2))
    app.handleTimer(arena, combat.hitTimer(1))
    assert hp(arena, 2) == combat.MAX_HEALTH


@pytest.mark.parametrize("change", ["stale", "epoch", "leaving", "prepare", "terrain"])
def test_unavailable_world_or_game_never_hits(arena, monkeypatch, change):
    swing(arena)
    if change == "stale": arena.context["botWorld"]["sampledAt"] -= 1000
    if change == "epoch": arena.context["botWorld"]["instanceStartedAt"] += 1
    if change == "leaving": arena.session["leaving"] = True
    if change == "prepare": arena.session["groundEnabled"] = False
    if change == "terrain": arena.context["botWorld"].pop("terrain")
    advance(arena, combat.hitTimer(1), fresh=False)
    assert hp(arena, 2) == combat.MAX_HEALTH


def test_hurt_and_contact_timers_cancel_on_leave(arena):
    swing(arena)
    advance(arena, combat.hitTimer(1))
    scene.leave(arena, struct.pack(">Hb", 2, 0))
    assert not any(name.startswith("combat-") for name in arena.session["pending"])
    arena.result["send"].clear()
    arena.now += 1000
    app.handleTimer(arena, combat.hurtTimer(2))
    assert not arena.result["send"]


def test_vision_refresh_and_migration_keep_current_health(arena):
    swing(arena)
    advance(arena, combat.hitTimer(1))
    expected = deepcopy(arena.state)
    assert app.migrateState(app.STATE_VERSION, arena.state) == expected
    arena.result["send"].clear()
    scene.message(arena, 0xE, 7, struct.pack(">HiQQb", 7, 2, 1, 2, 0))
    packets = [p["body"] for p in arena.result["send"] if p["command"] == 0xE]
    assert struct.unpack_from(">ii", packets[0], 201) == (100, 100)
    assert struct.unpack_from(">ii", packets[1], 201) == (100 - combat.DAMAGE, 100)
    assert arena.session["bot"]["health"] == 100 - combat.DAMAGE
    assert any(p["body"][:2] == b"\0\4" for p in arena.result["send"] if p["command"] == 4)
    arena.session["bot"]["health"] = -1
    assert not app.validateState(arena.state)


def test_new_round_resets_health_but_old_state_migration_is_unchanged(arena):
    arena.session["health"] = 37
    arena.session["bot"]["health"] = 52
    scene.begin(arena)
    assert hp(arena, 1) == hp(arena, 2) == combat.MAX_HEALTH
    old = flowAt().state
    assert app.migrateState(3, old) == old


def test_attr_health_wire_has_explicit_identity_and_zero_regeneration():
    body = attr_flow.encode_health_update(entity_rid=2, health=80, server_time_ms=1200)
    assert len(body) == 38
    assert struct.unpack(">HQiiiiiQ", body) == (1, 2, 1, 80, 0, 0, 0, 1200)
    with pytest.raises(ValueError):
        attr_flow.encode_health_update(entity_rid=2, health=-1, server_time_ms=1200)


def test_hurt_state_wire_preserves_full_target_and_animation_index():
    body = battle_flow.encode_battle_state_sync(
        instance_id=2, seq_no=4, state=35, state_change_ms=500,
        state_time_ms=0, target_rid=1, rand_anim_index=0)
    assert len(body) == 24
    assert struct.unpack(">HHHHIHBQb", body) == (4, 2, 4, 35, 500, 0, 0, 1, 0)


@pytest.mark.parametrize("target", [1, 2])
def test_health_stops_at_zero_without_death_settlement_or_automatic_heal(arena, target):
    for _ in range(7):
        combat.applyHit(arena, 3 - target, target, arena.context["botWorld"])
        assert app.validateState(arena.state)
    assert hp(arena, target) == 0
    advance(arena, combat.hurtTimer(target))
    battle.startAction(arena, 300560, actorId=target)
    assert battle.actionState(arena, target)["index"] == 0
    assert hp(arena, target) == 0
    assert not any(p["command"] == 4 and p["body"][:2] in (b"\0\x09", b"\0\x0a")
                   for p in arena.result["send"])


def test_hurt_recovery_waits_for_fresh_world_and_never_touches_another_epoch(arena):
    swing(arena)
    advance(arena, combat.hitTimer(1))
    arena.result["send"].clear()
    advance(arena, combat.hurtTimer(2), fresh=False)
    assert "hurtUntil" in arena.session["bot"]
    assert not arena.result["send"]
    arena.context["botWorld"]["instanceStartedAt"] += 1
    advance(arena, combat.hurtTimer(2))
    assert "hurtUntil" not in arena.session["bot"]
    assert not arena.result["send"]


def test_hot_reload_keeps_damage_and_resumes_existing_hurt_deadline(arena, tmp_path):
    from Business.runtime import host_runtime
    swing(arena)
    advance(arena, combat.hitTimer(1))
    original = deepcopy(arena.state)
    root = Path(__file__).resolve().parents[2]
    runtime = host_runtime.Runtime(root / "src/Business/scripts", tmp_path / "revisions")
    try:
        runtime.prepare()
        state = runtime.switch(host_runtime.encode(original))
        assert host_runtime.decode(state) == original
        due = arena.session["pending"][combat.hurtTimer(2)]
        context = {**arena.context, "nowMs": due, "connections": [1],
                   "botWorld": {**arena.context["botWorld"], "sampledAt": due}}
        event = {"type": "timer", "connection": 1, "name": combat.hurtTimer(2)}
        state, sends, _, _ = runtime.dispatch(host_runtime.encode(event), state, host_runtime.encode(context))
        value = host_runtime.decode(state)["sessions"]["1"]["bot"]
        assert value["health"] == combat.MAX_HEALTH - combat.DAMAGE
        assert "hurtUntil" not in value
        assert battle_flow.decode_battle_state_sync_simple(sends[0]["body"]).state == 2
    finally:
        runtime.close()
