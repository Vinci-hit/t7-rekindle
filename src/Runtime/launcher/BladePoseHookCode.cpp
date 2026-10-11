#include "ClientWorld.h"
#include "WorldHookCode.h"

namespace t7 {
Bytes buildBladePoseHook(uint32_t, uint32_t imageBase) {
    WorldHookCode c;
    c.emit({0x60,0x8B,0xF1,0x8D,0xAF}); c.word(offsetof(ActorMailbox, blade)); // ESI entity, EBP blade.
    auto store = [&](uint32_t offset, uint32_t value) { c.emit({0xC7,0x85}); c.word(offset); c.word(value); };
    auto save = [&](uint32_t offset) { c.emit({0x89,0x85}); c.word(offset); };
    store(offsetof(BladeMailbox, poseReady), 0);
    store(offsetof(BladeMailbox, bodyCount), 0);
    store(offsetof(BladeMailbox, bodyWorld), 0);
    store(offsetof(BladeMailbox, state), 0);
    c.emit({0x8B,0x46,8}); save(offsetof(BladeMailbox, actorEntity));
    auto component = [&](uint32_t name) {
        c.emit({0x8B,0xCE,0x68}); c.word(imageBase + name); c.call(imageBase + 0x26A1C0);
    };
    std::vector<size_t> noBodies, noBlade;
    auto nonzero = [&](unsigned char modrm, std::vector<size_t>& failures) {
        c.emit({0x85,modrm}); failures.push_back(c.branch(0x84));
    };
    component(0x1777D50); nonzero(0xC0, noBodies); // GeHavokPhysicable.
    c.emit({0x81,0x38}); c.word(imageBase + 0x1777C54); noBodies.push_back(c.branch(0x85));
    c.emit({0x8B,0x40,0x48}); nonzero(0xC0, noBodies); // Bone-linked hurt bodies, not movement triggers.
    c.emit({0x8B,0x58,0x0C,0x83,0xFB}); c.emit({static_cast<unsigned char>(MAX_BLADE_BODIES)});
    noBodies.push_back(c.branch(0x87)); nonzero(0xDB, noBodies);
    c.emit({0x8B,0x50,8}); nonzero(0xD2, noBodies);
    const auto bodyLoop = c.bytes.size();
    std::vector<size_t> skipBody;
    c.emit({0x8B,0x42,0x54}); nonzero(0xC0, skipBody);
    c.emit({0x83,0x78,0x20,2}); skipBody.push_back(c.branch(0x85));
    c.emit({0x8B,0x40,0x3C}); nonzero(0xC0, skipBody);
    c.emit({0x83,0x78,0x10,0}); skipBody.push_back(c.branch(0x84));
    c.emit({0x8B,0x48,8}); nonzero(0xC9, skipBody);
    c.emit({0x89,0x8D}); c.word(offsetof(BladeMailbox, bodyWorld));
    c.emit({0x83,0xC0,0x10,0x8B,0x8D}); c.word(offsetof(BladeMailbox, bodyCount));
    c.emit({0x89,0x84,0x8D}); c.word(offsetof(BladeMailbox, roots));
    c.emit({0xFF,0x85}); c.word(offsetof(BladeMailbox, bodyCount));
    for (auto branch : skipBody) c.bind(branch);
    c.emit({0x83,0xC2,0x60,0x4B}); c.bind(c.branch(0x85), bodyLoop);
    for (auto branch : noBodies) c.bind(branch);

    component(0x1783860); nonzero(0xC0, noBlade); // GeFightable's current weapon map.
    c.emit({0x81,0x38}); c.word(imageBase + 0x178380C); noBlade.push_back(c.branch(0x85));
    for (const auto& field : std::array<std::array<uint32_t, 2>, 3>{{
        {0x38, offsetof(BladeMailbox, state)}, {0x40, offsetof(BladeMailbox, sequence)},
        {0x48, offsetof(BladeMailbox, phaseTick)}
    }}) {
        c.emit({0x8B,0x50,static_cast<unsigned char>(field[0]),0x89,0x95}); c.word(field[1]);
    }
    c.emit({0x8B,0x90}); c.word(0x98); nonzero(0xD2, noBlade);
    c.emit({0x8B,0x80}); c.word(0x9C); nonzero(0xC0, noBlade);
    c.emit({0x8B,0x40,4,0xB9}); c.word(64);
    const auto weaponLoop = c.bytes.size();
    nonzero(0xC0, noBlade);
    c.emit({0x80,0x78,0x0D,0}); noBlade.push_back(c.branch(0x85));
    c.emit({0x3B,0x50,0x10}); const auto found = c.branch(0x84), left = c.branch(0x8C);
    c.emit({0x8B,0x40,8}); const auto advanced = c.jump();
    c.bind(left); c.emit({0x8B,0x00}); c.bind(advanced);
    c.emit({0x49}); c.bind(c.branch(0x85), weaponLoop); noBlade.push_back(c.jump());
    c.bind(found); c.emit({0x8B,0x40,0x14}); nonzero(0xC0, noBlade);
    c.emit({0x81,0x38}); c.word(imageBase + 0x179E16C); noBlade.push_back(c.branch(0x85));
    c.emit({0x8B,0x70,8}); nonzero(0xF6, noBlade);
    c.emit({0x8B,0x46,8}); save(offsetof(BladeMailbox, weaponEntity));
    c.emit({0x83,0x7E,0x7C,0}); noBlade.push_back(c.branch(0x84));
    component(0x17736BC); nonzero(0xC0, noBlade); // GeSwooshable holds the original bound NiAVObjects.
    c.emit({0x81,0x38}); c.word(imageBase + 0x1773684); noBlade.push_back(c.branch(0x85));
    c.emit({0x8B,0xD8});
    // Native ribbon sampling reads XYZ at NiAVObject + 0x88. B is the blade root, A the tip.
    // Read the live transforms, not the smoothed visual trail or its quality/visibility flags.
    for (const auto& point : std::array<std::array<uint32_t, 2>, 2>{{
        {0xB4, offsetof(BladeMailbox, base)}, {0xB0, offsetof(BladeMailbox, tip)}
    }}) {
        c.emit({0x8B,0x93}); c.word(point[0]); nonzero(0xD2, noBlade);
        for (uint32_t axis = 0; axis < 3; ++axis) {
            c.emit({0x8B,0x82}); c.word(0x88 + axis * 4); save(point[1] + axis * 4);
            c.emit({0x25}); c.word(0x7FFFFFFF); c.emit({0x3D}); c.word(0x47C35000);
            noBlade.push_back(c.branch(0x83));
        }
    }
    store(offsetof(BladeMailbox, poseReady), 1);
    for (auto branch : noBlade) c.bind(branch);
    c.emit({0x61,0xC3});
    if (c.bytes.size() > BLADE_SWEEP_CODE_OFFSET - BLADE_POSE_CODE_OFFSET)
        throw std::runtime_error("blade pose hook overflow");
    return c.bytes;
}
}
