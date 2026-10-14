#include "AudioPlaybackTrace.h"
#include <cstring>

namespace t7r {
namespace {
class Code {
public:
    Bytes bytes;
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void word(uint32_t value) {
        for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
    }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        const auto position = bytes.size(); word(0); return position;
    }
    void bind(size_t position, size_t target) {
        const auto delta = static_cast<uint32_t>(target - position - 4);
        std::memcpy(bytes.data() + position, &delta, 4);
    }
    void call(uint32_t address) { emit({0xB8}); word(address); emit({0xFF,0xD0}); }
    void store(unsigned char offset, uint32_t value) { emit({0xC7,0x43,offset}); word(value); }
};
unsigned argumentCount(AudioPlaybackApi api) {
    switch (api) {
    case AudioPlaybackApi::LoadBank: case AudioPlaybackApi::UnloadBank: return 3;
    case AudioPlaybackApi::PostEventName: case AudioPlaybackApi::PostEventId: return 8;
    case AudioPlaybackApi::GetId: return 1;
    case AudioPlaybackApi::RtpcName: case AudioPlaybackApi::RtpcId: case AudioPlaybackApi::Init: return 6;
    default: throw std::runtime_error("unsupported audio playback trace API");
    }
}
bool hasName(AudioPlaybackApi api) {
    return api == AudioPlaybackApi::LoadBank || api == AudioPlaybackApi::UnloadBank
        || api == AudioPlaybackApi::PostEventName || api == AudioPlaybackApi::GetId || api == AudioPlaybackApi::RtpcName;
}
}
Bytes buildAudioPlaybackHook(uint32_t queue, uint32_t original, uint32_t getTickCount, AudioPlaybackApi api) {
    if (!queue || queue > UINT32_MAX - AUDIO_PLAYBACK_ALLOCATION_SIZE || !original || !getTickCount)
        throw std::runtime_error("audio playback trace outside x86 range");
    const auto count = argumentCount(api);
    constexpr auto size = static_cast<uint32_t>(sizeof(AudioPlaybackRecord));
    Code c;
    // All per-call state stays on the caller's stack, not in shared scratch storage.
    c.emit({0x9C,0x60,0x8B,0xEC,0x81,0xEC}); c.word(size);
    c.emit({0x8B,0xDC,0x8B,0xFB,0x33,0xC0,0xB9}); c.word(size / 4); c.emit({0xFC,0xF3,0xAB});
    c.store(4, static_cast<uint32_t>(api));
    c.emit({0x64,0xA1}); c.word(0x24); c.emit({0x89,0x43,0x08});
    c.emit({0x64,0xA1}); c.word(0x34); c.emit({0x89,0x43,0x20});
    c.call(getTickCount); c.emit({0x89,0x43,0x0C});
    c.emit({0x8D,0x75,0x28,0x8D,0x7B,0x24,0xB9}); c.word(count); c.emit({0xF3,0xA5});
    if (hasName(api)) {
        c.emit({0x8B,0x73,0x24,0x85,0xF6}); const auto noName = c.branch(0x84);
        c.store(28, AUDIO_PLAYBACK_NAME);
        c.emit({0x8D,0x7B,0x44,0xB9}); c.word(127);
        const auto copy = c.bytes.size();
        c.emit({0xAC,0xAA,0x84,0xC0}); const auto ended = c.branch(0x84);
        c.emit({0x49}); const auto more = c.branch(0x85); c.bind(more, copy);
        c.emit({0x83,0x4B,0x1C,static_cast<unsigned char>(AUDIO_PLAYBACK_TRUNCATED)});
        c.bind(ended, c.bytes.size()); c.bind(noName, c.bytes.size());
    }
    c.emit({0x8B,0x43,0x20,0x64,0xA3}); c.word(0x34);
    for (unsigned i = count; i; --i) c.emit({0xFF,0x75,static_cast<unsigned char>(40 + (i - 1) * 4)});
    c.call(original);
    c.emit({0x89,0x43,0x14,0x89,0x45,0x1C,0x9C,0x8F,0x45,0x20});
    c.emit({0x8D,0xA5}); c.word(0u - size);
    c.emit({0x64,0xA1}); c.word(0x34); c.emit({0x89,0x43,0x20});
    c.call(getTickCount); c.emit({0x89,0x43,0x10});
    if (api == AudioPlaybackApi::LoadBank) {
        // A failed call need not initialize out_bankID; never dereference it on failure.
        c.emit({0x83,0x7B,0x14,0x01}); const auto failed = c.branch(0x85);
        c.emit({0x8B,0x43,0x2C,0x85,0xC0}); const auto absent = c.branch(0x84);
        c.emit({0x8B,0x00,0x89,0x43,0x18,0x83,0x4B,0x1C,static_cast<unsigned char>(AUDIO_PLAYBACK_BANK_ID)});
        c.bind(failed, c.bytes.size()); c.bind(absent, c.bytes.size());
    }
    // Nonblocking multi-producer queue: diagnostics drop instead of stalling an audio thread.
    c.emit({0x33,0xC0,0xBA}); c.word(1);
    c.emit({0xF0,0x0F,0xB1,0x15}); c.word(queue); const auto busy = c.branch(0x85);
    c.emit({0xA1}); c.word(queue + 8); c.emit({0x8B,0xD0,0x2B,0x15}); c.word(queue + 4);
    c.emit({0x81,0xFA}); c.word(AUDIO_PLAYBACK_CAPACITY); const auto full = c.branch(0x83);
    c.emit({0x8D,0x50,0x01,0x89,0x13,0x25}); c.word(AUDIO_PLAYBACK_CAPACITY - 1);
    c.emit({0x69,0xC0}); c.word(size); c.emit({0x05}); c.word(queue + sizeof(AudioPlaybackQueue));
    c.emit({0x8B,0xF8,0x8B,0xF3,0xB9}); c.word(size / 4); c.emit({0xFC,0xF3,0xA5});
    c.emit({0xFF,0x05}); c.word(queue + 8); const auto published = c.branch();
    c.bind(full, c.bytes.size()); c.emit({0xF0,0xFF,0x05}); c.word(queue + 12);
    c.bind(published, c.bytes.size());
    c.emit({0xC7,0x05}); c.word(queue); c.word(0); const auto released = c.branch();
    c.bind(busy, c.bytes.size()); c.emit({0xF0,0xFF,0x05}); c.word(queue + 12);
    c.bind(released, c.bytes.size());
    c.emit({0x8B,0x43,0x20,0x64,0xA3}); c.word(0x34);
    c.emit({0x8B,0xE5,0x61,0x9D,0xC3});
    return c.bytes;
}
}
