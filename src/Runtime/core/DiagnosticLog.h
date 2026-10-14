#pragma once
#include "Common.h"
#include <atomic>
#include <functional>
#include <memory>

namespace t7r {
enum class DiagnosticLogLevel : uint32_t { Debug = 0, Info = 1, Warn = 2, Error = 3 };
using DiagnosticLogCallback = std::function<void(DiagnosticLogLevel, std::string)>;
struct DiagnosticMessage {
    DiagnosticLogLevel level;
    std::string message;
};
struct DiagnosticLogFilter {
    std::atomic<DiagnosticLogLevel> minimum{DiagnosticLogLevel::Info};
    bool allows(DiagnosticLogLevel level) const { return level >= minimum.load(); }
};
const char* diagnosticLevelName(DiagnosticLogLevel level);
DiagnosticLogLevel parseDiagnosticLevel(const std::string& level);
DiagnosticLogLevel diagnosticRecordLevel(const std::string& record);
std::string formatDiagnosticRecord(const std::string& message, DiagnosticLogLevel level, const char* source);
std::string formatDiagnosticRecord(const std::string& message, const char* level, const char* source);
std::string boundedDiagnosticRecord(const std::string& record);
void appendNativeLog(const fs::path& directory, const std::string& record);
}
