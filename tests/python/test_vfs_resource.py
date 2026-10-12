import io
import zlib

import pytest

from Business.scripts import vfs_resource as vfs
from vfs_fixture import HEADER, archiveBytes, encodeRecord

PATH = '../data/scene/测试.bin'


@pytest.fixture(autouse=True)
def clearIndex():
    vfs.clearCache()
    yield
    vfs.clearCache()


@pytest.mark.parametrize('flags', [1, 3])
@pytest.mark.parametrize('padding,level', [(0, 1), (137, 9)])
def test_index_resolves_repacked_entries_read_only(tmp_path, flags, padding, level):
    raw = b'fixture' * 9000
    contents = archiveBytes([('../data/other.bin', b'other', 3), (PATH, raw, flags)],
                            padding=padding, level=level, inactive=True)
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(contents)
    assert vfs.readEntry(archive, 'DATA\\SCENE\\测试.BIN') == raw
    assert vfs.readEntry(archive, PATH) == raw
    assert archive.read_bytes() == contents


@pytest.mark.parametrize('field,value', [(0, 2), (1, 1), (1, 0xFFFFFFFE), (2, 99999),
                                       (3, 0), (7, 2), (8, 1), (9, 1), (10, 1)])
def test_invalid_directory_records_include_details(tmp_path, field, value):
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(archiveBytes([(PATH, b'content', 3)], change=lambda row: row.__setitem__(field, value)))
    with pytest.raises(vfs.ResourceError, match=r'\[T7_RESOURCE:directory_invalid\].*map.vfs'):
        vfs.readEntry(archive, PATH)


@pytest.mark.parametrize('contents', [b'', HEADER[:100], HEADER + b'garbage',
                                    archiveBytes([(PATH, b'content', 3)])[:-1]])
def test_truncated_or_missing_directory(tmp_path, contents):
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(contents)
    with pytest.raises(vfs.ResourceError):
        vfs.readEntry(archive, PATH)


def test_missing_entry_and_archive_are_distinct(tmp_path):
    archive = tmp_path / 'map.vfs'
    with pytest.raises(vfs.ResourceError, match='archive_read'):
        vfs.readEntry(archive, PATH)
    archive.write_bytes(archiveBytes([(PATH, b'content', 1)]))
    with pytest.raises(vfs.ResourceError, match='entry_missing.*data/missing'):
        vfs.readEntry(archive, 'data/missing')


@pytest.mark.parametrize('path', ['../data/scene/测试.BIN', '../data/scene/../测试.bin'])
def test_duplicate_or_traversing_paths_rejected(tmp_path, path):
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(archiveBytes([(PATH, b'a', 1), (path, b'b', 1)]))
    with pytest.raises(vfs.ResourceError, match='directory_invalid'):
        vfs.readEntry(archive, PATH)


def test_corrupt_zlib_and_target_size_limit(tmp_path, monkeypatch):
    archive = tmp_path / 'map.vfs'
    data = bytearray(archiveBytes([(PATH, b'content', 3)]))
    data[2048] = 0
    archive.write_bytes(data)
    with pytest.raises(vfs.ResourceError, match='entry_decode.*测试.bin'):
        vfs.readEntry(archive, PATH)
    vfs.clearCache()
    archive.write_bytes(archiveBytes([(PATH, b'content', 1), ('../data/large', bytes(50), 1)]))
    monkeypatch.setattr(vfs, 'MAX_ENTRY_BYTES', 10)
    assert vfs.readEntry(archive, PATH) == b'content'
    with pytest.raises(vfs.ResourceError, match='entry_size'):
        vfs.readEntry(archive, 'data/large')


def test_search_is_bounded_and_cache_can_be_refreshed(tmp_path, monkeypatch):
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(archiveBytes([(PATH, b'content', 1)]))
    assert vfs.readEntry(archive, PATH) == b'content'
    monkeypatch.setattr(vfs, 'MAX_TAIL_BYTES', 44)
    vfs.clearCache()
    with pytest.raises(vfs.ResourceError, match='directory_invalid'):
        vfs.readEntry(archive, PATH)


def test_raw_extent_and_overlapping_records_rejected(tmp_path):
    archive = tmp_path / 'map.vfs'
    for field, value in [(2, 2), (7, 1), (8, 1)]:
        archive.write_bytes(archiveBytes([(PATH, b'abc', 1)], change=lambda row: row.__setitem__(field, value)))
        with pytest.raises(vfs.ResourceError):
            vfs.readEntry(archive, PATH)
    archive.write_bytes(archiveBytes([(PATH, b'abc', 1), ('../data/second', b'def', 1)],
                                    change=lambda row: row.__setitem__(1, 2048)))
    with pytest.raises(vfs.ResourceError, match='directory_invalid'):
        vfs.readEntry(archive, PATH)


def test_false_path_before_directory_does_not_hide_valid_suffix(tmp_path):
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(archiveBytes([(PATH, b'../data/false\0', 1)]))
    assert vfs.readEntry(archive, PATH) == b'../data/false\0'


def test_ambiguous_validated_candidates_are_rejected(tmp_path, monkeypatch):
    archive = tmp_path / 'map.vfs'
    archive.write_bytes(archiveBytes([(PATH, b'content', 1)]))
    # Isolate the candidate-selection contract from record decoding.
    monkeypatch.setattr(vfs, 'nameRuns', lambda tail: iter([(0, 5), (5, 10)]))
    monkeypatch.setattr(vfs, 'parseDirectory', lambda *args: {PATH: object()})
    with pytest.raises(vfs.ResourceError, match='directory_ambiguous'):
        vfs.readEntry(archive, PATH)


def test_bounded_zlib_entry_consumes_each_stream():
    raw = b'fixture' * 100
    packed = zlib.compress(raw[:300]) + zlib.compress(raw[300:])
    assert vfs.readCompressedEntry(io.BytesIO(b'prefix' + packed), 6, len(packed), len(raw)) == raw
    for bad in [packed[:-1], packed + b'trailing', zlib.compress(raw * 10)]:
        with pytest.raises(ValueError):
            vfs.readCompressedEntry(io.BytesIO(bad), 0, len(bad), len(raw))


def test_actual_chunk_count_is_checked(tmp_path):
    archive = tmp_path / 'map.vfs'
    raw = b'fixture' * 100
    packed = zlib.compress(raw[:300]) + zlib.compress(raw[300:])
    name = PATH.encode('gbk')
    record = encodeRecord([3, 2048, len(packed), len(raw), 0, 0, 0, 1, 0, len(name), 0])
    archive.write_bytes(HEADER + packed + name + b'\0' + record)
    with pytest.raises(vfs.ResourceError, match='entry_decode.*chunk count'):
        vfs.readEntry(archive, PATH)


def test_truncated_cached_entry_and_invalid_gbk_path(tmp_path):
    archive = tmp_path / 'map.vfs'
    data = archiveBytes([(PATH, b'content', 1)])
    archive.write_bytes(data)
    assert vfs.readEntry(archive, PATH) == b'content'
    archive.write_bytes(HEADER + b'x')
    with pytest.raises(vfs.ResourceError, match='entry_decode.*truncated raw entry'):
        vfs.readEntry(archive, PATH)
    vfs.clearCache()
    archive.write_bytes(data.replace('测试'.encode('gbk'), b'\xff\xff\xff\xff'))
    with pytest.raises(vfs.ResourceError, match='directory_invalid'):
        vfs.readEntry(archive, PATH)
