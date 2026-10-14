from copy import deepcopy
import math
from pathlib import Path
import re

import pytest

from Business.scripts import app, battle, bot
from Business.scripts.codec import move_flow
from Business.scripts.navigation import NavMesh
from test_bot_player import flowAt, snapshot, tick


@pytest.fixture
def walking(monkeypatch):
    nav = NavMesh.fromPolygons([[(-50, -50, 0), (50, -50, 0), (50, 50, 0), (-50, 50, 0)]])
    monkeypatch.setattr(bot, "navigationFor", lambda flow: nav)
    flow = flowAt()
    flow.session["bot"].update(position=[0., 0., .2], heading=0)
    snapshot(flow, (40., 0., .2))
    tick(flow)
    return flow


def step(flow, elapsed=50, *, position=None):
    flow.now += elapsed
    snapshot(flow, (40., 0., .2))
    if position is not None:
        flow.context['botWorld']['bot'] = list(position)
    tick(flow, bot.MOVE_TIMER)


def lastMove(flow):
    packet = next(item for item in reversed(flow.result["send"])
                  if item["command"] == 2 and item["body"][:2] == b"\0\x26")
    return move_flow.decode_move_bc_with_system_and_active(packet["body"])


def run(flow):
    for _ in range(22):
        step(flow, position=(flow.session['bot']['position'][0] + .1, 0, .2))
    assert lastMove(flow).state == 10


def test_bot_pace_matches_player_runtime_parameters():
    header = (Path(__file__).resolve().parents[2] / "src/Runtime/launcher/SprintHookCode.h").read_text(encoding="utf-8")
    for name, value in (("JOG_SPEED", bot.JOG_SPEED), ("RUN_SPEED", bot.RUN_SPEED),
                        ("RUN_DELAY_MS", bot.RUN_DELAY_MS)):
        match = re.search(r"\b" + name + r"\s*=\s*([\d.]+)f?\s*[,;]", header)
        assert match and float(match[1]) == value


def test_native_position_is_preserved_when_velocity_and_animation_change(walking):
    flow = walking
    for i in range(40):
        actual = (.1 * (i + 1), 0, .2)
        step(flow, position=actual)
        move = lastMove(flow)
        assert move.target_instance_id == 2
        assert move.position == pytest.approx(actual)
        assert flow.session['bot']['position'] == list(actual)
        assert move.current_velocity == (3000 if i < 19 else 5000)
        assert move.state == (2 if i < 19 else 10)
        assert (move.forward_back, move.left_right) == (-1000, 0)
    step(flow)
    assert lastMove(flow).position == pytest.approx((4., 0, .2))
    assert app.validateState(flow.state)


def test_frame_crossing_run_threshold_does_not_integrate_position(walking):
    for i in range(19):
        step(walking, position=(.05 * (i + 1), 0, .2))
    step(walking, 75, position=(1.3, 0, .2))
    assert lastMove(walking).position == pytest.approx((1.3, 0, .2))
    assert lastMove(walking).state == 10


def test_repath_preserves_continuous_forward_warmup(walking):
    for i in range(18):
        step(walking, position=(.1 * (i + 1), 0, .2))
    snapshot(walking, (40., 0., .2))
    tick(walking)
    step(walking, 100)
    assert lastMove(walking).state == 10
    assert walking.session["bot"]["forwardMs"] == 1000


def test_stop_and_restart_begin_at_jog_pace(walking):
    run(walking)
    bot.stopMotion(walking)
    move = lastMove(walking)
    assert (move.state, move.current_velocity, move.forward_back, move.left_right) == (1, 0, 0, 0)
    assert walking.session["bot"].get("forwardMs", 0) == 0
    snapshot(walking, (40., 0., .2))
    tick(walking)
    step(walking)
    assert (lastMove(walking).state, lastMove(walking).current_velocity) == (2, 3000)


@pytest.mark.parametrize("reason", ["attack", "parry", "hurt", "reaction", "prepare", "stale", "epoch"])
def test_combat_slows_movement_while_invalid_world_and_prepare_stop_it(walking, reason):
    run(walking)
    value = walking.session["bot"]
    position = list(value["position"])
    if reason == "attack": battle.startAction(walking, 300560, actorId=2)
    if reason == "parry": battle.startAction(walking, 300580, actorId=2)
    if reason == "hurt": value["hurtUntil"] = walking.now + 400
    if reason == "reaction": value["action"]["reaction"] = "parried"
    if reason == "prepare": walking.session["groundEnabled"] = False
    walking.now += 50
    snapshot(walking, (40., 0., .2), age=1000 if reason == "stale" else 0,
             epoch=200 if reason == "epoch" else 100)
    walking.result["send"].clear()
    tick(walking, bot.MOVE_TIMER)
    assert value["position"] == position
    assert value.get("forwardMs", 0) == 0
    if reason in ('attack', 'parry', 'hurt', 'reaction'):
        assert value['moving'] and bot.MOVE_TIMER in walking.session['pending']
        assert lastMove(walking).current_velocity == 3000
        return
    assert not value["moving"]
    assert bot.MOVE_TIMER not in walking.session["pending"]
    if reason == "epoch":
        assert not walking.result["send"]
    else:
        assert lastMove(walking).state == 1


def test_large_turn_rotates_before_moving_and_resets_run(walking):
    run(walking)
    value = walking.session["bot"]
    position = list(value["position"])
    value.update(path=[[position[0], position[1] + 10, 0.]], step=0)
    step(walking)
    move = lastMove(walking)
    assert move.position == pytest.approx(position)
    assert move.direction_yaw == -18
    assert (move.state, move.current_velocity) == (1, 0)
    assert value.get("forwardMs", 0) == 0
    assert bot.MOVE_TIMER in walking.session["pending"]
    step(walking)
    assert lastMove(walking).direction_yaw == -36
    step(walking)
    move = lastMove(walking)
    assert move.position == pytest.approx(position)
    assert move.direction_yaw == -54
    assert move.state == 3 and move.current_velocity == 3000
    # Direction axes describe the path-relative movement, not a fictitious forward step.
    yaw = math.radians(move.direction_yaw)
    dx = -move.forward_back * math.cos(yaw) + move.left_right * math.sin(yaw)
    dy = move.forward_back * math.sin(yaw) + move.left_right * math.cos(yaw)
    assert abs(dx) < 1 and dy == pytest.approx(1000, abs=1)


@pytest.mark.parametrize("running", [False, True], ids=["jog", "run"])
@pytest.mark.parametrize("heading,jogState,runState", [(-45, 3, 11), (45, 9, 12)])
def test_small_turn_keeps_pace_and_uses_matching_diagonal_state(walking, running, heading, jogState, runState):
    if running:
        run(walking)
    value = walking.session["bot"]
    origin = list(value["position"])
    yaw = math.radians(heading)
    direction = (math.cos(yaw), -math.sin(yaw))
    value.update(path=[[origin[0] + 10 * direction[0], origin[1] + 10 * direction[1], 0.]], step=0)
    step(walking)
    move = lastMove(walking)
    assert move.state == (runState if running else jogState)
    assert move.current_velocity == (5000 if running else 3000)
    assert value["position"] == origin
    yaw = math.radians(move.direction_yaw)
    dx = -move.forward_back * math.cos(yaw) + move.left_right * math.sin(yaw)
    dy = move.forward_back * math.sin(yaw) + move.left_right * math.cos(yaw)
    assert (dx, dy) == pytest.approx(tuple(1000 * v for v in direction), abs=1)


@pytest.mark.parametrize("initial,target", [(179, -179), (-179, 179)])
def test_turn_uses_shortest_arc_across_heading_wrap(walking, initial, target):
    value = walking.session["bot"]
    value["heading"] = initial
    yaw = math.radians(target)
    value.update(path=[[10 * math.cos(yaw), -10 * math.sin(yaw), 0.]], step=0)
    step(walking)
    assert lastMove(walking).direction_yaw == target
    assert value["position"] == [0, 0, .2]


def test_corner_is_not_cut_during_turning(walking):
    value = walking.session["bot"]
    value.update(path=[[1., 0., 0.], [1., 5., 0.]], step=0)
    step(walking)
    assert value['step'] == 0
    assert value["position"] == [0, 0, .2]
    step(walking, position=(1., 0., .2))
    assert value['step'] == 1
    assert value["position"] == [1., 0., .2]
    assert lastMove(walking).direction_yaw == -18
    assert lastMove(walking).state == 1


def test_stalled_timer_does_not_jump_or_instantly_enable_running(walking):
    step(walking, 5000)
    assert math.dist(walking.session["bot"]["position"], (0, 0, .2)) <= .301
    assert lastMove(walking).state == 2


def test_arrival_and_observed_position_correction_clear_warmup(walking):
    run(walking)
    value = walking.session["bot"]
    value["path"] = [[value["position"][0] + .1, 0., 0.]]
    value["step"] = 0
    step(walking)
    assert not value["moving"] and value.get("forwardMs", 0) == 0
    assert lastMove(walking).state == 1
    snapshot(walking, (40., 0., .2))
    tick(walking)
    run(walking)
    snapshot(walking, (40., 0., .2))
    walking.context["botWorld"]["bot"][0] -= 2
    tick(walking)
    assert value.get("forwardMs", 0) == 0


def test_missing_warmup_defaults_to_jog_and_current_warmup_survives_reload(walking):
    value = walking.session["bot"]
    value.pop("forwardMs", None)
    value.pop("lastTurnAt", None)
    assert app.migrateState(app.STATE_VERSION, walking.state) == walking.state
    step(walking)
    assert lastMove(walking).current_velocity == 3000
    original = deepcopy(walking.state)
    assert app.migrateState(app.STATE_VERSION, walking.state) == original
    assert walking.state == original
    for field, invalid in (("forwardMs", -1), ("forwardMs", 1001), ("forwardMs", True),
                           ("lastTurnAt", -1), ("lastTurnAt", "0")):
        state = deepcopy(original)
        state["sessions"]["1"]["bot"][field] = invalid
        assert not app.validateState(state)


def test_close_target_prepares_while_turning_but_waits_for_valid_facing(walking):
    bot.stopMotion(walking)
    value = walking.session["bot"]
    value.update(heading=180, lastTurnAt=walking.now, reactAt=None)
    readyAt = None
    for _ in range(4):
        walking.now += 100
        snapshot(walking, (1.5, 0., .2))
        tick(walking)
        assert value["action"]["index"] == -1
        if readyAt is None:
            readyAt = value['reactAt']
            assert round(bot.REACTION_MS * .65) <= readyAt - walking.now <= round(bot.REACTION_MS * 1.2)
        assert value['reactAt'] == readyAt
    walking.now += 100
    snapshot(walking, (1.5, 0., .2))
    tick(walking)
    assert value["heading"] == 0
    assert walking.now >= readyAt
    assert value["action"]["index"] == 0
    assert walking.session['pending'][bot.ACTION_TIMER] - walking.now == battle.phaseMs(value['action']['intent'], 0)
