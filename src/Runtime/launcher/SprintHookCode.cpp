#include "SprintHookCode.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"
#include <cstring>

namespace t7 {
namespace {
constexpr uint32_t DIRECTIONS_OFFSET = 0x200;
constexpr uint32_t SPEED_RVA = 0x3D2550, RUNNING_RVA = 0x3D2E20;
constexpr uint32_t LINE_MOVE_RVA = 0x75D140, STOP_MOVE_RVA = 0x75D080;
uint32_t floatBits(float value) {
    uint32_t result; std::memcpy(&result, &value, sizeof(result)); return result;
}
}
void verifySprintFunctions(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {SPEED_RVA, {0x55,0x8B,0xEC,0x8B,0x49,0x38,0xF3,0x0F,0x10,0x45,8}},
        {RUNNING_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {LINE_MOVE_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {STOP_MOVE_RVA, {0x55,0x8B,0xEC,0x6A,0xFF,0x68}},
        {0x4C6E70, {0x55,0x8B,0xEC,0x83,0xEC,0x10,0x8B,0xC1,0x8B,0x4D,8}},
        {0x4971B0, {0x8B,0x01,0xC3}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("sprint function signature mismatch");
    }
}
void writeSprintDirections(Bytes& page) {
    // Indexed by W/S/A/D bits. Opposing keys cancel on each axis.
    const char* directions[]{"", "up", "down", "", "left", "leftUp", "leftDown", "left",
        "right", "rightUp", "rightDown", "right", "", "up", "down", ""};
    for (size_t i = 0; i < 16; ++i) {
        const auto offset = DIRECTIONS_OFFSET + i * 24;
        const auto length = static_cast<uint32_t>(std::strlen(directions[i]));
        const uint32_t capacity = 15;
        std::memcpy(page.data() + offset, directions[i], length + 1);
        std::memcpy(page.data() + offset + 16, &length, 4);
        std::memcpy(page.data() + offset + 20, &capacity, 4);
    }
}
Bytes buildSprintHook(uint32_t address, uint32_t imageBase) {
    // State contains identities/timestamps, never a pointer reused across frames.
    const auto data = address + SPRINT_DATA_OFFSET;
    const auto epoch = data, identity = data + 8, began = data + 12, tracking = data + 16;
    const auto lastDash = data + 20, lastKeys = data + 24, game = data + 28;
    const auto now = data + 32, offline = data + 36, speed = data + 40, running = data + 44;
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    std::vector<size_t> invalid;
    auto nonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); invalid.push_back(c.branch(0x84));
    };
    auto component = [&](uint32_t name, uint32_t table) {
        c.emit({0x8B,0xCF,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
        nonzero(0xC0);
        if (table) {
            c.emit({0x81,0x38}); c.word(imageBase + table); invalid.push_back(c.branch(0x85));
        }
    };
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); nonzero(0xC0);
    c.emit({0x8B,0x40,0x38,0xA3}); c.word(game);
    c.emit({0xA1}); c.word(imageBase + 0x2E3FA8C); nonzero(0xC0);
    c.emit({0x83,0xB8}); c.word(0x274); c.emit({0}); invalid.push_back(c.branch(0x84));
    c.emit({0xA1}); c.word(imageBase + 0x2D27358); nonzero(0xC0);
    c.emit({0x6A,1}); c.call(imageBase + 0x647770);
    c.emit({0x83,0xC4,4,0x83,0xF8,0xFF}); invalid.push_back(c.branch(0x84));
    c.emit({0x8B,0x0D}); c.word(imageBase + 0x2D27358);
    c.emit({0x8B,0x11,0x50,0xFF,0x52,0x28}); nonzero(0xC0);
    c.emit({0x83,0x78,0x7C,0}); invalid.push_back(c.branch(0x84));
    c.emit({0x8B,0xF8}); // EDI entity until its components have been resolved.
    c.emit({0x8B,0x35}); c.word(imageBase + 0x2E37804); nonzero(0xF6);
    std::vector<size_t> changed;
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0x3B,0x05}); c.word(epoch + offset);
        changed.push_back(c.branch(0x85));
    }
    c.emit({0x3B,0x3D}); c.word(identity); const auto sameEntity = c.branch(0x84);
    for (auto branch : changed) c.bind(branch);
    c.store(tracking, 0); c.store(lastDash, 0);
    for (uint32_t offset : {0u, 4u}) {
        c.emit({0x8B,0x46,static_cast<unsigned char>(0x68 + offset),0xA3}); c.word(epoch + offset);
    }
    c.emit({0x89,0x3D}); c.word(identity); c.bind(sameEntity);
    component(0x177867C, 0x17784E4);
    c.emit({0x8B,0xD8,0x8B,0x48,0x38}); nonzero(0xC9); // EBX movable
    c.emit({0x8B,0x01,0x81,0x78,0x3C}); c.word(imageBase + 0x295FA0);
    invalid.push_back(c.branch(0x85));
    c.emit({0x8B,0x81}); c.word(0xA8); nonzero(0xC0);
    c.emit({0x83,0x78,0x14,0}); invalid.push_back(c.branch(0x84));
    component(0x17ADDFC, 0); c.emit({0xA3}); c.word(offline);
    component(0x1783860, 0x178380C);
    c.emit({0x8B,0x70,0x38}); // ESI battle state
    component(0x178692C, 0x17868BC);
    c.emit({0x83,0x78,0x60,6}); invalid.push_back(c.branch(0x82));
    c.emit({0x8B,0x40,0x54}); nonzero(0xC0);
    c.emit({0x8B,0x38,0x83,0xE7,0x3F}); // EDI W/S/A/D/crouch/jump
    c.emit({0xA1}); c.word(imageBase + 0x16F6228); nonzero(0xC0);
    c.emit({0xFF,0xD0,0xA3}); c.word(now);
    c.store(speed, floatBits(JOG_SPEED)); c.store(running, 0);

    auto move = [&]() {
        // EAX direction mask; original commands retain collision/gravity and own the movement queue.
        c.emit({0x83,0xE0,0x0F,0x6B,0xC0,24,0x05}); c.word(address + DIRECTIONS_OFFSET);
        c.emit({0x8B,0x0D}); c.word(offline);
        c.emit({0x83,0x78,0x10,0}); const auto stop = c.branch(0x84);
        c.emit({0x50,0x6A,0,0x68}); c.word(floatBits(DASH_SPEED));
        c.emit({0xFF,0x35}); c.word(speed); c.call(imageBase + LINE_MOVE_RVA);
        const auto done = c.jump(); c.bind(stop); c.call(imageBase + STOP_MOVE_RVA); c.bind(done);
    };
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); const auto notGame = c.branch(0x85);
    c.emit({0x8B,0xC6,0x2D}); c.word(438);
    c.emit({0x83,0xF8,2}); const auto dash = c.branch(0x86);
    const auto normal = c.bytes.size();
    c.emit({0x83,0x3D}); c.word(lastDash); c.emit({0}); const auto noRestore = c.branch(0x84);
    c.emit({0x8B,0xC7}); move(); c.store(lastDash, 0);
    c.bind(noRestore);
    std::vector<size_t> reset;
    c.emit({0x83,0x3D}); c.word(game); c.emit({4}); reset.push_back(c.branch(0x85));
    // Ordinary attacks and parries retain JOG_SPEED and clear the run timer until idle.
    c.emit({0x83,0xFE,2}); reset.push_back(c.branch(0x85));
    c.emit({0x8B,0xC7,0x83,0xE0,0x33,0x83,0xF8,1}); reset.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(tracking); c.emit({0}); const auto started = c.branch(0x85);
    c.emit({0xA1}); c.word(now); c.emit({0xA3}); c.word(began); c.store(tracking, 1);
    c.bind(started);
    c.emit({0xA1}); c.word(now); c.emit({0x2B,0x05}); c.word(began);
    c.emit({0x3D}); c.word(RUN_DELAY_MS); const auto warming = c.branch(0x82);
    c.store(speed, floatBits(RUN_SPEED)); c.store(running, 1);
    const auto armed = c.jump();
    for (auto branch : reset) c.bind(branch);
    c.store(tracking, 0);
    const auto resetDone = c.jump();

    c.bind(notGame); c.emit({0x33,0xFF}); const auto inactive = c.jump(); c.bind(inactive, normal);
    c.bind(dash);
    c.store(tracking, 0); c.store(speed, 0);
    c.emit({0x81,0xFE}); c.word(439); const auto notStrike = c.branch(0x85);
    c.store(speed, floatBits(DASH_SPEED)); c.bind(notStrike);
    c.emit({0x3B,0x35}); c.word(lastDash); const auto phaseChanged = c.branch(0x85);
    c.emit({0x3B,0x3D}); c.word(lastKeys); const auto sameDash = c.branch(0x84);
    c.bind(phaseChanged);
    c.emit({0x89,0x35}); c.word(lastDash); c.emit({0x89,0x3D}); c.word(lastKeys);
    c.emit({0x33,0xC0,0x81,0xFE}); c.word(439); c.emit({0x0F,0x94,0xC0}); move();
    c.bind(sameDash); c.bind(warming); c.bind(armed); c.bind(resetDone);
    // Reapply only when the native key tree or a phase change altered the actual velocity.
    c.emit({0x8B,0x4B,0x38,0xA1}); c.word(speed); c.emit({0x3B,0x41,0x24});
    const auto sameSpeed = c.branch(0x84);
    c.emit({0x50,0x8B,0xCB}); c.call(imageBase + SPEED_RVA); c.bind(sameSpeed);
    c.emit({0xA1}); c.word(running); c.emit({0x38,0x83}); c.word(0xCC);
    const auto sameRunning = c.branch(0x84);
    c.emit({0x50,0x8B,0xCB}); c.call(imageBase + RUNNING_RVA); c.bind(sameRunning);
    const auto done = c.jump();
    for (auto branch : invalid) c.bind(branch);
    c.store(tracking, 0); c.store(lastDash, 0); c.store(identity, 0);
    c.bind(done);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    if (c.bytes.size() > 0x1000) throw std::runtime_error("sprint hook overflow");
    return c.bytes;
}
}
