from copy import deepcopy
import struct

import pytest

from Business.runtime import host_runtime
from Business.scripts import actors, app, battle, contracts as wire, controls, roster, scene
from Business.scripts.codec import leave_flow, move_flow
from test_bot_respawn import kill


@pytest.fixture(params=[False, True])
def game(request, spawnPoints):
    state = app.createState({"runtimeMovement": request.param})
    state["sessions"]["1"] = {
        "role": "instance", "hydration": [], "pending": {}, "camp": 1,
        "battleEntered": True, "heroChosen": True, "loaded": True,
        "groundEnabled": False, "instanceStartedAt": 100,
        "controlBaseline": wire.BASELINE_ID, "moveClock": controls.MOVE_CLOCK,
    }
    flow = app.Flow({"connection": 1}, state, {"nowMs": 1000})
    if request.param:
        roster.assemble(flow)
    flow.later("round-game", wire.START_MS)
    assert "round-end" not in flow.session["pending"]
    flow.now += wire.START_MS
    app.handleTimer(flow, "round-game")
    return flow


def clear(flow):
    flow.result["send"].clear()
    flow.result["timers"].clear()


def finish(flow, delay=0):
    flow.now = flow.session["pending"]["round-end"] + delay
    clear(flow)
    app.handleTimer(flow, "round-end")


def test_game_schedules_timeout_for_the_announced_duration_once(game):
    assert game.session["pending"]["round-end"] == game.now + wire.GAME_MS
    assert next(packet["body"] for packet in game.result["send"]
                if packet["reason"] == "instance-round-state-game") == wire.roundState(game.now, 4, wire.GAME_MS)
    assert [timer for timer in game.result["timers"] if timer["id"] == "1:round-end"] == [{
        "id": "1:round-end", "delayMs": wire.GAME_MS,
        "event": {"type": "timer", "connection": 1, "name": "round-end"},
    }]
    pending = dict(game.session["pending"])
    clear(game)
    game.now += 100
    app.handleTimer(game, "round-game")
    scene.message(game, 0x36, 0x64, struct.pack(">HHb", 0x64, 1, 0))
    assert game.session["pending"] == pending and not game.result["timers"]


@pytest.mark.parametrize("delay", [0, 2500])
def test_timeout_ends_the_round_once_and_waits_for_client_logout(game, delay):
    deadline = game.session["pending"]["round-end"]
    game.now = deadline - 1
    clear(game)
    app.handleTimer(game, "round-end")
    assert not game.result["send"] and not game.result["timers"]
    assert battle.enabled(game) and game.session["phase"] == "game-sent"

    finish(game, delay)
    assert [packet["body"] for packet in game.result["send"] if packet["command"] == 0xA] == [
        wire.roundState(deadline + delay, 5, 0),
        leave_flow.encode_si_finish_game(server_time_ms=deadline + delay, game_result=3, local_user_id=wire.USER_ID),
    ]
    active = [move_flow.decode_move_notify_active(packet["body"]).active
              for packet in game.result["send"]
              if packet["reason"] == "instance-move-notify-active-after-in-scene"]
    assert active == [0]
    assert game.session["roundEnded"] is True and not battle.enabled(game)
    assert game.session["phase"] == game.state["phase"] == "settlement"
    assert game.session["pending"] == {} and not game.session.get("leaving")
    assert not any(packet["command"] == 0x2C for packet in game.result["send"])
    assert app.validateState(game.state)

    state = deepcopy(game.state)
    clear(game)
    app.handleTimer(game, "round-end")
    assert game.state == state and not game.result["send"] and not game.result["timers"]


def test_timeout_stops_actions_movement_and_all_pending_timers(game):
    game.context["botWorld"] = {"instanceStartedAt": game.session["instanceStartedAt"]}
    for rid in actors.ids(game.session):
        battle.startAction(game, 300550, actorId=rid)
        battle.actionState(game, rid)["buffered"] = {"intent": 300560, "released": False}
        if rid != wire.ACTOR_ID:
            actors.data(game, rid)["moving"] = True
    controls.message(game, 2, 52, struct.pack(">Hi6Bfff", 52, 1, 1, 0, 0, 0, 0, 0, *wire.POSITION))
    game.later("time-sync", 2000)
    pending = dict(game.session["pending"])

    finish(game)
    assert game.session["pending"] == {}
    assert all(battle.actionState(game, rid)["index"] == -1
               and "buffered" not in battle.actionState(game, rid) for rid in actors.ids(game.session))
    assert all(not value["moving"] and value["status"] == "stopped"
               for value in actors.bots(game.session).values())
    assert len([packet for packet in game.result["send"] if packet["reason"] == "bot-stop"]) == len(
        actors.bots(game.session))
    expected = {f"1:{name}" for name in pending
                if name != "round-end" and not actors.localTimer(game.session, name)}
    assert {timer["id"] for timer in game.result["timers"] if timer["delayMs"] == -1} == expected
    assert not any(timer["delayMs"] >= 0 for timer in game.result["timers"])
    clear(game)
    for name in pending:
        app.handleTimer(game, name)
    assert not game.result["send"] and not game.result["timers"]


@pytest.mark.parametrize("game", [True], indirect=True)
@pytest.mark.parametrize("rid", [1, 2])
def test_timeout_cancels_player_and_bot_respawns_without_changing_scores(game, rid):
    game.now = game.session["pending"]["round-end"] - 1000
    kill(game, rid)
    life = deepcopy(actors.data(game, rid)["life"])
    score = [(actors.data(game, actorId).get("kills", 0), actors.data(game, actorId).get("deaths", 0),
              actors.data(game, actorId).get("battlePoints", 0)) for actorId in actors.ids(game.session)]
    pending = dict(game.session["pending"])
    finish(game)
    clear(game)
    game.now += 10000
    for name in pending:
        app.handleTimer(game, name)
    assert not game.result["send"] and not game.session["pending"]
    assert actors.data(game, rid)["life"] == life and actors.data(game, rid)["health"] == 0
    assert score == [(actors.data(game, actorId).get("kills", 0), actors.data(game, actorId).get("deaths", 0),
                      actors.data(game, actorId).get("battlePoints", 0)) for actorId in actors.ids(game.session)]
    assert app.validateState(game.state)


@pytest.mark.parametrize("expired", [False, True])
def test_leave_before_or_after_timeout_finishes_only_once_and_preserves_logout(game, expired):
    deadline = game.session["pending"]["round-end"]
    if expired:
        finish(game)
    clear(game)
    scene.leave(game, struct.pack(">Hb", 2, -1))
    reasons = [packet["reason"] for packet in game.result["send"]]
    assert reasons.count("instance-round-state-end") == int(not expired)
    assert reasons.count("instance-finish-game") == int(not expired)
    assert reasons.count("instance-leave-instance") == 1
    assert set(game.session["pending"]) == {"logout"}

    clear(game)
    game.now = max(deadline, game.session["pending"]["logout"])
    app.handleTimer(game, "round-end")
    assert not game.result["send"] and game.session["phase"] == "leaving"
    app.handleTimer(game, "logout")
    assert [packet["body"] for packet in game.result["send"]] == [
        leave_flow.encode_sync_logout_response(server_time_ms=game.now, back_data=255)]
    assert game.session["phase"] == "settlement" and not game.session["pending"]


def test_late_gameplay_requests_do_not_restart_a_finished_round(game):
    finish(game)
    state = deepcopy(game.state)
    clear(game)
    for command, body in [
        (0x23, b"\0\3\0"), (0x36, b"\0\x14\0"),
        (0x36, struct.pack(">Hi", 0x32, 1)), (0x36, struct.pack(">HHb", 0x64, 1, 0)),
        (0xE, struct.pack(">HiQb", 7, 1, 1, 1)),
        (2, struct.pack(">Hi6Bfff", 52, 1, 1, 0, 0, 0, 0, 0, *wire.POSITION)),
        (4, struct.pack(">HIib", 1, 0, 300550, 0)),
    ]:
        app.handleMessage(game, {"command": command, "body": body})
    assert game.state == state and not game.result["send"] and not game.result["timers"]


def test_reload_preserves_the_deadline_and_finished_state(game):
    deadline = game.session["pending"]["round-end"]
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(game.state)))
    flow = app.Flow({"connection": 1}, restored, {"nowMs": deadline})
    assert flow.session["pending"]["round-end"] == deadline
    finish(flow)
    restored = app.migrateState(app.STATE_VERSION, host_runtime.decode(host_runtime.encode(flow.state)))
    flow = app.Flow({"connection": 1}, restored, {"nowMs": deadline + 1})
    app.handleTimer(flow, "round-end")
    assert flow.session["roundEnded"] and not flow.result["send"] and not flow.result["timers"]
