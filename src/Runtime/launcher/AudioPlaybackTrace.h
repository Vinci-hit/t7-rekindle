#pragma once
#include "../core/DiagnosticLog.h"
#include <array>
#include <cstddef>

namespace t7 {
enum class AudioPlaybackApi : uint32_t {
    LoadBank = 1, UnloadBank, PostEventName, PostEventId, GetId, RtpcName, RtpcId, Init
};
struct AudioPlaybackRecord {
    uint32_t sequence, api, threadId, startedMs, completedMs, result, bankId, flags, lastError;
    uint32_t arguments[8];
    char name[128];
};
struct AudioPlaybackQueue { uint32_t lock, read, written, dropped; };
inline constexpr uint32_t AUDIO_PLAYBACK_CAPACITY = 256;
inline constexpr uint32_t AUDIO_PLAYBACK_CODE_OFFSET = 0x10000;
inline constexpr uint32_t AUDIO_PLAYBACK_ALLOCATION_SIZE = 0x20000;
inline constexpr uint32_t AUDIO_PLAYBACK_NAME = 1, AUDIO_PLAYBACK_TRUNCATED = 2, AUDIO_PLAYBACK_BANK_ID = 4;
static_assert(sizeof(AudioPlaybackRecord) == 196);
static_assert(offsetof(AudioPlaybackRecord, arguments) == 36 && offsetof(AudioPlaybackRecord, name) == 68);
static_assert(sizeof(AudioPlaybackQueue) + AUDIO_PLAYBACK_CAPACITY * sizeof(AudioPlaybackRecord)
              < AUDIO_PLAYBACK_CODE_OFFSET);

// The supported client imports these x86 cdecl overloads, including synchronous LoadBank.
Bytes buildAudioPlaybackHook(uint32_t queue, uint32_t original, uint32_t getTickCount, AudioPlaybackApi api);
std::string describeAudioPlayback(const AudioPlaybackRecord& record);
void drainAudioPlayback(HANDLE process, uint32_t queue, uint32_t& dropped, const DiagnosticLogCallback& log);

class AudioPlaybackTrace final {
public:
    void arm(HANDLE process, const DiagnosticLogCallback& log);
    bool handleBreakpoint(DWORD threadId, uintptr_t address);
    void poll();
    void debugString(const OUTPUT_DEBUG_STRING_INFO& info);
private:
    HANDLE process_ = nullptr;
    DiagnosticLogCallback log_;
    uint32_t entry_ = 0, allocation_ = 0, dropped_ = 0;
    unsigned char originalEntry_ = 0;
    bool armed_ = false, failed_ = false;
    void install();
};
}
