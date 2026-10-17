"""Display the server's frozen round result through the native settlement flow."""
from . import battle, controls, contracts as wire, online_scene, settlement


def receive(flow, room):
    from .custom_lobby import integer
    match = flow.state.get('onlineScene')
    if match is None or room['currentMatch']['matchId'] != match['matchId'] or match['phase'] != 3:
        raise ValueError('settlement requires the running frozen match')
    result = room['result']
    winner = integer(result['result'], 1, 3)
    if result['reason'] not in ('timeout', 'kill-target', 'pve-cleared', 'pve-wipe', 'pve-objective-destroyed'):
        raise ValueError('invalid server settlement reason')
    lives = result['lives']
    members = {m['actorId']: m for m in room['members']}
    if type(lives) is not list or len(lives) != len(members) or {v['actorId'] for v in lives} != set(members):
        raise ValueError('invalid final score roster')
    ranks = {r['actorId']: integer(r['rank'], 1, len(members)) for r in result['ranks']}
    if len(result['ranks']) != len(members) or set(ranks) != set(members):
        raise ValueError('invalid final ranking')
    camp_kills = {'1': 0, '2': 0}
    ranked = []
    for camp in (1, 2):
        team = []
        for life in lives:
            member = members[life['actorId']]
            if member['camp'] != camp:
                continue
            kills = integer(life['kills'], 0, 0x7fffffff)
            deaths = integer(life['deaths'], 0, 0x7fffffff)
            camp_kills[str(camp)] += kills
            team.append(dict(rid=online_scene.actor_rid(match, life['actorId']), userId=member['userId'],
                name=member['displayName'], camp=camp, heroId=member.get('loadout', {}).get('heroId', wire.HERO_ID), kills=kills, deaths=deaths,
                points=min(0x7fffffff, kills * 100), rank=ranks[life['actorId']], result=winner if winner == 3 else 1 if winner == camp else 2))
        team.sort(key=lambda m: m['rank'])
        ranked.extend(team)
    connection, session = flow.connection, flow.session
    try:
        for key, data in flow.state['sessions'].items():
            if data['role'] != 'instance' or not data.get('initialized') or data.get('leaving'):
                continue
            flow.connection, flow.session = int(key), data
            clock = data['battleClock']
            snapshot = dict(instanceStartedAt=data['instanceStartedAt'], startedAt=clock['startedAt'],
                durationMs=clock['durationMs'], endedAt=flow.now, reason=result['reason'], result=winner,
                campKills=camp_kills, members=ranked, killTarget=room['killTarget'])
            if not settlement.validState({'settlement': snapshot}):
                raise ValueError('invalid native settlement snapshot')
            flow.state['settlement'] = snapshot
            data.update(roundEnded=True, groundEnabled=False, onlinePhase=4)
            flow.cancelTimers()
            from . import online_prediction
            online_prediction.clear(data)
            battle.resetAction(flow)
            controls.activate(flow)
            settlement.publishScores(flow, snapshot)
            flow.send(0xA, wire.roundResult(flow.now, winner), 'online-round-result')
            flow.send(0xA, wire.roundState(flow.now, 5, 5000), 'online-round-end')
            flow.later('round-finish', 5000)
            flow.phase('settlement')
        match['phase'] = 4
    finally:
        flow.connection, flow.session = connection, session
