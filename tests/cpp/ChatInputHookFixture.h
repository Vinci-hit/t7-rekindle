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
constexpr uint32_t FOCUS_RESETS = CONTEXT + 20, KEYBOARD_BLOCKED = CONTEXT + 24;
constexpr uint32_t ASSOCIATE = BASE + 0xD400, SEND_MESSAGE = BASE + 0xD500;
constexpr uint32_t UI = BASE + 0xD600, RESET_FOCUS = BASE + 0x3FF3D0;
constexpr uint32_t CAMERA = BASE + 0xD800, CAMERA_GLOBAL = BASE + 0x2E35368;
constexpr uint32_t CAMERA_VTABLE = BASE + 0x1785478;
constexpr uint32_t UI_GLOBAL = BASE + 0x24396D0, UI_VTABLE = BASE + 0x177A9F4;
constexpr uint32_t WINDOW_GLOBAL = BASE + 0x243B70C, IME_GLOBAL = BASE + 0x2E249EC;
constexpr uint32_t ASSOCIATE_IMPORT = BASE + 0x16F611C, SEND_MESSAGE_IMPORT = BASE + 0x16F69E8;

inline void prepare(HANDLE process) {
    for (const auto& item : std::vector<std::pair<uint32_t, uint32_t>>{
        {WINDOW,BASE+0x172D034}, {WINDOW+0x138,1}, {IME,BASE+0x177AE48}, {IME+4,0x1234},
        {NEXT_IME,BASE+0x177AE48}, {NEXT_IME+4,0x5678}, {CONTEXT,0x1234},
        {ASSOCIATE_IMPORT,ASSOCIATE}, {SEND_MESSAGE_IMPORT,SEND_MESSAGE}, {UI,UI_VTABLE},
        {CAMERA,CAMERA_VTABLE}
    }) writeWord(process, item.first, item.second);
    t7::writeClientMemory(process, CAMERA + 0x61, {1});
    // Signature anchors for the native window getter, text-focus getter and cursor mode handler.
    t7::writeClientMemory(process, BASE + 0x12670, {0x8B,0x81,0x38,1,0,0,0xC3});
    t7::writeClientMemory(process, BASE + 0x400D30, {0x8A,0x41,8,0xC2,4,0});
    t7::writeClientMemory(process, BASE + 0x7D33B,
        {0x0F,0x94,0xC0,0x80,0xB9,0x11,1,0,0,0,0x88,0x81,0x10,1,0,0});
    t7::writeClientMemory(process, BASE + 0x3FF3FB, {0x80,0x7E,0x7D,0});
    t7::writeClientMemory(process, BASE + 0x4B2C70, {0x80,0x79,0x61,1,0x75,9,0x80,0x79,0x60,0});
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
    c.bytes.clear();
    c.emit({0x55,0x8B,0xEC,0x6A,0xFF,0x58});
    c.emit({0x81,0xF9}); c.word(UI); check();
    c.emit({0xFF,0x05}); c.word(FOCUS_RESETS);
    c.emit({0xE9}); c.word(BASE + 0xD700 - (RESET_FOCUS + static_cast<uint32_t>(c.bytes.size()) + 4));
    t7::writeClientMemory(process, RESET_FOCUS, c.bytes);
    c.bytes.clear();
    // UIResetFocus owns the native focus event: both IME and keyboard subscribers see focus loss.
    c.emit({0x80,0x79,0x7D,0}); const auto notFocused = c.branch(0x84);
    c.emit({0xC6,0x41,0x7D,0,0xA1}); c.word(IME_GLOBAL);
    c.emit({0xC6,0x40,8,0}); c.store(CONTEXT, 0); c.store(KEYBOARD_BLOCKED, 0);
    c.bind(notFocused); c.emit({0xD9,0xEE,0x0F,0x57,0xC0,0x5D,0xC3});
    t7::writeClientMemory(process, BASE + 0xD700, c.bytes);
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
        t7::writeClientMemory(process, UI + 0x7D, {static_cast<unsigned char>(enabled)});
        writeWord(process, KEYBOARD_BLOCKED, enabled ? 1 : 0);
    };
    auto freeCursor = [&]() {
        writeWord(process, CAMERA_BLOCKED, 1);
        // Publish last: the running frame may immediately capture this flag again during chat.
        const unsigned char value = 1; SIZE_T written = 0;
        require(WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(WINDOW + 0x114)),
            &value, sizeof(value), &written) && written == sizeof(value), "cursor event write failed");
    };
    writeWord(process, worldFixture::STATE + 0x38, 0);
    writeWord(process, WINDOW_GLOBAL, WINDOW); writeWord(process, IME_GLOBAL, IME); writeWord(process, UI_GLOBAL, UI);
    writeWord(process, CAMERA_GLOBAL, CAMERA);
    expect(0x1234, false, "lobby input changed");
    require(readWord(process, ASSOCIATIONS) == 0 && readWord(process, MESSAGES) == 0
        && readWord(process, FOCUS_RESETS) == 0, "lobby called input APIs");

    writeWord(process, worldFixture::STATE + 0x38, 2); focus(true); freeCursor();
    expect(0x1234, true, "preparation lost text focus or free cursor");

    writeWord(process, WINDOW, BASE + 0x172CFE0); // Base vtable during construction.
    writeWord(process, worldFixture::STATE + 0x38, 4);
    expect(0x1234, true, "uninitialized window accepted");
    writeWord(process, IME, BASE + 0x177AE24);
    writeWord(process, WINDOW, BASE + 0x172D034);
    expect(0x1234, true, "wrong IME interface accepted");
    writeWord(process, ASSOCIATE_IMPORT, 0);
    writeWord(process, IME, BASE + 0x177AE48);
    expect(0x1234, true, "unresolved IME import accepted");
    writeWord(process, UI_GLOBAL, 0); writeWord(process, ASSOCIATE_IMPORT, ASSOCIATE);
    expect(0x1234, true, "missing UI service accepted");
    writeWord(process, UI, BASE + 0x177A938); writeWord(process, UI_GLOBAL, UI);
    expect(0x1234, true, "uninitialized UI service accepted");
    writeWord(process, CAMERA_GLOBAL, 0); writeWord(process, UI, UI_VTABLE);
    expect(0x1234, true, "missing camera service accepted");
    writeWord(process, CAMERA, BASE + 0x172D4A0); writeWord(process, CAMERA_GLOBAL, CAMERA);
    expect(0x1234, true, "uninitialized camera service accepted");
    writeWord(process, CAMERA, CAMERA_VTABLE);
    expect(0, false, "GAME entry kept preparation focus, IME or camera lock");
    require(readWord(process, KEYBOARD_BLOCKED) == 0 && readWord(process, FOCUS_RESETS) == 1,
        "GAME entry did not reset native keyboard/text focus");
    const auto detached = readWord(process, ASSOCIATIONS); pollWorld();
    require(detached == 1 && readWord(process, ASSOCIATIONS) == detached, "IME detached every frame");

    focus(true); expect(0x1234, false, "chat did not retain IME and mouse camera control");
    const auto opened = readWord(process, ASSOCIATIONS); const auto released = readWord(process, MESSAGES);
    require(opened == detached, "chat focus redundantly re-associated the native IME context");
    pollWorld();
    require(readWord(process, ASSOCIATIONS) == opened && readWord(process, MESSAGES) == released,
        "stable chat focus reset IME composition or resent cursor events");
    require(readWord(process, KEYBOARD_BLOCKED) == 1 && readWord(process, FOCUS_RESETS) == 1,
        "chat camera capture cleared text focus or enabled gameplay keys");
    freeCursor(); expect(0x1234, false, "native cursor event during chat disabled the camera");
    focus(false); expect(0, false, "chat close did not restore action/camera input");
    focus(true); expect(0x1234, false, "chat reopen failed after native IME detach");
    focus(false); expect(0, false, "chat reclose failed");

    // Chat temporarily captures an already-free cursor, but restores that mode on close.
    freeCursor(); expect(0, true, "ordinary free-cursor mode was captured outside chat");
    const auto beforeFreeChat = readWord(process, MESSAGES);
    focus(true); expect(0x1234, false, "chat over a free cursor did not enable camera control");
    pollWorld();
    focus(false); expect(0, true, "chat stole the previous free-cursor mode");
    require(readWord(process, MESSAGES) == beforeFreeChat + 2, "redundant native cursor events");

    auto captureCursor = [&]() {
        writeWord(process, CAMERA_BLOCKED, 0);
        t7::writeClientMemory(process, WINDOW + 0x114, {0});
    };
    auto menu = [&](bool opened, bool textFocus) {
        // Complete one frame, then publish the native menu event before allowing the next frame.
        writeWord(process, worldFixture::FRAME_GATE, 2);
        const auto deadline = GetTickCount64() + 3000;
        while (readWord(process, worldFixture::FRAME_GATE) != 1) {
            require(GetTickCount64() < deadline, "menu event frame timeout"); Sleep(1);
        }
        t7::writeClientMemory(process, CAMERA + 0x61, {static_cast<unsigned char>(!opened)});
        focus(textFocus);
        if (opened) freeCursor(); else captureCursor();
        writeWord(process, worldFixture::FRAME_GATE, 0);
    };
    auto closeMenu = [&]() {
        menu(false, false);
        expect(0, false, "closing the menu did not return camera control");
    };
    menu(true, true);
    expect(0x1234, true, "menu text focus was treated as chat and lost its cursor");
    const auto menuMessages = readWord(process, MESSAGES), menuAssociations = readWord(process, ASSOCIATIONS);
    pollWorld();
    require(readWord(process, MESSAGES) == menuMessages && readWord(process, ASSOCIATIONS) == menuAssociations
        && readWord(process, KEYBOARD_BLOCKED) == 1 && readWord(process, FOCUS_RESETS) == 1,
        "stable menu changed focus, IME composition or cursor ownership");
    // Blur the settings field while the parent Esc panel remains open.
    focus(false); expect(0, true, "menu focus loss restored a stale chat cursor");
    closeMenu();

    for (bool textFocus : {false, true}) {
        focus(true); expect(0x1234, false, "chat before opening the menu");
        menu(true, textFocus);
        expect(textFocus ? 0x1234 : 0, true, "menu opened over chat lost its cursor");
        captureCursor();
        expect(textFocus ? 0x1234 : 0, true, "open menu did not recover a hidden cursor");
        closeMenu();
    }
    focus(true); expect(0x1234, false, "chat stopped capturing after closing the menu");
    focus(false); expect(0, false, "chat restored an obsolete menu cursor after closing");
    require((readWord(process, CAMERA + 0x61) & 0xFF) == 1,
        "chat adapter changed native operator activation");

    writeWord(process, worldFixture::STATE + 0x38, 0); pump(process);
    writeWord(process, CONTEXT, 0x1234);
    writeWord(process, worldFixture::STATE + 0x38, 4); expect(0, false, "battle reentry did not detach IME/capture camera");
    pollWorld();
    require(readWord(process, FOCUS_RESETS) == 2, "battle entry normalization repeated each frame");
    freeCursor();
    writeWord(process, worldFixture::STATE + 0x38, 0);
    expect(0x1234, true, "leaving battle did not restore the previous IME context");

    // A stale camera subscriber is independent of the window's desired cursor flag.
    writeWord(process, worldFixture::STATE + 0x38, 2); pump(process);
    t7::writeClientMemory(process, WINDOW + 0x114, {0});
    writeWord(process, CAMERA_BLOCKED, 1);
    writeWord(process, worldFixture::STATE + 0x38, 4); expect(0, false, "second battle entry");
    focus(true); expect(0x1234, false, "second battle chat");
    writeWord(process, worldFixture::STATE + 0x38, 0);
    pump(process); freeCursor();
    focus(false); expect(0, true, "leaving battle recaptured the lobby cursor");

    // A replacement window/service must never inherit a saved context from the old service.
    writeWord(process, CONTEXT, 0x1234);
    writeWord(process, worldFixture::STATE + 0x38, 4); expect(0, false, "third battle entry");
    writeWord(process, IME_GLOBAL, 0); pump(process);
    writeWord(process, WINDOW + 0x138, 2); writeWord(process, CONTEXT, 0x5678);
    writeWord(process, IME_GLOBAL, NEXT_IME); expect(0, false, "replacement IME not detached");
    writeWord(process, worldFixture::STATE + 0x38, 0);
    expect(0x5678, false, "replacement window restored a stale IME context");

    // A menu open across the countdown must retain its text focus and cursor on GAME entry.
    writeWord(process, IME_GLOBAL, IME);
    writeWord(process, worldFixture::STATE + 0x38, 2); pump(process);
    menu(true, true);
    const auto resetsBeforeMenuEntry = readWord(process, FOCUS_RESETS);
    writeWord(process, worldFixture::STATE + 0x38, 4);
    expect(0x1234, true, "GAME entry stole the open menu focus or cursor");
    require(readWord(process, FOCUS_RESETS) == resetsBeforeMenuEntry && readWord(process, KEYBOARD_BLOCKED) == 1,
        "GAME entry reset menu keyboard focus");
    closeMenu();
    require(readWord(process, FOCUS_RESETS) == resetsBeforeMenuEntry,
        "closing the entry menu triggered a delayed focus reset");

    writeWord(process, worldFixture::STATE + 0x38, 0); pump(process);
    writeWord(process, CONTEXT, 0x1234);
    writeWord(process, worldFixture::STATE + 0x38, 4);
    expect(0, false, "IME association was not detached before menu ownership test");
    menu(true, false);
    expect(0x1234, true, "menu without text focus did not restore the hook-owned IME association");
    const auto restoredAssociations = readWord(process, ASSOCIATIONS);
    pollWorld();
    require(readWord(process, ASSOCIATIONS) == restoredAssociations, "menu restored IME every frame");
    closeMenu();
    writeWord(process, WINDOW_GLOBAL, 0); writeWord(process, IME_GLOBAL, 0); writeWord(process, UI_GLOBAL, 0);
    writeWord(process, CAMERA_GLOBAL, 0);
    writeWord(process, worldFixture::STATE + 0x38, 4); pump(process);
}
}
