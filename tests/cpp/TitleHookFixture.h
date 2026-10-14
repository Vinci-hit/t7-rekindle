#pragma once
#include "../../src/Runtime/launcher/TitleSettings.h"
#include "../../src/Runtime/launcher/RemoteImage.h"
#include <algorithm>
#include <cstring>

namespace titleFixture {
constexpr uint32_t BASE = 0x400000, OWNER = BASE + 0x28000, ROWS = BASE + 0x29000, CALLS = OWNER + 0x20;
inline void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
inline void word(t7r::Bytes& bytes, uint32_t v) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<uint8_t>(v >> shift));
}
inline void put(HANDLE process, uint32_t address, uint32_t value) {
    t7r::Bytes bytes; word(bytes, value); t7r::writeClientMemory(process, address, bytes);
}
inline uint32_t get(HANDLE process, uint32_t address) {
    auto bytes = t7r::readClientMemory(process, address, 4);
    uint32_t result; std::memcpy(&result, bytes.data(), 4); return result;
}
inline void prepare(HANDLE process) {
    put(process, BASE + 0x2440AD0, OWNER); put(process, OWNER, BASE + 0x17302A4);
    put(process, OWNER + 8, ROWS); put(process, OWNER + 12, 3);
    put(process, BASE + 0x2E249B8, OWNER);
    t7r::writeClientMemory(process, ROWS, t7r::Bytes(548 * 3, 0x5A));
    put(process, ROWS, 1008); put(process, ROWS + 548, 999); put(process, ROWS + 1096, 1007);
    t7r::Bytes refresh{0x55,0x8B,0xEC,0x6A,0xFF,0x68}; word(refresh, 0);
    refresh.insert(refresh.end(), {0xFF,0x05}); word(refresh, CALLS);
    refresh.insert(refresh.end(), {0xD9,0xEE,0x0F,0x57,0xC0,0x8B,0xE5,0x5D,0xC3});
    t7r::writeClientMemory(process, BASE + t7r::TITLE_REFRESH_RVA, refresh);
}
inline void verify(HANDLE process, t7r::TitleSettings& settings) {
    t7r::Bytes descriptions(256, 0);
    descriptions[0] = 'A'; descriptions[128] = 'B';
    require(t7r::validTitleDescriptions(descriptions), "valid title text rejected");
    require(!t7r::validTitleDescriptions(t7r::Bytes(255, 0)), "short title mailbox accepted");
    require(!t7r::validTitleDescriptions(t7r::Bytes(256, 'X')), "unterminated title text accepted");
    auto badPadding = descriptions; badPadding[20] = 1;
    require(!t7r::validTitleDescriptions(badPadding), "title padding accepted hidden data");
    const auto before = t7r::readClientMemory(process, ROWS, 548 * 3);
    put(process, OWNER, 0);
    settings.update(descriptions); settings.poll(); Sleep(50); settings.poll();
    require(get(process, CALLS) == 0 && t7r::readClientMemory(process, ROWS, before.size()) == before,
        "unready original table was edited");
    put(process, OWNER, BASE + 0x17302A4);
    auto awaitCalls = [&](uint32_t count) {
        const auto deadline = GetTickCount64() + 4000;
        while (get(process, CALLS) < count) {
            settings.poll(); Sleep(5); require(GetTickCount64() < deadline, "title frame response timeout");
        }
        settings.poll();
    };
    awaitCalls(1);
    auto expected = before;
    std::copy(descriptions.begin(), descriptions.begin() + 128, expected.begin() + 1096 + 36);
    std::copy(descriptions.begin() + 128, descriptions.end(), expected.begin() + 36);
    require(t7r::readClientMemory(process, ROWS, expected.size()) == expected,
        "title update changed IDs, names, unrelated rows or description boundaries");
    settings.update(descriptions); settings.poll(); Sleep(30); settings.poll();
    require(get(process, CALLS) == 1, "unchanged title table rebuilt every frame");
    descriptions[0] = 'C'; settings.update(descriptions); settings.poll();
    descriptions[0] = 'D'; settings.update(descriptions);
    awaitCalls(3);
    require(t7r::readClientMemory(process, ROWS + 1096 + 36, 1)[0] == 'D', "pending title update lost newest description");
}
}
