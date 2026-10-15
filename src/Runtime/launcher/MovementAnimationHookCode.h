#pragma once
#include "../core/Common.h"

namespace t7 {
constexpr uint32_t MOVEMENT_ANIMATION_CODE_OFFSET = 0x1E000;
constexpr uint32_t MOVEMENT_ANIMATION_RVA = 0x3D2B9D;
inline const Bytes MOVEMENT_ANIMATION_ORIGINAL{0xF3,0x0F,0x10,0x46,0x5C};
void verifyMovementAnimationFunction(HANDLE process, uint32_t imageBase);
Bytes buildMovementAnimationHook(uint32_t address, uint32_t imageBase, uint32_t threadId,
                                 const MovementAnimationProfile& profile);
Bytes movementAnimationJump(uint32_t address, uint32_t imageBase);
}
