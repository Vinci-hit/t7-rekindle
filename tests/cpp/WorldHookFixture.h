#pragma once
#include "../../src/Runtime/launcher/ClientWorld.h"
#include "../../src/Runtime/launcher/RemoteImage.h"
#include <cmath>
#include <cstring>

namespace worldFixture {
constexpr uint32_t BASE = 0x400000, STATE = BASE + 0x9000, INSTANCE = BASE + 0x9100;
constexpr uint32_t RIDS = BASE + 0x9200, ENGINE = BASE + 0x9600, ENGINE_TABLE = BASE + 0x9700;
constexpr uint32_t ENTITY = BASE + 0x9800, MOVABLE = BASE + 0xA000, CONTROLLER = BASE + 0xA400;
constexpr uint32_t TABLE = BASE + 0xAA00, ANCHOR = BASE + 0xAB00, RESOLVE = BASE + 0xAC00;
inline void word(t7::Bytes& bytes, uint32_t v) {
    for (unsigned s = 0; s < 32; s += 8) bytes.push_back(static_cast<unsigned char>(v >> s));
}
inline void writeWord(HANDLE process, uint32_t address, uint32_t value) {
    t7::Bytes bytes; word(bytes, value); t7::writeClientMemory(process, address, bytes);
}
inline void prepare(HANDLE process) {
    writeWord(process, BASE + 0x2E405F8, STATE); writeWord(process, STATE + 0x38, 4);
    writeWord(process, BASE + 0x2E37804, INSTANCE); writeWord(process, INSTANCE + 0x68, 1234);
    writeWord(process, INSTANCE + 0x6C, 1); // Ensure the 64-bit epoch is not truncated.
    writeWord(process, BASE + 0x2E3FA8C, RIDS); writeWord(process, RIDS + 0x274, RIDS);
    writeWord(process, BASE + 0x2D27358, ENGINE); writeWord(process, ENGINE, ENGINE_TABLE);
    writeWord(process, ENGINE_TABLE + 0x28, RESOLVE);
    t7::writeClientMemory(process, BASE + 0x647770, {0x55,0x8B,0xEC,0xFF,0x75,0x08,0x58,0x5D,0xC3});
    t7::Bytes resolver{0x8B,0x44,0x24,4,0x48,0xC1,0xE0,8,0x05}; word(resolver, ENTITY);
    resolver.insert(resolver.end(), {0xC2,4,0}); t7::writeClientMemory(process, RESOLVE, resolver);
    t7::writeClientMemory(process, BASE + 0x26A1C0,
        {0x55,0x8B,0xEC,0x8B,0x55,0x08,0x56,0x8B,0x71,0x7C,0x8B,0x81,0x80,0,0,0,0x5E,0x5D,0xC2,4,0});
    t7::Bytes getter{0x55,0x8B,0xEC,0x8B,0x49,0x38,0x83,0xEC,0x0C,0x8B,0x45,8};
    for (unsigned char offset : std::array<unsigned char, 3>{0, 4, 8})
        getter.insert(getter.end(), {0x8B,0x51,static_cast<unsigned char>(0x10 + offset),0x89,0x50,offset});
    getter.insert(getter.end(), {0xD9,0xEE,0x0F,0x57,0xC0,0x8B,0xE5,0x5D,0xC2,4,0});
    t7::writeClientMemory(process, BASE + 0x3D1200, getter);
    t7::writeClientMemory(process, BASE + 0x2956F0, {0x53,0x8B,0xDC,0x83,0xEC,8,0x83,0xE4,0xF0});
    t7::writeClientMemory(process, BASE + 0x3D11F0, {0x8B,0x49,0x38,0x8B,0x01,0x8B,0x40,0x74,0xFF,0xE0});
    t7::writeClientMemory(process, BASE + 0x3D11B0, {0xD9,0x81,0xAC,0,0,0,0xC3});
    writeWord(process, TABLE + 0x34, BASE + 0x2956F0); writeWord(process, ANCHOR + 0x14, ANCHOR);
    writeWord(process, TABLE + 0x74, BASE + 0x3D11B0);
    for (uint32_t i : {0u, 1u}) {
        writeWord(process, ENTITY + i * 0x100 + 0x7C, ENTITY);
        writeWord(process, ENTITY + i * 0x100 + 0x80, MOVABLE + i * 0x100);
        writeWord(process, MOVABLE + i * 0x100, BASE + 0x17784E4);
        writeWord(process, MOVABLE + i * 0x100 + 0x38, CONTROLLER + i * 0x100);
        writeWord(process, CONTROLLER + i * 0x100, TABLE);
        writeWord(process, CONTROLLER + i * 0x100 + 0xA8, ANCHOR);
        const std::array<float, 3> position{270.f + i, 150.f + i, .25f};
        t7::Bytes bytes(sizeof(position)); std::memcpy(bytes.data(), position.data(), bytes.size());
        t7::writeClientMemory(process, CONTROLLER + i * 0x100 + 0x10, bytes);
        const float yaw = i == 0 ? .5f : -1.25f;
        bytes.resize(sizeof(yaw)); std::memcpy(bytes.data(), &yaw, bytes.size());
        t7::writeClientMemory(process, CONTROLLER + i * 0x100 + 0xAC, bytes);
    }
}
inline t7::WorldSnapshot read(t7::ClientWorld& reader) {
    t7::WorldSnapshot value;
    const auto deadline = GetTickCount64() + 3000;
    while (!reader.poll(value)) {
        if (GetTickCount64() >= deadline) throw std::runtime_error("world fixture response timeout");
        Sleep(5);
    }
    return value;
}
inline void verify(HANDLE process, t7::ClientWorld& reader) {
    auto value = read(reader);
    if (!value.ready || value.instanceStartedAt != 0x1000004D2ULL || !value.sampledAt
        || value.player != std::array<float, 3>{270, 150, .25f}
        || value.bot != std::array<float, 3>{271, 151, .25f}
        || std::abs(value.playerHeading - 28.64789f) > .0001f
        || std::abs(value.botHeading + 71.61972f) > .0001f)
        throw std::runtime_error("world entity/epoch/position snapshot mismatch");
    // Discard the already in-flight sample before asserting a changed fixture state.
    for (const auto& entry : std::array<std::pair<uint32_t, uint32_t>, 6>{{
        {STATE + 0x38, 4}, {BASE + 0x2E3FA8C, RIDS}, {ENTITY + 0x180, MOVABLE + 0x100},
        {MOVABLE + 0x138, CONTROLLER + 0x100}, {ANCHOR + 0x14, ANCHOR},
        {TABLE + 0x74, BASE + 0x3D11B0}
    }}) {
        writeWord(process, entry.first, 0); read(reader);
        if (read(reader).ready) throw std::runtime_error("world accepted an unavailable entity/instance");
        writeWord(process, entry.first, entry.second); read(reader);
        if (!read(reader).ready) throw std::runtime_error("world did not resume after entity recreation");
    }
    writeWord(process, CONTROLLER + 0x110, 0x7FC00000); read(reader);
    if (read(reader).ready) throw std::runtime_error("world accepted a non-finite position");
    writeWord(process, CONTROLLER + 0x110, 0x43878000); read(reader);
    if (!read(reader).ready) throw std::runtime_error("world valid pose recovery");
    writeWord(process, CONTROLLER + 0xAC, 0x3FC90FDB); read(reader); // Turn without moving or reporting keys.
    value = read(reader);
    if (!value.ready || std::abs(value.playerHeading - 90.f) > .0001f
        || value.player != std::array<float, 3>{270, 150, .25f})
        throw std::runtime_error("world missed a stationary turn");
    writeWord(process, CONTROLLER + 0xAC, 0x7FC00000); read(reader);
    if (read(reader).ready) throw std::runtime_error("world accepted a non-finite heading");
    writeWord(process, CONTROLLER + 0xAC, 0x3F000000); read(reader);
    if (!read(reader).ready) throw std::runtime_error("world valid heading recovery");
    Sleep(300);
    if (read(reader).ready) throw std::runtime_error("world acknowledged an expired mailbox sample");
    if (!read(reader).ready) throw std::runtime_error("world sample did not recover after delay");
}
}
