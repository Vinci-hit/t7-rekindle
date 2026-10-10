import struct

import pytest

from Business.scripts.navigation import NavMesh
from Business.scripts.navmesh_resource import MAGIC, TagReader, readCompressedEntry


def mesh():
    # An L-shaped walkable corridor, with a wall in the missing upper-left square.
    return NavMesh.fromPolygons([
        [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)],
        [(4, 0, 0), (8, 0, 0), (8, 4, 0), (4, 4, 0)],
        [(4, 4, 0), (8, 4, 0), (8, 8, 2), (4, 8, 2)],
        [(20, 0, 0), (24, 0, 0), (24, 4, 0), (20, 4, 0)],
    ])


def test_path_uses_connected_faces_and_turns_around_wall():
    nav = mesh()
    path = nav.findPath((1, 2, 0), (6, 7, 1.5), radius=0.3)
    assert path.status == "ready"
    assert path.faces == (0, 1, 2)
    assert path.points[0] == (1, 2, 0)
    assert path.points[-1] == (6, 7, 1.5)
    for a, b in zip(path.points, path.points[1:]):
        for i in range(21):
            p = tuple(x + (y - x) * i / 20 for x, y in zip(a, b))
            assert nav.locate(p) is not None
    assert path == nav.findPath((1, 2, 0), (6, 7, 1.5), radius=0.3)


def test_unreachable_and_outside_points_never_generate_straight_line():
    nav = mesh()
    assert nav.findPath((1, 2, 0), (22, 2, 0)).status == "no-path"
    assert nav.findPath((1, 2, 0), (2, 7, 0)).status == "outside-mesh"
    assert nav.locate((2, 2, 50)) is None


def test_height_and_same_face_path():
    nav = mesh()
    assert nav.project((6, 6, 1.1)) == pytest.approx((6, 6, 1))
    assert nav.findPath((5, 5, 0.5), (7, 7, 1.5)).faces == (2,)


def test_narrow_portal_rejects_large_agent():
    assert mesh().findPath((1, 2, 0), (6, 2, 0), radius=2.1).status == "no-path"


def test_nonfinite_vertices_and_invalid_polygons_are_rejected():
    with pytest.raises(ValueError):
        NavMesh.fromPolygons([[(0, 0, 0), (float("nan"), 0, 0), (0, 1, 0)]])
    with pytest.raises(ValueError):
        NavMesh.fromPolygons([[(0, 0, 0), (1, 0, 0)]])


def vint(value):
    unsigned = abs(value) * 2 + (value < 0)
    data = bytearray()
    while unsigned > 127:
        data.append((unsigned & 127) | 128)
        unsigned >>= 7
    return bytes(data + bytes([unsigned]))


def test_signed_tag_integer_and_truncation():
    for n in [0, 1, -1, 1803, 8069, 2**31 - 1]:
        assert TagReader(vint(n)).integer() == n
    for data in [b"", b"\x80", b"\x80" * 10]:
        with pytest.raises(ValueError):
            TagReader(data).integer()


def test_bounded_zlib_entry_consumes_each_stream():
    import io
    import zlib
    raw = b"fixture" * 100
    packed = zlib.compress(raw[:300]) + zlib.compress(raw[300:])
    assert readCompressedEntry(io.BytesIO(b"prefix" + packed), 6, len(packed), len(raw)) == raw
    for bad in [packed[:-1], packed + b"trailing", zlib.compress(raw * 10)]:
        with pytest.raises(ValueError):
            readCompressedEntry(io.BytesIO(bad), 0, len(bad), len(raw))


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


def test_full_synthetic_tagfile_columnar_arrays_and_inherited_mask():
    data = syntheticTagfile()
    resource = TagReader(data).document()
    assert len(resource["faces"]) == 2
    assert resource["edges"][1]["oppositeEdge"] == 7
    path = NavMesh.fromResource(resource).findPath((1, 2, 0), (7, 2, 0))
    assert path.status == "ready" and path.faces == (0, 1)
    assert path.points == ((1, 2, 0), (4, 2, 0), (7, 2, 0))
    for bad in (data[:8], data[:-1], data + b"trailing", b"badmagic" + data[8:]):
        with pytest.raises(ValueError):
            TagReader(bad).document()


@pytest.mark.parametrize("field,value", [("oppositeFace", 99), ("oppositeEdge", 6), ("b", 99), ("b", 3)])
def test_resource_topology_rejects_broken_links(field, value):
    resource = TagReader(syntheticTagfile()).document()
    resource["edges"][1][field] = value
    with pytest.raises(ValueError):
        NavMesh.fromResource(resource)


def test_funnel_shortens_corridor_without_cutting_the_corner():
    path = mesh().findPath((1, 2, 0), (6, 7, 1.5), radius=0.35)
    assert path.status == "ready"
    assert path.points[1][0] == 4 and path.points[1][1] > 2
    assert path.points[2][1] == 4 and path.points[2][0] >= 4.35


def test_stacked_floors_do_not_connect_by_xy_overlap():
    ground = [(0, 0, 0), (4, 0, 0), (4, 4, 0), (0, 4, 0)]
    upper = [(x, y, 3) for x, y, z in ground]
    nav = NavMesh.fromPolygons([ground, upper])
    assert nav.findPath((1, 1, 0), (1, 1, 3)).status == "no-path"


def test_ambiguous_funnel_keeps_connected_portals_instead_of_crossing_wall(monkeypatch):
    from Business.scripts import navigation
    monkeypatch.setattr(navigation, "pullString", lambda portals: [(portals[0][0], 0), (portals[-1][0], len(portals) - 1)])
    nav = mesh()
    path = nav.findPath((1, 2, 0), (6, 7, 1.5))
    assert path.status == "ready" and len(path.points) > 2
    for a, b in zip(path.points, path.points[1:]):
        assert all(nav.locate(tuple(x + (y - x) * i / 20 for x, y in zip(a, b))) is not None for i in range(21))


def test_nearly_collinear_face_boundary_is_not_a_geometry_exception():
    nav = NavMesh.fromPolygons([[(0, 0, 0), (1, 0, 0), (1, .000006, 0), (0, .000006, 0)]])
    assert nav.locate((.5, .000003, 0)) is None


@pytest.fixture
def clientNavigation(tmp_path, monkeypatch):
    import hashlib
    import zlib
    from Business.scripts import navmesh_resource as resource
    data = syntheticTagfile()
    packed = zlib.compress(data)
    directory = tmp_path / "Game" / "Bin"
    directory.mkdir(parents=True)
    (directory.parent / "vfs").mkdir()
    archive = directory.parent / "vfs/map.vfs"
    archive.write_bytes(packed)
    for name, value in {"NAV_OFFSET": 0, "NAV_SIZE": len(data), "NAV_STORED_SIZE": len(packed),
                        "NAV_HASH": hashlib.sha256(data).hexdigest()}.items():
        monkeypatch.setattr(resource, name, value)
    resource.loadNavigation.cache_clear()
    try:
        yield directory, archive, packed
    finally:
        resource.loadNavigation.cache_clear()


def test_resource_reader_never_writes_selected_client(clientNavigation, monkeypatch):
    from Business.scripts import navmesh_resource as resource
    directory, archive, packed = clientNavigation
    try:
        assert resource.loadNavigation(str(directory)).findPath((1, 1, 0), (7, 1, 0)).status == "ready"
        assert archive.read_bytes() == packed
        resource.loadNavigation.cache_clear()
        monkeypatch.setattr(resource, "NAV_HASH", "0" * 64)
        with pytest.raises(ValueError, match="supported client"):
            resource.loadNavigation(str(directory))
    finally:
        resource.loadNavigation.cache_clear()


def test_bot_loads_navigation_from_executable_directory_and_starts_moving(clientNavigation):
    from Business.scripts import bot
    from test_bot_player import flowAt, snapshot, tick
    directory, _, _ = clientNavigation
    flow = flowAt()
    flow.context["clientDirectory"] = str(directory)
    flow.session["bot"]["position"] = [1., 1., 0.]
    snapshot(flow, player=(7., 1., 0.))
    tick(flow)
    assert flow.session["bot"]["status"] == "approach"
    flow.now += bot.MOVE_MS
    tick(flow, bot.MOVE_TIMER)
    assert flow.session["bot"]["position"][0] > 1
    assert flow.result["send"][-1]["reason"] == "bot-move"
