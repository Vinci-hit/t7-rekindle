#include "Session.h"

namespace t7r::bridge {
int32_t Session::publishTitles(uint64_t epoch, std::string json, uint64_t requestId, bool success, Bytes descriptions) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7RNB_INVALID_HANDLE;
    if (snapshot_.state != T7RNB_STATE_RUNNING || snapshot_.operation != T7RNB_OPERATION_NONE) return T7RNB_NOT_READY;
    if (titleUpdates_.size() >= 16) return T7RNB_BUSY;
    titleUpdates_.push_back({epoch, requestId, std::move(json), success, std::move(descriptions)});
    return T7RNB_OK;
}
int32_t Session::titleRequest(TitleRequest& result) const {
    std::lock_guard<std::mutex> lock(mutex_);
    if (closing_) return T7RNB_INVALID_HANDLE;
    result = snapshot_.state == T7RNB_STATE_RUNNING ? titleRequest_ : TitleRequest{};
    return T7RNB_OK;
}
void Session::monitorTitles() {
    if (!server_ || !server_->running()) return;
    try {
        for (;;) {
            TitleUpdate update;
            {
                std::lock_guard<std::mutex> lock(mutex_);
                if (titleUpdates_.empty()) break;
                update = titleUpdates_.front();
            }
            server_->publishTitles(update.epoch, update.json, update.requestId, update.success);
            if (!update.descriptions.empty()) bootstrap_.updateTitleDescriptions(update.descriptions);
            std::lock_guard<std::mutex> lock(mutex_); titleUpdates_.pop_front();
        }
        bootstrap_.pollTitleDescriptions();
        const auto request = server_->titleRequest();
        std::lock_guard<std::mutex> lock(mutex_); titleRequest_ = request; titleError_.clear();
    } catch (const std::exception& error) {
        if (titleError_ != error.what()) {
            titleError_ = error.what();
            log("Title synchronization failed: " + titleError_, DiagnosticLogLevel::Error);
        }
    }
}
}
