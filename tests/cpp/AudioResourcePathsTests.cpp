#include "AudioResourcePathsFixture.h"
#include "../../src/Runtime/launcher/AudioResourcePaths.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include <iostream>

namespace {
using namespace audioPathFixture;
template<class F> void rejects(F action, const char* message) {
    bool rejected = false;
    try { action(); } catch (const std::runtime_error&) { rejected = true; }
    require(rejected, message);
}
void encodingCases() {
    const auto path = t7r::buildAudioResourcePaths(L"C:\\Games With Spaces\\.\\Audio", 1252);
    require(path.wide == L"C:\\Games With Spaces\\Audio\\" && path.ansi == "C:\\Games With Spaces\\Audio\\",
        "absolute path normalization/trailing separator");
    require(t7r::buildAudioResourcePaths(path.wide, 1252).ansi == path.ansi, "duplicate separator");
    const auto chinese = t7r::buildAudioResourcePaths(L"C:\\游戏\\声音", 936);
    require(chinese.ansi.size() > chinese.wide.size() && chinese.codePage == 936, "ANSI length must count bytes");
    const auto unicode = t7r::buildAudioResourcePaths(L"C:\\游戏\\声音", CP_UTF8);
    require(unicode.ansi == u8"C:\\游戏\\声音\\", "UTF-8 code page conversion");
    rejects([] { t7r::buildAudioResourcePaths(L"C:\\游戏", 1252); }, "lossy encoding accepted");
    rejects([] { t7r::buildAudioResourcePaths(L"C:\\∞", 1252); }, "best-fit encoding accepted");
    rejects([] { t7r::buildAudioResourcePaths(L"Data\\Audio"); }, "relative path accepted");
    rejects([] { t7r::buildAudioResourcePaths({}); }, "empty path accepted");
    rejects([] { t7r::buildAudioResourcePaths(std::wstring(L"C:\\a\0b", 6)); }, "embedded NUL accepted");
    rejects([] { t7r::buildAudioResourcePaths(L"C:\\" + std::wstring(226, L'a')); }, "legacy wide path limit lost");
    for (const size_t size : {127u,128u,229u}) {
        const auto value = t7r::buildAudioResourcePaths(L"C:\\" + std::wstring(size - 4, L'a'), 1252);
        require(value.ansi.size() == size, "path length boundary");
        const auto patch = t7r::buildAudioResourcePatch(0x10000000, BASE, value);
        require(patch.patches.size() == 5 && patch.storage.size() == t7r::AUDIO_PATH_ALLOCATION_SIZE, "path patch layout");
        uint32_t length = 0; std::memcpy(&length, patch.storage.data() + t7r::AUDIO_PATH_CODE_OFFSET + 1, 4);
        require(patch.storage[t7r::AUDIO_PATH_CODE_OFFSET] == 0x68 && length == size, "full-width path length missing");
    }
    for (const uint32_t bad : {0u,UINT32_MAX}) {
        rejects([&] { t7r::buildAudioResourcePatch(bad, BASE, path); }, "invalid x86 allocation accepted");
        rejects([&] { t7r::buildAudioResourcePatch(0x10000000, bad, path); }, "invalid x86 image accepted");
    }
}
void preflightCases(const t7r::fs::path& root) {
    const auto bin = root / "client" / "Bin";
    const auto audio = bin.parent_path() / "Data/Audio/Wwise/Windows";
    std::vector<std::string> messages;
    const auto log = [&](t7r::DiagnosticLogLevel level, const std::string& text) {
        require(level == t7r::DiagnosticLogLevel::Warn, "preflight failure log level"); messages.push_back(text);
    };
    require(!t7r::prepareAudioResourcePaths(bin, log), "missing directory did not degrade");
    t7r::fs::create_directories(audio);
    require(!t7r::prepareAudioResourcePaths(bin, log), "empty directory did not degrade");
    t7r::fs::create_directory(audio / "folder.bnk");
    require(!t7r::prepareAudioResourcePaths(bin, log), "directory mistaken for Bank");
    std::ofstream(audio / "Init.bnk").put('x');
    const auto ready = t7r::prepareAudioResourcePaths(bin, log);
    require(ready && messages.size() == 3 && ready->codePage == GetACP(), "preflight readiness/log count");
    require(std::all_of(messages.begin(), messages.end(), [](const auto& text) {
        return text.find("original audio behavior retained") != text.npos;
    }), "degraded audio status not explicit");
    const auto longPrefix = root / "limit";
    const auto longRoot = longPrefix / std::wstring(229 - longPrefix.wstring().size() - 1 - 25, L'x');
    const auto longAudio = longRoot / "Data/Audio/Wwise/Windows";
    t7r::fs::create_directories(longAudio); std::ofstream(longAudio / "Init.bnk").put('x');
    require(longAudio.wstring().size() > MAX_PATH - 32
        && !t7r::prepareAudioResourcePaths(longRoot / "Bin", log), "over-limit path did not degrade");
    if (GetACP() != CP_UTF8 && GetACP() != 54936) {
        const auto unicodeRoot = root / L"unrepresentable-\U0001F600";
        const auto unicodeAudio = unicodeRoot / "Data/Audio/Wwise/Windows";
        t7r::fs::create_directories(unicodeAudio); std::ofstream(unicodeAudio / "Init.bnk").put('x');
        require(!t7r::prepareAudioResourcePaths(unicodeRoot / "Bin", log), "unrepresentable directory did not degrade");
    }
}
void executionCase(const t7r::fs::path& executable, const t7r::AudioResourcePaths& paths, bool patched) {
    t7r::DebugClient client;
    std::vector<std::string> installed;
    client.start(executable, t7r::fileHash(executable), {}, [&](HANDLE process, HANDLE) {
        if (patched) t7r::installAudioResourcePaths(process, BASE, paths, [&](t7r::DiagnosticLogLevel level, const std::string& text) {
            require(level == t7r::DiagnosticLogLevel::Debug, "path installation log level"); installed.push_back(text);
        });
        else {
            const auto unavailable = t7r::prepareAudioResourcePaths(executable.parent_path() / "missing/Bin");
            require(!unavailable, "fallback fixture unexpectedly found audio resources");
        }
    });
    require(installed.size() == (patched ? 1u : 0u), "path installation status logging");
    const auto deadline = GetTickCount64() + 10000;
    while (!read(client.process(), DONE)) {
        client.check(); require(GetTickCount64() < deadline, "audio path fixture timed out"); Sleep(2);
    }
    for (uint32_t index = 0; index < 2; ++index) {
        const auto stats = SCAN_STATS + index * 0x100;
        require(read(client.process(), stats) == read(client.process(), stats + 8), "length trampoline changed flags");
        require(read(client.process(), stats + 4) - 8 == read(client.process(), stats + 12), "length trampoline changed ESP");
        unsigned offset = 16;
        for (const auto reg : {0xB8,0xBB,0xB9,0xBA,0xBE,0xBF}) {
            require(read(client.process(), stats + offset) == 0x12340000u + reg, "length trampoline changed register"); offset += 4;
        }
        require(read(client.process(), stats + 40) == 0, "displaced local initialization lost");
        require(read(client.process(), stats + 44) == (patched ? paths.ansi.size() : 28), "constructor length changed");
        require(read(client.process(), stats + 48) == (patched ? 1u : 0u), "directory scan did not follow absolute path");
    }
    require(t7r::readClientMemory(client.process(), FLOAT_BEFORE, 512)
        == t7r::readClientMemory(client.process(), FLOAT_AFTER, 512), "length trampoline changed x87/SSE");
    if (patched) {
        const auto pointer = read(client.process(), MEDIA_POINTER);
        const auto bytes = t7r::readClientMemory(client.process(), pointer, (paths.wide.size() + 1) * sizeof(wchar_t));
        require(std::memcmp(bytes.data(), paths.wide.c_str(), bytes.size()) == 0, "media path is not full UTF-16 path");
        MEMORY_BASIC_INFORMATION data{}, code{};
        require(VirtualQueryEx(client.process(), reinterpret_cast<void*>(static_cast<uintptr_t>(pointer)), &data, sizeof(data))
            == sizeof(data) && data.Protect == PAGE_READONLY, "path storage must be read-only");
        require(VirtualQueryEx(client.process(), reinterpret_cast<void*>(static_cast<uintptr_t>(pointer + t7r::AUDIO_PATH_CODE_OFFSET)),
            &code, sizeof(code)) == sizeof(code) && code.Protect == PAGE_EXECUTE_READ, "length code must be executable/read-only");
    }
    write(client.process(), COMMAND, 1);
    require(WaitForSingleObject(client.process(), 10000) == WAIT_OBJECT_0, "audio path fixture exit timeout");
    client.stop(); client.check();
}
void signatureCases(const t7r::fs::path& executable, const t7r::AudioResourcePaths& paths) {
    t7r::DebugClient client;
    client.start(executable, t7r::fileHash(executable), {}, [&](HANDLE process, HANDLE) {
        const auto patch = t7r::buildAudioResourcePatch(0x10000000, BASE, paths);
        for (const auto& damaged : patch.patches) {
            t7r::writeClientMemory(process, BASE + damaged.rva, {0xCC});
            rejects([&] { t7r::installAudioResourcePaths(process, BASE, paths); }, "unknown path signature accepted");
            for (const auto& unchanged : patch.patches) {
                if (unchanged.rva == damaged.rva) continue;
                require(t7r::readClientMemory(process, BASE + unchanged.rva, unchanged.expected.size()) == unchanged.expected,
                    "signature failure partially installed paths");
            }
            t7r::writeClientMemory(process, BASE + damaged.rva, damaged.expected);
        }
        HANDLE readOnly = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ | PROCESS_VM_OPERATION, FALSE, GetProcessId(process));
        require(readOnly != nullptr, "limited process handle");
        try { rejects([&] { t7r::installAudioResourcePaths(readOnly, BASE, paths); }, "path write failure hidden"); }
        catch (...) { CloseHandle(readOnly); throw; }
        require(CloseHandle(readOnly), "limited process handle cleanup");
        for (const auto& entry : patch.patches)
            require(t7r::readClientMemory(process, BASE + entry.rva, entry.expected.size()) == entry.expected, "failed storage write published pointer");
    });
    client.stop(); client.check();
    t7r::DebugClient failed;
    rejects([&] {
        failed.start(executable, t7r::fileHash(executable), {}, [&](HANDLE process, HANDLE) {
            t7r::installAudioResourcePaths(process, BASE, paths);
            throw std::runtime_error("fixture preparation failure after path publication");
        });
    }, "preparation failure after publication was swallowed");
    failed.stop();
    require(failed.process() && WaitForSingleObject(failed.process(), 0) == WAIT_OBJECT_0, "failed prepared client was resumed or left alive");
}
}
bool verifyAudioResourcePaths() {
    const auto root = t7r::fs::temp_directory_path() / (L"t7r-audio-paths-" + std::to_wstring(GetCurrentProcessId())
        + L"-" + std::to_wstring(GetTickCount64()));
    bool ownsRoot = false;
    const auto cleanup = [&] {
        if (!ownsRoot) return;
        require(t7r::fs::weakly_canonical(root).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()), "audio paths cleanup boundary");
        t7r::fs::remove_all(root);
    };
    try {
        encodingCases(); ownsRoot = t7r::fs::create_directory(root); require(ownsRoot, "fresh path fixture directory");
        preflightCases(root);
        const auto selected = GetACP() == 936 || GetACP() == CP_UTF8 ? L"selected client 游戏音效" : L"selected client";
        const auto audio = root / selected / std::wstring(80, L'x') / "Data/Audio/Wwise/Windows";
        t7r::fs::create_directories(audio); std::ofstream(audio / "Weapon.bnk").put('x');
        const auto paths = t7r::buildAudioResourcePaths(audio);
        require(paths.ansi.size() > 127, "fixture path must exceed signed byte length");
        t7r::fs::create_directory(root / "runner");
        const auto executable = createPaths(root / "runner");
        signatureCases(executable, paths); executionCase(executable, paths, false); executionCase(executable, paths, true);
        cleanup(); std::cout << "Audio resource paths: encoding, preflight degradation, five-site installation and x86 absolute scans passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Audio resource paths: " << error.what() << '\n'; cleanup(); return false;
    }
}
