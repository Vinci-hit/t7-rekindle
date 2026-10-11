from collections import Counter
from copy import deepcopy
import struct
import zlib

import pytest

from Business.scripts import actors, app, battle, bot, bot_names, bot_profiles, roster
from test_bot_decision import duel, nearBot, observe, choose
from test_bot_player import flowAt


def nameTable(rows):
    header = bytearray(140)
    struct.pack_into('<4sIII', header, 0, b'MSES', 7, 68, len(rows))
    header[64:68] = b'GBK\0'
    struct.pack_into('<I', header, 132, 140)
    return bytes(header) + b''.join(struct.pack('<I32s32s', 0, kind.encode('ascii'), name.encode('gbk'))
                                    for kind, name in rows)


def test_name_resource_reads_only_npc_names_and_deduplicates():
    data = nameTable([('npc', '测试甲'), ('npc', '测试乙'), ('zombie', '测试怪'), ('npc', '测试甲')])
    assert bot_names.parseNames(data) == ('测试甲', '测试乙')


def test_names_load_from_selected_client_and_exclude_human_name(tmp_path, monkeypatch):
    directory = tmp_path / 'client' / 'Bin'
    directory.mkdir(parents=True)
    archive = directory.parent / 'vfs' / 'data1.vfs'
    archive.parent.mkdir()
    data = nameTable([('npc', '测试甲'), ('npc', '测试乙'), ('npc', '测试丙')])
    compressed = zlib.compress(data)
    archive.write_bytes(b'padding' + compressed)
    for key, value in [('NAME_OFFSET', 7), ('NAME_STORED_SIZE', len(compressed)), ('NAME_SIZE', len(data))]:
        monkeypatch.setattr(bot_names, key, value)
    bot_names.loadNames.cache_clear()
    names = bot_names.loadNames(str(directory))
    assert set(bot_names.assignNames(names, 2, 42, '测试甲')) == {'测试乙', '测试丙'}
    assert bot_names.assignNames(names, 2, 42, '') == bot_names.assignNames(names, 2, 42, '')
    with pytest.raises(ValueError, match='not enough'):
        bot_names.assignNames(names, 3, 42, '测试甲')
    bot_names.loadNames.cache_clear()


@pytest.mark.parametrize('fault', ['magic', 'version', 'width', 'count', 'encoding', 'offset', 'tail', 'control', 'gbk'])
def test_name_resource_rejects_invalid_data(fault):
    data = bytearray(nameTable([('npc', '测试甲')]))
    if fault == 'magic': data[0] = 0
    if fault == 'version': data[4] = 1
    if fault == 'width': data[8] = 67
    if fault == 'count': data[12] = 2
    if fault == 'encoding': data[64] = 0
    if fault == 'offset': data[132] = 0
    if fault == 'tail': data.append(0)
    if fault == 'control': data[176] = 1
    if fault == 'gbk': data[176:208] = b'\xff' * 32
    with pytest.raises(ValueError):
        bot_names.parseNames(bytes(data))


def test_roster_assigns_unique_client_names_four_profiles_and_individual_tempos(spawnPoints):
    flow = flowAt()
    roster.assemble(flow)
    values = list(actors.bots(flow.session).values())
    assert len({value['name'] for value in values}) == 19
    assert all(value['name'].split(']', 1)[1].startswith('测试电脑') for value in values)
    assert Counter(value['profile'] for value in values) == {
        'aggressive': 5, 'balanced': 5, 'defensive': 5, 'berserker': 4}
    assert len({value['tempo'] for value in values}) > 3
    assert len({tuple(value['patrolAnchor']) for value in values}) > 3
    assert all({value['lane'] for value in values if value['profile'] == profile} == {-1, 0, 1}
               for profile in bot_profiles.PROFILES)
    previous = deepcopy(flow.state)
    assert app.migrateState(app.STATE_VERSION, previous) == previous
    assert app.validateState(previous)
    for field, invalid in [('profile', 'invalid'), ('tempo', 0), ('lane', 8), ('patrolAnchor', [0, float('nan'), 0])]:
        broken = deepcopy(previous)
        broken['sessions']['1']['bots']['2'][field] = invalid
        assert not app.validateState(broken)


def test_template_timing_has_distinct_attack_and_defence_priorities(duel):
    value = duel.session['bot']
    timings = []
    for profile in bot_profiles.PROFILES:
        value.update(profile=profile, tempo=100)
        settings = bot_profiles.get(value)
        timings.append((settings.attackDelayMs, settings.observationMs, settings.guardMs))
    assert timings[0][0] < timings[1][0] < timings[2][0]
    assert timings[0][1] > timings[1][1] > timings[2][1]
    assert timings[0][2] < timings[1][2] < timings[2][2]


def test_aggressive_pressures_a_held_attack_while_defensive_prefers_guard(duel):
    battle.startAction(duel, 300560, released=False)
    observe(duel)
    duel.now = duel.session['pending'][battle.ACTION_TIMER]
    app.handleTimer(duel, battle.ACTION_TIMER)
    observe(duel)
    assert duel.session['action']['index'] == 1
    duel.session['bot'].update(profile='aggressive', tempo=100)
    assert choose(duel).reason == 'pressure'
    duel.session['bot']['profile'] = 'defensive'
    assert choose(duel).kind == 'parry'


def test_defensive_observes_threat_sooner_than_aggressive(duel):
    battle.startAction(duel, 300550, released=True)
    observe(duel)
    observe(duel, 100)
    duel.session['bot'].update(profile='defensive', tempo=100)
    assert choose(duel).kind == 'parry'
    duel.session['bot']['profile'] = 'aggressive'
    assert choose(duel).reason == 'observe-threat'


@pytest.mark.parametrize('profile', ['aggressive', 'balanced', 'defensive'])
def test_defensive_guard_opens_a_counter_window_after_success_feedback(nearBot, profile):
    flow = nearBot
    flow.session['bot']['profile'] = profile
    battle.startAction(flow, 300610, actorId=2)
    flow.later(bot.RELEASE_TIMER, 850)
    battle.parryReaction(flow, 2, 1, success=True)
    deadline = flow.session['pending'][bot.ACTION_TIMER]
    flow.result['send'].clear()
    bot.think(flow)
    action = flow.session['bot']['action']
    assert action['released'] == (profile == 'defensive')
    assert action['reaction'] == 'parry-success'
    assert flow.session['pending'][bot.ACTION_TIMER] == deadline
    assert not flow.result['send']
    flow.now = deadline
    app.handleTimer(flow, bot.ACTION_TIMER)
    assert action['index'] == (-1 if profile == 'defensive' else 1)
