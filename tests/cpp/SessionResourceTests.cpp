#include "../../src/Runtime/bridge/Session.h"
#include <fstream>
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }

void write(const t7r::fs::path& path, const t7r::Bytes& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.exceptions(std::ios::failbit | std::ios::badbit);
    output.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
}

t7r::bridge::Operation waitFor(const std::shared_ptr<t7r::bridge::Session>& session, uint64_t id) {
    t7r::bridge::Operation result;
    const auto deadline = GetTickCount64() + 10000;
    do {
        require(session->operation(id, result) == T7RNB_OK, "resource operation lookup");
        if (result.status >= T7RNB_OPERATION_SUCCEEDED) return result;
        Sleep(5);
    } while (GetTickCount64() < deadline);
    throw std::runtime_error("resource operation timed out");
}
}

bool verifyResourcePreflight(const t7r::fs::path& packageRoot) {
    const auto root = packageRoot / "resource-client";
    const auto script = packageRoot / "Business/scripts/client_resources.py";
    const auto originalScript = t7r::readFile(script);
    std::shared_ptr<t7r::bridge::Session> session;
    try {
        t7r::fs::copy(packageRoot / "synthetic-client", root, t7r::fs::copy_options::recursive);
        const auto directory = t7r::utf8((root / "Bin").wstring());
        bool launched = false;
        bool expectAnimation = false;
        t7r::Bootstrap::TestAdapter adapter;
        adapter.check = [](const auto&, const auto&, const auto&) {};
        adapter.launch = [&](const auto&, const t7r::Config& config, const auto&, const auto&, const auto&) {
            require(config.combatProfiles.size() == 3 && config.combatProfiles[0].heroId == 1101
                && config.combatProfiles[0].movement.weaponId == 701 && !config.playerProfilePath.empty(),
                "Bootstrap lost the hero/weapon-selected profiles or persistence path");
            require(!config.dashAttack.enabled && config.dashAttack.weaponId == 1030411,
                "Bootstrap lost the disabled weapon resource profile");
            require(config.movementAnimation.enabled
                && config.movementAnimation.weaponId == 1030411
                && config.movementAnimation.forwardSlow == (expectAnimation ? 2.25f : 2.5f)
                && config.movementAnimation.lateralSlow == (expectAnimation ? 1.75f : 1.8f)
                && config.movementAnimation.backwardSlow == (expectAnimation ? .75f : 1.f)
                && config.movementAnimation.forwardSprint == (expectAnimation ? 4.25f : 3.6f),
                "Bootstrap lost the selected movement animation profile");
            launched = true;
        };
        session = t7r::bridge::Session::createForTest(t7r::utf8(packageRoot.wstring()), adapter);
        auto submit = [&](uint32_t kind, uint32_t expected) {
            uint64_t id = 0;
            require(session->submit(kind, kind == T7RNB_OPERATION_STOP ? "" : directory, id) == T7RNB_OK,
                "resource operation submit");
            auto result = waitFor(session, id);
            if (result.status != expected) throw std::runtime_error("resource operation status: " + result.error);
            return result;
        };
        auto idle = [&] {
            t7r::bridge::Snapshot snapshot;
            session->snapshot(snapshot);
            require(snapshot.operation == T7RNB_OPERATION_NONE && (snapshot.flags & 1u)
                && !snapshot.ports[0] && !snapshot.ports[1] && !snapshot.ports[2] && !launched,
                "resource check created a listener/client or retained ownership");
            t7r::PythonHost owner(packageRoot, packageRoot / "resource-owner");
        };
        submit(T7RNB_OPERATION_CHECK, T7RNB_OPERATION_SUCCEEDED);
        idle();
        uint64_t cursor = 0;
        uint32_t required = 0, flags = 0;
        require(session->readLogs(cursor, nullptr, 0, required, flags) == T7RNB_BUFFER_TOO_SMALL,
            "resource diagnostics did not reach the existing log stream");
        std::vector<uint8_t> buffer(required);
        require(session->readLogs(cursor, buffer.data(), required, required, flags) == T7RNB_OK,
            "resource diagnostic read failed");
        const std::string logs(buffer.begin(), buffer.end());
        require(logs.find("dash-disabled") == std::string::npos,
            "static combat profiles unexpectedly read missing client action tables");
        const auto archive = root / "vfs/data1.vfs";
        const auto validArchive = t7r::readFile(archive);
        write(archive, {0, 1, 2});
        for (auto kind : {T7RNB_OPERATION_CHECK, T7RNB_OPERATION_START}) {
            auto result = submit(kind, T7RNB_OPERATION_FAILED);
            require(result.error.find("[T7R_RESOURCE:directory_invalid]") != std::string::npos
                && result.error.find("data1.vfs") != std::string::npos
                && result.error.find("Traceback") != std::string::npos, "resource error lost type/entry/traceback");
            idle();
        }
        write(archive, validArchive);
        submit(T7RNB_OPERATION_CHECK, T7RNB_OPERATION_SUCCEEDED);
        idle();

        // Pause inside the generated Python package, after it acquires the
        // interpreter. Production code has no test skip or timing switch.
        const std::string hook = R"(
_fixtureCheck = checkResources
def checkResources(clientDirectory, playerName=''):
    import time
    marker = Path(clientDirectory) / 'preflight-pause'
    if marker.exists():
        marker.with_suffix('.entered').write_text('entered')
        time.sleep(0.5)
    return _fixtureCheck(clientDirectory, playerName)
)";
        auto pausedScript = originalScript;
        pausedScript.insert(pausedScript.end(), hook.begin(), hook.end());
        write(script, pausedScript);
        const auto pause = root / "Bin/preflight-pause";
        const auto entered = root / "Bin/preflight-pause.entered";
        write(pause, {1});
        uint64_t id = 0;
        require(session->submit(T7RNB_OPERATION_CHECK, directory, id) == T7RNB_OK, "cancel-check submit");
        auto deadline = GetTickCount64() + 10000;
        while (!t7r::fs::exists(entered) && GetTickCount64() < deadline) Sleep(5);
        require(t7r::fs::exists(entered), "resource check did not enter Python");
        require(session->cancel(id) == T7RNB_OK, "resource cancel rejected");
        require(waitFor(session, id).status == T7RNB_OPERATION_CANCELLED, "resource cancellation lost");
        idle();
        write(script, originalScript);
        t7r::fs::remove(pause);
        t7r::fs::remove(entered);
        submit(T7RNB_OPERATION_CHECK, T7RNB_OPERATION_SUCCEEDED);
        submit(T7RNB_OPERATION_START, T7RNB_OPERATION_SUCCEEDED);
        require(launched, "check-to-start failed to transfer Python ownership");
        submit(T7RNB_OPERATION_STOP, T7RNB_OPERATION_SUCCEEDED);
        const std::string animationHook = R"(
_fixtureCheck = checkResources

def checkResources(clientDirectory, playerName=''):
    result = _fixtureCheck(clientDirectory, playerName)
    return result | {'movementAnimation': dict(enabled=True, weaponId=1030411, forwardSlow=2.25,
        lateralSlow=1.75, backwardSlow=0.75, forwardSprint=4.25)}
)";
        auto animationScript = originalScript;
        animationScript.insert(animationScript.end(), animationHook.begin(), animationHook.end());
        write(script, animationScript);
        expectAnimation = true; launched = false;
        submit(T7RNB_OPERATION_START, T7RNB_OPERATION_SUCCEEDED);
        require(launched, "selected animation profile did not reach launch");
        submit(T7RNB_OPERATION_STOP, T7RNB_OPERATION_SUCCEEDED);
        write(script, originalScript);
        session->requestClose();
        require(session->waitForWorkerForTest(std::chrono::seconds(10)), "resource worker cleanup");
        std::cout << "Resource checks, direct start rejection, retry, cancellation and Python ownership passed\n";
        return true;
    } catch (const std::exception& error) {
        if (session) { session->requestClose(); session->waitForWorkerForTest(std::chrono::seconds(10)); }
        try { write(script, originalScript); }
        catch (const std::exception& cleanup) { std::cerr << "fixture restore: " << cleanup.what() << '\n'; }
        std::cerr << error.what() << '\n';
        return false;
    }
}
