use t7_game_server::{
    battle::ActionIntent,
    client_view::{ActorId, LocalRid},
    combat::{CombatEffect, ContactReport},
    limits::RoomRules,
    lobby::Lobby,
    maps::MapCatalog,
    motion::Pose,
    protocol::{ClientRequest, ErrorCode},
    pve::{Catalog, FIRST_MONSTER_ID, Phase as PvePhase},
    room::{GameMode, Phase},
};

fn config(position: [f32; 3], waves: &[u16], health: u16) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({"scenarios":[{
        "scenarioId":1,"mapId":2001,"instanceId":2001,"mapResourceId":10088,
        "playerSpawns":[[0,0,0]],"monsterSpawns":[position],
        "monster":{"resourceId":11001,"heroId":11001,"weaponId":11000002,"health":health,"speed":1.},
        "waves":waves,"intermissionMs":500
    }]})).unwrap()
}
fn lobby(position: [f32; 3], waves: &[u16], health: u16, duration: u64) -> Lobby {
    Lobby::with_rules(
        MapCatalog::default(),
        RoomRules::new(4)
            .unwrap()
            .with_round_limits(duration, 1)
            .unwrap(),
    )
    .with_pve_catalog(Catalog::from_json(&config(position, waves, health)).unwrap())
}
fn scene(position: [f32; 3], waves: &[u16], health: u16) -> (Lobby, String, String) {
    let mut lobby = lobby(position, waves, health, 60_000);
    let room = lobby.create_pve("a", "cooperative", 1).unwrap();
    let match_id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &match_id, 0).unwrap();
    lobby.advance(3000);
    (lobby, room.room_id, match_id)
}
fn pose(lobby: &mut Lobby, match_id: &str, sequence: u32, now: u64) {
    lobby
        .pose_input(
            "a",
            match_id,
            sequence,
            Pose {
                position: [0.; 3],
                heading: 0,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
            now,
        )
        .unwrap();
}
fn kill(lobby: &mut Lobby, room: &str, match_id: &str, sequence: u32, now: u64) -> u32 {
    pose(lobby, match_id, sequence, now);
    lobby
        .action_input("a", match_id, sequence * 2 - 1, ActionIntent::AttackUp, now)
        .unwrap();
    lobby
        .action_input(
            "a",
            match_id,
            sequence * 2,
            ActionIntent::AttackRelease,
            now,
        )
        .unwrap();
    lobby.advance(now + 400);
    let action = lobby
        .take_action_events()
        .into_iter()
        .find(|a| a.actor_id.0 == 1 && a.state == 238)
        .unwrap();
    let monster = lobby.snapshot(room).unwrap().pve.unwrap().monsters[0].clone();
    let contact = ContactReport {
        action_seq: action.action_seq,
        action_id: action.action_id.unwrap(),
        state: 238,
        elapsed_ms: 50,
        target_actor_id: monster.actor_id,
        position: [
            monster.position[0],
            monster.position[1],
            monster.position[2] + 1.,
        ],
        normal: [0., 0., 1.],
        direction: [1., 0., 0.],
        material: 0,
        weapon_contact: false,
        timing: None,
    };
    assert!(
        lobby
            .contact_input("a", match_id, sequence, contact.clone(), now + 450)
            .unwrap()
    );
    assert!(
        !lobby
            .contact_input("a", match_id, sequence + 100, contact, now + 451)
            .unwrap()
    );
    monster.actor_id
}
#[test]
fn cooperative_roster_readiness_and_capacity_are_separate_from_pvp() {
    let mut lobby = lobby([3., 0., 0.], &[1], 20, 60_000);
    assert_eq!(
        lobby.create_pve("a", "pve", 999),
        Err(ErrorCode::UnsupportedScenario)
    );
    let room = lobby.create_pve("a", "pve", 1).unwrap();
    assert_eq!(room.mode, GameMode::Pve);
    assert_eq!(room.capacity, 4);
    for user in ["b", "c", "d"] {
        lobby.join(user, &room.room_id).unwrap();
    }
    assert_eq!(lobby.join("e", &room.room_id), Err(ErrorCode::RoomFull));
    assert!(
        lobby
            .snapshot(&room.room_id)
            .unwrap()
            .members
            .iter()
            .all(|m| m.camp == 1)
    );
    assert_eq!(lobby.set_camp("b", 2), Err(ErrorCode::InvalidCamp));
    lobby.set_camp("b", 1).unwrap();
    assert_eq!(lobby.start("a", 0), Err(ErrorCode::NotReady));
    for user in ["b", "c", "d"] {
        lobby.ready(user, true).unwrap();
    }
    assert_eq!(lobby.start("a", 0).unwrap().phase, Phase::Loading);
}
#[test]
fn cleared_waves_use_fresh_ids_and_settle_then_reset_without_monster_respawns() {
    let (mut lobby, room, match_id) = scene([3., 0., 0.], &[1, 1], 20);
    lobby.advance(3001);
    let first = kill(&mut lobby, &room, &match_id, 1, 3100);
    assert_eq!(first, FIRST_MONSTER_ID);
    let view = lobby.client_view("a", &match_id).unwrap();
    assert_eq!(
        view.to_local(&match_id, ActorId(first)),
        Ok(LocalRid(first))
    );
    lobby.advance(3600);
    assert!(!lobby.snapshot(&room).unwrap().pve.unwrap().monsters[0].alive);
    lobby.advance(4600);
    let pve = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert_eq!(pve.phase, PvePhase::Intermission);
    assert!(pve.monsters.is_empty());
    lobby.advance(5100);
    assert_eq!(lobby.snapshot(&room).unwrap().pve.unwrap().wave, 2);
    let second = kill(&mut lobby, &room, &match_id, 102, 5200);
    assert_eq!(second, first + 1);
    assert_eq!(
        lobby
            .client_view("a", &match_id)
            .unwrap()
            .to_actor(&match_id, LocalRid(first)),
        Err(ErrorCode::UnknownActor)
    );
    lobby.advance(5700);
    lobby.advance(6700);
    let end = lobby.snapshot(&room).unwrap();
    assert_eq!(end.phase, Phase::Settling);
    assert_eq!(end.result.as_ref().unwrap().reason, "pve-cleared");
    assert_eq!(end.result.as_ref().unwrap().lives.len(), 1);
    assert_eq!(end.result.unwrap().lives[0].kills, 2);
    assert_eq!(end.pve.unwrap().phase, PvePhase::Victory);
    assert!(!lobby.take_combat_events().iter().any(
        |e| matches!(e.effect,CombatEffect::RespawnReady {actor_id} if actor_id.0>=FIRST_MONSTER_ID)
    ));
    lobby.advance(12700);
    let waiting = lobby.snapshot(&room).unwrap();
    assert_eq!(waiting.phase, Phase::Waiting);
    assert_eq!(waiting.pve.unwrap().wave, 0);
    let next = lobby
        .start("a", 12800)
        .unwrap()
        .current_match
        .unwrap()
        .match_id;
    assert_ne!(next, match_id);
    assert_eq!(
        lobby.contact_input(
            "a",
            &match_id,
            500,
            ContactReport {
                action_seq: 1,
                action_id: 1,
                state: 238,
                elapsed_ms: 0,
                target_actor_id: second,
                position: [0.; 3],
                normal: [0., 0., 1.],
                direction: [1., 0., 0.],
                material: 0,
                weapon_contact: false,
                timing: None,
            },
            12801
        ),
        Err(ErrorCode::WrongPhase)
    );
}
#[test]
fn server_ai_body_contacts_do_not_fabricate_weapon_collisions() {
    for block in [false, true] {
        let (mut lobby, room, match_id) = scene([1., 0., 0.], &[1], 100);
        pose(&mut lobby, &match_id, 1, 3001);
        if block {
            lobby
                .action_input("a", &match_id, 1, ActionIntent::BlockUp, 3001)
                .unwrap();
        }
        for n in 1..=12 {
            let now = 3000 + n * 100;
            pose(&mut lobby, &match_id, n as u32 + 1, now);
            lobby.advance(now);
        }
        let events = lobby.take_combat_events();
        let effects: Vec<_> = events
            .iter()
            .filter_map(|e| match &e.effect {
                CombatEffect::Contact {
                    attacker,
                    target,
                    damage,
                    parried,
                    ..
                } if attacker.0 >= FIRST_MONSTER_ID => Some((*target, *damage, *parried)),
                _ => None,
            })
            .collect();
        assert!(!effects.is_empty());
        let expected = (Some(ActorId(1)), 20, false);
        assert_eq!(effects[0], expected);
        assert_eq!(lobby.snapshot(&room).unwrap().phase, Phase::Running);
    }
}
#[test]
fn delayed_tick_does_not_teleport_or_burst_attacks_and_timeout_is_defeat() {
    let (mut lobby, room, match_id) = scene([10., 0., 0.], &[1], 100);
    pose(&mut lobby, &match_id, 1, 3001);
    lobby.advance(3100);
    let before = lobby.snapshot(&room).unwrap().pve.unwrap().monsters[0].position;
    pose(&mut lobby, &match_id, 2, 10000);
    lobby.advance(10000);
    let after = lobby.snapshot(&room).unwrap().pve.unwrap().monsters[0].position;
    assert!((before[0] - after[0]).abs() <= 0.101);
    assert!(lobby.take_combat_events().is_empty());
    lobby.advance(63000);
    let end = lobby.snapshot(&room).unwrap();
    assert_eq!(end.phase, Phase::Settling);
    assert_eq!(end.result.unwrap().reason, "timeout");
    assert_eq!(end.pve.unwrap().phase, PvePhase::Defeat);
}
#[test]
fn invalid_catalogs_and_client_spawn_injection_are_rejected() {
    let baseline: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], &[1], 20)).unwrap();
    for (key, value) in [
        ("waves", serde_json::json!([65])),
        ("waves", serde_json::json!([])),
        ("intermissionMs", serde_json::json!(0)),
        ("playerSpawns", serde_json::json!([])),
        ("mapId", serde_json::json!(0)),
    ] {
        let mut data = baseline.clone();
        data["scenarios"][0][key] = value;
        assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    }
    let mut data = baseline.clone();
    data["scenarios"][0]["monster"]["speed"] = serde_json::json!(100);
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    let mut data = baseline.clone();
    data["scenarios"]
        .as_array_mut()
        .unwrap()
        .push(baseline["scenarios"][0].clone());
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    assert!(Catalog::from_json(&vec![b' '; 131073]).is_err());
    assert!(serde_json::from_str::<ClientRequest>(r#"{"protocolVersion":1,"requestId":"1","command":{"type":"createPveRoom","payload":{"name":"pve","scenarioId":1,"monsterSpawns":[[0,0,0]]}}}"#).is_err());
    let mut no_pve = Lobby::default();
    assert_eq!(
        no_pve.create_pve("a", "pve", 1),
        Err(ErrorCode::UnsupportedScenario)
    );
}

#[test]
fn solo_wipe_settles_and_leaving_destroys_ai_state() {
    let (mut lobby, room, match_id) = scene([1., 0., 0.], &[1], 100);
    for n in 1..=200 {
        if lobby.snapshot(&room).unwrap().phase != Phase::Running {
            break;
        }
        let now = 3000 + n * 100;
        pose(&mut lobby, &match_id, n as u32, now);
        lobby.advance(now);
    }
    let end = lobby.snapshot(&room).unwrap();
    assert_eq!(end.phase, Phase::Settling);
    assert_eq!(end.result.unwrap().reason, "pve-wipe");
    assert_eq!(end.pve.unwrap().phase, PvePhase::Defeat);
    lobby.leave("a", false).unwrap();
    assert!(lobby.snapshot(&room).is_none());
    assert!(lobby.take_pve_events().is_empty());
    assert!(lobby.take_pose_events().is_empty());
    assert!(lobby.take_action_events().is_empty());
}

#[test]
fn pve_room_isolation_and_load_timeout_do_not_create_monsters() {
    let mut lobby = lobby([3., 0., 0.], &[1], 20, 60_000);
    let first = lobby.create_pve("a", "a", 1).unwrap();
    let second = lobby.create_pve("b", "b", 1).unwrap();
    let match_a = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.start("b", 0).unwrap();
    lobby.loaded("a", &match_a, 0).unwrap();
    lobby.advance(3000);
    lobby.advance(3100);
    assert_eq!(
        lobby
            .snapshot(&first.room_id)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .len(),
        1
    );
    assert!(
        lobby
            .snapshot(&second.room_id)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .is_empty()
    );
    assert!(
        lobby
            .take_pve_events()
            .iter()
            .all(|e| e.room_id == first.room_id)
    );
    lobby.advance(60_000);
    let timed_out = lobby.snapshot(&second.room_id).unwrap();
    assert_eq!(timed_out.phase, Phase::Waiting);
    assert_eq!(timed_out.last_abort_reason, Some("loadTimeout"));
    assert!(timed_out.pve.unwrap().monsters.is_empty());
}

#[test]
fn server_pve_melee_reach_hits_once_without_native_monster_contacts() {
    let (mut lobby, room, id) = scene([2.8, 0., 0.], &[1], 100);
    pose(&mut lobby, &id, 1, 3001);
    lobby
        .action_input("a", &id, 1, ActionIntent::AttackUp, 3001)
        .unwrap();
    lobby
        .action_input("a", &id, 2, ActionIntent::AttackRelease, 3001)
        .unwrap();
    for n in 1..=10 {
        let now = 3000 + n * 100;
        pose(&mut lobby, &id, n as u32 + 1, now);
        lobby.advance(now);
    }
    let hits: Vec<_> = lobby
        .take_combat_events()
        .into_iter()
        .filter(|e| matches!(e.effect,CombatEffect::Contact{attacker,..} if attacker.0==1))
        .collect();
    assert_eq!(hits.len(), 1);
    assert!(matches!(
        hits[0].effect,
        CombatEffect::Contact { damage: 21, .. }
    ));
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters[0].health,
        79
    );
}
