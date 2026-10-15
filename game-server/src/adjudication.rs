//! Event-time readiness rules. All times passed here must already be validated
//! and mapped to the server clock. This module does not trust raw client time.

pub const MAX_WAIT_MS: u64 = 70;
pub const GUARD_GRACE_MS: u64 = 15;

/// A transport budget, not extra event-time credit for a late guard.
pub fn wait_window(one_way_p90_ms: Option<u64>) -> u64 {
    one_way_p90_ms.map_or(MAX_WAIT_MS, |delay| {
        delay
            .saturating_add(GUARD_GRACE_MS + 10)
            .clamp(20, MAX_WAIT_MS)
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Readiness {
    Waiting,
    Complete,
    Deadline,
}

/// A movement barrier cannot advance the combat watermark. Only a contiguous
/// combat prefix proves which inputs have been observed.
#[derive(Clone, Debug, Default)]
pub struct InputWatermark {
    sequence: u64,
    through: Option<u64>,
    required_sequence: u64,
}

impl InputWatermark {
    pub fn observe_barrier(&mut self, sequence: u64) {
        self.required_sequence = self.required_sequence.max(sequence);
    }

    pub fn observe_combat(&mut self, sequence: u64, mapped_time: u64) -> bool {
        if self.sequence.checked_add(1) != Some(sequence) {
            return false;
        }
        self.sequence = sequence;
        // Event samples can precede processing-order heartbeats or move back
        // during clock slewing. Consume the contiguous sequence and lower the
        // watermark conservatively rather than rejecting every following input.
        self.through = Some(mapped_time);
        true
    }

    pub fn observe_contact(&mut self, sequence: u64) -> bool {
        if self.sequence.checked_add(1) != Some(sequence) {
            return false;
        }
        self.sequence = sequence;
        true
    }

    pub fn readiness(&self, contact_at: u64, now: u64, wait_ms: u64) -> Readiness {
        // Strictly later: another sampled input may carry the same timestamp.
        if self.sequence >= self.required_sequence
            && self
                .through
                .is_some_and(|t| t > contact_at.saturating_add(GUARD_GRACE_MS))
        {
            Readiness::Complete
        } else if now >= contact_at.saturating_add(wait_ms.min(MAX_WAIT_MS)) {
            Readiness::Deadline
        } else {
            Readiness::Waiting
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dynamic_wait_is_a_bounded_transport_budget() {
        assert_eq!(wait_window(Some(5)), 30);
        assert_eq!(wait_window(Some(50)), 70);
        assert_eq!(wait_window(Some(200)), 70);
        assert_eq!(wait_window(None), 70);
        assert_eq!(wait_window(Some(u64::MAX)), 70);
        let w = InputWatermark::default();
        assert_eq!(
            w.readiness(450, 479, wait_window(Some(5))),
            Readiness::Waiting
        );
        assert_eq!(
            w.readiness(450, 480, wait_window(Some(5))),
            Readiness::Deadline
        );
    }

    #[test]
    fn grace_and_equal_time_inputs_must_be_observed_before_early_commit() {
        let mut w = InputWatermark::default();
        assert!(w.observe_combat(1, 450));
        assert_eq!(w.readiness(450, 455, 70), Readiness::Waiting);
        assert!(w.observe_combat(2, 465));
        assert_eq!(w.readiness(450, 470, 70), Readiness::Waiting);
        assert!(w.observe_combat(3, 466));
        assert_eq!(w.readiness(450, 470, 70), Readiness::Complete);
    }

    #[test]
    fn independent_movement_lane_never_proves_missing_combat_delivered() {
        let mut w = InputWatermark::default();
        assert!(w.observe_combat(1, 470));
        w.observe_barrier(3);
        assert!(!w.observe_combat(3, 480));
        assert_eq!(w.readiness(450, 500, 70), Readiness::Waiting);
        assert!(w.observe_combat(2, 475));
        assert!(w.observe_combat(3, 480));
        assert_eq!(w.readiness(450, 500, 70), Readiness::Complete);
    }

    #[test]
    fn stalled_stream_hits_fixed_deadline_without_increasing_credit() {
        for requested_wait in [70, 100, 200, 600] {
            let w = InputWatermark::default();
            assert_eq!(w.readiness(450, 519, requested_wait), Readiness::Waiting);
            assert_eq!(w.readiness(450, 520, requested_wait), Readiness::Deadline);
        }
    }

    #[test]
    fn duplicates_are_rejected_and_earlier_samples_lower_watermark() {
        let mut w = InputWatermark::default();
        assert!(w.observe_combat(1, 450));
        assert!(!w.observe_combat(1, 500));
        assert!(w.observe_combat(2, 449));
        assert_eq!(w.readiness(450, 460, 70), Readiness::Waiting);
        assert!(w.observe_combat(3, 466));
    }
}
