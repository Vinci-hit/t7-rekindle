#pragma once
#include "../core/Common.h"

namespace t7 {
constexpr uint32_t MOVEMENT_ANIMATION_CODE_OFFSET = 0x1E000;
constexpr uint32_t MOVEMENT_ANIMATION_RVA = 0x3D2B9D;
constexpr uint32_t MOVEMENT_POSE_RVA = 0x29FB47;
constexpr uint32_t DEFENSE_EXIT_RVA = 0xCE0D50;
inline const Bytes MOVEMENT_ANIMATION_ORIGINAL{0xF3,0x0F,0x10,0x46,0x5C};
inline const Bytes MOVEMENT_POSE_ORIGINAL{0xFF,0x70,0x04,0x8B,0xCE};
inline const Bytes DEFENSE_EXIT_ORIGINAL{0xFF,0x74,0x24,8,0xFF,0x74,0x24,8};
void verifyMovementAnimationFunction(HANDLE process, uint32_t imageBase);
Bytes buildMovementAnimationHook(uint32_t address, uint32_t imageBase, uint32_t threadId,
                                 const MovementAnimationProfile& profile, bool dynamicProfiles = false);
Bytes movementAnimationJump(uint32_t address, uint32_t imageBase);
Bytes movementPoseJump(uint32_t address, uint32_t imageBase);
Bytes defenseExitJump(uint32_t address, uint32_t imageBase);
}
