#include "CombatProfileHookCode.h"
#include "WorldHookCode.h"
#include <cmath>
#include <set>

namespace t7 {
Bytes buildCombatProfileLookup(uint32_t address, uint32_t imageBase) {
    WorldHookCode c;
    c.emit({0x53,0x56,0x8B,0xF2,0x85,0xC9}); const auto noEntity = c.branch(0x84);
    c.emit({0x68}); c.word(imageBase + 0x179ED94); c.call(imageBase + 0x26A1C0);
    c.emit({0x85,0xC0}); const auto noPlayer = c.branch(0x84);
    c.emit({0x81,0x38}); c.word(imageBase + 0x179ECE4); const auto wrongPlayer = c.branch(0x85);
    // GePlayer::GetHeroID (vtable + 0x24) reads the identity supplied by VISION.
    c.emit({0x8B,0x58,0x2C,0x8B,0x0D}); c.word(address + COMBAT_PROFILES_OFFSET);
    c.emit({0xBA}); c.word(address + COMBAT_PROFILES_OFFSET + 4);
    const auto loop = c.bytes.size();
    c.emit({0x85,0xC9}); const auto exhausted = c.branch(0x84);
    c.emit({0x39,0x1A}); const auto nextHero = c.branch(0x85);
    c.emit({0x39,0x72,4}); const auto nextWeapon = c.branch(0x85);
    c.emit({0x8B,0xC2}); const auto found = c.jump();
    c.bind(nextHero); c.bind(nextWeapon);
    c.emit({0x83,0xC2,COMBAT_PROFILE_STRIDE,0x49}); const auto repeat = c.jump(); c.bind(repeat, loop);
    c.bind(noEntity); c.bind(noPlayer); c.bind(wrongPlayer); c.bind(exhausted); c.emit({0x33,0xC0});
    c.bind(found); c.emit({0x5E,0x5B,0xC3});
    return c.bytes;
}
void writeCombatProfiles(Bytes& page, const std::vector<CombatProfile>& profiles) {
    if (profiles.size() > MAX_COMBAT_PROFILES
        || page.size() < COMBAT_PROFILES_OFFSET + 4 + MAX_COMBAT_PROFILES * COMBAT_PROFILE_STRIDE)
        throw std::runtime_error("combat profile table capacity");
    WorldHookCode c; c.word(static_cast<uint32_t>(profiles.size()));
    std::set<std::pair<int32_t, int32_t>> keys;
    for (const auto& profile : profiles) {
        const auto& movement = profile.movement;
        const auto& dash = profile.dash;
        if (profile.heroId <= 0 || !movement.enabled || movement.weaponId <= 0
            || !keys.emplace(profile.heroId, movement.weaponId).second
            || (dash.enabled && (dash.weaponId != movement.weaponId || dash.moveMs > dash.durationMs
                                 || dash.moveMs > 65535)))
            throw std::runtime_error("invalid combat profile identity");
        c.word(static_cast<uint32_t>(profile.heroId)); c.word(static_cast<uint32_t>(movement.weaponId));
        for (float value : {movement.forwardSlow, movement.lateralSlow, movement.backwardSlow, movement.forwardSprint}) {
            if (!std::isfinite(value) || value <= 0) throw std::runtime_error("invalid combat movement coordinate");
            uint32_t bits; std::memcpy(&bits, &value, 4); c.word(bits);
        }
        if (movement.forwardSprint <= movement.forwardSlow || movement.forwardSprint <= movement.lateralSlow)
            throw std::runtime_error("invalid combat movement gait order");
        c.word(dash.enabled ? dash.moveMs : 0);
        const float speed = dash.enabled ? static_cast<float>(dash.speedMilli) / 1000.f : 0.f;
        uint32_t bits; std::memcpy(&bits, &speed, 4); c.word(bits);
    }
    std::memcpy(page.data() + COMBAT_PROFILES_OFFSET, c.bytes.data(), c.bytes.size());
}
}
