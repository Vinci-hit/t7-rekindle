#pragma once
#include "../core/Common.h"

namespace t7r {
constexpr uint32_t WEAPON_TRAIL_CODE_OFFSET = 0x8500;
void verifyWeaponTrailFunctions(HANDLE process, uint32_t imageBase);
Bytes buildWeaponTrailHook(uint32_t address, uint32_t imageBase);
void writeWeaponTrailPreset(Bytes& page);
}
