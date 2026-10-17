//! Target memory and stuck recovery for server-owned monsters.
//!
//! Pure functions and small state machines, so the behaviour is testable without the room runtime.

/// A new target replaces the current one only when it is this much closer (hysteresis against
/// flip-flopping between two equally distant players).
const RETARGET_RATIO: f32 = 0.75;
/// A monster that wants to move but gains less than this in `STUCK_WINDOW_MS` is stuck.
const STUCK_MIN_PROGRESS: f32 = 0.25;
const STUCK_WINDOW_MS: u64 = 1200;
/// Still stuck after this long: give up on the target for a while and pick another.
const GIVE_UP_MS: u64 = 4000;
pub(crate) const DETOUR_MS: u64 = 800;
pub(crate) const UNREACHABLE_MS: u64 = 3000;
/// A monster never stays in one non-idle action state longer than this.
pub(crate) const ACTION_WATCHDOG_MS: u64 = 3000;
const DETOUR_ANGLE_DEG: f32 = 70.;

/// Keep `current` while it is still a candidate and nothing is clearly closer.
/// Candidates are `(id, horizontal distance)`; ties resolve to the lowest id.
pub(crate) fn pick_target(current: Option<u32>, candidates: &[(u32, f32)]) -> Option<u32> {
    let best = candidates
        .iter()
        .min_by(|a, b| a.1.total_cmp(&b.1).then(a.0.cmp(&b.0)))?;
    let held = current.and_then(|id| candidates.iter().find(|c| c.0 == id));
    match held {
        Some(held) if best.1 >= held.1 * RETARGET_RATIO => Some(held.0),
        _ => Some(best.0),
    }
}

/// What the stuck watchdog asks the monster to do this step.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Stuck {
    Moving,
    /// Rebuild the route and step around whatever is in the way.
    Detour,
    /// The target cannot be reached from here; choose another for a while.
    GiveUp,
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Progress {
    anchor: [f32; 2],
    since: u64,
    last_detour: u64,
}

impl Progress {
    /// Call once per AI step after movement. `wants_to_move` is false while the monster is
    /// legitimately standing (in range, attacking, reacting), which resets the watch.
    pub(crate) fn observe(&mut self, now: u64, position: [f32; 3], wants_to_move: bool) -> Stuck {
        let moved = (position[0] - self.anchor[0]).hypot(position[1] - self.anchor[1]);
        if !wants_to_move || moved >= STUCK_MIN_PROGRESS || self.since == 0 {
            self.anchor = [position[0], position[1]];
            self.since = now.max(1);
            self.last_detour = 0;
            return Stuck::Moving;
        }
        let stuck_for = now.saturating_sub(self.since);
        if stuck_for >= GIVE_UP_MS {
            self.since = now;
            self.last_detour = now;
            return Stuck::GiveUp;
        }
        if stuck_for >= STUCK_WINDOW_MS && now.saturating_sub(self.last_detour) >= STUCK_WINDOW_MS {
            self.last_detour = now;
            return Stuck::Detour;
        }
        Stuck::Moving
    }
}

/// `seek` rotated sideways; `flip` alternates the side between successive detours.
pub(crate) fn detour(seek: [f32; 2], flip: bool) -> [f32; 2] {
    let angle = (if flip { -DETOUR_ANGLE_DEG } else { DETOUR_ANGLE_DEG }).to_radians();
    let (sin, cos) = angle.sin_cos();
    [seek[0] * cos - seek[1] * sin, seek[0] * sin + seek[1] * cos]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn target_is_sticky_until_another_is_clearly_closer() {
        assert_eq!(pick_target(None, &[(7, 5.), (3, 4.)]), Some(3));
        assert_eq!(pick_target(Some(7), &[(7, 5.), (3, 4.5)]), Some(7));
        assert_eq!(pick_target(Some(7), &[(7, 5.), (3, 3.)]), Some(3));
        assert_eq!(pick_target(Some(9), &[(7, 5.)]), Some(7), "a lost target is dropped");
        assert_eq!(pick_target(Some(7), &[]), None);
        assert_eq!(pick_target(None, &[(5, 2.), (4, 2.)]), Some(4), "ties go to the lowest id");
    }

    #[test]
    fn watchdog_escalates_from_detour_to_giving_up_and_resets_on_progress() {
        let mut p = Progress::default();
        let at = [0., 0., 0.];
        assert_eq!(p.observe(1000, at, true), Stuck::Moving);
        assert_eq!(p.observe(1500, at, true), Stuck::Moving);
        assert_eq!(p.observe(2300, at, true), Stuck::Detour);
        assert_eq!(p.observe(2400, at, true), Stuck::Moving, "one detour per window");
        assert_eq!(p.observe(3600, at, true), Stuck::Detour);
        assert_eq!(p.observe(5100, at, true), Stuck::GiveUp);
        // Real progress clears the watch.
        assert_eq!(p.observe(5200, [1., 0., 0.], true), Stuck::Moving);
        assert_eq!(p.observe(7000, [1., 0., 0.], true), Stuck::Detour);
        // Standing on purpose (attacking, reacting) never counts as stuck.
        for t in (8000..20000).step_by(100) {
            assert_eq!(p.observe(t, [1., 0., 0.], false), Stuck::Moving);
        }
    }

    #[test]
    fn detour_turns_sideways_and_alternates() {
        let left = detour([1., 0.], false);
        let right = detour([1., 0.], true);
        assert!(left[1] > 0.9 && right[1] < -0.9);
        assert!((left[0].hypot(left[1]) - 1.).abs() < 1e-5);
    }
}
