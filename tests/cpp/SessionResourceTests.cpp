#include "../../src/Runtime/bridge/Session.h"
#include <fstream>
#include <iostream>

namespace {
void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }

void write(const t7::fs::path& path, const t7::Bytes& bytes) {
    std::ofstream output(path, std::ios::binary | std::ios::trunc);
    output.exceptions(std::ios::failbit | std::ios::badbit);
    output.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
}

t7::bridge::Operation waitFor(const std::shared_ptr<t7::bridge::Session>& session, uint64_t id) {
    t7::bridge::Operation result;
    const auto deadline = GetTickCount64() + 10000;
    do {
        require(session->operation(id, result) == T7NB_OK, "resource operation lookup");
        if (result.status >= T7NB_OPERATION_SUCCEEDED) return result;
        Sleep(5);
    } while (GetTickCount64() < deadline);
    throw std::runtime_error("resource operation timed out");
}
}

bool verifyResourcePreflight(const t7::fs::path& packageRoot) {
    const auto root = packageRoot / "resource-client";
    const auto script = packageRoot / "Business/scripts/client_resources.py";
    const auto originalScript = t7::readFile(script);
    std::shared_ptr<t7::bridge::Session> session;
    try {
        t7::fs::copy(packageRoot / "synthetic-client", root, t7::fs::copy_options::recursive);
        const auto directory = t7::utf8((root / "Bin").wstring());
        bool launched = false;
        t7::Bootstrap::TestAdapter adapter;
        adapter.check = [](const auto&, const auto&, const auto&) {};
        adapter.launch = [&](const auto&, const auto&, const auto&, const auto&, const auto&) { launched = true; };
        session = t7::bridge::Session::createForTest(t7::utf8(packageRoot.wstring()), adapter);
        auto submit = [&](uint32_t kind, uint32_t expected) {
            uint64_t id = 0;
            require(session->submit(kind, kind == T7NB_OPERATION_STOP ? "" : directory, id) == T7NB_OK,
                "resource operation submit");
            auto result = waitFor(session, id);
            if (result.status != expected) throw std::runtime_error("resource operation status: " + result.error);
            return result;
        };
        auto idle = [&] {
            t7::bridge::Snapshot snapshot;
            session->snapshot(snapshot);
            require(snapshot.operation == T7NB_OPERATION_NONE && (snapshot.flags & 1u)
                && !snapshot.ports[0] && !snapshot.ports[1] && !snapshot.ports[2] && !launched,
                "resource check created a listener/client or retained ownership");
            t7::PythonHost owner(packageRoot, packageRoot / "resource-owner");
        };
        submit(T7NB_OPERATION_CHECK, T7NB_OPERATION_SUCCEEDED);
        idle();
        const auto archive = root / "vfs/data1.vfs";
        const auto validArchive = t7::readFile(archive);
        write(archive, {0, 1, 2});
        for (auto kind : {T7NB_OPERATION_CHECK, T7NB_OPERATION_START}) {
            auto result = submit(kind, T7NB_OPERATION_FAILED);
            require(result.error.find("[T7_RESOURCE:directory_invalid]") != std::string::npos
                && result.error.find("data1.vfs") != std::string::npos
                && result.error.find("Traceback") != std::string::npos, "resource error lost type/entry/traceback");
            idle();
        }
        write(archive, validArchive);
        submit(T7NB_OPERATION_CHECK, T7NB_OPERATION_SUCCEEDED);
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
        require(session->submit(T7NB_OPERATION_CHECK, directory, id) == T7NB_OK, "cancel-check submit");
        auto deadline = GetTickCount64() + 10000;
        while (!t7::fs::exists(entered) && GetTickCount64() < deadline) Sleep(5);
        require(t7::fs::exists(entered), "resource check did not enter Python");
        require(session->cancel(id) == T7NB_OK, "resource cancel rejected");
        require(waitFor(session, id).status == T7NB_OPERATION_CANCELLED, "resource cancellation lost");
        idle();
        write(script, originalScript);
        t7::fs::remove(pause);
        t7::fs::remove(entered);
        submit(T7NB_OPERATION_CHECK, T7NB_OPERATION_SUCCEEDED);
        submit(T7NB_OPERATION_START, T7NB_OPERATION_SUCCEEDED);
        require(launched, "check-to-start failed to transfer Python ownership");
        submit(T7NB_OPERATION_STOP, T7NB_OPERATION_SUCCEEDED);
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
