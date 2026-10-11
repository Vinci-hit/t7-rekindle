from copy import deepcopy
import math

import pytest

from Business.scripts import actors, app, battle, body_collision, bot, bot_footwork, bot_life, bot_profiles
from Business.scripts.codec import move_flow
from test_bot_roster import multi, sample
from test_bot_tactics import fight


@pytest.mark.parametrize('profile,maximum', [('aggressive', 150), ('balanced', 250), ('defensive', 450)])
def test_all_profiles_have_shorter_attack_and_guard_waits(profile, maximum):
    settings = bot_profiles.get({'profile': profile, 'tempo': 100})
    assert settings.attackDelayMs <= maximum
    assert settings.guardMs <= {'aggressive': 300, 'balanced': 450, 'defensive': 650}[profile]


@pytest.mark.parametrize('profile', list(bot_profiles.PROFILES))
def test_valid_melee_opportunity_does_not_wait_for_the_flanking_goal(multi, profile):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.2, 0, .2), 0), 3: ((-1.7, .15, .2), 0)}
    value = actors.data(multi, 3)
    value.update(profile=profile, tempo=100)
    for _ in range(8):
        placements[3] = (placements[3][0], value['heading'])
        fight(multi, placements)
        bot.think(multi, 3)
        if value.get('attackCount', 0):
            break
        multi.now += 100
    assert value['attackCount'] == 1
    assert value['action']['intent'] in battle.ATTACK_CHAINS
    assert value['path'] and actors.timerName(bot.MOVE_TIMER, 3) in multi.session['pending']
    assert value['position'] == [-1.7, .15, .2]


def test_small_continuous_turns_do_not_reset_attack_readiness(multi):
    value = actors.data(multi, 2)
    value.update(position=[0., 0., .2], heading=0, initialized=True, heightOffset=.2,
                 profile='balanced', tempo=100)
    for _ in range(8):
        bearing = value['heading'] + 54
        target = (1.5 * math.cos(math.radians(bearing)), -1.5 * math.sin(math.radians(bearing)), .2)
        placements = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(multi.session)}
        placements.update({1: (target, (bearing + 180) % 360 - 180), 2: ((0, 0, .2), value['heading'])})
        sample(multi, placements)
        bot.think(multi, 2)
        if value['attackCount']:
            break
        multi.now += 100
    assert value['attackCount'] == 1


@pytest.mark.parametrize('profile', list(bot_profiles.PROFILES))
def test_long_positioning_yields_to_a_body_clear_close_approach(multi, profile):
    placements = {1: ((0, 0, .2), 180), 2: ((-1., 0, .2), 0), 3: ((-3, 0, .2), 0)}
    value = actors.data(multi, 3)
    value.update(profile=profile, tempo=100)
    fight(multi, placements)
    bot.think(multi, 3)
    assert value['status'] in ('flank', 'rear', 'support')
    multi.now += 2400
    fight(multi, placements)
    bot.think(multi, 3)
    assert value['status'] == 'press'
    assert 'tactic' not in value
    assert math.dist(value['path'][-1][:2], (0, 0)) <= 1.3
    for start, end in zip([value['position'], *value['path']], value['path']):
        assert math.dist(body_collision.clip(start, end, [[-1., 0, .2]], slide=False), end) < .01


def test_pressure_does_not_replace_a_clear_flank_with_a_body_blocked_shortcut(multi):
    placements = {1: ((0, 0, .2), 180), 2: ((-1.2, 0, .2), 0), 3: ((-3, 0, .2), 0)}
    value = actors.data(multi, 3)
    value.update(profile='aggressive', tempo=100)
    fight(multi, placements)
    bot.think(multi, 3)
    multi.now += 2400
    fight(multi, placements)
    bot.think(multi, 3)
    assert value['status'] in ('flank', 'rear')
    assert value['tactic']['mode'] in ('flank', 'rear')


def test_two_groups_of_bots_open_attacks_instead_of_only_orbiting(multi):
    ids = (2, 3, 4, 12, 13, 14)
    placements = {rid: ((70 + rid, 70, .2), 0) for rid in actors.ids(multi.session)}
    for index, rid in enumerate(ids):
        position = (-2. if index < 3 else 2., (index % 3 - 1) * 1.3, .2)
        heading = 0 if index < 3 else 180
        placements[rid] = (position, heading)
        actors.data(multi, rid).update(position=list(position), heading=heading, initialized=True,
                                      heightOffset=.2, profile=tuple(bot_profiles.PROFILES)[index % len(bot_profiles.PROFILES)], tempo=100)
    for tick in range(120):
        world = sample(multi, placements)
        for rid in ids:
            world['actors'][str(rid)]['targetTerrain'] = {
                str(other): dict(ready=True, hit=False) for other in actors.enemies(multi, rid, world)}
        multi.result['send'].clear()
        for name, deadline in list(multi.session['pending'].items()):
            if deadline <= multi.now and name.startswith((bot.ACTION_TIMER, bot.RELEASE_TIMER)):
                app.handleTimer(multi, name)
        for rid in ids:
            if tick % 2 == 0:
                bot.think(multi, rid)
            if actors.timerName(bot.MOVE_TIMER, rid) in multi.session['pending']:
                bot.move(multi, rid)
        moves = {}
        for packet in multi.result['send']:
            if packet['reason'] in ('bot-move', 'bot-stop'):
                movement = move_flow.decode_move_bc_with_system_and_active(packet['body'])
                moves[movement.target_instance_id] = movement
        nextPlacements = dict(placements)
        for rid in ids:
            position = placements[rid][0]
            movement = moves.get(rid)
            if movement is not None and movement.current_velocity:
                yaw = math.radians(movement.direction_yaw)
                dx = -movement.forward_back * math.cos(yaw) + movement.left_right * math.sin(yaw)
                dy = movement.forward_back * math.sin(yaw) + movement.left_right * math.cos(yaw)
                scale = movement.current_velocity / 1000 * .05 / math.hypot(dx, dy)
                desired = (position[0] + dx * scale, position[1] + dy * scale, .2)
                position = body_collision.clip(position, desired, [placements[other][0] for other in ids if other != rid])
            nextPlacements[rid] = (position, actors.data(multi, rid)['heading'])
        placements = nextPlacements
        multi.now += 50
    counts = {profile: sum(actors.data(multi, rid)['attackCount'] for rid in ids
                          if actors.data(multi, rid)['profile'] == profile) for profile in bot_profiles.PROFILES}
    assert all(count > 0 for count in counts.values()), counts


@pytest.mark.parametrize('fault', ['wall', 'unknown', 'rear'])
def test_initiative_still_requires_native_contact_and_facing(multi, fault):
    placements = {1: ((1.5, 0, .2), 180), 2: ((0, 0, .2), 0)}
    value = actors.data(multi, 2)
    for _ in range(30):
        world = fight(multi, placements)
        if fault == 'wall':
            world['actors']['2']['terrain']['target'].update(
                hit=True, material=4, position=[.7, 0, 1.2], normal=[-1, 0, 0], direction=[1, 0, 0])
        elif fault == 'unknown':
            world['actors']['2']['terrain']['target']['ready'] = False
        else:
            world['actors']['2']['heading'] = 180
        bot.think(multi, 2)
        multi.now += 100
    assert value['attackCount'] == 0


def test_successful_attack_restarts_positioning_budget_without_changing_weapon_timing(multi):
    fight(multi, {1: ((1.5, 0, .2), 180), 2: ((0, 0, .2), 0)})
    value = actors.data(multi, 2)
    value.update(reactAt=multi.now, pressAt=multi.now - 1)
    bot.think(multi, 2)
    assert value['attackCount'] == 1
    assert 'pressAt' not in value
    assert multi.session['pending'][bot.ACTION_TIMER] - multi.now == battle.phaseMs(value['lastAttack'], 0)
    previous = deepcopy(value['action'])
    multi.now += 100
    fight(multi, {1: ((1.5, 0, .2), 180), 2: ((0, 0, .2), 0)})
    bot.think(multi, 2)
    assert value['action'] == previous


@pytest.mark.parametrize('reset', ['lost', 'stale', 'stop', 'respawn'])
def test_initiative_clock_clears_when_engagement_ends(multi, reset):
    fight(multi, {1: ((3, 0, .2), 180), 2: ((0, 0, .2), 0)})
    bot.think(multi, 2)
    value = actors.data(multi, 2)
    assert 'pressAt' in value
    assert app.validateState(multi.state)
    if reset == 'lost':
        multi.now += 100
        fight(multi, {1: ((40, 0, .2), 180), 2: ((0, 0, .2), 0)})
        bot.think(multi, 2)
    elif reset == 'stale':
        multi.now += 1000
        bot.think(multi, 2)
    elif reset == 'stop':
        bot.stop(multi, 2)
    else:
        bot_life.clearPerception(value)
    assert 'pressAt' not in value


@pytest.mark.parametrize('invalid', [-1, True, '1000'])
def test_invalid_initiative_deadline_is_rejected(multi, invalid):
    actors.data(multi, 2)['pressAt'] = invalid
    assert not bot_footwork.validState(actors.data(multi, 2))
