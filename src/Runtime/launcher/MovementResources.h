#pragma once
#include <string>
#include <string_view>

namespace t7r {
enum class MovementResource { None, Infantry, Router, Birth, MonsterBlood, MonsterDeath };
inline constexpr size_t MOVEMENT_XML_RESERVE = 64 * 1024;
inline constexpr size_t MOVEMENT_XML_LIMIT = 4 * 1024 * 1024;

std::string movementResourcePath(MovementResource resource);
std::string movementOfflineTreePath();
MovementResource movementResourceForPath(std::string_view path);
std::string transformMovementResource(MovementResource resource, std::string_view source);
}
