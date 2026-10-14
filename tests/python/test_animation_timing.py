import struct

import pytest

from Business.scripts.animation_resource import ClipReader as AnimationReader, animationDurationMs
from Business.scripts import animation_timing as timing
from dash_fixture import animation, tagInteger


def test_bulk_vectors_read_encoded_width_and_restore_omitted_components():
    reader = AnimationReader(animation())
    reader.data, reader.position = tagInteger(3) + struct.pack('<6f', 1, 2, 3, 4, 5, 6), 0
    assert reader.values(4, None, None, 2, True) == [(1., 2., 3., 0.), (4., 5., 6., 0.)]
    assert reader.position == len(reader.data)


def test_fixed_vectors_have_bulk_encoding_too():
    reader = AnimationReader(animation())
    reader.data, reader.position = tagInteger(2) + struct.pack('<2f', 1, 2), 0
    assert reader.values(4 | 32, 1, None, 1) == [[(1., 2., 0., 0.)]]


@pytest.mark.parametrize('width', [0, 5])
def test_invalid_bulk_vector_width_is_rejected(width):
    reader = AnimationReader(animation())
    reader.data, reader.position = tagInteger(width), 0
    with pytest.raises(ValueError, match='vector width'):
        reader.values(4, None, None, 1, True)


def test_complete_object_stream_retains_reference_indices():
    objects = AnimationReader(animation(.6)).objects()
    assert objects[0] is None
    assert objects[1]['className'] == 'hkaSplineCompressedAnimation'
    assert animationDurationMs(animation(.6)) == 600


def test_crop_and_playback_speed_define_duration_not_enforced_server_clock():
    assert timing.effectiveDuration(1.2, dict(cropStartAmountLocalTime=.1,
        cropEndAmountLocalTime=.3, playbackSpeed=2., enforcedDuration=99.)) == 400


@pytest.mark.parametrize('generator', [dict(mode=1), dict(playbackSpeed=0), dict(playbackSpeed=-1),
    dict(cropEndAmountLocalTime=2), dict(cropStartAmountLocalTime=-1), dict(playbackSpeed=float('nan'))])
def test_looping_or_invalid_clip_is_not_a_finite_state_duration(generator):
    with pytest.raises(ValueError):
        timing.effectiveDuration(1., generator)


def resolver(monkeypatch):
    result = timing.AnimationTimings.__new__(timing.AnimationTimings)
    result.directory, result.error = 'fixture', None
    result.aliases = {'A': 'a.hkt', 'B': 'b.hkt'}
    result.generators = {'A': [{}], 'B': [{}]}
    monkeypatch.setattr(timing, 'clipSeconds', lambda _, path: {'a.hkt': .6, 'b.hkt': .7}[path])
    return result


def test_animation_wins_over_valid_table_and_fallback_retains_reason(monkeypatch):
    result = resolver(monkeypatch)
    duration, source = result.select(950, ['A'])
    assert duration == 600 and source['tableMs'] == 950 and source['source'] == 'animation'
    duration, source = result.select(950, ['Unknown'])
    assert duration == 950 and source['reason'] == 'missing_animation_binding'


def test_different_branch_durations_and_dynamic_timing_fall_back(monkeypatch):
    result = resolver(monkeypatch)
    assert result.select(550, ['A', 'B'])[1]['reason'] == 'ambiguous_animation_duration'
    result.generators['A'][0]['unresolvedTiming'] = True
    assert result.select(550, ['A'])[1]['reason'] == 'dynamic_clip_timing'


def test_equal_branch_durations_can_use_animation(monkeypatch):
    result = resolver(monkeypatch)
    result.generators['B'][0]['cropEndAmountLocalTime'] = .1
    assert result.select(550, ['A', 'B'])[0] == 600


def test_missing_and_invalid_animation_data_falls_back(monkeypatch):
    result = resolver(monkeypatch)
    def missing(*_):
        raise timing.ResourceError('entry_missing', 'actor.vfs', 'a.hkt', 'fixture')
    monkeypatch.setattr(timing, 'clipSeconds', missing)
    assert result.select(550, ['A']) == (550, dict(tableMs=550, source='table', reason='entry_missing'))


@pytest.mark.parametrize('member,variable,expected', [
    ('enforcedDuration', 'BattleStateDuration', 400),
    ('enforcedDuration', 'UnresolvedDuration', 950),
    ('playbackSpeed', 'UnresolvedSpeed', 950),
])
def test_character_alias_binding_skips_empty_slots_and_checks_graph_clocks(monkeypatch, member, variable, expected):
    character = [None, dict(className='hkbCharacterStringData',
        animationBundleNameData=[dict(assetNames=['Unused', 'AttackUpProcess', 'Unbound'])],
        animationBundleFilenameData=[dict(assetNames=['', 'fixture.hkt'])],
        behaviorFilename='..\\Behaviors\\fixture.hkt')]
    graph = [None, dict(className='hkbBehaviorGraphStringData', variableNames=[variable]),
        dict(className='hkbVariableBindingSet', bindings=[dict(memberPath=member, variableIndex=0)]),
        dict(className='hkbClipGenerator', animationName='AttackUpProcess', variableBindingSet=2,
             cropEndAmountLocalTime=.2)]
    def read(directory, archive, path, parser):
        assert directory == 'fixture' and archive == 'actor.vfs'
        return graph if path.endswith('/behaviors/fixture.hkt') else character
    monkeypatch.setattr(timing, 'readSelected', read)
    monkeypatch.setattr(timing, 'clipSeconds', lambda *_: .6)
    result = timing.AnimationTimings('fixture', 'data/characters/fixture.hkt')
    assert result.error is None and set(result.aliases) == {'AttackUpProcess'}
    assert result.select(950, ['AttackUpProcess'])[0] == expected


def test_broken_character_binding_has_explicit_table_fallback(monkeypatch):
    monkeypatch.setattr(timing, 'readSelected', lambda *_: [None])
    result = timing.AnimationTimings('fixture', 'data/characters/fixture.hkt')
    assert result.select(550, ['Hurt']) == (550, dict(
        tableMs=550, source='table', reason='invalid_character_binding'))
