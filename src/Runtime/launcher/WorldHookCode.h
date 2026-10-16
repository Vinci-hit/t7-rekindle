#pragma once
#include "../core/Common.h"
#include <cstring>

namespace t7r {
class WorldHookCode {
public:
    Bytes bytes;
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void word(uint32_t value) {
        for (unsigned s = 0; s < 32; s += 8) bytes.push_back(static_cast<unsigned char>(value >> s));
    }
    size_t branch(unsigned char condition) {
        emit({0x0F, condition}); const auto position = bytes.size(); word(0); return position;
    }
    size_t jump() { emit({0xE9}); const auto position = bytes.size(); word(0); return position; }
    void bind(size_t position) { bind(position, bytes.size()); }
    void bind(size_t position, size_t target) {
        const auto delta = static_cast<uint32_t>(target - position - 4);
        std::memcpy(bytes.data() + position, &delta, 4);
    }
    void store(uint32_t address, uint32_t value) { emit({0xC7, 0x05}); word(address); word(value); }
    void call(uint32_t address) { emit({0xB8}); word(address); emit({0xFF, 0xD0}); }
};
}
