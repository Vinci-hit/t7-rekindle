#include "AudioResourcePaths.h"
#include "RemoteImage.h"
#include "ProcessCleanup.h"
#include <algorithm>
#include <cstring>

namespace t7r {
namespace {
constexpr uint32_t ANSI_OFFSET = 0x400;
void appendWord(Bytes& bytes, uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
}
Bytes push(uint32_t value) { Bytes bytes{0x68}; appendWord(bytes, value); return bytes; }
std::vector<MemoryPatch> originals(uint32_t imageBase) {
    if (!imageBase || imageBase > UINT32_MAX - 0x1779A60)
        throw std::runtime_error("audio resource image outside x86 range");
    auto media = push(imageBase + 0x17799F0);
    media.insert(media.end(), {0xFF,0x15}); appendWord(media, imageBase + 0x16F6080);
    const Bytes length{0x6A,0x1C,0xC7,0x45,0xE8,0,0,0,0};
    const auto path = push(imageBase + 0x1779A34);
    return {{0x3E5FDD,media,{}}, {0x3E60EA,length,{}}, {0x3E60FA,path,{}},
            {0x3E671B,length,{}}, {0x3E672B,path,{}}};
}
void verifyBanks(const AudioResourcePaths& paths) {
    WIN32_FIND_DATAA data{};
    const auto pattern = paths.ansi + "*.bnk";
    const auto search = FindFirstFileA(pattern.c_str(), &data);
    if (search == INVALID_HANDLE_VALUE) throw std::runtime_error(errorText("audio Bank directory enumeration"));
    bool found = false;
    DWORD error = ERROR_SUCCESS;
    do {
        if (!(data.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) { found = true; break; }
        if (!FindNextFileA(search, &data)) { error = GetLastError(); break; }
    } while (true);
    if (!FindClose(search)) throw std::runtime_error(errorText("audio Bank directory enumeration cleanup"));
    if (!found) throw std::runtime_error("audio Bank directory contains no readable Bank entry; error=" + std::to_string(error));
}
}
AudioResourcePaths buildAudioResourcePaths(const fs::path& directory, UINT codePage) {
    if (directory.empty() || !directory.is_absolute()) throw std::runtime_error("audio resource directory must be absolute");
    auto wide = directory.lexically_normal().make_preferred().wstring();
    if (wide.find(L'\0') != wide.npos || wide.size() > MAX_PATH - 32)
        throw std::runtime_error("audio resource directory exceeds legacy engine path limit or contains NUL");
    if (wide.back() != L'\\') wide.push_back(L'\\');
    const auto page = codePage == CP_ACP ? GetACP() : codePage;
    const bool unicode = page == CP_UTF8 || page == 54936;
    const DWORD flags = unicode ? WC_ERR_INVALID_CHARS : WC_NO_BEST_FIT_CHARS;
    BOOL substituted = FALSE;
    auto usedDefault = unicode ? nullptr : &substituted;
    const auto count = WideCharToMultiByte(page, flags, wide.data(), static_cast<int>(wide.size()), nullptr, 0, nullptr, usedDefault);
    if (!count) throw std::runtime_error(errorText("audio resource ANSI conversion"));
    std::string ansi(static_cast<size_t>(count), '\0');
    if (WideCharToMultiByte(page, flags, wide.data(), static_cast<int>(wide.size()), ansi.data(), count, nullptr, usedDefault) != count)
        throw std::runtime_error(errorText("audio resource ANSI conversion"));
    if (substituted) throw std::runtime_error("audio resource directory is not representable in the system code page");
    std::wstring roundtrip(wide.size(), L'\0');
    if (MultiByteToWideChar(page, MB_ERR_INVALID_CHARS, ansi.data(), count, roundtrip.data(), static_cast<int>(roundtrip.size()))
            != static_cast<int>(wide.size()) || roundtrip != wide)
        throw std::runtime_error("audio resource ANSI conversion is not lossless");
    return {std::move(wide), std::move(ansi), page};
}
std::optional<AudioResourcePaths> prepareAudioResourcePaths(const fs::path& clientBin, const DiagnosticLogCallback& log) {
    try {
        const auto directory = fs::absolute(clientBin.parent_path() / "Data/Audio/Wwise/Windows").lexically_normal();
        std::error_code error;
        if (!fs::is_directory(directory, error))
            throw std::runtime_error("audio resource directory missing or inaccessible; error=" + std::to_string(error.value()));
        auto paths = buildAudioResourcePaths(directory);
        verifyBanks(paths);
        return paths;
    } catch (const std::runtime_error& error) {
        if (log) log(DiagnosticLogLevel::Warn, "Audio resource paths unavailable; original audio behavior retained; codePage="
            + std::to_string(GetACP()) + "; reason=" + error.what());
        return std::nullopt;
    }
}
AudioResourcePatch buildAudioResourcePatch(uint32_t address, uint32_t imageBase, const AudioResourcePaths& paths) {
    if (!address || address > UINT32_MAX - AUDIO_PATH_ALLOCATION_SIZE)
        throw std::runtime_error("audio resource storage outside x86 range");
    if (paths.wide.empty() || paths.ansi.empty() || paths.wide.back() != L'\\' || paths.ansi.back() != '\\'
        || paths.wide.find(L'\0') != paths.wide.npos || paths.ansi.find('\0') != paths.ansi.npos
        || paths.wide.size() > MAX_PATH - 31 || (paths.wide.size() + 1) * sizeof(wchar_t) > ANSI_OFFSET
        || paths.ansi.size() + 1 > AUDIO_PATH_CODE_OFFSET - ANSI_OFFSET)
        throw std::runtime_error("invalid audio resource path storage");
    AudioResourcePatch result{Bytes(AUDIO_PATH_ALLOCATION_SIZE, 0), originals(imageBase)};
    std::memcpy(result.storage.data(), paths.wide.c_str(), (paths.wide.size() + 1) * sizeof(wchar_t));
    std::memcpy(result.storage.data() + ANSI_OFFSET, paths.ansi.c_str(), paths.ansi.size() + 1);
    auto& media = result.patches.front(); media.replacement = media.expected;
    std::memcpy(media.replacement.data() + 1, &address, 4);
    for (uint32_t index = 0; index < 2; ++index) {
        auto& length = result.patches[1 + index * 2];
        const auto offset = AUDIO_PATH_CODE_OFFSET + index * 0x20;
        auto stub = push(static_cast<uint32_t>(paths.ansi.size()));
        // Replay the complete mov [ebp-18h],0 displaced with push imm8.
        stub.insert(stub.end(), length.expected.begin() + 2, length.expected.end());
        stub.push_back(0xE9);
        appendWord(stub, imageBase + length.rva + static_cast<uint32_t>(length.expected.size())
            - (address + offset + static_cast<uint32_t>(stub.size()) + 4));
        std::copy(stub.begin(), stub.end(), result.storage.begin() + offset);
        length.replacement = {0xE9};
        appendWord(length.replacement, address + offset - (imageBase + length.rva + 5));
        length.replacement.resize(length.expected.size(), 0x90);
        result.patches[2 + index * 2].replacement = push(address + ANSI_OFFSET);
    }
    return result;
}
void installAudioResourcePaths(HANDLE process, uint32_t imageBase, const AudioResourcePaths& paths,
                               const DiagnosticLogCallback& log) {
    for (const auto& patch : originals(imageBase))
        if (readClientMemory(process, imageBase + patch.rva, patch.expected.size()) != patch.expected)
            throw std::runtime_error("audio resource path signature mismatch at RVA=" + std::to_string(patch.rva));
    const auto allocation = VirtualAllocEx(process, nullptr, AUDIO_PATH_ALLOCATION_SIZE, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    if (!allocation) throw std::runtime_error(errorText("audio resource path allocation"));
    bool published = false;
    try {
        const auto pointer = reinterpret_cast<uintptr_t>(allocation);
        if (pointer > UINT32_MAX - AUDIO_PATH_ALLOCATION_SIZE)
            throw std::runtime_error("audio resource allocation outside x86 range");
        const auto patch = buildAudioResourcePatch(static_cast<uint32_t>(pointer), imageBase, paths);
        writeClientMemory(process, pointer, patch.storage);
        DWORD previous = 0;
        if (!VirtualProtectEx(process, allocation, AUDIO_PATH_CODE_OFFSET, PAGE_READONLY, &previous)
            || !VirtualProtectEx(process, reinterpret_cast<void*>(pointer + AUDIO_PATH_CODE_OFFSET),
                AUDIO_PATH_ALLOCATION_SIZE - AUDIO_PATH_CODE_OFFSET, PAGE_EXECUTE_READ, &previous)
            || !FlushInstructionCache(process, allocation, AUDIO_PATH_ALLOCATION_SIZE))
            throw std::runtime_error(errorText("audio resource protection/cache"));
        // applyRemotePatches can fail after publishing an instruction. The owner
        // terminates the suspended client; retain storage even on partial failure.
        published = true;
        applyRemotePatches(process, imageBase, patch.patches);
    } catch (...) {
        cleanupAndRethrow(std::current_exception(), [&] {
            if (!published && !VirtualFreeEx(process, allocation, 0, MEM_RELEASE))
                throw std::runtime_error(errorText("audio resource unpublished storage cleanup"));
        });
    }
    if (log) log(DiagnosticLogLevel::Debug, "Audio absolute resource paths installed; sites=5; codePage="
        + std::to_string(paths.codePage) + "; ansiBytes=" + std::to_string(paths.ansi.size())
        + "; wideChars=" + std::to_string(paths.wide.size()) + "; bankLifecycle=original");
}
}
