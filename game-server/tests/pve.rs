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
fn server_ai_contacts_respect_directional_guard() {
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
        let expected = (Some(ActorId(1)), if block { 0 } else { 8 }, block);
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
    for n in 1..=400 {
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
fn nearby_monsters_are_not_hit_without_sampled_blade_contact() {
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
    assert!(hits.is_empty());
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters[0].health,
        100
    );
}

fn batched_scene(waves: &[u16], batch_size: u16, interval_ms: u64) -> (Lobby, String, String) {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], waves, 20)).unwrap();
    data["scenarios"][0]["spawnSchedule"] = serde_json::json!({
        "batchSize": batch_size, "intervalMs": interval_ms,
    });
    let mut lobby = lobby([3., 0., 0.], waves, 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "batches", 1).unwrap();
    let match_id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &match_id, 0).unwrap();
    lobby.advance(3000);
    lobby.advance(3001);
    (lobby, room.room_id, match_id)
}

#[test]
fn empty_between_batches_is_not_wave_completion() {
    let (mut lobby, room, match_id) = batched_scene(&[2], 1, 5000);
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters.len(),
        1
    );
    kill(&mut lobby, &room, &match_id, 1, 3100);
    lobby.advance(3600);
    lobby.advance(4600);
    let gap = lobby.snapshot(&room).unwrap();
    assert_eq!(gap.phase, Phase::Running);
    let gap = gap.pve.unwrap();
    assert_eq!(gap.phase, PvePhase::Active);
    assert_eq!(gap.wave, 1);
    assert!(gap.monsters.is_empty());
    lobby.advance(7900);
    assert!(
        lobby
            .snapshot(&room)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .is_empty()
    );
    lobby.advance(8100);
    let next = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert_eq!(next.monsters.len(), 1);
    assert_eq!(next.monsters[0].actor_id, FIRST_MONSTER_ID + 1);
    kill(&mut lobby, &room, &match_id, 102, 8200);
    lobby.advance(8700);
    lobby.advance(9700);
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().phase,
        PvePhase::Victory
    );
}

#[test]
fn delayed_tick_emits_one_batch_without_catchup_burst() {
    let (mut lobby, room, _) = batched_scene(&[5], 2, 1000);
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters.len(),
        2
    );
    lobby.advance(10_000);
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters.len(),
        4
    );
    lobby.advance(10_100);
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters.len(),
        4
    );
    lobby.advance(11_000);
    assert_eq!(
        lobby.snapshot(&room).unwrap().pve.unwrap().monsters.len(),
        5
    );
}

#[test]
fn spawn_schedule_rejects_invalid_limits() {
    for (size, interval) in [(0, 1000), (65, 1000), (1, 99), (1, 60_001)] {
        let mut data: serde_json::Value =
            serde_json::from_slice(&config([3., 0., 0.], &[6], 20)).unwrap();
        data["scenarios"][0]["spawnSchedule"] = serde_json::json!({
            "batchSize": size, "intervalMs": interval,
        });
        assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    }
}

fn entrance_scene(
    points: &[[f32; 3]],
    entrances: serde_json::Value,
    count: u16,
) -> (Lobby, String, String) {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], &[count], 20)).unwrap();
    data["scenarios"][0]["monsterSpawns"] = serde_json::json!(points);
    data["scenarios"][0]["spawnEntrances"] = entrances;
    let mut lobby = lobby([3., 0., 0.], &[count], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "entry", 1).unwrap();
    let match_id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &match_id, 0).unwrap();
    lobby.advance(3000);
    lobby.advance(3001);
    (lobby, room.room_id, match_id)
}

#[test]
fn entries_use_independent_sides_then_land_before_hunting() {
    let (mut lobby, room, match_id) = entrance_scene(
        &[[3., -3., 0.], [3., 3., 0.]],
        serde_json::json!([
            {"startOffset":[0,-4,2],"arcHeight":1,"durationMs":1000},
            {"startOffset":[0,4,2],"arcHeight":1,"durationMs":1000}
        ]),
        2,
    );
    pose(&mut lobby, &match_id, 1, 3002);
    let initial = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert_eq!(initial.monsters[0].position, [3., -7., 2.]);
    assert_eq!(initial.monsters[1].position, [3., 7., 2.]);
    for at in (3101..=3501).step_by(100) {
        lobby.advance(at);
    }
    let middle = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert_eq!(middle.monsters[0].position, [3., -5., 2.]);
    assert_eq!(middle.monsters[1].position, [3., 5., 2.]);
    for at in (3601..=4001).step_by(100) {
        lobby.advance(at);
    }
    let landed = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert_eq!(landed.monsters[0].position, [3., -3., 0.]);
    assert_eq!(landed.monsters[1].position, [3., 3., 0.]);
    pose(&mut lobby, &match_id, 2, 4002);
    lobby.advance(4101);
    let hunting = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert!(hunting.monsters[0].position[1] > -3.);
    assert!(hunting.monsters[1].position[1] < 3.);
    assert_eq!(hunting.monsters[0].position[2], 0.);
}

#[test]
fn entering_monster_cannot_attack_and_stall_does_not_skip_entry() {
    let (mut lobby, room, match_id) = entrance_scene(
        &[[0.5, 0., 0.]],
        serde_json::json!([{"startOffset":[0,0,0],"arcHeight":1,"durationMs":1000}]),
        1,
    );
    pose(&mut lobby, &match_id, 1, 3002);
    lobby.take_action_events();
    lobby.advance(8000);
    let pve = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert!((pve.monsters[0].position[2] - 0.36).abs() < 0.001);
    assert!(
        lobby
            .take_action_events()
            .iter()
            .all(|event| event.actor_id.0 < FIRST_MONSTER_ID)
    );
}

#[test]
fn entry_catalog_rejects_missing_paths_or_unbounded_motion() {
    for entries in [
        serde_json::json!([
            {"startOffset":[0,0,0],"arcHeight":0,"durationMs":1000},
            {"startOffset":[0,0,0],"arcHeight":0,"durationMs":1000}
        ]),
        serde_json::json!([{"startOffset":[0,0,0],"arcHeight":0,"durationMs":0}]),
        serde_json::json!([{"startOffset":[21,0,0],"arcHeight":0,"durationMs":1000}]),
        serde_json::json!([{"startOffset":[0,0,0],"arcHeight":-1,"durationMs":1000}]),
        serde_json::json!([{"startOffset":[20,20,20],"arcHeight":10,"durationMs":500}]),
    ] {
        let mut data: serde_json::Value =
            serde_json::from_slice(&config([3., 0., 0.], &[1], 20)).unwrap();
        data["scenarios"][0]["spawnEntrances"] = entries;
        assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    }
}

#[test]
fn killed_during_entry_does_not_finish_path_or_resume_hunting() {
    let (mut lobby, room, match_id) = entrance_scene(
        &[[1., 0., 0.]],
        serde_json::json!([{"startOffset":[0,0,0],"arcHeight":0,"durationMs":3000}]),
        1,
    );
    kill(&mut lobby, &room, &match_id, 1, 3100);
    lobby.advance(3600);
    let dead = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert!(!dead.monsters[0].alive);
    assert_eq!(dead.monsters[0].position, [1., 0., 0.]);
    lobby.advance(4600);
    let end = lobby.snapshot(&room).unwrap().pve.unwrap();
    assert!(end.monsters.is_empty());
    assert_eq!(end.phase, PvePhase::Victory);
}

fn encounter_lobby(entries: serde_json::Value, waves: &[u16]) -> Lobby {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], waves, 20)).unwrap();
    data["scenarios"][0]["encounters"] = entries;
    lobby([1., 0., 0.], waves, 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap())
}

#[test]
fn encounters_wait_for_living_player_proximity_and_can_be_visited_out_of_order() {
    let mut lobby = encounter_lobby(
        serde_json::json!([
            {"triggerPosition":[20,0,0],"triggerRadius":2,"monsterSpawns":[[21,0,0]]},
            {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]]}
        ]),
        &[1, 1],
    );
    let room = lobby.create_pve("a", "exploration", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    lobby.advance(3001);
    assert!(
        lobby
            .snapshot(&room.room_id)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .is_empty()
    );
    pose(&mut lobby, &id, 1, 3002);
    lobby.advance(3101);
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.wave, 1);
    assert_eq!(state.monsters[0].position, [1., 0., 0.]);
    kill(&mut lobby, &room.room_id, &id, 2, 3200);
    lobby.advance(3700);
    lobby.advance(4700);
    lobby.advance(5400);
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.phase, PvePhase::Intermission);
    assert_eq!(state.wave, 1);
    assert!(state.monsters.is_empty());
    // Remaining near an already cleared trigger cannot spawn it again.
    pose(&mut lobby, &id, 3, 5401);
    lobby.advance(5500);
    assert!(
        lobby
            .snapshot(&room.room_id)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .is_empty()
    );
}

#[test]
fn encounter_above_player_does_not_trigger_and_override_kind_is_server_owned() {
    let mut lobby = encounter_lobby(
        serde_json::json!([
            {"triggerPosition":[0,0,10],"triggerRadius":10,"monsterSpawns":[[1,0,10]]},
            {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]],
              "monster":{"resourceId":12001,"heroId":12002,"weaponId":12003,"health":42,"speed":2}}
        ]),
        &[1, 1],
    );
    let room = lobby.create_pve("a", "floors", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3101);
    let monsters = lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters;
    assert_eq!(monsters.len(), 1);
    assert_eq!(monsters[0].kind.resource_id, 12001);
    assert_eq!(monsters[0].health, 42);
}

#[test]
fn invalid_encounter_configuration_is_rejected() {
    for entry in [
        serde_json::json!({"triggerPosition":[0,0,0],"triggerRadius":0,"monsterSpawns":[[1,0,0]]}),
        serde_json::json!({"triggerPosition":[0,0,0],"triggerRadius":5,"monsterSpawns":[]}),
        serde_json::json!({"triggerPosition":[0,0,0],"triggerRadius":5,"monsterSpawns":[[1,0,0]],
            "monster":{"resourceId":1,"heroId":1,"weaponId":1,"health":0,"speed":2}}),
    ] {
        let mut data: serde_json::Value =
            serde_json::from_slice(&config([1., 0., 0.], &[1], 20)).unwrap();
        data["scenarios"][0]["encounters"] = serde_json::json!([entry]);
        assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    }
}

#[test]
fn hunting_samples_navigation_height_instead_of_preserving_spawn_altitude() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 2.], &[1], 20)).unwrap();
    data["scenarios"][0]["navigation"] = serde_json::json!({"polygons":[
        [[-1,-1,0],[5,-1,3],[5,1,3],[-1,1,0]]],"links":[[]]});
    let mut lobby = lobby([3., 0., 2.], &[1], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "slope", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3101);
    let monster = lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0].clone();
    assert!(monster.position[0] < 3.);
    assert!((monster.position[2] - (monster.position[0] + 1.) / 2.).abs() < 0.0001);
    assert!(
        lobby
            .take_pose_events()
            .iter()
            .any(|pose| pose.actor_id.0 >= FIRST_MONSTER_ID && pose.pose.forward_back == -1)
    );
}

#[test]
fn pve_contact_height_drives_damage_and_authoritative_hit_part() {
    use t7_game_server::combat::HitPart;
    for (height, part, amount) in [
        (0.3, HitPart::Legs, 15),
        (1., HitPart::Body, 20),
        (1.65, HitPart::Head, 30),
    ] {
        let (mut lobby, room, id) = scene([3., 0., 0.], &[1], 100);
        pose(&mut lobby, &id, 1, 3001);
        lobby.advance(3001);
        lobby
            .action_input("a", &id, 1, ActionIntent::AttackUp, 3100)
            .unwrap();
        lobby
            .action_input("a", &id, 2, ActionIntent::AttackRelease, 3100)
            .unwrap();
        pose(&mut lobby, &id, 2, 3500);
        lobby.advance(3500);
        let action = lobby
            .take_action_events()
            .into_iter()
            .find(|a| a.actor_id.0 == 1 && a.state == 238)
            .unwrap();
        let monster = lobby.snapshot(&room).unwrap().pve.unwrap().monsters[0].clone();
        let report = ContactReport {
            action_seq: action.action_seq,
            action_id: action.action_id.unwrap(),
            state: 238,
            elapsed_ms: 50,
            target_actor_id: monster.actor_id,
            position: [
                monster.position[0],
                monster.position[1],
                monster.position[2] + height,
            ],
            normal: [-1., 0., 0.],
            direction: [1., 0., 0.],
            material: 0,
            weapon_contact: false,
            timing: None,
        };
        assert!(
            lobby
                .contact_input("a", &id, 1, report.clone(), 3550)
                .unwrap()
        );
        assert!(!lobby.contact_input("a", &id, 2, report, 3551).unwrap());
        let hit = lobby
            .take_combat_events()
            .into_iter()
            .find(|e| matches!(e.effect, CombatEffect::Contact{attacker,..} if attacker.0==1))
            .unwrap();
        assert!(
            matches!(hit.effect, CombatEffect::Contact {hit_part, damage, ..} if hit_part == part && damage == amount)
        );
    }
}

#[test]
fn unreachable_target_stops_then_reacquires_without_waiting_for_route_cache() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], &[1], 100)).unwrap();
    data["scenarios"][0]["navigation"] = serde_json::json!({"polygons":[
        [[-1,-2,0],[8,-2,0],[8,2,0],[-1,2,0]]],"links":[[]]});
    let mut lobby = lobby([3., 0., 0.], &[1], 100, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "pursuit", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    for (sequence, now, position, expected_axis) in [
        (1, 3101, [0., 0., 0.], -1),
        (2, 3201, [0., 0., 2.], 0),
        (3, 3301, [5.5, 0., 0.], -1),
    ] {
        lobby
            .pose_input(
                "a",
                &id,
                sequence,
                Pose {
                    position,
                    heading: 0,
                    forward_back: 0,
                    left_right: 0,
                    running: false,
                    motion: None,
                },
                now,
            )
            .unwrap();
        lobby.advance(now);
        let movement = lobby
            .take_pose_events()
            .into_iter()
            .filter(|event| event.actor_id.0 == FIRST_MONSTER_ID)
            .last()
            .unwrap();
        assert_eq!(movement.pose.forward_back, expected_axis);
        if sequence == 1 {
            assert!(movement.pose.position[0] < 3.);
        }
        if sequence == 2 {
            assert_eq!(movement.pose.position[0], 2.9);
        }
        if sequence == 3 {
            assert!(movement.pose.position[0] > 2.9);
            assert_eq!(movement.pose.heading, 0);
        }
    }
    assert_eq!(
        lobby
            .snapshot(&room.room_id)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .len(),
        1
    );
}

#[test]
fn native_birth_pauses_ai_until_clip_end_and_publishes_completion_before_pursuit() {
    for (animation, duration) in [("emerge1", 3167), ("vault1", 1800), ("vault2", 3433)] {
        let mut data: serde_json::Value =
            serde_json::from_slice(&config([1., 0., 0.], &[1], 100)).unwrap();
        data["scenarios"][0]["birth"] = serde_json::json!(animation);
        let mut lobby = lobby([1., 0., 0.], &[1], 100, 60_000)
            .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
        let room = lobby.create_pve("a", "birth", 1).unwrap();
        let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
        lobby.loaded("a", &id, 0).unwrap();
        lobby.advance(3000);
        pose(&mut lobby, &id, 1, 3001);
        lobby.advance(3001);
        let first = lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0].clone();
        let birth = first.birth.unwrap();
        assert_eq!(birth.ends_at_ms - birth.started_at_ms, duration);
        let before = birth.ends_at_ms - 100;
        pose(&mut lobby, &id, 2, before);
        lobby.advance(before);
        let still = lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0].clone();
        assert_eq!(still.position, first.position);
        assert!(still.birth.is_some());
        assert!(
            !lobby
                .take_action_events()
                .iter()
                .any(|a| a.actor_id.0 == FIRST_MONSTER_ID)
        );
        assert!(lobby.take_combat_events().is_empty());
        pose(&mut lobby, &id, 3, birth.ends_at_ms);
        lobby.advance(birth.ends_at_ms);
        assert!(
            lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0]
                .birth
                .is_none()
        );
        assert!(lobby.take_pve_events().iter().any(|e| {
            e.snapshot
                .monsters
                .first()
                .is_some_and(|m| m.birth.is_none())
        }));
        assert!(
            lobby
                .take_action_events()
                .iter()
                .any(|a| a.actor_id.0 == FIRST_MONSTER_ID)
        );
    }
}

#[test]
fn invalid_or_dual_transform_birth_configuration_is_rejected() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1], 100)).unwrap();
    data["scenarios"][0]["birth"] = serde_json::json!("unknown");
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    data["scenarios"][0]["birth"] = serde_json::json!("emerge1");
    data["scenarios"][0]["spawnEntrances"] =
        serde_json::json!([{"startOffset":[0,0,0],"arcHeight":0,"durationMs":1000}]);
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
}

#[test]
fn sequential_encounters_do_not_skip_a_distant_prerequisite() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1, 1], 20)).unwrap();
    data["scenarios"][0]["encounterOrder"] = serde_json::json!("sequential");
    data["scenarios"][0]["encounters"] = serde_json::json!([
        {"triggerPosition":[20,0,0],"triggerRadius":2,"monsterSpawns":[[21,0,0]],
         "objective":{"approach":"Reach the gate","active":"Clear the gate"}},
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]]}
    ]);
    let mut lobby = lobby([1., 0., 0.], &[1, 1], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "story", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3101);
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.wave, 0);
    assert!(state.monsters.is_empty());
    let objective = state.objective.unwrap();
    assert_eq!(objective.text, "Reach the gate");
    assert!(!objective.active);
    assert_eq!(objective.encounter, 1);
    assert_eq!(objective.target_position, [20., 0., 0.]);
}

#[test]
fn sequential_clear_advances_objective_and_does_not_replay_completed_encounters() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1, 1], 20)).unwrap();
    data["scenarios"][0]["encounterOrder"] = serde_json::json!("sequential");
    data["scenarios"][0]["encounters"] = serde_json::json!([
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]],
         "objective":{"approach":"Reach the gate","active":"Clear the gate"}},
        {"triggerPosition":[20,0,0],"triggerRadius":2,"monsterSpawns":[[21,0,0]],
         "objective":{"approach":"Continue along the road","active":"Clear the road"}}
    ]);
    let mut lobby = lobby([1., 0., 0.], &[1, 1], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "story", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3101);
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.objective.as_ref().unwrap().text, "Clear the gate");
    assert!(state.objective.unwrap().active);
    kill(&mut lobby, &room.room_id, &id, 2, 3200);
    lobby.advance(3700);
    lobby.advance(4700);
    pose(&mut lobby, &id, 3, 5300);
    lobby.advance(5400);
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.phase, PvePhase::Intermission);
    assert_eq!(state.wave, 1);
    assert!(state.monsters.is_empty());
    let objective = state.objective.unwrap();
    assert_eq!(objective.encounter, 2);
    assert_eq!(objective.text, "Continue along the road");
    assert!(!objective.active);
}

#[test]
fn story_configuration_rejects_missing_encounters_and_invalid_objective_text() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1], 20)).unwrap();
    data["scenarios"][0]["encounterOrder"] = serde_json::json!("sequential");
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    for text in [" ".to_string(), "x".repeat(129), "line\nbreak".to_string()] {
        data["scenarios"][0]["encounters"] = serde_json::json!([
            {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]],
             "objective":{"approach":text,"active":"Clear the gate"}}
        ]);
        assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    }
}

#[test]
fn story_goals_do_not_skip_when_no_enemies_are_alive() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1], 20)).unwrap();
    data["scenarios"][0]["encounterOrder"] = serde_json::json!("sequential");
    data["scenarios"][0]["waves"] = serde_json::json!([0, 0, 1]);
    data["scenarios"][0]["encounters"] = serde_json::json!([
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]],
         "goal":{"type":"collect","points":[[0,0,0],[10,0,0]],"radius":1}},
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]],
         "goal":{"type":"defend","position":[0,0,0],"radius":2,"durationMs":500,"health":100}},
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]]}
    ]);
    let mut lobby = lobby([1., 0., 0.], &[1], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "story", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    for seq in 1..=8 {
        let now = 3000 + seq as u64 * 100;
        pose(&mut lobby, &id, seq, now);
        lobby.advance(now);
    }
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.wave, 1);
    assert_eq!(state.phase, PvePhase::Active);
    assert!(state.monsters.is_empty());
    let goal = state.goal.unwrap();
    assert_eq!(goal.kind, "collect");
    assert_eq!(goal.current, 1);
    assert_eq!(goal.total, 2);
    // Re-reading the view is a reconnect snapshot, not a new stage entry.
    let view = serde_json::to_value(lobby.client_view("a", &id).unwrap()).unwrap();
    assert_eq!(view["pve"]["goal"], serde_json::to_value(&goal).unwrap());
    assert!(
        lobby
            .snapshot(&room.room_id)
            .unwrap()
            .pve
            .unwrap()
            .monsters
            .is_empty()
    );
}

#[test]
fn zero_enemy_clear_and_disconnected_escort_are_rejected() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1], 20)).unwrap();
    data["scenarios"][0]["waves"] = serde_json::json!([0]);
    data["scenarios"][0]["encounters"] = serde_json::json!([
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]]}
    ]);
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
    data["scenarios"][0]["encounters"][0]["goal"] =
        serde_json::json!({"type":"escort","path":[[1,1,0],[11,1,0]],"radius":2,"speed":1});
    data["scenarios"][0]["encounters"][0]["triggerPosition"] = serde_json::json!([1, 1, 0]);
    data["scenarios"][0]["navigation"] = serde_json::json!({"polygons":[[[0,0,0],[4,0,0],[4,4,0],[0,4,0]],[[10,0,0],[14,0,0],[14,4,0],[10,4,0]]],"links":[[],[]]});
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
}

#[test]
fn story_travel_collection_escort_and_defense_advance_to_final_battle() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1], 20)).unwrap();
    data["scenarios"][0]["encounterOrder"] = serde_json::json!("sequential");
    data["scenarios"][0]["intermissionMs"] = serde_json::json!(100);
    data["scenarios"][0]["waves"] = serde_json::json!([0, 0, 0, 0, 1]);
    let goals = serde_json::json!([
        {"type":"reach","position":[0,0,0],"radius":1},
        {"type":"collect","points":[[0,0,0],[0.5,0,0]],"radius":1},
        {"type":"escort","path":[[0,0,0],[0.3,0,0]],"radius":2,"speed":1},
        {"type":"defend","position":[0,0,0],"radius":2,"durationMs":200,"health":100},
        {"type":"clear"}
    ]);
    data["scenarios"][0]["encounters"] = serde_json::Value::Array(
        goals
            .as_array()
            .unwrap()
            .iter()
            .map(|g| {
                serde_json::json!({
                    "triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[1,0,0]],"goal":g
                })
            })
            .collect(),
    );
    let mut lobby = lobby([1., 0., 0.], &[1], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "story", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    for seq in 1..=20 {
        let now = 3000 + seq as u64 * 100;
        pose(&mut lobby, &id, seq, now);
        lobby.advance(now);
    }
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.wave, 5);
    assert_eq!(state.phase, PvePhase::Active);
    assert_eq!(state.monsters.len(), 1);
    assert!(state.goal.is_none());
}

#[test]
fn enemies_can_destroy_defense_target_without_a_player_wipe() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], &[1], 100)).unwrap();
    data["scenarios"][0]["encounters"] = serde_json::json!([
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[3,0,0]],
         "goal":{"type":"defend","position":[2,0,0],"radius":3,"durationMs":60000,"health":10}}
    ]);
    let mut lobby = lobby([3., 0., 0.], &[1], 100, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "story", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    for seq in 1..=40 {
        let now = 3000 + seq as u64 * 100;
        if lobby.snapshot(&room.room_id).unwrap().result.is_some() {
            break;
        }
        pose(&mut lobby, &id, seq, now);
        lobby.advance(now);
    }
    let state = lobby.snapshot(&room.room_id).unwrap();
    let result = state.result.unwrap();
    assert_eq!(result.reason, "pve-objective-destroyed");
    assert_eq!(result.lives.len(), 1);
    assert_eq!(result.lives[0].health, 100);
}

#[test]
fn story_objects_follow_goal_progress_and_mixed_monsters_keep_their_resources() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], &[2], 20)).unwrap();
    let kind = data["scenarios"][0]["monster"].clone();
    let mut other = kind.clone();
    other["resourceId"] = serde_json::json!(11002);
    other["heroId"] = serde_json::json!(11002);
    data["scenarios"][0]["encounters"] = serde_json::json!([
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[3,0,0]],"monsterPool":[kind,other],
         "goal":{"type":"collect","points":[[0,0,0],[10,0,0]],"radius":1},
         "objects":[{"resourceId":9001,"position":[0,0,0],"binding":{"type":"collect","index":0}},
                    {"resourceId":9002,"position":[10,0,0],"binding":{"type":"collect","index":1}}]}
    ]);
    let mut lobby = lobby([3., 0., 0.], &[2], 20, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "story", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3101);
    pose(&mut lobby, &id, 2, 3201);
    lobby.advance(3201);
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.monsters.len(), 2);
    assert_eq!(state.monsters[0].kind.resource_id, 11001);
    assert_eq!(state.monsters[1].kind.resource_id, 11002);
    assert_eq!(state.objects.len(), 1);
    assert_eq!(
        state.objects[0].actor_id,
        t7_game_server::pve::FIRST_OBJECT_ID + 1
    );
    assert_eq!(state.objects[0].resource_id, 9002);
    data["scenarios"][0]["encounters"][0]["objects"][0]["binding"] =
        serde_json::json!({"type":"escort"});
    assert!(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).is_err());
}

#[test]
fn proximity_regions_spawn_without_clearing_earlier_monsters_or_waiting_for_batches() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([3., 0., 0.], &[2, 2], 100)).unwrap();
    let scenario = &mut data["scenarios"][0];
    scenario["encounterOrder"] = serde_json::json!("proximity");
    scenario["intermissionMs"] = serde_json::json!(100);
    scenario["spawnSchedule"] = serde_json::json!({"batchSize":1,"intervalMs":1500});
    scenario["encounters"] = serde_json::json!([
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[3,0,0]],"objective":{"approach":"Advance","active":"Fight","approachTaskId":20002,"activeTaskId":20002}},
        {"triggerPosition":[0,0,0],"triggerRadius":2,"monsterSpawns":[[4,0,0]]}
    ]);
    let mut lobby = lobby([3., 0., 0.], &[2, 2], 100, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "regions", 1).unwrap();
    assert_eq!(room.pve.as_ref().unwrap().objective.as_ref().unwrap().task_id, Some(20002));
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3001);
    assert_eq!(lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters.len(), 2);
    for seq in 2..=5 {
        let now = 3000 + seq as u64 * 100;
        pose(&mut lobby, &id, seq, now);
        lobby.advance(now);
    }
    let state = lobby.snapshot(&room.room_id).unwrap().pve.unwrap();
    assert_eq!(state.wave, 2);
    assert_eq!(state.monsters.len(), 4);
    assert!(state.monsters.iter().all(|m| m.alive));
    assert_ne!(state.phase, PvePhase::Victory);
}

#[test]
fn damage_during_birth_preserves_entry_until_its_original_deadline() {
    let mut data: serde_json::Value =
        serde_json::from_slice(&config([1., 0., 0.], &[1], 100)).unwrap();
    data["scenarios"][0]["birth"] = serde_json::json!("emerge1");
    let mut lobby = lobby([1., 0., 0.], &[1], 100, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "entry", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3001);
    assert!(lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0].birth.is_some());
    kill(&mut lobby, &room.room_id, &id, 2, 3200);
    lobby.advance(3700);
    let monster = lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0].clone();
    assert!(monster.alive);
    assert!(monster.health < 100);
    let birth = monster.birth.expect("nonlethal damage must not interrupt birth");
    assert_eq!(monster.position, [1., 0., 0.]);
    pose(&mut lobby, &id, 3, birth.ends_at_ms);
    lobby.advance(birth.ends_at_ms);
    assert!(lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters[0].birth.is_none());
}


#[test]
fn crowd_attacks_are_spaced_and_cannot_instantly_kill_a_player() {
    let (mut lobby, room, id) = scene([1., 0., 0.], &[18], 100);
    let mut strikes = Vec::new();
    let mut release_times = std::collections::BTreeMap::new();
    for seq in 1..=40 {
        let now = 3000 + seq as u64 * 100;
        pose(&mut lobby, &id, seq, now);
        lobby.advance(now);
        for event in lobby.take_action_events() {
            if event.actor_id.0 >= FIRST_MONSTER_ID && event.state == 238 {
                release_times.insert((event.actor_id.0, event.action_seq), event.server_time_ms);
            }
        }
        for event in lobby.take_combat_events() {
            if let CombatEffect::Contact { attacker, action_seq, damage, .. } = event.effect {
                if attacker.0 >= FIRST_MONSTER_ID {
                    assert_eq!(damage, 8);
                    assert!(event.server_time_ms >= release_times[&(attacker.0, action_seq)] + 360);
                    strikes.push(event.server_time_ms);
                }
            }
        }
    }
    assert!(strikes.len() >= 2);
    assert!(strikes.windows(2).all(|pair| pair[1] - pair[0] >= 700));
    assert_eq!(lobby.snapshot(&room).unwrap().phase, Phase::Running);
}

#[test]
fn proximity_trigger_does_not_require_the_ai_navigation_component() {
    let mut data: serde_json::Value = serde_json::from_slice(&config([3., 0., 0.], &[1], 100)).unwrap();
    let scenario = &mut data["scenarios"][0];
    scenario["encounterOrder"] = serde_json::json!("proximity");
    scenario["navigation"] = serde_json::json!({"polygons":[
        [[-1,-1,0],[1,-1,0],[1,1,0],[-1,1,0]],
        [[2,-1,0],[4,-1,0],[4,1,0],[2,1,0]]],"links":[[],[]]});
    scenario["encounters"] = serde_json::json!([
        {"triggerPosition":[3,0,0],"triggerRadius":4,"monsterSpawns":[[3,0,0]]}
    ]);
    let mut lobby = lobby([3., 0., 0.], &[1], 100, 60_000)
        .with_pve_catalog(Catalog::from_json(&serde_json::to_vec(&data).unwrap()).unwrap());
    let room = lobby.create_pve("a", "trigger", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3100);
    lobby.advance(3100);
    assert_eq!(lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters.len(), 1);
}

#[test]
fn a_wave_sharing_one_spawn_point_spreads_out_instead_of_stacking() {
    let mut lobby = lobby([6., 0., 0.], &[6], 100, 60_000);
    let room = lobby.create_pve("a", "crowd", 1).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    pose(&mut lobby, &id, 1, 3001);
    lobby.advance(3001);
    let gaps = |lobby: &Lobby| {
        let monsters = lobby.snapshot(&room.room_id).unwrap().pve.unwrap().monsters;
        assert_eq!(monsters.len(), 6);
        let mut gap = f32::INFINITY;
        for (i, a) in monsters.iter().enumerate() {
            for b in &monsters[i + 1..] {
                gap = gap.min((a.position[0] - b.position[0]).hypot(a.position[1] - b.position[1]));
            }
        }
        gap
    };
    // Six monsters on one configured point used to share the identical coordinate.
    assert!(gaps(&lobby) > 0.5, "spawn stacked: {}", gaps(&lobby));
    for seq in 2..=80u32 {
        let now = 3001 + u64::from(seq) * 100;
        pose(&mut lobby, &id, seq, now);
        lobby.advance(now);
    }
    // After closing on the player they keep their spacing rather than collapsing onto one point.
    assert!(gaps(&lobby) > 0.4, "crowd collapsed: {}", gaps(&lobby));
}
