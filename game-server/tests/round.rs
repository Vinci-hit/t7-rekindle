use t7_game_server::{
    battle::ActionIntent, limits::RoomRules, lobby::Lobby, maps::MapCatalog, protocol::ErrorCode,
    room::Phase,
};

fn scene(duration: u64) -> (Lobby, String, String) {
    let rules = RoomRules::new(2)
        .unwrap()
        .with_round_limits(duration, 1)
        .unwrap();
    let mut lobby = Lobby::with_rules(MapCatalog::default(), rules);
    let room = lobby.create("a", "round", 1003).unwrap();
    let id = lobby.start("a", 0).unwrap().current_match.unwrap().match_id;
    lobby.loaded("a", &id, 0).unwrap();
    lobby.advance(3000);
    (lobby, room.room_id, id)
}

#[test]
fn timeout_freezes_scores_rejects_inputs_and_resets_for_a_second_match() {
    let (mut lobby, room, old) = scene(1000);
    assert!(lobby.advance(3999).is_empty());
    let end = lobby.advance(4000).pop().unwrap();
    assert_eq!(end.phase, Phase::Settling);
    let result = end.result.unwrap();
    assert_eq!((result.reason, result.result), ("timeout", 3));
    assert_eq!(
        lobby.action_input("a", &old, 1, ActionIntent::AttackUp, 4001),
        Err(ErrorCode::WrongPhase)
    );
    assert!(lobby.advance(9999).is_empty());
    let waiting = lobby.advance(10000).pop().unwrap();
    assert_eq!(waiting.phase, Phase::Waiting);
    assert!(waiting.result.is_none() && waiting.current_match.is_none());
    assert!(lobby.take_action_events().is_empty());
    let new = lobby
        .start("a", 10001)
        .unwrap()
        .current_match
        .unwrap()
        .match_id;
    assert_ne!(new, old);
    lobby.loaded("a", &new, 10002).unwrap();
    lobby.advance(13002);
    assert_eq!(lobby.snapshot(&room).unwrap().phase, Phase::Running);
    assert_eq!(
        lobby.action_input("a", &old, 1, ActionIntent::AttackUp, 13003),
        Err(ErrorCode::StaleMatch)
    );
    lobby
        .action_input("a", &new, 1, ActionIntent::AttackUp, 13003)
        .unwrap();
    let event = lobby.take_action_events().pop().unwrap();
    assert_eq!(
        (event.event_seq, event.action_seq, event.weapon_id),
        (1, 2, 1030411)
    );
}

#[test]
fn round_limits_reject_invalid_values() {
    for (duration, kills) in [(0, 1), (86_400_001, 1), (1000, 0), (1000, u32::MAX)] {
        assert!(
            RoomRules::default()
                .with_round_limits(duration, kills)
                .is_err()
        );
    }
}
