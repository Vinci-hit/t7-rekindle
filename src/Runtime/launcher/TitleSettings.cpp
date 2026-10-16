#include "TitleSettings.h"
#include "RemoteImage.h"
#include <algorithm>
#include <cstring>

namespace t7r {
namespace {
class Code {
public:
    Bytes bytes;
    void emit(std::initializer_list<unsigned char> v) { bytes.insert(bytes.end(), v); }
    void word(uint32_t v) { for (unsigned i = 0; i < 32; i += 8) bytes.push_back(static_cast<unsigned char>(v >> i)); }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F, condition}); else emit({0xE9});
        auto p = bytes.size(); word(0); return p;
    }
    void bind(size_t p, size_t target) { auto d = static_cast<uint32_t>(target - p - 4); std::memcpy(bytes.data() + p, &d, 4); }
    void store(uint32_t at, uint32_t value) { emit({0xC7,0x05}); word(at); word(value); }
};
void write(HANDLE process, uint32_t address, const Bytes& bytes) {
    SIZE_T written = 0;
    if (!WriteProcessMemory(process, reinterpret_cast<void*>(static_cast<uintptr_t>(address)), bytes.data(), bytes.size(), &written)
        || written != bytes.size()) throw std::runtime_error(errorText("title mailbox write"));
}
}
bool validTitleDescriptions(const Bytes& bytes) {
    if (bytes.size() != 256) return false;
    for (size_t offset : {0u, 128u}) {
        const auto end = std::find(bytes.begin() + offset, bytes.begin() + offset + 128, 0);
        if (end == bytes.begin() + offset + 128 || std::any_of(end, bytes.begin() + offset + 128, [](auto b) { return b != 0; })) return false;
    }
    return true;
}
Bytes buildTitleHook(uint32_t address, uint32_t imageBase, uint32_t threadId) {
    if (!address || address > UINT32_MAX - TITLE_PAGE_SIZE || !imageBase || imageBase > UINT32_MAX - 0x2E249B8 || !threadId)
        throw std::runtime_error("title hook outside x86 range");
    Code c;
    c.emit({0x9C,0x60,0x64,0xA1}); c.word(0x24); c.emit({0x3D}); c.word(threadId);
    std::vector<size_t> idle{c.branch(0x85)};
    c.emit({0x83,0x3D}); c.word(address); c.emit({1}); idle.push_back(c.branch(0x85));
    c.store(address, 2);
    c.emit({0x8B,0xEC,0x81,0xEC}); c.word(0x210);
    c.emit({0x83,0xE4,0xF0,0x0F,0xAE,0x04,0x24,0xFC});
    c.emit({0xA1}); c.word(imageBase + 0x2440AD0); c.emit({0x85,0xC0});
    std::vector<size_t> unavailable{c.branch(0x84)};
    c.emit({0x81,0x38}); c.word(imageBase + 0x17302A4); unavailable.push_back(c.branch(0x85));
    c.emit({0x83,0x3D}); c.word(imageBase + 0x2E249B8); c.emit({0}); unavailable.push_back(c.branch(0x84));
    // Original resource owner: rows at +8, count at +12, 548-byte native records.
    // Resolve both IDs before editing either description.
    for (uint32_t i = 0; i < 2; ++i) {
        c.emit({0x8B,0x70,0x08,0x8B,0x48,0x0C,0x85,0xF6}); unavailable.push_back(c.branch(0x84));
        c.emit({0x85,0xC9}); unavailable.push_back(c.branch(0x8E));
        c.emit({0x81,0xF9}); c.word(1024); unavailable.push_back(c.branch(0x87));
        auto loop = c.bytes.size(); c.emit({0x81,0x3E}); c.word(1007 + i); auto found = c.branch(0x84);
        c.emit({0x81,0xC6}); c.word(548); c.emit({0x49}); auto next = c.branch(0x85); c.bind(next, loop);
        unavailable.push_back(c.branch()); c.bind(found, c.bytes.size());
        c.emit({0x83,0xC6,0x24,0x89,0x35}); c.word(address + 0x120 + i * 4);
    }
    for (uint32_t i = 0; i < 2; ++i) {
        c.emit({0xBE}); c.word(address + 0x10 + i * 128);
        c.emit({0x8B,0x3D}); c.word(address + 0x120 + i * 4);
        c.emit({0xB9}); c.word(32); c.emit({0xF3,0xA5});
    }
    // Rebuild the original playerTitleDataTable on the same frame thread.
    c.emit({0xB8}); c.word(imageBase + TITLE_REFRESH_RVA); c.emit({0xFF,0xD0});
    c.store(address + 4, 1); auto success = c.branch();
    for (auto p : unavailable) c.bind(p, c.bytes.size());
    c.store(address + 4, 0); c.bind(success, c.bytes.size());
    c.emit({0x0F,0xAE,0x0C,0x24,0x8B,0xE5}); c.store(address, 0);
    for (auto p : idle) c.bind(p, c.bytes.size());
    c.emit({0x61,0x9D,0xC3});
    if (c.bytes.size() > TITLE_PAGE_SIZE - TITLE_CODE_OFFSET) throw std::runtime_error("title hook overflow");
    Bytes page(TITLE_PAGE_SIZE, 0);
    std::copy(c.bytes.begin(), c.bytes.end(), page.begin() + TITLE_CODE_OFFSET);
    return page;
}
void TitleSettings::update(const Bytes& bytes) {
    if (!validTitleDescriptions(bytes)) throw std::runtime_error("invalid title descriptions");
    desired_ = bytes;
}
void TitleSettings::poll() {
    if (!process_ || desired_.empty()) return;
    if (pending_) {
        auto bytes = readClientMemory(process_, remote_, 8);
        uint32_t command = 0, result = 0;
        std::memcpy(&command, bytes.data(), 4); std::memcpy(&result, bytes.data() + 4, 4);
        if (command) {
            if (GetTickCount64() - requestedAt_ > 10000) throw std::runtime_error("title frame thread response timed out");
            return;
        }
        pending_ = false;
        if (result == 1) applied_ = sent_;
        else retryAt_ = requestedAt_ + 1000;
    }
    if (desired_ == applied_ || GetTickCount64() < retryAt_) return;
    write(process_, remote_ + 0x10, desired_);
    sent_ = desired_;
    write(process_, remote_, Bytes{1,0,0,0});
    pending_ = true; requestedAt_ = GetTickCount64();
}
}
