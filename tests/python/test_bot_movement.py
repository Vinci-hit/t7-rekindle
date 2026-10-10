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


def step(flow, elapsed=50):
    flow.now += elapsed
    snapshot(flow, (40., 0., .2))
    tick(flow, bot.MOVE_TIMER)


def lastMove(flow):
    packet = next(item for item in reversed(flow.result["send"])
                  if item["command"] == 2 and item["body"][:2] == b"\0\x26")
    return move_flow.decode_move_bc_with_system_and_active(packet["body"])


def run(flow):
    for _ in range(42):
        step(flow)
    assert lastMove(flow).state == 10


def test_bot_pace_matches_player_runtime_parameters():
    header = (Path(__file__).resolve().parents[2] / "src/Runtime/launcher/SprintHookCode.h").read_text(encoding="utf-8")
    for name, value in (("JOG_SPEED", bot.JOG_SPEED), ("RUN_SPEED", bot.RUN_SPEED),
                        ("RUN_DELAY_MS", bot.RUN_DELAY_MS)):
        match = re.search(r"\b" + name + r"\s*=\s*([\d.]+)f?\s*[,;]", header)
        assert match and float(match[1]) == value


def test_displacement_velocity_and_animation_change_together_after_two_seconds(walking):
    flow = walking
    for i in range(40):
        step(flow)
        move = lastMove(flow)
        assert move.target_instance_id == 2
        assert move.position == pytest.approx((3 * (i + 1) * .05, 0, .2))
        assert move.current_velocity == (3000 if i < 39 else 5000)
        assert move.state == (2 if i < 39 else 10)
        assert (move.forward_back, move.left_right) == (-1000, 0)
    step(flow)
    assert lastMove(flow).position == pytest.approx((6.25, 0, .2))
    assert app.validateState(flow.state)


def test_frame_crossing_run_threshold_integrates_both_speeds(walking):
    for _ in range(39):
        step(walking)
    step(walking, 75)
    assert lastMove(walking).position == pytest.approx((6.125, 0, .2))
    assert lastMove(walking).state == 10


def test_repath_preserves_continuous_forward_warmup(walking):
    for _ in range(38):
        step(walking)
    snapshot(walking, (40., 0., .2))
    tick(walking)
    step(walking, 100)
    assert lastMove(walking).state == 10
    assert walking.session["bot"]["forwardMs"] == 2000


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
def test_interruption_stops_motion_and_discards_run_warmup(walking, reason):
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
    assert move.position[1] > position[1]
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
    assert math.dist(value["position"], origin) == pytest.approx(.25 if running else .15)
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
    assert math.dist(value["position"], (0, 0, .2)) == pytest.approx(.15)


def test_corner_is_not_cut_during_turning(walking):
    value = walking.session["bot"]
    value.update(path=[[.1, 0., 0.], [.1, 5., 0.]], step=0)
    step(walking)
    assert value["position"] == pytest.approx((.1, 0., .2))
    step(walking)
    assert value["position"] == pytest.approx((.1, 0., .2))
    assert lastMove(walking).direction_yaw == -18


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


def test_old_state_defaults_to_jog_and_current_warmup_survives_migration(walking):
    value = walking.session["bot"]
    value.pop("forwardMs", None)
    value.pop("lastTurnAt", None)
    assert app.migrateState(5, walking.state) == walking.state
    step(walking)
    assert lastMove(walking).current_velocity == 3000
    original = deepcopy(walking.state)
    assert app.migrateState(app.STATE_VERSION, walking.state) == original
    assert walking.state == original
    for field, invalid in (("forwardMs", -1), ("forwardMs", 2001), ("forwardMs", True),
                           ("lastTurnAt", -1), ("lastTurnAt", "0")):
        state = deepcopy(original)
        state["sessions"]["1"]["bot"][field] = invalid
        assert not app.validateState(state)


def test_close_target_waits_for_turn_before_reaction_delay(walking):
    bot.stopMotion(walking)
    value = walking.session["bot"]
    value.update(heading=180, lastTurnAt=walking.now)
    for _ in range(4):
        walking.now += 100
        snapshot(walking, (1.5, 0., .2))
        tick(walking)
        assert value["action"]["index"] == -1
        assert value["reactAt"] is None
    walking.now += 100
    snapshot(walking, (1.5, 0., .2))
    tick(walking)
    assert value["heading"] == 0
    assert value["reactAt"] == walking.now + bot.REACTION_MS
    walking.now += bot.REACTION_MS
    snapshot(walking, (1.5, 0., .2))
    tick(walking)
    assert value["action"]["index"] == 0
