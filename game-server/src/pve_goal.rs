//! Deterministic encounter goals. Progress is owned by the room, never submitted by clients.
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug, Default, Deserialize, Serialize, PartialEq)]
#[serde(
    tag = "type",
    rename_all = "camelCase",
    rename_all_fields = "camelCase",
    deny_unknown_fields
)]
pub enum Goal {
    #[default]
    Clear,
    Reach {
        position: [f32; 3],
        radius: f32,
    },
    Collect {
        points: Vec<[f32; 3]>,
        radius: f32,
    },
    Escort {
        path: Vec<[f32; 3]>,
        radius: f32,
        speed: f32,
    },
    Defend {
        position: [f32; 3],
        radius: f32,
        duration_ms: u64,
        health: u16,
    },
}
impl Goal {
    pub(crate) fn validate(&self) -> Result<(), &'static str> {
        let point = |p: &[f32; 3]| p.iter().all(|v| v.is_finite() && v.abs() < 100_000.);
        let radius = |r: f32| r.is_finite() && (0.5..=20.).contains(&r);
        let valid = match self {
            Self::Clear => true,
            Self::Reach {
                position,
                radius: r,
            } => point(position) && radius(*r),
            Self::Collect { points, radius: r } => {
                !points.is_empty()
                    && points.len() <= 64
                    && points.iter().all(point)
                    && radius(*r)
                    && points
                        .iter()
                        .enumerate()
                        .all(|(i, p)| !points[..i].contains(p))
            }
            Self::Escort {
                path,
                radius: r,
                speed,
            } => {
                (2..=128).contains(&path.len())
                    && path.iter().all(point)
                    && radius(*r)
                    && speed.is_finite()
                    && (0.1..=5.).contains(speed)
                    && path.windows(2).all(|p| distance(p[0], p[1]) >= 0.01)
            }
            Self::Defend {
                position,
                radius: r,
                duration_ms,
                health,
            } => {
                point(position)
                    && radius(*r)
                    && (100..=600_000).contains(duration_ms)
                    && *health > 0
            }
        };
        if valid {
            Ok(())
        } else {
            Err("invalid PVE encounter goal")
        }
    }
    pub(crate) fn is_clear(&self) -> bool {
        matches!(self, Self::Clear)
    }
    pub(crate) fn points(&self) -> &[[f32; 3]] {
        match self {
            Self::Clear => &[],
            Self::Reach { position, .. } | Self::Defend { position, .. } => {
                std::slice::from_ref(position)
            }
            Self::Collect { points, .. } => points,
            Self::Escort { path, .. } => path,
        }
    }
}

#[derive(Clone, Debug, Serialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct Progress {
    pub kind: &'static str,
    pub current: u64,
    pub total: u64,
    pub target_position: [f32; 3],
    pub complete: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub health: Option<u16>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub maximum_health: Option<u16>,
}

#[derive(Clone, Debug)]
pub(crate) struct Runtime {
    goal: Goal,
    collected: Vec<bool>,
    cursor: usize,
    position: [f32; 3],
    elapsed_ms: u64,
    health: Option<u16>,
    complete: bool,
}
impl Runtime {
    pub(crate) fn new(goal: Goal) -> Self {
        let collected = match &goal {
            Goal::Collect { points, .. } => vec![false; points.len()],
            _ => vec![],
        };
        let position = match &goal {
            Goal::Reach { position, .. } | Goal::Defend { position, .. } => *position,
            Goal::Collect { points, .. } => points[0],
            Goal::Escort { path, .. } => path[0],
            Goal::Clear => [0.; 3],
        };
        let health = match &goal {
            Goal::Defend { health, .. } => Some(*health),
            _ => None,
        };
        Self {
            goal,
            collected,
            cursor: 1,
            position,
            elapsed_ms: 0,
            health,
            complete: false,
        }
    }
    /// Only fresh living player poses are passed here. One stalled tick advances at most one AI step.
    pub(crate) fn advance(&mut self, players: &[[f32; 3]], step_ms: u64) {
        if self.complete || self.failed() {
            return;
        }
        let near = |p, radius| players.iter().any(|v| distance(*v, p) <= radius);
        match &self.goal {
            Goal::Clear => self.complete = true,
            Goal::Reach { position, radius } => self.complete = near(*position, *radius),
            Goal::Collect { points, radius } => {
                for (i, p) in points.iter().enumerate() {
                    if near(*p, *radius) {
                        self.collected[i] = true;
                    }
                }
                self.complete = self.collected.iter().all(|v| *v);
                if let Some(i) = self.collected.iter().position(|v| !v) {
                    self.position = points[i];
                }
            }
            Goal::Escort {
                path,
                radius,
                speed,
            } => {
                if near(self.position, *radius) {
                    let mut travel = speed * step_ms.min(100) as f32 / 1000.;
                    while self.cursor < path.len() {
                        let target = path[self.cursor];
                        let length = distance(self.position, target);
                        if length > travel {
                            self.position = std::array::from_fn(|i| {
                                self.position[i] + (target[i] - self.position[i]) * travel / length
                            });
                            break;
                        }
                        self.position = target;
                        self.cursor += 1;
                        travel -= length;
                    }
                    self.complete = self.cursor == path.len();
                }
            }
            Goal::Defend {
                position,
                radius,
                duration_ms,
                ..
            } => {
                if near(*position, *radius) {
                    self.elapsed_ms = self
                        .elapsed_ms
                        .saturating_add(step_ms.min(100))
                        .min(*duration_ms);
                }
                self.complete = self.elapsed_ms == *duration_ms;
            }
        }
    }
    pub(crate) fn damage(&mut self, amount: u16) {
        if let Some(health) = &mut self.health {
            *health = health.saturating_sub(amount);
        }
    }
    pub(crate) fn protected_position(&self) -> Option<[f32; 3]> {
        matches!(self.goal, Goal::Defend { .. }).then_some(self.position)
    }
    pub(crate) fn complete(&self) -> bool {
        self.complete
    }
    pub(crate) fn position(&self) -> [f32; 3] {
        self.position
    }
    pub(crate) fn collected(&self, index: usize) -> bool {
        self.collected.get(index).copied().unwrap_or(false)
    }
    pub(crate) fn health(&self) -> Option<u16> {
        self.health
    }
    pub(crate) fn failed(&self) -> bool {
        self.health == Some(0)
    }
    pub(crate) fn progress(&self) -> Option<Progress> {
        let (kind, current, total, max) = match &self.goal {
            Goal::Clear => return None,
            Goal::Reach { .. } => ("reach", u64::from(self.complete), 1, None),
            Goal::Collect { points, .. } => (
                "collect",
                self.collected.iter().filter(|v| **v).count() as u64,
                points.len() as u64,
                None,
            ),
            Goal::Escort { path, .. } => (
                "escort",
                (self.cursor - 1) as u64,
                (path.len() - 1) as u64,
                None,
            ),
            Goal::Defend {
                duration_ms,
                health,
                ..
            } => ("defend", self.elapsed_ms, *duration_ms, Some(*health)),
        };
        Some(Progress {
            kind,
            current,
            total,
            target_position: self.position,
            complete: self.complete,
            health: self.health,
            maximum_health: max,
        })
    }
}
fn distance(a: [f32; 3], b: [f32; 3]) -> f32 {
    a.iter()
        .zip(b)
        .map(|(x, y)| (x - y).powi(2))
        .sum::<f32>()
        .sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn collect_is_shared_idempotent_and_checks_height() {
        let mut r = Runtime::new(Goal::Collect {
            points: vec![[0.; 3], [3., 0., 0.]],
            radius: 1.,
        });
        r.advance(&[[0., 0., 4.]], 100);
        assert_eq!(r.progress().unwrap().current, 0);
        r.advance(&[[0.; 3], [0.; 3]], 100);
        r.advance(&[[0.; 3]], 100);
        assert_eq!(r.progress().unwrap().current, 1);
        assert!(!r.complete());
        r.advance(&[[3., 0., 0.]], 100);
        assert!(r.complete());
    }
    #[test]
    fn escort_pauses_without_living_players_and_does_not_catch_up() {
        let mut r = Runtime::new(Goal::Escort {
            path: vec![[0.; 3], [1., 0., 0.]],
            radius: 2.,
            speed: 1.,
        });
        r.advance(&[], 1000);
        assert_eq!(r.position, [0.; 3]);
        r.advance(&[[0.; 3]], 10_000);
        assert_eq!(r.position, [0.1, 0., 0.]);
        for _ in 0..10 {
            r.advance(&[[0.; 3]], 100);
        }
        assert!(r.complete());
    }
    #[test]
    fn defend_can_fail_and_clock_requires_presence() {
        let mut r = Runtime::new(Goal::Defend {
            position: [0.; 3],
            radius: 2.,
            duration_ms: 200,
            health: 10,
        });
        r.advance(&[], 100);
        assert_eq!(r.progress().unwrap().current, 0);
        r.advance(&[[0.; 3]], 100);
        r.damage(10);
        r.advance(&[[0.; 3]], 100);
        assert!(r.failed());
        assert!(!r.complete());
    }
    #[test]
    fn invalid_goals_are_rejected() {
        assert!(
            Goal::Collect {
                points: vec![[0.; 3], [0.; 3]],
                radius: 1.
            }
            .validate()
            .is_err()
        );
        assert!(
            Goal::Escort {
                path: vec![[0.; 3]],
                radius: 1.,
                speed: 1.
            }
            .validate()
            .is_err()
        );
        assert!(
            Goal::Reach {
                position: [f32::NAN, 0., 0.],
                radius: 1.
            }
            .validate()
            .is_err()
        );
    }
}
