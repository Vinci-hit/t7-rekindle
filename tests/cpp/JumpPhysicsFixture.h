#pragma once
#include "SprintHookFixture.h"

namespace jumpPhysicsFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xB0000, DISPATCH = DATA + 0x100, APPLY = DATA + 0x300;
constexpr uint32_t COMMAND = DATA + 0x500, CHILDREN = DATA + 0x540, DIRECTION = DATA + 0x580;
constexpr uint32_t REQUEST = DATA, STEPS = DATA + 4, VELOCITY = DATA + 8, HEADING = DATA + 12;
constexpr uint32_t STEP = DATA + 0x600, GRAVITY = DATA + 16, ACTIVE = DATA + 0x700;
constexpr uint32_t FLUSH_RVA = 0x3D0AAE, FORCE_STEP_RVA = 0x29B200;
inline void prepare(HANDLE process) {
    t7::WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24});
    c.emit({0xA1}); c.word(REQUEST); c.emit({0x85,0xC0}); const auto idle = c.branch(0x84);
    c.emit({0x83,0xF8,2}); const auto regular = c.branch(0x84);
    c.emit({0x68}); c.word(COMMAND); c.emit({0xB9}); c.word(worldFixture::MOVABLE);
    c.call(APPLY); const auto done = c.jump();
    c.bind(regular); c.emit({0xB9}); c.word(worldFixture::CONTROLLER);
    c.emit({0x68}); c.word(0x3DCCCCCD); c.call(STEP);
    c.bind(done); c.store(REQUEST, 0); c.bind(idle);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    t7::writeClientMemory(process, DISPATCH, c.bytes);

    // The command already changed direction before the unconditional native flush.
    c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x56,0x57,0x8B,0xF1,0x8B,0x7D,8});
    c.emit({0xA1}); c.word(HEADING); c.emit({0x8B,0x4E,0x38,0x89,0x81}); c.word(0xAC);
    c.emit({0x8B,0x01,0xE9});
    c.word(BASE + FLUSH_RVA - (APPLY + static_cast<uint32_t>(c.bytes.size()) + 4));
    t7::writeClientMemory(process, APPLY, c.bytes);
    t7::writeClientMemory(process, BASE + FLUSH_RVA,
        {0xFF,0x90,0xD0,0,0,0,0x5F,0x5E,0x5D,0xC2,4,0});

    // Native force-update advances a full controller interval, even at zero elapsed time.
    c.bytes.clear(); c.emit({0x56,0x8B,0xF1,0x8B,0x06,0x8B,0x40,0x2C,0xFF,0xD0});
    c.emit({0x84,0xC0,0x74,0x4B,0x80,0xBE,0xB4,0,0,0,0,0x74,0x42});
    c.emit({0x66,0x0F,0x6E,0x86,0xEC,0,0,0,0x8B,0x06,0x0F,0x5B,0xC0,0x51});
    c.emit({0xF3,0x0F,0x59,0x05}); c.word(BASE + 0x1A9E2B8);
    c.emit({0x8B,0xCE,0xF3,0x0F,0x11,0x04,0x24,0xFF,0x90,0xA0,0,0,0,0x8B,0x06});
    c.emit({0xC7,0x86,0xE8,0,0,0,0,0,0,0,0xC7,0x86,0xF0,0,0,0,0,0,0,0});
    c.emit({0x8B,0xCE,0x5E,0xFF,0xA0,0xA8,0,0,0,0x5E,0xC3});
    t7::writeClientMemory(process, BASE + FORCE_STEP_RVA, c.bytes);
    writeWord(process, BASE + 0x1A9E2B8, 0x3A83126F); // 0.001 seconds per millisecond.
    writeWord(process, GRAVITY, 0x41200000);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(STEPS);
    c.emit({0xF3,0x0F,0x10,0x44,0x24,4,0xF3,0x0F,0x59,0x05}); c.word(GRAVITY);
    c.emit({0xF3,0x0F,0x10,0x0D}); c.word(VELOCITY);
    c.emit({0xF3,0x0F,0x5C,0xC8,0xF3,0x0F,0x11,0x0D}); c.word(VELOCITY); c.emit({0xC2,4,0});
    t7::writeClientMemory(process, STEP, c.bytes);
    t7::writeClientMemory(process, ACTIVE, {0xB0,1,0xC3});
    t7::writeClientMemory(process, ACTIVE + 16, {0xC3});
}
inline void verify(HANDLE process) {
    using worldFixture::MOVABLE;
    using worldFixture::CONTROLLER;
    using worldFixture::TABLE;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    const auto active = readWord(process, TABLE + 0x2C);
    const auto owner = readWord(process, MOVABLE + 8);
    const auto heading = readWord(process, CONTROLLER + 0xAC);
    const auto flags = readWord(process, CONTROLLER + 8);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, MOVABLE + 0x20, 0x00010001);
    writeWord(process, TABLE + 0x2C, ACTIVE);
    writeWord(process, TABLE + 0xA0, STEP);
    writeWord(process, TABLE + 0xA8, ACTIVE + 16);
    writeWord(process, TABLE + 0xD0, BASE + FORCE_STEP_RVA);
    writeWord(process, CONTROLLER + 8, 1);
    writeWord(process, CONTROLLER + 0xB4, 1);
    writeWord(process, CONTROLLER + 0xEC, 100);
    writeWord(process, COMMAND, BASE + 0x17782A4);
    writeWord(process, COMMAND + 0x10, CHILDREN);
    writeWord(process, COMMAND + 0x14, CHILDREN + 4);
    writeWord(process, CHILDREN, DIRECTION);
    writeWord(process, DIRECTION, BASE + 0x17782C8);
    auto invoke = [&](uint32_t request) {
        writeWord(process, REQUEST, request);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, REQUEST)) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("jump physics command timeout");
            Sleep(1);
        }
    };
    auto expect = [&](bool extraStep) {
        writeWord(process, STEPS, 0); writeWord(process, VELOCITY, 0x403504F3); // 2.828427 m/s.
        writeWord(process, CONTROLLER + 0xE8, 7); writeWord(process, CONTROLLER + 0xF0, 35);
        writeWord(process, HEADING, 0x3F800000);
        invoke(1);
        if (readWord(process, STEPS) != static_cast<uint32_t>(extraStep)
            || readWord(process, CONTROLLER + 0xAC) != 0x3F800000
            || readWord(process, CONTROLLER + 0xE8) != (extraStep ? 0u : 7u)
            || readWord(process, CONTROLLER + 0xF0) != (extraStep ? 0u : 35u)
            || readWord(process, VELOCITY) != (extraStep ? 0x3FEA09E6u : 0x403504F3u))
            throw std::runtime_error("airborne heading consumed an extra physics step or lost native command state");
    };
    expect(false);
    // Repeated attack-direction updates must not shorten ascent; real frame steps still apply gravity.
    for (uint32_t state : {241u,243u,244u,245u,2u}) {
        sprintFixture::setFightState(process, state);
        expect(false); invoke(1); invoke(1);
        if (readWord(process, STEPS)) throw std::runtime_error("repeated heading advanced gravity");
        invoke(2);
        if (readWord(process, STEPS) != 1 || readWord(process, VELOCITY) != 0x3FEA09E6)
            throw std::runtime_error("airborne heading disabled the regular physics step");
    }
    for (uint32_t jumpFlags : {0u,1u,0x10000u}) {
        writeWord(process, MOVABLE + 0x20, jumpFlags); expect(true);
    }
    writeWord(process, MOVABLE + 0x20, 0x00010001);
    writeWord(process, worldFixture::STATE + 0x38, 2); expect(true);
    writeWord(process, worldFixture::STATE + 0x38, 4);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY + 0x100); expect(true);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    writeWord(process, CONTROLLER + 8, 0x10001); expect(true); // A queued new jump retains its initial step.
    writeWord(process, CONTROLLER + 8, 1);
    for (uint32_t commandType : {0x17782B8u,0x17782D8u,0x17782E8u,0x1778318u}) {
        writeWord(process, DIRECTION, BASE + commandType); expect(true);
    }
    writeWord(process, DIRECTION, BASE + 0x17782C8);
    writeWord(process, COMMAND + 0x14, CHILDREN + 8); expect(true); // Position + direction corrections stay native.
    writeWord(process, COMMAND + 0x14, CHILDREN); expect(true);
    writeWord(process, COMMAND + 0x14, CHILDREN + 4);
    writeWord(process, CHILDREN, 0); expect(true); writeWord(process, CHILDREN, DIRECTION);
    writeWord(process, COMMAND, BASE + 0x1778294); expect(true);
    writeWord(process, COMMAND, BASE + 0x17782A4);
    writeWord(process, TABLE + 0x2C, active); writeWord(process, MOVABLE + 8, owner);
    writeWord(process, CONTROLLER + 8, flags); writeWord(process, CONTROLLER + 0xAC, heading);
    writeWord(process, MOVABLE + 0x20, 0); writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
