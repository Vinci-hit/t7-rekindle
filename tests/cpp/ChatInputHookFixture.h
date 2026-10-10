#pragma once
#include "SprintHookFixture.h"

namespace chatInputFixture {
using worldFixture::BASE;
using worldFixture::writeWord;
using sprintFixture::readWord;
using sprintFixture::pump;
constexpr uint32_t WINDOW = BASE + 0xD000, IME = BASE + 0xD200, NEXT_IME = IME + 0x20;
constexpr uint32_t CONTEXT = BASE + 0xD300, ASSOCIATIONS = CONTEXT + 4, MESSAGES = CONTEXT + 8;
constexpr uint32_t ERRORS = CONTEXT + 12, CAMERA_BLOCKED = CONTEXT + 16;
constexpr uint32_t ASSOCIATE = BASE + 0xD400, SEND_MESSAGE = BASE + 0xD500;
constexpr uint32_t WINDOW_GLOBAL = BASE + 0x243B70C, IME_GLOBAL = BASE + 0x2E249EC;
constexpr uint32_t ASSOCIATE_IMPORT = BASE + 0x16F611C, SEND_MESSAGE_IMPORT = BASE + 0x16F69E8;

inline void prepare(HANDLE process) {
    for (const auto& item : std::vector<std::pair<uint32_t, uint32_t>>{
        {WINDOW,BASE+0x172D034}, {WINDOW+0x138,1}, {IME,BASE+0x177AE48}, {IME+4,0x1234},
        {NEXT_IME,BASE+0x177AE48}, {NEXT_IME+4,0x5678}, {CONTEXT,0x1234},
        {ASSOCIATE_IMPORT,ASSOCIATE}, {SEND_MESSAGE_IMPORT,SEND_MESSAGE}
    }) writeWord(process, item.first, item.second);
    // Signature anchors for the native window getter, text-focus getter and cursor mode handler.
    t7::writeClientMemory(process, BASE + 0x12670, {0x8B,0x81,0x38,1,0,0,0xC3});
    t7::writeClientMemory(process, BASE + 0x400D30, {0x8A,0x41,8,0xC2,4,0});
    t7::writeClientMemory(process, BASE + 0x7D33B,
        {0x0F,0x94,0xC0,0x80,0xB9,0x11,1,0,0,0,0x88,0x81,0x10,1,0,0});
    t7::WorldHookCode c;
    auto check = [&]() {
        const auto valid = c.branch(0x84);
        c.emit({0xFF,0x05}); c.word(ERRORS); c.bind(valid);
    };
    auto windowArgument = [&]() {
        c.emit({0x8B,0x44,0x24,4,0x3B,0x05}); c.word(WINDOW + 0x138); check();
    };
    windowArgument();
    c.emit({0x8B,0x44,0x24,8,0x87,0x05}); c.word(CONTEXT); // Return the previous HIMC.
    c.emit({0xFF,0x05}); c.word(ASSOCIATIONS);
    c.emit({0xD9,0xEE,0x0F,0x57,0xC0,0xC2,8,0});
    t7::writeClientMemory(process, ASSOCIATE, c.bytes);
    c.bytes.clear(); windowArgument();
    c.emit({0x81,0x7C,0x24,8}); c.word(0x41E); check();
    c.emit({0x83,0x7C,0x24,16,0}); check();
    c.emit({0x8B,0x44,0x24,12,0xA2}); c.word(WINDOW + 0x114);
    c.emit({0xA3}); c.word(CAMERA_BLOCKED); // GE_SHOW_MOUSE / GE_HIDE_MOUSE subscriber.
    c.emit({0xFF,0x05}); c.word(MESSAGES);
    c.emit({0xD9,0xEE,0x0F,0x57,0xC0,0x33,0xC0,0xC2,16,0});
    t7::writeClientMemory(process, SEND_MESSAGE, c.bytes);
}

inline void verify(HANDLE process, t7::ClientWorld& reader) {
    auto require = [&](bool value, const char* reason) { if (!value) throw std::runtime_error(reason); };
    auto pollWorld = [&]() {
        worldFixture::read(reader); // Discard a sample requested before the current input state.
        for (int i = 0; i < 3; ++i) {
            const auto value = worldFixture::read(reader);
            require(value.ready && value.terrain[0][0].ready, "chat input test did not execute terrain queries");
            pump(process);
        }
    };
    auto expect = [&](uint32_t context, bool freeCursor, const char* reason) {
        pump(process);
        require(readWord(process, CONTEXT) == context
            && (readWord(process, WINDOW + 0x114) & 0xFF) == static_cast<uint32_t>(freeCursor)
            && readWord(process, CAMERA_BLOCKED) == static_cast<uint32_t>(freeCursor), reason);
        require(readWord(process, ERRORS) == 0, "wrong IME/cursor API arguments");
    };
    auto focus = [&](bool enabled) {
        // The original TextInputFocusChange handler also associates/detaches its cached HIMC.
        writeWord(process, CONTEXT, enabled ? 0x1234 : 0);
        t7::writeClientMemory(process, IME + 8, {static_cast<unsigned char>(enabled)});
    };
    writeWord(process, worldFixture::STATE + 0x38, 0);
    writeWord(process, WINDOW_GLOBAL, WINDOW); writeWord(process, IME_GLOBAL, IME);
    expect(0x1234, false, "lobby input changed");
    require(readWord(process, ASSOCIATIONS) == 0 && readWord(process, MESSAGES) == 0, "lobby called input APIs");

    writeWord(process, WINDOW, BASE + 0x172CFE0); // Base vtable during construction.
    writeWord(process, worldFixture::STATE + 0x38, 4);
    expect(0x1234, false, "uninitialized window accepted");
    writeWord(process, IME, BASE + 0x177AE24);
    writeWord(process, WINDOW, BASE + 0x172D034);
    expect(0x1234, false, "wrong IME interface accepted");
    writeWord(process, ASSOCIATE_IMPORT, 0);
    writeWord(process, IME, BASE + 0x177AE48);
    expect(0x1234, false, "unresolved IME import accepted");
    writeWord(process, ASSOCIATE_IMPORT, ASSOCIATE);
    expect(0, false, "battle without chat left the default IME attached");
    const auto detached = readWord(process, ASSOCIATIONS); pollWorld();
    require(detached == 1 && readWord(process, ASSOCIATIONS) == detached, "IME detached every frame");

    focus(true); expect(0x1234, true, "chat did not enable IME and release the camera/mouse");
    const auto opened = readWord(process, ASSOCIATIONS); const auto released = readWord(process, MESSAGES);
    require(opened == detached, "chat focus redundantly re-associated the native IME context");
    pollWorld();
    require(readWord(process, ASSOCIATIONS) == opened && readWord(process, MESSAGES) == released,
        "stable chat focus reset IME composition or resent cursor events");
    focus(false); expect(0, false, "chat close did not restore action/camera input");
    focus(true); expect(0x1234, true, "chat reopen failed after native IME detach");
    focus(false); expect(0, false, "chat reclose failed");

    // Opening chat over an already-free cursor must not capture that cursor on close.
    t7::writeClientMemory(process, WINDOW + 0x114, {1}); writeWord(process, CAMERA_BLOCKED, 1);
    const auto beforeFreeChat = readWord(process, MESSAGES);
    focus(true); expect(0x1234, true, "chat captured an already-free cursor");
    pollWorld();
    focus(false); expect(0, true, "chat stole the previous free-cursor mode");
    require(readWord(process, MESSAGES) == beforeFreeChat, "redundant native cursor events");

    writeWord(process, worldFixture::STATE + 0x38, 0); pump(process);
    writeWord(process, CONTEXT, 0x1234);
    writeWord(process, worldFixture::STATE + 0x38, 4); expect(0, true, "battle reentry did not detach IME");
    pollWorld();
    writeWord(process, worldFixture::STATE + 0x38, 0);
    expect(0x1234, true, "leaving battle did not restore the previous IME context");

    writeWord(process, worldFixture::STATE + 0x38, 4); expect(0, true, "second battle entry");
    focus(true); expect(0x1234, true, "second battle chat");
    writeWord(process, worldFixture::STATE + 0x38, 0);
    focus(false); expect(0, true, "leaving battle recaptured the lobby cursor");

    // A replacement window/service must never inherit a saved context from the old service.
    writeWord(process, CONTEXT, 0x1234);
    writeWord(process, worldFixture::STATE + 0x38, 4); expect(0, true, "third battle entry");
    writeWord(process, IME_GLOBAL, 0); pump(process);
    writeWord(process, WINDOW + 0x138, 2); writeWord(process, CONTEXT, 0x5678);
    writeWord(process, IME_GLOBAL, NEXT_IME); expect(0, true, "replacement IME not detached");
    writeWord(process, worldFixture::STATE + 0x38, 0);
    expect(0x5678, true, "replacement window restored a stale IME context");
    writeWord(process, WINDOW_GLOBAL, 0); writeWord(process, IME_GLOBAL, 0);
    writeWord(process, worldFixture::STATE + 0x38, 4); pump(process);
}
}
