use serde::{Deserialize, Serialize};

use crate::maps::LobbyMap;
use crate::player::PlayerProfile;
use crate::protocol::ErrorCode;

pub const MAP_ID: u32 = 1028;

pub const CAPACITY: usize = crate::limits::ROOM_CAPACITY;
pub const LOAD_TIMEOUT_MS: u64 = 60_000;
pub const COUNTDOWN_MS: u64 = 3_000;

/// A directory key for this server lifetime, distinct from an account or actor.
/// JSON uses a decimal string so gateways can preserve every u64 bit.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(try_from = "String", into = "String")]
pub struct RoomGuid(u64);

impl TryFrom<String> for RoomGuid {
    type Error = &'static str;

    fn try_from(value: String) -> Result<Self, Self::Error> {
        let number = value.parse::<u64>().map_err(|_| "invalid room guid")?;
        if number == 0 || number.to_string() != value {
            return Err("invalid room guid");
        }
        Ok(Self(number))
    }
}

impl From<RoomGuid> for String {
    fn from(value: RoomGuid) -> Self {
        value.0.to_string()
    }
}

impl RoomGuid {
    pub fn value(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomAddress {
    pub room_guid: RoomGuid,
    pub room_number: u32,
    pub channel_id: u32,
}

impl RoomAddress {
    pub(crate) fn new(number: u32, channel_id: u32) -> Self {
        Self {
            room_guid: RoomGuid(u64::from(number)),
            room_number: number,
            channel_id,
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum Phase {
    Waiting,
    Loading,
    Countdown,
    Running,
    Settling,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Member {
    pub account_id: String,
    #[serde(flatten)]
    pub profile: PlayerProfile,
    pub actor_id: u32,
    pub camp: u8,
    pub ready: bool,
    pub loaded: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub loadout: Option<crate::loadout::Loadout>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Match {
    pub match_id: String,
    pub load_deadline_ms: u64,
    pub starts_at_ms: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoomSnapshot {
    pub room_id: String,
    #[serde(flatten)]
    pub address: RoomAddress,
    pub name: String,
    pub map_id: u32,
    pub capacity: usize,
    pub mode: GameMode,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub pve: Option<crate::pve::Snapshot>,
    pub revision: u64,
    pub owner_account_id: String,
    pub phase: Phase,
    pub members: Vec<Member>,
    pub current_match: Option<Match>,
    pub duration_ms: u64,
    pub kill_target: u32,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub result: Option<crate::round::RoundResult>,
    pub last_abort_reason: Option<&'static str>,
}

/// Current authority only; reconnect never replays expired contacts or reactions.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Recovery {
    pub action_seq: u64,
    pub combat_seq: u64,
    pub pose_seq: u64,
    pub pve_seq: u64,
    pub actions: Vec<crate::battle::ActionEvent>,
    pub poses: Vec<crate::motion::PoseEvent>,
    pub lives: Vec<crate::combat::Health>,
    pub respawn_ready: Vec<u32>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum GameMode {
    Pvp,
    Pve,
}

#[derive(Debug)]
struct RoomData {
    pve: Option<std::sync::Arc<crate::pve::Scenario>>,
    room_id: String,
    map: LobbyMap,
    rules: crate::limits::RoomRules,
    address: RoomAddress,
    name: String,
    revision: u64,
    owner_account_id: String,
    members: Vec<Member>,
    next_actor_id: u32,
    last_abort_reason: Option<&'static str>,
}

#[derive(Debug)]
pub struct Waiting;

#[derive(Debug)]
pub struct Loading {
    current_match: Match,
}

#[derive(Debug)]
pub struct Countdown {
    current_match: Match,
    starts_at_ms: u64,
}

#[derive(Debug)]
pub struct Running {
    current_match: Match,
    battle: Box<crate::battle::Battle>,
    motion: Box<crate::motion::Motion>,
    combat: Box<crate::combat::Combat>,
    pve: Option<Box<crate::pve::Pve>>,
}

#[derive(Debug)]
pub struct Settling {
    running: Box<Running>,
    result: Box<crate::round::RoundResult>,
    return_at: u64,
}

/// The common data is composed with phase-specific data. Only a waiting room
/// exposes preparation operations; transitions consume the previous room.
///
/// ```compile_fail
/// use t7_game_server::room::{Loading, Room};
/// fn change_ready(room: &mut Room<Loading>) {
///     room.ready("player-a", true).unwrap();
/// }
/// ```
#[derive(Debug)]
pub struct Room<S> {
    data: Box<RoomData>,
    state: S,
}

impl<S> Room<S> {
    fn snapshot(&self, phase: Phase, current_match: Option<Match>) -> RoomSnapshot {
        RoomSnapshot {
            room_id: self.data.room_id.clone(),
            address: self.data.address,
            name: self.data.name.clone(),
            map_id: self.data.map.value(),
            capacity: self.data.rules.capacity(),
            mode: if self.data.pve.is_some() {
                GameMode::Pve
            } else {
                GameMode::Pvp
            },
            pve: self.data.pve.as_ref().map(|scenario| {
                crate::pve::Pve::new(
                    scenario.clone(),
                    self.data.room_id.clone(),
                    String::new(),
                    0,
                )
                .snapshot()
            }),
            revision: self.data.revision,
            owner_account_id: self.data.owner_account_id.clone(),
            phase,
            members: self.data.members.clone(),
            current_match,
            duration_ms: self.data.rules.duration_ms(),
            kill_target: self.data.rules.kill_target(),
            result: None,
            last_abort_reason: self.data.last_abort_reason,
        }
    }

    fn member_mut(&mut self, account: &str) -> Result<&mut Member, ErrorCode> {
        self.data
            .members
            .iter_mut()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)
    }

    fn into_waiting(mut self, reason: Option<&'static str>) -> Room<Waiting> {
        self.data.last_abort_reason = reason;
        for member in &mut self.data.members {
            member.ready = false;
            member.loaded = false;
        }
        self.data.revision += 1;
        Room {
            data: self.data,
            state: Waiting,
        }
    }
}

impl Room<Waiting> {
    pub(crate) fn new(
        name: String,
        account: &str,
        profile: PlayerProfile,
        address: RoomAddress,
        map: LobbyMap,
        rules: crate::limits::RoomRules,
    ) -> Self {
        Self {
            data: Box::new(RoomData {
                room_id: uuid::Uuid::new_v4().to_string(),
                pve: None,
                address,
                map,
                rules,
                name,
                revision: 1,
                owner_account_id: account.to_owned(),
                members: vec![Member {
                    account_id: account.to_owned(),
                    profile,
                    actor_id: 1,
                    camp: 1,
                    ready: false,
                    loaded: false,
                    loadout: None,
                }],
                next_actor_id: 2,
                last_abort_reason: None,
            }),
            state: Waiting,
        }
    }

    pub(crate) fn configure_pve(&mut self, scenario: std::sync::Arc<crate::pve::Scenario>) {
        self.data.pve = Some(scenario);
    }

    pub(crate) fn join(&mut self, account: &str, profile: PlayerProfile) -> Result<(), ErrorCode> {
        if self.data.members.iter().any(|m| m.account_id == account) {
            return Err(ErrorCode::AlreadyInRoom);
        }
        if self.data.members.len() >= self.data.rules.capacity() {
            return Err(ErrorCode::RoomFull);
        }
        let next = self
            .data
            .next_actor_id
            .checked_add(1)
            .ok_or(ErrorCode::RoomFull)?;
        let camp = if self.data.pve.is_some() {
            1
        } else if self.data.members.iter().filter(|m| m.camp == 1).count()
            > self.data.members.iter().filter(|m| m.camp == 2).count()
        {
            2
        } else {
            1
        };
        self.data.members.push(Member {
            account_id: account.to_owned(),
            profile,
            actor_id: self.data.next_actor_id,
            camp,
            ready: false,
            loaded: false,
            loadout: None,
        });
        self.data.next_actor_id = next;
        self.clear_ready();
        self.data.revision += 1;
        Ok(())
    }

    pub fn set_camp(&mut self, account: &str, camp: u8) -> Result<(), ErrorCode> {
        if !matches!(camp, 1 | 2) || self.data.pve.is_some() && camp != 1 {
            return Err(ErrorCode::InvalidCamp);
        }
        if self
            .data
            .members
            .iter()
            .filter(|m| m.camp == camp && m.account_id != account)
            .count()
            >= if self.data.pve.is_some() {
                self.data.rules.capacity()
            } else {
                self.data.rules.team_capacity()
            }
        {
            return Err(ErrorCode::InvalidTeams);
        }
        let member = self.member_mut(account)?;
        if member.camp != camp {
            member.camp = camp;
            self.clear_ready();
            self.data.revision += 1;
        }
        Ok(())
    }

    pub fn ready(&mut self, account: &str, ready: bool) -> Result<(), ErrorCode> {
        let member = self.member_mut(account)?;
        if member.ready != ready {
            member.ready = ready;
            self.data.revision += 1;
        }
        Ok(())
    }

    pub fn start(mut self, account: &str, now_ms: u64) -> Result<Room<Loading>, (Self, ErrorCode)> {
        // Start confirms the owner's intent; only guests need a separate ready flag.
        let error = if self.data.owner_account_id != account {
            Some(ErrorCode::NotOwner)
        } else if self.data.pve.is_none() && !matches!(self.data.map.value(), 1003 | MAP_ID) {
            Some(ErrorCode::UnsupportedMap)
        } else if self
            .data
            .members
            .iter()
            .any(|member| member.account_id != account && !member.ready)
        {
            Some(ErrorCode::NotReady)
        } else if self.data.pve.is_none()
            && self.data.members.len() > 1
            && !self
                .data
                .members
                .iter()
                .any(|m| m.camp != self.data.members[0].camp)
        {
            Some(ErrorCode::InvalidTeams)
        } else {
            None
        };
        if let Some(error) = error {
            return Err((self, error));
        }
        self.data.last_abort_reason = None;
        self.data.revision += 1;
        Ok(Room {
            data: self.data,
            state: Loading {
                current_match: Match {
                    match_id: uuid::Uuid::new_v4().to_string(),
                    load_deadline_ms: now_ms.saturating_add(LOAD_TIMEOUT_MS),
                    starts_at_ms: None,
                },
            },
        })
    }

    fn clear_ready(&mut self) {
        for member in &mut self.data.members {
            member.ready = false;
            member.loaded = false;
        }
    }
}

impl Room<Loading> {
    pub fn loaded(&mut self, account: &str, match_id: &str, now_ms: u64) -> Result<(), ErrorCode> {
        if self.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        if now_ms >= self.state.current_match.load_deadline_ms {
            return Err(ErrorCode::LoadTimeout);
        }
        let member = self.member_mut(account)?;
        if !member.loaded {
            member.loaded = true;
            self.data.revision += 1;
        }
        Ok(())
    }

    pub fn finish_loading(mut self, now_ms: u64) -> Result<Room<Countdown>, Self> {
        if now_ms >= self.state.current_match.load_deadline_ms
            || !self.data.members.iter().all(|m| m.loaded)
        {
            return Err(self);
        }
        let starts_at_ms = now_ms.saturating_add(COUNTDOWN_MS);
        self.state.current_match.starts_at_ms = Some(starts_at_ms);
        self.data.revision += 1;
        Ok(Room {
            data: self.data,
            state: Countdown {
                current_match: self.state.current_match,
                starts_at_ms,
            },
        })
    }
}

impl Room<Countdown> {
    pub fn begin(mut self, now_ms: u64) -> Result<Room<Running>, Self> {
        if now_ms < self.state.starts_at_ms {
            return Err(self);
        }
        self.data.revision += 1;
        let mut battle = crate::battle::Battle::new(
            self.data.room_id.clone(),
            self.state.current_match.match_id.clone(),
            self.data.members.iter().map(|m| m.actor_id),
        );
        for member in &self.data.members {
            if let Some(loadout) = &member.loadout {
                battle.configure(member.actor_id, loadout.clone());
            }
        }
        let combat = crate::combat::Combat::new(
            self.data.room_id.clone(),
            self.state.current_match.match_id.clone(),
            self.data.members.iter().map(|m| (m.actor_id, m.camp)),
        );
        let motion = crate::motion::Motion::new(
            self.data.room_id.clone(),
            self.state.current_match.match_id.clone(),
            self.data.members.iter().map(|m| m.actor_id),
        );
        let pve = self.data.pve.as_ref().map(|scenario| {
            Box::new(crate::pve::Pve::new(
                scenario.clone(),
                self.data.room_id.clone(),
                self.state.current_match.match_id.clone(),
                now_ms,
            ))
        });
        Ok(Room {
            data: self.data,
            state: Running {
                pve,
                battle: Box::new(battle),
                motion: Box::new(motion),
                combat: Box::new(combat),
                current_match: self.state.current_match,
            },
        })
    }
}

// The directory must store rooms at different phases. This enum owns a typed
// room; it does not allow constructing a phase separately from its data.
pub(crate) enum RoomState {
    Waiting(Room<Waiting>),
    Loading(Room<Loading>),
    Countdown(Room<Countdown>),
    Running(Room<Running>),
    Settling(Room<Settling>),
}

impl RoomState {
    pub(crate) fn members(&self) -> &[Member] {
        match self {
            Self::Waiting(room) => &room.data.members,
            Self::Loading(room) => &room.data.members,
            Self::Countdown(room) => &room.data.members,
            Self::Running(room) => &room.data.members,
            Self::Settling(room) => &room.data.members,
        }
    }

    pub fn weapon(&self, account: &str) -> Result<i32, ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        room.state
            .battle
            .weapon(actor.actor_id)
            .ok_or(ErrorCode::UnknownActor)
    }
    pub fn pose_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        pose: crate::motion::Pose,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        if !room.state.combat.alive(actor.actor_id) {
            return Ok(());
        }
        room.state
            .motion
            .input(actor.actor_id, input_seq, pose, now_ms)
    }

    pub fn take_pose_events(&mut self) -> Vec<crate::motion::PoseEvent> {
        match self {
            Self::Running(room) => room.state.motion.take_events(),
            Self::Settling(room) => room.state.running.motion.take_events(),
            _ => Vec::new(),
        }
    }

    pub fn action_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        intent: crate::battle::ActionIntent,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        self.action_input_at(account, match_id, input_seq, intent, now_ms, now_ms)
    }

    pub(crate) fn action_input_at(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        intent: crate::battle::ActionIntent,
        sampled_at: u64,
        now_ms: u64,
    ) -> Result<(), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        if intent == crate::battle::ActionIntent::DashAttack
            && room
                .state
                .motion
                .latest(actor.actor_id, now_ms)
                .is_none_or(|pose| !pose.running || pose.forward_back != -1)
        {
            return Ok(());
        }
        room.state.battle.advance_input(actor.actor_id, now_ms);
        room.state
            .battle
            .input_at(actor.actor_id, input_seq, intent, sampled_at, now_ms)
    }

    pub(crate) fn reserve_contact(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        report: &crate::combat::ContactReport,
        now: u64,
    ) -> Result<(u64, Option<String>), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?
            .actor_id;
        room.state.battle.advance_input(actor, now);
        room.state.combat.reserve_input(actor, input_seq)?;
        let at = room
            .state
            .battle
            .sample_action(actor, report.action_id, report.state)
            .map(|s| s.at.saturating_add(u64::from(report.elapsed_ms)).min(now))
            .unwrap_or(now);
        let target = room
            .data
            .members
            .iter()
            .find(|m| m.actor_id == report.target_actor_id)
            .map(|m| m.account_id.clone());
        Ok((at, target))
    }

    pub(crate) fn resolve_contact(
        &mut self,
        account: &str,
        match_id: &str,
        report: crate::combat::ContactReport,
        now: u64,
        guard_deadline: u64,
    ) -> Result<bool, ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?
            .actor_id;
        room.state.combat.server_contact_before(
            actor,
            report,
            now,
            &mut room.state.battle,
            &room.state.motion,
            Some(guard_deadline),
        )
    }

    pub fn contact_input(
        &mut self,
        account: &str,
        match_id: &str,
        input_seq: u32,
        report: crate::combat::ContactReport,
        now_ms: u64,
    ) -> Result<bool, ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?
            .actor_id;
        // A predicted contact can arrive between ticks after the phase deadline.
        room.state.battle.advance_input(actor, now_ms);
        room.state.combat.input(
            actor,
            input_seq,
            report,
            now_ms,
            &mut room.state.battle,
            &room.state.motion,
        )
    }
    pub fn respawn(&mut self, account: &str, match_id: &str, now_ms: u64) -> Result<(), ErrorCode> {
        let Self::Running(room) = self else {
            return Err(ErrorCode::WrongPhase);
        };
        if room.state.current_match.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let actor = room
            .data
            .members
            .iter()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?
            .actor_id;
        room.state.combat.respawn(
            actor,
            now_ms,
            &mut room.state.battle,
            &mut room.state.motion,
        )
    }
    pub(crate) fn take_pve_events(&mut self) -> Vec<crate::pve::Event> {
        match self {
            Self::Running(room) => room
                .state
                .pve
                .as_mut()
                .map(|pve| pve.take_events())
                .unwrap_or_default(),
            Self::Settling(room) => room
                .state
                .running
                .pve
                .as_mut()
                .map(|pve| pve.take_events())
                .unwrap_or_default(),
            _ => Vec::new(),
        }
    }

    pub fn take_combat_events(&mut self) -> Vec<crate::combat::CombatEvent> {
        match self {
            Self::Running(room) => room.state.combat.take_events(),
            Self::Settling(room) => room.state.running.combat.take_events(),
            _ => Vec::new(),
        }
    }
    pub(crate) fn pause_input(&mut self, account: &str, now: u64) {
        if let Self::Running(room) = self {
            if let Some(actor) = room
                .data
                .members
                .iter()
                .find(|m| m.account_id == account)
                .map(|m| m.actor_id)
            {
                room.state.battle.pause_input(actor, now);
                room.state.motion.pause_input(actor, now);
            }
        }
    }

    pub(crate) fn recovery(&self, now: u64) -> Option<Recovery> {
        let state = match self {
            Self::Running(room) => &room.state,
            Self::Settling(room) => &room.state.running,
            _ => return None,
        };
        let (action_seq, actions) = state.battle.recovery(now);
        let (combat_seq, lives, respawn_ready) = state.combat.recovery();
        let (pose_seq, poses) = state.motion.recovery();
        Some(Recovery {
            action_seq,
            combat_seq,
            pose_seq,
            pve_seq: state.pve.as_ref().map_or(0, |p| p.event_sequence()),
            actions,
            poses,
            lives,
            respawn_ready,
        })
    }

    pub(crate) fn select_loadout(
        &mut self,
        account: &str,
        match_id: &str,
        loadout: crate::loadout::Loadout,
    ) -> Result<(), ErrorCode> {
        loadout.validate()?;
        let (data, current) = match self {
            Self::Loading(r) => (&mut r.data, &r.state.current_match),
            Self::Running(r) => {
                if r.state.current_match.match_id != match_id {
                    return Err(ErrorCode::StaleMatch);
                }
                let member = r
                    .data
                    .members
                    .iter_mut()
                    .find(|m| m.account_id == account)
                    .ok_or(ErrorCode::NotInRoom)?;
                if r.state.combat.alive(member.actor_id) {
                    return Err(ErrorCode::WrongPhase);
                }
                r.state.battle.configure(member.actor_id, loadout.clone());
                member.loadout = Some(loadout);
                r.data.revision += 1;
                return Ok(());
            }
            _ => return Err(ErrorCode::WrongPhase),
        };
        if current.match_id != match_id {
            return Err(ErrorCode::StaleMatch);
        }
        let member = data
            .members
            .iter_mut()
            .find(|m| m.account_id == account)
            .ok_or(ErrorCode::NotInRoom)?;
        if member.loaded {
            return Err(ErrorCode::WrongPhase);
        }
        member.loadout = Some(loadout);
        data.revision += 1;
        Ok(())
    }

    pub fn take_action_events(&mut self) -> Vec<crate::battle::ActionEvent> {
        match self {
            Self::Running(room) => room.state.battle.take_events(),
            Self::Settling(room) => room.state.running.battle.take_events(),
            _ => Vec::new(),
        }
    }
    pub fn snapshot(&self) -> RoomSnapshot {
        match self {
            Self::Waiting(room) => room.snapshot(Phase::Waiting, None),
            Self::Loading(room) => {
                room.snapshot(Phase::Loading, Some(room.state.current_match.clone()))
            }
            Self::Countdown(room) => {
                room.snapshot(Phase::Countdown, Some(room.state.current_match.clone()))
            }
            Self::Running(room) => {
                let mut snapshot =
                    room.snapshot(Phase::Running, Some(room.state.current_match.clone()));
                snapshot.pve = room.state.pve.as_ref().map(|pve| pve.snapshot());
                snapshot
            }
            Self::Settling(room) => {
                let mut snapshot = room.snapshot(
                    Phase::Settling,
                    Some(room.state.running.current_match.clone()),
                );
                snapshot.pve = room.state.running.pve.as_ref().map(|pve| pve.snapshot());
                snapshot.result = Some((*room.state.result).clone());
                snapshot
            }
        }
    }

    pub fn waiting_mut(&mut self) -> Result<&mut Room<Waiting>, ErrorCode> {
        match self {
            Self::Waiting(room) => Ok(room),
            _ => Err(ErrorCode::WrongPhase),
        }
    }

    pub fn start(self, account: &str, now_ms: u64) -> Result<Self, (Self, ErrorCode)> {
        match self {
            Self::Waiting(room) => room
                .start(account, now_ms)
                .map(Self::Loading)
                .map_err(|(room, error)| (Self::Waiting(room), error)),
            _ => Err((self, ErrorCode::WrongPhase)),
        }
    }

    pub fn loaded(
        self,
        account: &str,
        match_id: &str,
        now_ms: u64,
    ) -> Result<Self, (Self, ErrorCode)> {
        if self
            .snapshot()
            .current_match
            .as_ref()
            .is_none_or(|m| m.match_id != match_id)
        {
            return Err((self, ErrorCode::StaleMatch));
        }
        match self {
            Self::Loading(mut room) => {
                if let Err(error) = room.loaded(account, match_id, now_ms) {
                    return Err((Self::Loading(room), error));
                }
                Ok(match room.finish_loading(now_ms) {
                    Ok(room) => Self::Countdown(room),
                    Err(room) => Self::Loading(room),
                })
            }
            _ => Err((self, ErrorCode::WrongPhase)),
        }
    }

    pub fn leave(self, account: &str, reason: &'static str) -> Option<Self> {
        let mut room = match self {
            Self::Waiting(room) => {
                let previous = room.data.last_abort_reason;
                room.into_waiting(previous)
            }
            Self::Loading(room) => room.into_waiting(Some(reason)),
            Self::Countdown(room) => room.into_waiting(Some(reason)),
            Self::Running(room) => room.into_waiting(Some(reason)),
            Self::Settling(room) => room.into_waiting(Some(reason)),
        };
        room.data.members.retain(|m| m.account_id != account);
        let first = room.data.members.first()?;
        if room.data.owner_account_id == account {
            room.data.owner_account_id = first.account_id.clone();
        }
        Some(Self::Waiting(room))
    }

    pub fn advance(self, now_ms: u64) -> (Self, bool) {
        match self {
            Self::Loading(room) if now_ms >= room.state.current_match.load_deadline_ms => {
                (Self::Waiting(room.into_waiting(Some("loadTimeout"))), true)
            }
            Self::Countdown(room) => match room.begin(now_ms) {
                Ok(room) => (Self::Running(room), true),
                Err(room) => (Self::Countdown(room), false),
            },
            Self::Running(mut room) => {
                room.state.battle.advance(now_ms);
                if let Some(pve) = room.state.pve.as_mut() {
                    let players: Vec<_> = room.data.members.iter().map(|m| m.actor_id).collect();
                    pve.advance(
                        now_ms,
                        &players,
                        &mut room.state.battle,
                        &mut room.state.motion,
                        &mut room.state.combat,
                    );
                }
                room.state.motion.advance(now_ms);
                room.state.combat.advance(now_ms);
                let started = room
                    .state
                    .current_match
                    .starts_at_ms
                    .expect("running deadline");
                let timeout = now_ms.saturating_sub(started) >= room.data.rules.duration_ms();
                let result = if let Some(pve) = room.state.pve.as_mut() {
                    let players: Vec<_> = room.data.members.iter().map(|m| m.actor_id).collect();
                    pve.result(&players, &room.state.combat, timeout, now_ms)
                } else {
                    crate::round::finish(
                        room.state.combat.health(),
                        room.data.members.iter().map(|m| (m.actor_id, m.camp)),
                        timeout,
                        room.data.rules.kill_target(),
                        now_ms,
                    )
                };
                if let Some(result) = result {
                    room.data.revision += 1;
                    (
                        Self::Settling(Room {
                            data: room.data,
                            state: Settling {
                                running: Box::new(room.state),
                                result: Box::new(result),
                                return_at: now_ms
                                    .saturating_add(crate::round::SETTLEMENT_HOLD_MS + 1000),
                            },
                        }),
                        true,
                    )
                } else {
                    (Self::Running(room), false)
                }
            }
            Self::Settling(room) if now_ms >= room.state.return_at => {
                (Self::Waiting(room.into_waiting(None)), true)
            }
            _ => (self, false),
        }
    }
}
