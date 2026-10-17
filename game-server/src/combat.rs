//! Resolve reported native blade contacts against server-owned actions and lives.
use crate::{battle::Battle, client_view::ActorId, motion::Motion, protocol::ErrorCode};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

pub const MAX_HEALTH: u16 = 100;
pub const RESPAWN_MS: u64 = 8000;
const DAMAGE: u16 = 20;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub enum HitPart {
    Head,
    Body,
    Legs,
}

fn body_damage(height: f32, closing_speed: f32, base: u16) -> (u16, HitPart) {
    let part = if !(0.0..=1.8).contains(&height) {
        HitPart::Body
    } else if height >= 1.5 {
        HitPart::Head
    } else if height < 0.6 {
        HitPart::Legs
    } else {
        HitPart::Body
    };
    let multiplier = match part {
        HitPart::Head => 1.5,
        HitPart::Body => 1.,
        HitPart::Legs => 0.75,
    };
    let bonus = (closing_speed.max(0.) * 0.05).min(0.5);
    (
        (f32::from(base) * multiplier * (1. + bonus))
            .round()
            .max(1.) as u16,
        part,
    )
}

const REPORT_GRACE_MS: u64 = 600;

/// Client monotonic timestamps for observing gateway delay; not authoritative time.
#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContactTiming {
    pub sample_time_ms: u64,
    pub phase_start_ms: u64,
    pub gateway_time_ms: u64,
}

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ContactReport {
    pub action_seq: u16,
    pub action_id: u32,
    pub state: u16,
    pub elapsed_ms: u16,
    // Zero is terrain; the sender's actor is always inferred from its connection.
    pub target_actor_id: u32,
    pub position: [f32; 3],
    pub normal: [f32; 3],
    pub direction: [f32; 3],
    pub material: i8,
    #[serde(default)]
    pub weapon_contact: bool,
    #[serde(default)]
    pub timing: Option<ContactTiming>,
}

impl ContactReport {
    fn valid(&self) -> bool {
        self.action_id > 0
            && self.action_seq > 0
            && (-1..=7).contains(&self.material)
            && self
                .position
                .iter()
                .all(|v| v.is_finite() && v.abs() < 100_000.)
            && [&self.normal, &self.direction].iter().all(|v| {
                let length = v.iter().map(|n| n * n).sum::<f32>().sqrt();
                length.is_finite() && (0.9..=1.1).contains(&length)
            })
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Health {
    pub actor_id: ActorId,
    pub health: u16,
    pub kills: u32,
    pub deaths: u32,
    pub streak: u32,
}

#[derive(Clone, Debug, Serialize)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase"
)]
pub enum CombatEffect {
    Contact {
        attacker: ActorId,
        target: Option<ActorId>,
        action_seq: u16,
        weapon_id: i32,
        target_weapon_id: i32,
        parried: bool,
        damage: u16,
        hit_part: HitPart,
        position: [f32; 3],
        normal: [f32; 3],
        direction: [f32; 3],
        material: i8,
        lives: Vec<Health>,
    },
    RespawnReady {
        actor_id: ActorId,
    },
    Respawned {
        actor_id: ActorId,
        lives: Vec<Health>,
    },
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct CombatEvent {
    pub room_id: String,
    pub match_id: String,
    pub event_seq: u64,
    pub server_time_ms: u64,
    pub effect: CombatEffect,
}

#[derive(Debug)]
enum Life {
    Alive(u16),
    Dead { until: u64, ready: bool },
}
#[derive(Debug)]
struct Fighter {
    id: u32,
    camp: u8,
    life: Life,
    input_seq: u32,
    kills: u32,
    deaths: u32,
    streak: u32,
}
#[derive(Debug)]
struct SwingHits {
    actor: u32,
    action_id: u32,
    targets: Vec<u32>,
    stopped: bool,
}

#[derive(Debug)]
pub(crate) struct Combat {
    room_id: String,
    match_id: String,
    event_seq: u64,
    fighters: Vec<Fighter>,
    hits: VecDeque<SwingHits>,
    pending: Vec<CombatEvent>,
}

impl Fighter {
    fn new(id: u32, camp: u8, health: u16) -> Self {
        Self {
            id,
            camp,
            life: Life::Alive(health),
            input_seq: 0,
            kills: 0,
            deaths: 0,
            streak: 0,
        }
    }
}

impl Combat {
    pub(crate) fn new(
        room_id: String,
        match_id: String,
        actors: impl Iterator<Item = (u32, u8)>,
    ) -> Self {
        Self {
            room_id,
            match_id,
            event_seq: 0,
            fighters: actors
                .map(|(id, camp)| Fighter::new(id, camp, MAX_HEALTH))
                .collect(),
            hits: VecDeque::new(),
            pending: Vec::new(),
        }
    }
    pub(crate) fn spawn(&mut self, id: u32, health: u16) {
        self.fighters.push(Fighter::new(id, 2, health));
    }

    pub(crate) fn remove(&mut self, id: u32) {
        self.fighters.retain(|f| f.id != id);
    }

    pub(crate) fn alive(&self, id: u32) -> bool {
        self.fighters
            .iter()
            .any(|f| f.id == id && matches!(f.life, Life::Alive(_)))
    }
    pub(crate) fn health(&self) -> Vec<Health> {
        self.fighters
            .iter()
            .map(|f| Health {
                actor_id: ActorId(f.id),
                health: match f.life {
                    Life::Alive(hp) => hp,
                    Life::Dead { .. } => 0,
                },
                kills: f.kills,
                deaths: f.deaths,
                streak: f.streak,
            })
            .collect()
    }
    fn publish(&mut self, effect: CombatEffect, now: u64) {
        self.event_seq += 1;
        self.pending.push(CombatEvent {
            room_id: self.room_id.clone(),
            match_id: self.match_id.clone(),
            event_seq: self.event_seq,
            server_time_ms: now,
            effect,
        });
    }
    pub(crate) fn input(
        &mut self,
        actor: u32,
        input_seq: u32,
        report: ContactReport,
        now: u64,
        battle: &mut Battle,
        motion: &Motion,
    ) -> Result<bool, ErrorCode> {
        self.reserve_input(actor, input_seq)?;
        self.server_contact(actor, report, now, battle, motion)
    }

    pub(crate) fn reserve_input(&mut self, actor: u32, input_seq: u32) -> Result<(), ErrorCode> {
        let sender = self
            .fighters
            .iter_mut()
            .find(|f| f.id == actor)
            .ok_or(ErrorCode::UnknownActor)?;
        if input_seq == 0 || input_seq <= sender.input_seq {
            return Err(ErrorCode::StaleInput);
        }
        sender.input_seq = input_seq;
        Ok(())
    }

    pub(crate) fn server_contact(
        &mut self,
        actor: u32,
        report: ContactReport,
        now: u64,
        battle: &mut Battle,
        motion: &Motion,
    ) -> Result<bool, ErrorCode> {
        self.server_contact_before(actor, report, now, battle, motion, None)
    }

    pub(crate) fn server_contact_before(
        &mut self,
        actor: u32,
        report: ContactReport,
        now: u64,
        battle: &mut Battle,
        motion: &Motion,
        guard_deadline: Option<u64>,
    ) -> Result<bool, ErrorCode> {
        if !report.valid()
            || !self.alive(actor)
            || report.target_actor_id == actor
            || report.weapon_contact && report.target_actor_id == 0
        {
            return Ok(false);
        }
        if report.target_actor_id != 0
            && self
                .fighters
                .iter()
                .find(|f| f.id == actor)
                .zip(
                    self.fighters
                        .iter()
                        .find(|f| f.id == report.target_actor_id),
                )
                .is_some_and(|(source, target)| source.camp == target.camp)
        {
            return Ok(false);
        }
        let Some(action) = battle.sample_action(actor, report.action_id, report.state) else {
            return Ok(false);
        };
        let Some(direction) = [238, 244, 250, 256, 439]
            .iter()
            .position(|state| *state == action.state)
        else {
            return Ok(false);
        };
        let duration = if direction == 4 {
            crate::battle::DASH_DURATION_MS
        } else if action.weapon_id == crate::battle::FIST_WEAPON {
            632
        } else if direction == 2 {
            700
        } else {
            640
        };
        let contact_end = if direction == 4 {
            crate::battle::DASH_CONTACT_END_MS
        } else {
            duration - 100
        };
        let contact_start = if direction == 2 && action.weapon_id != crate::battle::FIST_WEAPON {
            crate::battle::THRUST_CONTACT_START_MS
        } else {
            0
        };
        let rebound = if direction == 4 {
            crate::battle::DASH_REBOUND_MS
        } else if action.weapon_id == crate::battle::FIST_WEAPON {
            [520, 450, 560, 450][direction]
        } else {
            [500, 500, 400, 530][direction]
        };
        let profile = battle.profile(actor, action.weapon_id);
        let attack = profile.and_then(|p| {
            p["attacks"].get(["300560", "300550", "300570", "300540", "700250"][direction])
        });
        let dash = profile.map(|p| &p["dashAttack"]);
        let duration = if direction == 4 {
            dash.and_then(|p| p["durationMs"].as_u64())
                .unwrap_or(duration)
        } else {
            attack
                .and_then(|p| p["phases"][2].as_u64())
                .unwrap_or(duration)
        };
        let contact_start = if direction == 4 {
            dash.and_then(|p| p["contactStartMs"].as_u64())
                .unwrap_or(contact_start)
        } else {
            attack
                .and_then(|p| p["contactStartMs"].as_u64())
                .unwrap_or(contact_start)
        };
        let contact_end = if direction == 4 {
            dash.and_then(|p| p["contactEndMs"].as_u64())
                .unwrap_or(contact_end)
        } else {
            attack
                .and_then(|p| p["contactEndMs"].as_u64())
                .unwrap_or(contact_end)
        };
        let terrain_rebound = if direction == 4 {
            dash.and_then(|p| p["reboundMs"].as_u64())
                .unwrap_or(rebound)
        } else {
            attack
                .and_then(|p| p["rebound"].as_u64())
                .unwrap_or(rebound)
        };
        let parried_duration = attack
            .and_then(|p| p["parried"].as_u64())
            .unwrap_or(rebound);
        if action.state != report.state
            || u64::from(report.elapsed_ms) < contact_start
            || u64::from(report.elapsed_ms) > contact_end
            || now < action.at
            || now - action.at > duration + REPORT_GRACE_MS
            || action
                .ended_at
                .is_some_and(|at| now.saturating_sub(at) > REPORT_GRACE_MS)
        {
            return Ok(false);
        }
        if let Some(timing) = &report.timing {
            tracing::debug!(
                actor,
                room_id = %self.room_id,
                match_id = %self.match_id,
                action_seq = report.action_seq,
                received_at = now,
                phase_at = action.at,
                sample_time_ms = timing.sample_time_ms,
                phase_start_ms = timing.phase_start_ms,
                gateway_time_ms = timing.gateway_time_ms,
                elapsed_ms = report.elapsed_ms,
                "contact timing observation"
            );
        }
        let contact_at = action
            .at
            .saturating_add(u64::from(report.elapsed_ms))
            .min(now);
        if now - contact_at > REPORT_GRACE_MS {
            return Ok(false);
        }
        let Some(source) = motion.at(actor, contact_at) else {
            return Ok(false);
        };
        if distance(&source.position, &report.position) > 5.0 {
            return Ok(false);
        }
        let target_id = report.target_actor_id;
        let mut parried = false;
        let mut guard_direction = None;
        if target_id != 0 {
            if !self.alive(target_id) {
                return Ok(false);
            }
            let Some(target) = motion.at(target_id, contact_at) else {
                return Ok(false);
            };
            // The two endpoints may render a 100ms-old pose. These coarse bounds
            // supplement the native blade report; map geometry is not yet authoritative.
            // Native blade contact determines reach. Bound each endpoint against
            // the reported contact instead of imposing one sword-length root cap.
            if (source.position[2] - target.position[2]).abs() > 2.0
                || distance(&target.position, &report.position) > 3.0
                || !facing(source.position, target.position, source.heading, 0.0)
            {
                return Ok(false);
            }
            let guard = guard_deadline.map_or_else(
                || battle.at(target_id, contact_at),
                |deadline| battle.at_before_receipt(target_id, contact_at, deadline),
            );
            if let Some(guard) = guard {
                guard_direction = [223, 224, 225, 226, 309]
                    .iter()
                    .position(|state| *state == guard.state);
                parried = guard_direction.is_some_and(|guard| {
                    if guard == 4 {
                        facing(target.position, source.position, target.heading, 0.5)
                    } else {
                        guard_matches(direction, guard, target.position, source.position, target.heading)
                    }
                });
            }
        }
        if report.weapon_contact && !parried {
            return Ok(false);
        }
        let index = if let Some(i) = self
            .hits
            .iter()
            .position(|s| s.actor == actor && s.action_id == report.action_id)
        {
            i
        } else {
            if self.hits.len() == crate::limits::MAX_SWING_HISTORY {
                self.hits.pop_front();
            }
            self.hits.push_back(SwingHits {
                actor,
                action_id: report.action_id,
                targets: Vec::new(),
                stopped: false,
            });
            self.hits.len() - 1
        };
        let hits = &mut self.hits[index];
        if hits.stopped || hits.targets.contains(&target_id) {
            return Ok(false);
        }
        hits.targets.push(target_id);
        tracing::debug!(
            actor,
            action_id = report.action_id,
            action_seq = report.action_seq,
            target_id,
            contact_at,
            weapon_contact = report.weapon_contact,
            ?guard_direction,
            parried,
            "combat contact committed"
        );
        let mut damage = 0;
        let mut hit_part = HitPart::Body;
        if target_id == 0 {
            // Ground ray normals may be reversed. The contact plane must also
            // pass near the source's feet; a ceiling is still an obstruction.
            if !ground(&report, &source.position) {
                hits.stopped = true;
                battle.react(
                    actor,
                    [466, 468, 467, 469, 443][direction],
                    now,
                    if action.weapon_id == crate::battle::FIST_WEAPON {
                        if profile.is_some() {
                            terrain_rebound
                        } else {
                            rebound - 100
                        }
                    } else {
                        terrain_rebound
                    },
                    false,
                    Some(target_id),
                );
            }
        } else if parried {
            hits.stopped = true;
            battle.react(
                actor,
                [240, 246, 252, 258, 443][direction],
                now,
                parried_duration,
                false,
                Some(target_id),
            );
            battle.react(
                target_id,
                [231, 232, 233, 234, 111][guard_direction.expect("matched guard")],
                now,
                1,
                false,
                Some(actor),
            );
        } else {
            let target = self
                .fighters
                .iter_mut()
                .find(|f| f.id == target_id)
                .expect("validated target");
            let Life::Alive(hp) = &mut target.life else {
                unreachable!()
            };
            let target_pose = motion
                .at(target_id, contact_at)
                .expect("validated target pose");
            let mut closing = 0.;
            if let Some((source_velocity, target_velocity)) = motion
                .velocity(actor, contact_at)
                .zip(motion.velocity(target_id, contact_at))
            {
                let delta: [f32; 3] =
                    std::array::from_fn(|i| target_pose.position[i] - source.position[i]);
                let length = delta.iter().map(|v| v * v).sum::<f32>().sqrt();
                if length > 0.01 {
                    closing = (0..3)
                        .map(|i| (source_velocity[i] - target_velocity[i]) * delta[i] / length)
                        .sum::<f32>()
                        .max(0.);
                }
            }
            let (amount, part) = body_damage(
                report.position[2] - target_pose.position[2],
                if actor >= crate::pve::FIRST_MONSTER_ID { 0. } else { closing },
                if actor >= crate::pve::FIRST_MONSTER_ID {
                    crate::pve::MONSTER_DAMAGE
                } else if direction == 4 { 40 } else { DAMAGE },
            );
            hit_part = part;
            damage = amount.min(*hp);
            *hp -= damage;
            if *hp == 0 {
                target.life = Life::Dead {
                    until: now + RESPAWN_MS,
                    ready: false,
                };
                target.deaths = target.deaths.saturating_add(1).min(i32::MAX as u32);
                target.streak = 0;
                let sender = self
                    .fighters
                    .iter_mut()
                    .find(|f| f.id == actor)
                    .expect("validated attacker");
                sender.kills = sender.kills.saturating_add(1).min(i32::MAX as u32);
                sender.streak = sender.streak.saturating_add(1).min(i32::MAX as u32);
                battle.react(target_id, 42, now, 0, true, Some(actor));
            } else {
                battle.react(
                    target_id,
                    35,
                    now,
                    crate::limits::HURT_RECOVERY_MS,
                    false,
                    Some(actor),
                );
            }
        }
        self.publish(
            CombatEffect::Contact {
                attacker: ActorId(actor),
                target: (target_id != 0).then_some(ActorId(target_id)),
                action_seq: report.action_seq,
                weapon_id: action.weapon_id,
                target_weapon_id: if parried && guard_direction == Some(4) {
                    battle.shield_weapon(target_id).unwrap_or(0)
                } else {
                    battle.weapon(target_id).unwrap_or(0)
                },
                parried,
                damage,
                hit_part,
                position: report.position,
                normal: report.normal,
                direction: report.direction,
                material: report.material,
                lives: self.health(),
            },
            now,
        );
        Ok(true)
    }
    pub(crate) fn advance(&mut self, now: u64) {
        let mut ready = Vec::new();
        for fighter in &mut self.fighters {
            if fighter.id >= crate::pve::FIRST_MONSTER_ID {
                continue;
            }
            if let Life::Dead {
                until,
                ready: published,
            } = &mut fighter.life
                && now >= *until
                && !*published
            {
                *published = true;
                ready.push(fighter.id);
            }
        }
        for id in ready {
            self.publish(
                CombatEffect::RespawnReady {
                    actor_id: ActorId(id),
                },
                now,
            );
        }
    }
    pub(crate) fn respawn(
        &mut self,
        actor: u32,
        now: u64,
        battle: &mut Battle,
        motion: &mut Motion,
    ) -> Result<(), ErrorCode> {
        let fighter = self
            .fighters
            .iter_mut()
            .find(|f| f.id == actor)
            .ok_or(ErrorCode::UnknownActor)?;
        if !matches!(fighter.life, Life::Dead { until, ready: true } if now >= until) {
            return Err(ErrorCode::WrongPhase);
        }
        fighter.life = Life::Alive(MAX_HEALTH);
        self.hits.retain(|s| s.actor != actor);
        motion.reset(actor);
        battle.react(actor, 2, now, 0, false, None);
        self.publish(
            CombatEffect::Respawned {
                actor_id: ActorId(actor),
                lives: self.health(),
            },
            now,
        );
        Ok(())
    }
    pub(crate) fn recovery(&self) -> (u64, Vec<Health>, Vec<u32>) {
        (
            self.event_seq,
            self.health(),
            self.fighters
                .iter()
                .filter_map(|f| matches!(f.life, Life::Dead { ready: true, .. }).then_some(f.id))
                .collect(),
        )
    }

    pub(crate) fn take_events(&mut self) -> Vec<CombatEvent> {
        std::mem::take(&mut self.pending)
    }
}
fn guard_matches(
    attack: usize,
    guard: usize,
    defender: [f32; 3],
    attacker: [f32; 3],
    heading: i16,
) -> bool {
    let dx = attacker[0] - defender[0];
    let dy = attacker[1] - defender[1];
    let length = dx.hypot(dy);
    // An overlapping root gives no reliable side; never grant omnidirectional guard.
    if length < 0.01 {
        return false;
    }
    let yaw = f32::from(heading).to_radians();
    let forward = (dx * yaw.cos() - dy * yaw.sin()) / length;
    if forward >= crate::limits::GUARD_FACING_COS {
        // Facing opponents have mirrored left/right controls.
        return match attack {
            0 => guard == 0,
            1 => guard == 3,
            2 | 4 => guard == 2,
            3 => guard == 1,
            _ => false,
        };
    }
    if forward < crate::limits::GUARD_SIDE_COS {
        return false;
    }
    // Outside the frontal cone, only the inward cut can meet a lateral guard.
    let right = -dx * yaw.sin() - dy * yaw.cos();
    let side = if right > 0.0 { 1 } else { 3 };
    attack == side && guard == side
}
fn distance(a: &[f32; 3], b: &[f32; 3]) -> f32 {
    a.iter()
        .zip(b)
        .map(|(a, b)| (a - b) * (a - b))
        .sum::<f32>()
        .sqrt()
}
fn ground(report: &ContactReport, source: &[f32; 3]) -> bool {
    report.normal[2].abs() >= 0.5
        && report
            .normal
            .iter()
            .zip(report.position.iter().zip(source))
            .map(|(n, (p, s))| n * (p - s))
            .sum::<f32>()
            / report.normal[2]
            <= 0.1
}
fn facing(source: [f32; 3], target: [f32; 3], heading: i16, limit: f32) -> bool {
    let dx = target[0] - source[0];
    let dy = target[1] - source[1];
    let len = dx.hypot(dy);
    let yaw = f32::from(heading).to_radians();
    len < 0.01 || (dx * yaw.cos() - dy * yaw.sin()) / len >= limit
}

#[cfg(test)]
mod shield_tests {
    use super::*;
    use crate::{battle::ActionIntent, loadout::{Equipment, Loadout}, motion::Pose};

    #[test]
    fn shield_blocks_front_strikes_but_not_back_strikes() {
        for heading in [0, 180] {
            let mut battle = Battle::new("room".into(), "match".into(), [1, 2].into_iter());
            battle.configure(2, Loadout { hero_id: 1001, equipment: vec![
                Equipment { slot: 1, weapon_id: 1010811 },
                Equipment { slot: 2, weapon_id: 1090211 },
            ]});
            let mut motion = Motion::new("room".into(), "match".into(), [1, 2].into_iter());
            for (id, x, yaw) in [(1, 0., 0), (2, 1., heading)] {
                motion.input(id, 1, Pose { position: [x, 0., 0.], heading: yaw,
                    forward_back: 0, left_right: 0, running: false, motion: None }, 400).unwrap();
            }
            battle.input(2, 1, ActionIntent::ShieldGuard, 0).unwrap();
            battle.input(1, 1, ActionIntent::AttackUp, 0).unwrap();
            battle.input(1, 2, ActionIntent::AttackRelease, 1).unwrap();
            battle.advance(400);
            let action = battle.current(1).unwrap();
            let mut combat = Combat::new("room".into(), "match".into(), [(1, 1), (2, 2)].into_iter());
            assert!(combat.input(1, 1, ContactReport { action_seq: action.seq,
                action_id: action.action_id.unwrap(), state: 238, elapsed_ms: 40,
                target_actor_id: 2, position: [1., 0., 1.], normal: [-1., 0., 0.],
                direction: [1., 0., 0.], material: 0, weapon_contact: false, timing: None },
                400, &mut battle, &motion).unwrap());
            let events = combat.take_events();
            let CombatEffect::Contact { parried, damage, target_weapon_id, .. } = events[0].effect else { panic!("contact") };
            assert_eq!(parried, heading == 180);
            assert_eq!(damage == 0, heading == 180);
            if parried {
                assert_eq!(battle.current(2).unwrap().state, 111);
                assert_eq!(target_weapon_id, 1090211);
            }
        }
    }
}
