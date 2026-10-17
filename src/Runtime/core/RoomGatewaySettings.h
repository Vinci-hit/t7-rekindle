#pragma once
#include <string>
#include <cstddef>

namespace t7r {
inline constexpr size_t RoomGatewayFramesPerSecond = 1024;
inline constexpr size_t RoomGatewayMessageBytes = 256 * 1024;
inline constexpr int RoomGatewayReconnectSeconds = 60;
inline constexpr int RoomGatewayRetryMs = 1000;

struct RoomGatewaySettings {
    unsigned short port = 8090;
    std::string accountId;
    std::string host = "127.0.0.1";
    bool secure = false;
    std::string ticket;
};
}
