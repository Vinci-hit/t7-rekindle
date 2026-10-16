#include "ClientWorld.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"
#include <cstring>

namespace t7r {
namespace {
constexpr uint32_t WORLD_RAY_RVA = 0x10FA3B0, CLOSEST_HIT_RVA = 0x108BFA0, MATERIAL_RVA = 0x29D4B0;
constexpr uint32_t SCENE_MANAGER_RVA = 0x2E24AF8, PHYSICS_MANAGER_RVA = 0x2D379A8;
constexpr uint32_t MAP_INFO_NAME_RVA = 0x177E884, MAP_INFO_VTABLE_RVA = 0x177E7EC;
constexpr uint32_t COLLISION_MAP_VTABLE_RVA = 0x177C1DC, SCENE_DATA_VTABLE_RVA = 0x1764C58;
constexpr uint32_t BODY_VTABLE_RVA = 0x1764B68, WORLD_VTABLE_RVA = 0x1ACFF4C;
constexpr uint32_t COLLECTOR_CALLBACK = 0xC00, COLLECTOR_TABLE = 0xD00, CONSTANTS = 0xD10;
constexpr uint32_t TERRAIN_DATA_OFFSET = 0x600;
}

void verifyTerrainFunctions(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {WORLD_RAY_RVA, {0x55,0x8B,0xEC,0x83,0xE4,0xF0,0x83,0xEC,0x58}},
        {CLOSEST_HIT_RVA, {0x53,0x8B,0x5C,0x24,0x0C,0x57,0x8B,0xF9}},
        {MATERIAL_RVA, {0x8B,0x41,0x08,0x85,0xC0,0x74,0x28}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("terrain query function signature mismatch");
    }
}

Bytes buildTerrainHook(uint32_t address, uint32_t imageBase) {
    // Aligned scratch storage stays above persistent sprint/chat state in the data page.
    const auto collector = address + TERRAIN_DATA_OFFSET, input = collector + 0x100, materialQuery = collector + 0x140;
    const auto code = address + TERRAIN_CODE_OFFSET;
    const auto one = code + CONSTANTS, reach = one + 4;
    WorldHookCode c;
    c.emit({0x60});
    for (uint32_t i = 0; i < 2; ++i) c.store(address + 48 + i * 48, 0);
    std::vector<size_t> unavailable;
    auto nonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); unavailable.push_back(c.branch(0x84));
    };
    auto load = [&](unsigned char offset) { c.emit({0x8B,0x40,offset}); nonzero(0xC0); };
    auto vtable = [&](uint32_t rva) {
        c.emit({0x81,0x38}); c.word(imageBase + rva); unavailable.push_back(c.branch(0x85));
    };
    c.emit({0xA1}); c.word(imageBase + SCENE_MANAGER_RVA); nonzero(0xC0);
    load(0x1C); // Scene entity -> MapInfo -> acollision, not anavigation.
    c.emit({0x83,0x78,0x7C,0}); unavailable.push_back(c.branch(0x84));
    c.emit({0x8B,0xC8,0x68}); c.word(imageBase + MAP_INFO_NAME_RVA); c.call(imageBase + 0x26A1C0);
    nonzero(0xC0); vtable(MAP_INFO_VTABLE_RVA);
    load(0x1C); vtable(COLLISION_MAP_VTABLE_RVA);
    load(0x38); vtable(SCENE_DATA_VTABLE_RVA);
    load(0x1C); vtable(BODY_VTABLE_RVA);
    c.emit({0x83,0x78,0x20,14}); unavailable.push_back(c.branch(0x85));
    load(0x3C); c.emit({0x8B,0x70,8}); nonzero(0xF6); // Body's owning hkpWorld.
    c.emit({0x8D,0x50,0x10,0x89,0x15}); c.word(collector + 0x70); // Exact root collidable.
    load(0x10);
    c.emit({0x80,0x78,8,16}); unavailable.push_back(c.branch(0x85)); // Static compound shape.
    c.emit({0xA3}); c.word(materialQuery + 8);
    c.emit({0xA1}); c.word(imageBase + PHYSICS_MANAGER_RVA); nonzero(0xC0);
    load(0x10); load(0x10);
    c.emit({0x3B,0xC6}); unavailable.push_back(c.branch(0x85));
    vtable(WORLD_VTABLE_RVA);
    c.emit({0x83,0x78,0x74,0}); unavailable.push_back(c.branch(0x84));

    auto copy = [&](uint32_t from, uint32_t to) {
        c.emit({0xA1}); c.word(from); c.emit({0xA3}); c.word(to);
    };
    auto height = [&](uint32_t from, uint32_t to) {
        c.emit({0xD9,0x05}); c.word(from); c.emit({0xD8,0x05}); c.word(one);
        c.emit({0xD9,0x1D}); c.word(to);
    };
    for (uint32_t i = 0; i < 2; ++i) {
        size_t skip = 0, coincident = 0;
        if (i == 1) {
            c.emit({0x83,0x3D}); c.word(address + 240); c.emit({1}); skip = c.branch(0x85);
        } else {
            c.emit({0x83,0x3D}); c.word(address + TERRAIN_TARGET_ONLY_OFFSET); c.emit({0}); skip = c.branch(0x85);
        }
        const auto result = address + 48 + i * 48;
        const auto actor = i / 2, position = address + 16 + actor * 12;
        copy(position, result + 8); copy(position + 4, result + 12);
        height(position + 8, result + 16);
        if (i % 2 == 0) {
            c.emit({0xD9,0x05}); c.word(address + 40 + actor * 4);
            c.emit({0xD9,0xFB}); // FSINCOS: heading uses (cos(yaw), -sin(yaw)).
            c.emit({0xD8,0x0D}); c.word(reach); c.emit({0xD8,0x05}); c.word(result + 8);
            c.emit({0xD9,0x1D}); c.word(result + 20);
            c.emit({0xD9,0xE0,0xD8,0x0D}); c.word(reach); c.emit({0xD8,0x05}); c.word(result + 12);
            c.emit({0xD9,0x1D}); c.word(result + 24); copy(result + 16, result + 28);
        } else {
            const auto target = address + 16 + (1 - actor) * 12;
            copy(target, result + 20); copy(target + 4, result + 24);
            height(target + 8, result + 28);
            // Overlapping actors have no intervening terrain segment; do not submit a zero-length ray.
            c.emit({0x0F,0x57,0xC0});
            for (uint32_t axis = 0; axis < 3; ++axis) {
                c.emit({0xF3,0x0F,0x10,0x0D}); c.word(result + 8 + axis * 4);
                c.emit({0xF3,0x0F,0x5C,0x0D}); c.word(result + 20 + axis * 4);
                c.emit({0xF3,0x0F,0x59,0xC9,0xF3,0x0F,0x58,0xC1});
            }
            c.emit({0x0F,0x57,0xC9,0x0F,0x2F,0xC1}); const auto distinct = c.branch(0x85);
            c.store(result, 1); c.store(result + 4, UINT32_MAX); coincident = c.jump();
            c.bind(distinct);
        }
        for (uint32_t j = 0; j < 3; ++j) {
            copy(result + 8 + j * 4, input + j * 4);
            copy(result + 20 + j * 4, input + 16 + j * 4);
        }
        c.store(input + 12, 0); c.store(input + 28, 0);
        c.store(input + 32, 0); c.store(input + 36, 12); c.store(input + 40, 0);
        // The original query layer 12 includes scene layer 14. The collector rejects other bodies.
        c.store(collector, code + COLLECTOR_TABLE);
        c.store(collector + 4, 0x3F800000); c.store(collector + 0x20, 0x3F800000);
        c.store(collector + 0x60, 0);
        for (uint32_t j = 0; j < 8; ++j) c.store(collector + 0x30 + j * 4, UINT32_MAX);
        c.emit({0x68}); c.word(collector); c.emit({0x68}); c.word(input);
        c.emit({0x8B,0xCE}); c.call(imageBase + WORLD_RAY_RVA); // thiscall, callee pops 8.
        c.store(result, 1); c.store(result + 4, UINT32_MAX);
        c.emit({0x83,0x3D}); c.word(collector + 0x60); c.emit({0}); const auto clear = c.branch(0x84);
        c.store(result, 2); copy(collector + 0x20, result + 44);
        for (uint32_t j = 0; j < 3; ++j) copy(collector + 0x10 + j * 4, result + 32 + j * 4);
        // The last terminated shape key identifies the compound instance's material user data.
        c.emit({0xBA}); c.word(collector + 0x30); c.emit({0xB9}); c.word(8);
        const auto keys = c.bytes.size();
        c.emit({0x83,0x3A,0xFF}); const auto terminated = c.branch(0x84);
        c.emit({0x83,0xC2,4,0x49}); c.bind(c.branch(0x85), keys);
        c.emit({0xE9}); const auto unterminated = c.bytes.size(); c.word(0);
        c.bind(terminated);
        c.emit({0x81,0xFA}); c.word(collector + 0x30); const auto noKey = c.branch(0x84);
        c.emit({0x83,0xEA,4,0x89,0x15}); c.word(materialQuery + 12);
        c.emit({0xB9}); c.word(materialQuery); c.call(imageBase + MATERIAL_RVA);
        c.emit({0x83,0xF8,7}); const auto unknownMaterial = c.branch(0x87);
        c.emit({0xA3}); c.word(result + 4);
        c.bind(unknownMaterial); c.bind(noKey); c.bind(unterminated); c.bind(clear);
        if (coincident) c.bind(coincident);
        if (skip) c.bind(skip);
    }
    for (auto branch : unavailable) c.bind(branch);
    c.emit({0x61,0xC3});
    if (c.bytes.size() > COLLECTOR_CALLBACK) throw std::runtime_error("terrain hook overflow");
    c.bytes.resize(COLLECTOR_CALLBACK, 0xCC);
    // addRayHit(cdBody, hit): follow parents to the root before invoking the original closest collector.
    c.emit({0x8B,0x44,0x24,4});
    const auto parent = c.bytes.size();
    c.emit({0x8B,0x50,0x0C,0x85,0xD2}); const auto root = c.branch(0x84);
    c.emit({0x8B,0xC2,0xE9}); const auto loop = c.bytes.size(); c.word(0); c.bind(loop, parent);
    c.bind(root); c.emit({0x3B,0x41,0x70}); const auto different = c.branch(0x85);
    c.emit({0xB8}); c.word(imageBase + CLOSEST_HIT_RVA); c.emit({0xFF,0xE0});
    c.bind(different); c.emit({0xC2,8,0});
    if (c.bytes.size() > COLLECTOR_TABLE) throw std::runtime_error("terrain collector overflow");
    c.bytes.resize(COLLECTOR_TABLE, 0);
    c.word(code + COLLECTOR_CALLBACK); c.word(0); // No allocation/destruction through this borrowed vtable.
    c.bytes.resize(CONSTANTS, 0); c.word(0x3F800000); c.word(0x400CCCCD); // Weapon height 1 m; reach 2.2 m.
    return c.bytes;
}
}
