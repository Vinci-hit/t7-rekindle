//! Bounded client pose replication while the original client owns local physics.
use serde::{Deserialize, Serialize};

use crate::{client_view::ActorId, protocol::ErrorCode};

pub const POSE_INTERVAL_MS: u64 = 50;
const MAX_SPEED: f32 = 40.0;
const BURST_DISTANCE: f32 = 2.0;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MotionFrame {
    pub tick: u64,
    pub velocity: [f32; 3],
    pub airborne: bool,
    pub jumping: bool,
    pub action_id: u16,
    pub action_tick: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Pose {
    pub position: [f32; 3],
    pub heading: i16,
    pub forward_back: i8,
    pub left_right: i8,
    pub running: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub motion: Option<MotionFrame>,
}

impl Pose {
    pub(crate) fn valid(&self) -> bool {
        self.position
            .iter()
            .all(|v| v.is_finite() && v.abs() < 100_000.0)
            && (-180..=180).contains(&self.heading)
            && (-1..=1).contains(&self.forward_back)
            && (-1..=1).contains(&self.left_right)
            && (!self.running || self.forward_back == -1)
            && self.motion.as_ref().is_none_or(|frame| {
                frame.action_tick <= frame.tick
                    && (!frame.jumping || frame.airborne)
                    && frame
                        .velocity
                        .iter()
                        .all(|v| v.is_finite() && v.abs() <= MAX_SPEED)
            })
    }
}

/// Causal checkpoint for an event that may overtake the independent pose stream.
#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct PoseCheckpoint {
    pub input_seq: u32,
    pub pose: Pose,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PoseEvent {
    pub room_id: String,
    pub match_id: String,
    pub actor_id: ActorId,
    pub event_seq: u64,
    pub input_seq: u32,
    pub server_time_ms: u64,
    pub pose: Pose,
}

#[derive(Debug)]
struct ActorPose {
    id: ActorId,
    input_seq: u32,
    accepted: Option<(Pose, u64)>,
    velocity: [f32; 3],
    distance_budget: f32,
    dirty: bool,
    history: std::collections::VecDeque<(Pose, u64, [f32; 3])>,
}

#[derive(Debug)]
pub(crate) struct Motion {
    room_id: String,
    match_id: String,
    event_seq: u64,
    next_publish: u64,
    actors: Vec<ActorPose>,
    pending: Vec<PoseEvent>,
}

impl ActorPose {
    fn new(id: u32) -> Self {
        Self {
            id: ActorId(id),
            input_seq: 0,
            accepted: None,
            velocity: [0.; 3],
            distance_budget: BURST_DISTANCE,
            dirty: false,
            history: std::collections::VecDeque::new(),
        }
    }
}

impl Motion {
    pub fn new(room_id: String, match_id: String, actors: impl Iterator<Item = u32>) -> Self {
        Self {
            room_id,
            match_id,
            event_seq: 0,
            next_publish: 0,
            actors: actors.map(ActorPose::new).collect(),
            pending: Vec::new(),
        }
    }

    pub(crate) fn spawn(&mut self, id: u32) {
        self.actors.push(ActorPose::new(id));
    }

    pub(crate) fn remove(&mut self, id: u32) {
        self.actors.retain(|a| a.id.0 != id);
    }

    pub fn input(
        &mut self,
        actor_id: u32,
        input_seq: u32,
        pose: Pose,
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
        if !pose.valid() {
            return Err(ErrorCode::InvalidPose);
        }
        let mut budget = actor.distance_budget;
        if let Some((previous, at)) = &actor.accepted {
            budget += now.saturating_sub(*at).min(5000) as f32 * MAX_SPEED / 1000.0;
            let distance = previous
                .position
                .iter()
                .zip(pose.position)
                .map(|(a, b)| (a - b).powi(2))
                .sum::<f32>()
                .sqrt();
            if distance > budget {
                return Err(ErrorCode::InvalidPose);
            }
            budget = (budget - distance).min(BURST_DISTANCE);
        }
        actor.velocity = [0.; 3];
        if let Some((previous, at)) = &actor.accepted {
            let elapsed = now.saturating_sub(*at);
            if (1..=100).contains(&elapsed) {
                let velocity = std::array::from_fn(|i| {
                    (pose.position[i] - previous.position[i]) * 1000. / elapsed as f32
                });
                if velocity.iter().map(|v| v * v).sum::<f32>().sqrt() <= 25. {
                    actor.velocity = velocity;
                }
            }
        }
        actor.distance_budget = budget;
        actor.input_seq = input_seq;
        actor.history.push_back((pose.clone(), now, actor.velocity));
        while actor.history.len() > 32 {
            actor.history.pop_front();
        }
        actor.accepted = Some((pose, now));
        actor.dirty = true;
        Ok(())
    }

    pub fn advance(&mut self, now: u64) {
        if now < self.next_publish {
            return;
        }
        let intervals = (now - self.next_publish) / POSE_INTERVAL_MS + 1;
        self.next_publish = self
            .next_publish
            .saturating_add(intervals.saturating_mul(POSE_INTERVAL_MS));
        for actor in &mut self.actors {
            if !actor.dirty {
                continue;
            }
            actor.dirty = false;
            let (pose, at) = actor.accepted.as_ref().expect("dirty pose is accepted");
            self.event_seq += 1;
            self.pending.push(PoseEvent {
                room_id: self.room_id.clone(),
                match_id: self.match_id.clone(),
                actor_id: actor.id,
                event_seq: self.event_seq,
                input_seq: actor.input_seq,
                server_time_ms: *at,
                pose: pose.clone(),
            });
        }
    }

    pub fn take_events(&mut self) -> Vec<PoseEvent> {
        std::mem::take(&mut self.pending)
    }

    pub(crate) fn latest(&self, id: u32, now: u64) -> Option<&Pose> {
        let (pose, at) = self
            .actors
            .iter()
            .find(|a| a.id.0 == id)?
            .accepted
            .as_ref()?;
        (now.saturating_sub(*at) <= 1000).then_some(pose)
    }

    /// Zero-order hold: a later pose must never leak into an earlier contact.
    pub(crate) fn at(&self, id: u32, at: u64) -> Option<&Pose> {
        let actor = self.actors.iter().find(|a| a.id.0 == id)?;
        actor
            .history
            .iter()
            .rev()
            .find(|(_, time, _)| *time <= at && at - *time <= 1000)
            .map(|(pose, _, _)| pose)
    }

    /// Velocity of the last sample at or before the contact, never a later update.
    pub(crate) fn velocity(&self, id: u32, now: u64) -> Option<[f32; 3]> {
        let actor = self.actors.iter().find(|a| a.id.0 == id)?;
        actor
            .history
            .iter()
            .rev()
            .find(|(_, time, _)| *time <= now && now - *time <= 100)
            .map(|(_, _, velocity)| *velocity)
    }

    pub(crate) fn reset(&mut self, id: u32) {
        let actor = self
            .actors
            .iter_mut()
            .find(|a| a.id.0 == id)
            .expect("known actor");
        actor.accepted = None;
        actor.history.clear();
        actor.velocity = [0.; 3];
        actor.distance_budget = BURST_DISTANCE;
        actor.dirty = false;
    }
}

#[cfg(test)]
mod damage_sampling_tests {
    use super::*;

    #[test]
    fn velocity_expires_and_rejects_gaps_jumps_and_previous_lives() {
        let mut motion = Motion::new("room".into(), "match".into(), [1].into_iter());
        let pose = |x| Pose {
            position: [x, 0., 0.],
            heading: 0,
            forward_back: 0,
            left_right: 0,
            running: false,
            motion: None,
        };
        motion.input(1, 1, pose(0.), 100).unwrap();
        motion.input(1, 2, pose(0.5), 150).unwrap();
        assert_eq!(motion.velocity(1, 200), Some([10., 0., 0.]));
        assert_eq!(motion.velocity(1, 251), None);
        motion.input(1, 3, pose(1.8), 200).unwrap();
        assert_eq!(motion.velocity(1, 200), Some([0.; 3]));
        assert_eq!(motion.velocity(1, 175), Some([10., 0., 0.]));
        assert_eq!(motion.velocity(1, 99), None);
        motion.input(1, 4, pose(2.), 301).unwrap();
        assert_eq!(motion.velocity(1, 301), Some([0.; 3]));
        motion.reset(1);
        assert_eq!(motion.velocity(1, 301), None);
        assert_eq!(motion.velocity(1, 175), None);
        motion.input(1, 5, pose(20.), 350).unwrap();
        assert_eq!(motion.velocity(1, 350), Some([0.; 3]));
    }
}
