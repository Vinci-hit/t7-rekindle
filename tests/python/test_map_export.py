"""Offline map exports retain topology and placements without client writes."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from Business.scripts import map_data, map_navigation_data, navmesh_resource, spawn_resource, vfs_resource
from Business.scripts.navigation import NavMesh
from vfs_fixture import NAV_PATH, SPAWN_PATH, archiveBytes, spawnXml, writeClient

spec = importlib.util.spec_from_file_location('export_map_config',
    Path(__file__).resolve().parents[2] / 'scripts/export_map_config.py')
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


@pytest.fixture
def client(tmp_path, monkeypatch):
    directory, digest = writeClient(tmp_path / 'client')
    monkeypatch.setattr(navmesh_resource, 'NAV_HASH', digest)
    yield directory
    for loader in (navmesh_resource.loadNavigation, spawn_resource.loadSpawns, spawn_resource.loadRespawns):
        loader.cache_clear()
    vfs_resource.clearCache()


def test_offline_export_is_read_only_and_static_roundtrip_preserves_geometry(client, monkeypatch):
    before = {path: path.read_bytes() for path in (client.parent / 'vfs').iterdir()}
    original = NavMesh.fromResource(navmesh_resource.readNavigationResource(str(client)))
    files = exporter.export(str(client))
    assert before == {path: path.read_bytes() for path in before}
    for name, module in [('map_data.py', map_data), ('map_navigation_data.py', map_navigation_data)]:
        namespace = {}
        assert str(client) not in files[name]
        exec(files[name], namespace)
        for key, value in namespace.items():
            if key.isupper():
                monkeypatch.setattr(module, key, value)
    monkeypatch.setattr(vfs_resource, 'readEntry', lambda *args: pytest.fail('unexpected runtime client read'))
    for loader in (navmesh_resource.loadNavigation, spawn_resource.loadSpawns, spawn_resource.loadRespawns):
        loader.cache_clear()
    nav = navmesh_resource.loadNavigation()
    for field in ('polygons', 'links', 'centers', 'cells'):
        assert getattr(nav, field) == getattr(original, field)
    assert nav.findPath((1, 2, 0), (7, 2, 0)) == original.findPath((1, 2, 0), (7, 2, 0))
    data = ET.tostring(spawnXml())
    assert spawn_resource.loadSpawns() == spawn_resource.parseSpawns(data)
    assert spawn_resource.loadRespawns() == spawn_resource.parseRespawns(data)


@pytest.mark.parametrize('entry', [NAV_PATH, SPAWN_PATH])
def test_offline_export_validates_original_map_content(client, entry):
    (client.parent / 'vfs/map.vfs').write_bytes(archiveBytes([(entry, b'unsupported content', 3)]))
    if entry == NAV_PATH:
        read = navmesh_resource.readNavigationResource
    else:
        read = lambda directory: vfs_resource.readResource(directory, 'map.vfs', spawn_resource.SPAWN_PATH,
                                                           spawn_resource.parseSpawns)
    with pytest.raises(vfs_resource.ResourceError, match='content_mismatch') as error:
        read(str(client))
    assert entry[3:] in str(error.value) and error.value.__cause__ is not None


def test_export_command_writes_only_to_a_new_directory(client, tmp_path, monkeypatch):
    target = tmp_path / 'generated'
    monkeypatch.setattr(exporter.sys, 'argv', ['export_map_config.py', '--client-directory', str(client), '--output', str(target)])
    exporter.main()
    files = {path.name: path.read_text(encoding='utf-8') for path in target.iterdir()}
    assert files == exporter.export(str(client))
    with pytest.raises(FileExistsError):
        exporter.main()
    assert files == {path.name: path.read_text(encoding='utf-8') for path in target.iterdir()}


def test_export_command_rejects_relative_client_directory(tmp_path, monkeypatch):
    target = tmp_path / 'generated'
    monkeypatch.setattr(exporter.sys, 'argv', ['export_map_config.py', '--client-directory', 'client/Bin', '--output', str(target)])
    with pytest.raises(SystemExit) as error:
        exporter.main()
    assert error.value.code == 2 and not target.exists()
