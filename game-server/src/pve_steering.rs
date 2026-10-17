//! Crowd steering for server-owned monsters: spread spawns, keep spacing, surround the target.
//!
//! Pure functions over horizontal positions so the behaviour is testable without the room runtime.

/// Monsters closer than this (horizontally, same floor) push each other apart.
pub(crate) const SEPARATION_RADIUS: f32 = 1.0;
/// Weight of the separation push relative to the unit seek direction. A monster queued behind
/// another settles where (R - d) / R * W = 1, i.e. d = R (1 - 1 / W) = 0.67 u apart.
pub(crate) const SEPARATION_WEIGHT: f32 = 3.0;
/// Steps shorter than this are not taken, so the gait does not flicker walk/stop at the range edge.
pub(crate) const MIN_STEP: f32 = 0.02;
/// Fraction of normal speed used to slide around the target ring while in attack range.
pub(crate) const RING_SLIDE_FRACTION: f32 = 0.35;
const SAME_FLOOR: f32 = 1.5;
const GOLDEN_ANGLE: f32 = 2.399_963_2;
const SPAWN_SPACING: f32 = 0.8;

/// Sum of repulsion from nearby crowd members; each contributes (R - d) / R along the away vector.
pub(crate) fn separation(
    position: [f32; 3],
    others: impl IntoIterator<Item = [f32; 3]>,
) -> [f32; 2] {
    let mut push = [0.0f32; 2];
    for other in others {
        if (other[2] - position[2]).abs() > SAME_FLOOR {
            continue;
        }
        let away = [position[0] - other[0], position[1] - other[1]];
        let d = away[0].hypot(away[1]);
        if d >= SEPARATION_RADIUS {
            continue;
        }
        // Coincident members get a deterministic direction from their offset sign instead of NaN.
        let unit = if d > 1e-4 {
            [away[0] / d, away[1] / d]
        } else {
            [1.0, 0.0]
        };
        let weight = (SEPARATION_RADIUS - d) / SEPARATION_RADIUS;
        push[0] += unit[0] * weight;
        push[1] += unit[1] * weight;
    }
    push
}

/// Unit travel direction: seek toward `toward` blended with the crowd push. Zero when there is none.
pub(crate) fn blend(seek: [f32; 2], push: [f32; 2]) -> [f32; 2] {
    let v = [
        seek[0] + push[0] * SEPARATION_WEIGHT,
        seek[1] + push[1] * SEPARATION_WEIGHT,
    ];
    let n = v[0].hypot(v[1]);
    if n < 1e-4 {
        [0.0, 0.0]
    } else {
        [v[0] / n, v[1] / n]
    }
}

/// The part of `push` tangential to the circle around `target`, so a monster in attack range slides
/// around its target to make room instead of being pushed out of range (which would oscillate).
pub(crate) fn tangential(position: [f32; 3], target: [f32; 3], push: [f32; 2]) -> [f32; 2] {
    let radial = [target[0] - position[0], target[1] - position[1]];
    let d = radial[0].hypot(radial[1]);
    if d < 1e-4 {
        return push;
    }
    let u = [radial[0] / d, radial[1] / d];
    let along = push[0] * u[0] + push[1] * u[1];
    [push[0] - along * u[0], push[1] - along * u[1]]
}

/// Spawn offset for the `round`-th reuse of the same spawn point (round 0 = the point itself).
/// Successive reuses spiral outward on a golden-angle sunflower so no two monsters share a point.
pub(crate) fn spawn_offset(round: usize) -> [f32; 2] {
    if round == 0 {
        return [0.0, 0.0];
    }
    let r = SPAWN_SPACING * (round as f32).sqrt();
    let a = GOLDEN_ANGLE * round as f32;
    [r * a.cos(), r * a.sin()]
}

/// Movement axes (forward_back, left_right) for travelling toward `travel` while facing `facing`
/// (both degrees in the pose convention), matching the client's eight-way gait table and the bots.
pub(crate) fn axes(facing: i16, travel: i16) -> (i8, i8) {
    const TABLE: [(i8, i8); 8] = [
        (-1, 0),
        (-1, -1),
        (0, -1),
        (1, -1),
        (1, 0),
        (1, 1),
        (0, 1),
        (-1, 1),
    ];
    let delta = (i32::from(travel) - i32::from(facing) + 180).rem_euclid(360) - 180;
    let index = ((delta as f32 + 22.5) / 45.).floor() as i32;
    TABLE[index.rem_euclid(8) as usize]
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hyp(v: [f32; 2]) -> f32 {
        v[0].hypot(v[1])
    }

    #[test]
    fn separation_pushes_apart_and_ignores_far_or_other_floor_members() {
        let p = [0.0, 0.0, 0.0];
        let push = separation(p, [[0.3, 0.0, 0.0]]);
        assert!(push[0] < 0.0 && push[1].abs() < 1e-6);
        assert_eq!(separation(p, [[2.0, 0.0, 0.0]]), [0.0, 0.0]);
        assert_eq!(separation(p, [[0.1, 0.0, 3.0]]), [0.0, 0.0]);
        let coincident = separation(p, [[0.0, 0.0, 0.0]]);
        assert!(coincident[0].is_finite() && hyp(coincident) > 0.9);
    }

    #[test]
    fn axes_match_the_bot_gait_convention() {
        assert_eq!(axes(30, 30), (-1, 0));
        assert_eq!(axes(30, 120), (0, -1));
        assert_eq!(axes(30, -60), (0, 1));
        assert_eq!(axes(170, -10), (1, 0));
        assert_eq!(axes(-170, 175), (-1, 0));
    }

    #[test]
    fn blend_is_unit_or_zero() {
        assert!((hyp(blend([1.0, 0.0], [0.0, 1.0])) - 1.0).abs() < 1e-5);
        assert_eq!(blend([0.0, 0.0], [0.0, 0.0]), [0.0, 0.0]);
    }

    #[test]
    fn tangential_slide_never_moves_toward_or_away_from_the_target() {
        let t = tangential([1.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.7, 0.4]);
        assert!(t[0].abs() < 1e-6 && (t[1] - 0.4).abs() < 1e-6);
    }

    #[test]
    fn spawn_offsets_are_distinct_and_spaced() {
        let points: Vec<[f32; 2]> = (0..32).map(spawn_offset).collect();
        for (i, a) in points.iter().enumerate() {
            for b in &points[i + 1..] {
                assert!(hyp([a[0] - b[0], a[1] - b[1]]) > 0.35, "{a:?} {b:?}");
            }
        }
    }

    /// Eight monsters walking at one stationary target end up spread around it, none overlapping.
    #[test]
    fn a_crowd_surrounds_the_target_instead_of_stacking() {
        let target = [0.0f32, 0.0, 0.0];
        let range = 1.1f32;
        let speed = 2.0f32;
        let dt = 0.1f32;
        let mut crowd: Vec<[f32; 3]> = (0..8)
            .map(|i| [8.0 + 0.05 * i as f32, 0.01 * i as f32, 0.0])
            .collect();
        for _ in 0..200 {
            let snapshot = crowd.clone();
            for (i, p) in crowd.iter_mut().enumerate() {
                let others = snapshot
                    .iter()
                    .enumerate()
                    .filter(|(j, _)| *j != i)
                    .map(|(_, o)| *o);
                let push = separation(*p, others);
                let to = [target[0] - p[0], target[1] - p[1]];
                let length = to[0].hypot(to[1]);
                if length > range {
                    let dir = blend([to[0] / length, to[1] / length], push);
                    let step = (speed * dt).min(length - range);
                    if step >= MIN_STEP {
                        p[0] += dir[0] * step;
                        p[1] += dir[1] * step;
                    }
                } else {
                    let slide = tangential(*p, target, push);
                    let n = hyp(slide);
                    let step = (speed * dt * RING_SLIDE_FRACTION).min(n);
                    if n > 0.05 && step >= MIN_STEP {
                        p[0] += slide[0] / n * step;
                        p[1] += slide[1] / n * step;
                    }
                }
            }
        }
        let mut min_gap = f32::INFINITY;
        for (i, a) in crowd.iter().enumerate() {
            for b in &crowd[i + 1..] {
                min_gap = min_gap.min((a[0] - b[0]).hypot(a[1] - b[1]));
            }
        }
        let max_range = crowd
            .iter()
            .map(|p| p[0].hypot(p[1]))
            .fold(0.0f32, f32::max);
        // Without steering every monster ends at (1.1, ~0): gap ~0.
        assert!(min_gap > 0.45, "monsters still overlap: min gap {min_gap}");
        // Spreading may hold the back ranks outside melee range, but never far out.
        assert!(max_range < 3.5, "crowd drifted away: {max_range}");
    }
}
