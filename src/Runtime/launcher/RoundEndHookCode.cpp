#include "RoundEndHookCode.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"

namespace t7 {
namespace {
constexpr uint32_t INSTANCE_RVA = 0x2E37804, ROOM_RVA = 0x2E3A4C4, SCENE_RVA = 0x2E3CB88;

void epoch(WorldHookCode& c, uint32_t data) {
    // ESI is the live instance. A missing actor or a recreated service is not a new battle.
    c.emit({0x8B,0x46,0x68,0x8B,0x56,0x6C,0x3B,0x05}); c.word(data);
    const auto changed = c.branch(0x85);
    c.emit({0x3B,0x15}); c.word(data + 4); const auto same = c.branch(0x84);
    c.bind(changed);
    c.emit({0xA3}); c.word(data); c.emit({0x89,0x15}); c.word(data + 4);
    c.store(data + 8, 0); c.store(data + 12, 0);
    c.bind(same);
}

void jumpTo(WorldHookCode& c, uint32_t origin, uint32_t target) {
    c.emit({0xE9}); c.word(target - (origin + static_cast<uint32_t>(c.bytes.size()) + 4));
}
}

std::vector<std::pair<uint32_t, Bytes>> roundEndOriginals(uint32_t imageBase) {
    WorldHookCode leave;
    leave.emit({0x8B,0x0D}); leave.word(imageBase + ROOM_RVA);
    return {{FINISH_RESULT_RVA, {0x8B,0x4D,0xD0,0x8B,0x46,0x10}}, {NATIVE_LEAVE_RVA, leave.bytes}};
}

std::vector<std::pair<uint32_t, Bytes>> roundEndJumps(uint32_t address, uint32_t imageBase) {
    std::vector<std::pair<uint32_t, Bytes>> result;
    for (auto item : {std::pair{FINISH_RESULT_RVA, FINISH_OBSERVER_OFFSET},
                     std::pair{NATIVE_LEAVE_RVA, LEAVE_OBSERVER_OFFSET}}) {
        WorldHookCode c;
        jumpTo(c, imageBase + item.first, address + item.second); c.emit({0x90});
        result.emplace_back(item.first, c.bytes);
    }
    return result;
}

void verifyRoundEndFunctions(HANDLE process, uint32_t imageBase) {
    auto signatures = roundEndOriginals(imageBase);
    signatures.push_back({FINISH_RESULT_RVA + 6, {0x56,0x89,0x41,0x4C,0xE8,0xEE,0xD9,0xFF,0xFF}});
    WorldHookCode leave;
    leave.emit({0x6A,0,0xE8,0x73,0x2C,0xE8,0xFF,0xA1}); leave.word(imageBase + SCENE_RVA);
    leave.emit({0x83,0x78,0x24,0,0x74,5,0xE8,0x93,0x11,0xE0,0xFF,0xB8,1,0,0,0,0xC2,0x10,0});
    signatures.push_back({NATIVE_LEAVE_RVA + 6, leave.bytes});
    for (const auto& entry : signatures)
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("round end native function signature mismatch");
}

Bytes buildRoundEndHooks(uint32_t address, uint32_t imageBase) {
    const auto data = address + ROUND_END_DATA_OFFSET;
    Bytes result(0x1000, 0);
    auto copy = [&](const WorldHookCode& c, uint32_t offset) {
        if (c.bytes.size() > 0x400) throw std::runtime_error("round end hook overflow");
        std::memcpy(result.data() + offset - ROUND_END_CODE_OFFSET, c.bytes.data(), c.bytes.size());
    };
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    c.emit({0x8B,0x35}); c.word(imageBase + INSTANCE_RVA);
    c.emit({0x85,0xF6}); std::vector<size_t> done{c.branch(0x84)};
    epoch(c, data);
    c.emit({0x83,0x7E,0x7C,5}); done.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(data + 8); c.emit({0}); done.push_back(c.branch(0x85));
    c.emit({0xA1}); c.word(data + 12); c.emit({0x48,0x83,0xF8,2}); done.push_back(c.branch(0x87));
    std::vector<size_t> failed;
    for (auto rva : {ROOM_RVA, SCENE_RVA}) {
        c.emit({0x83,0x3D}); c.word(imageBase + rva); c.emit({0}); failed.push_back(c.branch(0x84));
    }
    c.store(data + 8, 1); // Mark before entering the original UI leave action.
    c.emit({0x6A,0,0x6A,0,0x6A,0,0x6A,0}); c.call(imageBase + NATIVE_LEAVE_RVA);
    c.emit({0x84,0xC0}); done.push_back(c.branch(0x85));
    for (auto branch : failed) c.bind(branch);
    c.store(data + 8, 2);
    for (auto branch : done) c.bind(branch);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    copy(c, ROUND_END_CODE_OFFSET);

    // Observe actual FINISH consumption, not a result field left over from the previous battle.
    c.bytes.clear(); c.emit({0x9C,0x60,0x8B,0xDE,0x8B,0x75,0xD0});
    epoch(c, data);
    c.emit({0x8B,0x43,0x10,0xA3}); c.word(data + 12);
    c.emit({0x61,0x9D,0x8B,0x4D,0xD0,0x8B,0x46,0x10});
    jumpTo(c, address + FINISH_OBSERVER_OFFSET, imageBase + FINISH_RESULT_RVA + 6);
    copy(c, FINISH_OBSERVER_OFFSET);

    // A manual leave uses the same entry and suppresses a later automatic request.
    c.bytes.clear(); c.emit({0x9C,0x60,0x8B,0x35}); c.word(imageBase + INSTANCE_RVA);
    c.emit({0x85,0xF6}); const auto noInstance = c.branch(0x84);
    epoch(c, data); c.store(data + 8, 1);
    c.bind(noInstance); c.emit({0x61,0x9D,0x8B,0x0D}); c.word(imageBase + ROOM_RVA);
    jumpTo(c, address + LEAVE_OBSERVER_OFFSET, imageBase + NATIVE_LEAVE_RVA + 6);
    copy(c, LEAVE_OBSERVER_OFFSET);
    return result;
}
}
