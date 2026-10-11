"""Melee action phases shared by the local player and computer player."""
import struct

from . import actors, contracts as wire, controls
from .codec import battle_flow

ACTION_TIMER = "battle-action-phase"
BLOCK_PHASE_MS = 0
ATTACK_RELEASE = 300020
BLOCK_RELEASE = 300620
DASH_ATTACK = 700250
DASH_CHAIN = (438, 439)
# Strike duration: s_battle_param_cli group 6, weapon category 32, action 32, mode 2.
# Enter the strike directly; only the reaction delay remains offline tuning.
DASH_PHASES_MS = (0, 2033)
DASH_REACTION_MS = 350
ATTACK_CHAINS = {
    300560: (235, 237, 238, 239),  # up
    300550: (241, 243, 244, 245),  # right
    300570: (247, 249, 250, 251),  # down
    300540: (253, 255, 256, 257),  # left
}
BLOCK_CHAINS = {
    # Prepare -> ParryHold -> ParryRelease. Release only follows the matching key-up.
    300580: (305, 223, 227),  # up
    300590: (308, 224, 228),  # right
    300600: (307, 225, 229),  # down
    300610: (306, 226, 230),  # left
}
PARRIED_STATES = {300560: 240, 300550: 246, 300570: 252, 300540: 258, DASH_ATTACK: 443}
PARRY_SUCCESS_STATES = {300580: 231, 300590: 232, 300600: 233, 300610: 234}
TERRAIN_REBOUND_STATES = {300560: 466, 300550: 468, 300570: 467, 300540: 469, DASH_ATTACK: 443}
# s_battle_param_cli: group 2, weapon category 32, directions 1/4/2/3, mode 2.
# The held phase has no deadline. These are base timings, not an attack-speed formula.
WEAPON_ATTACK_PHASES_MS = {
    1030411: {
        300560: (360, 0, 640, 500),
        300550: (360, 0, 640, 500),
        300570: (400, 0, 700, 400),
        300540: (360, 0, 640, 530),
    },
}


def phaseMs(intent, index):
    if intent == DASH_ATTACK:
        return DASH_PHASES_MS[index]
    if intent not in ATTACK_CHAINS:
        return BLOCK_PHASE_MS
    timings = WEAPON_ATTACK_PHASES_MS.get(wire.WEAPON_ID)
    if timings is None:
        raise ValueError("weapon has no verified attack timing")
    return timings[intent][index]


def attackChain(intent):
    return DASH_CHAIN if intent == DASH_ATTACK else ATTACK_CHAINS.get(intent)


def reactionMs(intent):
    return DASH_REACTION_MS if intent == DASH_ATTACK else phaseMs(intent, -1)


def strikeIndex(intent):
    return 1 if intent == DASH_ATTACK else 2


def actorData(flow, actorId):
    return actors.data(flow, actorId)


def actionTimer(actorId):
    return ACTION_TIMER if actorId == wire.ACTOR_ID else actors.timerName("bot-action-phase", actorId)


def actionState(flow, actorId=wire.ACTOR_ID):
    return actorData(flow, actorId).setdefault("action", {
        "intent": 0, "index": -1, "seq": 1, "released": False,
    })


def enabled(flow):
    return (flow.session["role"] == "instance" and not flow.session.get("leaving")
            and flow.session.get("controlBaseline") == wire.BASELINE_ID
            and controls.groundEnabled(flow))


def botInstanceMatches(flow):
    snapshot = flow.context.get("botWorld")
    return (type(snapshot) is dict
            and snapshot.get("instanceStartedAt") == flow.session.get("instanceStartedAt"))


def syncState(flow, state, actorId=wire.ACTOR_ID, *, targetId=None):
    from . import combat
    startedAt = flow.session.get("instanceStartedAt")
    if type(startedAt) is not int:
        raise ValueError("battle animation requires an instance start time")
    action = actionState(flow, actorId)
    sequence = action["seq"] % 0xFFFF + 1
    # DashAttack.enforcedDuration consumes the full state duration, not elapsed time.
    duration = phaseMs(DASH_ATTACK, 1) if state == DASH_CHAIN[1] else 0
    fields = dict(instance_id=actorId, seq_no=sequence, state=state,
                  state_change_ms=flow.now - startedAt, state_time_ms=duration)
    body = (battle_flow.encode_battle_state_sync_simple(**fields) if targetId is None
            else battle_flow.encode_battle_state_sync(**fields, target_rid=targetId))
    action["seq"] = sequence
    if actorId != wire.ACTOR_ID:
        action.update(state=state, changedAt=flow.now)
        if targetId is None:
            action.pop("targetId", None)
        else:
            action["targetId"] = targetId
    flow.send(battle_flow.BATTLE_COMMAND, body,
              ("battle-model-state-" if actorId == wire.ACTOR_ID else "bot-model-state-") + str(state))
    combat.actionChanged(flow, actorId, state)


def resetAction(flow, actorId=wire.ACTOR_ID):
    from . import combat
    combat.cancelSwing(flow, actorId, reason="interrupted")
    flow.cancel(actionTimer(actorId))
    data = actorData(flow, actorId)
    if "action" in data:
        data["action"].update(intent=0, index=-1, released=False)
        data["action"].pop("buffered", None)
        data["action"].pop("reaction", None)


def startAction(flow, intent, released=False, *, actorId=wire.ACTOR_ID):
    data = actorData(flow, actorId)
    if not actors.active(flow, actorId) or "hurtUntil" in data or data.get("action", {}).get("reaction"):
        return
    index = strikeIndex(intent) if intent == DASH_ATTACK else 0
    delay = phaseMs(intent, index)
    chain = attackChain(intent) or BLOCK_CHAINS[intent]
    action = actionState(flow, actorId)
    action.update(intent=intent, index=index, released=released or intent == DASH_ATTACK)
    action.pop("buffered", None)
    syncState(flow, chain[index], actorId)
    flow.later(actionTimer(actorId), delay)


def parryReaction(flow, actorId, targetId, *, success, intent=None):
    action = actionState(flow, actorId)
    intent = action['intent'] if intent is None else intent
    resetAction(flow, actorId)
    action.update(intent=intent, released=not success,
                  reaction="parry-success" if success else "parried")
    state = PARRY_SUCCESS_STATES[intent] if success else PARRIED_STATES[intent]
    syncState(flow, state, actorId, targetId=targetId)
    flow.later(actionTimer(actorId), BLOCK_PHASE_MS if success else reactionMs(intent))


def finishReaction(flow, actorId):
    action = actionState(flow, actorId)
    if not botInstanceMatches(flow):
        resetAction(flow, actorId)
    elif action["reaction"] == "parry-success" and not action["released"]:
        action.pop("reaction")
        action["index"] = 1
        syncState(flow, BLOCK_CHAINS[action["intent"]][1], actorId)
    else:
        syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
        resetAction(flow, actorId)


def terrainReaction(flow, actorId, *, intent=None):
    action = actionState(flow, actorId)
    intent = action['intent'] if intent is None else intent
    resetAction(flow, actorId)
    action.update(intent=intent, released=True, reaction="terrain-rebound")
    syncState(flow, TERRAIN_REBOUND_STATES[intent], actorId, targetId=0)
    flow.later(actionTimer(actorId), reactionMs(intent))


def bufferActionInput(action, intent):
    chain = attackChain(action["intent"]) or BLOCK_CHAINS.get(action["intent"])
    if chain is None or action["index"] < 0:
        return False
    if action["intent"] in BLOCK_CHAINS and (
            not action["released"] or action["index"] != len(chain) - 1):
        return False
    if intent in ATTACK_CHAINS or intent in BLOCK_CHAINS:
        action["buffered"] = {"intent": intent, "released": False}
        return True
    buffered = action.get("buffered")
    if buffered is not None:
        if ((intent == ATTACK_RELEASE and buffered["intent"] in ATTACK_CHAINS)
                or (intent == BLOCK_RELEASE and buffered["intent"] in BLOCK_CHAINS)):
            action["buffered"] = {**buffered, "released": True}
    # Key-up must also release the current attack during prepare or hold.
    return False


def timer(flow, name):
    actorId = next((rid for rid in actors.ids(flow.session) if name == actionTimer(rid)), None)
    if actorId is None:
        return False
    if not actors.active(flow, actorId):
        return True
    if actorId != wire.ACTOR_ID and not botInstanceMatches(flow):
        resetAction(flow, actorId)
        return True
    action = actorData(flow, actorId).get("action")
    if not enabled(flow) or action is None:
        resetAction(flow, actorId)
        return True
    if action.get("reaction"):
        finishReaction(flow, actorId)
        return True
    if action["index"] < 0:
        resetAction(flow, actorId)
        return True
    intent = action["intent"]
    chain = attackChain(intent) or BLOCK_CHAINS.get(intent)
    if chain is None:
        raise ValueError("unknown active battle animation")
    nextIndex = action["index"] + 1
    if intent in ATTACK_CHAINS and action["released"] and nextIndex == 1:
        nextIndex = 2
    holdIndex = 1
    if not action["released"] and nextIndex > holdIndex:
        return True
    if nextIndex == len(chain):
        buffered = action.get("buffered")
        if buffered is not None and not buffered["released"]:
            startAction(flow, buffered["intent"], buffered["released"], actorId=actorId)
        else:
            syncState(flow, battle_flow.ACT_STATE_IDLE, actorId)
            resetAction(flow, actorId)
        return True
    action["index"] = nextIndex
    syncState(flow, chain[nextIndex], actorId)
    if action["released"] or nextIndex < holdIndex:
        flow.later(name, phaseMs(intent, nextIndex))
    return True


def message(flow, command, selector, body):
    if (command != battle_flow.BATTLE_COMMAND or selector != 1
            or flow.session["role"] != "instance"):
        return False
    if (flow.session.get("leaving")
            or flow.session.get("controlBaseline") != wire.BASELINE_ID):
        resetAction(flow)
        return False
    wire.exact(body, 11, "battle action")
    keys, intent, autoParry = struct.unpack_from(">Iib", body, 2)
    if autoParry not in (0, 1):
        raise ValueError("battle auto-parry flag must be 0 or 1")
    chain = attackChain(intent) or BLOCK_CHAINS.get(intent)
    if chain is None and intent not in (ATTACK_RELEASE, BLOCK_RELEASE):
        return False
    if not enabled(flow) or not actors.active(flow, wire.ACTOR_ID):
        resetAction(flow)
        return True
    if "hurtUntil" in flow.session:
        return True
    if chain is not None:
        phaseMs(intent, 0)
    action = actionState(flow)
    if intent == DASH_ATTACK:
        # Movement keys use a separate cache; the native hook checks them before arming RUN.
        if (keys & (1 << 26)
                and keys & ((1 << 21) | (1 << 7))
                and action["index"] < 0 and not action.get("reaction")):
            startAction(flow, intent)
        return True
    if action.get("reaction"):
        releaseAction(flow, intent)
        return True
    interruptsAttack = (intent in BLOCK_CHAINS and action["intent"] in ATTACK_CHAINS
                        and action["index"] in (0, 1))
    interruptsParry = (intent in ATTACK_CHAINS and action["intent"] in BLOCK_CHAINS
                       and action["index"] in (0, 1))
    if interruptsAttack or (interruptsParry and not action["released"]):
        resetAction(flow)
        startAction(flow, intent)
        return True
    if bufferActionInput(action, intent):
        return True
    if chain is not None:
        if action["index"] >= 0:
            return True
        startAction(flow, intent)
        return True
    releaseAction(flow, intent)
    return True


def releaseAction(flow, intent, actorId=wire.ACTOR_ID):
    action = actionState(flow, actorId)
    if action.get("reaction"):
        if action["reaction"] == "parry-success" and intent == BLOCK_RELEASE:
            action["released"] = True
        return True
    expectedRelease = ATTACK_RELEASE if action["intent"] in ATTACK_CHAINS else BLOCK_RELEASE
    if action["index"] < 0 or action["released"] or intent != expectedRelease:
        return True
    if intent == ATTACK_RELEASE and action["index"] == 0:
        action["released"] = True
        return True
    flow.cancel(actionTimer(actorId))
    action.update(index=2, released=True)
    chain = ATTACK_CHAINS.get(action["intent"]) or BLOCK_CHAINS[action["intent"]]
    syncState(flow, chain[2], actorId)
    flow.later(actionTimer(actorId), phaseMs(action["intent"], 2))
    return True
