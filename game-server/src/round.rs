//! Immutable, server-owned results retained through the native settlement screen.
use crate::combat::Health;
use serde::Serialize;

pub const DEFAULT_DURATION_MS: u64 = 1_200_000;
pub const DEFAULT_KILL_TARGET: u32 = 60;
pub const SETTLEMENT_HOLD_MS: u64 = 5000;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct RoundResult {
    pub reason: &'static str,
    /// 1/2 are winning camps; 3 is a draw.
    pub result: u8,
    pub ended_at_ms: u64,
    pub lives: Vec<Health>,
    pub ranks: Vec<FinalRank>,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct FinalRank {
    pub actor_id: u32,
    pub rank: u32,
}

pub(crate) fn finish(
    lives: Vec<Health>,
    camps: impl Iterator<Item = (u32, u8)>,
    timeout: bool,
    target: u32,
    now: u64,
) -> Option<RoundResult> {
    let camps: Vec<_> = camps.collect();
    let mut kills = [0_u64; 2];
    for &(id, camp) in &camps {
        kills[usize::from(camp - 1)] +=
            u64::from(lives.iter().find(|life| life.actor_id.0 == id)?.kills);
    }
    let reached = kills.iter().any(|kills| *kills >= u64::from(target));
    if !timeout && !reached {
        return None;
    }
    let mut ranks = Vec::new();
    for camp in [1, 2] {
        let mut team: Vec<_> = lives
            .iter()
            .filter(|life| camps.contains(&(life.actor_id.0, camp)))
            .collect();
        team.sort_by_key(|life| (std::cmp::Reverse(life.kills), life.deaths, life.actor_id.0));
        ranks.extend(team.into_iter().enumerate().map(|(index, life)| FinalRank {
            actor_id: life.actor_id.0,
            rank: index as u32 + 1,
        }));
    }
    Some(RoundResult {
        reason: if reached { "kill-target" } else { "timeout" },
        result: match kills[0].cmp(&kills[1]) {
            std::cmp::Ordering::Greater => 1,
            std::cmp::Ordering::Less => 2,
            std::cmp::Ordering::Equal => 3,
        },
        ended_at_ms: now,
        lives,
        ranks,
    })
}
