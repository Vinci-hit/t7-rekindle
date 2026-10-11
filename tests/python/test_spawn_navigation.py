import math

import pytest

from Business.scripts import actors, bot, spawn_navigation
from Business.scripts.navigation import NavMesh
from test_bot_respawn import kill, respawnTimer, tick
from test_bot_roster import multi, sample


def square(x, y, z=0, size=10):
    return [(x, y, z), (x + size, y, z), (x + size, y + size, z), (x, y + size, z)]


def candidates(nav, position, anchor=(2, 2, .2)):
    return spawn_navigation.candidates(nav, ((tuple(position), 90),), tuple(anchor))[0]


def test_even_on_mesh_reference_is_replaced_with_a_different_ground_point():
    nav = NavMesh.fromPolygons([square(0, 0)])
    proposals = candidates(nav, (1, 1, .3))
    assert proposals and all(point[:2] != (1, 1) for point, _ in proposals)
    assert all(point[2] == pytest.approx(.2) for point, _ in proposals)


def test_off_mesh_reference_has_nearby_interior_battlefield_spawn():
    nav = NavMesh.fromPolygons([square(0, 0)])
    proposals = candidates(nav, (-2, 4, .2))
    assert proposals
    point, heading = proposals[0]
    assert point == pytest.approx((.351, 4, .2)) and heading == 90
    assert nav.findPath(point, (2, 2, .2)).status == 'ready'


def test_projection_does_not_choose_disconnected_island_or_other_floor():
    nav = NavMesh.fromPolygons([square(0, 0), square(-5, 0, size=2), square(-6, 0, z=-5)])
    proposals = candidates(nav, (-4, 4, .2))
    assert proposals and all(nav.locate(point) == 0 for point, _ in proposals)
    assert all(point[2] == pytest.approx(.2) for point, _ in proposals)


@pytest.mark.parametrize('point', [(-40, 4, .2), (-32, 4, .2)])
def test_no_spawn_outside_the_strict_horizontal_radius(point):
    assert not candidates(NavMesh.fromPolygons([square(0, 0)]), point)


@pytest.mark.parametrize('z', [5.2, 50.2, -50.2])
def test_spawn_uses_ground_height_not_the_reference_height(z):
    nav = NavMesh.fromPolygons([square(0, 0)])
    origin = (-2, 4, z)
    proposals = candidates(nav, origin)
    assert proposals and proposals[0][0] == pytest.approx((.351, 4, .2))
    assert origin == (-2, 4, z)


def test_horizontal_radius_includes_the_boundary_but_not_extra_centimeters():
    nav = NavMesh.fromPolygons([square(0, 0)])
    assert candidates(nav, (spawn_navigation.MARGIN - 32, 4, 20.2))
    assert not candidates(nav, (spawn_navigation.MARGIN - 32.01, 4, 20.2))


def test_large_face_has_spawns_near_reference_even_when_its_center_is_far_away():
    nav = NavMesh.fromPolygons([square(-100, -100, size=200)])
    origin = (10, 60, .2)
    proposals = candidates(nav, origin)
    assert len(proposals) >= 4
    assert all(0 < math.dist(point[:2], origin[:2]) <= 32 for point, _ in proposals)


def test_spawn_region_can_reach_main_ground_beyond_fifteen_meters():
    nav = NavMesh.fromPolygons([square(0, 0)])
    proposals = candidates(nav, (-30, 4, .2))
    assert proposals and proposals[0][0] == pytest.approx((.351, 4, .2))
    assert nav.findPath(proposals[0][0], (2, 2, .2)).status == 'ready'


def test_narrow_portal_does_not_make_spawn_reachable():
    nav = NavMesh([square(0, 0), square(-4, 0, size=2)],
                  [[(1, (0, 0, 0), (0, .5, 0))], [(0, (0, .5, 0), (0, 0, 0))]])
    assert all(nav.locate(point) == 0 for point, _ in candidates(nav, (-3, 1, .2)))


def test_missing_anchor_or_too_thin_surface_has_no_candidate():
    nav = NavMesh.fromPolygons([square(0, 0)])
    assert not candidates(nav, (-2, 2, .2), anchor=(30, 30, .2))
    thin = NavMesh.fromPolygons([[(0, 0, 0), (.4, 0, 0), (.4, 10, 0), (0, 10, 0)]])
    assert not candidates(thin, (-2, 2, .2), anchor=(.2, 5, .2))


def test_battlefield_respawns_replace_platform_points_and_resume_ai(multi, monkeypatch):
    nav = NavMesh.fromPolygons([square(0, 0, size=60)])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    for index, point in enumerate(multi.session['respawnPoints']['2']):
        point[0] = [-2., 5. + index * 2, .2]
    kill(multi, 2)
    kill(multi, 3)
    due = multi.now + 5000
    tick(multi, respawnTimer(2), due)
    tick(multi, respawnTimer(3), due)
    first, second = (actors.data(multi, rid) for rid in (2, 3))
    originals = [point[0] for point in multi.session['respawnPoints']['2']]
    assert first['position'] not in originals and second['position'] not in originals
    assert nav.project(first['position']) is not None
    assert math.dist(first['position'], second['position']) >= .701
    assert all('spawnExit' not in value for value in (first, second))
    assert all(min(math.dist(value['position'][:2], point[:2]) for point in originals) <= 32
               for value in (first, second))
    multi.now = due + 100
    sample(multi)
    bot.think(multi)
    assert first['initialized'] and 'life' not in first
    assert first['status'] in ('advance', 'patrol', 'approach', 'ready', 'turning')
    assert not any(packet['reason'].startswith('bot-spawn-exit-') for packet in multi.result['send'])


def test_missing_battlefield_spawn_retries_without_using_original_or_out_of_range_points(multi, monkeypatch):
    nav = NavMesh.fromPolygons([square(0, 0)])
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: nav)
    kill(multi)
    multi.result['send'].clear()
    due = multi.now + 5000
    tick(multi, respawnTimer(2), due)
    value = actors.data(multi, 2)
    assert value['health'] == 0 and value['life']['state'] == 'dead'
    assert not multi.result['send']
    assert value['status'] == 'respawn-no-battlefield-point'
    assert multi.session['pending'][respawnTimer(2)] == due + 50


def test_other_map_keeps_its_existing_respawn_rule(multi, monkeypatch):
    multi.session['resourceId'] = 9999
    monkeypatch.setattr(bot, 'navigationFor', lambda flow: pytest.fail('unrelated map queried Luoyang navigation'))
    kill(multi)
    tick(multi, respawnTimer(2), multi.now + 5000)
    value = actors.data(multi, 2)
    assert value['position'] in [point[0] for point in multi.session['respawnPoints']['2']]
    assert 'spawnExit' not in value
