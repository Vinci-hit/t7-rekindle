#pragma once
#include "T7RNativeBridge.h"

typedef struct T7RBattleHostOpaque* T7RBattleHostHandle;

enum T7RBattleHostState : uint32_t {
    T7RBH_STOPPED = 0, T7RBH_RUNNING = 1, T7RBH_FAILED = 2
};

#pragma pack(push, 8)
typedef struct T7RBattleHostCreateArgs {
    uint32_t abiVersion, structSize;
    T7RNativePath packageRoot, clientDirectory, writableRoot;
} T7RBattleHostCreateArgs;

typedef struct T7RBattleHostSnapshot {
    uint32_t abiVersion, structSize;
    uint32_t state, reserved;
    uint16_t loginPort, logicPort, instancePort, reservedPort;
    uint32_t connections, timers;
    uint64_t startedAt;
    char phase[64];
} T7RBattleHostSnapshot;
#pragma pack(pop)

static_assert(sizeof(T7RBattleHostCreateArgs) == 80, "battle create ABI changed");
static_assert(sizeof(T7RBattleHostSnapshot) == 104, "battle snapshot ABI changed");

// This host owns only the battle runtime, never Bootstrap or a client process.
// The three legacy game listeners are private loopback endpoints. Public
// transports must perform admission before forwarding game traffic to them.
// Start/stop/release are synchronous; call them off the UI thread. Release
// joins all runtime workers before returning. The caller serializes release
// against all other calls and must not reuse a released handle.
extern "C" {
T7RNB_EXPORT int32_t T7RNB_CALL t7r_battle_host_create(const T7RBattleHostCreateArgs* args, T7RBattleHostHandle* host);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_battle_host_start(T7RBattleHostHandle host);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_battle_host_stop(T7RBattleHostHandle host);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_battle_host_snapshot(T7RBattleHostHandle host, T7RBattleHostSnapshot* snapshot);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_battle_host_error(T7RBattleHostHandle host, uint8_t* buffer, uint32_t capacity, uint32_t* required);
T7RNB_EXPORT int32_t T7RNB_CALL t7r_battle_host_release(T7RBattleHostHandle host);
}
