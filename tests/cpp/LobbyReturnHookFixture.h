#pragma once
#include "SprintHookFixture.h"
#include "../../src/Runtime/launcher/RoundEndHookCode.h"

namespace lobbyReturnFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
constexpr uint32_t GFX = BASE + 0xE800, FILE = GFX + 0x40, STREAM = GFX + 0x80, TABLE = GFX + 0xC0;
constexpr uint32_t PATH = GFX + 0x100, SOURCE = GFX + 0x180, BUFFER = GFX + 0x200, OBSERVED = GFX + 0x280;
constexpr uint32_t READ_CALL = GFX + 0x300, STREAM_READ = GFX + 0x400;
constexpr uint32_t READ_RVA = 0x3F2B10, METHOD_OFFSET = 477100, RESOURCE_SIZE = 902623;
const t7r::Bytes ORIGINAL{0xD0,0x30,0x5D,0x39,0x2C,0x42,0x46,0x39,1,0x29,0xD0,0x27,0x68,0x3A};
const t7r::Bytes HOME{0xD0,0x30,0xD0,0x2C,3,0x68,0x89,1,2,2,0xD0,0x27,0x68,0x3A};

inline void setPath(HANDLE process, const std::string& path) {
    t7r::Bytes bytes(path.begin(), path.end()); bytes.push_back(0);
    const bool heap = path.size() >= 16;
    t7r::writeClientMemory(process, heap ? PATH : GFX + 0xC, bytes);
    if (heap) writeWord(process, GFX + 0xC, PATH);
    writeWord(process, GFX + 0x1C, static_cast<uint32_t>(path.size()));
    writeWord(process, GFX + 0x20, heap ? static_cast<uint32_t>(path.size()) : 15);
}

inline void prepare(HANDLE process) {
    setPath(process, "../data/ui/Scripts_Game.gfx");
    writeWord(process, GFX + 8, FILE); writeWord(process, FILE + 0x30, STREAM);
    writeWord(process, STREAM, TABLE); writeWord(process, STREAM + 8, RESOURCE_SIZE);
    writeWord(process, TABLE + 4, STREAM_READ + 0x80);
    writeWord(process, TABLE + 0x10, STREAM_READ + 0x90);
    writeWord(process, TABLE + 0x18, STREAM_READ);
    t7r::writeClientMemory(process, STREAM_READ + 0x80, {0x8B,0x41,8,0xC3});
    t7r::writeClientMemory(process, STREAM_READ + 0x90, {0x8B,0x41,4,0xC3});
    t7r::writeClientMemory(process, BASE + 0x71570, {0x8B,0x49,0x30,0x85,0xC9,0x74,5,0x8B,1,0xFF,0x60,0x10,0x33,0xC0,0xC3});
    t7r::writeClientMemory(process, BASE + 0x71580, {0x8B,0x49,0x30,0x85,0xC9,0x74,5,0x8B,1,0xFF,0x60,4,0x33,0xC0,0xC3});
    t7r::writeClientMemory(process, BASE + 0x717C0,
        {0x55,0x8B,0xEC,0x8B,0x49,0x30,0x85,0xC9,0x74,6,0x8B,1,0x5D,0xFF,0x60,0x18,0x33,0xC0,0x5D,0xC2,8,0});
    t7r::writeClientMemory(process, BASE + READ_RVA,
        {0x55,0x8B,0xEC,0x8B,0x49,8,0x85,0xC9,0x75,7,0x83,0xC8,0xFF,0x5D,0xC2,8,0,0x5D,0xE9,0x99,0xEC,0xC7,0xFF});

    t7r::WorldHookCode c;
    c.emit({0x55,0x8B,0xEC,0x56,0x57,0x8B,0x41,0xC,0x85,0xC0}); const auto noData = c.branch(0x8E);
    c.emit({0x01,0x41,4,0x8B,0xC8,0xBE}); c.word(SOURCE);
    c.emit({0x8B,0x7D,8,0xFC,0xF3,0xA4}); c.bind(noData); c.emit({0x5F,0x5E,0x5D,0xC2,8,0});
    t7r::writeClientMemory(process, STREAM_READ, c.bytes);
    c.bytes.clear(); c.emit({0x9C,0x60,0x89,0x25}); c.word(OBSERVED + 4);
    c.emit({0xBB}); c.word(0x11223344); c.emit({0xBE}); c.word(0x22334455);
    c.emit({0xBF}); c.word(0x33445566); c.emit({0xBD}); c.word(0x44556677);
    c.emit({0xFF,0x35}); c.word(OBSERVED + 28); c.emit({0x68}); c.word(BUFFER);
    c.emit({0xB9}); c.word(GFX); c.call(BASE + READ_RVA);
    c.emit({0xA3}); c.word(OBSERVED); c.emit({0x89,0x25}); c.word(OBSERVED + 8);
    for (const auto& reg : {std::pair{0x1D,12u}, std::pair{0x35,16u}, std::pair{0x3D,20u}, std::pair{0x2D,24u}}) {
        c.emit({0x89,static_cast<unsigned char>(reg.first)}); c.word(OBSERVED + reg.second);
    }
    c.emit({0x61,0x9D,0xC3}); t7r::writeClientMemory(process, READ_CALL, c.bytes);
}

template<class Step>
void verify(HANDLE process, t7r::ClientWorld& reader, uint32_t mailbox, Step step) {
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    const auto error = mailbox + t7r::ROUND_END_DATA_OFFSET + 16;
    auto read = [&](uint32_t offset, const t7r::Bytes& source, int returned = -2) {
        const auto count = returned == -2 ? static_cast<int>(source.size()) : returned;
        writeWord(process, STREAM + 4, offset); writeWord(process, STREAM + 12, static_cast<uint32_t>(count));
        writeWord(process, OBSERVED + 28, static_cast<uint32_t>(source.size()));
        t7r::writeClientMemory(process, SOURCE, source);
        t7r::writeClientMemory(process, BUFFER, t7r::Bytes(64, 0xCC));
        step(3);
        require(readWord(process, OBSERVED) == static_cast<uint32_t>(count), "lobby resource read changed native result");
        require(readWord(process, OBSERVED + 4) == readWord(process, OBSERVED + 8), "lobby resource read changed ESP");
        for (const auto& reg : {std::pair{12u,0x11223344u}, std::pair{16u,0x22334455u},
                               std::pair{20u,0x33445566u}, std::pair{24u,0x44556677u}})
            require(readWord(process, OBSERVED + reg.first) == reg.second, "lobby resource read changed nonvolatile register");
        return t7r::readClientMemory(process, BUFFER, source.size());
    };
    for (const auto& path : {"../data/ui/Scripts_Game.gfx", "..\\DATA\\UI\\SCRIPTS_GAME.GFX"}) {
        setPath(process, path);
        t7r::Bytes source(8, 0xAA), expected = source;
        source.insert(source.end(), ORIGINAL.begin(), ORIGINAL.end()); source.insert(source.end(), 8, 0xBB);
        expected.insert(expected.end(), HOME.begin(), HOME.end()); expected.insert(expected.end(), 8, 0xBB);
        require(read(METHOD_OFFSET - 8, source) == expected, "lobby entry retained custom-room return page");
        require(read(METHOD_OFFSET - 8, source) == expected, "re-reading lobby resource was not deterministic");
    }
    // Resource loaders may split the method at any byte boundary.
    for (size_t split = 1; split < ORIGINAL.size(); ++split) {
        t7r::Bytes first(ORIGINAL.begin(), ORIGINAL.begin() + split), last(ORIGINAL.begin() + split, ORIGINAL.end());
        require(read(METHOD_OFFSET, first) == t7r::Bytes(HOME.begin(), HOME.begin() + split), "lobby prefix short read");
        require(read(METHOD_OFFSET + static_cast<uint32_t>(split), last) == t7r::Bytes(HOME.begin() + split, HOME.end()),
                "lobby suffix short read");
    }
    auto shortRead = read(METHOD_OFFSET, ORIGINAL, 6);
    require(t7r::Bytes(shortRead.begin(), shortRead.begin() + 6) == t7r::Bytes(HOME.begin(), HOME.begin() + 6)
        && t7r::Bytes(shortRead.begin() + 6, shortRead.end()) == t7r::Bytes(8, 0xCC), "lobby read used requested rather than actual length");
    for (const auto& path : {"../data/ui/panel_score.gfx", "../data/ui/scripts_game.gfx.bak", "short.gfx"}) {
        setPath(process, path);
        require(read(METHOD_OFFSET, ORIGINAL) == ORIGINAL, "lobby adaptation changed unrelated resource");
    }
    setPath(process, "../data/ui/scripts_game.gfx");
    require(read(0, ORIGINAL) == ORIGINAL && read(METHOD_OFFSET + 14, ORIGINAL) == ORIGINAL,
            "lobby adaptation changed unrelated bytecode");
    for (int result : {0, -1})
        require(read(METHOD_OFFSET, ORIGINAL, result) == t7r::Bytes(ORIGINAL.size(), 0xCC), "lobby read failure changed buffer");
    writeWord(process, GFX + 8, 0);
    read(METHOD_OFFSET, ORIGINAL, -1); writeWord(process, GFX + 8, FILE);
    require(readWord(process, error) == 0, "valid lobby resource reported mismatch");
    auto rejected = [&] {
        require(readWord(process, error) == 1, "unknown lobby resource was silently accepted");
        const auto epoch = worldFixture::INSTANCE + 0x68;
        writeWord(process, epoch, readWord(process, epoch) + 1); step(0);
        require(readWord(process, error) == 1, "battle epoch cleared persistent lobby resource failure");
        bool reported = false;
        try { t7r::WorldSnapshot value; reader.poll(value); } catch (const std::runtime_error&) { reported = true; }
        require(reported, "lobby resource failure was not reported");
        writeWord(process, error, 0);
    };
    writeWord(process, STREAM + 8, RESOURCE_SIZE + 1);
    require(read(METHOD_OFFSET, ORIGINAL) == ORIGINAL, "wrong-size lobby resource was modified"); rejected();
    writeWord(process, STREAM + 8, RESOURCE_SIZE);
    auto unknown = ORIGINAL; unknown.back() ^= 1;
    require(read(METHOD_OFFSET, unknown) == unknown, "unknown lobby bytecode was partially modified"); rejected();
    require(read(METHOD_OFFSET, ORIGINAL) == HOME, "lobby resource did not resume for valid input");
}
}
