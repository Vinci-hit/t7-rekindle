use t7_game_server::{
    battle::ActionIntent as A,
    combat::{CombatEffect, ContactReport},
    lobby::Lobby,
    motion::Pose,
    protocol::ErrorCode,
};
fn scene() -> (Lobby, String) {
    scene_with_rules(t7_game_server::limits::RoomRules::default())
}
fn scene_with_rules(rules: t7_game_server::limits::RoomRules) -> (Lobby, String) {
    let mut l = Lobby::with_rules(t7_game_server::maps::MapCatalog::default(), rules);
    let r = l.create("a", "combat", 1003).unwrap();
    l.join("b", &r.room_id).unwrap();
    l.ready("b", true).unwrap();
    let m = l.start("a", 0).unwrap().current_match.unwrap().match_id;
    l.loaded("a", &m, 0).unwrap();
    l.loaded("b", &m, 0).unwrap();
    l.advance(3000);
    (l, m)
}
fn pose(l: &mut Lobby, m: &str, seq: u32, now: u64) {
    for (account, x, yaw) in [("a", 0., 0), ("b", 1.2, 180)] {
        l.pose_input(
            account,
            m,
            seq,
            Pose {
                position: [x, 0., 0.5],
                heading: yaw,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
            now,
        )
        .unwrap();
    }
}
fn strike(l: &mut Lobby, m: &str, seq: u32, now: u64) -> u32 {
    l.action_input("a", m, seq, A::AttackUp, now).unwrap();
    l.action_input("a", m, seq + 1, A::AttackRelease, now + 1)
        .unwrap();
    l.advance(now + 360);
    l.take_action_events()
        .into_iter()
        .rfind(|e| e.actor_id.0 == 1 && e.state == 238)
        .unwrap()
        .action_id
        .unwrap()
}
fn report(seq: u32) -> ContactReport {
    ContactReport {
        action_seq: 54321,
        action_id: seq,
        state: 238,
        elapsed_ms: 50,
        target_actor_id: 2,
        position: [0.9, 0., 1.5],
        normal: [-1., 0., 0.],
        direction: [1., 0., 0.],
        material: 4,
        weapon_contact: false,
        timing: None,
    }
}
#[test]
fn contacts_are_authorized_once_and_interrupt_the_victim() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    assert!(l.contact_input("a", &m, 1, report(seq), 3420).unwrap());
    assert!(!l.contact_input("a", &m, 2, report(seq), 3421).unwrap());
    assert_eq!(
        l.contact_input("a", &m, 2, report(seq), 3421),
        Err(ErrorCode::StaleInput)
    );
    let event = l.take_combat_events().pop().unwrap();
    match event.effect {
        CombatEffect::Contact { damage, lives, .. } => {
            assert_eq!(damage, 20);
            assert_eq!(lives[1].health, 80);
        }
        _ => panic!("expected contact"),
    }
    let reaction = l.take_action_events().pop().unwrap();
    assert_eq!(reaction.state, 35);
    assert_eq!(reaction.target_actor_id.unwrap().0, 1);
}
#[test]
fn hurt_recovers_once_after_the_native_animation_finishes() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let action = strike(&mut l, &m, 1, 3000);
    l.contact_input("a", &m, 1, report(action), 3420).unwrap();
    let reactions = l.take_action_events();
    assert_eq!(reactions.iter().filter(|e| e.state == 35).count(), 1);
    // The former 400ms recovery must not interrupt the 550ms native clip.
    for at in [3820, 3969] {
        l.advance(at);
        assert!(l.take_action_events().iter().all(|e| e.actor_id.0 != 2));
    }
    l.advance(3970);
    let recovered = l.take_action_events();
    assert_eq!(recovered.len(), 1);
    assert_eq!((recovered[0].actor_id.0, recovered[0].state), (2, 2));
    l.advance(4100);
    assert!(l.take_action_events().iter().all(|e| e.actor_id.0 != 2));
}

#[test]
fn weapon_contact_parries_without_damage_and_can_remain_held() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
    match l.take_combat_events().pop().unwrap().effect {
        CombatEffect::Contact {
            parried,
            damage,
            lives,
            ..
        } => {
            assert!(parried);
            assert_eq!(damage, 0);
            assert_eq!(lives[1].health, 100);
        }
        _ => panic!("expected parry"),
    }
    assert_eq!(
        l.take_action_events()
            .iter()
            .map(|e| e.state)
            .collect::<Vec<_>>(),
        [240, 231]
    );
    l.advance(3520);
    assert_eq!(l.take_action_events()[0].state, 223);
    l.action_input("b", &m, 2, A::BlockRelease, 3521).unwrap();
    assert_eq!(l.take_action_events()[0].state, 2);
}
#[test]
fn directional_guard_resolves_body_contact_once_for_every_guard_and_facing() {
    let attacks = [
        (A::AttackUp, 238),
        (A::AttackRight, 244),
        (A::AttackDown, 250),
        (A::AttackLeft, 256),
    ];
    let guards = [A::BlockUp, A::BlockRight, A::BlockDown, A::BlockLeft];
    for (attack_dir, (attack, state)) in attacks.into_iter().enumerate() {
        for (guard_dir, guard) in guards.into_iter().enumerate() {
            for heading in [
                180, -180, 121, 119, 90, 61, 59, 0, -59, -61, -90, -119, -121,
            ] {
                let (mut l, m) = scene();
                pose(&mut l, &m, 1, 3000);
                l.pose_input(
                    "b",
                    &m,
                    2,
                    Pose {
                        position: [1.2, 0., 0.5],
                        heading,
                        forward_back: 0,
                        left_right: 0,
                        running: false,
                        motion: None,
                    },
                    3000,
                )
                .unwrap();
                l.action_input("b", &m, 1, guard, 3000).unwrap();
                l.advance(3000);
                l.action_input("a", &m, 1, attack, 3000).unwrap();
                l.action_input("a", &m, 2, A::AttackRelease, 3001).unwrap();
                l.advance(3400);
                let action_id = l
                    .take_action_events()
                    .into_iter()
                    .rfind(|e| e.actor_id.0 == 1 && e.state == state)
                    .unwrap()
                    .action_id
                    .unwrap();
                let mut contact = report(action_id);
                contact.state = state;
                assert!(l.contact_input("a", &m, 1, contact, 3450).unwrap());
                match l.take_combat_events().pop().unwrap().effect {
                    CombatEffect::Contact {
                        parried,
                        damage,
                        lives,
                        ..
                    } => {
                        let expected = if heading.abs() >= 120 {
                            match attack_dir {
                                0 => guard_dir == 0,
                                1 => guard_dir == 3,
                                2 => guard_dir == 2,
                                3 => guard_dir == 1,
                                _ => unreachable!(),
                            }
                        } else if heading.abs() > 60 {
                            let side = if heading > 0 { 1 } else { 3 };
                            attack_dir == side && guard_dir == side
                        } else {
                            false
                        };
                        assert_eq!(
                            parried, expected,
                            "attack={attack:?}, guard={guard:?}, heading={heading}"
                        );
                        assert_eq!(damage, if expected { 0 } else { 20 });
                        assert_eq!(lives[1].health, if expected { 100 } else { 80 });
                        let mut later = report(action_id);
                        later.state = state;
                        later.weapon_contact = true;
                        later.elapsed_ms += 1;
                        assert!(!l.contact_input("a", &m, 2, later, 3451).unwrap());
                        assert!(l.take_combat_events().is_empty());
                    }
                    _ => panic!("expected contact"),
                }
            }
        }
    }
}

#[test]
fn misses_self_unknown_targets_invalid_vectors_and_expired_reports_cannot_damage() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut cases = vec![];
    for target in [1, 3] {
        let mut r = report(seq);
        r.target_actor_id = target;
        cases.push(r);
    }
    let mut r = report(seq);
    r.normal = [0.; 3];
    cases.push(r);
    let mut r = report(seq);
    r.elapsed_ms = 600;
    cases.push(r);
    let mut r = report(seq);
    r.position = [500., 0., 0.];
    cases.push(r);
    let mut r = report(seq);
    r.state = 235;
    cases.push(r);
    for (i, r) in cases.into_iter().enumerate() {
        assert!(!l.contact_input("a", &m, i as u32 + 1, r, 3420).unwrap());
    }
    assert!(!l.contact_input("a", &m, 10, report(seq), 6000).unwrap());
    assert!(l.take_combat_events().is_empty());
}
#[test]
fn five_hits_kill_then_server_waits_for_respawn_readiness_and_play() {
    let (mut l, m) = scene();
    let mut last = 0;
    for n in 0..5 {
        let now = 3000 + n * 3000;
        l.advance(now);
        pose(&mut l, &m, n as u32 + 1, now);
        let seq = strike(&mut l, &m, n as u32 * 2 + 1, now);
        last = now + 420;
        assert!(
            l.contact_input("a", &m, n as u32 + 1, report(seq), last)
                .unwrap()
        );
        l.advance(now + 1100);
        l.advance(now + 2000);
        l.take_action_events();
    }
    let events = l.take_combat_events();
    match &events.last().unwrap().effect {
        CombatEffect::Contact { lives, .. } => {
            assert_eq!(lives[1].health, 0);
            assert_eq!(lives[0].kills, 1);
            assert_eq!(lives[1].deaths, 1);
        }
        _ => panic!("death expected"),
    }
    assert_eq!(l.respawn("b", &m, last + 7999), Err(ErrorCode::WrongPhase));
    l.advance(last + 8000);
    assert!(matches!(
        l.take_combat_events()[0].effect,
        CombatEffect::RespawnReady { .. }
    ));
    l.respawn("b", &m, last + 8001).unwrap();
    assert!(matches!(
        l.take_combat_events()[0].effect,
        CombatEffect::Respawned { .. }
    ));
    assert_eq!(l.respawn("b", &m, last + 8002), Err(ErrorCode::WrongPhase));
    l.pose_input(
        "b",
        &m,
        6,
        Pose {
            position: [100., 0., 0.5],
            heading: 0,
            forward_back: 0,
            left_right: 0,
            running: false,
            motion: None,
        },
        last + 8003,
    )
    .unwrap();
}

#[test]
fn reversed_ground_normals_do_not_rebound_but_ceilings_and_walls_do() {
    for (normal, height, rebound) in [
        ([0., 0., -1.], 0.5, false),
        ([0., 0., 1.], 2.0, true),
        ([-1., 0., 0.], 1.5, true),
    ] {
        let (mut l, m) = scene();
        pose(&mut l, &m, 1, 3000);
        let seq = strike(&mut l, &m, 1, 3000);
        let mut contact = report(seq);
        contact.target_actor_id = 0;
        contact.normal = normal;
        contact.position[2] = height;
        assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
        let reactions = l.take_action_events();
        assert_eq!(reactions.last().map(|e| e.state), rebound.then_some(466));
        match l.take_combat_events().pop().unwrap().effect {
            CombatEffect::Contact { damage, lives, .. } => {
                assert_eq!(damage, 0);
                assert!(lives.iter().all(|life| life.health == 100));
            }
            _ => panic!("expected terrain contact"),
        }
    }
}

#[test]
fn releasing_guard_during_parry_feedback_returns_to_idle() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
    l.take_action_events();
    l.action_input("b", &m, 2, A::BlockRelease, 3450).unwrap();
    l.advance(3520);
    assert_eq!(l.take_action_events()[0].state, 2);
}

#[test]
fn weapon_contact_can_only_parry_and_does_not_consume_a_later_body_hit_when_rejected() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(!l.contact_input("a", &m, 1, contact.clone(), 3420).unwrap());
    l.action_input("b", &m, 1, A::BlockDown, 3420).unwrap();
    l.advance(3420);
    assert!(!l.contact_input("a", &m, 2, contact, 3430).unwrap());
    assert!(l.contact_input("a", &m, 3, report(seq), 3440).unwrap());
    match l.take_combat_events().pop().unwrap().effect {
        CombatEffect::Contact { damage, .. } => assert_eq!(damage, 20),
        _ => panic!("body damage expected"),
    }

    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
    assert!(!l.contact_input("a", &m, 2, report(seq), 3430).unwrap());
    match l.take_combat_events().pop().unwrap().effect {
        CombatEffect::Contact {
            parried, damage, ..
        } => {
            assert!(parried);
            assert_eq!(damage, 0);
        }
        _ => panic!("weapon parry expected"),
    }
}

#[test]
fn teammates_cannot_damage_each_other_but_a_second_opponent_can_be_hit() {
    let mut l = Lobby::default();
    let r = l.create("a", "four", 1003).unwrap();
    for account in ["b", "c", "d"] {
        l.join(account, &r.room_id).unwrap();
        l.ready(account, true).unwrap();
    }
    // Joining invalidates previous ready flags.
    for account in ["b", "c", "d"] {
        l.ready(account, true).unwrap();
    }
    let m = l.start("a", 0).unwrap().current_match.unwrap().match_id;
    for account in ["a", "b", "c", "d"] {
        l.loaded(account, &m, 0).unwrap();
    }
    l.advance(3000);
    for (account, x, yaw) in [
        ("a", 0., 0),
        ("b", 1.2, 180),
        ("c", 1.0, 180),
        ("d", 1.3, 180),
    ] {
        l.pose_input(
            account,
            &m,
            1,
            Pose {
                position: [x, 0., 0.5],
                heading: yaw,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
            3000,
        )
        .unwrap();
    }
    let action = strike(&mut l, &m, 1, 3000);
    let mut hit = report(action);
    hit.target_actor_id = 3;
    assert!(!l.contact_input("a", &m, 1, hit, 3420).unwrap());
    let mut hit = report(action);
    hit.target_actor_id = 4;
    assert!(l.contact_input("a", &m, 2, hit, 3421).unwrap());
    let effects = l.take_combat_events();
    let CombatEffect::Contact { lives, target, .. } = &effects[0].effect else {
        panic!("contact expected");
    };
    assert_eq!(target.unwrap().0, 4);
    assert_eq!(lives[2].health, 100);
    assert_eq!(lives[3].health, 80);
    assert!(l.contact_input("a", &m, 3, report(action), 3422).unwrap());
    assert!(!l.contact_input("a", &m, 4, report(action), 3423).unwrap());
    let mut duplicate = report(action);
    duplicate.target_actor_id = 4;
    assert!(!l.contact_input("a", &m, 5, duplicate, 3424).unwrap());
    let effects = l.take_combat_events();
    assert_eq!(effects.len(), 1);
    let reactions = l.take_action_events();
    for target in [2, 4] {
        assert_eq!(
            reactions
                .iter()
                .filter(|e| e.actor_id.0 == target && e.state == 35)
                .count(),
            1
        );
    }
}

#[test]
fn body_regions_and_closing_speed_match_local_rules() {
    for (height, speed, expected, part) in [
        (1.5, 0., 30, "head"),
        (0.6, 0., 20, "body"),
        (0.59, 0., 15, "legs"),
        (1.81, 0., 20, "body"),
        (1., 4., 24, "body"),
        (1., 10., 30, "body"),
        (1., 20., 30, "body"),
        (1., -4., 20, "body"),
    ] {
        let (mut l, m) = scene();
        let seq = strike(&mut l, &m, 1, 3000);
        pose(&mut l, &m, 1, 3320);
        l.pose_input(
            "a",
            &m,
            2,
            Pose {
                position: [speed * 0.05, 0., 0.5],
                heading: 0,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
            3370,
        )
        .unwrap();
        l.pose_input(
            "b",
            &m,
            2,
            Pose {
                position: [1.2, 0., 0.5],
                heading: 180,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
            3370,
        )
        .unwrap();
        let mut contact = report(seq);
        contact.position[2] = 0.5 + height;
        assert!(l.contact_input("a", &m, 1, contact, 3420).unwrap());
        let value = serde_json::to_value(l.take_combat_events().pop().unwrap()).unwrap();
        assert_eq!(value["effect"]["damage"], expected);
        assert_eq!(value["effect"]["hitPart"], part);
    }
}

#[test]
fn hurt_buffers_a_released_followup_until_recovery() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    let seq = strike(&mut l, &m, 1, 3000);
    l.contact_input("a", &m, 1, report(seq), 3420).unwrap();
    l.take_action_events();
    l.action_input("b", &m, 1, A::AttackRight, 3421).unwrap();
    l.action_input("b", &m, 2, A::AttackRelease, 3422).unwrap();
    assert!(l.take_action_events().is_empty());
    l.advance(3969);
    assert!(l.take_action_events().is_empty());
    l.advance(3970);
    assert_eq!(l.take_action_events().last().unwrap().state, 241);
    l.advance(4330);
    assert!(
        l.take_action_events()
            .iter()
            .any(|e| e.actor_id.0 == 2 && e.state == 244)
    );
}

#[test]
fn dash_requires_running_and_uses_its_contact_window_and_damage() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("a", &m, 1, A::DashAttack, 3000).unwrap();
    assert!(l.take_action_events().is_empty());
    l.pose_input(
        "a",
        &m,
        2,
        Pose {
            position: [0., 0., 0.5],
            heading: 0,
            forward_back: -1,
            left_right: 0,
            running: true,
            motion: None,
        },
        3001,
    )
    .unwrap();
    l.action_input("a", &m, 2, A::DashAttack, 3001).unwrap();
    let event = l.take_action_events().pop().unwrap();
    assert_eq!((event.state, event.duration_ms), (439, 2033));
    let mut contact = report(event.action_id.unwrap());
    contact.state = 439;
    contact.elapsed_ms = 1134;
    assert!(!l.contact_input("a", &m, 1, contact.clone(), 4135).unwrap());
    contact.elapsed_ms = 1133;
    // Refresh both positions so the long dash still has valid contact evidence.
    pose(&mut l, &m, 3, 4134);
    assert!(l.contact_input("a", &m, 2, contact, 4134).unwrap());
    assert!(matches!(
        l.take_combat_events().pop().unwrap().effect,
        CombatEffect::Contact { damage: 40, .. }
    ));
}

#[test]
fn last_kill_is_retained_before_the_match_enters_settlement() {
    let rules = t7_game_server::limits::RoomRules::default()
        .with_round_limits(1200000, 1)
        .unwrap();
    let (mut l, m) = scene_with_rules(rules);
    for n in 0..5 {
        let now = 3000 + n * 1600;
        pose(&mut l, &m, n as u32 + 1, now);
        let seq = strike(&mut l, &m, n as u32 * 2 + 1, now);
        assert!(
            l.contact_input("a", &m, n as u32 + 1, report(seq), now + 420)
                .unwrap()
        );
        if n < 4 {
            l.take_combat_events();
            l.advance(now + 1500);
        }
    }
    let changed = l.advance(9821).pop().unwrap();
    assert_eq!(changed.phase, t7_game_server::room::Phase::Settling);
    let result = changed.result.unwrap();
    assert_eq!((result.reason, result.result), ("kill-target", 1));
    assert_eq!(result.lives[0].kills, 1);
    assert!(
        matches!(l.take_combat_events().pop().unwrap().effect, CombatEffect::Contact { lives, .. } if lives[1].health == 0)
    );
    assert_eq!(l.respawn("b", &m, 18000), Err(ErrorCode::WrongPhase));
}

#[test]
fn predicted_contact_advances_due_attack_without_waiting_for_periodic_tick() {
    let (mut lobby, match_id) = scene();
    pose(&mut lobby, &match_id, 1, 3000);
    lobby
        .action_input("a", &match_id, 1, A::AttackUp, 3000)
        .unwrap();
    lobby
        .action_input("a", &match_id, 2, A::AttackRelease, 3001)
        .unwrap();
    // No lobby.advance: the local predictor has already reached the strike phase.
    assert!(
        lobby
            .contact_input("a", &match_id, 1, report(1), 3410)
            .unwrap()
    );
    assert!(
        lobby
            .take_action_events()
            .iter()
            .any(|e| e.actor_id.0 == 1 && e.state == 238)
    );
}

#[test]
fn predicted_contact_cannot_advance_attack_before_its_deadline() {
    let (mut lobby, match_id) = scene();
    pose(&mut lobby, &match_id, 1, 3000);
    lobby
        .action_input("a", &match_id, 1, A::AttackUp, 3000)
        .unwrap();
    lobby
        .action_input("a", &match_id, 2, A::AttackRelease, 3001)
        .unwrap();
    assert!(
        !lobby
            .contact_input("a", &match_id, 1, report(1), 3359)
            .unwrap()
    );
    assert!(lobby.take_combat_events().is_empty());
}

#[test]
fn delayed_contact_uses_guard_and_heading_at_contact_not_at_arrival() {
    let (mut lobby, match_id) = scene();
    pose(&mut lobby, &match_id, 1, 3000);
    lobby
        .action_input("b", &match_id, 1, A::BlockUp, 3000)
        .unwrap();
    lobby.advance(3000);
    let seq = strike(&mut lobby, &match_id, 1, 3000);
    // Contact occurred at 3410; defender releases and turns before its report arrives.
    lobby
        .action_input("b", &match_id, 2, A::BlockRelease, 3450)
        .unwrap();
    lobby
        .pose_input(
            "b",
            &match_id,
            2,
            Pose {
                position: [1.2, 0., 0.5],
                heading: 0,
                forward_back: 0,
                left_right: 0,
                running: false,
                motion: None,
            },
            3460,
        )
        .unwrap();
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(
        lobby
            .contact_input("a", &match_id, 1, contact, 3600)
            .unwrap()
    );
    assert!(matches!(
        lobby.take_combat_events().pop().unwrap().effect,
        CombatEffect::Contact {
            parried: true,
            damage: 0,
            ..
        }
    ));
}

#[test]
fn future_guard_cannot_parry_a_past_contact() {
    let (mut lobby, match_id) = scene();
    pose(&mut lobby, &match_id, 1, 3000);
    let seq = strike(&mut lobby, &match_id, 1, 3000);
    lobby
        .action_input("b", &match_id, 1, A::BlockUp, 3450)
        .unwrap();
    lobby.advance(3450);
    assert!(
        lobby
            .contact_input("a", &match_id, 1, report(seq), 3600)
            .unwrap()
    );
    assert!(matches!(
        lobby.take_combat_events().pop().unwrap().effect,
        CombatEffect::Contact {
            parried: false,
            damage: 20,
            ..
        }
    ));
}

#[test]
fn contact_rewind_window_is_bounded_even_while_attack_sample_is_retained() {
    let (mut lobby, match_id) = scene();
    pose(&mut lobby, &match_id, 1, 3000);
    let seq = strike(&mut lobby, &match_id, 1, 3000);
    assert!(
        !lobby
            .contact_input("a", &match_id, 1, report(seq), 4011)
            .unwrap()
    );
    assert!(lobby.take_combat_events().is_empty());
}

#[test]
fn mismatched_body_contact_damages_and_cannot_be_replaced_by_a_later_weapon_report() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.action_input("b", &m, 1, A::BlockLeft, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    assert!(l.contact_input("a", &m, 1, report(seq), 3420).unwrap());
    let mut later = report(seq);
    later.weapon_contact = true;
    later.elapsed_ms += 1;
    assert!(!l.contact_input("a", &m, 2, later, 3421).unwrap());
    assert!(matches!(
        l.take_combat_events().pop().unwrap().effect,
        CombatEffect::Contact {
            parried: false,
            damage: 20,
            ..
        }
    ));
}

#[test]
fn weapon_probe_does_not_bypass_guard_direction() {
    for guard in [A::BlockUp, A::BlockRight, A::BlockDown, A::BlockLeft] {
        let (mut l, m) = scene();
        pose(&mut l, &m, 1, 3000);
        l.action_input("b", &m, 1, guard, 3000).unwrap();
        l.advance(3000);
        let seq = strike(&mut l, &m, 1, 3000);
        let mut contact = report(seq);
        contact.weapon_contact = true;
        let matched = guard == A::BlockUp;
        assert_eq!(l.contact_input("a", &m, 1, contact, 3420).unwrap(), matched);
        if !matched {
            assert!(l.take_combat_events().is_empty());
            assert!(l.contact_input("a", &m, 2, report(seq), 3421).unwrap());
        }
        assert!(matches!(l.take_combat_events().pop().unwrap().effect,
            CombatEffect::Contact { parried, damage, .. }
                if parried == matched && damage == if matched { 0 } else { 20 }));
    }
}

#[test]
fn weapon_report_cannot_parry_when_defender_faces_away() {
    let (mut l, m) = scene();
    pose(&mut l, &m, 1, 3000);
    l.pose_input(
        "b",
        &m,
        2,
        Pose {
            position: [1.2, 0., 0.5],
            heading: 0,
            forward_back: 0,
            left_right: 0,
            running: false,
            motion: None,
        },
        3000,
    )
    .unwrap();
    l.action_input("b", &m, 1, A::BlockUp, 3000).unwrap();
    l.advance(3000);
    let seq = strike(&mut l, &m, 1, 3000);
    let mut contact = report(seq);
    contact.weapon_contact = true;
    assert!(!l.contact_input("a", &m, 1, contact, 3420).unwrap());
    assert!(l.take_combat_events().is_empty());
    assert!(l.contact_input("a", &m, 2, report(seq), 3421).unwrap());
    assert!(matches!(
        l.take_combat_events().pop().unwrap().effect,
        CombatEffect::Contact {
            parried: false,
            damage: 20,
            ..
        }
    ));
}

#[test]
fn delayed_contact_uses_attacker_position_and_heading_at_contact() {
    for (later_position, later_heading) in [([7., 0., 0.5], 0), ([0., 0., 0.5], 180)] {
        for weapon_contact in [false, true] {
            let (mut lobby, match_id) = scene();
            pose(&mut lobby, &match_id, 1, 3000);
            lobby
                .action_input("b", &match_id, 1, A::BlockUp, 3000)
                .unwrap();
            let seq = strike(&mut lobby, &match_id, 1, 3000);
            // Contact is at 3410. Later movement must not invalidate its reach or facing.
            lobby
                .pose_input(
                    "a",
                    &match_id,
                    2,
                    Pose {
                        position: later_position,
                        heading: later_heading,
                        forward_back: 0,
                        left_right: 0,
                        running: false,
                        motion: None,
                    },
                    3500,
                )
                .unwrap();
            let mut contact = report(seq);
            contact.weapon_contact = weapon_contact;
            assert!(
                lobby
                    .contact_input("a", &match_id, 1, contact, 3600)
                    .unwrap()
            );
            assert!(matches!(
                lobby.take_combat_events().pop().unwrap().effect,
                CombatEffect::Contact { parried, damage, .. }
                    if parried && damage == 0
            ));
        }
    }
}

#[test]
fn a_future_attacker_pose_cannot_authorize_a_past_contact() {
    let (mut lobby, match_id) = scene();
    let seq = strike(&mut lobby, &match_id, 1, 3000);
    // There is no attacker pose before the contact at 3410.
    pose(&mut lobby, &match_id, 1, 3450);
    assert!(
        !lobby
            .contact_input("a", &match_id, 1, report(seq), 3500)
            .unwrap()
    );
    assert!(lobby.take_combat_events().is_empty());
}

#[test]
fn contact_damage_uses_historical_closing_speed_despite_later_motion() {
    for arrival in [3410, 3500] {
        let (mut lobby, match_id) = scene();
        let seq = strike(&mut lobby, &match_id, 1, 3000);
        pose(&mut lobby, &match_id, 1, 3320);
        for (input_seq, at, attacker_x) in [(2, 3370, 0.2), (3, 3450, 0.2)] {
            if at > arrival {
                continue;
            }
            for (account, x, heading) in [("a", attacker_x, 0), ("b", 1.2, 180)] {
                lobby
                    .pose_input(
                        account,
                        &match_id,
                        input_seq,
                        Pose {
                            position: [x, 0., 0.5],
                            heading,
                            forward_back: 0,
                            left_right: 0,
                            running: false,
                            motion: None,
                        },
                        at,
                    )
                    .unwrap();
            }
        }
        assert!(
            lobby
                .contact_input("a", &match_id, 1, report(seq), arrival)
                .unwrap()
        );
        assert!(matches!(
            lobby.take_combat_events().pop().unwrap().effect,
            CombatEffect::Contact { damage: 24, .. }
        ));
    }
}
