#pragma once
#include <array>
#include <cstdint>

namespace t7 {
struct TerrainContact {
    bool ready = false, hit = false;
    int32_t material = -1;
    std::array<float, 3> position{}, normal{}, direction{};
};
// Internal Runtime -> Business data, not part of the launcher C ABI.
struct WorldSnapshot {
    bool ready = false;
    uint64_t sampledAt = 0, instanceStartedAt = 0;
    std::array<float, 3> player{}, bot{};
    float playerHeading = 0, botHeading = 0; // Degrees, normalized to [-180, 180].
    // Per actor: forward reach, then the segment ending at the opponent (not beyond it).
    std::array<std::array<TerrainContact, 2>, 2> terrain{};
};
}
