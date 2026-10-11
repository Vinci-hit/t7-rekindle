#pragma once
#include "TerrainHookFixture.h"
#include "SprintHookFixture.h"

namespace bladeFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
constexpr uint32_t QUERY = BASE + 0x80000, OBSTACLES = BASE + 0x81000, HIT = BASE + 0x82000;
constexpr uint32_t OBJECTS = BASE + 0x90000;
inline uint32_t object(uint32_t index) { return OBJECTS + index * 0x1000; }
inline void vector(HANDLE process, uint32_t address, std::array<float, 3> value) {
    t7::Bytes bytes(sizeof(value)); std::memcpy(bytes.data(), value.data(), bytes.size());
    t7::writeClientMemory(process, address, bytes);
}
inline void number(HANDLE process, uint32_t address, float value) {
    uint32_t bits; std::memcpy(&bits, &value, 4); writeWord(process, address, bits);
}
inline void prepare(HANDLE process) {
    for (uint32_t i = 0; i < 3; ++i) {
        const auto p = object(i), entity = worldFixture::ENTITY + i * 0x100;
        for (const auto& field : std::vector<std::array<uint32_t, 2>>{
            {entity+8,i+100}, {p,BASE+0x1777C54}, {p+0x48,p+0x100},
            {p+0x108,p+0x200}, {p+0x10C,1}, {p+0x254,p+0x300},
            {p+0x320,2}, {p+0x324,0}, {p+0x33C,p+0x400},
            {p+0x408,terrainFixture::WORLD}, {p+0x410,p+0x500}, {p+0x60C,p+0x410},
            {p+0x700,BASE+0x178380C}, {p+0x798,1030411}, {p+0x79C,p+0xE00},
            {p+0x738,238}, {p+0x740,17}, {p+0x748,GetTickCount()},
            {p+0xE04,p+0x800}, {p+0x810,1030411}, {p+0x814,p+0x900},
            {p+0x900,BASE+0x179E16C}, {p+0x908,p+0xA00}, {p+0xA08,i+200},
            {p+0xA7C,entity}, {p+0xA90,p+0xB00}, {p+0xB00,BASE+0x1773684},
            {p+0xBB0,p+0xD00}, {p+0xBB4,p+0xC00}
        }) writeWord(process, field[0], field[1]);
        // Swoosh visibility remains zero: geometry must not depend on graphical effects.
        vector(process, p + 0xC88, {270,150,1}); vector(process, p + 0xD88, {271,150,1});
    }
    writeWord(process, HIT, 0xBF800000);
    for (uint32_t i = 0; i < 5; ++i) {
        writeWord(process, OBSTACLES + i * 16, i < 3 ? 1 : 0);
        number(process, OBSTACLES + i * 16 + 4, 270.f + .25f * (i + 1));
        writeWord(process, OBSTACLES + i * 16 + 8,
                  i < 3 ? object(i) + 0x600 : i == 3 ? terrainFixture::CHILD : terrainFixture::ACTOR_CHILD);
    }
    t7::WorldHookCode c;
    c.emit({0x60}); // EDI ray input, ESI collector. Synthetic planes use the native callback ABI.
    for (uint32_t i = 0; i < 5; ++i) {
        std::vector<size_t> skip;
        c.emit({0x83,0x3D}); c.word(OBSTACLES + i * 16); c.emit({0}); skip.push_back(c.branch(0x84));
        c.emit({0xF3,0x0F,0x10,0x47,16,0xF3,0x0F,0x5C,0x07,0x0F,0x57,0xC9,0x0F,0x2F,0xC1});
        skip.push_back(c.branch(0x84));
        c.emit({0xF3,0x0F,0x10,0x15}); c.word(OBSTACLES + i * 16 + 4);
        c.emit({0xF3,0x0F,0x5C,0x17,0xF3,0x0F,0x5E,0xD0,0x0F,0x2F,0xD1});
        skip.push_back(c.branch(0x82));
        c.emit({0x0F,0x2F,0x15}); c.word(OBSTACLES + 0x80); skip.push_back(c.branch(0x87));
        c.emit({0xF3,0x0F,0x11,0x15}); c.word(HIT + 0x10);
        c.emit({0x68}); c.word(HIT); c.emit({0xFF,0x35}); c.word(OBSTACLES + i * 16 + 8);
        c.emit({0x8B,0xCE,0x8B,0x06,0xFF,0x10});
        for (auto branch : skip) c.bind(branch);
    }
    c.emit({0x61,0xC3}); t7::writeClientMemory(process, QUERY, c.bytes);
    number(process, OBSTACLES + 0x80, 1);
}
inline void verify(HANDLE process, t7::ClientWorld& reader, uint32_t mailbox) {
    auto require = [](bool value, const char* message) { if (!value) throw std::runtime_error(message); };
    writeWord(process, worldFixture::FRAME_GATE, 1); Sleep(30);
    t7::WorldSnapshot last;
    auto step = [&] {
        Sleep(16);
        require(!reader.poll(last), "native fixture expected a fresh consumer request");
        writeWord(process, worldFixture::FRAME_GATE, 2);
        last = worldFixture::read(reader);
        return last;
    };
    auto sample = [&] {
        step(); return step();
    };
    auto blade = [&](std::array<float, 3> base, std::array<float, 3> tip) {
        Sleep(10); // Keep the two explicitly requested poses on distinct sample ticks.
        vector(process, object(0) + 0xC88, base); vector(process, object(0) + 0xD88, tip);
        return step().actors[0].blade;
    };
    for (uint32_t i = 0; i < 3; ++i)
        writeWord(process, worldFixture::ENTITY + i * 0x100 + 0x88, object(i));
    writeWord(process, worldFixture::ENTITY + 0x8C, object(0) + 0x700);
    auto value = sample().actors[0].blade;
    require(value.ready && value.length == 1 && value.base == std::array<float,3>{270,150,1}
            && value.tip == std::array<float,3>{271,150,1}, "native ribbon endpoints/visibility mismatch");
    require(!value.contacts[1].contact.hit && value.contacts[2].contact.hit && value.contacts[3].contact.hit,
            "blade collector lost target roots or admitted self");
    require(value.contacts[2].contact.position == std::array<float,3>{270.5f,150,1}
            && value.contacts[2].contact.normal == std::array<float,3>{-1,0,0}
            && value.contacts[2].along == .5f, "blade native point/normal/fraction mismatch");
    require(!sample().actors[1].blade.ready, "actor without a weapon published a blade");
    blade({270,150,1}, {270,151,1}); sample();
    const auto frozen = t7::readClientMemory(process, mailbox + t7::ACTORS_DATA_OFFSET, sizeof(t7::ActorMailbox));
    const auto publishedBatch = last.contactBatchId;
    auto frameOnly = [&] {
        Sleep(16); writeWord(process, worldFixture::FRAME_GATE, 2);
        const auto deadline = GetTickCount64() + 3000;
        while (t7::readClientMemory(process, worldFixture::FRAME_GATE, 4) != t7::Bytes{1,0,0,0}) {
            require(GetTickCount64() < deadline, "unrequested native frame timeout"); Sleep(1);
        }
    };
    vector(process, object(0) + 0xC88, {271,150,1}); vector(process, object(0) + 0xD88, {271,151,1});
    frameOnly(); // No reader.poll: a complete crossing happens between consumer requests.
    for (int i = 0; i < 8; ++i) frameOnly(); // Keep sampling beyond the old 100 ms request-gap limit.
    require(t7::readClientMemory(process, mailbox + t7::ACTORS_DATA_OFFSET, sizeof(t7::ActorMailbox)) == frozen,
            "native sampling changed the unacknowledged response");
    const auto retained = step();
    require(retained.contactsReady && retained.contactFrames >= 9 && !retained.droppedContacts
            && retained.contactBatchId != publishedBatch, "native sampling waited for a business request");
    require(!retained.actors[0].blade.contacts[2].contact.hit
            && std::any_of(retained.contacts.begin(), retained.contacts.end(), [&](const auto& event) {
                return event.actorId == 1 && event.hit.targetId == 2 && event.entity == 100 && event.targetEntity == 101
                    && event.state == 238 && event.sequence == 17 && event.occurredAt < retained.sampledAt - 100
                    && std::abs(event.hit.contact.position[0] - 270.5f) < .0001f;
            }), "native journal lost the swept contact before mailbox acknowledgement");
    require(step().contacts.empty(), "native journal replayed a consumed batch");
    blade({270,150,1}, {271,150,1}); sample();
    const auto record = t7::readClientMemory(process, mailbox + t7::CONTACTS_DATA_OFFSET, sizeof(t7::ContactEventMailbox));
    t7::Bytes full(t7::MAX_FRAME_CONTACTS * sizeof(t7::ContactEventMailbox));
    for (size_t i = 0; i < t7::MAX_FRAME_CONTACTS; ++i)
        std::copy(record.begin(), record.end(), full.begin() + i * sizeof(t7::ContactEventMailbox));
    t7::writeClientMemory(process, mailbox + t7::LIVE_CONTACTS_OFFSET, full);
    writeWord(process, mailbox + t7::FRAME_DATA_OFFSET + offsetof(t7::ContactFrameMailbox, count), t7::MAX_FRAME_CONTACTS);
    const auto saturated = step();
    require(saturated.contacts.size() == t7::MAX_FRAME_CONTACTS && saturated.droppedContacts >= 2,
            "native journal overflow was silent or overwrote retained contacts");
    const auto resumed = step();
    require(resumed.contacts.size() == 2 && resumed.droppedContacts == 0, "native journal failed to resume after acknowledgement");
    writeWord(process, OBSTACLES + 4 * 16, 1);
    value = sample().actors[0].blade;
    require(!value.contacts[0].contact.hit, "unregistered rigid body became scene contact");
    writeWord(process, OBSTACLES + 3 * 16, 1); number(process, OBSTACLES + 3 * 16 + 4, 270.625f);
    value = sample().actors[0].blade;
    require(value.contacts[0].contact.hit && value.contacts[0].contact.material == 4
            && value.contacts[2].along < value.contacts[0].along && value.contacts[3].along > value.contacts[0].along,
            "blade collector lost scene material or independently ordered contacts");
    writeWord(process, OBSTACLES + 3 * 16, 0);
    writeWord(process, worldFixture::PLAYER + 0x134, 0); value = sample().actors[0].blade;
    require(!value.contacts[2].contact.hit && value.contacts[3].contact.hit, "corpse consumed blade contact");
    writeWord(process, worldFixture::PLAYER + 0x134, 1);
    writeWord(process, object(1) + 0x320, 9); value = sample().actors[0].blade;
    require(!value.contacts[2].contact.hit, "movement trigger admitted as hurt geometry");
    writeWord(process, object(1) + 0x320, 2);
    writeWord(process, object(1) + 0x408, terrainFixture::WORLD + 0x100); value = sample().actors[0].blade;
    require(!value.contacts[2].contact.hit, "foreign physics world admitted as hurt geometry");
    writeWord(process, object(1) + 0x408, terrainFixture::WORLD);
    blade({270,150,1}, {270,151,1}); sample();
    value = blade({271,150,1}, {271,151,1}); // Both static sections miss; only the swept interior crosses.
    require(value.ready && value.spanMs > 0 && value.contacts[2].contact.hit
            && std::abs(value.contacts[2].time - .5f) < .0001f
            && std::abs(value.contacts[2].contact.position[0] - 270.5f) < .0001f,
            "moving blade tunneled between frame endpoints");
    require(!sample().actors[0].blade.contacts[2].contact.hit, "old blade sweep replayed while stationary");
    writeWord(process, sprintFixture::CLOCK, GetTickCount()); writeWord(process, sprintFixture::CLOCK_MODE, 1);
    blade({270,150,1}, {270,151,1});
    value = blade({271,150,1}, {271,151,1});
    require(value.ready && value.spanMs == 0 && value.contacts[2].contact.hit,
            "two native frames on one clock tick lost their swept geometry");
    writeWord(process, sprintFixture::CLOCK_MODE, 0);
    // Missing nodes reset history; restoring them must not connect across the missing frame.
    writeWord(process, object(0) + 0xBB0, 0);
    require(!sample().actors[0].blade.ready, "missing ribbon node accepted");
    vector(process, object(0) + 0xC88, {270,150,1}); vector(process, object(0) + 0xD88, {270,151,1});
    writeWord(process, object(0) + 0xBB0, object(0) + 0xD00);
    value = sample().actors[0].blade;
    require(value.ready && !value.contacts[2].contact.hit, "missing node bridged stale weapon history");
    writeWord(process, object(0) + 0xA08, 999);
    value = blade({271,150,1}, {271,151,1});
    require(value.ready && value.spanMs == 0 && !value.contacts[2].contact.hit, "weapon replacement retained old sweep");
    writeWord(process, worldFixture::INSTANCE + 0x68, 1235);
    value = blade({270,150,1}, {270,151,1});
    require(value.ready && value.spanMs == 0 && !value.contacts[2].contact.hit, "epoch change retained old sweep");
    writeWord(process, worldFixture::INSTANCE + 0x68, 1234);
    sample(); Sleep(120); value = blade({271,150,1}, {271,151,1});
    require(value.ready && value.spanMs == 0 && !value.contacts[2].contact.hit, "long sampling gap retained old sweep");
    number(process, object(0) + 0xD88, std::numeric_limits<float>::quiet_NaN());
    require(!sample().actors[0].blade.ready, "non-finite ribbon point accepted");
    value = blade({270,150,1}, {270,150,1}); require(!value.ready, "zero-length blade accepted");
    value = blade({270,150,1}, {271,150,1}); require(value.ready, "blade recovery failed");
    writeWord(process, terrainFixture::MAP + 0x1C, 0);
    require(!sample().actors[0].blade.ready, "missing scene world accepted as blade clear");
    writeWord(process, terrainFixture::MAP + 0x1C, terrainFixture::COLLISION);
    for (uint32_t i = 0; i < 3; ++i) writeWord(process, worldFixture::ENTITY + i * 0x100 + 0x88, 0);
    writeWord(process, worldFixture::ENTITY + 0x8C, 0); sample();
    writeWord(process, worldFixture::FRAME_GATE, 0);
}
}
