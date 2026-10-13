#include "AudioPlaybackFixture.h"
#include "../../src/Runtime/launcher/DebugClient.h"
#include <atomic>
#include <iostream>
#include <mutex>

namespace {
using namespace audioFixture;
struct Run {
    t7::AudioPlaybackTrace trace;
    std::mutex mutex;
    std::vector<std::string> lines;
    std::atomic<bool> installed{false};
    t7::DebugClient client;
    void log(t7::DiagnosticLogLevel, const std::string& message) {
        { std::lock_guard<std::mutex> lock(mutex); lines.push_back(message); }
        if (message.find("Audio playback trace installed;") == 0) installed.store(true);
    }
    bool contains(const std::string& text) {
        std::lock_guard<std::mutex> lock(mutex);
        return std::any_of(lines.begin(), lines.end(), [&](const auto& line) { return line.find(text) != std::string::npos; });
    }
    void start(const t7::fs::path& path, bool enabled) {
        const auto logger = [this](t7::DiagnosticLogLevel level, const std::string& line) { log(level, line); };
        client.start(path, t7::fileHash(path), {}, [&](HANDLE process, HANDLE) { if (enabled) trace.arm(process, logger); },
            logger, {}, {}, [this](DWORD tid, uintptr_t address) { return trace.handleBreakpoint(tid, address); });
        if (enabled) wait([&] { return installed.load(); });
    }
    void wait(const std::function<bool()>& condition) {
        const auto until = GetTickCount64() + 10000;
        while (!condition()) { client.check(); require(GetTickCount64() < until, "audio fixture wait timed out"); Sleep(2); }
    }
    void command(uint32_t value) {
        write(client.process(), DONE, 0); write(client.process(), COMMAND, value);
        wait([&] { return read(client.process(), DONE) == value; });
    }
    void finish() {
        write(client.process(), COMMAND, 4);
        require(WaitForSingleObject(client.process(), 10000) == WAIT_OBJECT_0, "audio fixture exit timeout");
        client.stop(); client.check(); DWORD result = 1;
        require(GetExitCodeProcess(client.process(), &result) && result == 0, "audio fixture changed process result");
    }
};
void verifyRegisters(Run& run) {
    const auto process = run.client.process();
    require(read(process, STATS) == read(process, STATS + 8), "audio wrapper changed caller stack");
    require(read(process, STATS + 4) == 456, "audio wrapper changed playing ID");
    for (const auto pair : {std::pair<uint32_t,uint32_t>{12,0x11223344},{16,0x22334455},{20,0x33445566},{24,0x44556677}})
        require(read(process, STATS + pair.first) == pair.second, "audio wrapper changed nonvolatile register");
    require(read(process, STATS + 28) == 0x76543210 && read(process, STATS + 32) == 0x1234,
            "audio diagnostics changed original/returned LastError");
    const uint32_t expected[]{NAME,42,8,0x123400,0x234500,2,0x345600,99};
    for (unsigned i = 0; i < 8; ++i) require(read(process, ARGUMENTS + i * 4) == expected[i], "audio callback/cookie/argument changed");
    require(read(process, BANK_ID) == 12345, "audio wrapper changed bank output");
}
void verifyTrace(const t7::fs::path& path) {
    Run run; run.start(path, true); run.command(1); verifyRegisters(run);
    const auto process = run.client.process();
    const auto queue = read(process, iat(module("aksoundenginedll.dll"), "?SetRTPCValue@SoundEngine@AK@@YA?AW4AKRESULT@@KM"))
        - t7::AUDIO_PLAYBACK_CODE_OFFSET;
    require(read(process, queue + 8) == 8, "audio import coverage count");
    run.trace.poll();
    for (const auto* expected : {"eventName=\"Play_voice_fixture\"", "playingId=456; accepted=1; audible=not-observed",
            "bank=\"Media_Vox_System.bnk\"; akResult=1; bankId=12345", "akResult=2; bankId=not-observed",
            "api=UnloadBank", "api=GetIDFromString; name=\"Play_voice_fixture\"; id=77",
            "api=SetRTPCValue; name=\"Voice_Volume\"; value=0.5", "api=SetRTPCValue; id=81; value=1", "api=Init; akResult=1"})
        require(run.contains(expected), expected);
    require(read(process, BANK_NAME) != static_cast<uint32_t>('M'), "fixture original bank call did not execute");
    run.command(3); run.trace.poll(); require(run.contains("playingId=0; accepted=0"), "rejected PostEvent was not logged");

    // Queue contention and saturation may lose diagnostics, never an original API call.
    write(process, queue, 1); run.command(3); write(process, queue, 0); run.trace.poll();
    require(run.contains("totalDropped=1"), "contended queue did not report dropped coverage");
    const auto before = read(process, CALLS);
    run.command(2);
    const auto written = read(process, queue + 8), consumed = read(process, queue + 4), dropped = read(process, queue + 12);
    require(read(process, CALLS) - before == 2000, "trace queue dropped original calls");
    require(written - consumed + dropped - 1 == 2000, "concurrent trace accounting mismatch");
    require(written - consumed == t7::AUDIO_PLAYBACK_CAPACITY, "bounded trace queue did not saturate");
    run.trace.poll(); require(run.contains("coverage incomplete; dropped="), "trace overflow omitted warning");
    // Unsigned cursors and sequences must also work across wraparound.
    write(process, queue + 4, UINT32_MAX); write(process, queue + 8, UINT32_MAX);
    run.command(3); run.trace.poll(); require(run.contains("sequence=0;"), "trace cursor wraparound");
    run.command(3); run.trace.poll();

    t7::Bytes debugText{'W','w','i','s','e',':',' ','e','r','r','o','r','\n',0};
    t7::writeClientMemory(process, NAME, debugText);
    OUTPUT_DEBUG_STRING_INFO info{}; info.lpDebugStringData = reinterpret_cast<char*>(static_cast<uintptr_t>(NAME));
    info.nDebugStringLength = static_cast<WORD>(debugText.size()); run.trace.debugString(info);
    require(run.contains("Audio engine debug output; message=\"Wwise: error\\u000a\""), "existing Wwise debug output was not captured");
    require(!run.contains("collection stopped"), "trace collector stopped independently of mailbox");
    run.finish();
}
void verifyFormat() {
    t7::AudioPlaybackRecord record{}; record.api = static_cast<uint32_t>(t7::AudioPlaybackApi::PostEventName);
    record.result = 7; record.startedMs = UINT32_MAX - 9; record.completedMs = 10;
    std::memset(record.name, 'x', sizeof(record.name)); record.flags = t7::AUDIO_PLAYBACK_TRUNCATED;
    const auto text = t7::describeAudioPlayback(record);
    require(text.find("durationMs=20;") != std::string::npos && text.find("nameTruncated=1") != std::string::npos,
            "bounded name/timestamp formatting");
    record.api = 999; bool rejected = false;
    try { t7::describeAudioPlayback(record); } catch (const std::runtime_error&) { rejected = true; }
    require(rejected, "unknown playback record accepted");
}
}
bool verifyAudioPlaybackTrace() {
    const auto root = t7::fs::temp_directory_path() / (L"t7-audio-trace-" + std::to_wstring(GetCurrentProcessId())
                                                   + L"-" + std::to_wstring(GetTickCount64()));
    bool ownsRoot = false;
    const auto cleanup = [&] {
        if (!ownsRoot) return;
        require(t7::fs::weakly_canonical(root).parent_path() == t7::fs::canonical(t7::fs::temp_directory_path()), "audio fixture cleanup extent");
        t7::fs::remove_all(root);
    };
    try {
        verifyFormat(); ownsRoot = t7::fs::create_directory(root); require(ownsRoot, "fresh audio fixture directory");
        const auto path = create(root);
        { Run baseline; baseline.start(path, false); baseline.command(1); verifyRegisters(baseline); baseline.finish(); }
        verifyTrace(path); cleanup();
        std::cout << "Audio playback trace: x86 ABI, synchronous banks, PostEvent results, concurrency and bounded collection passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Audio playback trace: " << error.what() << '\n'; cleanup(); return false;
    }
}
