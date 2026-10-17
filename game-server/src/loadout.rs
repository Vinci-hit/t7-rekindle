//! Catalogue-backed selections; clients submit IDs, never action timings.
use crate::protocol::ErrorCode;
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, sync::OnceLock};

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Equipment {
    pub slot: u8,
    pub weapon_id: i32,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Loadout {
    pub hero_id: i32,
    pub equipment: Vec<Equipment>,
}

fn catalogue() -> &'static BTreeMap<i32, BTreeMap<i32, serde_json::Value>> {
    static DATA: OnceLock<BTreeMap<i32, BTreeMap<i32, serde_json::Value>>> = OnceLock::new();
    DATA.get_or_init(|| {
        serde_json::from_str(include_str!("combat_profiles.json"))
            .expect("verified combat catalogue")
    })
}
pub fn profile(hero: i32, weapon: i32) -> Option<&'static serde_json::Value> {
    catalogue().get(&hero)?.get(&weapon)
}
impl Default for Loadout {
    fn default() -> Self {
        Self {
            hero_id: 1101,
            equipment: vec![Equipment {
                slot: 1,
                weapon_id: crate::battle::MAIN_WEAPON,
            }],
        }
    }
}
impl Loadout {
    pub fn validate(&self) -> Result<(), ErrorCode> {
        let mut slots = std::collections::BTreeSet::new();
        let mut weapons = std::collections::BTreeSet::new();
        let mut shields = 0;
        if !catalogue().contains_key(&self.hero_id) || self.equipment.len() > 4 {
            return Err(ErrorCode::BadRequest);
        }
        for item in &self.equipment {
            let p = profile(self.hero_id, item.weapon_id).ok_or(ErrorCode::BadRequest)?;
            if !(1..=4).contains(&item.slot)
                || item.weapon_id == crate::battle::FIST_WEAPON
                || !slots.insert(item.slot)
                || !weapons.insert(item.weapon_id)
            {
                return Err(ErrorCode::BadRequest);
            }
            shields += usize::from(p["shield"].as_bool() == Some(true));
        }
        if shields > 1 {
            return Err(ErrorCode::BadRequest);
        }
        Ok(())
    }
    pub fn initial_weapon(&self) -> i32 {
        self.equipment
            .iter()
            .filter(|i| {
                !profile(self.hero_id, i.weapon_id).unwrap()["shield"]
                    .as_bool()
                    .unwrap()
            })
            .min_by_key(|i| i.slot)
            .map_or(crate::battle::FIST_WEAPON, |i| i.weapon_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn catalogue_and_selections_cover_every_hero_weapon() {
        assert_eq!(catalogue().len(), 36);
        let mut melee = 0;
        for (&hero, weapons) in catalogue() {
            for (&weapon, profile) in weapons {
                if weapon == crate::battle::FIST_WEAPON {
                    continue;
                }
                let selection = Loadout {
                    hero_id: hero,
                    equipment: vec![Equipment {
                        slot: 4,
                        weapon_id: weapon,
                    }],
                };
                selection.validate().unwrap();
                if profile["shield"] == false {
                    assert_eq!(selection.initial_weapon(), weapon);
                    melee += 1;
                }
            }
        }
        assert_eq!(melee + 36, 246);
        assert!(
            Loadout {
                hero_id: 1101,
                equipment: vec![Equipment {
                    slot: 1,
                    weapon_id: 1010811
                }]
            }
            .validate()
            .is_err()
        );
    }
}

#[cfg(test)]
mod room_tests {
    use super::*;
    #[test]
    fn selection_is_broadcast_in_room_snapshot_and_locked_after_loading() {
        let mut lobby = crate::lobby::Lobby::default();
        let room = lobby.create("owner", "room", crate::room::MAP_ID).unwrap();
        let loading = lobby.start("owner", 0).unwrap();
        let id = loading.current_match.unwrap().match_id;
        let selection = Loadout {
            hero_id: 1102,
            equipment: vec![Equipment {
                slot: 1,
                weapon_id: 1010911,
            }],
        };
        let updated = lobby
            .select_loadout("owner", &id, selection.clone())
            .unwrap();
        assert_eq!(updated.members[0].loadout, Some(selection.clone()));
        assert!(updated.revision > room.revision);
        lobby.loaded("owner", &id, 1).unwrap();
        assert_eq!(
            lobby.select_loadout("owner", &id, selection.clone()),
            Err(ErrorCode::WrongPhase)
        );
        lobby.advance(1 + crate::room::COUNTDOWN_MS);
        assert_eq!(lobby.weapon("owner"), Ok(1010911));
        assert_eq!(
            lobby.select_loadout("owner", &id, selection),
            Err(ErrorCode::WrongPhase)
        );
    }
}
