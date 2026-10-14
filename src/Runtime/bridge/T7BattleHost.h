#pragma once
#include "T7NativeBridge.h"

typedef struct T7BattleHostOpaque* T7BattleHostHandle;

enum T7BattleHostState : uint32_t {
    T7BH_STOPPED = 0, T7BH_RUNNING = 1, T7BH_FAILED = 2
};

#pragma pack(push, 8)
typedef struct T7BattleHostCreateArgs {
    uint32_t abiVersion, structSize;
    T7NativePath packageRoot, clientDirectory, writableRoot;
} T7BattleHostCreateArgs;

typedef struct T7BattleHostSnapshot {
    uint32_t abiVersion, structSize;
    uint32_t state, reserved;
    uint16_t loginPort, logicPort, instancePort, reservedPort;
    uint32_t connections, timers;
    uint64_t startedAt;
    char phase[64];
} T7BattleHostSnapshot;
#pragma pack(pop)

static_assert(sizeof(T7BattleHostCreateArgs) == 80, "battle create ABI changed");
static_assert(sizeof(T7BattleHostSnapshot) == 104, "battle snapshot ABI changed");

// This host owns only the battle runtime, never Bootstrap or a client process.
// The three legacy game listeners are private loopback endpoints. Public
// transports must perform admission before forwarding game traffic to them.
// Start/stop/release are synchronous; call them off the UI thread. Release
// joins all runtime workers before returning. The caller serializes release
// against all other calls and must not reuse a released handle.
extern "C" {
T7NB_EXPORT int32_t T7NB_CALL t7_battle_host_create(const T7BattleHostCreateArgs* args, T7BattleHostHandle* host);
T7NB_EXPORT int32_t T7NB_CALL t7_battle_host_start(T7BattleHostHandle host);
T7NB_EXPORT int32_t T7NB_CALL t7_battle_host_stop(T7BattleHostHandle host);
T7NB_EXPORT int32_t T7NB_CALL t7_battle_host_snapshot(T7BattleHostHandle host, T7BattleHostSnapshot* snapshot);
T7NB_EXPORT int32_t T7NB_CALL t7_battle_host_error(T7BattleHostHandle host, uint8_t* buffer, uint32_t capacity, uint32_t* required);
T7NB_EXPORT int32_t T7NB_CALL t7_battle_host_release(T7BattleHostHandle host);
}
