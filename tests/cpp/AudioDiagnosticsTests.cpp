#include "../../src/Runtime/launcher/AudioDiagnostics.h"
#include "../../src/Runtime/launcher/AudioSettings.h"
#include <mmreg.h>
#include <iostream>
#include <regex>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
void verifyFormats() {
    WAVEFORMATEX pcm{};
    pcm.wFormatTag = WAVE_FORMAT_PCM; pcm.nChannels = 2; pcm.nSamplesPerSec = 44100;
    pcm.wBitsPerSample = 16; pcm.nBlockAlign = 4;
    const auto description = t7::describeAudioFormat(&pcm, sizeof(pcm));
    require(description == "formatTag=0x1; sampleRate=44100; channels=2; bitsPerSample=16; blockAlign=4", "PCM audio format");
    require(t7::describeAudioFormat(&pcm, 16) == description, "legacy PCM format");
    require(t7::describeAudioFormat(nullptr, 0).find("unknown") == 0, "missing audio format fabricated");
    require(t7::describeAudioFormat(&pcm, 15).find("unknown") == 0, "truncated audio format read");
    WAVEFORMATEXTENSIBLE extended{};
    extended.Format = pcm; extended.Format.wFormatTag = WAVE_FORMAT_EXTENSIBLE;
    extended.Format.cbSize = sizeof(extended) - sizeof(pcm);
    extended.Format.nSamplesPerSec = 48000; extended.Format.wBitsPerSample = 32; extended.Format.nBlockAlign = 8;
    extended.Samples.wValidBitsPerSample = 24; extended.dwChannelMask = 3;
    extended.SubFormat = {1, 0, 0x10, {0x80, 0, 0, 0xAA, 0, 0x38, 0x9B, 0x71}};
    const auto format = t7::describeAudioFormat(&extended, sizeof(extended));
    require(format.find("sampleRate=48000; channels=2; bitsPerSample=32; blockAlign=8; validBits=24; channelMask=0x3") != std::string::npos,
        "mix format lost sample rate, valid bits or channel layout");
    require(format.find("subFormat={00000001-0000-0010-8000-00AA00389B71}") != std::string::npos, "extensible subtype missing");
    require(t7::describeAudioFormat(&extended, sizeof(extended) - 1).find("unknown") == 0, "truncated extensible audio format read");
    extended.Format.cbSize = 0;
    require(t7::describeAudioFormat(&extended, sizeof(extended)).find("unknown") == 0, "missing extension size accepted");
}
void verifyEffects() {
    PROPVARIANT value{};
    require(t7::describeSystemEffects(S_OK, value).find("unknown") == 0, "missing effects property treated as disabled");
    value.vt = VT_UI4;
    require(t7::describeSystemEffects(S_OK, value) == "enabled", "system effects enabled flag reversed");
    value.ulVal = 1;
    require(t7::describeSystemEffects(S_OK, value) == "disabled", "system effects disabled flag reversed");
    value.ulVal = 2;
    require(t7::describeSystemEffects(S_OK, value).find("unknown") == 0, "unsupported effects flag accepted");
    require(t7::describeSystemEffects(E_ACCESSDENIED, value) == "unknown; hr=0x80070005", "effects query error lost HRESULT");
    value.vt = VT_BOOL; value.boolVal = VARIANT_FALSE;
    require(t7::describeSystemEffects(S_OK, value).find("unknown") == 0, "wrong effects variant accepted");
}
void verifyModuleVersions() {
    for (const auto* name : {L"AkSoundEngineDLL.dll", L"XAudio2_7.dll", L"xaudio2_9.dll", L"AudioDevProps2.dll",
                            L"ASM.Plugin.AudioDevProps2.dll", L"NahimicOSD.dll"})
        require(t7::isAudioDiagnosticModule(name), "audio module filter missed relevant DLL");
    require(!t7::isAudioDiagnosticModule(L"kernel32.dll"), "unrelated module selected for audio diagnostics");
    require(t7::describeAudioModuleVersion(nullptr) == "version=unknown; reason=no-file-handle", "missing module handle accepted");
    require(t7::describeAudioModuleVersion(INVALID_HANDLE_VALUE).find("unknown") != std::string::npos, "invalid module handle accepted");
    wchar_t path[32768]{};
    const auto length = GetModuleFileNameW(GetModuleHandleW(L"kernel32.dll"), path, static_cast<DWORD>(std::size(path)));
    require(length && length < std::size(path), "system module path fixture");
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, 0, nullptr);
    require(file != INVALID_HANDLE_VALUE, "system module file fixture");
    std::string version;
    try { version = t7::describeAudioModuleVersion(file); }
    catch (...) { CloseHandle(file); throw; }
    require(CloseHandle(file) != 0, "module fixture close");
    require(std::regex_match(version, std::regex(R"(version=\d+\.\d+\.\d+\.\d+)")), "audio module version missing or exposed a path");
}
}
bool verifyAudioDiagnostics() {
    try {
        verifyFormats(); verifyEffects(); verifyModuleVersions();
        t7::AudioValues values; values.musicMuted = 1; values.effectsVolume = .25f;
        require(t7::describeAudioValues(values) == "musicMuted=1; musicVolume=1; effectsMuted=0; effectsVolume=0.25", "audio values log format");
        std::vector<std::string> lines;
        t7::logAudioEnvironment([&](std::string line) { lines.push_back(std::move(line)); });
        require(lines.size() >= 4 && lines.front().find("Audio diagnostics begin;") == 0
            && lines.back() == "Audio diagnostics end; playbackStatus=not-observed", "audio environment probe lost completion markers");
        bool services = false, endpoints = false;
        for (const auto& line : lines) {
            services = services || line.find("Audio service") == 0;
            endpoints = endpoints || line.find("Audio endpoint") == 0;
            require(line.find_first_of("\r\n") == std::string::npos, "audio diagnostics emitted multiple lines per record");
        }
        require(services && endpoints, "audio environment probe skipped a diagnostic section");
        t7::logAudioEnvironment({});
        std::cout << "Audio diagnostic format, effects, module version and read-only environment tests passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Audio diagnostics: " << error.what() << '\n'; return false;
    }
}
