//! Cooperative waves. Native monster rendering belongs to the gateway; decisions
//! and lives belong to the room. No client can submit AI decisions or spawn IDs.
use crate::{
    battle::{ActionIntent, Battle},
    combat::{Combat, ContactReport},
    motion::{Motion, Pose},
    round::{FinalRank, RoundResult},
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, sync::Arc};

pub const FIRST_MONSTER_ID: u32 = 0x40000001;
pub const AI_INTERVAL_MS: u64 = 100;
pub const MAX_MONSTERS: usize = 64;
pub const MAX_WAVES: usize = 32;
const CORPSE_MS: u64 = 1000;
const ATTACK_RANGE: f32 = 1.8;
const ATTACK_COOLDOWN_MS: u64 = 1800;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct MonsterKind {
    pub resource_id: u32,
    pub hero_id: u32,
    pub weapon_id: i32,
    pub health: u16,
    pub speed: f32,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Scenario {
    pub scenario_id: u32,
    pub map_id: u32,
    pub instance_id: u32,
    pub map_resource_id: u32,
    pub player_spawns: Vec<[f32; 3]>,
    pub monster_spawns: Vec<[f32; 3]>,
    pub monster: MonsterKind,
    pub waves: Vec<u16>,
    pub intermission_ms: u64,
}

impl Scenario {
    pub fn validate(&self) -> Result<(), &'static str> {
        let valid_id = |id: u32| id > 0 && id <= i32::MAX as u32;
        let valid_points = |points: &Vec<[f32; 3]>| {
            !points.is_empty()
                && points.len() <= MAX_MONSTERS
                && points
                    .iter()
                    .flatten()
                    .all(|v| v.is_finite() && v.abs() < 100_000.)
        };
        if ![
            self.scenario_id,
            self.map_id,
            self.instance_id,
            self.map_resource_id,
            self.monster.resource_id,
            self.monster.hero_id,
        ]
        .into_iter()
        .all(valid_id)
            || self.monster.weapon_id <= 0
            || self.monster.health == 0
            || !self.monster.speed.is_finite()
            || !(0.1..=10.).contains(&self.monster.speed)
            || !valid_points(&self.player_spawns)
            || !valid_points(&self.monster_spawns)
            || self.waves.is_empty()
            || self.waves.len() > MAX_WAVES
            || self
                .waves
                .iter()
                .any(|n| *n == 0 || usize::from(*n) > MAX_MONSTERS)
            || !(100..=60_000).contains(&self.intermission_ms)
        {
            return Err("invalid PVE scenario limits, resources or spawn points");
        }
        Ok(())
    }
}

#[derive(Clone, Default)]
pub struct Catalog(BTreeMap<u32, Arc<Scenario>>);
impl Catalog {
    pub fn from_json(data: &[u8]) -> Result<Self, &'static str> {
        if data.len() > 131_072 {
            return Err("PVE catalog exceeds 128 KiB");
        }
        #[derive(Deserialize)]
        #[serde(rename_all = "camelCase", deny_unknown_fields)]
        struct Input {
            scenarios: Vec<Scenario>,
        }
        let input: Input = serde_json::from_slice(data).map_err(|_| "invalid PVE catalog JSON")?;
        if input.scenarios.is_empty() || input.scenarios.len() > 32 {
            return Err("PVE catalog requires 1..32 scenarios");
        }
        let mut catalog = Self::default();
        for scenario in input.scenarios {
            scenario.validate()?;
            if catalog
                .0
                .insert(scenario.scenario_id, Arc::new(scenario))
                .is_some()
            {
                return Err("duplicate PVE scenario");
            }
        }
        Ok(catalog)
    }
    pub(crate) fn get(&self, id: u32) -> Option<Arc<Scenario>> {
        self.0.get(&id).cloned()
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum Phase {
    Intermission,
    Active,
    Victory,
    Defeat,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct MonsterView {
    pub actor_id: u32,
    pub kind: MonsterKind,
    pub position: [f32; 3],
    pub heading: i16,
    pub alive: bool,
    pub health: u16,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    pub scenario_id: u32,
    pub instance_id: u32,
    pub map_resource_id: u32,
    pub phase: Phase,
    pub wave: usize,
    pub total_waves: usize,
    pub next_wave_at_ms: Option<u64>,
    pub player_spawns: Vec<[f32; 3]>,
    pub monsters: Vec<MonsterView>,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Event {
    pub room_id: String,
    pub match_id: String,
    pub event_seq: u64,
    pub server_time_ms: u64,
    pub snapshot: Snapshot,
}

#[derive(Debug)]
struct Monster {
    view: MonsterView,
    input_seq: u32,
    contact_seq: u32,
    next_attack: u64,
    hit_action: Option<u16>,
    died_at: Option<u64>,
}

#[derive(Debug)]
pub(crate) struct Pve {
    pub(crate) scenario: Arc<Scenario>,
    room_id: String,
    match_id: String,
    phase: Phase,
    wave: usize,
    next_wave_at: u64,
    next_tick: u64,
    previous_tick: u64,
    next_id: u32,
    monsters: Vec<Monster>,
    event_seq: u64,
    pending: Vec<Event>,
}
impl Pve {
    pub(crate) fn new(
        scenario: Arc<Scenario>,
        room_id: String,
        match_id: String,
        now: u64,
    ) -> Self {
        Self {
            scenario,
            room_id,
            match_id,
            phase: Phase::Intermission,
            wave: 0,
            next_wave_at: now,
            next_tick: now,
            previous_tick: now,
            next_id: FIRST_MONSTER_ID,
            monsters: Vec::new(),
            event_seq: 0,
            pending: Vec::new(),
        }
    }
    pub(crate) fn event_sequence(&self) -> u64 {
        self.event_seq
    }

    pub(crate) fn snapshot(&self) -> Snapshot {
        Snapshot {
            scenario_id: self.scenario.scenario_id,
            instance_id: self.scenario.instance_id,
            map_resource_id: self.scenario.map_resource_id,
            phase: self.phase,
            wave: self.wave,
            total_waves: self.scenario.waves.len(),
            next_wave_at_ms: (self.phase == Phase::Intermission).then_some(self.next_wave_at),
            player_spawns: self.scenario.player_spawns.clone(),
            monsters: self.monsters.iter().map(|m| m.view.clone()).collect(),
        }
    }
    fn publish(&mut self, now: u64) {
        self.event_seq += 1;
        self.pending.push(Event {
            room_id: self.room_id.clone(),
            match_id: self.match_id.clone(),
            event_seq: self.event_seq,
            server_time_ms: now,
            snapshot: self.snapshot(),
        });
    }
    pub(crate) fn take_events(&mut self) -> Vec<Event> {
        std::mem::take(&mut self.pending)
    }

    pub(crate) fn advance(
        &mut self,
        now: u64,
        players: &[u32],
        battle: &mut Battle,
        motion: &mut Motion,
        combat: &mut Combat,
    ) {
        if now < self.next_tick || matches!(self.phase, Phase::Victory | Phase::Defeat) {
            return;
        }
        self.next_tick = now.saturating_add(AI_INTERVAL_MS);
        let dt = now.saturating_sub(self.previous_tick).min(AI_INTERVAL_MS) as f32 / 1000.;
        self.previous_tick = now;
        if players.iter().all(|id| !combat.alive(*id)) {
            self.phase = Phase::Defeat;
            self.publish(now);
            return;
        }
        let mut changed = false;
        let lives = combat.health();
        for monster in &mut self.monsters {
            let health = lives
                .iter()
                .find(|life| life.actor_id.0 == monster.view.actor_id)
                .map_or(0, |life| life.health);
            if health != monster.view.health {
                monster.view.health = health;
                changed = true;
            }
            if monster.view.alive && !combat.alive(monster.view.actor_id) {
                monster.view.alive = false;
                monster.died_at = Some(now);
                changed = true;
            }
        }
        self.monsters.retain(|m| {
            if m.died_at
                .is_some_and(|at| now.saturating_sub(at) >= CORPSE_MS)
            {
                battle.remove(m.view.actor_id);
                motion.remove(m.view.actor_id);
                combat.remove(m.view.actor_id);
                changed = true;
                false
            } else {
                true
            }
        });
        if self.phase == Phase::Active && self.monsters.is_empty() {
            if self.wave == self.scenario.waves.len() {
                self.phase = Phase::Victory;
            } else {
                self.phase = Phase::Intermission;
                self.next_wave_at = now.saturating_add(self.scenario.intermission_ms);
            }
            changed = true;
        }
        if self.phase == Phase::Intermission && now >= self.next_wave_at {
            let count = self.scenario.waves[self.wave];
            self.wave += 1;
            self.phase = Phase::Active;
            for index in 0..usize::from(count) {
                let id = self.next_id;
                self.next_id += 1;
                let position =
                    self.scenario.monster_spawns[index % self.scenario.monster_spawns.len()];
                battle.spawn(id, self.scenario.monster.weapon_id);
                motion.spawn(id);
                combat.spawn(id, self.scenario.monster.health);
                self.monsters.push(Monster {
                    view: MonsterView {
                        actor_id: id,
                        kind: self.scenario.monster.clone(),
                        position,
                        heading: 0,
                        alive: true,
                        health: self.scenario.monster.health,
                    },
                    input_seq: 0,
                    contact_seq: 0,
                    next_attack: now,
                    hit_action: None,
                    died_at: None,
                });
            }
            changed = true;
        }
        for monster in self.monsters.iter_mut().filter(|m| m.view.alive) {
            let id = monster.view.actor_id;
            let target = players
                .iter()
                .filter(|id| combat.alive(**id))
                .filter_map(|id| motion.latest(*id, now).map(|pose| (*id, pose.position)))
                .min_by(|a, b| {
                    distance(monster.view.position, a.1)
                        .total_cmp(&distance(monster.view.position, b.1))
                });
            let state = battle.current(id);
            let idle = state.is_none_or(|s| s.state == 2);
            if let Some((target_id, position)) = target {
                let delta = [
                    position[0] - monster.view.position[0],
                    position[1] - monster.view.position[1],
                ];
                let length = delta[0].hypot(delta[1]);
                if idle && length > 0.01 {
                    monster.view.heading = (-delta[1]).atan2(delta[0]).to_degrees().round() as i16;
                    if length > ATTACK_RANGE {
                        let step = (monster.view.kind.speed * dt).min(length - ATTACK_RANGE);
                        monster.view.position[0] += delta[0] / length * step;
                        monster.view.position[1] += delta[1] / length * step;
                    }
                }
                monster.input_seq += 1;
                let pose = Pose {
                    position: monster.view.position,
                    heading: monster.view.heading,
                    forward_back: if idle && length > ATTACK_RANGE { -1 } else { 0 },
                    left_right: 0,
                    running: false,
                    motion: None,
                };
                // Generated motion stays within the same bounded transport contract.
                motion
                    .input(id, monster.input_seq, pose, now)
                    .expect("bounded AI pose");
                if idle
                    && length <= ATTACK_RANGE
                    && (monster.view.position[2] - position[2]).abs() <= 1.5
                    && now >= monster.next_attack
                {
                    monster.input_seq += 1;
                    battle
                        .input(id, monster.input_seq, ActionIntent::AttackUp, now)
                        .expect("known AI actor");
                    monster.input_seq += 1;
                    battle
                        .input(id, monster.input_seq, ActionIntent::AttackRelease, now)
                        .expect("known AI actor");
                    monster.next_attack = now.saturating_add(ATTACK_COOLDOWN_MS);
                }
                if let Some(action) = state.filter(|s| {
                    s.state == 238
                        && now.saturating_sub(s.at) >= 200
                        && now.saturating_sub(s.at) <= 540
                }) && monster.hit_action != Some(action.seq)
                {
                    monster.hit_action = Some(action.seq);
                    if length <= ATTACK_RANGE
                        && (monster.view.position[2] - position[2]).abs() <= 1.5
                    {
                        monster.contact_seq += 1;
                        let yaw = f32::from(monster.view.heading).to_radians();
                        let _ = combat.input(
                            id,
                            monster.contact_seq,
                            ContactReport {
                                action_seq: action.seq,
                                action_id: action.action_id.unwrap_or(0),
                                state: action.state,
                                elapsed_ms: (now - action.at) as u16,
                                target_actor_id: target_id,
                                position: [position[0], position[1], position[2] + 1.],
                                normal: [0., 0., 1.],
                                direction: [yaw.cos(), -yaw.sin(), 0.],
                                material: 0,
                                weapon_contact: false,
                                timing: None,
                            },
                            now,
                            battle,
                            motion,
                        );
                    }
                }
            } else {
                monster.input_seq += 1;
                motion
                    .input(
                        id,
                        monster.input_seq,
                        Pose {
                            position: monster.view.position,
                            heading: monster.view.heading,
                            forward_back: 0,
                            left_right: 0,
                            running: false,
                            motion: None,
                        },
                        now,
                    )
                    .expect("stationary AI pose");
            }
        }
        // PVE uses bounded server-space melee reach until native monster blade
        // contacts are available. Reports and AI share swing deduplication.
        for player in players
            .iter()
            .copied()
            .filter(|id| combat.alive(*id))
            .collect::<Vec<_>>()
        {
            let Some(action) = battle.current(player) else {
                continue;
            };
            let elapsed = now.saturating_sub(action.at);
            if ![238, 244, 250, 256, 439].contains(&action.state) || !(200..=540).contains(&elapsed)
            {
                continue;
            }
            let Some(source) = motion.latest(player, now).cloned() else {
                continue;
            };
            let yaw = f32::from(source.heading).to_radians();
            for monster in self.monsters.iter().filter(|m| m.view.alive) {
                let position = monster.view.position;
                let dx = position[0] - source.position[0];
                let dy = position[1] - source.position[1];
                let length = dx.hypot(dy);
                if length > 3.2
                    || (position[2] - source.position[2]).abs() > 1.5
                    || length > 0.01 && (dx * yaw.cos() - dy * yaw.sin()) / length < 0.2
                {
                    continue;
                }
                let _ = combat.server_contact(
                    player,
                    ContactReport {
                        action_seq: action.seq,
                        action_id: action.action_id.unwrap_or(0),
                        state: action.state,
                        elapsed_ms: elapsed as u16,
                        target_actor_id: monster.view.actor_id,
                        position: [position[0], position[1], position[2] + 1.],
                        normal: [0., 0., 1.],
                        direction: [yaw.cos(), -yaw.sin(), 0.],
                        material: 0,
                        weapon_contact: false,
                        timing: None,
                    },
                    now,
                    battle,
                    motion,
                );
            }
        }
        if changed {
            self.publish(now);
        }
    }
    pub(crate) fn result(
        &mut self,
        players: &[u32],
        combat: &Combat,
        timeout: bool,
        now: u64,
    ) -> Option<RoundResult> {
        if timeout && !matches!(self.phase, Phase::Victory | Phase::Defeat) {
            self.phase = Phase::Defeat;
            self.publish(now);
        }
        if !matches!(self.phase, Phase::Victory | Phase::Defeat) {
            return None;
        }
        let mut lives = combat.health();
        lives.retain(|life| players.contains(&life.actor_id.0));
        let mut sorted = lives.clone();
        sorted.sort_by_key(|life| (std::cmp::Reverse(life.kills), life.deaths, life.actor_id.0));
        let ranks = sorted
            .iter()
            .enumerate()
            .map(|(i, life)| FinalRank {
                actor_id: life.actor_id.0,
                rank: i as u32 + 1,
            })
            .collect();
        Some(RoundResult {
            reason: if self.phase == Phase::Victory {
                "pve-cleared"
            } else if timeout {
                "timeout"
            } else {
                "pve-wipe"
            },
            result: if self.phase == Phase::Victory { 1 } else { 2 },
            ended_at_ms: now,
            lives,
            ranks,
        })
    }
}
fn distance(a: [f32; 3], b: [f32; 3]) -> f32 {
    (a[0] - b[0]).hypot(a[1] - b[1])
}
