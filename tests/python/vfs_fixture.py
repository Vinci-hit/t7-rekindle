"""Synthetic indexed archives shared by Python and native integration tests."""
from hashlib import sha256
import struct
import xml.etree.ElementTree as ET
import zlib

MAGIC = bytes.fromhex('1e0db0cacefa11d0')
HEADER = bytes.fromhex('9010ba22') + bytes(2044)
SPAWN_PATH = '../data/scene/map/lysd/dactorspawngroup.dat'
NAV_PATH = '../data/scene/map/lysd/anavmesh.dat'
NAME_PATH = '../data/table/sh_res_ai_name_cli.bin'


def encodeRecord(fields):
    decoded = struct.pack('<11I', *fields)
    return bytes((((value << 1) & 255) | (decoded[(i + 1) % 44] >> 7)) ^ 0xCC
                 for i, value in enumerate(decoded))


def archiveBytes(entries, *, padding=0, level=6, inactive=False, change=None):
    """entries: (GBK path, decoded bytes, flags); change mutates directory fields."""
    data, names, records = bytearray(HEADER), bytearray(), []
    ordinal = 0
    for path, raw, flags in entries:
        data.extend(bytes(padding))
        chunks = [raw[i:i + 16384] for i in range(0, len(raw), 16384)] if flags == 3 else []
        packed = b''.join(zlib.compress(chunk, level) for chunk in chunks) if flags == 3 else raw
        name = path.encode('gbk')
        fields = [flags, len(data), len(packed), len(raw), 0, 0, 0, len(chunks), ordinal, len(name), len(names)]
        if change:
            change(fields)
        records.append(encodeRecord(fields))
        if inactive:
            records.append(encodeRecord([0] * 11))
        data.extend(packed)
        names.extend(name + b'\0')
        ordinal += len(chunks)
    return bytes(data + names) + b''.join(records)


def writeClient(root, *, padding=0, level=6):
    directory = root / 'Bin'
    directory.mkdir(parents=True, exist_ok=True)
    (root / 'Data').mkdir(exist_ok=True)
    (root / 'vfs').mkdir(exist_ok=True)
    navigation = syntheticTagfile()
    (root / 'vfs/map.vfs').write_bytes(archiveBytes([
        (NAV_PATH, navigation, 3), (SPAWN_PATH, ET.tostring(spawnXml()), 3)], padding=padding, level=level))
    (root / 'vfs/data1.vfs').write_bytes(archiveBytes([
        (NAME_PATH, nameTable([('npc', f'测试电脑{i}') for i in range(30)]), 3),
        ('../data/table/s_res_pve_stage_cli.bin', stageTable(), 3)], padding=padding, level=level))
    return directory, sha256(navigation).hexdigest()


def stageTable():
    header = bytearray(140)
    struct.pack_into('<4sIII', header, 0, b'MSES', 7, 4, 3)
    struct.pack_into('<I', header, 132, 140)
    return bytes(header) + struct.pack('<III', 1, 2, 3)


def vint(value):
    unsigned = abs(value) * 2 + (value < 0)
    data = bytearray()
    while unsigned > 127:
        data.append((unsigned & 127) | 128)
        unsigned >>= 7
    return bytes(data + bytes([unsigned]))


def syntheticTagfile():
    def string(value):
        return vint(len(value)) + value.encode("ascii")

    def schema(name, version, parent, fields):
        data = vint(2) + string(name) + vint(version) + vint(parent) + vint(len(fields))
        for field, kind, ref in fields:
            data += string(field) + vint(kind) + (string(ref) if ref else b"")
        return data

    def integers(values):
        return vint(4) + b"".join(map(vint, values))

    data = MAGIC + vint(1) + vint(5) + string("hk_2014.2.5-r1") + vint(-10) + vint(0) * 3
    data += schema("Base", 0, 0, [("referenceCount", 0, None)])
    data += schema("hkaiNavMesh", 14, 1, [
        ("faces", 25, "Face"), ("edges", 25, "Edge"),
        ("vertices", 20, None), ("streamingSets", 18, None)])
    data += schema("Face", 0, 0, [(n, 2, None) for n in ("startEdgeIndex", "numEdges", "numUserEdges")])
    data += schema("Edge", 0, 0, [(n, 2, None) for n in ("a", "b", "oppositeEdge", "oppositeFace")])
    data += vint(4) + vint(2) + b"\x1e"
    data += vint(2) + b"\x07" + integers([0, 4]) + integers([4, 4]) + integers([0, 0])
    data += vint(8) + b"\x0f"
    for column in ([0, 1, 2, 3, 1, 4, 5, 2], [1, 2, 3, 0, 4, 5, 2, 1],
                   [-1, 7, -1, -1, -1, -1, -1, 1], [-1, 1, -1, -1, -1, -1, -1, 0]):
        data += integers(column)
    data += vint(6) + vint(4)
    for x, y in [(0, 0), (4, 0), (4, 4), (0, 4), (8, 0), (8, 4)]:
        data += struct.pack("<4f", x, y, 0, 0)
    return data + vint(0) + vint(7)


def nameTable(rows):
    header = bytearray(140)
    struct.pack_into('<4sIII', header, 0, b'MSES', 7, 68, len(rows))
    header[64:68] = b'GBK\0'
    struct.pack_into('<I', header, 132, 140)
    return bytes(header) + b''.join(struct.pack('<I32s32s', 0, kind.encode('ascii'), name.encode('gbk'))
                                    for kind, name in rows)


def spawnXml():
    root = ET.Element("SH_RES_ACTOR_SPAWN_GROUP_TAB")
    for camp, born, infantry, count in [(1, 1, 1, 10), (2, 1, 1, 10), (1, 0, 1, 14),
                                        (2, 0, 1, 12), (1, 1, 0, 4), (2, 1, 0, 6)]:
        group = ET.SubElement(root, "SH_RES_ACTOR_SPAWN_GROUP")
        flags = dict(is_enable=1, is_blue=int(camp == 1), is_red=int(camp == 2), is_born=born,
                     is_revive=1-born, is_infantry=infantry, is_cavalry=1-infantry, point_num=count)
        for key, value in flags.items():
            ET.SubElement(group, key, value=str(value))
        for i in range(count):
            point = ET.SubElement(group, "points")
            ET.SubElement(point, "pos", value=f"{i},{camp * 20},0.2")
            ET.SubElement(point, "dir", value="0,1,0" if camp == 1 else "0,-1,0")
    return root
