//! Server-owned melee phases. Hit validation and damage are separate operations.
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

use crate::{client_view::ActorId, protocol::ErrorCode};

pub const MAIN_WEAPON: i32 = 1030411;
pub const FIST_WEAPON: i32 = 100000;
// Provisional tuning: the downward thrust winds up inside its release clip.
// Original melee collision rows contain 0/0, not a verified active-frame marker.
pub const THRUST_CONTACT_START_MS: u64 = 150;
pub const DASH_DURATION_MS: u64 = 2033;
pub const DASH_CONTACT_END_MS: u64 = 1133;
pub const DASH_REBOUND_MS: u64 = 933;
const FIST_PHASES: [[u64; 4]; 4] = [
    [369, 0, 632, 369],
    [369, 0, 632, 264],
    [369, 0, 632, 264],
    [369, 0, 632, 264],
];

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum ActionIntent {
    AttackUp,
    AttackRight,
    AttackDown,
    AttackLeft,
    BlockUp,
    BlockRight,
    BlockDown,
    BlockLeft,
    ShieldGuard,
    DashAttack,
    EquipMain,
    EquipSlot2,
    EquipSlot3,
    EquipSlot4,
    ToggleFists,
    AttackRelease,
    BlockRelease,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct ActionEvent {
    pub room_id: String,
    pub match_id: String,
    pub actor_id: ActorId,
    pub event_seq: u64,
    pub action_seq: u16,
    pub action_id: Option<u32>,
    pub state: u16,
    pub weapon_id: i32,
    pub duration_ms: u64,
    pub server_time_ms: u64,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub target_actor_id: Option<ActorId>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Gesture {
    Attack(usize),
    Block(usize),
    Dash,
}

impl Gesture {
    fn from_intent(intent: ActionIntent) -> Option<Self> {
        use ActionIntent::*;
        Some(match intent {
            AttackUp => Self::Attack(0),
            AttackRight => Self::Attack(1),
            AttackDown => Self::Attack(2),
            AttackLeft => Self::Attack(3),
            BlockUp => Self::Block(0),
            BlockRight => Self::Block(1),
            BlockDown => Self::Block(2),
            BlockLeft => Self::Block(3),
            ShieldGuard => Self::Block(4),
            DashAttack => Self::Dash,
            EquipMain | EquipSlot2 | EquipSlot3 | EquipSlot4 | ToggleFists | AttackRelease
            | BlockRelease => return None,
        })
    }

    fn states(self) -> [u16; 4] {
        match self {
            Self::Dash => [438, 438, 439, 2],
            Self::Attack(direction) => [
                [235, 237, 238, 239],
                [241, 243, 244, 245],
                [247, 249, 250, 251],
                [253, 255, 256, 257],
            ][direction],
            Self::Block(4) => [95, 309, 2, 2],
            Self::Block(direction) => [
                [305, 223, 227, 2],
                [308, 224, 228, 2],
                [307, 225, 229, 2],
                [306, 226, 230, 2],
            ][direction],
        }
    }

    fn duration(self, phase: usize, weapon: i32) -> u64 {
        match self {
            Self::Dash => [0, 0, DASH_DURATION_MS, 0][phase],
            Self::Attack(direction) if weapon == FIST_WEAPON => FIST_PHASES[direction][phase],
            Self::Attack(direction) => [
                [360, 0, 640, 500],
                [360, 0, 640, 500],
                [400, 0, 700, 400],
                [360, 0, 640, 530],
            ][direction][phase],
            Self::Block(_) => 0,
        }
    }

    fn released_by(self, intent: ActionIntent) -> bool {
        matches!(
            (self, intent),
            (Self::Attack(_), ActionIntent::AttackRelease)
                | (Self::Block(_), ActionIntent::BlockRelease)
        )
    }
}

#[derive(Debug)]
enum ActionPhase {
    Preparing { until: u64 },
    Held,
    Releasing { until: u64 },
    Recovering { until: u64, chain_at: u64 },
}

#[derive(Clone, Copy, Debug)]
pub(crate) struct ActionSample {
    pub state: u16,
    pub seq: u16,
    pub action_id: Option<u32>,
    pub at: u64,
    pub ended_at: Option<u64>,
    pub received_at: u64,
    pub weapon_id: i32,
}

#[derive(Clone, Copy, Debug)]
struct BufferedAction {
    gesture: Gesture,
    released: bool,
    action_id: u32,
}

#[derive(Debug)]
struct ActiveAction {
    action_id: u32,
    gesture: Gesture,
    phase: ActionPhase,
    released: bool,
    buffered: Option<BufferedAction>,
}

#[derive(Debug)]
struct Actor {
    id: ActorId,
    input_seq: u32,
    action_seq: u16,
    emitted_action_id: Option<u32>,
    active: Option<ActiveAction>,
    history: VecDeque<ActionSample>,
    reaction_until: Option<u64>,
    disabled: bool,
    weapon_id: i32,
    buffered_reaction: Option<BufferedAction>,
    loadout: Option<crate::loadout::Loadout>,
    main_weapon: i32,
    /// Monsters only: no new hurt reaction starts before this time.
    stagger_immune_until: u64,
}

#[derive(Debug)]
pub(crate) struct Battle {
    room_id: String,
    match_id: String,
    event_seq: u64,
    actors: Vec<Actor>,
    pending: Vec<ActionEvent>,
}

impl Actor {
    fn profile(&self, weapon: i32) -> Option<&'static serde_json::Value> {
        crate::loadout::profile(self.loadout.as_ref()?.hero_id, weapon)
    }
    fn has_shield(&self) -> bool {
        self.weapon_id != FIST_WEAPON
            && self.profile(self.weapon_id).is_some_and(|p| p["hand"] == 2)
            && self.loadout.as_ref().is_some_and(|l| l.equipment.iter().any(|e| {
                self.profile(e.weapon_id).is_some_and(|p| p["shield"] == true)
            }))
    }

    fn dash_available(&self) -> bool {
        self.profile(self.weapon_id)
            .map_or(self.weapon_id == MAIN_WEAPON, |p| {
                p["dashAttack"]["enabled"] == true
            })
    }
    fn duration(&self, gesture: Gesture, phase: usize) -> u64 {
        let Some(profile) = self.profile(self.weapon_id) else {
            return gesture.duration(phase, self.weapon_id);
        };
        match gesture {
            Gesture::Attack(d) => {
                profile["attacks"][["300560", "300550", "300570", "300540"][d]]["phases"][phase]
                    .as_u64()
                    .expect("attack phase")
            }
            Gesture::Dash => {
                if phase == 2 {
                    profile["dashAttack"]["durationMs"].as_u64().unwrap_or(0)
                } else {
                    0
                }
            }
            Gesture::Block(4) if phase == 0 => profile["guard"]["prepareMs"].as_u64().unwrap_or(0),
            Gesture::Block(_) => 0,
        }
    }
    fn state_duration(&self, state: u16) -> u64 {
        if matches!(state, 95 | 111) {
            let field = if state == 95 { "prepareMs" } else { "successMs" };
            return self.profile(self.weapon_id).and_then(|p| p["guard"][field].as_u64()).unwrap_or(0);
        }
        {
            for direction in 0..4 {
                let gesture = Gesture::Attack(direction);
                if let Some(phase) = gesture.states().iter().position(|s| *s == state) {
                    return self.duration(gesture, phase);
                }
            }
            if state == 1 {
                return self
                    .profile(self.weapon_id)
                    .and_then(|p| p["changeMs"].as_u64())
                    .unwrap_or(0);
            }
        }
        0
    }
    fn new(id: u32, weapon_id: i32) -> Self {
        Self {
            id: ActorId(id),
            input_seq: 0,
            action_seq: 1,
            emitted_action_id: None,
            active: None,
            history: VecDeque::new(),
            reaction_until: None,
            disabled: false,
            weapon_id,
            buffered_reaction: None,
            loadout: None,
            main_weapon: weapon_id,
            stagger_immune_until: 0,
        }
    }
}

impl Battle {
    pub fn new(room_id: String, match_id: String, actors: impl Iterator<Item = u32>) -> Self {
        Self {
            room_id,
            match_id,
            event_seq: 0,
            actors: actors.map(|id| Actor::new(id, MAIN_WEAPON)).collect(),
            pending: Vec::new(),
        }
    }

    pub(crate) fn spawn(&mut self, id: u32, weapon: i32) {
        self.actors.push(Actor::new(id, weapon));
    }

    pub(crate) fn configure(&mut self, id: u32, loadout: crate::loadout::Loadout) {
        if let Some(actor) = self.actors.iter_mut().find(|a| a.id.0 == id) {
            actor.weapon_id = loadout.initial_weapon();
            actor.main_weapon = actor.weapon_id;
            actor.loadout = Some(loadout);
            actor.active = None;
        }
    }

    pub(crate) fn shield_weapon(&self, actor: u32) -> Option<i32> {
        let actor = self.actors.iter().find(|a| a.id.0 == actor)?;
        if !actor.has_shield() { return None; }
        actor.loadout.as_ref()?.equipment.iter().find_map(|e|
            actor.profile(e.weapon_id).is_some_and(|p| p["shield"] == true).then_some(e.weapon_id))
    }

    pub(crate) fn profile(&self, actor: u32, weapon: i32) -> Option<&'static serde_json::Value> {
        let a = self.actors.iter().find(|a| a.id.0 == actor)?;
        crate::loadout::profile(a.loadout.as_ref()?.hero_id, weapon)
    }

    pub(crate) fn remove(&mut self, id: u32) {
        self.actors.retain(|a| a.id.0 != id);
    }

    pub fn input(
        &mut self,
        actor_id: u32,
        input_seq: u32,
        intent: ActionIntent,
        now: u64,
    ) -> Result<(), ErrorCode> {
        self.input_at(actor_id, input_seq, intent, now, now)
    }

    pub(crate) fn input_at(
        &mut self,
        actor_id: u32,
        input_seq: u32,
        intent: ActionIntent,
        sampled_at: u64,
        now: u64,
    ) -> Result<(), ErrorCode> {
        let actor = self
            .actors
            .iter_mut()
            .find(|a| a.id.0 == actor_id)
            .ok_or(ErrorCode::UnknownActor)?;
        if input_seq == 0 || input_seq <= actor.input_seq {
            return Err(ErrorCode::StaleInput);
        }
        actor.input_seq = input_seq;
        if intent == ActionIntent::ShieldGuard && !actor.has_shield() {
            return Ok(());
        }
        if actor.disabled {
            return Ok(());
        }
        if matches!(
            intent,
            ActionIntent::EquipMain
                | ActionIntent::EquipSlot2
                | ActionIntent::EquipSlot3
                | ActionIntent::EquipSlot4
                | ActionIntent::ToggleFists
        ) {
            if actor.active.is_none() && actor.reaction_until.is_none() {
                let weapon =
                    if intent == ActionIntent::ToggleFists && actor.weapon_id != FIST_WEAPON {
                        FIST_WEAPON
                    } else {
                        actor.main_weapon
                    };
                let weapon = if intent == ActionIntent::ToggleFists {
                    Some(weapon)
                } else if let Some(loadout) = &actor.loadout {
                    let slot = match intent {
                        ActionIntent::EquipSlot2 => 2,
                        ActionIntent::EquipSlot3 => 3,
                        ActionIntent::EquipSlot4 => 4,
                        _ => 1,
                    };
                    loadout
                        .equipment
                        .iter()
                        .find(|i| i.slot == slot)
                        .map(|i| i.weapon_id)
                } else {
                    Some(weapon)
                };
                let Some(mut weapon) = weapon else {
                    return Ok(());
                };
                if actor.profile(weapon).is_some_and(|p| p["shield"] == true) {
                    let Some(loadout) = &actor.loadout else {
                        return Ok(());
                    };
                    let Some(item) = loadout
                        .equipment
                        .iter()
                        .filter(|i| actor.profile(i.weapon_id).is_some_and(|p| p["hand"] == 2))
                        .min_by_key(|i| {
                            (
                                i.weapon_id != actor.weapon_id,
                                i.weapon_id != actor.main_weapon,
                                i.slot,
                            )
                        })
                    else {
                        return Ok(());
                    };
                    weapon = item.weapon_id;
                }
                if weapon != actor.weapon_id {
                    actor.weapon_id = weapon;
                    if weapon != FIST_WEAPON {
                        actor.main_weapon = weapon;
                    }
                    let change = actor
                        .profile(weapon)
                        .and_then(|p| p["changeMs"].as_u64())
                        .unwrap_or(100);
                    actor.reaction_until = Some(now + change);
                    let mut states = Vec::new();
                    actor.emitted_action_id = Some(input_seq);
                    actor.emit(1, now, &mut states);
                    self.publish(actor_id, states, Some(now));
                }
            }
            return Ok(());
        }
        if intent == ActionIntent::DashAttack
            && (!actor.dash_available() || actor.active.is_some() || actor.reaction_until.is_some())
        {
            return Ok(());
        }
        if actor.reaction_until.is_some() {
            if actor.history.back().is_some_and(|sample| sample.state == 1) {
                return Ok(());
            }
            if intent == ActionIntent::BlockRelease {
                actor.active = None;
            }
            if let Some(gesture) = Gesture::from_intent(intent) {
                actor.buffered_reaction = Some(BufferedAction {
                    gesture,
                    released: false,
                    action_id: input_seq,
                });
            } else if let Some(buffered) = actor.buffered_reaction.as_mut()
                && buffered.gesture.released_by(intent)
            {
                buffered.released = true;
            }
            return Ok(());
        }
        let mut states = Vec::new();
        // Eligibility is current; a validated start cannot cross an already
        // committed reaction or phase boundary.
        let start = sampled_at
            .min(now)
            .max(actor.history.back().map_or(0, |s| s.at));
        actor.input(intent, input_seq, start, &mut states);
        actor.advance(now, &mut states);
        self.publish(actor_id, states, Some(now));
        Ok(())
    }

    pub(crate) fn advance_input(&mut self, actor_id: u32, now: u64) {
        let Some(actor) = self.actors.iter_mut().find(|actor| actor.id.0 == actor_id) else {
            return;
        };
        // Input may arrive between ticks. Reactions keep their existing input-buffer rules.
        if actor.disabled || actor.reaction_until.is_some() {
            return;
        }
        let mut states = Vec::new();
        actor.advance(now, &mut states);
        self.publish(actor_id, states, None);
    }

    pub fn advance(&mut self, now: u64) {
        for index in 0..self.actors.len() {
            let actor = &mut self.actors[index];
            let actor_id = actor.id.0;
            let mut states = Vec::new();
            if let Some(until) = actor.reaction_until.filter(|until| now >= *until) {
                actor.reaction_until = None;
                if let Some(buffered) = actor.buffered_reaction.take() {
                    actor.active = None;
                    actor.finish(Some(buffered), until, &mut states);
                } else {
                    let state = actor.active.as_ref().map_or(2, |a| a.gesture.states()[1]);
                    actor.emitted_action_id = actor.active.as_ref().map(|a| a.action_id);
                    actor.emit(state, until, &mut states);
                }
                actor.advance(now, &mut states);
            } else if actor.reaction_until.is_none() && !actor.disabled {
                actor.advance(now, &mut states);
            }
            self.publish(actor_id, states, None);
        }
    }

    fn publish(
        &mut self,
        actor_id: u32,
        states: Vec<(u16, u16, u64, Option<u32>)>,
        received_at: Option<u64>,
    ) {
        for (state, action_seq, server_time_ms, action_id) in states {
            let actor = self
                .actors
                .iter_mut()
                .find(|a| a.id.0 == actor_id)
                .expect("known actor");
            if let Some(previous) = actor.history.back_mut() {
                previous.ended_at = Some(server_time_ms);
            }
            let received_at = received_at
                .or_else(|| {
                    action_id.and_then(|id| {
                        actor
                            .history
                            .iter()
                            .rev()
                            .find(|s| s.action_id == Some(id))
                            .map(|s| s.received_at)
                    })
                })
                .unwrap_or(server_time_ms);
            actor.history.push_back(ActionSample {
                state,
                seq: action_seq,
                action_id,
                at: server_time_ms,
                ended_at: None,
                received_at,
                weapon_id: actor.weapon_id,
            });
            while actor.history.len() > 32 {
                actor.history.pop_front();
            }
            self.event_seq += 1;
            self.pending.push(ActionEvent {
                room_id: self.room_id.clone(),
                match_id: self.match_id.clone(),
                actor_id: ActorId(actor_id),
                event_seq: self.event_seq,
                action_seq,
                action_id,
                state,
                weapon_id: actor.weapon_id,
                duration_ms: if state == 439 {
                    actor.duration(Gesture::Dash, 2)
                } else if state == 443 {
                    actor
                        .profile(actor.weapon_id)
                        .and_then(|p| p["dashAttack"]["reboundMs"].as_u64())
                        .unwrap_or(DASH_REBOUND_MS)
                } else {
                    actor.state_duration(state)
                },
                server_time_ms,
                target_actor_id: None,
            });
        }
    }

    pub(crate) fn weapon(&self, actor: u32) -> Option<i32> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor)
            .map(|a| a.weapon_id)
    }

    pub(crate) fn pause_input(&mut self, id: u32, now: u64) {
        let Some(actor) = self.actors.iter_mut().find(|a| a.id.0 == id) else {
            return;
        };
        actor.active = None;
        actor.buffered_reaction = None;
        if !actor.disabled && actor.reaction_until.is_none() {
            self.react(id, 2, now, 0, false, None);
        }
    }

    pub(crate) fn recovery(&self, now: u64) -> (u64, Vec<ActionEvent>) {
        (
            self.event_seq,
            self.actors
                .iter()
                .map(|actor| {
                    let sample = actor.history.back();
                    ActionEvent {
                        room_id: self.room_id.clone(),
                        match_id: self.match_id.clone(),
                        actor_id: actor.id,
                        event_seq: self.event_seq,
                        action_seq: actor.action_seq,
                        action_id: sample.and_then(|s| s.action_id),
                        state: sample.map_or(2, |s| s.state),
                        weapon_id: actor.weapon_id,
                        duration_ms: actor.reaction_until.map_or(0, |t| t.saturating_sub(now)),
                        server_time_ms: now,
                        target_actor_id: None,
                    }
                })
                .collect(),
        )
    }

    pub fn take_events(&mut self) -> Vec<ActionEvent> {
        std::mem::take(&mut self.pending)
    }

    #[cfg(test)]
    pub(crate) fn sample(&self, actor_id: u32, seq: u16) -> Option<ActionSample> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .iter()
            .rev()
            .find(|s| s.seq == seq)
            .copied()
    }

    pub(crate) fn sample_action(
        &self,
        actor_id: u32,
        action_id: u32,
        state: u16,
    ) -> Option<ActionSample> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .iter()
            .rev()
            .find(|s| s.action_id == Some(action_id) && s.state == state)
            .copied()
    }

    /// Sample the authoritative phase that covered a contact, never a future guard.
    pub(crate) fn at(&self, actor_id: u32, at: u64) -> Option<ActionSample> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .iter()
            .rev()
            .find(|s| s.at <= at && s.ended_at.is_none_or(|end| at < end))
            .copied()
    }

    pub(crate) fn at_before_receipt(
        &self,
        actor_id: u32,
        at: u64,
        deadline: u64,
    ) -> Option<ActionSample> {
        // A later backdated release closes the previous interval in live history,
        // but must not erase the guard known before this contact's deadline.
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .iter()
            .rev()
            .find(|s| s.at <= at && s.received_at <= deadline)
            .copied()
    }

    pub(crate) fn current(&self, actor_id: u32) -> Option<ActionSample> {
        self.actors
            .iter()
            .find(|a| a.id.0 == actor_id)?
            .history
            .back()
            .copied()
    }

    pub(crate) fn react(
        &mut self,
        actor_id: u32,
        state: u16,
        now: u64,
        duration: u64,
        disabled: bool,
        target: Option<u32>,
    ) {
        let actor = self
            .actors
            .iter_mut()
            .find(|a| a.id.0 == actor_id)
            .expect("known actor");
        if state == 35 && actor_id >= crate::pve::FIRST_MONSTER_ID {
            // Staggers are rationed for monsters; damage itself is unaffected.
            if now < actor.stagger_immune_until {
                return;
            }
            actor.stagger_immune_until = now
                .saturating_add(duration)
                .saturating_add(crate::limits::MONSTER_STAGGER_IMMUNITY_MS);
        }
        // Historical parry feedback must not re-press a guard already released
        // while the verdict was pending, or replace a newer guard direction.
        actor.active = if [231, 232, 233, 234, 111].contains(&state) {
            actor
                .active
                .take()
                .filter(|active| matches!(active.gesture, Gesture::Block(_)) && !active.released)
                .map(|mut active| {
                    active.phase = ActionPhase::Held;
                    active
                })
        } else {
            None
        };
        actor.buffered_reaction = None;
        actor.disabled = disabled;
        actor.reaction_until = (!disabled && duration > 0).then_some(now + duration);
        let mut output = Vec::new();
        actor.emitted_action_id = None;
        actor.emit(state, now, &mut output);
        self.publish(actor_id, output, Some(now));
        let event = self.pending.last_mut().expect("reaction event");
        // Native enforcedDuration must match the authoritative recovery deadline.
        event.duration_ms = duration;
        event.target_actor_id = target.map(ActorId);
    }
}

impl Actor {
    fn emit(&mut self, state: u16, now: u64, output: &mut Vec<(u16, u16, u64, Option<u32>)>) {
        self.action_seq = self.action_seq % u16::MAX + 1;
        output.push((state, self.action_seq, now, self.emitted_action_id));
    }

    fn start(
        &mut self,
        gesture: Gesture,
        action_id: u32,
        now: u64,
        output: &mut Vec<(u16, u16, u64, Option<u32>)>,
    ) {
        self.emitted_action_id = Some(action_id);
        self.active = Some(ActiveAction {
            action_id,
            gesture,
            phase: ActionPhase::Preparing {
                until: now + self.duration(gesture, 0),
            },
            released: false,
            buffered: None,
        });
        if gesture == Gesture::Dash {
            self.active.as_mut().expect("started dash").phase = ActionPhase::Releasing {
                until: now + self.duration(Gesture::Dash, 2),
            };
            self.emit(439, now, output);
        } else {
            self.emit(gesture.states()[0], now, output);
        }
    }

    fn input(
        &mut self,
        intent: ActionIntent,
        input_seq: u32,
        now: u64,
        output: &mut Vec<(u16, u16, u64, Option<u32>)>,
    ) {
        let gesture = Gesture::from_intent(intent);
        let release_duration = self
            .active
            .as_ref()
            .map(|a| self.duration(a.gesture, 2))
            .unwrap_or(0);
        let Some(active) = self.active.as_mut() else {
            if let Some(gesture) = gesture {
                self.start(gesture, input_seq, now, output);
            }
            return;
        };
        if let Some(gesture) = gesture {
            let chain_window = matches!(active.phase, ActionPhase::Recovering { chain_at, .. }
                if now >= chain_at || matches!(gesture, Gesture::Block(_)));
            if chain_window {
                self.start(gesture, input_seq, now, output);
                return;
            }
            let preparing = matches!(
                active.phase,
                ActionPhase::Preparing { .. } | ActionPhase::Held
            );
            if preparing
                && std::mem::discriminant(&gesture) != std::mem::discriminant(&active.gesture)
            {
                self.start(gesture, input_seq, now, output);
            } else if matches!(active.gesture, Gesture::Attack(_))
                || matches!(
                    active.phase,
                    ActionPhase::Releasing { .. } | ActionPhase::Recovering { .. }
                )
            {
                active.buffered = Some(BufferedAction {
                    gesture,
                    released: false,
                    action_id: input_seq,
                });
            }
            return;
        }
        if let Some(buffered) = active.buffered.as_mut()
            && buffered.gesture.released_by(intent)
        {
            buffered.released = true;
        }
        if active.released || !active.gesture.released_by(intent) {
            return;
        }
        if matches!(active.gesture, Gesture::Block(_)) {
            let buffered = active.buffered;
            self.active = None;
            self.finish(buffered, now, output);
            return;
        }
        active.released = true;
        if matches!(active.phase, ActionPhase::Held) {
            let gesture = active.gesture;
            active.phase = ActionPhase::Releasing {
                until: now + release_duration,
            };
            self.emit(gesture.states()[2], now, output);
        }
    }

    fn advance(&mut self, now: u64, output: &mut Vec<(u16, u16, u64, Option<u32>)>) {
        // Preserve phase deadlines across late ticks, including buffered actions.
        while let Some(mut active) = self.active.take() {
            let gesture = active.gesture;
            let (state, at) = match active.phase {
                ActionPhase::Preparing { until } if now >= until => {
                    if active.released {
                        if matches!(gesture, Gesture::Block(_)) {
                            self.finish(active.buffered, until, output);
                            continue;
                        }
                        active.phase = ActionPhase::Releasing {
                            until: until + self.duration(gesture, 2),
                        };
                        (gesture.states()[2], until)
                    } else {
                        active.phase = ActionPhase::Held;
                        (gesture.states()[1], until)
                    }
                }
                ActionPhase::Releasing { until } if now >= until => {
                    if matches!(gesture, Gesture::Block(_) | Gesture::Dash)
                        || active.buffered.is_some()
                    {
                        self.finish(active.buffered, until, output);
                        continue;
                    }
                    active.phase = ActionPhase::Recovering {
                        until: until + self.duration(gesture, 3),
                        chain_at: until,
                    };
                    (gesture.states()[3], until)
                }
                ActionPhase::Recovering { chain_at, .. }
                    if active.buffered.is_some_and(|b| {
                        matches!(b.gesture, Gesture::Block(_)) || now >= chain_at
                    }) =>
                {
                    self.finish(active.buffered, chain_at, output);
                    continue;
                }
                ActionPhase::Recovering { until, .. } if now >= until => {
                    self.finish(active.buffered, until, output);
                    continue;
                }
                _ => {
                    self.active = Some(active);
                    break;
                }
            };
            self.active = Some(active);
            self.emit(state, at, output);
        }
    }

    fn finish(
        &mut self,
        buffered: Option<BufferedAction>,
        now: u64,
        output: &mut Vec<(u16, u16, u64, Option<u32>)>,
    ) {
        if let Some(buffered) = buffered {
            self.start(buffered.gesture, buffered.action_id, now, output);
            self.active.as_mut().expect("started action").released = buffered.released;
        } else {
            self.emit(2, now, output);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn battle() -> Battle {
        Battle::new("room".into(), "match".into(), [1].into_iter())
    }

    #[test]
    fn calibrated_attack_starts_do_not_shift_with_transport_delay() {
        for one_way in [5, 50, 100] {
            let mut b = Battle::new("room".into(), "match".into(), [1].into_iter());
            b.input_at(1, 1, ActionIntent::AttackUp, 3000, 3000 + one_way)
                .unwrap();
            b.advance_input(1, 3001 + one_way);
            b.input_at(1, 2, ActionIntent::AttackRelease, 3001, 3001 + one_way)
                .unwrap();
            b.advance(3360 + one_way);
            let release = b.sample_action(1, 1, 238).unwrap();
            assert_eq!(release.at, 3360);
            // A committed reaction remains a receipt-time eligibility boundary.
            b.react(1, 35, 3400, 200, false, None);
            b.input_at(1, 3, ActionIntent::BlockUp, 3390, 3450).unwrap();
            assert_eq!(b.at(1, 3400).unwrap().state, 35);
        }
    }

    #[test]
    fn a_monster_cannot_be_stunlocked_by_repeated_hits() {
        let id = crate::pve::FIRST_MONSTER_ID;
        let mut b = Battle::new("r".into(), "m".into(), std::iter::empty());
        b.spawn(id, MAIN_WEAPON);
        b.react(id, 35, 1000, 550, false, None);
        b.react(id, 35, 1300, 550, false, None);
        // The second hit neither restarts the reaction nor extends it.
        assert_eq!(b.actors[0].reaction_until, Some(1550));
        b.advance(1560);
        assert_eq!(b.current(id).unwrap().state, 2);
        // Still inside the immunity window after recovery.
        b.react(id, 35, 1700, 550, false, None);
        assert_eq!(b.current(id).unwrap().state, 2);
        // Immunity ends 800 ms after the reaction finished.
        b.react(id, 35, 2400, 550, false, None);
        assert_eq!(b.current(id).unwrap().state, 35);
        // Players are never rationed.
        let mut p = Battle::new("r".into(), "m".into(), [1].into_iter());
        p.react(1, 35, 1000, 550, false, None);
        p.react(1, 35, 1300, 550, false, None);
        assert_eq!(p.actors[0].reaction_until, Some(1850));
    }

    #[test]
    fn phase_timeline_is_independent_of_tick_cadence() {
        for buffered in [false, true] {
            let run = |ticks: &[u64]| {
                let mut b = battle();
                b.input(1, 1, ActionIntent::AttackUp, 5).unwrap();
                b.input(1, 2, ActionIntent::AttackRelease, 10).unwrap();
                if buffered {
                    b.input(1, 3, ActionIntent::AttackLeft, 20).unwrap();
                    b.input(1, 4, ActionIntent::AttackRelease, 30).unwrap();
                }
                for &at in ticks {
                    b.advance(at);
                }
                b
            };
            let ticks: Vec<_> = (5..=3005).step_by(5).collect();
            let mut regular = run(&ticks);
            let mut delayed = run(&[400, 3005]);
            let timeline = |b: &mut Battle| {
                b.take_events()
                    .into_iter()
                    .map(|e| (e.state, e.server_time_ms, e.action_seq, e.action_id))
                    .collect::<Vec<_>>()
            };
            assert_eq!(timeline(&mut delayed), timeline(&mut regular));
            // The entire release interval remains queryable after catch-up.
            assert_eq!(delayed.at(1, 364).unwrap().state, 235);
            assert_eq!(delayed.at(1, 365).unwrap().state, 238);
            assert_eq!(delayed.at(1, 1004).unwrap().state, 238);
            assert_ne!(delayed.at(1, 1005).unwrap().state, 238);
            let release = delayed.sample_action(1, 1, 238).unwrap();
            assert_eq!((release.at, release.ended_at), (365, Some(1005)));
            assert_eq!(delayed.at(1, 3005).unwrap().state, 2);
        }
    }

    #[test]
    fn held_release_starts_at_input_time_after_late_preparation_tick() {
        let mut b = battle();
        b.input(1, 1, ActionIntent::AttackUp, 5).unwrap();
        b.advance(800);
        b.input(1, 2, ActionIntent::AttackRelease, 900).unwrap();
        b.advance(3000);
        assert_eq!(
            b.take_events()
                .iter()
                .map(|e| (e.state, e.server_time_ms))
                .collect::<Vec<_>>(),
            [(235, 5), (237, 365), (238, 900), (239, 1540), (2, 2040)]
        );
    }

    #[test]
    fn animation_duration_matches_phase_and_reaction_deadlines() {
        let mut b = battle();
        b.input(1, 1, ActionIntent::AttackUp, 0).unwrap();
        let event = b.take_events().pop().unwrap();
        assert_eq!(event.duration_ms, 360);
        for (state, duration) in [(240, 530), (241, 430), (35, 550)] {
            b.react(1, state, 1000, duration, false, None);
            let event = b.take_events().pop().unwrap();
            assert_eq!(event.duration_ms, duration);
            assert_eq!(b.actors[0].reaction_until, Some(1000 + event.duration_ms));
        }
    }

    #[test]
    fn delayed_reaction_recovery_advances_buffered_action_from_deadline() {
        let mut b = battle();
        b.react(1, 35, 100, 200, false, None);
        b.input(1, 1, ActionIntent::AttackUp, 120).unwrap();
        b.input(1, 2, ActionIntent::AttackRelease, 130).unwrap();
        b.advance(3000);
        assert_eq!(
            b.take_events()
                .iter()
                .map(|e| (e.state, e.server_time_ms))
                .collect::<Vec<_>>(),
            [(35, 100), (235, 300), (238, 660), (239, 1300), (2, 1800)]
        );
        assert_eq!(b.at(1, 299).unwrap().state, 35);
        assert_eq!(b.at(1, 300).unwrap().state, 235);
    }

    #[test]
    fn zero_duration_guard_and_dash_finish_at_their_deadlines() {
        let mut b = battle();
        b.input(1, 1, ActionIntent::BlockLeft, 100).unwrap();
        b.advance(900);
        assert_eq!(b.sample_action(1, 1, 226).unwrap().at, 100);
        b.input(1, 2, ActionIntent::BlockRelease, 1000).unwrap();
        b.input(1, 3, ActionIntent::DashAttack, 1100).unwrap();
        b.advance(10000);
        assert_eq!(b.at(1, 3132).unwrap().state, 439);
        assert_eq!(b.at(1, 3133).unwrap().state, 2);
    }

    #[test]
    fn recovery_interrupt_keeps_input_identity_when_idle_is_skipped() {
        let mut b = battle();
        b.input(1, 1, ActionIntent::AttackUp, 0).unwrap();
        b.input(1, 2, ActionIntent::AttackRelease, 10).unwrap();
        b.advance(360);
        b.advance(1000);
        b.input(1, 3, ActionIntent::BlockUp, 1100).unwrap();
        b.advance(1100);
        let events = b.take_events();
        assert_eq!(
            events.iter().map(|e| e.state).collect::<Vec<_>>(),
            [235, 238, 239, 305, 223]
        );
        assert_eq!(
            events.iter().map(|e| e.action_id).collect::<Vec<_>>(),
            [Some(1), Some(1), Some(1), Some(3), Some(3)]
        );
        // A client's extra predicted idle changes its native seq, not this input identity.
        assert_eq!(events[3].action_seq, 5);
        assert_eq!(b.sample(1, 5).unwrap().action_id, Some(3));
    }

    #[test]
    fn buffered_action_keeps_origin_across_release_and_phase_changes() {
        let mut b = battle();
        b.input(1, 1, ActionIntent::AttackUp, 0).unwrap();
        b.input(1, 2, ActionIntent::AttackRelease, 10).unwrap();
        b.advance(360);
        b.input(1, 3, ActionIntent::AttackLeft, 400).unwrap();
        b.input(1, 4, ActionIntent::AttackRelease, 410).unwrap();
        b.advance(1000);
        b.advance(1360);
        let events = b.take_events();
        assert_eq!(
            events
                .iter()
                .map(|e| (e.state, e.action_id))
                .collect::<Vec<_>>(),
            [
                (235, Some(1)),
                (238, Some(1)),
                (253, Some(3)),
                (256, Some(3))
            ]
        );
    }

    #[test]
    fn reaction_is_not_an_input_echo_and_buffered_input_keeps_identity() {
        let mut b = battle();
        b.input(1, 1, ActionIntent::BlockUp, 0).unwrap();
        b.advance(0);
        b.react(1, 231, 10, 100, false, None);
        b.advance(110);
        let events = b.take_events();
        assert_eq!(events[2].action_id, None);
        assert_eq!((events[3].state, events[3].action_id), (223, Some(1)));
        b.react(1, 35, 120, 100, false, None);
        b.input(1, 2, ActionIntent::AttackLeft, 130).unwrap();
        b.input(1, 3, ActionIntent::AttackRelease, 140).unwrap();
        b.advance(220);
        b.advance(580);
        let events = b.take_events();
        assert_eq!(
            events
                .iter()
                .map(|e| (e.state, e.action_id))
                .collect::<Vec<_>>(),
            [(35, None), (253, Some(2)), (256, Some(2))]
        );
    }
}

#[cfg(test)]
mod catalogue_tests {
    use super::*;
    #[test]
    fn selected_weapon_uses_catalogue_deadlines_and_slots() {
        let loadout = crate::loadout::Loadout {
            hero_id: 1102,
            equipment: vec![
                crate::loadout::Equipment {
                    slot: 1,
                    weapon_id: 1010911,
                },
                crate::loadout::Equipment {
                    slot: 3,
                    weapon_id: 1030611,
                },
            ],
        };
        loadout.validate().unwrap();
        let mut b = Battle::new("room".into(), "match".into(), [1].into_iter());
        b.configure(1, loadout);
        b.input(1, 1, ActionIntent::AttackUp, 0).unwrap();
        b.input(1, 2, ActionIntent::AttackRelease, 1).unwrap();
        let prepare = b.profile(1, 1010911).unwrap()["attacks"]["300560"]["phases"][0]
            .as_u64()
            .unwrap();
        b.advance_input(1, prepare - 1);
        assert_eq!(b.actors[0].history.back().unwrap().state, 235);
        b.advance_input(1, prepare);
        assert_eq!(b.actors[0].history.back().unwrap().state, 238);
        b.advance_input(1, 10000);
        b.input(1, 3, ActionIntent::EquipSlot3, 10001).unwrap();
        assert_eq!(b.weapon(1), Some(1030611));
        b.advance(20000);
        b.input(1, 4, ActionIntent::ToggleFists, 20001).unwrap();
        assert_eq!(b.weapon(1), Some(FIST_WEAPON));
        b.advance(30000);
        b.input(1, 5, ActionIntent::ToggleFists, 30001).unwrap();
        assert_eq!(b.weapon(1), Some(1030611));
    }
    #[test]
    fn shield_uses_native_chain_and_release_without_directional_guard() {
        let mut b = Battle::new("room".into(), "match".into(), [1, 2].into_iter());
        b.configure(1, crate::loadout::Loadout {
            hero_id: 1001,
            equipment: vec![
                crate::loadout::Equipment { slot: 1, weapon_id: 1010811 },
                crate::loadout::Equipment { slot: 2, weapon_id: 1090211 },
            ],
        });
        b.input(2, 1, ActionIntent::ShieldGuard, 0).unwrap();
        assert!(b.current(2).is_none_or(|s| s.state == 2));
        b.input(1, 1, ActionIntent::ShieldGuard, 0).unwrap();
        assert_eq!(b.current(1).unwrap().state, 95);
        let delay = b.profile(1, 1010811).unwrap()["guard"]["prepareMs"].as_u64().unwrap();
        b.advance(delay);
        assert_eq!(b.current(1).unwrap().state, 309);
        b.react(1, 111, delay + 1, 10, false, Some(2));
        b.advance(delay + 11);
        assert_eq!(b.current(1).unwrap().state, 309);
        b.input(1, 2, ActionIntent::BlockRelease, delay + 12).unwrap();
        assert_eq!(b.current(1).unwrap().state, 2);
    }

}
