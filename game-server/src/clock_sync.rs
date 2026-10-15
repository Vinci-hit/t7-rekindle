//! Connection-scoped monotonic clock calibration. Client timestamps remain
//! untrusted; mapping bounds and uncertainty are separate from RTT estimates.
use std::collections::VecDeque;

use serde::{Deserialize, Serialize};

pub const SAMPLE_WINDOW_MS: u64 = 20_000;
pub const MAX_SAMPLES: usize = 32;
pub const MAX_PROBE_AGE_MS: u64 = 2_000;
pub const MAX_CLIENT_HOLD_MS: u64 = 80;
pub const MAX_REPORT_AGE_MS: u64 = 100;

#[derive(Clone, Debug, Deserialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Echo {
    pub epoch: String,
    pub nonce: String,
    pub received_ms: u64,
    pub sent_ms: u64,
}

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Estimate {
    pub epoch: String,
    pub offset_ms: i64,
    pub uncertainty_ms: u64,
    pub round_trip_ms: u64,
    pub samples: usize,
}

#[derive(Debug)]
struct Sample {
    at: u64,
    offset: i64,
    round_trip: u64,
}

#[derive(Debug)]
pub struct ClockSync {
    epoch: String,
    pending: Option<(String, u64)>,
    samples: VecDeque<Sample>,
    estimate: Option<Estimate>,
    updated_at: u64,
}

impl Default for ClockSync {
    fn default() -> Self {
        Self {
            epoch: uuid::Uuid::new_v4().to_string(),
            pending: None,
            samples: VecDeque::new(),
            estimate: None,
            updated_at: 0,
        }
    }
}

impl ClockSync {
    pub fn probe(&mut self, now: u64) -> (String, String) {
        if self.estimate.is_some() && now.saturating_sub(self.updated_at) > SAMPLE_WINDOW_MS {
            *self = Self::default();
        }
        let nonce = uuid::Uuid::new_v4().to_string();
        self.pending = Some((nonce.clone(), now));
        (self.epoch.clone(), nonce)
    }

    pub fn echo(&mut self, echo: &Echo, now: u64) -> Option<Estimate> {
        let (nonce, sent) = self.pending.as_ref()?;
        if echo.epoch != self.epoch || echo.nonce != *nonce {
            return None;
        }
        let round_trip = now.checked_sub(*sent)?;
        let hold = echo.sent_ms.checked_sub(echo.received_ms)?;
        if round_trip > MAX_PROBE_AGE_MS || hold > MAX_CLIENT_HOLD_MS || hold > round_trip {
            return None;
        }
        let offset = (i128::from(*sent) + i128::from(now)
            - i128::from(echo.received_ms)
            - i128::from(echo.sent_ms))
            / 2;
        let offset = i64::try_from(offset).ok()?;
        self.pending = None;
        // Never subtract an untrusted client hold from the uncertainty bound.
        self.samples
            .retain(|s| now.saturating_sub(s.at) <= SAMPLE_WINDOW_MS);
        self.samples.push_back(Sample {
            at: now,
            offset,
            round_trip,
        });
        while self.samples.len() > MAX_SAMPLES {
            self.samples.pop_front();
        }
        let best = self.samples.iter().min_by_key(|s| s.round_trip)?;
        let offset_ms = match &self.estimate {
            None => best.offset,
            Some(previous) => {
                let step = (now.saturating_sub(self.updated_at) / 500)
                    .max(1)
                    .min(i64::MAX as u64) as i64;
                best.offset.clamp(
                    previous.offset_ms.saturating_sub(step),
                    previous.offset_ms.saturating_add(step),
                )
            }
        };
        let slew_error = offset_ms.abs_diff(best.offset);
        let estimate = Estimate {
            epoch: self.epoch.clone(),
            offset_ms,
            uncertainty_ms: best.round_trip.div_ceil(2).saturating_add(slew_error),
            round_trip_ms: round_trip,
            samples: self.samples.len(),
        };
        self.updated_at = now;
        self.estimate = Some(estimate.clone());
        Some(estimate)
    }

    pub fn one_way_p90(&self, now: u64) -> Option<u64> {
        let mut rtts: Vec<_> = self
            .samples
            .iter()
            .filter(|sample| now.saturating_sub(sample.at) <= SAMPLE_WINDOW_MS)
            .map(|sample| sample.round_trip)
            .collect();
        rtts.sort_unstable();
        let index = (rtts.len() * 9).div_ceil(10).checked_sub(1)?;
        Some(rtts[index].div_ceil(2))
    }

    /// Report lateness is bounded independently of rewind history and phase length.
    pub fn map(&self, epoch: &str, sampled_ms: u64, now: u64) -> Option<(u64, bool)> {
        let estimate = self.estimate.as_ref()?;
        if epoch != self.epoch || now.saturating_sub(self.updated_at) > SAMPLE_WINDOW_MS {
            return None;
        }
        let raw = i128::from(sampled_ms) + i128::from(estimate.offset_ms);
        let bounded = raw.clamp(
            i128::from(now.saturating_sub(MAX_REPORT_AGE_MS)),
            i128::from(now),
        );
        Some((bounded as u64, raw != bounded))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sync(
        clock: &mut ClockSync,
        now: u64,
        down: u64,
        up: u64,
        hold: u64,
        offset: u64,
    ) -> Estimate {
        let (epoch, nonce) = clock.probe(now);
        clock
            .echo(
                &Echo {
                    epoch,
                    nonce,
                    received_ms: now + down + offset,
                    sent_ms: now + down + offset + hold,
                },
                now + down + hold + up,
            )
            .unwrap()
    }

    #[test]
    fn transport_budget_uses_recent_tail_latency_and_expires() {
        let mut c = ClockSync::default();
        assert_eq!(c.one_way_p90(0), None);
        for (i, one_way) in [5, 5, 5, 50].into_iter().enumerate() {
            sync(&mut c, 1000 + i as u64 * 1000, one_way, one_way, 0, 10_000);
        }
        assert_eq!(c.one_way_p90(5000), Some(50));
        assert_eq!(c.one_way_p90(30_000), None);
    }

    #[test]
    fn symmetric_links_map_the_same_frame_time_at_different_rtt() {
        for one_way in [5, 50, 100] {
            let mut clock = ClockSync::default();
            let e = sync(&mut clock, 1000, one_way, one_way, 0, 10_000);
            assert_eq!(e.offset_ms, -10_000);
            assert_eq!(
                clock.map(&e.epoch, 12_000, 2000 + one_way),
                Some((2000, false))
            );
            assert_eq!(e.uncertainty_ms, one_way);
        }
    }

    #[test]
    fn asymmetric_path_bias_is_exposed_as_uncertainty() {
        let mut clock = ClockSync::default();
        let e = sync(&mut clock, 1000, 20, 40, 0, 10_000);
        assert_eq!(e.offset_ms, -9990);
        assert_eq!(e.uncertainty_ms, 30);
    }

    #[test]
    fn host_stall_does_not_silently_become_clock_offset() {
        let mut clock = ClockSync::default();
        let e = sync(&mut clock, 1000, 50, 50, 80, 10_000);
        assert_eq!(e.offset_ms, -10_000);
        assert_eq!(e.round_trip_ms, 180);
        assert_eq!(e.uncertainty_ms, 90);
    }

    #[test]
    fn stale_calibration_starts_a_new_epoch_instead_of_slewing_from_old_state() {
        let mut clock = ClockSync::default();
        let old = sync(&mut clock, 1000, 5, 5, 0, 10_000);
        let new = sync(&mut clock, 30_000, 5, 5, 0, 20_000);
        assert_ne!(old.epoch, new.epoch);
        assert_eq!(new.offset_ms, -20_000);
        assert_eq!(new.samples, 1);
        assert!(clock.map(&old.epoch, 50_000, 30_010).is_none());
    }

    #[test]
    fn jitter_and_tcp_stalls_do_not_replace_the_lowest_uncertainty_sample() {
        let mut clock = ClockSync::default();
        let initial = sync(&mut clock, 1000, 5, 5, 0, 10_000);
        for (i, delay) in [30, 80, 200, 400].into_iter().enumerate() {
            let next = sync(&mut clock, 2000 + i as u64 * 1000, 5, delay, 0, 10_000);
            assert_eq!(next.offset_ms, initial.offset_ms);
            assert_eq!(next.uncertainty_ms, 5);
        }
    }

    #[test]
    fn nonce_epoch_expiry_and_backdating_are_bounded() {
        let mut clock = ClockSync::default();
        let (epoch, nonce) = clock.probe(1000);
        let echo = Echo {
            epoch: epoch.clone(),
            nonce,
            received_ms: 11_050,
            sent_ms: 11_050,
        };
        assert!(clock.echo(&echo, 1100).is_some());
        assert!(clock.echo(&echo, 1101).is_none());
        assert_eq!(clock.map(&epoch, 0, 2000), Some((1900, true)));
        assert_eq!(clock.map(&epoch, u64::MAX, 2000), Some((2000, true)));
        assert!(clock.map("old-epoch", 12_000, 2000).is_none());
        assert!(clock.map(&epoch, 40_000, 30_000).is_none());
    }
}
