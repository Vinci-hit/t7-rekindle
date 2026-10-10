#pragma once
#include "../core/Common.h"
#include "../core/WorldSnapshot.h"

namespace t7 {
struct TerrainMailbox {
    uint32_t status = 0; // 0 unavailable, 1 clear, 2 hit.
    int32_t material = -1;
    std::array<float, 3> from{}, to{}, normal{};
    float fraction = 1;
};
static_assert(sizeof(TerrainMailbox) == 48, "terrain mailbox layout");
struct WorldMailbox {
    uint32_t command = 0, ready = 0;
    uint64_t instanceStartedAt = 0;
    std::array<float, 3> player{}, bot{};
    float playerYaw = 0, botYaw = 0; // Original controller radians.
    std::array<TerrainMailbox, 4> terrain{};
};
static_assert(sizeof(WorldMailbox) == 240, "world mailbox layout");
constexpr uint32_t WORLD_CODE_OFFSET = 0x1000, WORLD_PAGE_SIZE = 0x5000;
constexpr uint32_t TERRAIN_CODE_OFFSET = 0x2000;
void verifyWorldFunctions(HANDLE process, uint32_t imageBase);
void verifyTerrainFunctions(HANDLE process, uint32_t imageBase);
Bytes buildTerrainHook(uint32_t address, uint32_t imageBase);
Bytes buildWorldHook(uint32_t address, uint32_t imageBase, uint32_t threadId);

class ClientWorld final {
public:
    void attach(HANDLE process, uint32_t address);
    void clear();
    bool poll(WorldSnapshot& value);
private:
    void request();
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0;
    ULONGLONG requestedAt_ = 0;
    bool pending_ = false;
};
}
