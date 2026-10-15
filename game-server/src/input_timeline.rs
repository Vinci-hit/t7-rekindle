//! Contiguous combat progress and cross-lane barriers on validated server time.
use serde::Deserialize;

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Stamp {
    pub epoch: String,
    pub combat_seq: u64,
    pub sampled_ms: u64,
    pub gateway_ms: u64,
    pub source: Source,
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase")]
pub enum Source {
    GatewayInput,
    NativeContact,
    NativePose,
    Heartbeat,
}

#[derive(Clone, Debug, Default)]
pub struct Stream {
    epoch: Option<String>,
    last_seq: u64,
    gateway_ms: Option<u64>,
    pub movement_barrier: u64,
    pub watermark: crate::adjudication::InputWatermark,
}

impl Stream {
    pub fn observe_mapped(&mut self, stamp: &Stamp, motion: bool, mapped_time: u64) -> bool {
        let mut candidate = self.clone();
        if !candidate.observe(stamp, motion) {
            return false;
        }
        let valid = if motion {
            candidate.watermark.observe_barrier(stamp.combat_seq);
            true
        } else if stamp.source == Source::NativeContact {
            candidate.watermark.observe_contact(stamp.combat_seq)
        } else {
            candidate
                .watermark
                .observe_combat(stamp.combat_seq, mapped_time)
        };
        if valid {
            *self = candidate;
        }
        valid
    }

    pub fn observe(&mut self, stamp: &Stamp, motion: bool) -> bool {
        if self.epoch.as_deref() != Some(&stamp.epoch) {
            *self = Self {
                epoch: Some(stamp.epoch.clone()),
                ..Self::default()
            };
        }
        if stamp.sampled_ms > stamp.gateway_ms {
            return false;
        }
        if motion {
            if stamp.source != Source::NativePose {
                return false;
            }
            self.movement_barrier = self.movement_barrier.max(stamp.combat_seq);
            return true;
        }
        if stamp.source == Source::NativePose
            || self.last_seq.checked_add(1) != Some(stamp.combat_seq)
            || self
                .gateway_ms
                .is_some_and(|previous| stamp.gateway_ms < previous)
        {
            return false;
        }
        // Native contact frames can precede a previously forwarded input. Their
        // sample time must not be mistaken for contiguous input-stream progress.
        self.last_seq = stamp.combat_seq;
        self.gateway_ms = Some(stamp.gateway_ms);
        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn stamp(seq: u64, sampled_ms: u64, gateway_ms: u64, source: Source) -> Stamp {
        Stamp {
            epoch: "epoch".into(),
            combat_seq: seq,
            sampled_ms,
            gateway_ms,
            source,
        }
    }

    #[test]
    fn mapped_prefix_contact_samples_and_movement_barriers_share_one_watermark() {
        use crate::adjudication::Readiness;
        let mut s = Stream::default();
        assert!(s.observe_mapped(&stamp(1, 100, 100, Source::GatewayInput), false, 100));
        assert!(s.observe_mapped(&stamp(3, 130, 130, Source::NativePose), true, 130));
        assert!(s.observe_mapped(&stamp(2, 90, 120, Source::NativeContact), false, 90));
        assert_eq!(s.watermark.readiness(100, 140, 70), Readiness::Waiting);
        assert!(!s.observe_mapped(&stamp(3, 99, 130, Source::GatewayInput), false, 99));
        assert_eq!(s.last_seq, 2); // Invalid observations are transactional.
        assert!(s.observe_mapped(&stamp(3, 130, 130, Source::Heartbeat), false, 130));
        assert_eq!(s.watermark.readiness(100, 140, 70), Readiness::Complete);
        let mut new_epoch = stamp(1, 140, 140, Source::Heartbeat);
        new_epoch.epoch = "new-epoch".into();
        assert!(s.observe_mapped(&new_epoch, false, 140));
        assert_eq!(s.last_seq, 1);
        assert_eq!(s.movement_barrier, 0);
    }

    #[test]
    fn retained_contact_frames_do_not_have_to_postdate_the_last_input() {
        let mut stream = Stream::default();
        assert!(stream.observe(&stamp(1, 100, 100, Source::GatewayInput), false));
        assert!(stream.observe(&stamp(2, 90, 110, Source::NativeContact), false));
        assert!(!stream.observe(&stamp(4, 120, 120, Source::Heartbeat), false));
        assert!(stream.observe(&stamp(3, 120, 120, Source::Heartbeat), false));
    }

    #[test]
    fn coalesced_motion_only_records_a_barrier() {
        let mut stream = Stream::default();
        assert!(stream.observe(&stamp(8, 100, 100, Source::NativePose), true));
        assert_eq!(stream.last_seq, 0);
        assert_eq!(stream.movement_barrier, 8);
        assert!(stream.observe(&stamp(1, 90, 90, Source::GatewayInput), false));
        assert!(!stream.observe(&stamp(2, 110, 100, Source::GatewayInput), false));
    }
}
