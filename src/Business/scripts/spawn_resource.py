"""Infantry spawn and respawn placements from the selected client's map resource."""
from functools import lru_cache
import math
import xml.etree.ElementTree as ET

from .vfs_resource import readResource

SPAWN_PATH = 'data/scene/map/lysd/dactorspawngroup.dat'


def parsePoints(data, flagName, counts):
    root = ET.fromstring(data.decode("gbk"))
    if root.tag != "SH_RES_ACTOR_SPAWN_GROUP_TAB":
        raise ValueError("unexpected spawn resource root")
    camps = {1: [], 2: []}
    for group in root:
        def flag(name):
            node = group.find(name)
            if node is None or node.get("value") not in ("0", "1"):
                raise ValueError("invalid spawn flag: " + name)
            return node.get("value") == "1"
        if not (flag("is_enable") and flag(flagName) and flag("is_infantry") and not flag("is_cavalry")):
            continue
        blue, red = flag("is_blue"), flag("is_red")
        if blue == red:
            raise ValueError("infantry spawn requires one camp")
        points = group.findall("points")
        if len(points) != int(group.find("point_num").get("value")):
            raise ValueError("spawn point count mismatch")
        for point in points:
            vectors = [tuple(float(v) for v in point.find(name).get("value").split(","))
                       for name in ("pos", "dir")]
            if any(len(v) != 3 or not all(math.isfinite(n) and abs(n) < 100000 for n in v) for v in vectors):
                raise ValueError("invalid spawn vector")
            position, direction = vectors
            if math.hypot(*direction[:2]) < .001:
                raise ValueError("invalid spawn direction")
            heading = round(math.degrees(math.atan2(-direction[1], direction[0])))
            camps[1 if blue else 2].append((position, heading))
    if any(len(camps[camp]) != count for camp, count in counts.items()):
        raise ValueError("unsupported infantry spawn counts: " + flagName)
    if len({position for points in camps.values() for position, _ in points}) != sum(counts.values()):
        raise ValueError("overlapping infantry spawns")
    return {camp: tuple(points) for camp, points in camps.items()}


def parseSpawns(data):
    return parsePoints(data, "is_born", {1: 10, 2: 10})


def parseRespawns(data):
    return parsePoints(data, "is_revive", {1: 14, 2: 12})


@lru_cache(maxsize=1)
def loadSpawns(clientDirectory):
    return readResource(clientDirectory, 'map.vfs', SPAWN_PATH, parseSpawns)


@lru_cache(maxsize=1)
def loadRespawns(clientDirectory):
    return readResource(clientDirectory, 'map.vfs', SPAWN_PATH, parseRespawns)
