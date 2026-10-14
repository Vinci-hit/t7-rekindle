#pragma once
#include "../core/Common.h"

namespace t7r {
constexpr uint32_t MOVEMENT_COMMAND_CODE_OFFSET = 0x1D000, MOVEMENT_COMMAND_RVA = 0x3CFE38;
inline const Bytes MOVEMENT_COMMAND_ORIGINAL{0x8B,3,0x8B,0xF9,0x8B,0xCB};
constexpr uint32_t AIR_INPUT_CODE_OFFSET = MOVEMENT_COMMAND_CODE_OFFSET + 0x800, AIR_INPUT_RVA = 0x3CFC09;
inline const Bytes AIR_INPUT_ORIGINAL{0x8B,1,0x53,0xFF,0x50,8};
void verifyMovementCommandFunction(HANDLE process, uint32_t imageBase);
Bytes buildMovementCommandHook(uint32_t address, uint32_t imageBase, uint32_t threadId);
Bytes movementCommandJump(uint32_t address, uint32_t imageBase);
Bytes buildAirInputHook(uint32_t address, uint32_t imageBase, uint32_t threadId);
Bytes airInputJump(uint32_t address, uint32_t imageBase);
}
