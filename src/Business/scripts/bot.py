"""One local melee opponent. Decision rules are local, not recovered server AI."""
import math
import struct

from . import battle, bot_decision as decision, contracts as wire, controls
from .codec import battle_flow, move_flow, room_flow, vision_flow
from .navmesh_resource import loadNavigation

THINK_TIMER = "bot-think"
MOVE_TIMER = "bot-move"
ACTION_TIMER = "bot-action-phase"
RELEASE_TIMER = "bot-release"
THINK_MS, MOVE_MS, REPATH_MS = 100, 50, 500
REACTION_MS, SNAPSHOT_MAX_AGE_MS = 300, 250
MELEE_RANGE, JOG_SPEED, RUN_SPEED = 2.0, 3.0, 5.0
RUN_DELAY_MS = controls.GROUND_RUN_DELAY_MS
TURN_SPEED, MOVE_ANGLE = 360.0, 45.0
PARRY_MS = 600
USER_ID = 10001
NAME = "电脑玩家".encode("gbk")
ATTACK_ORDER = decision.ATTACK_ORDER
PARRY_FOR_ATTACK = decision.PARRY_FOR_ATTACK


def createBot(playerCamp):
    if playerCamp not in (1, 2):
        raise ValueError("bot requires a playing camp")
    return {"camp": 3 - playerCamp, "position": list(wire.ENEMY_POSITION), "heading": 0,
            "status": "waiting", "moving": False, "initialized": False,
            "path": [], "step": 0, "attackCount": 0, "repathAt": 0, "reactAt": None,
            "forwardMs": 0}


def vision(value):
    body = wire.actorVision(value["camp"], wire.HERO_ID, NAME,
                           runtimeMovement=True, position=tuple(value["position"]),
                           actorId=wire.BOT_ID, userId=USER_ID, currentHp=value.get("health", 100))
    # CS_ACTOR_CURR_MOVE_DATA starts at byte 24; dir is at +21.
    return body[:45] + struct.pack(">h", value["heading"]) + body[47:]


def importMember(flow):
    value = flow.session.get("bot")
    if value is None:
        return
    # The instance roster is independent of VISION's scene entity registry.
    flow.send(0xA, room_flow.encode_instance_update_actor_basic_info(
        server_time_ms=flow.now, instance_id=1, actor_mid=wire.BOT_ID, user_id=USER_ID,
        user_name=NAME, user_image_id=7, level=1, actor_state=8,
        hero_resource_id=wire.HERO_ID, camp=value["camp"], start_pattern=wire.START_PATTERN),
        "bot-instance-member")
    flow.send(0x23, wire.campExchange(flow.now, value["camp"], actorId=wire.BOT_ID), "bot-camp-exchange")


def enterScene(flow):
    value = flow.session.get("bot")
    if value is None:
        return
    flow.send(0x36, room_flow.encode_actor_update_state(
        user_id=USER_ID, actor_mid=wire.BOT_ID, actor_state=6, update_time_ms=flow.now), "bot-in-scene")
    # Loading-time VISION can precede scene creation; rebuild at the same
    # battle-entry boundary as the local actor, not on every VISION refresh.
    flow.send(0xE, vision_flow.encode_vision_del_event(object_mid=wire.BOT_ID), "bot-vision-del-at-entry")
    flow.send(0xE, vision(value), "bot-vision-add-at-entry")


def refresh(flow):
    action = flow.session["bot"].get("action", {})
    if "state" in action:
        fields = dict(
            instance_id=wire.BOT_ID, seq_no=action["seq"], state=action["state"],
            state_change_ms=action["changedAt"] - flow.session["instanceStartedAt"],
            state_time_ms=min(65535, max(0, flow.now - action["changedAt"])))
        body = (battle_flow.encode_battle_state_sync(**fields, target_rid=action["targetId"])
                if "targetId" in action else battle_flow.encode_battle_state_sync_simple(**fields))
        flow.send(battle_flow.BATTLE_COMMAND, body, "bot-action-snapshot")


def validState(value):
    return (type(value) is dict and value.get("camp") in (1, 2)
            and validPosition(value.get("position")) and type(value.get("heading")) is int
            and -180 <= value["heading"] <= 180 and type(value.get("status")) is str
            and all(type(value.get(key)) is bool for key in ("moving", "initialized"))
            and type(value.get("path")) is list and all(validPosition(p) for p in value["path"])
            and type(value.get("step")) is int and 0 <= value["step"] <= len(value["path"])
            and all(type(value.get(key)) is int and value[key] >= 0 for key in ("attackCount", "repathAt"))
            and (value.get("reactAt") is None or type(value["reactAt"]) is int)
            and type(value.get("forwardMs", 0)) is int and 0 <= value.get("forwardMs", 0) <= RUN_DELAY_MS
            and type(value.get("lastTurnAt", 0)) is int and value.get("lastTurnAt", 0) >= 0
            and (not value["initialized"] or type(value.get("heightOffset")) in (int, float)
                 and math.isfinite(value["heightOffset"]))
            and decision.validState(value))


def validPosition(value):
    return (type(value) in (list, tuple) and len(value) == 3
            and all(type(v) in (float, int) and math.isfinite(v) and abs(v) < 100000 for v in value))


def navigationFor(flow):
    directory = flow.context.get("clientDirectory")
    if not directory:
        raise ValueError("selected client navigation resource is unavailable")
    return loadNavigation(directory)


def world(flow):
    value = flow.context.get("botWorld")
    if (type(value) is not dict or value.get("ready") is not True
            or type(value.get("sampledAt")) is not int
            or not 0 <= flow.now - value["sampledAt"] <= SNAPSHOT_MAX_AGE_MS
            or value.get("instanceStartedAt") != flow.session.get("instanceStartedAt")):
        return None
    if not all(validPosition(value.get(name)) for name in ("player", "bot")):
        return None
    return value


def status(flow, text):
    value = flow.session["bot"]
    if value["status"] != text:
        value["status"] = text
        flow.result["logs"].append("bot status=" + text)


def broadcast(flow, moving, travelHeading=None):
    value = flow.session["bot"]
    tick = flow.now - flow.session["instanceStartedAt"]
    if not 0 <= tick <= controls.MAX_MOVE_TICK:
        raise ValueError("bot movement clock outside instance")
    state, forward, lateral, speed = 1, 0, 0, 0
    if moving:
        angle = headingDelta(value["heading"], travelHeading if travelHeading is not None else value["heading"])
        forward, lateral = -round(1000 * math.cos(math.radians(angle))), -round(1000 * math.sin(math.radians(angle)))
        axes = (-1, 1 if angle < -22.5 else -1 if angle > 22.5 else 0)
        running = value.get("forwardMs", 0) >= RUN_DELAY_MS
        state = (controls.GROUND_RUN_STATES if running else controls.GROUND_WALK_STATES)[axes]
        speed = round(1000 * (RUN_SPEED if running else JOG_SPEED))
    flow.send(2, move_flow.encode_move_bc_with_system_and_active(
        server_tick=tick, target_instance_id=wire.BOT_ID, position=tuple(value["position"]),
        direction_yaw=value["heading"], state=state, forward_back=forward, left_right=lateral,
        current_velocity=speed, max_velocity=25000 if moving else 0,
        system_group=3, active=1, acceleration=0), "bot-move" if moving else "bot-stop")
    value["moving"] = moving


def stopMotion(flow, *, publish=True):
    value = flow.session["bot"]
    flow.cancel(MOVE_TIMER)
    value["path"] = []
    value["step"] = 0
    value["forwardMs"] = 0
    if value["moving"] and publish and battle.botInstanceMatches(flow):
        broadcast(flow, False)
    value["moving"] = False


def stop(flow, *, publish=True):
    if "bot" not in flow.session:
        return
    stopMotion(flow, publish=publish)
    flow.cancel(THINK_TIMER)
    flow.cancel(RELEASE_TIMER)
    battle.resetAction(flow, wire.BOT_ID)
    flow.session["bot"].pop("observedPlayer", None)
    flow.session["bot"]["reactAt"] = None
    status(flow, "stopped")


def start(flow):
    if "bot" in flow.session:
        flow.later(THINK_TIMER, THINK_MS)


def headingDelta(current, target):
    return (target - current + 180) % 360 - 180


def turnToward(flow, heading):
    value = flow.session["bot"]
    elapsed = max(0, min(controls.MAX_GROUND_ELAPSED_MS,
                         flow.now - value.get("lastTurnAt", flow.now - THINK_MS)))
    value["lastTurnAt"] = flow.now
    limit = TURN_SPEED * elapsed / 1000
    change = max(-limit, min(limit, headingDelta(value["heading"], heading)))
    value["heading"] = round((value["heading"] + change + 180) % 360 - 180)
    return abs(headingDelta(value["heading"], heading))


def faceTarget(flow, target):
    value = flow.session["bot"]
    dx, dy = target[0] - value["position"][0], target[1] - value["position"][1]
    if dx == 0 and dy == 0:
        return True
    heading = int(round(math.degrees(math.atan2(-dy, dx))))
    previous = value["heading"]
    remaining = turnToward(flow, heading)
    if value["heading"] != previous:
        flow.send(2, move_flow.encode_move_direct_bc(
            server_tick=flow.now - flow.session["instanceStartedAt"], target_instance_id=wire.BOT_ID,
            direction_yaw=value["heading"], position=tuple(value["position"])), "bot-face-target")
    return remaining == 0


def think(flow):
    value = flow.session["bot"]
    snapshot = world(flow)
    if snapshot is None:
        stopMotion(flow)
        if not battle.botInstanceMatches(flow):
            battle.resetAction(flow, wire.BOT_ID)
            flow.cancel(RELEASE_TIMER)
        value["reactAt"] = None
        value.pop("observedPlayer", None)
        status(flow, "world-unavailable")
        return
    decision.observe(flow)
    if "hurtUntil" in value:
        stopMotion(flow)
        status(flow, "hurt")
        return
    reaction = value.get("action", {}).get("reaction")
    if reaction:
        stopMotion(flow)
        status(flow, reaction)
        return
    try:
        nav = navigationFor(flow)
    except (ValueError, OSError) as error:
        stopMotion(flow)
        status(flow, "navigation-unavailable" if isinstance(error, OSError) else "navigation-invalid")
        return
    actual = nav.project(snapshot["bot"])
    target = nav.project(snapshot["player"])
    if actual is None or target is None:
        value["position"] = list(snapshot["bot"])
        stopMotion(flow)
        status(flow, "outside-mesh")
        return
    if not value["initialized"]:
        value["position"] = list(snapshot["bot"])
        value["heightOffset"] = snapshot["bot"][2] - actual[2]
        battle.syncState(flow, battle_flow.ACT_STATE_IDLE, wire.BOT_ID)
        value["initialized"] = True
    if math.dist(snapshot["bot"], value["position"]) > 1.0:
        # Reconcile against the observed entity; never pull it through an obstruction.
        value["position"] = list(snapshot["bot"])
        value["heightOffset"] = snapshot["bot"][2] - actual[2]
        stopMotion(flow)
        value["repathAt"] = 0
    action = battle.actionState(flow, wire.BOT_ID)
    if action["index"] >= 0:
        stopMotion(flow)
        status(flow, "parry" if action["intent"] in battle.BLOCK_CHAINS else "attack")
        return
    distance = math.dist(value["position"], snapshot["player"])
    route = None
    if distance <= MELEE_RANGE:
        route = nav.findPath(value["position"], snapshot["player"])
        routeLength = sum(math.dist(a, b) for a, b in zip(route.points, route.points[1:]))
        if route.status == "ready" and routeLength <= MELEE_RANGE:
            stopMotion(flow)
            if not faceTarget(flow, target):
                value["reactAt"] = None
                status(flow, "turning")
                return
            if value["reactAt"] is None:
                value["reactAt"] = flow.now + REACTION_MS
            choice = decision.choose(flow, snapshot)
            if choice.kind == "wait":
                status(flow, choice.reason)
                return
            parry = choice.kind == "parry"
            battle.startAction(flow, choice.intent, released=not parry, actorId=wire.BOT_ID)
            if parry:
                flow.later(RELEASE_TIMER, PARRY_MS)
            else:
                value["attackCount"] += 1
                value.update(lastAttack=choice.intent, decisionSeed=choice.seed)
            value["reactAt"] = None
            flow.result["logs"].append(f"bot-decision reason={choice.reason} intent={choice.intent}")
            status(flow, choice.reason)
            return
    value["reactAt"] = None
    if not value["path"] or flow.now >= value["repathAt"]:
        route = route or nav.findPath(value["position"], snapshot["player"])
        value["repathAt"] = flow.now + REPATH_MS
        if route.status != "ready":
            stopMotion(flow)
            status(flow, route.status)
            return
        value["path"], value["step"] = [list(p) for p in route.points[1:]], 0
    status(flow, "approach")
    if MOVE_TIMER not in flow.session["pending"]:
        value["lastMoveAt"] = value["lastTurnAt"] = flow.now
        flow.later(MOVE_TIMER, MOVE_MS)


def move(flow):
    value = flow.session["bot"]
    if world(flow) is None:
        stopMotion(flow)
        value.pop("observedPlayer", None)
        value["reactAt"] = None
        status(flow, "world-unavailable")
        return
    action = value.get("action", {})
    if "hurtUntil" in value or action.get("reaction") or action.get("index", -1) >= 0:
        stopMotion(flow)
        return
    elapsed = max(0, min(controls.MAX_GROUND_ELAPSED_MS, flow.now - value.get("lastMoveAt", flow.now)))
    value["lastMoveAt"] = flow.now
    position = list(value["position"])
    position[2] -= value["heightOffset"]
    while value["step"] < len(value["path"]) and math.dist(position, value["path"][value["step"]]) < 1e-6:
        value["step"] += 1
    if value["step"] >= len(value["path"]):
        broadcast(flow, False)
        stopMotion(flow)
        return
    target = value["path"][value["step"]]
    heading = math.degrees(math.atan2(-(target[1] - position[1]), target[0] - position[0]))
    if turnToward(flow, heading) > MOVE_ANGLE:
        value["forwardMs"] = 0
        broadcast(flow, False)
        flow.later(MOVE_TIMER, MOVE_MS)
        return
    forwardMs = value.get("forwardMs", 0)
    jogMs = min(elapsed, RUN_DELAY_MS - forwardMs)
    travel = (JOG_SPEED * jogMs + RUN_SPEED * (elapsed - jogMs)) / 1000
    value["forwardMs"] = min(RUN_DELAY_MS, forwardMs + elapsed)
    distance = math.dist(position, target)
    # Stop at each corner; the next update turns toward the following path leg.
    if distance <= travel:
        position = list(target)
        value["step"] += 1
    else:
        position = [a + (b - a) * travel / distance for a, b in zip(position, target)]
    position[2] += value["heightOffset"]
    value["position"] = position
    if value["step"] >= len(value["path"]):
        broadcast(flow, False)
        stopMotion(flow)
        return
    broadcast(flow, True, heading)
    flow.later(MOVE_TIMER, MOVE_MS)


def timer(flow, name):
    if name not in (THINK_TIMER, MOVE_TIMER, RELEASE_TIMER):
        return False
    if "bot" not in flow.session:
        return True
    if not battle.enabled(flow):
        stop(flow)
        return True
    if name == THINK_TIMER:
        think(flow)
        flow.later(THINK_TIMER, THINK_MS)
    elif name == MOVE_TIMER:
        move(flow)
    else:
        if battle.botInstanceMatches(flow):
            battle.releaseAction(flow, battle.BLOCK_RELEASE, wire.BOT_ID)
        else:
            battle.resetAction(flow, wire.BOT_ID)
    return True
