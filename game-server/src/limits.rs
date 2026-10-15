/// Original client slots: owner 1, opponents 2..=11, teammates 12..=20.
pub const TEAM_CAPACITY: usize = 10;
pub const PVE_CAPACITY: usize = 4;
pub const ROOM_CAPACITY: usize = TEAM_CAPACITY * 2;
pub const MAX_CONNECTIONS: usize = 128;
pub const TICKET_TTL_SECS: u64 = 120;
pub const MAX_AUTH_RECORDS: usize = 4096;
pub const COMMAND_QUEUE_SIZE: usize = 1024;
pub const OUTPUT_QUEUE_SIZE: usize = 128;
pub const ROOM_COMMAND_QUEUE_SIZE: usize = 256;
pub const ROOM_TICK_MS: u64 = 25;
/// Frontal matching covers +/-60 degrees; lateral matching ends at +/-120.
pub const GUARD_FACING_COS: f32 = 0.5;
pub const GUARD_SIDE_COS: f32 = -0.5;
/// Supported client state 35 finishes after 550 ms.
pub const HURT_RECOVERY_MS: u64 = 550;
pub const LOGIN_WINDOW_SECS: u64 = 60;
pub const LOGIN_ATTEMPTS_PER_SOURCE: usize = 30;

#[derive(Clone, Copy, Debug)]
pub struct RoomRules {
    capacity: usize,
    duration_ms: u64,
    kill_target: u32,
}
impl Default for RoomRules {
    fn default() -> Self {
        Self {
            capacity: ROOM_CAPACITY,
            duration_ms: crate::round::DEFAULT_DURATION_MS,
            kill_target: crate::round::DEFAULT_KILL_TARGET,
        }
    }
}
impl RoomRules {
    pub fn new(capacity: usize) -> Result<Self, &'static str> {
        if !(1..=ROOM_CAPACITY).contains(&capacity) {
            return Err("room capacity must be between 1 and 20 client slots");
        }
        Ok(Self {
            capacity,
            ..Self::default()
        })
    }
    pub fn with_round_limits(
        mut self,
        duration_ms: u64,
        kill_target: u32,
    ) -> Result<Self, &'static str> {
        if !(1..=86_400_000).contains(&duration_ms) || !(1..=i32::MAX as u32).contains(&kill_target)
        {
            return Err("invalid round limits");
        }
        self.duration_ms = duration_ms;
        self.kill_target = kill_target;
        Ok(self)
    }
    pub fn duration_ms(self) -> u64 {
        self.duration_ms
    }
    pub fn kill_target(self) -> u32 {
        self.kill_target
    }
    pub fn capacity(self) -> usize {
        self.capacity
    }
    pub fn team_capacity(self) -> usize {
        self.capacity.div_ceil(2)
    }
}

pub const MAX_SWING_HISTORY: usize = ROOM_CAPACITY * 8;
pub const ENEMY_FIRST_RID: u32 = 2;
pub const FRIENDLY_FIRST_RID: u32 = ENEMY_FIRST_RID + TEAM_CAPACITY as u32;

/// A transport outage reserves its logical session without extending the match.
pub const RECONNECT_GRACE_MS: u64 = 60_000;
