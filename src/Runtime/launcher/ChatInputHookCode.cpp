#include "ChatInputHookCode.h"
#include "RemoteImage.h"
#include "WorldHookCode.h"

namespace t7 {
namespace {
constexpr uint32_t CHAT_INPUT_DATA_OFFSET = 0x400;
constexpr uint32_t WINDOW_RVA = 0x243B70C, WINDOW_VTABLE_RVA = 0x172D034;
constexpr uint32_t IME_RVA = 0x2E249EC, IME_VTABLE_RVA = 0x177AE48;
constexpr uint32_t UI_RVA = 0x24396D0, UI_VTABLE_RVA = 0x177A9F4, RESET_FOCUS_RVA = 0x3FF3D0;
constexpr uint32_t ASSOCIATE_CONTEXT_RVA = 0x16F611C, SEND_MESSAGE_RVA = 0x16F69E8;
constexpr uint32_t CURSOR_MODE_MESSAGE = 0x41E;
}

void verifyChatInputFunctions(HANDLE process, uint32_t imageBase) {
    for (const auto& entry : std::vector<std::pair<uint32_t, Bytes>>{
        {0x12670, {0x8B,0x81,0x38,1,0,0,0xC3}}, // Window service HWND getter.
        {0x400D30, {0x8A,0x41,8,0xC2,4,0}}, // IME interface text-focus flag.
        {RESET_FOCUS_RVA, {0x55,0x8B,0xEC,0x6A,0xFF}},
        {0x3FF3FB, {0x80,0x7E,0x7D,0}}, // UIResetFocus checks the UI interface's text-focus flag.
        {0x7D33B, {0x0F,0x94,0xC0,0x80,0xB9,0x11,1,0,0,0,0x88,0x81,0x10,1,0,0}}
    }) {
        if (readClientMemory(process, imageBase + entry.first, entry.second.size()) != entry.second)
            throw std::runtime_error("chat input function signature mismatch");
    }
}

Bytes buildChatInputHook(uint32_t address, uint32_t imageBase) {
    const auto data = address + CHAT_INPUT_DATA_OFFSET;
    const auto window = data, ime = data + 4, detached = data + 8, context = data + 12;
    const auto chatting = data + 16, previousCursor = data + 20, inBattle = data + 24;
    WorldHookCode c;
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    std::vector<size_t> done;
    auto nonzero = [&](unsigned char modrm) {
        c.emit({0x85,modrm}); done.push_back(c.branch(0x84));
    };
    // Resolve live services each frame; saved identities are only compared, never dereferenced.
    c.emit({0x8B,0x1D}); c.word(imageBase + WINDOW_RVA); nonzero(0xDB);
    c.emit({0x81,0x3B}); c.word(imageBase + WINDOW_VTABLE_RVA); done.push_back(c.branch(0x85));
    c.emit({0x8B,0xBB}); c.word(0x138); nonzero(0xFF); // EDI HWND, EBX window service.
    c.emit({0x8B,0x35}); c.word(imageBase + IME_RVA); nonzero(0xF6);
    c.emit({0x81,0x3E}); c.word(imageBase + IME_VTABLE_RVA); done.push_back(c.branch(0x85));
    c.emit({0x83,0x7E,4,0}); done.push_back(c.branch(0x84));
    for (uint32_t import : {ASSOCIATE_CONTEXT_RVA, SEND_MESSAGE_RVA}) {
        c.emit({0x83,0x3D}); c.word(imageBase + import); c.emit({0}); done.push_back(c.branch(0x84));
    }
    c.emit({0x3B,0x3D}); c.word(window); const auto newWindow = c.branch(0x85);
    c.emit({0x3B,0x35}); c.word(ime); const auto sameServices = c.branch(0x84);
    c.bind(newWindow);
    c.emit({0x89,0x3D}); c.word(window); c.emit({0x89,0x35}); c.word(ime);
    c.store(detached, 0); c.store(context, 0); c.store(chatting, 0); c.store(inBattle, 0);
    c.bind(sameServices);

    auto restoreIme = [&]() {
        // Restore only the association this hook removed; the native focus event owns IME open status.
        c.emit({0xA1}); c.word(context); c.emit({0x85,0xC0}); const auto noContext = c.branch(0x84);
        c.emit({0x50,0x57,0xFF,0x15}); c.word(imageBase + ASSOCIATE_CONTEXT_RVA);
        c.store(context, 0); c.bind(noContext); c.store(detached, 0);
    };
    auto cursorMode = [&]() {
        // EAX free/captured. Native dispatch controls clipping, recentering, UI cursor and camera together.
        c.emit({0x6A,0,0x50,0x68}); c.word(CURSOR_MODE_MESSAGE);
        c.emit({0x57,0xFF,0x15}); c.word(imageBase + SEND_MESSAGE_RVA);
    };
    c.emit({0xA1}); c.word(imageBase + 0x2E405F8);
    c.emit({0x85,0xC0}); const auto noActor = c.branch(0x84);
    c.emit({0x83,0x78,0x38,4}); const auto outsideBattle = c.branch(0x85);
    c.emit({0x83,0x3D}); c.word(inBattle); c.emit({0}); const auto alreadyInBattle = c.branch(0x85);
    c.emit({0x8B,0x0D}); c.word(imageBase + UI_RVA); nonzero(0xC9);
    c.emit({0x81,0x39}); c.word(imageBase + UI_VTABLE_RVA); done.push_back(c.branch(0x85));
    // Complete the preparation-to-GAME handoff through the original UI focus event, not just the IME flag.
    c.call(imageBase + RESET_FOCUS_RVA);
    c.store(inBattle, 1); c.store(detached, 0); c.store(chatting, 0);
    c.emit({0x33,0xC0}); cursorMode(); // Reconcile camera subscribers even if the cursor flag is already zero.
    c.bind(alreadyInBattle);
    // TextInputFocusChange already gates the original keyboard dispatcher, including Shift and Enter.
    c.emit({0x80,0x7E,8,0}); const auto noFocus = c.branch(0x84);
    // Native focus gain already restored its HIMC; re-associating here could disturb the first composition.
    c.store(context, 0); c.store(detached, 0);
    c.emit({0x83,0x3D}); c.word(chatting); c.emit({0}); const auto alreadyChatting = c.branch(0x85);
    c.emit({0x0F,0xB6,0x83}); c.word(0x114); c.emit({0xA3}); c.word(previousCursor);
    c.store(chatting, 1); c.bind(alreadyChatting);
    c.emit({0x80,0xBB}); c.word(0x114); c.emit({0}); done.push_back(c.branch(0x84));
    c.emit({0x33,0xC0}); cursorMode(); done.push_back(c.jump());

    c.bind(noFocus);
    c.emit({0x83,0x3D}); c.word(detached); c.emit({0}); const auto alreadyDetached = c.branch(0x85);
    c.store(detached, 1);
    c.emit({0x6A,0,0x57,0xFF,0x15}); c.word(imageBase + ASSOCIATE_CONTEXT_RVA);
    c.emit({0xA3}); c.word(context); c.bind(alreadyDetached);
    c.emit({0x83,0x3D}); c.word(chatting); c.emit({0}); done.push_back(c.branch(0x84));
    c.store(chatting, 0);
    c.emit({0xA1}); c.word(previousCursor);
    c.emit({0x38,0x83}); c.word(0x114); done.push_back(c.branch(0x84));
    cursorMode(); done.push_back(c.jump());

    c.bind(noActor); c.bind(outsideBattle);
    restoreIme();
    c.store(chatting, 0); c.store(inBattle, 0); // Lobby/menu transitions own the cursor after leaving GAME.
    for (auto branch : done) c.bind(branch);
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5,0x61,0x9D,0xC3});
    if (c.bytes.size() > 0x1000) throw std::runtime_error("chat input hook overflow");
    return c.bytes;
}
}
