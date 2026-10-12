#pragma once
#include "../core/Common.h"

namespace t7 {
constexpr uint32_t ROUND_END_CODE_OFFSET = 0x1F000, ROUND_END_DATA_OFFSET = 0x600;
constexpr uint32_t FINISH_OBSERVER_OFFSET = 0x1F400, LEAVE_OBSERVER_OFFSET = 0x1F800;
constexpr uint32_t FINISH_RESULT_RVA = 0x6A7B73, NATIVE_LEAVE_RVA = 0x79C790;
struct RoundEndMailbox {
    uint64_t epoch = 0;
    uint32_t status = 0; // 0 idle, 1 logout requested, 2 native handoff failed.
    uint32_t result = 0; // Set only when this instance's FINISH_GAME is consumed.
};
static_assert(sizeof(RoundEndMailbox) == 16, "round end mailbox layout");
void verifyRoundEndFunctions(HANDLE process, uint32_t imageBase);
Bytes buildRoundEndHooks(uint32_t address, uint32_t imageBase);
std::vector<std::pair<uint32_t, Bytes>> roundEndOriginals(uint32_t imageBase);
std::vector<std::pair<uint32_t, Bytes>> roundEndJumps(uint32_t address, uint32_t imageBase);
}
