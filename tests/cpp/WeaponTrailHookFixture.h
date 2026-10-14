#pragma once
#include "BladeHookFixture.h"
#include "SprintHookFixture.h"
#include "../../src/Runtime/launcher/ClientResourceXml.h"

namespace weaponTrailFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t DATA = BASE + 0xA0000, FIGHT = DATA + 0x100, HEAD = DATA + 0x200;
constexpr uint32_t MODE = DATA, OPENS = DATA + 4, CLOSES = DATA + 8, TEXTURES = DATA + 12;
constexpr uint32_t LOD = DATA + 16;
inline uint32_t node(uint32_t i) { return DATA + 0x300 + i * 0x40; }
inline uint32_t weapon(uint32_t i) { return DATA + 0x1000 + i * 0x1000; }
inline uint32_t swoosh(uint32_t i) { return weapon(i) + 0x200; }
inline void prepare(HANDLE process) {
    writeWord(process, FIGHT, BASE + 0x178380C);
    writeWord(process, FIGHT + 0x38, 2); writeWord(process, FIGHT + 0x98, 101);
    writeWord(process, FIGHT + 0x9C, HEAD); writeWord(process, HEAD, node(0));
    writeWord(process, HEAD + 4, node(0)); writeWord(process, HEAD + 8, node(1));
    writeWord(process, HEAD + 12, 0x100);
    for (uint32_t i = 0; i < 2; ++i) {
        for (const auto& field : std::vector<std::array<uint32_t, 2>>{
            {node(i),HEAD}, {node(i)+4,i ? node(0) : HEAD}, {node(i)+8,i ? HEAD : node(1)},
            {node(i)+16,101+i}, {node(i)+20,weapon(i)},
            {weapon(i),BASE+0x179E16C}, {weapon(i)+8,weapon(i)+0x100},
            {weapon(i)+0x17C,weapon(i)}, {weapon(i)+0x190,swoosh(i)},
            {swoosh(i),BASE+0x1773684}, {swoosh(i)+0xB0,weapon(i)+0x400},
            {swoosh(i)+0xB4,weapon(i)+0x500}, {swoosh(i)+0x34,15}, {swoosh(i)+0x98,1}
        }) writeWord(process, field[0], field[1]);
    }
    writeWord(process, BASE + 0x2DEA690, DATA);
    writeWord(process, BASE + 0x2440C44, DATA);
    t7r::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x6A,0xFF,0x58,0xA1}); c.word(MODE); c.emit({0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + 0x6EC2D0, c.bytes);
    c.bytes.clear(); c.emit({0x56,0x8B,0xF1,0x8B,0x0D}); c.word(BASE + 0x2DEA690);
    c.emit({0x83,0x3D}); c.word(LOD); c.emit({2,0x0F,0x97,0x46,0x1D});
    c.emit({0xC6,0x46,0x1C,1,0xFF,0x05}); c.word(OPENS);
    c.emit({0x5E,0xC3}); t7r::writeClientMemory(process, BASE + 0x3735A0, c.bytes);
    c.bytes.clear(); c.emit({0x56,0x8B,0xF1,0x80,0x7E,0x1C,1,0xC6,0x46,0x1C,0,0xFF,0x05}); c.word(CLOSES);
    c.emit({0x5E,0xC3}); t7r::writeClientMemory(process, BASE + 0x373630, c.bytes);
    c.bytes.clear(); c.emit({0x55,0x8B,0xEC,0x83,0xC1,0x20,0x56,0x57,0x8B,0xF9,0x8B,0x75,8,0xB9}); c.word(6);
    c.emit({0xF3,0xA5});
    // Native loading selects a texture group; the blue preset's group 1 has empty textures.
    c.emit({0x33,0xC0,0x83,0x7F,0x60,0,0x0F,0x94,0xC0,0x6B,0xD0,0x78});
    for (auto offset : std::array<unsigned char, 3>{0,24,48}) {
        c.emit({0x89,0x57,offset,0x89,0x47,static_cast<unsigned char>(offset+16),
                0xC7,0x47,static_cast<unsigned char>(offset+20)}); c.word(15);
    }
    c.emit({0xFF,0x05}); c.word(TEXTURES); c.emit({0x5F,0x5E,0x5D,0xC2,4,0});
    t7r::writeClientMemory(process, BASE + 0x373A60, c.bytes);
    for (const auto& method : std::array<std::array<uint32_t, 2>, 3>{{{0x1C,0x3735A0},{0x20,0x373630},{0x28,0x373A60}}})
        writeWord(process, BASE + 0x1773684 + method[0], BASE + method[1]);
}
inline void verify(HANDLE process) {
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    auto active = [&](uint32_t i) { return (readWord(process, swoosh(i) + 0x1C) & 0xFF) != 0; };
    auto textured = [&](uint32_t i) {
        return readWord(process, swoosh(i) + 0x48) == 1 && readWord(process, swoosh(i) + 0x60) == 1
            && readWord(process, swoosh(i) + 0x78) == 1;
    };
    auto expect = [&](bool first, bool second, const char* message) {
        sprintFixture::pump(process); require(active(0) == first && active(1) == second, message);
    };
    writeWord(process, worldFixture::ENTITY + 0x8C, FIGHT);
    writeWord(process, worldFixture::ENTITY + 0x100 + 0x8C, bladeFixture::object(1) + 0x700);
    writeWord(process, bladeFixture::object(1) + 0x738, 238);
    writeWord(process, bladeFixture::object(1) + 0xB1C, 1);
    writeWord(process, bladeFixture::object(1) + 0xB98, 1);
    expect(false, false, "idle weapon emitted a trail");
    require(readWord(process, swoosh(0) + 0x98) == 1, "idle weapon changed texture group");
    writeWord(process, FIGHT + 0x38, 238);
    expect(true, false, "player strike did not open the native trail");
    const auto blue = t7r::resourceXml::encoded(L"PVP赵云专用");
    const auto actual = t7r::readClientMemory(process, swoosh(0) + 0x20, blue.size());
    require(std::string(actual.begin(), actual.end()) == blue, "player trail did not use the native blue preset");
    require(readWord(process, swoosh(0) + 0x98) == 0 && textured(0), "player trail kept the empty texture group");
    require(readWord(process, swoosh(1) + 0x98) == 1, "sheathed weapon changed texture group");
    require(readWord(process, TEXTURES) == 1, "blue preset was reapplied every frame");
    const auto opened = readWord(process, OPENS);
    expect(true, false, "native trail stopped during a strike");
    require(readWord(process, OPENS) == opened, "native trail was restarted every frame");
    require(readWord(process, TEXTURES) == 1, "configured trail reloaded textures every frame");
    const auto closed = readWord(process, CLOSES);
    for (auto offset : {0x48u,0x60u,0x78u}) writeWord(process, swoosh(0) + offset, 0);
    writeWord(process, swoosh(0) + 0x98, 1);
    expect(true, false, "same-preset texture group change stopped the trail");
    require(readWord(process, swoosh(0) + 0x98) == 0 && textured(0), "same-preset empty textures were not reloaded");
    require(readWord(process, CLOSES) == closed + 1 && readWord(process, OPENS) == opened + 1
        && readWord(process, TEXTURES) == 2, "active trail was not closed, reconfigured and reopened once");
    expect(true, false, "reconfigured trail did not stay active");
    require(readWord(process, OPENS) == opened + 1 && readWord(process, TEXTURES) == 2,
        "reconfigured trail kept restarting or reloading textures");
    writeWord(process, MODE, 2); expect(false, false, "disabled Swoosh setting kept the trail visible");
    writeWord(process, MODE, 1); expect(true, false, "self-only Swoosh setting hid the player trail");
    writeWord(process, MODE, 0); expect(true, false, "all Swoosh setting hid the player trail");
    require(readWord(process, bladeFixture::object(1) + 0xB1C) == 1
        && readWord(process, bladeFixture::object(1) + 0xB30) == 0
        && readWord(process, bladeFixture::object(1) + 0xB98) == 1, "player trail changed another actor's native effects");
    writeWord(process, MODE, 2); expect(false, false, "LOD setup failed to close trail");
    writeWord(process, LOD, 3); writeWord(process, MODE, 1);
    expect(true, false, "native LOD activation was skipped");
    require((readWord(process, swoosh(0) + 0x1C) & 0xFF00) == 0x100, "trail hook overrode native LOD visibility");
    writeWord(process, LOD, 0);
    for (uint32_t state : {238u,244u,250u,256u,439u}) {
        writeWord(process, FIGHT + 0x38, 2); expect(false, false, "idle did not close the trail");
        writeWord(process, FIGHT + 0x38, state); expect(true, false, "strike direction missing its trail");
    }
    for (uint32_t state : {235u,237u,239u,240u,305u,223u,35u,466u,438u}) {
        writeWord(process, FIGHT + 0x38, state); expect(false, false, "non-strike phase emitted a trail");
    }
    writeWord(process, FIGHT + 0x38, 238); expect(true, false, "strike did not resume");
    writeWord(process, FIGHT + 0x98, 102); expect(false, true, "weapon change left the old trail active");
    require(readWord(process, swoosh(1) + 0x98) == 0 && textured(1), "newly equipped weapon kept empty textures");
    writeWord(process, worldFixture::PLAYER + 0x34, 0); expect(false, false, "dead player emitted a trail");
    writeWord(process, worldFixture::PLAYER + 0x34, 1); expect(false, true, "respawn failed to resume trail");
    writeWord(process, worldFixture::STATE + 0x38, 3); expect(false, false, "spectator emitted a trail");
    writeWord(process, worldFixture::STATE + 0x38, 4); expect(false, true, "battle return did not resume trail");
    writeWord(process, swoosh(1) + 0xB0, 0); expect(false, false, "unbound blade emitted a trail");
    writeWord(process, swoosh(1) + 0xB0, weapon(1) + 0x400); expect(false, true, "late blade binding was not retried");
    writeWord(process, MODE, 3); expect(false, false, "invalid setting enabled a trail");
    writeWord(process, MODE, 0); writeWord(process, FIGHT + 0x38, 2);
    expect(false, false, "trail cleanup failed");
    writeWord(process, worldFixture::ENTITY + 0x8C, 0);
    writeWord(process, worldFixture::ENTITY + 0x100 + 0x8C, 0);
    writeWord(process, bladeFixture::object(1) + 0xB1C, 0);
    writeWord(process, bladeFixture::object(1) + 0xB98, 0);
}
}
