#pragma once
#include "../core/Common.h"

namespace t7 {
constexpr uint32_t MOVEMENT_COMMAND_CODE_OFFSET = 0x1D000, MOVEMENT_COMMAND_RVA = 0x3CFE38;
inline const Bytes MOVEMENT_COMMAND_ORIGINAL{0x8B,3,0x8B,0xF9,0x8B,0xCB};
void verifyMovementCommandFunction(HANDLE process, uint32_t imageBase);
Bytes buildMovementCommandHook(uint32_t address, uint32_t imageBase, uint32_t threadId);
Bytes movementCommandJump(uint32_t address, uint32_t imageBase);
}
