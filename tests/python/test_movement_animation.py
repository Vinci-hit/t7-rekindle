import math

import pytest

from Business.scripts import movement_animation as resources
from movement_animation_fixture import PROPERTIES, VALUES, character, profile
from vfs_fixture import archiveBytes


def test_reference_bound_character_properties_supply_animation_speeds():
    expected = {key: value for key, value in profile().items() if key != 'weaponId'}
    assert resources.characterSpeeds(character()) == expected
    values = tuple(reversed(VALUES))
    names = tuple(reversed(PROPERTIES))
    assert resources.characterSpeeds(character(values, names)) == expected


@pytest.mark.parametrize('data', [b'bad', character()[:-3], character(references=(0, 4)),
    character(names=PROPERTIES[:-1]), character(names=PROPERTIES[:-1] + (PROPERTIES[0],)),
    character(types=[5, 4, 4, 4]), character(values=(0, 1, 1, 4)),
    character(values=(-1, 1, 1, 4)), character(values=(math.nan, 1, 1, 4)),
    character(values=(math.inf, 1, 1, 4)), character(values=(4, 1, 1, 2))])
def test_invalid_character_properties_are_rejected(data):
    with pytest.raises(ValueError):
        resources.characterSpeeds(data)


def test_selected_resource_is_read_only_and_unknown_bindings_do_not_borrow_it(tmp_path):
    directory = tmp_path / 'Bin'
    directory.mkdir()
    archive = tmp_path / 'vfs/actor.vfs'
    archive.parent.mkdir()
    path = resources.CHARACTERS[(110001, 1030411)]
    data = archiveBytes([('../' + path, character(), 3)])
    archive.write_bytes(data)
    result, diagnostics = resources.loadMovementAnimation(str(directory), 110001, 1030411)
    assert result == profile() and not diagnostics
    result['forwardSlow'] = 99
    assert resources.loadMovementAnimation(str(directory), 110001, 1030411)[0] == profile()
    assert archive.read_bytes() == data
    for hero, weapon in ((110003, 1030411), (110001, 200)):
        result, diagnostics = resources.loadMovementAnimation(str(directory), hero, weapon)
        assert not result['enabled'] and diagnostics


def test_missing_or_loose_character_retains_native_selection_with_diagnostic(tmp_path):
    directory = tmp_path / 'Bin'
    result, diagnostics = resources.loadMovementAnimation(str(directory), 110001, 1030411)
    assert not result['enabled'] and diagnostics
    loose = tmp_path / resources.CHARACTERS[(110001, 1030411)]
    loose.parent.mkdir(parents=True)
    loose.write_bytes(character())
    result, diagnostics = resources.loadMovementAnimation(str(directory), 110001, 1030411)
    assert not result['enabled'] and 'unsupported_overlay' in diagnostics[0]['message']
