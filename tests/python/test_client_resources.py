import importlib
from pathlib import Path

import pytest

from Business.runtime.host_runtime import Runtime
from Business.scripts import bot_names, client_resources, navmesh_resource, spawn_resource, vfs_resource
from vfs_fixture import NAME_PATH, NAV_PATH, SPAWN_PATH, archiveBytes, nameTable, writeClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    directory, digest = writeClient(tmp_path / 'client')
    monkeypatch.setattr(navmesh_resource, 'NAV_HASH', digest)
    yield directory
    for loader in (bot_names.loadNames, navmesh_resource.loadNavigation,
                   spawn_resource.loadSpawns, spawn_resource.loadRespawns):
        loader.cache_clear()
    vfs_resource.clearCache()


def test_preflight_repacked_resources_are_read_only(client):
    root = client.parent
    files = list((root / 'vfs').iterdir())
    before = {path: path.read_bytes() for path in files}
    client_resources.checkResources(str(client))
    assert before == {path: path.read_bytes() for path in files}
    writeClient(root, padding=197, level=1)
    client_resources.checkResources(str(client))
    assert len(bot_names.loadNames(str(client))) == 30


@pytest.mark.parametrize('overlay', ['vfs/MapPatch.vfs', 'vfs/extra.vfs', 'vfs/MapPatch2Remove.txt',
                                   'Data/Config/VFSConfig.cfg', SPAWN_PATH[3:], NAV_PATH[3:], NAME_PATH[3:]])
def test_actual_overlays_are_rejected(client, overlay):
    path = client.parent / overlay
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('fixture', encoding='utf-8')
    with pytest.raises(vfs_resource.ResourceError, match='unsupported_overlay.*覆盖布局尚未支持'):
        client_resources.checkResources(str(client))


def test_manifest_references_and_version_are_not_compatibility_decisions(client):
    client_resources.checkResources(str(client))  # No update.lst is required.
    (client.parent / 'update.lst').write_text('v1.2.3.4Beta\nvfs/MapPatch.vfs\nvfs/MapPatch2Remove.txt\n', encoding='utf-8')
    client_resources.checkResources(str(client))
    (client.parent / 'vfs/map.vfs').write_bytes(b'bad archive, same client version')
    with pytest.raises(vfs_resource.ResourceError, match='directory_invalid'):
        client_resources.checkResources(str(client))


@pytest.mark.parametrize('entry,archive', [(NAV_PATH, 'map.vfs'), (SPAWN_PATH, 'map.vfs'), (NAME_PATH, 'data1.vfs')])
def test_content_validation_retained(client, entry, archive):
    (client.parent / 'vfs' / archive).write_bytes(archiveBytes([(entry, b'unsupported content', 3)]))
    loader = {NAV_PATH: navmesh_resource.loadNavigation, SPAWN_PATH: spawn_resource.loadSpawns,
              NAME_PATH: bot_names.loadNames}[entry]
    loader.cache_clear()
    with pytest.raises(vfs_resource.ResourceError, match='content_mismatch') as error:
        loader(str(client))
    assert error.value.__cause__ is not None
    assert entry[3:] in str(error.value)


def test_recheck_drops_cached_success_and_can_retry(client):
    client_resources.checkResources(str(client))
    archive = client.parent / 'vfs/data1.vfs'
    names = tuple(f'新名字{i}' for i in range(30))
    archive.write_bytes(archiveBytes([(NAME_PATH, nameTable([('npc', name) for name in names]), 1)], padding=10))
    client_resources.checkResources(str(client))
    assert bot_names.loadNames(str(client)) == names
    archive.unlink()
    with pytest.raises(vfs_resource.ResourceError, match='archive_read'):
        client_resources.checkResources(str(client))
    writeClient(client.parent)
    client_resources.checkResources(str(client))


def test_revision_preflight_uses_same_resource_loaders_and_retires_cache(client, tmp_path):
    business = Path(__file__).resolve().parents[2] / 'src/Business'
    runtime = Runtime(business / 'scripts', tmp_path / 'revisions')
    revision = runtime.active.path
    try:
        nav = importlib.import_module(runtime.active.name + '.navmesh_resource')
        nav.NAV_HASH = navmesh_resource.NAV_HASH
        runtime.checkResources(str(client))
        assert nav.loadNavigation.cache_info().currsize == 1
        (client.parent / 'vfs/map.vfs').unlink()
        with pytest.raises(ValueError, match='archive_read'):
            runtime.checkResources(str(client))
    finally:
        runtime.close()
    assert not revision.exists()


def test_relative_directory_is_rejected():
    with pytest.raises(vfs_resource.ResourceError, match='absolute Bin'):
        client_resources.checkResources('relative/Bin')


@pytest.mark.parametrize('names,player', [(['测试名字'], ''),
                                       ([f'测试名字{i}' for i in range(19)], '测试名字0'),
                                       (['名字超出加前缀后上限的长度测试' + str(i) for i in range(19)], '')])
def test_unusable_name_pool_is_rejected_before_launch(client, names, player):
    (client.parent / 'vfs/data1.vfs').write_bytes(archiveBytes([
        (NAME_PATH, nameTable([('npc', name) for name in names]), 3)]))
    with pytest.raises(vfs_resource.ResourceError, match='content_mismatch.*sh_res_ai_name_cli.bin'):
        client_resources.checkResources(str(client), player)
