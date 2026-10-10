#include "ClientWorld.h"
#include "RemoteImage.h"
#include <cmath>
#include <cstring>

namespace t7 {
namespace {
bool validPosition(const std::array<float, 3>& position) {
    for (auto coordinate : position)
        if (!std::isfinite(coordinate) || std::abs(coordinate) >= 100000) return false;
    return true;
}
TerrainContact terrainContact(const TerrainMailbox& raw) {
    TerrainContact value;
    if ((raw.status != 1 && raw.status != 2) || !validPosition(raw.from) || !validPosition(raw.to)) return value;
    if (raw.status == 1) { value.ready = true; return value; }
    if (!validPosition(raw.normal) || !std::isfinite(raw.fraction) || raw.fraction < 0 || raw.fraction > 1
        || raw.material < -1 || raw.material > 7) return value;
    std::array<float, 3> delta{};
    for (size_t i = 0; i < 3; ++i) delta[i] = raw.to[i] - raw.from[i];
    const auto length = std::hypot(delta[0], delta[1], delta[2]);
    const auto normalLength = std::hypot(raw.normal[0], raw.normal[1], raw.normal[2]);
    if (length < .0001f || normalLength < .9f || normalLength > 1.1f) return value;
    value.ready = value.hit = true; value.material = raw.material;
    for (size_t i = 0; i < 3; ++i) {
        value.position[i] = raw.from[i] + delta[i] * raw.fraction;
        value.normal[i] = raw.normal[i] / normalLength;
        value.direction[i] = delta[i] / length;
    }
    return value;
}
}
void ClientWorld::attach(HANDLE process, uint32_t address) {
    if (process_ || !process || !address) throw std::runtime_error("world mailbox attachment state");
    process_ = process; remote_ = address;
}
void ClientWorld::clear() {
    if (process_ && WaitForSingleObject(process_, 0) != WAIT_OBJECT_0)
        throw std::runtime_error("world bridge cleanup requires terminated owned client");
    process_ = nullptr; remote_ = 0; requestedAt_ = 0; pending_ = false;
}
void ClientWorld::request() {
    const uint32_t command = 1;
    SIZE_T actual = 0;
    requestedAt_ = GetTickCount64();
    if (!WriteProcessMemory(process_, reinterpret_cast<void*>(static_cast<uintptr_t>(remote_)),
                            &command, sizeof(command), &actual) || actual != sizeof(command))
        throw std::runtime_error(errorText("world request publication"));
    pending_ = true;
}
bool ClientWorld::poll(WorldSnapshot& value) {
    if (!process_) return false;
    if (!pending_) { request(); return false; }
    const auto bytes = readClientMemory(process_, remote_, sizeof(WorldMailbox));
    WorldMailbox raw;
    std::memcpy(&raw, bytes.data(), sizeof(raw));
    if (raw.command != 0) return false;
    value = {};
    value.sampledAt = requestedAt_; // Conservative lower bound, not the time of host readback.
    value.instanceStartedAt = raw.instanceStartedAt;
    constexpr float DEGREES_PER_RADIAN = 57.29577951308232f;
    const auto playerHeading = std::remainder(raw.playerYaw, 6.283185307179586f) * DEGREES_PER_RADIAN;
    const auto botHeading = std::remainder(raw.botYaw, 6.283185307179586f) * DEGREES_PER_RADIAN;
    value.ready = raw.ready == 1 && raw.instanceStartedAt != 0
        && GetTickCount64() - requestedAt_ <= 250 && validPosition(raw.player) && validPosition(raw.bot)
        && std::isfinite(playerHeading) && std::isfinite(botHeading);
    if (value.ready) {
        value.player = raw.player; value.bot = raw.bot;
        value.playerHeading = playerHeading; value.botHeading = botHeading;
        for (size_t actor = 0; actor < 2; ++actor)
            for (size_t probe = 0; probe < 2; ++probe)
                value.terrain[actor][probe] = terrainContact(raw.terrain[actor * 2 + probe]);
    }
    pending_ = false;
    request();
    return true;
}
}
