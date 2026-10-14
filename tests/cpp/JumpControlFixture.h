#pragma once
#include "MovementCommandFixture.h"

namespace jumpControlFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xE0000, DISPATCH = DATA + 0x100, CHILDREN = DATA + 0x200;
constexpr uint32_t GROUP = DATA + 0x240, HEADING = DATA + 0x280, MOVE = DATA + 0x2C0;
constexpr uint32_t JUMP = DATA + 0x300, POSITION = DATA + 0x340;
constexpr uint32_t REQUEST = DATA, JUMPS = DATA + 4, CROUCHES = DATA + 8, CORRECTIONS = DATA + 12;
constexpr uint32_t COMPOSITE_RVA = 0x3CFBF0, COMMAND_CALL_RVA = 0x3CFC09;
inline void prepare(HANDLE process) {
    t7r::WorldHookCode c;
    c.emit({0x9C,0x60,0x83,0x3D}); c.word(REQUEST); c.emit({0}); const auto idle = c.branch(0x84);
    c.emit({0x68}); c.word(worldFixture::MOVABLE); c.emit({0xB9}); c.word(GROUP);
    c.call(BASE + COMPOSITE_RVA); c.store(REQUEST, 0); c.bind(idle); c.emit({0x61,0x9D,0xC3});
    t7r::writeClientMemory(process, DISPATCH, c.bytes);
    // Native composite traversal calls each child separately, including mixed corrections.
    t7r::writeClientMemory(process, BASE + COMPOSITE_RVA,
        {0x55,0x8B,0xEC,0x56,0x8B,0x71,0x10,0x57,0x8B,0x79,0x14,0x3B,0xF7,0x74,0x18,
         0x53,0x8B,0x5D,8,0x8B,0x0E,0x85,0xC9,0x74,6,0x8B,1,0x53,0xFF,0x50,8,
         0x83,0xC6,4,0x3B,0xF7,0x75,0xED,0x5B,0x5F,0x5E,0x5D,0xC2,4,0});
    c.bytes = {0x8B,0x44,0x24,4,0x8B,0x40,0x38,0x8B,0x51,8,0x89,0x90}; c.word(0xAC);
    c.emit({0xC2,4,0}); t7r::writeClientMemory(process, DATA + 0x400, c.bytes);
    c.bytes = {0x83,0x79,0x0C,1}; const auto noCrouch = c.branch(0x87);
    c.emit({0xFF,0x05}); c.word(CROUCHES); c.bind(noCrouch);
    c.emit({0x80,0x79,8,0}); const auto noJump = c.branch(0x84);
    c.emit({0xFF,0x05}); c.word(JUMPS);
    c.emit({0x8B,0x44,0x24,4,0x8B,0x40,0x38,0xC6,0x40,0x0A,1});
    c.bind(noJump); c.emit({0xC2,4,0}); t7r::writeClientMemory(process, DATA + 0x440, c.bytes);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(CORRECTIONS); c.emit({0xC2,4,0});
    t7r::writeClientMemory(process, DATA + 0x4C0, c.bytes);
    for (const auto& entry : std::array<std::pair<uint32_t, uint32_t>, 4>{{
        {HEADING,0x17782C8}, {MOVE,0x17782D8}, {JUMP,0x17782E8}, {POSITION,0x17782B8}
    }}) writeWord(process, entry.first, BASE + entry.second);
    writeWord(process, BASE + 0x17782C8 + 8, DATA + 0x400);
    writeWord(process, BASE + 0x17782D8 + 8, BASE + movementCommandFixture::APPLY_RVA);
    writeWord(process, BASE + 0x17782E8 + 8, DATA + 0x440);
    writeWord(process, BASE + 0x17782B8 + 8, DATA + 0x4C0);
    writeWord(process, GROUP + 0x10, CHILDREN);
}
inline void verify(HANDLE process) {
    using worldFixture::MOVABLE;
    using worldFixture::CONTROLLER;
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    const auto owner = readWord(process, MOVABLE + 8), flags = readWord(process, CONTROLLER + 8);
    const auto heading = readWord(process, CONTROLLER + 0xAC);
    writeWord(process, MOVABLE + 8, worldFixture::ENTITY);
    auto invoke = [&](std::initializer_list<uint32_t> commands) {
        uint32_t end = CHILDREN;
        for (auto command : commands) { writeWord(process, end, command); end += 4; }
        writeWord(process, GROUP + 0x14, end); writeWord(process, REQUEST, 1);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, REQUEST)) {
            if (GetTickCount64() >= deadline) throw std::runtime_error("jump control command timeout");
            Sleep(1);
        }
    };
    auto setup = [&](uint32_t physicalFlags, bool pending = false) {
        writeWord(process, MOVABLE + 0x20, physicalFlags);
        writeWord(process, CONTROLLER + 8, pending ? 0x10001 : 1);
        writeWord(process, CONTROLLER + 0xAC, 0x3E800000);
        writeWord(process, CONTROLLER + 0x24, 0x40400000);
        writeWord(process, MOVABLE + 0x2C, 2);
        writeWord(process, HEADING + 8, 0x3F800000);
        writeWord(process, MOVE + 0x0C, 0x40800000); writeWord(process, MOVE + 0x30, 9);
        writeWord(process, JUMP + 8, 1); writeWord(process, JUMP + 0x0C, 2);
        writeWord(process, JUMPS, 0); writeWord(process, CROUCHES, 0);
    };
    auto expect = [&](bool changedDirection, uint32_t jumps) {
        if (readWord(process, CONTROLLER + 0xAC) != (changedDirection ? 0x3F800000u : 0x3E800000u)
            || readWord(process, CONTROLLER + 0x24) != (changedDirection ? 0x40800000u : 0x40400000u)
            || readWord(process, MOVABLE + 0x2C) != (changedDirection ? 9u : 2u)
            || readWord(process, JUMPS) != jumps || readWord(process, JUMP + 8) != 1)
            throw std::runtime_error("jump allowed direction changes/repeated takeoff or changed the queued input");
    };
    setup(0x10001); invoke({HEADING,MOVE,JUMP}); expect(false, 0);
    for (uint32_t state : {241u,243u,244u,245u,2u}) {
        sprintFixture::setFightState(process, state);
        invoke({HEADING,MOVE,JUMP}); expect(false, 0);
    }
    writeWord(process, JUMP + 8, 0); invoke({JUMP});
    writeWord(process, JUMP + 8, 1); invoke({JUMP}); expect(false, 0);
    writeWord(process, JUMP + 0x0C, 1); const auto corrections = readWord(process, CORRECTIONS);
    invoke({POSITION,HEADING,MOVE,JUMP}); expect(false, 0);
    if (readWord(process, CROUCHES) != 1 || readWord(process, CORRECTIONS) != corrections + 1)
        throw std::runtime_error("jump restrictions discarded crouch or position correction");
    setup(0); invoke({HEADING,MOVE,JUMP,JUMP}); expect(true, 1);
    setup(0, true); invoke({HEADING,MOVE,JUMP}); expect(false, 0); // Same-frame repeated takeoff.
    setup(0x10001); invoke({HEADING,MOVE,JUMP}); expect(false, 0);
    setup(0); invoke({HEADING,MOVE,JUMP}); expect(true, 1); // Landing restores all controls.
    setup(1); invoke({HEADING,MOVE,JUMP}); expect(true, 0); // Falling is not a new jump.
    setup(0x10000); invoke({HEADING,MOVE,JUMP}); expect(true, 1);
    setup(0x10001); writeWord(process, MOVE + 0x34, 1); invoke({MOVE});
    if (readWord(process, MOVABLE + 0x2C) != 9 || readWord(process, CONTROLLER + 0x24) != 0x40800000)
        throw std::runtime_error("jump restriction discarded state-directed movement");
    writeWord(process, MOVE + 0x34, 0);
    setup(0x10001); writeWord(process, worldFixture::STATE + 0x38, 2);
    invoke({HEADING,MOVE,JUMP}); expect(true, 1); writeWord(process, worldFixture::STATE + 0x38, 4);
    setup(0x10001); writeWord(process, MOVABLE + 8, worldFixture::ENTITY + 0x100);
    invoke({HEADING,MOVE,JUMP}); expect(true, 1);
    setup(0); writeWord(process, MOVABLE + 8, owner); writeWord(process, CONTROLLER + 8, flags);
    writeWord(process, CONTROLLER + 0xAC, heading);
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
