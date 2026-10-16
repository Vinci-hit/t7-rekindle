"""Map configuration is owned by the server, not the selected client archive."""
import pytest

from Business.scripts import contracts, navmesh_resource, spawn_resource, vfs_resource


@pytest.fixture(autouse=True)
def clearMapCaches():
    loaders = (spawn_resource.loadSpawns, spawn_resource.loadRespawns, navmesh_resource.loadNavigation)
    for loader in loaders:
        loader.cache_clear()
    yield
    for loader in loaders:
        loader.cache_clear()


@pytest.fixture
def noClientReads(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail('runtime map configuration must not read client archives')
    monkeypatch.setattr(vfs_resource, 'readEntry', fail)


@pytest.mark.parametrize('directory', ['', 'missing-client/Bin'])
def test_map_loaders_work_without_client_resources(noClientReads, directory):
    spawns = spawn_resource.loadSpawns(directory)
    respawns = spawn_resource.loadRespawns(directory)
    nav = navmesh_resource.loadNavigation(directory)
    assert {camp: len(points) for camp, points in spawns.items()} == {1: 10, 2: 10}
    assert {camp: len(points) for camp, points in respawns.items()} == {1: 14, 2: 12}
    assert spawns[1][0] == ((272.451, 157.634, 0.217519), -116)
    assert spawns[2][0] == ((242.861, 216.406, 0.165875), 81)
    assert respawns[1][0] == ((282.172, 126.648, -0.439665), -116)
    assert respawns[2][0] == ((218.26, 237.99, -0.213287), 5)
    assert len(nav.polygons) == 1803
    assert sum(bool(polygon) for polygon in nav.polygons) == 1668
    assert all(nav.locate(position) is not None for points in spawns.values() for position, _ in points)


def test_map_metadata_and_topology_are_included_in_server_package():
    from Business.scripts import map_data, map_navigation_data, spawn_navigation
    assert contracts.RESOURCE_ID == map_data.RESOURCE_ID == 1028
    assert contracts.POSITION == map_data.POSITION
    assert contracts.ENEMY_POSITION == map_data.ENEMY_POSITION
    assert spawn_navigation.RESOURCE_IDS == {map_data.RESOURCE_ID}
    assert tuple(map(len, (map_navigation_data.VERTICES, map_navigation_data.EDGES,
                          map_navigation_data.FACES))) == (4100, 8069, 1803)


@pytest.mark.parametrize('camp', [1, 2])
def test_roster_snapshots_static_initial_and_respawn_placements(monkeypatch, noClientReads, camp):
    from Business.scripts import actors, roster
    from test_bot_player import flowAt
    monkeypatch.setattr(roster, 'loadNames', lambda directory: tuple(f'测试电脑{i}' for i in range(30)))
    flow = flowAt()
    flow.session['camp'] = camp
    roster.assemble(flow)
    spawns, respawns = spawn_resource.loadSpawns(), spawn_resource.loadRespawns()
    assert flow.session['resourceId'] == contracts.RESOURCE_ID
    assert (tuple(flow.session['spawnPosition']), flow.session['spawnHeading']) == spawns[camp][0]
    assert flow.session['respawnPoints'] == {
        str(team): [[list(position), heading] for position, heading in points] for team, points in respawns.items()}
    bots = actors.bots(flow.session)
    assert len(bots) == 19
    for team in (1, 2):
        expected = spawns[team][1:] if team == camp else spawns[team]
        assert {(tuple(value['position']), value['heading']) for value in bots.values() if value['camp'] == team} == set(expected)


@pytest.mark.parametrize('name,loader', [('SPAWNS', spawn_resource.loadSpawns),
                                      ('RESPAWNS', spawn_resource.loadRespawns)])
@pytest.mark.parametrize('invalid', ['camp', 'count', 'duplicate', 'position', 'heading'])
def test_invalid_static_placements_fail_without_client_fallback(monkeypatch, noClientReads, name, loader, invalid):
    from Business.scripts import map_data
    points = {camp: list(values) for camp, values in getattr(map_data, name).items()}
    if invalid == 'camp':
        points[3] = points.pop(2)
    elif invalid == 'count':
        points[1].pop()
    elif invalid == 'duplicate':
        points[1][1] = points[1][0]
    elif invalid == 'position':
        points[1][0] = ((float('nan'), 0, 0), 0)
    else:
        points[1][0] = (points[1][0][0], 181)
    monkeypatch.setattr(map_data, name, points)
    with pytest.raises(ValueError, match='spawn'):
        loader('')


@pytest.mark.parametrize('name,rows', [('VERTICES', ()), ('EDGES', ((0, 1, -1),)),
                                    ('FACES', ((-1, 3, 0),))])
def test_invalid_static_navigation_fails_without_client_fallback(monkeypatch, noClientReads, name, rows):
    from Business.scripts import map_navigation_data
    monkeypatch.setattr(map_navigation_data, name, rows)
    with pytest.raises(ValueError):
        navmesh_resource.loadNavigation('')
