#pragma once
#include "../core/Common.h"

namespace t7r {
constexpr uint32_t TITLE_PAGE_SIZE = 0x2000, TITLE_CODE_OFFSET = 0x1000;
constexpr uint32_t TITLE_REFRESH_RVA = 0x5AB130;
Bytes buildTitleHook(uint32_t address, uint32_t imageBase, uint32_t threadId);
bool validTitleDescriptions(const Bytes& bytes);
class TitleSettings {
public:
    void attach(HANDLE process, uint32_t address) { process_ = process; remote_ = address; }
    void clear() { process_ = nullptr; remote_ = 0; pending_ = false; retryAt_ = 0; desired_.clear(); sent_.clear(); applied_.clear(); }
    void update(const Bytes& bytes);
    void poll();
private:
    HANDLE process_ = nullptr;
    uint32_t remote_ = 0;
    bool pending_ = false;
    uint64_t requestedAt_ = 0, retryAt_ = 0;
    Bytes desired_, sent_, applied_;
};
}
