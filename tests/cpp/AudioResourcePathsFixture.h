#pragma once
#include "AudioPlaybackFixture.h"

namespace audioPathFixture {
using namespace audioFixture;
constexpr uint32_t ROOT_SITE = 0x3E5FDD, INIT_SITE = 0x3E60EA, SCENE_SITE = 0x3E671B;
constexpr uint32_t MEDIA_POINTER = DATA + 0x400, SCAN_STATS = DATA + 0x500;
constexpr uint32_t PATTERN = DATA + 0x1000, FIND_DATA = DATA + 0x1800;
constexpr uint32_t FLOAT_BEFORE = DATA + 0x2000, FLOAT_AFTER = DATA + 0x2200;
inline void direct(Code& code, uint32_t address) {
    code.emit({0xE8}); word(code.bytes, address - (code.base + static_cast<uint32_t>(code.bytes.size()) + 4));
}
inline t7r::fs::path createPaths(const t7r::fs::path& root) {
    auto kernel = module("KERNEL32.dll");
    kernel.symbols.insert(kernel.symbols.end(), {"ExitProcess", "FindFirstFileA", "FindClose"});
    t7r::Bytes memory(0x1780000, 0);
    const auto imports = t7r::writeClientImports(memory, 0x8000, 0x10000, {kernel});
    const std::string relative = "..\\Data\\Audio\\Wwise\\Windows\\";
    const std::wstring wide(relative.begin(), relative.end());
    std::memcpy(memory.data() + 0x1779A34, relative.c_str(), relative.size() + 1);
    std::memcpy(memory.data() + 0x17799F0, wide.c_str(), (wide.size() + 1) * sizeof(wchar_t));
    const uint32_t mediaFunction = BASE + 0x2400;
    std::memcpy(memory.data() + 0x16F6080, &mediaFunction, 4);
    Code media(mediaFunction); media.emit({0x8B,0x44,0x24,4}); media.imm(0xA3, MEDIA_POINTER);
    media.imm(0xB8, 1); media.emit({0xC3}); put(memory, 0x2400, media.bytes);
    Code rootCode(BASE + ROOT_SITE); rootCode.imm(0x68, BASE + 0x17799F0);
    rootCode.indirect(BASE + 0x16F6080); rootCode.emit({0x83,0xC4,4,0xC3}); put(memory, ROOT_SITE, rootCode.bytes);

    // Emulate the length-taking string constructor, then use the actual ANSI enumerator.
    Code scan(BASE + 0x2000);
    scan.emit({0x55,0x8B,0xEC,0x56,0x57,0x8B,0x75,8,0x8B,0x4D,12}); scan.imm(0xBF, PATTERN);
    scan.emit({0xFC,0xF3,0xA4,0xC7,7}); word(scan.bytes, 0x6E622E2A); // *.bn
    scan.emit({0x66,0xC7,0x47,4,0x6B,0}); // k + NUL
    scan.imm(0x68, FIND_DATA); scan.imm(0x68, PATTERN); scan.indirect(iat(kernel, "FindFirstFileA"));
    scan.emit({0x83,0xF8,0xFF}); const auto failed = scan.branch(0x84);
    scan.emit({0x50}); scan.indirect(iat(kernel, "FindClose"));
    scan.imm(0xB8, 1); const auto done = scan.branch();
    scan.bind(failed, scan.bytes.size()); scan.emit({0x33,0xC0}); scan.bind(done, scan.bytes.size());
    scan.emit({0x5F,0x5E,0x5D,0xC3}); put(memory, 0x2000, scan.bytes);

    for (uint32_t index = 0; index < 2; ++index) {
        const auto site = index == 0 ? INIT_SITE : SCENE_SITE;
        const auto stats = SCAN_STATS + index * 0x100;
        Code before(BASE + site - 0x80);
        before.emit({0x55,0x8B,0xEC,0x83,0xEC,0x28,0xDB,0xE3,0xD9,0xE8}); // frame, FNINIT, FLD1
        before.emit({0x0F,0xAE,0x05}); word(before.bytes, FLOAT_BEFORE);
        for (const auto reg : {0xB8,0xBB,0xB9,0xBA,0xBE,0xBF}) before.imm(static_cast<unsigned char>(reg), 0x12340000u + reg);
        before.emit({0xF9,0x9C,0x8F,0x05}); word(before.bytes, stats); // STC; save flags
        before.emit({0x89,0x25}); word(before.bytes, stats + 4);
        before.emit({0xE9}); word(before.bytes, BASE + site - (before.base + static_cast<uint32_t>(before.bytes.size()) + 4));
        put(memory, site - 0x80, before.bytes);
        Code body(BASE + site);
        body.emit({0x6A,0x1C,0xC7,0x45,0xE8,0,0,0,0,0xC7,0x45,0xEC,0,0,0,0});
        body.imm(0x68, BASE + 0x1779A34);
        body.emit({0x9C,0x8F,0x05}); word(body.bytes, stats + 8);
        body.emit({0x89,0x25}); word(body.bytes, stats + 12);
        unsigned offset = 16;
        for (const auto reg : {0x05,0x1D,0x0D,0x15,0x35,0x3D}) {
            body.emit({0x89,static_cast<unsigned char>(reg)}); word(body.bytes, stats + offset); offset += 4;
        }
        body.emit({0x0F,0xAE,0x05}); word(body.bytes, FLOAT_AFTER);
        body.emit({0x8B,0x45,0xE8}); body.imm(0xA3, stats + 40);
        body.emit({0x8B,0x44,0x24,4}); body.imm(0xA3, stats + 44);
        direct(body, BASE + 0x2000); body.imm(0xA3, stats + 48);
        body.emit({0x83,0xC4,8,0x8B,0xE5,0x5D,0xC3}); put(memory, site, body.bytes);
    }
    Code main(BASE + 0x1000);
    direct(main, BASE + ROOT_SITE);
    direct(main, BASE + INIT_SITE - 0x80);
    direct(main, BASE + SCENE_SITE - 0x80);
    main.store(DONE, 1);
    const auto wait = main.bytes.size(); main.emit({0x6A,1}); main.indirect(iat(kernel, "Sleep"));
    main.emit({0x83,0x3D}); word(main.bytes, COMMAND); main.emit({0}); main.bind(main.branch(0x84), wait);
    main.emit({0x6A,0}); main.indirect(iat(kernel, "ExitProcess")); put(memory, 0x1000, main.bytes);
    const auto path = root / "AudioPathsFixture.exe";
    pe(path, memory, BASE, 0x1000, {imports.rva, imports.size}, {}, false);
    return path;
}
}
