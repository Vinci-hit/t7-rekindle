from copy import deepcopy
import struct

import pytest

from Business.scripts import actors, app, bot, bot_names, roster
from test_bot_lifecycle import members
from test_bot_player import flowAt


PREFIXES = {'aggressive': '[进攻]', 'balanced': '[全能]', 'defensive': '[防守]', 'berserker': '[疯狗]'}


@pytest.mark.parametrize('camp', [1, 2])
def test_roster_uses_profile_prefixes_in_both_teams_and_all_name_packets(spawnPoints, camp):
    flow = flowAt()
    flow.session['camp'] = camp
    playerName = flow.playerName
    roster.assemble(flow)
    baseNames = []
    for actorId in range(2, 21):
        value = actors.data(flow, actorId)
        prefix = PREFIXES[value['profile']]
        assert value['name'].startswith(prefix + '测试电脑')
        assert value['name'].count(prefix) == 1
        baseNames.append(value['name'][len(prefix):])
        encoded = value['name'].encode('gbk')
        assert len(encoded) <= 31
        assert struct.pack('>I', len(encoded) + 1) + encoded + b'\0' in bot.vision(value, actorId)
        bot.importMember(flow, actorId)
    assert len(set(baseNames)) == 19
    assert flow.playerName == playerName
    assert all(members(flow.result['send'])[actorId][1] == actors.data(flow, actorId)['name']
               for actorId in range(2, 21))


@pytest.mark.parametrize('prefix', ['', '[进攻]', '[全能]', '[防守]', '[疯狗]'])
def test_name_assignment_excludes_plain_and_prefixed_player_names(prefix):
    assert bot_names.assignNames(('青松', '长风'), 1, 42, prefix + '青松') == ['长风']


def test_name_assignment_counts_prefix_bytes_and_skips_long_names_without_truncating():
    accepted = ('A' * 25, '界' * 12 + 'A')
    rejected = ('B' * 26, '界' * 13)
    names = accepted + rejected
    selected = bot_names.assignNames(names, 2, 42, '')
    assert set(selected) == set(accepted)
    assert selected == bot_names.assignNames(names, 2, 42, '')
    assert all(len((prefix + name).encode('gbk')) == 31 for prefix in PREFIXES.values() for name in selected)
    with pytest.raises(ValueError, match='not enough'):
        bot_names.assignNames(names, 3, 42, '')


@pytest.mark.parametrize('prefixed', [False, True])
def test_reload_preserves_existing_names_and_profiles_without_adding_prefixes(spawnPoints, prefixed):
    flow = flowAt()
    roster.assemble(flow)
    if not prefixed:
        for index, value in enumerate(actors.bots(flow.session).values()):
            value.update(name=f'测试电脑{index:02}', profile='balanced')
    previous = deepcopy(flow.state)
    assert app.migrateState(app.STATE_VERSION, flow.state) == previous
    assert flow.state == previous and app.validateState(previous)
