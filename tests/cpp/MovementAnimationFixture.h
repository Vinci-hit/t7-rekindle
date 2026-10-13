#pragma once
#include "SprintHookFixture.h"
#include "../../src/Runtime/launcher/MovementAnimationHookCode.h"

namespace movementAnimationFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xF000, DISPATCH = BASE + 0xF100;
constexpr uint32_t VELOCITY = DATA + 4, WRITES = DATA + 8, VARIABLE = DATA + 12, ACTIVE = DATA + 16;
inline t7::MovementAnimationProfile profile() { return {true, 1030411, 2.25f, 1.75f, .75f, 4.25f}; }
inline void writeFloat(HANDLE process, uint32_t address, float value) {
    uint32_t bits; std::memcpy(&bits, &value, 4); writeWord(process, address, bits);
}
inline float readFloat(HANDLE process, uint32_t address) {
    const auto bits = readWord(process, address); float value; std::memcpy(&value, &bits, 4); return value;
}
inline void prepare(HANDLE process) {
    t7::WorldHookCode c;
    c.emit({0x8B,0x0D}); c.word(DATA); c.emit({0x85,0xC9}); const auto disabled = c.branch(0x84);
    c.call(BASE + 0x3D2A10); c.bind(disabled); c.emit({0xC3});
    t7::writeClientMemory(process, DISPATCH, c.bytes);
    writeWord(process, worldFixture::TABLE + 0x2C, BASE + 0xF500);
    writeWord(process, worldFixture::TABLE + 0x40, BASE + 0xF520);
    c.bytes.clear(); c.emit({0xA1}); c.word(ACTIVE); c.emit({0xC3});
    t7::writeClientMemory(process, BASE + 0xF500, c.bytes);
    t7::writeClientMemory(process, BASE + 0xF520, {0xD9,0x41,0x24,0xC3});
    writeWord(process, ACTIVE, 1);
    writeWord(process, BASE + 0x177722C + 0x54, BASE + 0xF540);
    c.bytes.clear(); c.emit({0x8B,0x44,0x24,4,0xA3}); c.word(VARIABLE);
    c.emit({0x8B,0x44,0x24,8,0x89,0x41,0x34,0xFF,0x05}); c.word(WRITES); c.emit({0xC2,8,0});
    t7::writeClientMemory(process, BASE + 0xF540, c.bytes);
    writeWord(process, BASE + 0x2D37918, 123);
    writeFloat(process, BASE + 0x1A71B3C, 1.5f);
    const uint32_t directions[]{0,0,1,8,7,6,5,4,3,2,1,8,2};
    for (uint32_t i = 0; i < 13; ++i) writeWord(process, DATA + 0x80 + i * 4, directions[i]);
    // Native updater stack layout and MoveDir cache precede the hooked speed block.
    c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x83,0xE4,0xF8,0x83,0xEC,0x1C,0x53,0x56,0x57,0x8B,0xF1});
    c.emit({0xA1}); c.word(VELOCITY); c.emit({0x89,0x44,0x24,0x10,0x8B,0x46,0x2C,0x8B,0x04,0x85});
    c.word(DATA + 0x80); c.emit({0x89,0x46,0x60,0xE9});
    c.word(BASE + t7::MOVEMENT_ANIMATION_RVA - (BASE + 0x3D2A10 + static_cast<uint32_t>(c.bytes.size()) + 4));
    t7::writeClientMemory(process, BASE + 0x3D2A10, c.bytes);
    // Original cache comparison, active-controller floor, and single native variable setter.
    c.bytes = t7::MOVEMENT_ANIMATION_ORIGINAL;
    c.emit({0xF3,0x0F,0x10,0x4C,0x24,0x10,0x0F,0x2E,0xC1,0x9F,0xF6,0xC4,0x44,0x7B,0x5A,
        0x8B,0x4E,0x38,0xF3,0x0F,0x11,0x4E,0x5C,0x8B,0x01,0x8B,0x40,0x2C,0xFF,0xD0,0x3C,1,0x75,0x2E,
        0x8B,0x4E,0x38,0x8B,0x01,0x8B,0x40,0x40,0xFF,0xD0,0xD9,0x5C,0x24,0x18,
        0xF3,0x0F,0x10,0x44,0x24,0x18,0xF3,0x0F,0x10,0x0D}); c.word(BASE + 0x1A71B3C);
    c.emit({0x0F,0x2F,0xC1,0x72,0x0D,0x0F,0x2F,0x4E,0x5C,0x76,7,0xC7,0x46,0x5C}); c.word(0x3FC00000);
    if (c.bytes.size() != 0x3D2BF2 - t7::MOVEMENT_ANIMATION_RVA) throw std::runtime_error("animation fixture writer offset");
    c.emit({0x8B,0x4E,0x58,0xF3,0x0F,0x10,0x46,0x5C,0x8B,0x01,0x51,0xF3,0x0F,0x11,0x04,0x24,0xFF,0x35});
    c.word(BASE + 0x2D37918); c.emit({0xFF,0x50,0x54,0x5F,0x5E,0x5B,0x8B,0xE5,0x5D,0xC3});
    t7::writeClientMemory(process, BASE + t7::MOVEMENT_ANIMATION_RVA, c.bytes);
}
inline void verify(HANDLE process, uint32_t mailbox) {
    using namespace sprintFixture;
    using worldFixture::MOVABLE;
    const auto config = profile();
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    uint32_t tick = 10000;
    writeWord(process, CLOCK_MODE, 1); writeWord(process, DATA, MOVABLE);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, FIGHT + 0x98, static_cast<uint32_t>(config.weaponId));
    const auto eventCount = readWord(process, ANIMATION_EVENTS);
    auto frame = [&](float velocity, float physical, float animation) {
        writeFloat(process, VELOCITY, velocity); writeWord(process, CLOCK, tick);
        writeWord(process, worldFixture::FRAME_GATE, 2);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            if (GetTickCount64() > deadline) throw std::runtime_error("animation frame timeout");
            Sleep(1);
        }
        const auto actual = readFloat(process, ANIMATION + 0x34);
        if (readFloat(process, worldFixture::CONTROLLER + 0x24) != physical
            || std::abs(actual - animation) > .00001f || readFloat(process, MOVABLE + 0x5C) != actual
            || readWord(process, VARIABLE) != 123)
            throw std::runtime_error("animation gait/cache/physical speed mismatch: expected="
                + std::to_string(animation) + " actual=" + std::to_string(actual));
    };
    for (uint32_t keys : {1u, 5u, 9u}) {
        writeWord(process, KEYS, keys);
        const auto slow = keys == 1 ? config.forwardSlow : config.lateralSlow;
        for (const auto& block : std::array<std::pair<uint32_t, uint32_t>, 4>{{{305,223},{308,224},{307,225},{306,226}}}) {
            setFightState(process, block.first, tick); frame(5, 3, slow);
            setFightState(process, block.second, tick); frame(3, 3, slow);
            const auto writes = readWord(process, WRITES);
            setFightState(process, 2, tick); frame(3, 3, slow);
            if (readWord(process, WRITES) != writes) throw std::runtime_error("unchanged mapped animation was written again");
            if (readWord(process, mailbox + t7::SPRINT_DATA_OFFSET + 12) != tick)
                throw std::runtime_error("animation change altered defense run-timer reset");
            tick += 999; frame(3, 3, slow);
            ++tick; frame(3, 5, slow);
            frame(4, 5, (slow + config.forwardSprint) * .5f);
            frame(5, 5, config.forwardSprint);
        }
    }
    for (uint32_t keys : {2u, 6u, 10u, 4u, 8u}) {
        writeWord(process, KEYS, keys);
        const auto slow = keys & 2 ? config.backwardSlow : config.lateralSlow;
        frame(3, 3, slow); frame(1.5f, 3, slow * .5f);
        frame(0, 3, slow * .5f); // Preserve the native active-controller floor before remapping.
        writeWord(process, ACTIVE, 0); frame(.75f, 3, slow * .25f); writeWord(process, ACTIVE, 1);
    }
    writeWord(process, KEYS, 0); frame(0, 3, 1.5f); // Stationary graph is untouched.
    writeWord(process, KEYS, 17); frame(3, 3, 3); // Crouch is untouched.
    writeWord(process, KEYS, 1); setFightState(process, 237); frame(3, 3, 3);
    setFightState(process, 439, tick); frame(3, static_cast<float>(sprintFixture::profile().speedMilli) / 1000.f, 3);
    setFightState(process, 2, tick); frame(3, 3, config.forwardSlow);
    writeWord(process, MOVABLE + 0x20, 1); frame(3, 3, 3); writeWord(process, MOVABLE + 0x20, 0);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY + 0x100); frame(3, 3, 3);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, FIGHT + 0x98, 100000); frame(3, 3, 3);
    writeWord(process, FIGHT + 0x98, static_cast<uint32_t>(config.weaponId)); frame(3, 3, config.forwardSlow);
    if (readWord(process, ANIMATION_EVENTS) != eventCount) throw std::runtime_error("gait mapping sent an animation event");
    writeWord(process, DATA, 0); writeWord(process, KEYS, 0); writeWord(process, CLOCK_MODE, 0);
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
