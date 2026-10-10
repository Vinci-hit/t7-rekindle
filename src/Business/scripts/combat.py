"""Local melee contact and health; original client messages render the result."""
import math

from . import battle, bot, contracts as wire
from .codec import attr_flow, battle_flow

MAX_HEALTH, DAMAGE = 100, 20
DASH_DAMAGE = 40  # Offline tuning, independent of the original damage formula.
HURT_STATE, HURT_MS = 35, 400
CONTACT_MS, CONTACT_OPEN_MS, CONTACT_END_MARGIN_MS = 50, 120, 100
REACH, HEIGHT_LIMIT, FACING_COS = 2.2, 1.0, 0.5
ACTORS = (wire.ACTOR_ID, wire.BOT_ID)


def hitTimer(actorId):
    return f"combat-hit-{actorId}"


def hurtTimer(actorId):
    return f"combat-hurt-{actorId}"


def initialize(flow):
    for actorId in ACTORS:
        data = battle.actorData(flow, actorId)
        data["health"] = MAX_HEALTH
        data.pop("hurtUntil", None)
        cancelSwing(flow, actorId)
        flow.cancel(hurtTimer(actorId))


def validState(data):
    if "health" not in data:
        return not any(key in data for key in ("swing", "hurtUntil"))
    if type(data["health"]) is not int or not 0 <= data["health"] <= MAX_HEALTH:
        return False
    if "hurtUntil" in data and (type(data["hurtUntil"]) is not int or data["hurtUntil"] < 0):
        return False
    swing = data.get("swing")
    return swing is None or (type(swing) is dict and all(
        type(swing.get(key)) is int and swing[key] >= 0 for key in ("seq", "epoch", "until")))


def cancelSwing(flow, actorId, *, reason=None):
    flow.cancel(hitTimer(actorId))
    swing = battle.actorData(flow, actorId).pop("swing", None)
    if swing is not None and reason:
        detail = swing.get("miss", "expired") if flow.now >= swing["until"] else reason
        flow.result["logs"].append(f"combat-miss attacker={actorId} reason={detail}")


def actionChanged(flow, actorId, state):
    cancelSwing(flow, actorId, reason="action-changed")
    data = battle.actorData(flow, actorId)
    action = data.get("action", {})
    chain = battle.attackChain(action.get("intent"))
    strike = battle.strikeIndex(action.get("intent"))
    if ("health" not in data or "bot" not in flow.session or not battle.enabled(flow)
            or chain is None or action.get("index") != strike or state != chain[strike]):
        return
    data["swing"] = {"seq": action["seq"], "epoch": flow.session["instanceStartedAt"],
                     "until": flow.now + battle.phaseMs(action["intent"], strike) - CONTACT_END_MARGIN_MS}
    flow.later(hitTimer(actorId), CONTACT_OPEN_MS)


def facing(position, target, heading):
    dx, dy = target[0] - position[0], target[1] - position[1]
    length = math.hypot(dx, dy)
    yaw = math.radians(heading)
    return length < 0.01 or (dx * math.cos(yaw) - dy * math.sin(yaw)) / length >= FACING_COS


def heading(snapshot, actorId):
    value = snapshot.get("playerHeading" if actorId == wire.ACTOR_ID else "botHeading")
    return value if type(value) in (int, float) and math.isfinite(value) and -180 <= value <= 180 else None


def contactFailure(flow, snapshot, attacker):
    origin = snapshot["player" if attacker == wire.ACTOR_ID else "bot"]
    target = snapshot["bot" if attacker == wire.ACTOR_ID else "player"]
    distance = math.dist(origin[:2], target[:2])
    if distance > REACH:
        return f"range distance={distance:.2f}"
    if abs(origin[2] - target[2]) > HEIGHT_LIMIT:
        return "height"
    if any(heading(snapshot, actorId) is None for actorId in ACTORS):
        return "heading-unavailable"
    yaw = heading(snapshot, attacker)
    if not facing(origin, target, yaw):
        bearing = math.degrees(math.atan2(-(target[1] - origin[1]), target[0] - origin[0]))
        angle = abs((bearing - yaw + 180) % 360 - 180)
        return f"facing distance={distance:.2f} angle={angle:.1f}"
    return None


def terrainContact(snapshot, attacker, *, targeted):
    probes = snapshot.get("terrain")
    if type(probes) is not list or len(probes) != 2 or type(probes[attacker - 1]) is not dict:
        return None
    contact = probes[attacker - 1].get("target" if targeted else "forward")
    if (type(contact) is not dict or contact.get("ready") is not True
            or type(contact.get("hit")) is not bool):
        return None
    if contact["hit"] and (type(contact.get("material")) is not int
            or not -1 <= contact["material"] <= 7
            or not all(bot.validPosition(contact.get(name)) for name in ("position", "normal", "direction"))
            or not all(.9 <= math.hypot(*contact[name]) <= 1.1 for name in ("normal", "direction"))):
        return None
    return contact


def hitTerrain(flow, attacker, contact):
    cancelSwing(flow, attacker)
    if attacker == wire.BOT_ID:
        bot.stopMotion(flow)
        flow.cancel(bot.RELEASE_TIMER)
        battle.actorData(flow, attacker)["reactAt"] = None
    battle.terrainReaction(flow, attacker)
    flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_hit_point_notify(
        hit_pos=tuple(contact["position"]), hit_direction=tuple(contact["direction"]),
        hit_normal=tuple(contact["normal"]), material_id=contact["material"], ignore_hurt=True,
        attacker_mid=attacker, target_mid=0, attacker_weapon_id=wire.WEAPON_ID,
        target_weapon_id=0, server_time_ms=flow.now), "combat-terrain-hit")
    point = ",".join(f"{v:.2f}" for v in contact["position"])
    flow.result["logs"].append(
        f"combat-terrain attacker={attacker} material={contact['material']} point={point}")


def publishHealth(flow, actorId):
    health = battle.actorData(flow, actorId)["health"]
    if actorId == wire.ACTOR_ID:
        command = battle_flow.BATTLE_COMMAND
        body = battle_flow.encode_battle_data_update_notify(
            result_type=battle_flow.RESULT_DATA_HP, result_data=health)
    else:
        command = attr_flow.ATTR_COMMAND
        body = attr_flow.encode_health_update(entity_rid=actorId, health=health, server_time_ms=flow.now)
    flow.send(command, body, f"combat-health-{actorId}")


def parry(flow, attacker, defender, snapshot):
    attack = battle.actionState(flow, attacker)
    defence = battle.actionState(flow, defender)
    lateral = attack["intent"] in (300540, 300550)
    acceptsAttack = (defence["intent"] in (300590, 300610) if lateral
                     else defence["intent"] == bot.PARRY_FOR_ATTACK[attack["intent"]])
    if defence["index"] != 1 or defence["released"] or not acceptsAttack:
        return False
    origin, target = (snapshot[name] for name in (("player", "bot") if defender == 1 else ("bot", "player")))
    yaw = heading(snapshot, defender)
    if yaw is None or not facing(origin, target, yaw):
        return False
    bot.stopMotion(flow)
    battle.parryReaction(flow, attacker, defender, success=False)
    battle.parryReaction(flow, defender, attacker, success=True)
    flow.result["logs"].append(
        f"combat-parry attacker={attacker} target={defender} attack={attack['intent']} defence={defence['intent']}")
    return True


def applyHit(flow, attacker, target, snapshot):
    data = battle.actorData(flow, target)
    damage = DASH_DAMAGE if battle.actionState(flow, attacker)["intent"] == battle.DASH_ATTACK else DAMAGE
    data["health"] = max(0, data["health"] - damage)
    publishHealth(flow, target)
    position = snapshot["player" if target == wire.ACTOR_ID else "bot"]
    flow.send(4, battle_flow.encode_battle_hit_point_notify(
        hit_pos=(position[0], position[1], position[2] + 1.0),
        attacker_mid=attacker, target_mid=target, attacker_weapon_id=wire.WEAPON_ID,
        target_weapon_id=0, server_time_ms=flow.now), "combat-hit-point")
    battle.resetAction(flow, target)
    if target == wire.BOT_ID:
        bot.stopMotion(flow)
        flow.cancel(bot.RELEASE_TIMER)
        data["reactAt"] = None
    data["hurtUntil"] = flow.now + HURT_MS
    battle.syncState(flow, HURT_STATE, target, targetId=attacker)
    flow.later(hurtTimer(target), HURT_MS)
    flow.result["logs"].append(f"combat-hit attacker={attacker} target={target} hp={data['health']}")


def checkHit(flow, attacker):
    data = battle.actorData(flow, attacker)
    swing = data.get("swing")
    action = data.get("action", {})
    strike = battle.strikeIndex(action.get("intent"))
    if (swing is None or not battle.enabled(flow) or action.get("index") != strike
            or action.get("seq") != swing["seq"] or flow.now > swing["until"]
            or swing["epoch"] != flow.session.get("instanceStartedAt")):
        cancelSwing(flow, attacker, reason="interrupted")
        return
    snapshot = bot.world(flow)
    failure = "world-unavailable" if snapshot is None else contactFailure(flow, snapshot, attacker)
    if snapshot is not None:
        beganAt = swing["until"] - battle.phaseMs(action["intent"], strike) + CONTACT_END_MARGIN_MS
        contact = (terrainContact(snapshot, attacker, targeted=failure is None)
                   if snapshot["sampledAt"] >= beganAt else None)
        if contact is None:
            failure = "terrain-unavailable"
        elif contact["hit"]:
            hitTerrain(flow, attacker, contact)
            return
    if failure is None:
        target = wire.BOT_ID if attacker == wire.ACTOR_ID else wire.ACTOR_ID
        cancelSwing(flow, attacker)
        if not parry(flow, attacker, target, snapshot):
            applyHit(flow, attacker, target, snapshot)
    else:
        swing["miss"] = failure
        if flow.now + CONTACT_MS <= swing["until"]:
            flow.later(hitTimer(attacker), CONTACT_MS)
        else:
            cancelSwing(flow, attacker, reason=failure)


def finishHurt(flow, actorId):
    data = battle.actorData(flow, actorId)
    if "hurtUntil" not in data:
        return
    if not battle.enabled(flow) or not battle.botInstanceMatches(flow):
        data.pop("hurtUntil")
        return
    if bot.world(flow) is None:
        flow.later(hurtTimer(actorId), CONTACT_MS)
        return
    data.pop("hurtUntil")
    battle.syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)


def timer(flow, name):
    for actorId in ACTORS:
        if name not in (hitTimer(actorId), hurtTimer(actorId)):
            continue
        if "bot" in flow.session:
            if name == hitTimer(actorId):
                checkHit(flow, actorId)
            else:
                finishHurt(flow, actorId)
        return True
    return False
