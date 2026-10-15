#pragma once
#include "../core/Common.h"
#include <array>

namespace t7 {
// Offline movement tuning; not a reconstruction of the server's speed formula.
constexpr float JOG_SPEED = 3.f, RUN_SPEED = 5.f;
constexpr uint32_t RUN_DELAY_MS = 1000;
constexpr uint32_t SPRINT_DATA_OFFSET = 0x100, SPRINT_CODE_OFFSET = 0x3000;
constexpr uint32_t JUMP_EVENT_OFFSET = 0x180, END_JUMP_EVENT_OFFSET = 0x198;
constexpr uint32_t ANIMATION_VTABLE_RVA = 0x177722C, ANIMATION_EVENT_RVA = 0x3BEEC0;
inline constexpr std::array<uint32_t, 17> AIRBORNE_ANIMATION_STATES{
    2,235,237,238,239,241,243,244,245,247,249,250,251,253,255,256,257};
void verifySprintFunctions(HANDLE process, uint32_t imageBase);
Bytes buildSprintHook(uint32_t address, uint32_t imageBase, const DashAttackProfile& dash);
void writeSprintDirections(Bytes& page);
}
