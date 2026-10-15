"""Opt-in first-client experiment for the Great Wall trial instance."""
from functools import lru_cache
import math
import os
import xml.etree.ElementTree as ET

from . import combat, contracts as wire, controls
from .codec import vision_flow
from .vfs_resource import readResource

TEST_ENVIRONMENT_VARIABLE = "T7_PVE_ZOMBIE_TEST"
INSTANCE_ID = 2017
MAP_RESOURCE_ID = 10088
INSTANCE_START_PATTERN = 6
START_PATTERN_DATA = 6
MONSTER_RESOURCE_ID = 11001
MONSTER_HERO_ID = 11001
MONSTER_WEAPON_ID = 11000002
MONSTER_RID = 0x40000001
MONSTER_SPAWN_TIMER = "pve-zombie-spawn"
MAP_ROOT = "data/scene/map/pve_map_greatwall_xs/"


def enabled(flow):
    return (flow.state.get("pveZombieTest") is True
            and "roomGateway" not in flow.context and "onlineScene" not in flow.state)


def resourceId(flow):
    match = flow.state.get("onlineScene", {})
    return match["pve"]["instanceId"] if "pve" in match else match.get("mapId", MAP_RESOURCE_ID if enabled(flow) else wire.RESOURCE_ID)


def instanceStartPattern(flow):
    return INSTANCE_START_PATTERN if enabled(flow) or "pve" in flow.state.get("onlineScene", {}) else wire.START_PATTERN


def startPatternData(flow):
    return START_PATTERN_DATA if enabled(flow) or "pve" in flow.state.get("onlineScene", {}) else 7


def _value(element, name):
    node = element.find(name)
    if node is None or "value" not in node.attrib:
        raise ValueError("PVE spawn resource is missing " + name)
    return node.attrib["value"]


def _position(node):
    values = tuple(float(part) for part in _value(node, "pos").split(","))
    direction = tuple(float(part) for part in _value(node, "dir").split(","))
    if (len(values) != 3 or len(direction) != 3
            or not all(math.isfinite(item) and abs(item) < 100000 for item in values + direction)
            or math.hypot(*direction[:2]) < .001):
        raise ValueError("invalid PVE spawn point")
    heading = round(math.degrees(math.atan2(-direction[1], direction[0])))
    return values, direction, heading


def parseSpawnResources(actor_data, group_data, point_data):
    actor_root = ET.fromstring(actor_data.decode("gbk"))
    group_root = ET.fromstring(group_data.decode("gbk"))
    point_root = ET.fromstring(point_data.decode("gbk"))
    if (actor_root.tag != "SH_RES_ACTOR_SPAWN_GROUP_TAB"
            or group_root.tag != "RES_MONSTER_SPAWN_GROUP_ENTRY_TAB"
            or point_root.tag != "RES_SPAWN_POINT_SET_ENTRY_TAB"):
        raise ValueError("unexpected PVE spawn resource schema")

    player_points = []
    for group in actor_root:
        if all(_value(group, name) == "1" for name in ("is_enable", "is_born", "is_infantry")):
            for point in group.findall("points"):
                player_points.append(_position(point))
    if not player_points:
        raise ValueError("PVE map has no enabled player spawn points")

    point_sets = {}
    for entry in point_root:
        set_id = int(_value(entry, "set_id"))
        point_sets[set_id] = tuple(_position(point) for point in entry.findall("points"))

    monster_points = []
    for group in group_root:
        monster_ids = {int(node.attrib["value"]) for node in group.findall("monster_res_array")}
        if MONSTER_RESOURCE_ID not in monster_ids:
            continue
        set_id = int(_value(group, "spawn_point_set_id"))
        monster_points.extend(point_sets.get(set_id, ()))
    if not monster_points:
        raise ValueError("PVE map has no spawn point for the selected zombie")

    player, zombie = min(
        ((player, monster) for player in player_points for monster in monster_points),
        key=lambda pair: math.dist(pair[0][0], pair[1][0]),
    )
    return {"player": player, "zombie": zombie}


@lru_cache(maxsize=1)
def loadSpawnResources(clientDirectory):
    return parseSpawnResources(
        readResource(clientDirectory, "map.vfs", MAP_ROOT + "dactorspawngroup.dat", bytes),
        readResource(clientDirectory, "map.vfs", MAP_ROOT + "dmonsterspawngroup.dat", bytes),
        readResource(clientDirectory, "map.vfs", MAP_ROOT + "dmonsterspawnset.dat", bytes),
    )


def initialize(flow):
    resources = loadSpawnResources(flow.context.get("clientDirectory", ""))
    player, zombie = resources["player"], resources["zombie"]
    controls.groundState(flow).update(position=list(player[0]), heading=player[2])
    flow.session.update(resourceId=MAP_RESOURCE_ID, spawnPosition=list(player[0]), spawnHeading=player[2],
                        pveZombie={"position": list(zombie[0]), "direction": list(zombie[1])})
    combat.initialize(flow)
    flow.log("INFO", f"pve-greatwall-test instance={INSTANCE_ID} map={MAP_RESOURCE_ID} "
                      f"monster={MONSTER_RESOURCE_ID} position={list(zombie[0])}")


def spawn(flow):
    value = flow.session.get("pveZombie")
    if not enabled(flow) or type(value) is not dict:
        return
    flow.send(0xE, vision_flow.encode_simple_monster_add_event(
        rid=MONSTER_RID, resource_id=MONSTER_RESOURCE_ID, hero_id=MONSTER_HERO_ID,
        weapon_id=MONSTER_WEAPON_ID, position=tuple(value["position"]),
        direction=tuple(value["direction"]), camp=2, server_time_ms=flow.now,
        display_name="长城四足小怪".encode("gbk")), "pve-greatwall-simple-vision-add")
    flow.log("INFO", f"pve-greatwall-spawn rid={MONSTER_RID} resource={MONSTER_RESOURCE_ID}")
