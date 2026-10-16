#pragma once
#include "ClientAdaptation.h"
#include "../core/DiagnosticLog.h"
#include <optional>

namespace t7r {
struct AudioResourcePaths {
    std::wstring wide;
    std::string ansi;
    UINT codePage = 0;
};
struct AudioResourcePatch {
    Bytes storage;
    std::vector<MemoryPatch> patches;
};
constexpr uint32_t AUDIO_PATH_CODE_OFFSET = 0x1000, AUDIO_PATH_ALLOCATION_SIZE = 0x2000;
AudioResourcePaths buildAudioResourcePaths(const fs::path& directory, UINT codePage = CP_ACP);
std::optional<AudioResourcePaths> prepareAudioResourcePaths(const fs::path& clientBin,
    const DiagnosticLogCallback& log = {});
AudioResourcePatch buildAudioResourcePatch(uint32_t address, uint32_t imageBase, const AudioResourcePaths& paths);
// The owned client must remain suspended until installation succeeds. On failure,
// its owner terminates it; published storage must never be freed while it can run.
void installAudioResourcePaths(HANDLE process, uint32_t imageBase, const AudioResourcePaths& paths,
    const DiagnosticLogCallback& log = {});
}
