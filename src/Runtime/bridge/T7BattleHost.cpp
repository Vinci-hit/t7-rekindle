#include "T7BattleHost.h"
#include "../server/Server.h"
#include <algorithm>
#include <cstring>
#include <memory>
#include <mutex>

struct T7BattleHostOpaque {
    std::mutex mutex;
    t7::fs::path packageRoot, clientDirectory, writableRoot;
    t7::Server server;
    std::string error;
    bool winsock = false, failed = false;

    ~T7BattleHostOpaque() {
        server.stop();
        if (winsock) WSACleanup();
    }
};

namespace {
bool header(uint32_t version, uint32_t size, size_t expected) {
    return version == T7NB_ABI_VERSION && size >= expected;
}
int32_t path(const T7NativePath& input, t7::fs::path& result) {
    if (!header(input.abiVersion, input.structSize, sizeof(input))) return T7NB_INVALID_ABI;
    if (!input.data || !input.length || input.length > 32768) return T7NB_INVALID_ARGUMENT;
    const std::string text(reinterpret_cast<const char*>(input.data), input.length);
    if (text.find_first_of("\r\n") != std::string::npos || text.find('\0') != std::string::npos)
        return T7NB_INVALID_ARGUMENT;
    try {
        result = t7::fs::path(t7::wide(text));
        return result.is_absolute() ? T7NB_OK : T7NB_INVALID_ARGUMENT;
    } catch (const std::exception&) { return T7NB_INVALID_ARGUMENT; }
}
template<class F>
int32_t withHost(T7BattleHostHandle host, F&& action) noexcept {
    if (!host) return T7NB_INVALID_HANDLE;
    try {
        std::lock_guard<std::mutex> lock(host->mutex);
        try { return action(); }
        catch (const std::exception& error) {
            host->error = error.what(); host->failed = true;
            return T7NB_FAILED;
        }
    } catch (...) { return T7NB_INTERNAL_ERROR; }
}
}

extern "C" int32_t T7NB_CALL t7_battle_host_create(const T7BattleHostCreateArgs* args, T7BattleHostHandle* output) {
    if (!output) return T7NB_INVALID_ARGUMENT;
    *output = nullptr;
    if (!args) return T7NB_INVALID_ARGUMENT;
    if (!header(args->abiVersion, args->structSize, sizeof(*args))) return T7NB_INVALID_ABI;
    try {
        auto host = std::make_unique<T7BattleHostOpaque>();
        for (const auto& item : {std::make_pair(&args->packageRoot, &host->packageRoot),
                                std::make_pair(&args->clientDirectory, &host->clientDirectory),
                                std::make_pair(&args->writableRoot, &host->writableRoot)}) {
            const auto status = path(*item.first, *item.second);
            if (status != T7NB_OK) return status;
        }
        *output = host.release();
        return T7NB_OK;
    } catch (...) { return T7NB_INTERNAL_ERROR; }
}

extern "C" int32_t T7NB_CALL t7_battle_host_start(T7BattleHostHandle host) {
    return withHost(host, [&]() -> int32_t {
        if (host->server.running()) return T7NB_BUSY;
        if (!host->winsock) {
            WSADATA data{};
            if (WSAStartup(MAKEWORD(2, 2), &data)) throw std::runtime_error("battle host WSAStartup failed");
            host->winsock = true;
        }
        t7::Config config;
        config.clientDirectory = host->clientDirectory;
        // No public raw game ports: admission belongs to BattleProtocol, not AUTH.
        host->server.start(config, host->packageRoot, host->writableRoot);
        host->error.clear(); host->failed = false;
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_battle_host_stop(T7BattleHostHandle host) {
    return withHost(host, [&]() -> int32_t {
        host->server.stop();
        if (host->winsock) {
            if (WSACleanup()) throw std::runtime_error("battle host WSACleanup failed");
            host->winsock = false;
        }
        host->failed = false;
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_battle_host_snapshot(T7BattleHostHandle host, T7BattleHostSnapshot* output) {
    if (!output) return T7NB_INVALID_ARGUMENT;
    if (!header(output->abiVersion, output->structSize, sizeof(*output))) return T7NB_INVALID_ABI;
    return withHost(host, [&]() -> int32_t {
        const auto snapshot = host->server.snapshot();
        const auto config = host->server.boundConfig();
        T7BattleHostSnapshot result{};
        result.abiVersion = T7NB_ABI_VERSION; result.structSize = sizeof(result);
        result.state = host->failed || snapshot.status == "failed" ? T7BH_FAILED
            : host->server.running() ? T7BH_RUNNING : T7BH_STOPPED;
        if (result.state == T7BH_RUNNING) {
            result.loginPort = config.ports[0]; result.logicPort = config.ports[1]; result.instancePort = config.ports[2];
        }
        result.connections = static_cast<uint32_t>(snapshot.connections);
        result.timers = static_cast<uint32_t>(snapshot.timers); result.startedAt = snapshot.started;
        const auto length = (std::min)(snapshot.phase.size(), sizeof(result.phase) - 1);
        std::memcpy(result.phase, snapshot.phase.data(), length);
        *output = result;
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_battle_host_error(T7BattleHostHandle host, uint8_t* buffer, uint32_t capacity, uint32_t* required) {
    if (!required || (capacity && !buffer)) return T7NB_INVALID_ARGUMENT;
    *required = 0;
    return withHost(host, [&]() -> int32_t {
        const auto snapshot = host->server.snapshot();
        const auto& message = host->error.empty() ? snapshot.error : host->error;
        *required = static_cast<uint32_t>(message.size());
        if (capacity < *required) return T7NB_BUFFER_TOO_SMALL;
        if (*required) std::memcpy(buffer, message.data(), *required);
        return T7NB_OK;
    });
}

extern "C" int32_t T7NB_CALL t7_battle_host_release(T7BattleHostHandle host) {
    if (!host) return T7NB_INVALID_HANDLE;
    // Synchronous worker join is required before a managed caller unloads the DLL.
    try { delete host; return T7NB_OK; }
    catch (...) { return T7NB_INTERNAL_ERROR; }
}
