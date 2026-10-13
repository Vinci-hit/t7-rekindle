#include "MovementAnimationHookCode.h"
#include "RemoteImage.h"
#include "SprintHookCode.h"
#include "WorldHookCode.h"
#include <cmath>

namespace t7 {
namespace {
constexpr uint32_t WRITER_RVA = 0x3D2BF2, RETURN_RVA = 0x3D2C0B, CONSTANTS_OFFSET = 0x700;
}
void verifyMovementAnimationFunction(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {MOVEMENT_ANIMATION_RVA, {0xF3,0x0F,0x10,0x46,0x5C,0xF3,0x0F,0x10,0x4C,0x24,0x10}},
        {WRITER_RVA, {0x8B,0x4E,0x58,0xF3,0x0F,0x10,0x46,0x5C,0x8B,0x01,0x51,0xF3,0x0F,0x11,0x04,0x24}},
        {RETURN_RVA, {0x5F,0x5E,0x5B,0x8B,0xE5,0x5D,0xC3}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("movement animation writer signature mismatch");
    }
}
Bytes movementAnimationJump(uint32_t address, uint32_t imageBase) {
    WorldHookCode c; c.emit({0xE9});
    c.word(address + MOVEMENT_ANIMATION_CODE_OFFSET - (imageBase + MOVEMENT_ANIMATION_RVA + 5));
    return c.bytes;
}
Bytes buildMovementAnimationHook(uint32_t address, uint32_t imageBase, uint32_t threadId,
                                 const MovementAnimationProfile& profile) {
    if (!profile.enabled) return {};
    if (profile.weaponId <= 0) throw std::runtime_error("invalid movement animation weapon");
    for (float value : {profile.forwardSlow, profile.lateralSlow, profile.backwardSlow, profile.forwardSprint})
        if (!std::isfinite(value) || value <= 0) throw std::runtime_error("invalid movement animation profile");
    if (profile.forwardSprint <= profile.forwardSlow || profile.forwardSprint <= profile.lateralSlow)
        throw std::runtime_error("invalid movement animation gait order");
    const auto code = address + MOVEMENT_ANIMATION_CODE_OFFSET, data = address + SPRINT_DATA_OFFSET;
    const auto constants = code + CONSTANTS_OFFSET;
    WorldHookCode c;
    auto jump = [&](uint32_t target) { c.emit({0xE9}); c.word(target - (code + static_cast<uint32_t>(c.bytes.size()) + 4)); };
    auto restore = [&] { c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D}); };
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    std::vector<size_t> native;
    c.emit({0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId); native.push_back(c.branch(0x85));
    c.emit({0x8B,0x46,8,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0x3B,0x05}); c.word(data + 8); native.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 28); c.emit({4}); native.push_back(c.branch(0x85));
    c.emit({0x80,0x7E,0x20,0}); native.push_back(c.branch(0x85));
    auto component = [&](uint32_t name, uint32_t table) {
        c.emit({0x8B,0x4E,8,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
        c.emit({0x85,0xC0}); native.push_back(c.branch(0x84));
        c.emit({0x81,0x38}); c.word(imageBase + table); native.push_back(c.branch(0x85));
    };
    component(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); native.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54,0x85,0xC0}); native.push_back(c.branch(0x84));
    c.emit({0xF6,0x00,0x10}); native.push_back(c.branch(0x85)); // Crouch retains its native graph coordinates.
    component(0x1783860, 0x178380C);
    c.emit({0x81,0xB8}); c.word(0x98); c.word(static_cast<uint32_t>(profile.weaponId));
    native.push_back(c.branch(0x85)); // Sheathing or switching weapons must not borrow this character's coordinates.
    c.emit({0x8B,0x58,0x38,0x83,0xFB,2}); const auto idle = c.branch(0x84);
    c.emit({0x8D,0x83}); c.word(static_cast<uint32_t>(-223));
    c.emit({0x83,0xF8,3}); const auto holding = c.branch(0x86);
    c.emit({0x8D,0x83}); c.word(static_cast<uint32_t>(-305));
    c.emit({0x83,0xF8,3}); native.push_back(c.branch(0x87));
    c.bind(idle); c.bind(holding);
    c.emit({0x8B,0x46,0x60,0x8D,0x50,0xFF,0x83,0xFA,7}); native.push_back(c.branch(0x87));
    c.emit({0x33,0xFF}); // EDI selects established running, independently of physical velocity.
    c.emit({0xBA}); c.word(constants + 16); // Forward diagonals share the lateral Slow property.
    c.emit({0x83,0xF8,1}); const auto notForward = c.branch(0x85);
    c.emit({0xBA}); c.word(constants + 12); c.bind(notForward);
    c.emit({0x8D,0x48,0xFC,0x83,0xF9,2}); const auto notBackward = c.branch(0x87);
    c.emit({0xBA}); c.word(constants + 20); c.bind(notBackward);
    std::vector<size_t> slow;
    c.emit({0x83,0xFB,2}); slow.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 16); c.emit({2}); slow.push_back(c.branch(0x85));
    c.emit({0x83,0xF8,2}); const auto forward = c.branch(0x86);
    c.emit({0x83,0xF8,8}); slow.push_back(c.branch(0x85));
    c.bind(forward); c.emit({0x47});
    for (auto branch : slow) c.bind(branch);
    c.emit({0x8B,0xDA}); // EBX Slow coordinate and EDI gait survive native getters.
    c.emit({0x8B,0x4E,0x38,0x85,0xC9}); native.push_back(c.branch(0x84));
    c.emit({0x8B,0x01,0xFF,0x50,0x2C,0x3C,1}); const auto inactive = c.branch(0x85);
    c.emit({0x8B,0x4E,0x38,0x8B,0x01,0xFF,0x50,0x40,0xD9,0x9C,0x24}); c.word(0x200);
    c.emit({0xF3,0x0F,0x10,0x84,0x24}); c.word(0x200);
    c.emit({0x0F,0x2F,0x05}); c.word(constants); const auto belowFloor = c.branch(0x82);
    c.emit({0xF3,0x0F,0x10,0x45,0x34,0xF3,0x0F,0x5F,0x05}); c.word(constants);
    const auto floorApplied = c.jump(); c.bind(inactive); c.bind(belowFloor);
    c.emit({0xF3,0x0F,0x10,0x45,0x34}); // Original [ESP+0x10], before pushfd/pushad.
    c.bind(floorApplied);
    // Both gaits meet at JOG_SPEED -> Slow. Arming a run must not move the blend backwards while velocity catches up.
    c.emit({0x85,0xFF}); const auto slowGait = c.branch(0x84);
    c.emit({0x0F,0x2F,0x05}); c.word(constants + 4); const auto slowVelocity = c.branch(0x82);
    c.emit({0xF3,0x0F,0x5C,0x05}); c.word(constants + 4);
    c.emit({0xF3,0x0F,0x5E,0x05}); c.word(constants + 8);
    c.emit({0xF3,0x0F,0x5D,0x05}); c.word(constants + 28);
    c.emit({0xF3,0x0F,0x10,0x0D}); c.word(constants + 24);
    c.emit({0xF3,0x0F,0x5C,0x0B,0xF3,0x0F,0x59,0xC1,0xF3,0x0F,0x58,0x03});
    const auto mapped = c.jump(); c.bind(slowGait); c.bind(slowVelocity);
    // Old running velocity is capped immediately when defense selects Slow.
    c.emit({0xF3,0x0F,0x5E,0x05}); c.word(constants + 4);
    c.emit({0xF3,0x0F,0x5D,0x05}); c.word(constants + 28);
    c.emit({0xF3,0x0F,0x59,0x03}); c.bind(mapped);
    c.emit({0x0F,0x2E,0x46,0x5C}); const auto same = c.branch(0x84);
    c.emit({0xF3,0x0F,0x11,0x46,0x5C}); restore(); jump(imageBase + WRITER_RVA);
    c.bind(same); restore(); jump(imageBase + RETURN_RVA);
    for (auto branch : native) c.bind(branch);
    restore(); c.bytes.insert(c.bytes.end(), MOVEMENT_ANIMATION_ORIGINAL.begin(), MOVEMENT_ANIMATION_ORIGINAL.end());
    jump(imageBase + MOVEMENT_ANIMATION_RVA + 5);
    if (c.bytes.size() > CONSTANTS_OFFSET) throw std::runtime_error("movement animation hook overflow");
    c.bytes.resize(CONSTANTS_OFFSET, 0);
    for (float value : {1.5f, JOG_SPEED, RUN_SPEED - JOG_SPEED, profile.forwardSlow, profile.lateralSlow,
                       profile.backwardSlow, profile.forwardSprint, 1.f}) {
        uint32_t bits; std::memcpy(&bits, &value, 4); c.word(bits);
    }
    return c.bytes;
}
}
