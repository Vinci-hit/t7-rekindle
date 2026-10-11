import pytest

from Business.scripts import roster


@pytest.fixture
def respawnPoints(monkeypatch):
    points = {camp: tuple(((float(i * 2), float(camp * 20 + 5), .2), -90 if camp == 1 else 90)
                         for i in range(14 if camp == 1 else 12)) for camp in (1, 2)}
    monkeypatch.setattr(roster, 'loadRespawns', lambda directory: points)
    return points


@pytest.fixture
def spawnPoints(monkeypatch, respawnPoints):
    points = {camp: tuple(((float(i * 2), float(camp * 20), .2), -90 if camp == 1 else 90)
                         for i in range(10)) for camp in (1, 2)}
    monkeypatch.setattr(roster, "loadSpawns", lambda directory: points)
    monkeypatch.setattr(roster, "loadNames", lambda directory: tuple(f'测试电脑{i:02}' for i in range(30)))
    return points
