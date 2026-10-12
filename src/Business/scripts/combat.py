"""Local melee contact and health; original client messages render the result."""
import math

from . import actors, battle, blade_contacts, body_collision, bot, bot_life, bot_roaming, contact_journal, contracts as wire, player_life
from .codec import attr_flow, battle_flow, kill_flow

MAX_HEALTH, DAMAGE = 100, 20
DASH_DAMAGE = 40  # Offline tuning, independent of the original damage formula.
HURT_STATE, HURT_MS = 35, 400
CONTACT_MS, CONTACT_END_MARGIN_MS = 50, 100
DASH_CONTACT_END_MS = 1133  # Current weapon's s_battle_param_cli collision end, not animation end.
REACH, HEIGHT_LIMIT, FACING_COS = 2.2, 1.0, 0.5
HIT_HEIGHT = 1.0


def hitTimer(actorId):
    return f"combat-hit-{actorId}"


def hurtTimer(actorId):
    return f"combat-hurt-{actorId}"


def initialize(flow):
    player_life.reset(flow)
    for actorId in actors.ids(flow.session):
        data = battle.actorData(flow, actorId)
        data["health"] = MAX_HEALTH
        data['contactLifeAt'] = flow.now
        data.pop('contactSwings', None)
        data['killStreak'] = 0
        data.update(kills=0, deaths=0, battlePoints=0)
        data.pop("hurtUntil", None)
        data.pop('life', None)
        flow.cancel(actors.timerName(bot_life.RESPAWN_TIMER, actorId))
        cancelSwing(flow, actorId)
        flow.cancel(hurtTimer(actorId))


def validState(data):
    if type(data.get('killStreak', 0)) is not int or not 0 <= data.get('killStreak', 0) <= kill_flow.MAX_STREAK:
        return False
    if any(type(data.get(key, 0)) is not int or not 0 <= data.get(key, 0) <= kill_flow.MAX_SCORE
           for key in ('kills', 'deaths', 'battlePoints')):
        return False
    if "health" not in data:
        return not any(key in data for key in ("swing", "hurtUntil"))
    if type(data["health"]) is not int or not 0 <= data["health"] <= MAX_HEALTH:
        return False
    if "hurtUntil" in data and (type(data["hurtUntil"]) is not int or data["hurtUntil"] < 0):
        return False
    if type(data.get('contactLifeAt', 0)) is not int or data.get('contactLifeAt', 0) < 0:
        return False
    previous = data.get('contactSwings', [])
    if type(previous) is not list or len(previous) > 32:
        return False
    if not all(validSwing(value) and type(value.get('closedAt')) is int and value['closedAt'] >= 0
               and type(value.get('intent')) is int and battle.attackChain(value['intent']) is not None
               and type(value.get('state')) is int for value in previous):
        return False
    swing = data.get('swing')
    return swing is None or validSwing(swing)


def validSwing(swing):
    if type(swing) is not dict or not all(
            type(swing.get(key)) is int and swing[key] >= 0 for key in ("seq", "epoch", "until")):
        return False
    hits = swing.get("hits", [])
    cutoff = swing.get('cutoff', 0)
    return (type(cutoff) in (int, float) and math.isfinite(cutoff) and cutoff >= 0
            and type(hits) is list and all(type(rid) is int and 1 <= rid <= actors.MAX_ACTORS for rid in hits)
            and len(hits) == len(set(hits)))


def cancelSwing(flow, actorId, *, reason=None):
    flow.cancel(hitTimer(actorId))
    data = battle.actorData(flow, actorId)
    if reason == 'interrupted':
        for previous in data.get('contactSwings', []):
            previous['cutoff'] = min(previous.get('cutoff', flow.now), getattr(flow, 'contactAt', flow.now))
    swing = data.pop("swing", None)
    if swing is not None and reason and contact_journal.available(flow):
        swing['closedAt'] = flow.now
        if reason != 'action-changed':
            swing['cutoff'] = getattr(flow, 'contactAt', flow.now)
        previous = data.get('contactSwings', [])
        if len(previous) >= 32:
            raise ValueError('native contact settlement stalled: swing history exhausted')
        data['contactSwings'] = [*previous, swing]
        return
    if swing is not None and reason and not swing.get("hits"):
        detail = swing.get("miss", "expired") if flow.now >= swing["until"] else reason
        flow.log("DEBUG", f"combat-miss attacker={actorId} reason={detail}")


def contactEndMs(intent):
    if intent == battle.DASH_ATTACK:
        return DASH_CONTACT_END_MS
    return battle.phaseMs(intent, battle.strikeIndex(intent)) - CONTACT_END_MARGIN_MS


def actionChanged(flow, actorId, state):
    cancelSwing(flow, actorId, reason="action-changed")
    data = battle.actorData(flow, actorId)
    action = data.get("action", {})
    chain = battle.attackChain(action.get("intent"))
    strike = battle.strikeIndex(action.get("intent"))
    if (not actors.active(flow, actorId) or "health" not in data or not actors.bots(flow.session) or not battle.enabled(flow)
            or chain is None or action.get("index") != strike or state != chain[strike]):
        return
    data["swing"] = {"seq": action["seq"], "epoch": flow.session["instanceStartedAt"],
                     "until": flow.now + contactEndMs(action["intent"]),
                     "beganAt": flow.now, "intent": action['intent'], "state": state,
                     "hits": []}
    flow.later(hitTimer(actorId), 0)


def facing(position, target, heading):
    dx, dy = target[0] - position[0], target[1] - position[1]
    length = math.hypot(dx, dy)
    yaw = math.radians(heading)
    return length < 0.01 or (dx * math.cos(yaw) - dy * math.sin(yaw)) / length >= FACING_COS


def heading(snapshot, actorId):
    value = snapshot.get(actors.positionName(snapshot, actorId) + "Heading")
    return value if type(value) in (int, float) and math.isfinite(value) and -180 <= value <= 180 else None


def contactFailure(flow, snapshot, attacker):
    origin = snapshot[actors.positionName(snapshot, attacker)]
    target = snapshot["player" if actors.positionName(snapshot, attacker) == "bot" else "bot"]
    distance = math.dist(origin[:2], target[:2])
    if distance > REACH:
        return f"range distance={distance:.2f}"
    if abs(origin[2] - target[2]) > HEIGHT_LIMIT:
        return "height"
    if any(heading(snapshot, actorId) is None for actorId in (snapshot.get("sourceId", 2), snapshot.get("targetId", 1))):
        return "heading-unavailable"
    yaw = heading(snapshot, attacker)
    if not facing(origin, target, yaw):
        bearing = math.degrees(math.atan2(-(target[1] - origin[1]), target[0] - origin[0]))
        angle = abs((bearing - yaw + 180) % 360 - 180)
        return f"facing distance={distance:.2f} angle={angle:.1f}"
    return None


def terrainContact(snapshot, attacker, *, targeted):
    index = 0 if actors.positionName(snapshot, attacker) == "player" else 1
    probes = snapshot.get("terrain")
    if type(probes) is not list or len(probes) != 2 or type(probes[index]) is not dict:
        return None
    contact = probes[index].get("target" if targeted else "forward")
    if (type(contact) is not dict or contact.get("ready") is not True
            or type(contact.get("hit")) is not bool):
        return None
    if contact["hit"] and (type(contact.get("material")) is not int
            or not -1 <= contact["material"] <= 7
            or not all(bot.validPosition(contact.get(name)) for name in ("position", "normal", "direction"))
            or not all(.9 <= math.hypot(*contact[name]) <= 1.1 for name in ("normal", "direction"))):
        return None
    return contact


def hitTerrain(flow, attacker, contact, *, intent=None):
    cancelSwing(flow, attacker)
    if attacker != wire.ACTOR_ID:
        battle.actorData(flow, attacker)['forwardMs'] = 0
        flow.cancel(actors.timerName(bot.RELEASE_TIMER, attacker))
        battle.actorData(flow, attacker)["reactAt"] = None
    battle.terrainReaction(flow, attacker, intent=intent)
    flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_hit_point_notify(
        hit_pos=tuple(contact["position"]), hit_direction=tuple(contact["direction"]),
        hit_normal=tuple(contact["normal"]), material_id=contact["material"], ignore_hurt=True,
        attacker_mid=attacker, target_mid=0, attacker_weapon_id=wire.WEAPON_ID,
        target_weapon_id=0, server_time_ms=int(getattr(flow, 'contactAt', flow.now))), "combat-terrain-hit")
    point = ",".join(f"{v:.2f}" for v in contact["position"])
    flow.log("DEBUG",
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


def scoreTotals(data):
    return data.get('kills', 0), data.get('deaths', 0), data.get('battlePoints', 0)


def publishScore(flow, actorId, *, previous=None):
    current = scoreTotals(battle.actorData(flow, actorId))
    if current == (0, 0, 0):
        return
    previous = current if previous is None else previous
    flow.send(kill_flow.SCORE_COMMAND, kill_flow.encode_score_update_event(
        server_time_ms=flow.now, entity_rid=actorId, previous_kills=previous[0], kills=current[0],
        previous_deaths=previous[1], deaths=current[1],
        previous_points=previous[2], points=current[2]), 'combat-score')


def publishCampScore(flow):
    members = tuple(actors.data(flow, actorId) for actorId in actors.ids(flow.session))
    # The deathmatch HUD's kill counters are script variables, not combat points.
    totals = {camp: min(kill_flow.MAX_SCORE, sum(value.get('kills', 0) for value in members
                                               if value.get('camp', 1) == camp)) for camp in (1, 2)}
    flow.send(kill_flow.SCRIPT_COMMAND, kill_flow.encode_camp_kill_update(
        server_time_ms=flow.now, instance_id=1, blue_kills=totals[1], red_kills=totals[2]), 'combat-camp-score')


def parry(flow, attacker, defender, snapshot, *, attackIntent=None, defenceIntent=None):
    attack = battle.actionState(flow, attacker)
    defence = battle.actionState(flow, defender)
    if attackIntent is not None:
        attack = dict(attack, intent=attackIntent)
    if defenceIntent is not None:
        defence = dict(defence, intent=defenceIntent, index=1, released=False)
    lateral = attack["intent"] in (300540, 300550)
    acceptsAttack = (defence["intent"] in (300590, 300610) if lateral
                     else defence["intent"] == bot.PARRY_FOR_ATTACK[attack["intent"]])
    if defence["index"] != 1 or defence["released"] or not acceptsAttack:
        return False
    origin, target = snapshot[actors.positionName(snapshot, defender)], snapshot[actors.positionName(snapshot, attacker)]
    yaw = heading(snapshot, defender)
    if yaw is None or not facing(origin, target, yaw):
        return False
    for rid in (attacker, defender):
        if rid != wire.ACTOR_ID:
            battle.actorData(flow, rid)['forwardMs'] = 0
    battle.parryReaction(flow, attacker, defender, success=False, intent=attack['intent'])
    battle.parryReaction(flow, defender, attacker, success=True, intent=defence['intent'])
    if defender != wire.ACTOR_ID:
        bot.parrySucceeded(flow, defender)
    flow.log("DEBUG",
        f"combat-parry attacker={attacker} target={defender} attack={attack['intent']} defence={defence['intent']}")
    return True


def bodyContact(snapshot, attacker, target):
    """Project the torso-center line onto the target's upright body proxy."""
    origin = snapshot[actors.positionName(snapshot, attacker)]
    destination = snapshot[actors.positionName(snapshot, target)]
    delta = tuple(b - a for a, b in zip(origin, destination))
    horizontal = math.hypot(*delta[:2])
    if horizontal < .01:
        # Coincident XY has no incoming side; avoid a vertical render orientation.
        yaw = math.radians(heading(snapshot, attacker))
        direction = (math.cos(yaw), -math.sin(yaw), 0.)
        normal = tuple(-value for value in direction)
        position = (destination[0] + normal[0] * body_collision.RADIUS,
                    destination[1] + normal[1] * body_collision.RADIUS, destination[2] + HIT_HEIGHT)
        return position, direction, normal
    start = (origin[0], origin[1], origin[2] + HIT_HEIGHT)
    normal = (-delta[0] / horizontal, -delta[1] / horizontal, 0.)
    # Negative entry fractions keep overlapping bodies' feedback on the surface.
    fraction = 1 - body_collision.RADIUS / horizontal
    if delta[2] != 0:
        cap = destination[2] + (body_collision.HEIGHT if delta[2] < 0 else 0)
        capFraction = (cap - start[2]) / delta[2]
        if capFraction > fraction:
            fraction = capFraction
            normal = (0., 0., -math.copysign(1., delta[2]))
    position = tuple(start[i] + delta[i] * fraction for i in range(3))
    length = math.hypot(*delta)
    direction = tuple(value / length for value in delta)
    return position, direction, normal


def applyHit(flow, attacker, target, snapshot, *, contact=None, intent=None):
    if not actors.active(flow, attacker) or not actors.active(flow, target):
        return
    hitPosition, hitDirection, hitNormal = (bodyContact(snapshot, attacker, target) if contact is None
                                          else tuple(tuple(contact[key]) for key in ("position", "direction", "normal")))
    data = battle.actorData(flow, target)
    action = battle.actionState(flow, attacker)
    intent = action['intent'] if intent is None else intent
    damage = DASH_DAMAGE if intent == battle.DASH_ATTACK else DAMAGE
    previousHealth = data["health"]
    data["health"] = max(0, data["health"] - damage)
    publishHealth(flow, target)
    position = snapshot[actors.positionName(snapshot, target)]
    flow.send(4, battle_flow.encode_battle_hit_point_notify(
        hit_pos=hitPosition, hit_direction=hitDirection, hit_normal=hitNormal,
        attacker_mid=attacker, target_mid=target, attacker_weapon_id=wire.WEAPON_ID,
        target_weapon_id=0, server_time_ms=int(getattr(flow, 'contactAt', flow.now))), "combat-hit-point")
    if attacker == wire.ACTOR_ID:
        # Retained contacts can arrive after the attacker has advanced to another state.
        sequence = action['seq'] if contact is None else contact.get('seq', action['seq'])
        flow.send(battle_flow.BATTLE_COMMAND, battle_flow.encode_battle_result(
            attacker_rid=attacker, target_rid=target, attacker_seq_no=sequence,
            target_life_state=battle_flow.LIFE_STATE_KILL if data['health'] == 0 else battle_flow.LIFE_STATE_NONE,
            attack_weapon_id=wire.WEAPON_ID, target_result_type=battle_flow.RESULT_DATA_DELTA_HP,
            target_result_data=data['health'] - previousHealth), 'combat-damage')
    flow.log("DEBUG", f"combat-hit attacker={attacker} target={target} hp={data['health']}")
    if data['health'] == 0:
        if target == wire.ACTOR_ID:
            player_life.die(flow, attacker, position)
        else:
            bot_life.die(flow, target, attacker, position)
        return
    bot_roaming.noticeHit(flow, attacker, target, snapshot, damage=damage)
    battle.resetAction(flow, target)
    if target != wire.ACTOR_ID:
        data['forwardMs'] = 0
        flow.cancel(actors.timerName(bot.RELEASE_TIMER, target))
        data["reactAt"] = None
    data["hurtUntil"] = flow.now + HURT_MS
    battle.syncState(flow, HURT_STATE, target, targetId=attacker)
    flow.later(hurtTimer(target), HURT_MS)


def checkHit(flow, attacker):
    if contact_journal.available(flow):
        return  # Native contact batches are settled once, globally in occurrence order.
    data = battle.actorData(flow, attacker)
    swing = data.get("swing")
    action = data.get("action", {})
    strike = battle.strikeIndex(action.get("intent"))
    if (swing is None or not actors.active(flow, attacker) or not battle.enabled(flow) or action.get("index") != strike
            or action.get("seq") != swing["seq"]
            or swing["epoch"] != flow.session.get("instanceStartedAt")):
        cancelSwing(flow, attacker, reason="interrupted")
        return
    raw = actors.world(flow)
    beganAt = swing["until"] - contactEndMs(action["intent"])
    contacts = blade_contacts.contacts(raw, attacker, beganAt, swing['until'])
    failure = "blade-unavailable" if contacts is None else "blade-clear"
    enemies = set(actors.enemies(flow, attacker, raw))
    for contact in contacts or ():
        target = contact["targetId"]
        if target == 0:
            hitTerrain(flow, attacker, contact)
            return
        if target not in enemies or target in swing.get("hits", []):
            continue
        snapshot = actors.pair(raw, attacker, target)
        if snapshot is None:
            failure = "world-unavailable"
            continue
        if parry(flow, attacker, target, snapshot):
            return
        swing["hits"] = [*swing.get("hits", []), target]
        applyHit(flow, attacker, target, snapshot, contact=contact)
    hits = swing.get("hits", [])
    camp = data.get("camp", 1)
    remaining = any(rid not in hits and actors.active(flow, rid)
                    and battle.actorData(flow, rid).get("camp", 1) != camp for rid in actors.ids(flow.session))
    nextCheckAt = min(flow.now + CONTACT_MS, swing["until"])
    if hits and not remaining:
        cancelSwing(flow, attacker)
    elif flow.now < nextCheckAt <= swing["until"]:
        swing["miss"] = failure
        flow.later(hitTimer(attacker), nextCheckAt - flow.now)
    else:
        cancelSwing(flow, attacker, reason=failure)


def worldUpdated(flow):
    if not battle.enabled(flow):
        return
    if contact_journal.available(flow):
        contact_journal.settle(flow)
        return
    pending = flow.session.get("pending", {})
    swings = sorted((pending.get(hitTimer(rid), flow.now), rid) for rid in actors.ids(flow.session)
                    if battle.actorData(flow, rid).get("swing") is not None)
    for _, actorId in swings:
        # A fresh native frame is damage evidence; the legacy retry deadline is not a hit gate.
        flow.cancel(hitTimer(actorId))
        checkHit(flow, actorId)


def finishHurt(flow, actorId):
    data = battle.actorData(flow, actorId)
    if "hurtUntil" not in data:
        return
    if not actors.active(flow, actorId) or not battle.enabled(flow) or not battle.botInstanceMatches(flow):
        data.pop("hurtUntil")
        return
    if actors.pose(actors.world(flow), actorId) is None:
        flow.later(hurtTimer(actorId), CONTACT_MS)
        return
    data.pop("hurtUntil")
    battle.syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)


def timer(flow, name):
    for actorId in actors.ids(flow.session):
        if name not in (hitTimer(actorId), hurtTimer(actorId)):
            continue
        if actors.bots(flow.session):
            if name == hitTimer(actorId):
                checkHit(flow, actorId)
            else:
                finishHurt(flow, actorId)
        return True
    return False
