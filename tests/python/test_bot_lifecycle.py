import struct

import pytest

from Business.scripts import app, bot, contracts as wire, controls, scene
from Business.scripts.codec import room_flow


def instanceFlow():
    state = app.createState({"runtimeMovement": True})
    state["sessions"]["1"] = {"role": "instance", "hydration": [], "pending": {}}
    flow = app.Flow({"connection": 1}, state, {"nowMs": 1000})
    scene.begin(flow)
    return flow


def message(flow, command, body):
    flow.result["send"].clear()
    app.handleMessage(flow, {"command": command, "body": body})
    return list(flow.result["send"])


def selectCamp(flow, camp):
    message(flow, 0x23, struct.pack(">Hi", 1, camp))
    return message(flow, 0x23, b"\0\3\0")


def members(packets):
    result = {}
    for packet in packets:
        body = packet["body"]
        if packet["command"] != 0xA or body[:2] != b"\0\x10":
            continue
        mid, userId = struct.unpack_from(">QI", body, 18)
        nameLength, = struct.unpack_from(">I", body, 30)
        name = body[34:34 + nameLength - 1].decode("gbk")
        _, level, state, hero, camp, _ = struct.unpack_from(">6i", body, 34 + nameLength)
        result[mid] = (userId, name, level, state, hero, camp)
    return result


def visionEvents(packets):
    result = []
    for packet in packets:
        if packet["command"] != 0xE:
            continue
        body = packet["body"]
        selector, = struct.unpack_from(">H", body)
        if selector in (1, 2):
            mid, = struct.unpack_from(">Q", body, 14 if selector == 1 else 10)
            result.append((selector, mid))
    return result


@pytest.mark.parametrize("camp", [1, 2])
def test_camp_confirmation_imports_both_members_before_loading(camp):
    flow = instanceFlow()
    packets = selectCamp(flow, camp)
    roster = members(packets)
    assert set(roster) == {wire.ACTOR_ID, wire.BOT_ID}
    assert roster[wire.ACTOR_ID] == (wire.USER_ID, "新玩家", wire.USER_LEVEL, 4, wire.HERO_ID, camp)
    assert roster[wire.BOT_ID] == (10001, "电脑玩家", 1, 8, wire.HERO_ID, 3 - camp)
    exchanges = [struct.unpack(">HQiQi", item["body"])[3:]
                 for item in packets if item["command"] == 0x23 and item["body"][:2] == b"\0\6"]
    assert exchanges == [(wire.ACTOR_ID, camp), (wire.BOT_ID, 3 - camp)]
    # The local actor's established VISION-before-basic-info order stays intact.
    localVision = next(i for i, item in enumerate(packets) if item["command"] == 0xE)
    localMember = next(i for i, item in enumerate(packets) if members([item]).get(wire.ACTOR_ID))
    assert localVision < localMember
    assert not any(name.startswith("bot-") for name in flow.session["pending"])


@pytest.mark.parametrize("camp", [1, 2])
@pytest.mark.parametrize("confirmBeforeCountdown", [False, True])
def test_loading_vision_is_rebuilt_once_at_battle_entry(camp, confirmBeforeCountdown):
    flow = instanceFlow()
    selectCamp(flow, camp)
    # GET_OBJECTS during loading must not be the bot's only spawn opportunity.
    loading = message(flow, 0xE, struct.pack(">HiQQb", 7, 2, 1, 2, 0))
    assert visionEvents(loading) == [(1, 1), (1, 2)]
    message(flow, 0x36, b"\0\x14\0")
    flow.now += 1000
    app.handleTimer(flow, "actor-select")
    message(flow, 0x36, struct.pack(">Hi", 0x32, 1))
    if confirmBeforeCountdown:
        entered = message(flow, 0x36, struct.pack(">HHb", 0x64, 1, 0))
    else:
        flow.result["send"].clear()
        flow.now = flow.session["pending"]["round-start"]
        app.handleTimer(flow, "round-start")
        entered = list(flow.result["send"])

    assert visionEvents(entered) == [(2, 1), (1, 1), (2, 2), (1, 2)]
    botStates = [struct.unpack(">HIQiQ", item["body"])[1:4] for item in entered
                 if item["command"] == 0x36 and item["body"][:2] == b"\0\1"
                 and struct.unpack_from(">Q", item["body"], 6)[0] == wire.BOT_ID]
    assert botStates == [(10001, wire.BOT_ID, 6)]
    assert not controls.groundEnabled(flow)
    assert not any(name.startswith("bot-") for name in flow.session["pending"])

    repeated = message(flow, 0x36, struct.pack(">HHb", 0x64, 1, 0))
    assert visionEvents(repeated) == []
    if confirmBeforeCountdown:
        flow.now = flow.session["pending"]["round-start"]
        app.handleTimer(flow, "round-start")
    flow.result["send"].clear()
    flow.now = flow.session["pending"]["round-game"]
    app.handleTimer(flow, "round-game")
    assert visionEvents(flow.result["send"]) == []
    assert flow.session["pending"][bot.THINK_TIMER] == flow.now + bot.THINK_MS
    assert app.validateState(flow.state)


def test_bot_import_does_not_change_player_identity_or_selected_hero():
    flow = instanceFlow()
    selectCamp(flow, 1)
    flow.session.update(heroChosen=True, heroId=wire.HERO_ID)
    before = (flow.state["actorId"], flow.session["heroId"], flow.playerName)
    flow.result["send"].clear()
    scene.battleEntry(flow)
    packet = next(item for item in flow.result["send"]
                  if item["command"] == 0x36 and item["body"] == room_flow.encode_actor_update_state(
                      user_id=10001, actor_mid=wire.BOT_ID, actor_state=6, update_time_ms=flow.now))
    assert packet["body"] != wire.actorState(flow.now, 6)
    assert (flow.state["actorId"], flow.session["heroId"], flow.playerName) == before
