//! Monster controllers. A brain turns what a monster senses into a `Command`: where it stands,
//! which way it faces and which movement axes it presses, the same vocabulary a player's client
//! reports. The room executes every command through one path (motion validation, then the
//! action state machine), so a new kind of monster is a new brain, not a new pipeline.
//!
//! Pure functions over plain values, so behaviour is testable without the room runtime.
use crate::pve_steering as steering;

pub(crate) struct Senses<'a> {
    pub id: u32,
    pub position: [f32; 3],
    pub heading: i16,
    /// Walking speed in metres per second.
    pub speed: f32,
    /// Seconds covered by this step.
    pub dt: f32,
    /// False while an action (attack, hurt reaction) owns the body.
    pub idle: bool,
    /// The target's real position, used for facing and range.
    pub target: [f32; 3],
    /// The walkable surface the target stands on is within reach of this monster's floor.
    pub same_floor: bool,
    /// Next route waypoint toward the target, when the router has one.
    pub destination: Option<[f32; 3]>,
    /// Everyone sharing the arena, this monster included (filtered by `id`).
    pub crowd: &'a [(u32, [f32; 3])],
    /// Side to step around an obstacle, while a detour is active.
    pub detour: Option<bool>,
    pub attack_range: f32,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub(crate) struct Command {
    pub position: [f32; 3],
    pub heading: i16,
    /// `(forward_back, left_right)` as a player's gait would report them.
    pub axes: (i8, i8),
    /// The wanted step leaves the walkable mesh in every direction tried: drop the route.
    pub blocked: bool,
}

/// Whether the monster is closing the distance, so the router should supply a waypoint.
pub(crate) fn chasing(idle: bool, length: f32, same_floor: bool, range: f32) -> bool {
    idle && (length > range || !same_floor)
}

fn heading_to(from: [f32; 3], to: [f32; 3]) -> i16 {
    (-(to[1] - from[1]))
        .atan2(to[0] - from[0])
        .to_degrees()
        .round() as i16
}

/// The zombie: face the target, walk the route while keeping spacing from the crowd, and once in
/// range slide around the target to make room. Deliberately simple; it never flanks or retreats.
pub(crate) fn think(senses: &Senses, project: &dyn Fn([f32; 3]) -> Option<[f32; 3]>) -> Command {
    let mut command = Command {
        position: senses.position,
        heading: senses.heading,
        axes: (0, 0),
        blocked: false,
    };
    let length =
        (senses.target[0] - senses.position[0]).hypot(senses.target[1] - senses.position[1]);
    if senses.idle && length > 0.01 {
        command.heading = heading_to(senses.position, senses.target);
    }
    if chasing(senses.idle, length, senses.same_floor, senses.attack_range) {
        if let Some(destination) = senses.destination {
            walk(senses, length, destination, project, &mut command);
        }
    } else if senses.idle && senses.same_floor {
        slide(senses, project, &mut command);
    }
    command
}

fn crowd_push(senses: &Senses) -> [f32; 2] {
    steering::separation(
        senses.position,
        senses
            .crowd
            .iter()
            .filter(|(other, _)| *other != senses.id)
            .map(|(_, p)| *p),
    )
}

fn walk(
    senses: &Senses,
    length: f32,
    destination: [f32; 3],
    project: &dyn Fn([f32; 3]) -> Option<[f32; 3]>,
    command: &mut Command,
) {
    let delta = [
        destination[0] - senses.position[0],
        destination[1] - senses.position[1],
    ];
    let travel = delta[0].hypot(delta[1]);
    let seek = [delta[0] / travel.max(1e-4), delta[1] / travel.max(1e-4)];
    // A detour sidesteps whatever blocked the straight way for a moment.
    let seek = senses
        .detour
        .map_or(seek, |flip| crate::pve_ai::detour(seek, flip));
    // Crowd spacing on the target's floor only; stairs and ramps follow the route.
    let push = if senses.same_floor {
        crowd_push(senses)
    } else {
        [0., 0.]
    };
    let step = (senses.speed * senses.dt)
        .min(travel)
        .min(if senses.same_floor {
            length - senses.attack_range
        } else {
            f32::INFINITY
        });
    // Micro steps would toggle the gait walk/stop every tick; the one step that closes into attack
    // range is always taken, or the monster could never attack.
    let closes = senses.same_floor && step > 0. && step >= length - senses.attack_range - 1e-4;
    if travel <= 0.001 || (step < steering::MIN_STEP && !closes) {
        return;
    }
    // Prefer the spaced direction; if the push leaves the walkable mesh (walls, edges) fall back to
    // the plain route step before giving up on the route.
    let grounded = [steering::blend(seek, push), seek]
        .into_iter()
        .filter(|direction| *direction != [0., 0.])
        .find_map(|direction| {
            let candidate = [
                senses.position[0] + direction[0] * step,
                senses.position[1] + direction[1] * step,
                senses.position[2],
            ];
            project(candidate).map(|point| (direction, point))
        });
    match grounded {
        Some((direction, point)) => {
            command.heading = (-direction[1]).atan2(direction[0]).to_degrees().round() as i16;
            command.position = point;
            command.axes = (-1, 0);
        }
        None => command.blocked = true,
    }
}

/// In attack range: slide around the target (never toward or away from it) to make room for the
/// crowd, facing the target and strafing as a player would.
fn slide(senses: &Senses, project: &dyn Fn([f32; 3]) -> Option<[f32; 3]>, command: &mut Command) {
    let push = crowd_push(senses);
    let slide = steering::tangential(senses.position, senses.target, push);
    let amount = slide[0].hypot(slide[1]);
    let step = (senses.speed * senses.dt * steering::RING_SLIDE_FRACTION).min(amount);
    if amount <= 0.05 || step < steering::MIN_STEP {
        return;
    }
    let candidate = [
        senses.position[0] + slide[0] / amount * step,
        senses.position[1] + slide[1] / amount * step,
        senses.position[2],
    ];
    if let Some(point) = project(candidate) {
        command.position = point;
        let travel = (-slide[1]).atan2(slide[0]).to_degrees().round() as i16;
        command.axes = steering::axes(command.heading, travel);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RANGE: f32 = 1.1;
    fn flat(point: [f32; 3]) -> Option<[f32; 3]> {
        Some(point)
    }
    fn senses<'a>(crowd: &'a [(u32, [f32; 3])], target: [f32; 3]) -> Senses<'a> {
        Senses {
            id: 1,
            position: [3., 0., 0.],
            heading: 0,
            speed: 2.,
            dt: 0.1,
            idle: true,
            target,
            same_floor: true,
            destination: Some(target),
            crowd,
            detour: None,
            attack_range: RANGE,
        }
    }

    #[test]
    fn walks_toward_the_target_with_a_forward_gait() {
        let crowd = [(1, [3., 0., 0.])];
        let command = think(&senses(&crowd, [0., 0., 0.]), &flat);
        assert_eq!(command.axes, (-1, 0));
        assert!((command.position[0] - 2.8).abs() < 1e-5, "{command:?}");
        assert_eq!(command.heading.abs(), 180);
        assert!(!command.blocked);
    }

    #[test]
    fn the_closing_step_stops_exactly_at_attack_range_then_it_stands() {
        let crowd = [(1, [1.15, 0., 0.])];
        let mut s = senses(&crowd, [0., 0., 0.]);
        s.position = [1.15, 0., 0.];
        let command = think(&s, &flat);
        assert!((command.position[0] - RANGE).abs() < 1e-5, "{command:?}");
        // Inside range with nobody crowding: faces the target and holds still.
        s.position = [RANGE, 0., 0.];
        let command = think(&s, &flat);
        assert_eq!(command.position, [RANGE, 0., 0.]);
        assert_eq!(command.axes, (0, 0));
        assert_eq!(command.heading.abs(), 180);
    }

    #[test]
    fn a_busy_body_neither_turns_nor_moves() {
        let crowd = [(1, [3., 0., 0.])];
        let mut s = senses(&crowd, [0., 0., 0.]);
        s.idle = false;
        s.heading = 90;
        let command = think(&s, &flat);
        assert_eq!(
            (command.position, command.heading, command.axes),
            ([3., 0., 0.], 90, (0, 0))
        );
    }

    #[test]
    fn a_blocked_step_asks_for_a_new_route() {
        let crowd = [(1, [3., 0., 0.])];
        let command = think(&senses(&crowd, [0., 0., 0.]), &|_| None);
        assert!(command.blocked);
        assert_eq!(command.position, [3., 0., 0.]);
    }

    #[test]
    fn a_crowd_member_in_range_makes_the_monster_slide_around_the_target() {
        let mut s = senses(&[], [0., 0., 0.]);
        s.position = [1., 0., 0.];
        let crowd = [(1, [1., 0., 0.]), (2, [1., 0.2, 0.])];
        s.crowd = &crowd;
        let command = think(&s, &flat);
        // Tangential to the target: moves in y only, strafing.
        assert!((command.position[0] - 1.).abs() < 1e-5);
        assert!(command.position[1] < 0.);
        assert_ne!(command.axes, (0, 0));
        assert_ne!(command.axes, (-1, 0));
    }

    #[test]
    fn a_detour_turns_the_walk_sideways() {
        let crowd = [(1, [3., 0., 0.])];
        let mut s = senses(&crowd, [0., 0., 0.]);
        s.detour = Some(false);
        let command = think(&s, &flat);
        assert!(command.position[1].abs() > 0.1, "{command:?}");
    }

    #[test]
    fn the_router_is_only_needed_while_closing_in() {
        assert!(chasing(true, 5., true, RANGE));
        assert!(chasing(true, 0.5, false, RANGE));
        assert!(!chasing(true, 0.5, true, RANGE));
        assert!(!chasing(false, 5., true, RANGE));
    }
}
