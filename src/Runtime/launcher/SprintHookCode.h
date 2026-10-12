#pragma once
#include "../core/Common.h"

namespace t7 {
// Offline movement tuning; not a reconstruction of the server's speed formula.
constexpr float JOG_SPEED = 3.f, RUN_SPEED = 5.f;
constexpr uint32_t RUN_DELAY_MS = 1000;
// s_battle_param_cli: group 6, weapon category 32, action 32, mode 2.
// Native movement commands convert fixed-point velocity to world units by /1000.
constexpr float DASH_SPEED = 2585.f / 1000.f;
constexpr uint32_t DASH_MOVE_MS = 967;
constexpr uint32_t SPRINT_DATA_OFFSET = 0x100, SPRINT_CODE_OFFSET = 0x3000;
void verifySprintFunctions(HANDLE process, uint32_t imageBase);
Bytes buildSprintHook(uint32_t address, uint32_t imageBase);
void writeSprintDirections(Bytes& page);
}
