#include "../../src/Runtime/bridge/T7BattleHost.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
void require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}
T7NativePath path(const std::string& value) {
    return {T7NB_ABI_VERSION, sizeof(T7NativePath), reinterpret_cast<const uint8_t*>(value.data()),
            static_cast<uint32_t>(value.size())};
}
T7BattleHostSnapshot snapshot(T7BattleHostHandle host) {
    T7BattleHostSnapshot value{};
    value.abiVersion = T7NB_ABI_VERSION; value.structSize = sizeof(value);
    require(t7_battle_host_snapshot(host, &value) == T7NB_OK, "snapshot failed");
    return value;
}
std::string error(T7BattleHostHandle host) {
    uint32_t required = 0;
    require(t7_battle_host_error(host, nullptr, 0, &required) == T7NB_BUFFER_TOO_SMALL && required,
            "error length missing");
    std::vector<uint8_t> bytes(required);
    require(t7_battle_host_error(host, bytes.data(), required, &required) == T7NB_OK, "error read failed");
    return {bytes.begin(), bytes.end()};
}
struct Host {
    T7BattleHostHandle value = nullptr;
    ~Host() { if (value) t7_battle_host_release(value); }
};
void authenticate(uint16_t port) {
    SOCKET client = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    require(client != INVALID_SOCKET, "client socket creation failed");
    sockaddr_in address{}; address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK); address.sin_port = htons(port);
    int timeout = 5000;
    setsockopt(client, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&timeout), sizeof(timeout));
    const bool connected = connect(client, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0;
    // TPDU AUTH uses the same local protocol as the embedded launcher runtime.
    const char auth[29] = {0x55, 0x0e, 3, 0, 0, 0, 0, 0,
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 3, 0, 0, 0, 0, 0};
    const int sent = connected ? send(client, auth, sizeof(auth), 0) : -1;
    char reply[256]{};
    const int received = sent == sizeof(auth) ? recv(client, reply, sizeof(reply), 0) : -1;
    closesocket(client);
    require(received > 0, "headless runtime did not answer AUTH");
}
}

int wmain(int argc, wchar_t** argv) {
    try {
        require(argc == 2, "expected generated runtime fixture path");
        const auto fixture = std::filesystem::absolute(argv[1]);
        const std::string root = fixture.u8string();
        const std::string resources = (fixture / "synthetic-client" / "bin").u8string();
        const std::string state = (fixture / "battle-host-state").u8string();
        T7BattleHostCreateArgs args{T7NB_ABI_VERSION, sizeof(T7BattleHostCreateArgs), path(root), path(resources), path(state)};
        Host first, second;
        require(t7_battle_host_create(nullptr, &first.value) == T7NB_INVALID_ARGUMENT && !first.value, "null args accepted");
        require(t7_battle_host_start(nullptr) == T7NB_INVALID_HANDLE, "null start accepted");
        require(t7_battle_host_stop(nullptr) == T7NB_INVALID_HANDLE, "null stop accepted");
        require(t7_battle_host_release(nullptr) == T7NB_INVALID_HANDLE, "null release accepted");
        ++args.abiVersion;
        require(t7_battle_host_create(&args, &first.value) == T7NB_INVALID_ABI && !first.value, "bad ABI accepted");
        --args.abiVersion;
        auto validPath = args.writableRoot;
        std::string relative = "relative-state";
        args.writableRoot = path(relative);
        require(t7_battle_host_create(&args, &first.value) == T7NB_INVALID_ARGUMENT && !first.value, "relative root accepted");
        args.writableRoot = validPath;
        require(t7_battle_host_create(&args, &first.value) == T7NB_OK && first.value, "create failed");
        require(snapshot(first.value).state == T7BH_STOPPED, "create started runtime");
        if (t7_battle_host_start(first.value) != T7NB_OK) throw std::runtime_error(error(first.value));
        auto running = snapshot(first.value);
        require(running.state == T7BH_RUNNING && running.loginPort && running.logicPort && running.instancePort,
                "missing private endpoints");
        require(t7_battle_host_start(first.value) == T7NB_BUSY, "double start accepted");
        authenticate(running.loginPort);
        require(t7_battle_host_create(&args, &second.value) == T7NB_OK, "second create failed");
        require(t7_battle_host_start(second.value) == T7NB_FAILED, "multiple Python owners accepted");
        require(!error(second.value).empty() && snapshot(second.value).state == T7BH_FAILED, "start failure missing");
        require(snapshot(first.value).state == T7BH_RUNNING, "owner conflict stopped first host");
        require(t7_battle_host_stop(first.value) == T7NB_OK, "stop failed");
        auto stopped = snapshot(first.value);
        require(stopped.state == T7BH_STOPPED && !stopped.loginPort && !stopped.logicPort && !stopped.instancePort,
                "stop retained listeners");
        require(t7_battle_host_stop(first.value) == T7NB_OK, "stop not idempotent");
        require(t7_battle_host_start(second.value) == T7NB_OK, "failed host did not recover");
        require(t7_battle_host_release(second.value) == T7NB_OK, "release failed");
        second.value = nullptr;
        require(t7_battle_host_start(first.value) == T7NB_OK, "release retained Python owner");
        require(t7_battle_host_stop(first.value) == T7NB_OK, "restart stop failed");
        std::cout << "battle host ABI, AUTH, ownership, stop and restart checks passed\n";
        return 0;
    } catch (const std::exception& exception) { std::cerr << exception.what() << '\n'; return 1; }
}
