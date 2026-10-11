#include "WeaponTrailHookCode.h"
#include "ClientResourceXml.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"

namespace t7 {
namespace {
constexpr uint32_t DATA_OFFSET = 0x500, PRESET_OFFSET = 0x520;
constexpr uint32_t SETTINGS_RVA = 0x6EC2D0, OPEN_RVA = 0x3735A0, CLOSE_RVA = 0x373630, TEXTURE_RVA = 0x373A60;
std::string bluePreset() { return resourceXml::encoded(L"PVP赵云专用"); }
}
void verifyWeaponTrailFunctions(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {SETTINGS_RVA, {0x55,0x8B,0xEC,0x6A,0xFF}},
        {OPEN_RVA, {0x56,0x8B,0xF1,0x8B,0x0D}},
        {CLOSE_RVA, {0x56,0x8B,0xF1,0x80,0x7E,0x1C,1}},
        {TEXTURE_RVA, {0x55,0x8B,0xEC,0x83,0xC1,0x20}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("weapon trail function signature mismatch");
    }
}
void writeWeaponTrailPreset(Bytes& page) {
    const auto preset = bluePreset();
    if (preset.size() > 15) throw std::runtime_error("weapon trail preset exceeds native SSO capacity");
    const auto length = static_cast<uint32_t>(preset.size());
    const uint32_t capacity = 15;
    std::memcpy(page.data() + PRESET_OFFSET, preset.c_str(), preset.size() + 1);
    std::memcpy(page.data() + PRESET_OFFSET + 16, &length, 4);
    std::memcpy(page.data() + PRESET_OFFSET + 20, &capacity, 4);
}
Bytes buildWeaponTrailHook(uint32_t address, uint32_t imageBase) {
    const auto enabled = address + DATA_OFFSET, remaining = enabled + 4;
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    std::vector<size_t> done;
    auto nonzero = [&](unsigned char modrm, std::vector<size_t>& failures) {
        c.emit({0x85,modrm}); failures.push_back(c.branch(0x84));
    };
    auto component = [&](uint32_t name) {
        c.emit({0x8B,0xCF,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
    };
    // Only the local player's owned weapons are touched. Resolve all pointers anew on the frame thread.
    c.store(enabled, 0);
    c.emit({0xA1}); c.word(imageBase + 0x2E3FA8C); nonzero(0xC0, done);
    c.emit({0x83,0xB8}); c.word(0x274); c.emit({0}); done.push_back(c.branch(0x84));
    c.emit({0xA1}); c.word(imageBase + 0x2D27358); nonzero(0xC0, done);
    c.emit({0x6A,1}); c.call(imageBase + 0x647770);
    c.emit({0x83,0xC4,4,0x83,0xF8,0xFF}); done.push_back(c.branch(0x84));
    c.emit({0x8B,0x0D}); c.word(imageBase + 0x2D27358);
    c.emit({0x8B,0x11,0x50,0xFF,0x52,0x28}); nonzero(0xC0, done);
    c.emit({0x8B,0xF8,0x83,0x7F,0x7C,0}); done.push_back(c.branch(0x84));
    component(0x1783860); nonzero(0xC0, done);
    c.emit({0x81,0x38}); c.word(imageBase + 0x178380C); done.push_back(c.branch(0x85));
    c.emit({0x8B,0xD8}); // EBX GeFightable.
    std::vector<size_t> inactive;
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8); nonzero(0xC0, inactive);
    c.emit({0x83,0x78,0x38,4}); inactive.push_back(c.branch(0x85));
    component(0x179ED94); nonzero(0xC0, inactive);
    c.emit({0x81,0x38}); c.word(imageBase + 0x179ECE4); inactive.push_back(c.branch(0x85));
    c.emit({0x8B,0xC8}); c.call(imageBase + 0x64C9D0);
    c.emit({0x84,0xC0}); inactive.push_back(c.branch(0x85));
    std::vector<size_t> striking;
    c.emit({0x8B,0x43,0x38});
    for (uint32_t state : {238u,244u,250u,256u,439u}) {
        c.emit({0x3D}); c.word(state); striking.push_back(c.branch(0x84));
    }
    inactive.push_back(c.jump());
    for (auto branch : striking) c.bind(branch);
    for (uint32_t service : {0x243B714u,0x2DEA690u,0x2440C44u}) {
        c.emit({0x83,0x3D}); c.word(imageBase + service); c.emit({0}); inactive.push_back(c.branch(0x84));
    }
    // The original settings getter sees both in-game and launcher changes; 0=all, 1=self, 2=off.
    c.call(imageBase + SETTINGS_RVA); c.emit({0x83,0xF8,1}); inactive.push_back(c.branch(0x87));
    c.store(enabled, 1);
    for (auto branch : inactive) c.bind(branch);
    c.emit({0x8B,0x83}); c.word(0x9C); nonzero(0xC0, done);
    c.emit({0x8B,0x30}); c.store(remaining, 64); // Walk owned weapons, closing sheathed ones as well.
    const auto loop = c.bytes.size();
    nonzero(0xF6, done);
    c.emit({0x80,0x7E,0x0D,0}); done.push_back(c.branch(0x85));
    c.emit({0xFF,0x0D}); c.word(remaining); done.push_back(c.branch(0x88));
    std::vector<size_t> next, close;
    c.emit({0x8B,0x46,20}); nonzero(0xC0, next);
    c.emit({0x81,0x38}); c.word(imageBase + 0x179E16C); next.push_back(c.branch(0x85));
    c.emit({0x8B,0x78,8}); nonzero(0xFF, next);
    c.emit({0x83,0x7F,0x7C,0}); next.push_back(c.branch(0x84));
    component(0x17736BC); nonzero(0xC0, next);
    c.emit({0x81,0x38}); c.word(imageBase + 0x1773684); next.push_back(c.branch(0x85));
    c.emit({0x8B,0xF8,0x8B,0x46,16,0x3B,0x83}); c.word(0x98); close.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(enabled); c.emit({0}); close.push_back(c.branch(0x84));
    for (uint32_t point : {0xB0u,0xB4u}) {
        c.emit({0x83,0xBF}); c.word(point); c.emit({0}); close.push_back(c.branch(0x84));
    }
    const auto preset = bluePreset();
    std::vector<size_t> configure;
    c.emit({0x83,0x7F,0x30,static_cast<unsigned char>(preset.size())}); configure.push_back(c.branch(0x85));
    c.emit({0x8D,0x57,0x20,0x83,0x7F,0x34,16}); const auto inlineName = c.branch(0x82);
    c.emit({0x8B,0x12}); c.bind(inlineName); nonzero(0xD2, configure);
    for (size_t i = 0; i < preset.size(); ++i) {
        c.emit({0x80,0x7A,static_cast<unsigned char>(i),static_cast<unsigned char>(preset[i])});
        configure.push_back(c.branch(0x85));
    }
    const auto configured = c.jump();
    for (auto branch : configure) c.bind(branch);
    c.emit({0x80,0x7F,0x1C,0}); const auto alreadyClosed = c.branch(0x84);
    c.emit({0x8B,0xCF}); c.call(imageBase + CLOSE_RVA); c.bind(alreadyClosed);
    c.emit({0x68}); c.word(address + PRESET_OFFSET); c.emit({0x8B,0xCF}); c.call(imageBase + TEXTURE_RVA);
    c.bind(configured);
    c.emit({0x80,0x7F,0x1C,0}); next.push_back(c.branch(0x85));
    // Native activation retains LOD, texture loading, ribbon sampling and fade-out behavior.
    c.emit({0x8B,0xCF}); c.call(imageBase + OPEN_RVA); next.push_back(c.jump());
    for (auto branch : close) c.bind(branch);
    c.emit({0x80,0x7F,0x1C,0}); next.push_back(c.branch(0x84));
    c.emit({0x8B,0xCF}); c.call(imageBase + CLOSE_RVA);
    for (auto branch : next) c.bind(branch);
    c.emit({0x8B,0x46,8}); nonzero(0xC0, done);
    c.emit({0xB9}); c.word(64);
    c.emit({0x80,0x78,0x0D,0}); const auto ascend = c.branch(0x85);
    c.emit({0x8B,0xF0});
    const auto descend = c.bytes.size();
    c.emit({0x8B,0x06}); nonzero(0xC0, done);
    c.emit({0x80,0x78,0x0D,0}); c.bind(c.branch(0x85), loop);
    c.emit({0x8B,0xF0,0x49}); c.bind(c.branch(0x85), descend); done.push_back(c.jump());
    c.bind(ascend);
    const auto parent = c.bytes.size();
    c.emit({0x8B,0x46,4}); nonzero(0xC0, done);
    c.emit({0x80,0x78,0x0D,0}); done.push_back(c.branch(0x85));
    c.emit({0x3B,0x70,8,0x8B,0xF0}); c.bind(c.branch(0x85), loop);
    c.emit({0x49}); c.bind(c.branch(0x85), parent);
    for (auto branch : done) c.bind(branch);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    if (c.bytes.size() > 0xB00) throw std::runtime_error("weapon trail hook overflow");
    return c.bytes;
}
}
