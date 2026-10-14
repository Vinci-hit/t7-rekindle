import struct

import pytest

from Business.scripts.hero_resources import parseCatalogue
from loadout_fixture import resourceTables


def test_catalogue_uses_enabled_unlock_graph_not_npc_category_alone():
    value = parseCatalogue(*resourceTables())
    assert tuple(value.heroes) == (1101,)
    assert value.heroes[1101].weapon(0, 0) == 701
    assert value.heroes[1101].defaults == ((0, 0),)
    assert value.heroes[1101].groupIds == (900, 0, 0, 0, 0, 0, 0, 0)
    assert value.reference(1101, 1) == 1101
    with pytest.raises(ValueError):
        value.reference(1101, 2)


@pytest.mark.parametrize('group,index', [(8, 0), (-1, 0), (0, 6), (0, True), (1, 0)])
def test_invalid_weapon_reference(group, index):
    with pytest.raises(ValueError):
        parseCatalogue(*resourceTables()).heroes[1101].weapon(group, index)


def test_missing_group_fails_instead_of_substituting_default_weapon():
    data = list(resourceTables())
    hero = bytearray(data[0])
    struct.pack_into('<I', hero, 140 + 4211, 999)
    data[0] = bytes(hero)
    with pytest.raises(ValueError, match='missing weapon group'):
        parseCatalogue(*data)


def test_unsupported_weapons_stay_in_locked_native_grid():
    from Business.scripts import player_profile
    from Business.scripts.codec import loadout_flow
    from loadout_fixture import table
    data = list(resourceTables())
    groups, weapon = bytearray(data[2]), bytearray(data[3][140:140+2829])
    struct.pack_into('<I', groups, 140 + 66 + 228, 799)
    struct.pack_into('<I', weapon, 0, 799)
    struct.pack_into('<H', weapon, 56, 77)
    category = bytearray(550)
    struct.pack_into('<HH', category, 0, 77, 7)
    struct.pack_into('<H', category, 46, 3)
    data[2] = bytes(groups)
    data[3] = table(2829, [data[3][140:140+2829], data[3][140+2829:], weapon])
    data[4] = table(550, [data[4][140:690], data[4][690:], category])
    cat = parseCatalogue(*data)
    assert cat.heroes[1101].nativeGroups[0][:2] == (701, 799)
    assert cat.heroes[1101].groups[0][:2] == (701, 0)
    card = loadout_flow.heroCard(player_profile.create(cat), cat, 1101)
    assert struct.unpack_from('>bi', card, 51+106) == (2, 799)
