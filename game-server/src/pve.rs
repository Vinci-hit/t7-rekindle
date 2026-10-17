//! Cooperative encounters and waves. Native monster rendering belongs to the gateway; decisions
//! and lives belong to the room. No client can submit AI decisions or spawn IDs.
use crate::pve_goal::{Goal, Progress as GoalProgress, Runtime as GoalRuntime};
use crate::pve_navigation::Navigation;
use crate::{
    battle::{ActionIntent, Battle},
    combat::{Combat, ContactReport},
    motion::{Motion, Pose},
    round::{FinalRank, RoundResult},
};
use serde::{Deserialize, Serialize};
use crate::{pve_ai, pve_brain, pve_steering};
use std::{collections::BTreeMap, sync::Arc};

pub const FIRST_MONSTER_ID: u32 = 0x40000001;
pub const FIRST_OBJECT_ID: u32 = 0x50000001;
pub const AI_INTERVAL_MS: u64 = 100;
const MAX_AI_STEP_MS: u64 = AI_INTERVAL_MS;
const GOAL_PUBLISH_INTERVAL_MS: u64 = 500;
const ROUTES_PER_TICK: usize = 8;
const ROUTE_REFRESH_MS: u64 = 1000;
pub const MAX_MONSTERS: usize = 64;
pub const MAX_WAVES: usize = 128;
pub const MAX_CATALOG_BYTES: usize = 4 * 1024 * 1024;
const CORPSE_MS: u64 = 1000;
// Conservative fallback until native monster blade trajectories are available.
const ATTACK_RANGE: f32 = 1.1;
const ATTACK_CONTACT_START_MS: u64 = 360;
const ATTACK_CONTACT_END_MS: u64 = 540;
const TARGET_ATTACK_SPACING_MS: u64 = 700;
pub(crate) const MONSTER_DAMAGE: u16 = 8;
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

/// Native non-combat birth clips; these are not battle action states.
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum BirthAnimation {
    Emerge1,
    Emerge2,
    Emerge3,
    Vault1,
    Vault2,
}
impl BirthAnimation {
    pub fn duration_ms(self) -> u64 {
        match self {
            Self::Emerge1 | Self::Emerge2 | Self::Emerge3 => 3167,
            Self::Vault1 => 1800,
            Self::Vault2 => 3433,
        }
    }
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub struct MonsterBirth {
    pub animation: BirthAnimation,
    pub started_at_ms: u64,
    pub ends_at_ms: u64,
}

/// Batches are paced from actual emission time; a delayed tick never catches up
/// by creating several batches at once.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SpawnSchedule {
    pub batch_size: u16,
    pub interval_ms: u64,
}

/// A server-scripted entry ending at the configured spawn point.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SpawnEntrance {
    pub start_offset: [f32; 3],
    pub arc_height: f32,
    pub duration_ms: u64,
}

impl SpawnEntrance {
    fn valid(&self) -> bool {
        let distance = self.start_offset.iter().map(|v| v * v).sum::<f32>().sqrt();
        self.start_offset.iter().all(|v| v.is_finite() && v.abs() <= 20.)
            && self.arc_height.is_finite()
            && (0.0..=10.).contains(&self.arc_height)
            && (500..=10_000).contains(&self.duration_ms)
            // Upper bound on the path's derivative, below the pose speed limit.
            && (distance + 4. * self.arc_height) * 1000. / self.duration_ms as f32 <= 20.
    }

    fn position(&self, landing: [f32; 3], elapsed_ms: u64) -> [f32; 3] {
        let t = (elapsed_ms as f32 / self.duration_ms as f32).min(1.);
        let mut position = std::array::from_fn(|i| landing[i] + self.start_offset[i] * (1. - t));
        position[2] += 4. * self.arc_height * t * (1. - t);
        position
    }
}

/// An encounter starts once a living player reaches this region. Unvisited
/// regions remain dormant; proximity mode retains survivors from older regions.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub enum EncounterOrder {
    #[default]
    Nearest,
    Sequential,
    /// Regions activate by proximity; surviving monsters do not gate later regions.
    Proximity,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct EncounterObjective {
    pub approach: String,
    pub active: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub approach_task_id: Option<u32>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub active_task_id: Option<u32>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Encounter {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub monster_pool: Vec<MonsterKind>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub objects: Vec<StageObject>,
    #[serde(default)]
    pub goal: Goal,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub objective: Option<EncounterObjective>,
    pub trigger_position: [f32; 3],
    pub trigger_radius: f32,
    pub monster_spawns: Vec<[f32; 3]>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub monster: Option<MonsterKind>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub birth: Option<BirthAnimation>,
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(tag = "type", rename_all = "camelCase", deny_unknown_fields)]
pub enum ObjectBinding {
    Static,
    Escort,
    Defend,
    Collect { index: usize },
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct StageObject {
    pub resource_id: u32,
    pub position: [f32; 3],
    pub binding: ObjectBinding,
}
#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ObjectView {
    pub actor_id: u32,
    pub resource_id: u32,
    pub position: [f32; 3],
    pub health: u16,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Scenario {
    #[serde(default)]
    pub encounter_order: EncounterOrder,
    pub scenario_id: u32,
    pub map_id: u32,
    pub instance_id: u32,
    pub map_resource_id: u32,
    pub player_spawns: Vec<[f32; 3]>,
    pub monster_spawns: Vec<[f32; 3]>,
    pub monster: MonsterKind,
    pub waves: Vec<u16>,
    pub intermission_ms: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub spawn_schedule: Option<SpawnSchedule>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub spawn_entrances: Vec<SpawnEntrance>,
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub encounters: Vec<Encounter>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub navigation: Option<Navigation>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub birth: Option<BirthAnimation>,
}

impl Scenario {
    pub fn validate(&self) -> Result<(), &'static str> {
        let valid_id = |id: u32| id > 0 && id <= i32::MAX as u32;
        let valid_points = |points: &[[f32; 3]]| {
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
            || (self.encounter_order != EncounterOrder::Nearest && self.encounters.is_empty())
            || (!self.spawn_entrances.is_empty()
                && (self.birth.is_some()
                    || self.spawn_entrances.len() != self.monster_spawns.len()
                    || self.spawn_entrances.iter().any(|entry| !entry.valid())
                    || self.monster_spawns.iter().zip(&self.spawn_entrances).any(
                        |(landing, entry)| {
                            landing.iter().zip(entry.start_offset).any(|(v, offset)| {
                                v.abs() + offset.abs() + entry.arc_height >= 100_000.
                            })
                        },
                    )))
            || (!self.encounters.is_empty()
                && (self.encounters.len() != self.waves.len()
                    || !self.spawn_entrances.is_empty()
                    || self.encounters.iter().any(|entry| {
                        entry.monster_pool.len() > 32
                            || (!entry.monster_pool.is_empty() && entry.monster.is_some())
                            || entry.monster_pool.iter().any(|kind| {
                                !valid_id(kind.resource_id)
                                    || !valid_id(kind.hero_id)
                                    || kind.weapon_id <= 0
                                    || kind.health == 0
                                    || !kind.speed.is_finite()
                                    || !(0.1..=10.).contains(&kind.speed)
                            })
                            || entry.objects.len() > 64
                            || entry.objects.iter().any(|object| {
                                !valid_id(object.resource_id)
                                    || !valid_points(std::slice::from_ref(&object.position))
                                    || !match (&object.binding, &entry.goal) {
                                        (ObjectBinding::Static, _) => true,
                                        (ObjectBinding::Escort, Goal::Escort { .. }) => true,
                                        (ObjectBinding::Defend, Goal::Defend { .. }) => true,
                                        (
                                            ObjectBinding::Collect { index },
                                            Goal::Collect { points, .. },
                                        ) => *index < points.len(),
                                        _ => false,
                                    }
                            })
                            || entry.goal.validate().is_err()
                            || entry.objective.as_ref().is_some_and(|objective| {
                                [objective.approach_task_id, objective.active_task_id]
                                    .into_iter()
                                    .flatten()
                                    .any(|id| !valid_id(id))
                                    || [&objective.approach, &objective.active].iter().any(|text| {
                                        text.trim().is_empty()
                                            || text.chars().count() > 128
                                            || text.chars().any(char::is_control)
                                    })
                            })
                            || !valid_points(std::slice::from_ref(&entry.trigger_position))
                            || !entry.trigger_radius.is_finite()
                            || !(1.0..=50.0).contains(&entry.trigger_radius)
                            || !valid_points(&entry.monster_spawns)
                            || entry.monster.as_ref().is_some_and(|kind| {
                                !valid_id(kind.resource_id)
                                    || !valid_id(kind.hero_id)
                                    || kind.weapon_id <= 0
                                    || kind.health == 0
                                    || !kind.speed.is_finite()
                                    || !(0.1..=10.).contains(&kind.speed)
                            })
                    })))
            || self.waves.is_empty()
            || self.waves.len() > MAX_WAVES
            || self.waves.iter().enumerate().any(|(i, n)| {
                (*n == 0 && self.encounters.get(i).is_none_or(|e| e.goal.is_clear()))
                    || usize::from(*n) > MAX_MONSTERS
            })
            || self.spawn_schedule.as_ref().is_some_and(|schedule| {
                schedule.batch_size == 0
                    || usize::from(schedule.batch_size) > MAX_MONSTERS
                    || !(100..=60_000).contains(&schedule.interval_ms)
            })
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
        if data.len() > MAX_CATALOG_BYTES {
            return Err("PVE catalog exceeds 4 MiB");
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
        for mut scenario in input.scenarios {
            scenario.validate()?;
            if let Some(nav) = &mut scenario.navigation {
                nav.prepare()?;
                if scenario
                    .monster_spawns
                    .iter()
                    .chain(
                        scenario
                            .encounters
                            .iter()
                            .flat_map(|entry| entry.monster_spawns.iter()),
                    )
                    .any(|point| nav.project(*point).is_none())
                {
                    return Err("PVE spawn point is outside navigation");
                }
                if scenario.encounters.iter().any(|entry| {
                    entry
                        .monster_spawns
                        .iter()
                        .any(|point| !nav.connected(entry.trigger_position, *point))
                }) {
                    return Err(
                        "PVE encounter spawns must share a walking region with their trigger",
                    );
                }
                for entry in &mut scenario.encounters {
                    if entry
                        .goal
                        .points()
                        .iter()
                        .any(|p| nav.project(*p).is_none())
                    {
                        return Err("PVE objective point is outside navigation");
                    }
                    if let Goal::Escort { path, .. } = &mut entry.goal {
                        let mut corridor = vec![path[0]];
                        for pair in path.windows(2) {
                            let segment = nav.route(pair[0], pair[1]);
                            if segment.is_empty() {
                                return Err("PVE escort route is disconnected");
                            }
                            corridor.extend(segment);
                        }
                        if corridor.len() > 16_384 {
                            return Err("PVE escort route exceeds limit");
                        }
                        *path = corridor;
                    }
                }
            }
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
    /// Enemy roster slot (RID) every client presents this monster in, exactly like an enemy player.
    /// The original client only samples, animates and collides actors in its 2..=20 roster, so
    /// concurrently living monsters are bounded by the enemy team's slots.
    pub local_rid: u32,
    pub kind: MonsterKind,
    pub position: [f32; 3],
    pub heading: i16,
    pub alive: bool,
    pub health: u16,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub birth: Option<MonsterBirth>,
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Snapshot {
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub objects: Vec<ObjectView>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub goal: Option<GoalProgress>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub objective: Option<ObjectiveView>,
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

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct ObjectiveView {
    #[serde(skip_serializing_if = "Option::is_none")]
    pub task_id: Option<u32>,
    /// One-based encounter index, independent of the number already cleared.
    pub encounter: usize,
    pub text: String,
    pub target_position: [f32; 3],
    pub active: bool,
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
enum MonsterLocomotion {
    Spawning {
        until: u64,
    },
    Entering {
        landing: [f32; 3],
        path: SpawnEntrance,
        elapsed_ms: u64,
        updated_at: u64,
    },
    Hunting,
}

#[derive(Debug)]
struct Monster {
    view: MonsterView,
    input_seq: u32,
    contact_seq: u32,
    next_attack: u64,
    hit_action: Option<u16>,
    attack_target: Option<u32>,
    died_at: Option<u64>,
    locomotion: MonsterLocomotion,
    route: std::collections::VecDeque<[f32; 3]>,
    next_route_at: u64,
    route_target: Option<u32>,
    stuck: pve_ai::Progress,
    detour_until: u64,
    detour_flip: bool,
    /// Targets given up on as unreachable, until the stored time.
    ignored: Vec<(u32, u64)>,
}

#[derive(Debug)]
pub(crate) struct Pve {
    pub(crate) scenario: Arc<Scenario>,
    room_id: String,
    match_id: String,
    phase: Phase,
    wave: usize,
    active_encounter: Option<usize>,
    goal: Option<GoalRuntime>,
    visited_encounters: std::collections::BTreeSet<usize>,
    next_wave_at: u64,
    remaining_to_spawn: u16,
    spawned_in_wave: usize,
    next_batch_at: u64,
    next_tick: u64,
    previous_tick: u64,
    goal_dirty_since: Option<u64>,
    next_id: u32,
    monsters: Vec<Monster>,
    target_attack_deadlines: BTreeMap<u32, u64>,
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
            active_encounter: None,
            goal: None,
            visited_encounters: std::collections::BTreeSet::new(),
            next_wave_at: now,
            remaining_to_spawn: 0,
            spawned_in_wave: 0,
            next_batch_at: now,
            next_tick: now,
            previous_tick: now,
            goal_dirty_since: None,
            next_id: FIRST_MONSTER_ID,
            monsters: Vec::new(),
            target_attack_deadlines: BTreeMap::new(),
            event_seq: 0,
            pending: Vec::new(),
        }
    }
    pub(crate) fn event_sequence(&self) -> u64 {
        self.event_seq
    }

    pub(crate) fn snapshot(&self) -> Snapshot {
        let objective = if matches!(self.phase, Phase::Victory | Phase::Defeat) {
            None
        } else {
            let index = if self.phase == Phase::Active {
                self.active_encounter
            } else if self.scenario.encounter_order == EncounterOrder::Sequential {
                Some(self.wave)
            } else if self.scenario.encounter_order == EncounterOrder::Proximity {
                (0..self.scenario.encounters.len()).find(|index| !self.visited_encounters.contains(index))
            } else {
                None
            };
            index.and_then(|index| {
                let encounter = self.scenario.encounters.get(index)?;
                let objective = encounter.objective.as_ref()?;
                let active = self.phase == Phase::Active;
                Some(ObjectiveView {
                    task_id: if active {
                        objective.active_task_id
                    } else {
                        objective.approach_task_id
                    },
                    encounter: index + 1,
                    text: if active {
                        &objective.active
                    } else {
                        &objective.approach
                    }
                    .clone(),
                    target_position: self
                        .goal
                        .as_ref()
                        .and_then(GoalRuntime::progress)
                        .map_or(encounter.trigger_position, |progress| {
                            progress.target_position
                        }),
                    active,
                })
            })
        };
        Snapshot {
            objects: if self.phase == Phase::Active || self.phase == Phase::Defeat {
                self.active_encounter
                    .map(|index| {
                        self.scenario.encounters[index]
                            .objects
                            .iter()
                            .enumerate()
                            .filter_map(|(slot, object)| {
                                let goal = self.goal.as_ref()?;
                                if let ObjectBinding::Collect { index } = object.binding
                                    && goal.collected(index)
                                {
                                    return None;
                                }
                                Some(ObjectView {
                                    actor_id: FIRST_OBJECT_ID + (index * 64 + slot) as u32,
                                    resource_id: object.resource_id,
                                    position: if matches!(
                                        object.binding,
                                        ObjectBinding::Escort | ObjectBinding::Defend
                                    ) {
                                        goal.position()
                                    } else {
                                        object.position
                                    },
                                    health: if object.binding == ObjectBinding::Defend {
                                        goal.health().unwrap_or(1)
                                    } else {
                                        1
                                    },
                                })
                            })
                            .collect()
                    })
                    .unwrap_or_default()
            } else {
                vec![]
            },
            goal: self.goal.as_ref().and_then(GoalRuntime::progress),
            objective,
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
        self.goal_dirty_since = None;
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
        // Fixed-rate schedule anchored on the previous deadline, so room-tick quantisation and
        // scheduler jitter do not stretch the average period; integrate the real elapsed time.
        self.next_tick = self.next_tick.saturating_add(AI_INTERVAL_MS);
        if self.next_tick <= now {
            self.next_tick = now.saturating_add(AI_INTERVAL_MS);
        }
        let dt = now.saturating_sub(self.previous_tick).min(MAX_AI_STEP_MS) as f32 / 1000.;
        self.previous_tick = now;
        if players.iter().all(|id| !combat.alive(*id)) {
            self.phase = Phase::Defeat;
            self.publish(now);
            return;
        }
        let mut changed = false;
        if self.phase == Phase::Active {
            let poses: Vec<_> = players
                .iter()
                .filter(|id| combat.alive(**id))
                .filter_map(|id| motion.latest(*id, now).map(|p| p.position))
                .collect();
            if let Some(goal) = &mut self.goal {
                let previous = goal.progress();
                goal.advance(&poses, (dt * 1000.).round() as u64);
                let current = goal.progress();
                if previous != current {
                    // Continuous progress (defend timer, escort position) is coalesced; discrete
                    // changes (completion, health, step) publish immediately.
                    let discrete = match (&previous, &current) {
                        (Some(a), Some(b)) => {
                            a.complete != b.complete
                                || a.health != b.health
                                || (a.kind != "defend" && a.current != b.current)
                        }
                        _ => true,
                    };
                    if discrete {
                        changed = true;
                    } else {
                        self.goal_dirty_since.get_or_insert(now);
                    }
                }
            }
        }
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
                monster.view.birth = None;
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
        if self.phase == Phase::Active
            && self.remaining_to_spawn == 0
            && (self.monsters.is_empty()
                || self.scenario.encounter_order == EncounterOrder::Proximity)
            && self.goal.as_ref().is_none_or(GoalRuntime::complete)
        {
            self.goal = None;
            if self.wave == self.scenario.waves.len() && self.monsters.is_empty() {
                self.phase = Phase::Victory;
            } else {
                self.phase = Phase::Intermission;
                self.next_wave_at = now.saturating_add(self.scenario.intermission_ms);
            }
            changed = true;
        }
        if self.phase == Phase::Intermission
            && self.wave == self.scenario.waves.len()
            && self.monsters.is_empty()
        {
            self.phase = Phase::Victory;
            changed = true;
        }
        if self.phase == Phase::Intermission && now >= self.next_wave_at {
            let selected = if self.wave == self.scenario.waves.len() {
                None
            } else if self.scenario.encounters.is_empty() {
                Some(self.wave)
            } else {
                self.scenario
                    .encounters
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| !self.visited_encounters.contains(index))
                    .filter(|(index, _)| {
                        self.scenario.encounter_order != EncounterOrder::Sequential
                            || *index == self.wave
                    })
                    .filter_map(|(index, entry)| {
                        players
                            .iter()
                            .filter(|id| combat.alive(**id))
                            .filter_map(|id| motion.latest(*id, now))
                            .filter(|pose| {
                                (pose.position[2] - entry.trigger_position[2]).abs() <= 3.0
                                    && (self.scenario.encounter_order == EncounterOrder::Proximity
                                        || self.scenario.navigation.as_ref().is_none_or(|nav| {
                                            nav.connected(pose.position, entry.trigger_position)
                                        }))
                            })
                            .map(|pose| distance(pose.position, entry.trigger_position))
                            .filter(|distance| *distance <= entry.trigger_radius)
                            .min_by(f32::total_cmp)
                            .map(|distance| (index, distance))
                    })
                    .min_by(|a, b| a.1.total_cmp(&b.1).then(a.0.cmp(&b.0)))
                    .map(|(index, _)| index)
            };
            if let Some(index) = selected {
                self.active_encounter = (!self.scenario.encounters.is_empty()).then_some(index);
                self.goal = self
                    .active_encounter
                    .map(|i| GoalRuntime::new(self.scenario.encounters[i].goal.clone()));
                self.visited_encounters.insert(index);
                self.remaining_to_spawn = self.scenario.waves[index];
                self.spawned_in_wave = 0;
                self.next_batch_at = now;
                self.wave += 1;
                self.phase = Phase::Active;
                changed = true;
            }
        }
        if self.phase == Phase::Active && self.remaining_to_spawn > 0 && now >= self.next_batch_at {
            let count = if self.scenario.encounter_order == EncounterOrder::Proximity {
                // Each reached region emits independently of kills in older regions.
                self.remaining_to_spawn
            } else {
                self.scenario.spawn_schedule.as_ref().map_or(self.remaining_to_spawn, |schedule| {
                    schedule.batch_size.min(self.remaining_to_spawn)
                })
            };
            // A monster is only spawned into a free enemy slot; the rest of the wave waits for one.
            let free = (crate::limits::ENEMY_FIRST_RID..crate::limits::FRIENDLY_FIRST_RID)
                .filter(|rid| !self.monsters.iter().any(|m| m.view.local_rid == *rid))
                .count();
            let count = usize::from(count)
                .min(MAX_MONSTERS.saturating_sub(self.monsters.len()))
                .min(free);
            let encounter = self
                .active_encounter
                .map(|index| &self.scenario.encounters[index]);
            let points =
                encounter.map_or(&self.scenario.monster_spawns, |entry| &entry.monster_spawns);
            let kind = encounter
                .and_then(|entry| entry.monster.as_ref())
                .unwrap_or(&self.scenario.monster);
            for index in self.spawned_in_wave..self.spawned_in_wave + count {
                let kind = encounter
                    .and_then(|e| {
                        (!e.monster_pool.is_empty())
                            .then(|| &e.monster_pool[index % e.monster_pool.len()])
                    })
                    .unwrap_or(kind);
                let id = self.next_id;
                self.next_id += 1;
                let local_rid = (crate::limits::ENEMY_FIRST_RID..crate::limits::FRIENDLY_FIRST_RID)
                    .find(|rid| !self.monsters.iter().any(|m| m.view.local_rid == *rid))
                    .expect("spawn count is bounded by free enemy slots");
                let point_index = index % points.len();
                // More monsters than spawn points: each reuse spirals outward from the point
                // instead of stacking every monster on the identical coordinate.
                let offset = pve_steering::spawn_offset(index / points.len());
                let spread = [
                    points[point_index][0] + offset[0],
                    points[point_index][1] + offset[1],
                    points[point_index][2],
                ];
                let landing = self
                    .scenario
                    .navigation
                    .as_ref()
                    .and_then(|nav| nav.project(spread).or_else(|| nav.project(points[point_index])))
                    .unwrap_or(spread);
                let birth = encounter
                    .and_then(|entry| entry.birth)
                    .or(self.scenario.birth)
                    .map(|animation| MonsterBirth {
                        animation,
                        started_at_ms: now,
                        ends_at_ms: now.saturating_add(animation.duration_ms()),
                    });
                let entrance = self.scenario.spawn_entrances.get(point_index);
                let position = entrance.map_or(landing, |entry| entry.position(landing, 0));
                let heading = entrance.map_or(0, |entry| {
                    entry.start_offset[1]
                        .atan2(-entry.start_offset[0])
                        .to_degrees()
                        .round() as i16
                });
                let locomotion = entrance.map_or_else(
                    || {
                        birth.map_or(MonsterLocomotion::Hunting, |birth| {
                            MonsterLocomotion::Spawning {
                                until: birth.ends_at_ms,
                            }
                        })
                    },
                    |path| MonsterLocomotion::Entering {
                        landing,
                        path: path.clone(),
                        elapsed_ms: 0,
                        updated_at: now,
                    },
                );
                battle.spawn(id, kind.weapon_id);
                motion.spawn(id);
                combat.spawn(id, kind.health);
                self.monsters.push(Monster {
                    view: MonsterView {
                        actor_id: id,
                        local_rid,
                        kind: kind.clone(),
                        position,
                        heading,
                        alive: true,
                        health: kind.health,
                        birth,
                    },
                    input_seq: 0,
                    contact_seq: 0,
                    next_attack: now,
                    hit_action: None,
                    attack_target: None,
                    died_at: None,
                    locomotion,
                    route: std::collections::VecDeque::new(),
                    next_route_at: now,
                    route_target: None,
                    stuck: pve_ai::Progress::default(),
                    detour_until: 0,
                    detour_flip: false,
                    ignored: Vec::new(),
                });
            }
            self.remaining_to_spawn -= count as u16;
            self.spawned_in_wave += count;
            self.next_batch_at = now.saturating_add(
                self.scenario
                    .spawn_schedule
                    .as_ref()
                    .map_or(AI_INTERVAL_MS, |s| s.interval_ms),
            );
            changed |= count > 0;
        }
        self.target_attack_deadlines.retain(|_, deadline| *deadline > now);
        let mut routes_left = ROUTES_PER_TICK;
        // Positions at the start of the step: every monster steers against the same crowd snapshot.
        let crowd: Vec<(u32, [f32; 3])> = self
            .monsters
            .iter()
            .filter(|m| m.view.alive)
            .map(|m| (m.view.actor_id, m.view.position))
            .collect();
        let scenario = Arc::clone(&self.scenario);
        let project = |point: [f32; 3]| {
            scenario
                .navigation
                .as_ref()
                .map_or(Some(point), |nav| nav.project(point))
        };
        for monster in self.monsters.iter_mut().filter(|m| m.view.alive) {
            let id = monster.view.actor_id;
            if let MonsterLocomotion::Spawning { until } = monster.locomotion {
                // Damage does not transfer locomotion ownership away from the birth clip.
                // Death is handled before this loop and removes the birth state separately.
                if now >= until {
                    monster.view.birth = None;
                    monster.locomotion = MonsterLocomotion::Hunting;
                    changed = true;
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
                        .expect("stationary birth pose");
                    continue;
                }
            }
            if let MonsterLocomotion::Entering {
                landing,
                path,
                elapsed_ms,
                updated_at,
            } = &mut monster.locomotion
            {
                // Entry owns the transform until landing. A stall advances at most
                // one AI step, as with hunting, rather than jumping to the endpoint.
                *elapsed_ms =
                    elapsed_ms.saturating_add(now.saturating_sub(*updated_at).min(AI_INTERVAL_MS));
                *updated_at = now;
                monster.view.position = path.position(*landing, *elapsed_ms);
                let landed = *elapsed_ms >= path.duration_ms;
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
                    .expect("validated entry path");
                if landed {
                    monster.locomotion = MonsterLocomotion::Hunting;
                }
                // Pursuit starts on the following step, preserving the exact landing pose.
                continue;
            }
            let protected = self
                .goal
                .as_ref()
                .and_then(GoalRuntime::protected_position)
                .map(|position| (FIRST_MONSTER_ID - 1, position));
            monster.ignored.retain(|(_, until)| *until > now);
            // Reachability is judged on the walkable point nearest the target, so a player who is
            // jumping or pressed against a wall is still chased. Targets given up on are skipped
            // unless nothing else is left, so a monster never just stands still.
            let mut candidates: Vec<(u32, [f32; 3], [f32; 3], bool)> = players
                .iter()
                .filter(|id| combat.alive(**id))
                .filter_map(|id| motion.latest(*id, now).map(|pose| (*id, pose.position)))
                .chain(protected)
                .filter_map(|(id, position)| {
                    let goal = match &self.scenario.navigation {
                        Some(nav) => nav
                            .anchor(position)
                            .filter(|goal| nav.connected(monster.view.position, *goal))?,
                        None => position,
                    };
                    let ignored = monster.ignored.iter().any(|(other, _)| *other == id);
                    Some((id, position, goal, ignored))
                })
                .collect();
            if candidates.iter().any(|candidate| !candidate.3) {
                candidates.retain(|candidate| !candidate.3);
            }
            let distances: Vec<(u32, f32)> = candidates
                .iter()
                .map(|candidate| (candidate.0, distance(monster.view.position, candidate.1)))
                .collect();
            let target = pve_ai::pick_target(monster.route_target, &distances)
                .and_then(|id| candidates.iter().find(|candidate| candidate.0 == id))
                .map(|candidate| (candidate.0, candidate.1, candidate.2));
            // Safety net: an action state that never ends must not freeze the monster for good.
            if battle.current(id).is_some_and(|sample| {
                sample.state != 2 && now.saturating_sub(sample.at) > pve_ai::ACTION_WATCHDOG_MS
            }) {
                battle.pause_input(id, now);
            }
            let state = battle.current(id);
            let idle = state.is_none_or(|s| s.state == 2);
            if let Some((target_id, position, goal)) = target {
                if monster.route_target != Some(target_id) {
                    monster.route.clear();
                    monster.next_route_at = now;
                    monster.route_target = Some(target_id);
                }
                let length = distance(monster.view.position, position);
                let same_floor = (monster.view.position[2] - goal[2]).abs() <= 1.5;
                // The router supplies a waypoint only while the monster is closing in.
                let destination = if !pve_brain::chasing(idle, length, same_floor, ATTACK_RANGE) {
                    None
                } else if let Some(nav) = &self.scenario.navigation {
                    // Bounded per tick; monsters over budget keep their old route and retry next
                    // tick. Refresh is staggered per actor to avoid a herd.
                    if now >= monster.next_route_at && routes_left > 0 {
                        routes_left -= 1;
                        monster.route = nav.route(monster.view.position, goal).into();
                        monster.next_route_at = now.saturating_add(
                            ROUTE_REFRESH_MS + u64::from(monster.view.actor_id % 10) * AI_INTERVAL_MS / 2,
                        );
                    }
                    while monster
                        .route
                        .front()
                        .is_some_and(|point| distance(monster.view.position, *point) < 0.001)
                    {
                        monster.route.pop_front();
                    }
                    monster.route.front().copied()
                } else {
                    Some(goal)
                };
                let command = pve_brain::think(
                    &pve_brain::Senses {
                        id,
                        position: monster.view.position,
                        heading: monster.view.heading,
                        speed: monster.view.kind.speed,
                        dt,
                        idle,
                        target: position,
                        same_floor,
                        destination,
                        crowd: &crowd,
                        detour: (now < monster.detour_until).then_some(monster.detour_flip),
                        attack_range: ATTACK_RANGE,
                    },
                    &project,
                );
                monster.view.position = command.position;
                monster.view.heading = command.heading;
                if command.blocked {
                    monster.route.clear();
                }
                let axes = command.axes;
                let wants_to_move = idle && (length > ATTACK_RANGE + 0.15 || !same_floor);
                match monster.stuck.observe(now, monster.view.position, wants_to_move) {
                    pve_ai::Stuck::Moving => {}
                    pve_ai::Stuck::Detour => {
                        monster.route.clear();
                        monster.next_route_at = now;
                        monster.detour_until = now.saturating_add(pve_ai::DETOUR_MS);
                        monster.detour_flip = !monster.detour_flip;
                    }
                    pve_ai::Stuck::GiveUp => {
                        monster.route.clear();
                        monster.next_route_at = now;
                        monster.route_target = None;
                        monster.ignored.retain(|(other, _)| *other != target_id);
                        monster
                            .ignored
                            .push((target_id, now.saturating_add(pve_ai::UNREACHABLE_MS)));
                    }
                }
                monster.input_seq += 1;
                let pose = Pose {
                    position: monster.view.position,
                    heading: monster.view.heading,
                    forward_back: axes.0,
                    left_right: axes.1,
                    running: false,
                    motion: None,
                };
                // Generated motion stays within the same bounded transport contract.
                // A rejected generated pose must not panic the room task: drop the route and retry.
                if motion.input(id, monster.input_seq, pose, now).is_err() {
                    monster.route.clear();
                    continue;
                }
                if idle
                    && length <= ATTACK_RANGE
                    && (monster.view.position[2] - position[2]).abs() <= 1.5
                    && now >= monster.next_attack
                    && !self.target_attack_deadlines.contains_key(&target_id)
                {
                    monster.input_seq += 1;
                    let up = battle.input(id, monster.input_seq, ActionIntent::AttackUp, now);
                    monster.input_seq += 1;
                    let release =
                        battle.input(id, monster.input_seq, ActionIntent::AttackRelease, now);
                    if up.is_err() || release.is_err() {
                        continue;
                    }
                    monster.next_attack = now.saturating_add(ATTACK_COOLDOWN_MS);
                    monster.attack_target = Some(target_id);
                    self.target_attack_deadlines.insert(target_id, now.saturating_add(TARGET_ATTACK_SPACING_MS));
                }
                if let Some(action) = state.filter(|s| {
                    s.state == 238
                        && now.saturating_sub(s.at) >= ATTACK_CONTACT_START_MS
                        && now.saturating_sub(s.at) <= ATTACK_CONTACT_END_MS
                }) && monster.hit_action != Some(action.seq)
                {
                    monster.hit_action = Some(action.seq);
                    if monster.attack_target == Some(target_id)
                        && attack_reaches(monster.view.position, position, monster.view.heading)
                    {
                        if target_id == FIRST_MONSTER_ID - 1 {
                            if let Some(goal) = &mut self.goal {
                                goal.damage(MONSTER_DAMAGE);
                            }
                            changed = true;
                            continue;
                        }
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
                // Reacquiring a reachable player must rebuild immediately,
                // rather than resume a cached route to the previous target.
                monster.route_target = None;
                monster.route.clear();
                monster.next_route_at = now;
                monster.input_seq += 1;
                let stationary = Pose {
                    position: monster.view.position,
                    heading: monster.view.heading,
                    forward_back: 0,
                    left_right: 0,
                    running: false,
                    motion: None,
                };
                if motion.input(id, monster.input_seq, stationary, now).is_err() {
                    monster.route.clear();
                }
            }
        }
        // Player hits come from sampled blade contacts. A proximity fallback
        // would commit a body hit before the head/leg contact can arrive.
        if self.goal.as_ref().is_some_and(GoalRuntime::failed) {
            self.phase = Phase::Defeat;
            changed = true;
        }
        if self
            .goal_dirty_since
            .is_some_and(|since| now.saturating_sub(since) >= GOAL_PUBLISH_INTERVAL_MS)
        {
            changed = true;
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
            } else if self.goal.as_ref().is_some_and(GoalRuntime::failed) {
                "pve-objective-destroyed"
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


fn attack_reaches(source: [f32; 3], target: [f32; 3], heading: i16) -> bool {
    let delta = [target[0] - source[0], target[1] - source[1]];
    let horizontal = delta[0].hypot(delta[1]);
    let yaw = f32::from(heading).to_radians();
    distance(source, target) <= ATTACK_RANGE
        && (source[2] - target[2]).abs() <= 1.0
        && (horizontal < 0.01 || (delta[0] * yaw.cos() - delta[1] * yaw.sin()) / horizontal >= 0.5)
}

#[cfg(test)]
mod attack_tests {
    use super::*;
    #[test]
    fn fallback_melee_cannot_hit_distant_side_back_or_other_floor_targets() {
        assert!(attack_reaches([0.; 3], [1., 0., 0.], 0));
        for target in [[1.2, 0., 0.], [-0.5, 0., 0.], [0., 0.5, 0.], [0., 0., 1.1]] {
            assert!(!attack_reaches([0.; 3], target, 0));
        }
    }
}
