#pragma once
#include "WorldHookFixture.h"
#include "../../src/Runtime/launcher/SprintHookCode.h"
#include "../../src/Runtime/launcher/WorldHookCode.h"

namespace sprintFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
constexpr uint32_t INPUT = BASE + 0xC000, FIGHT = BASE + 0xC100, OFFLINE = BASE + 0xC200;
constexpr uint32_t KEYS = BASE + 0xC300, CLOCK = KEYS + 4, FRAMES = KEYS + 8;
constexpr uint32_t EVENTS = KEYS + 12, DIRECTION = KEYS + 16, MOVES = KEYS + 20;
constexpr uint32_t RESET_SPEED = KEYS + 24, APPLIED_RESET = KEYS + 28;
inline uint32_t readWord(HANDLE process, uint32_t address) {
    const auto bytes = t7::readClientMemory(process, address, 4);
    uint32_t value; std::memcpy(&value, bytes.data(), 4); return value;
}
inline void prepare(HANDLE process) {
    t7::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x8B,0x55,8,0x56,0x8B,0x71,0x7C});
    std::vector<size_t> done;
    for (const auto& item : std::vector<std::pair<uint32_t, uint32_t>>{
        {0x178692C, INPUT}, {0x1783860, FIGHT}, {0x17ADDFC, OFFLINE}
    }) {
        c.emit({0x81,0xFA}); c.word(BASE + item.first); const auto next = c.branch(0x85);
        c.emit({0xB8}); c.word(item.second); done.push_back(c.jump()); c.bind(next);
    }
    c.emit({0x8B,0x81}); c.word(0x80);
    for (auto branch : done) c.bind(branch);
    c.emit({0x5E,0x5D,0xC2,4,0});
    t7::writeClientMemory(process, BASE + 0x26A1C0, c.bytes);
    for (const auto& item : std::vector<std::pair<uint32_t, uint32_t>>{
        {INPUT,BASE+0x17868BC}, {INPUT+0x60,6}, {INPUT+0x54,KEYS},
        {FIGHT,BASE+0x178380C}, {FIGHT+0x38,2}, {CLOCK,1000},
        {worldFixture::TABLE+0x3C,BASE+0x295FA0}, {BASE+0x16F6228,BASE+0xC400}
    }) writeWord(process, item.first, item.second);
    c.bytes.clear(); c.emit({0xFF,0x05}); c.word(FRAMES);
    c.emit({0xA1}); c.word(RESET_SPEED); c.emit({0x3B,0x05}); c.word(APPLIED_RESET);
    const auto unchanged = c.branch(0x84);
    c.emit({0xA3}); c.word(APPLIED_RESET); c.store(worldFixture::CONTROLLER + 0x24, 0x40400000);
    c.bind(unchanged); c.emit({0xA1}); c.word(CLOCK); c.emit({0xC3});
    t7::writeClientMemory(process, BASE + 0xC400, c.bytes);
    t7::writeClientMemory(process, BASE + 0x4C6E70,
        {0x55,0x8B,0xEC,0x83,0xEC,0x10,0x8B,0xC1,0x8B,0x4D,8});
    t7::writeClientMemory(process, BASE + 0x4971B0, {0x8B,0x01,0xC3});
    t7::writeClientMemory(process, BASE + 0x3D2550,
        {0x55,0x8B,0xEC,0x8B,0x49,0x38,0xF3,0x0F,0x10,0x45,8,
         0xF3,0x0F,0x11,0x41,0x24,0x5D,0xC2,4,0});
    auto begin = [&]() { c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x6A,0xFF,0x68}); c.word(0); c.emit({0x58,0x58}); };
    begin(); c.emit({0x8B,0x45,8,0x88,0x81}); c.word(0xCC);
    c.emit({0xFF,0x05}); c.word(EVENTS); c.emit({0x5D,0xC2,4,0});
    t7::writeClientMemory(process, BASE + 0x3D2E20, c.bytes);
    begin(); c.emit({0x8B,0x45,0x14,0x8B,0x00,0xA3}); c.word(DIRECTION);
    c.emit({0xFF,0x05}); c.word(MOVES); c.emit({0x5D,0xC2,16,0});
    t7::writeClientMemory(process, BASE + 0x75D140, c.bytes);
    begin(); c.store(DIRECTION, 0); c.emit({0xFF,0x05}); c.word(MOVES); c.emit({0x5D,0xC3});
    t7::writeClientMemory(process, BASE + 0x75D080, c.bytes);
}
inline void pump(HANDLE process) {
    const auto before = readWord(process, FRAMES);
    const auto deadline = GetTickCount64() + 3000;
    while (readWord(process, FRAMES) - before < 10) {
        if (GetTickCount64() >= deadline) throw std::runtime_error("sprint frame timeout");
        Sleep(1);
    }
}
inline void expect(HANDLE process, float speed, bool running, const char* reason) {
    pump(process);
    uint32_t bits; std::memcpy(&bits, &speed, 4);
    if (readWord(process, worldFixture::CONTROLLER + 0x24) != bits
        || (readWord(process, worldFixture::MOVABLE + 0xCC) & 0xFF) != static_cast<uint32_t>(running))
        throw std::runtime_error(reason);
}
inline void verify(HANDLE process) {
    uint32_t tick = 1000;
    auto time = [&](uint32_t delta) { tick += delta; writeWord(process, CLOCK, tick); pump(process); };
    writeWord(process, KEYS, 1); expect(process, 3, false, "jog speed");
    time(1999); expect(process, 3, false, "sprint armed too early");
    time(1); expect(process, 5, true, "run speed/HUD did not arm");
    const auto events = readWord(process, EVENTS); time(100);
    if (readWord(process, EVENTS) != events) throw std::runtime_error("duplicate running events");
    writeWord(process, KEYS, 9); expect(process, 5, true, "diagonal movement reset running");
    // A new keyboard command can reset velocity; the next frame restores the selected speed.
    writeWord(process, RESET_SPEED, 1);
    expect(process, 5, true, "native key command lost run velocity");
    for (const auto& chain : std::vector<std::vector<uint32_t>>{
        {235,237,238,239}, {241,243,244,245}, {247,249,250,251}, {253,255,256,257},
        {305,223,231,223,227}, {308,224,232,224,228}, {307,225,233,225,229}, {306,226,234,226,230}
    }) {
        for (auto state : chain) {
            writeWord(process, FIGHT + 0x38, state);
            expect(process, 3, false, "attack/parry retained run speed or HUD");
            writeWord(process, KEYS, 1); time(2000);
            expect(process, 3, false, "held movement armed running during attack/parry");
            writeWord(process, KEYS, 9);
            expect(process, 3, false, "direction change accelerated attack/parry");
        }
        writeWord(process, FIGHT + 0x38, 2); pump(process); time(1999);
        expect(process, 3, false, "attack/parry kept elapsed run time");
        time(1); expect(process, 5, true, "attack/parry recovery did not rearm running");
    }
    writeWord(process, FIGHT + 0x38, 438); expect(process, 0, false, "dash preparation did not consume running");
    writeWord(process, FIGHT + 0x38, 439); expect(process, 7, false, "dash strike speed");
    if (readWord(process, DIRECTION) != 0x7075) throw std::runtime_error("dash did not move forward");
    writeWord(process, KEYS, 0); expect(process, 7, false, "key release interrupted committed dash");
    if (readWord(process, DIRECTION) != 0x7075) throw std::runtime_error("dash key-up lost direction");
    writeWord(process, FIGHT + 0x38, 440); expect(process, 0, false, "dash recovery did not stop");
    writeWord(process, FIGHT + 0x38, 2); expect(process, 3, false, "dash did not return to jog");
    if (readWord(process, DIRECTION)) throw std::runtime_error("released keys resumed motion");
    for (uint32_t keys : {0u, 2u, 4u, 8u, 3u, 17u, 33u}) {
        writeWord(process, KEYS, keys); pump(process); time(2000);
        expect(process, 3, false, "stop/backward/strafe/crouch/jump armed running");
    }
    writeWord(process, KEYS, 1); pump(process); time(2000); expect(process, 5, true, "run rearm");
    writeWord(process, FIGHT + 0x38, 35); expect(process, 3, false, "hurt kept sprint ready");
    writeWord(process, FIGHT + 0x38, 2); pump(process); time(1999); expect(process, 3, false, "hurt kept elapsed run time");
    time(1); expect(process, 5, true, "hurt recovery rearm");
    writeWord(process, worldFixture::STATE + 0x38, 0); expect(process, 3, false, "leaving kept sprint ready");
    writeWord(process, worldFixture::STATE + 0x38, 4); pump(process); time(2000);
    writeWord(process, worldFixture::INSTANCE + 0x6C, 2); expect(process, 3, false, "epoch reused sprint readiness");
    time(2000); expect(process, 5, true, "epoch recovery");
    // Tick subtraction remains valid across GetTickCount wraparound.
    writeWord(process, KEYS, 0); pump(process); tick = 0xFFFFFF00; time(0);
    writeWord(process, KEYS, 1); pump(process); time(2000); expect(process, 5, true, "clock wrap broke run timer");
    writeWord(process, FIGHT + 0x38, 439); pump(process);
    writeWord(process, KEYS, 4); pump(process);
    writeWord(process, FIGHT + 0x38, 35); expect(process, 3, false, "interrupted dash speed");
    if (readWord(process, DIRECTION) != 0x7466656C) throw std::runtime_error("dash interruption did not restore held direction");
    writeWord(process, KEYS, 0); writeWord(process, FIGHT + 0x38, 2);
    writeWord(process, worldFixture::INSTANCE + 0x6C, 1); pump(process);
}
}
