#pragma once
#include "../core/Common.h"

namespace t7r {
constexpr uint32_t CHAT_INPUT_CODE_OFFSET = 0x4000;
void verifyChatInputFunctions(HANDLE process, uint32_t imageBase);
Bytes buildChatInputHook(uint32_t address, uint32_t imageBase);
}
