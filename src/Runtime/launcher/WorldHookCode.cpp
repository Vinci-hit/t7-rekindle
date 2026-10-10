#include "ClientWorld.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"
#include "SprintHookCode.h"
#include "ChatInputHookCode.h"
#include <cstring>

namespace t7 {
namespace {
constexpr uint32_t RID_LOOKUP_RVA = 0x647770, COMPONENT_RVA = 0x26A1C0, POSITION_RVA = 0x3D1200;
constexpr uint32_t RID_MANAGER_RVA = 0x2E3FA8C, ENTITY_MANAGER_RVA = 0x2D27358;
constexpr uint32_t INSTANCE_RVA = 0x2E37804, LOCAL_ACTOR_RVA = 0x2E405F8;
constexpr uint32_t MOVABLE_NAME_RVA = 0x177867C, MOVABLE_VTABLE_RVA = 0x17784E4;
constexpr uint32_t CONTROLLER_POSITION_RVA = 0x2956F0;
constexpr uint32_t HEADING_RVA = 0x3D11F0, CONTROLLER_HEADING_RVA = 0x3D11B0;
}
void verifyWorldFunctions(HANDLE process, uint32_t imageBase) {
    verifyTerrainFunctions(process, imageBase);
    verifySprintFunctions(process, imageBase);
    verifyChatInputFunctions(process, imageBase);
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {RID_LOOKUP_RVA, {0x55,0x8B,0xEC,0xFF,0x75,0x08}},
        {COMPONENT_RVA, {0x55,0x8B,0xEC,0x8B,0x55,0x08,0x56,0x8B,0x71,0x7C}},
        {POSITION_RVA, {0x55,0x8B,0xEC,0x8B,0x49,0x38,0x83,0xEC,0x0C}},
        {CONTROLLER_POSITION_RVA, {0x53,0x8B,0xDC,0x83,0xEC,0x08,0x83,0xE4,0xF0}},
        {HEADING_RVA, {0x8B,0x49,0x38,0x8B,0x01,0x8B,0x40,0x74,0xFF,0xE0}},
        {CONTROLLER_HEADING_RVA, {0xD9,0x81,0xAC,0x00,0x00,0x00,0xC3}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("world reader function signature mismatch");
    }
}
Bytes buildWorldHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    if (!address || address > UINT32_MAX - WORLD_PAGE_SIZE || !imageBase
        || imageBase > UINT32_MAX - LOCAL_ACTOR_RVA - 4 || !threadId)
        throw std::runtime_error("world hook outside x86 range");
    WorldHookCode c;
    c.emit({0x9C,0x60,0x64,0xA1}); c.word(0x24);
    c.emit({0x3D}); c.word(threadId); const auto wrongThread = c.branch(0x85);
    c.call(address + CHAT_INPUT_CODE_OFFSET);
    c.call(address + SPRINT_CODE_OFFSET);
    c.emit({0x83,0x3D}); c.word(address); c.emit({1}); const auto idle = c.branch(0x85);
    c.store(address, 2); c.store(address + 4, 0);
    c.emit({0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC}); // FXSAVE; CLD
    std::vector<size_t> invalid;
    auto nonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); invalid.push_back(c.branch(0x84));
    };
    c.emit({0xA1}); c.word(imageBase + LOCAL_ACTOR_RVA); nonzero(0xC0);
    c.emit({0x83,0x78,0x38,4}); invalid.push_back(c.branch(0x85)); // GAME actor state
    c.emit({0x8B,0x35}); c.word(imageBase + INSTANCE_RVA); nonzero(0xF6);
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0xA3}); c.word(address + 8 + offset);
    }
    c.emit({0xA1}); c.word(imageBase + RID_MANAGER_RVA); nonzero(0xC0);
    c.emit({0x83,0xB8}); c.word(0x274); c.emit({0}); invalid.push_back(c.branch(0x84));
    c.emit({0xA1}); c.word(imageBase + ENTITY_MANAGER_RVA); nonzero(0xC0);
    for (uint32_t rid : {1u, 2u}) {
        c.emit({0x68}); c.word(rid); c.call(imageBase + RID_LOOKUP_RVA);
        c.emit({0x83,0xC4,4,0x83,0xF8,0xFF}); invalid.push_back(c.branch(0x84)); // cdecl
        c.emit({0x8B,0x0D}); c.word(imageBase + ENTITY_MANAGER_RVA);
        c.emit({0x8B,0x11,0x50,0xFF,0x52,0x28}); nonzero(0xC0);
        c.emit({0x83,0x78,0x7C,0}); invalid.push_back(c.branch(0x84));
        c.emit({0x8B,0xC8,0x68}); c.word(imageBase + MOVABLE_NAME_RVA); c.call(imageBase + COMPONENT_RVA);
        nonzero(0xC0);
        c.emit({0x81,0x38}); c.word(imageBase + MOVABLE_VTABLE_RVA); invalid.push_back(c.branch(0x85));
        c.emit({0x8B,0xD8,0x8B,0x50,0x38}); nonzero(0xD2); // EBX retains this entity's movable.
        c.emit({0x8B,0x02,0x81,0x78,0x34}); c.word(imageBase + CONTROLLER_POSITION_RVA);
        invalid.push_back(c.branch(0x85));
        c.emit({0x81,0x78,0x74}); c.word(imageBase + CONTROLLER_HEADING_RVA);
        invalid.push_back(c.branch(0x85));
        c.emit({0x8B,0x82}); c.word(0xA8); nonzero(0xC0);
        c.emit({0x83,0x78,0x14,0}); invalid.push_back(c.branch(0x84));
        // Read orientation and position from the same entity on the same game frame.
        c.emit({0x8B,0xCB}); c.call(imageBase + HEADING_RVA);
        c.emit({0xD9,0x1D}); c.word(address + (rid == 1 ? 40 : 44)); // FSTP yaw.
        // The original getter returns protocol-space XYZ (including the controller's vertical offset).
        c.emit({0x8B,0xCB,0x68}); c.word(address + (rid == 1 ? 16 : 28)); c.call(imageBase + POSITION_RVA);
    }
    // Invalid floats must never enter the physics broad phase or FSINCOS.
    for (uint32_t offset = 16; offset < 48; offset += 4) {
        c.emit({0xA1}); c.word(address + offset);
        c.emit({0x25}); c.word(0x7FFFFFFF);
        c.emit({0x3D}); c.word(0x47C35000); // abs(value) < 100000
        invalid.push_back(c.branch(0x83));
    }
    c.call(address + TERRAIN_CODE_OFFSET);
    c.emit({0x3B,0x35}); c.word(imageBase + INSTANCE_RVA); invalid.push_back(c.branch(0x85));
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0x3B,0x05}); c.word(address + 8 + offset);
        invalid.push_back(c.branch(0x85));
    }
    c.store(address + 4, 1);
    for (auto branch : invalid) c.bind(branch);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5}); // FXRSTOR; restore saved stack
    c.store(address, 0); // Completion published last; no asynchronous entity pointer escapes this frame.
    c.bind(wrongThread); c.bind(idle); c.emit({0x61,0x9D,0xC3});
    if (c.bytes.size() > TERRAIN_CODE_OFFSET - WORLD_CODE_OFFSET) throw std::runtime_error("world hook overflow");
    Bytes page(WORLD_PAGE_SIZE, 0);
    std::memcpy(page.data() + WORLD_CODE_OFFSET, c.bytes.data(), c.bytes.size());
    const auto terrain = buildTerrainHook(address, imageBase);
    std::memcpy(page.data() + TERRAIN_CODE_OFFSET, terrain.data(), terrain.size());
    const auto sprint = buildSprintHook(address, imageBase);
    std::memcpy(page.data() + SPRINT_CODE_OFFSET, sprint.data(), sprint.size());
    const auto chatInput = buildChatInputHook(address, imageBase);
    std::memcpy(page.data() + CHAT_INPUT_CODE_OFFSET, chatInput.data(), chatInput.size());
    writeSprintDirections(page);
    return page;
}
}
