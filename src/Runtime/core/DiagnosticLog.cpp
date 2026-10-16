#include "DiagnosticLog.h"
#include <algorithm>
#include <fstream>
#include <mutex>
#include <regex>

namespace t7r {
namespace {
constexpr size_t MAX_RECORD_BYTES = 8192;
constexpr unsigned MAX_ARCHIVES = 7;
std::mutex fileMutex;

void rotateNativeLog(const fs::path& directory) {
    const auto path = directory / "native.log";
    if (!fs::exists(path)) return;
    if (!fs::is_regular_file(path)) throw std::runtime_error("native.log is not a regular file");
    WIN32_FILE_ATTRIBUTE_DATA attributes{};
    SYSTEMTIME utc{}, written{}, now{};
    if (!GetFileAttributesExW(path.c_str(), GetFileExInfoStandard, &attributes)
        || !FileTimeToSystemTime(&attributes.ftLastWriteTime, &utc)
        || !SystemTimeToTzSpecificLocalTime(nullptr, &utc, &written))
        throw std::runtime_error(errorText("native.log date read"));
    GetLocalTime(&now);
    if ((!attributes.nFileSizeHigh && !attributes.nFileSizeLow)
        || (written.wYear == now.wYear && written.wMonth == now.wMonth && written.wDay == now.wDay)) return;

    char date[11];
    sprintf_s(date, "%04u-%02u-%02u", written.wYear, written.wMonth, written.wDay);
    unsigned sequence = 1;
    fs::path archive;
    do { archive = directory / (std::string("native.") + date + "." + std::to_string(sequence++) + ".log"); }
    while (fs::exists(archive));
    fs::rename(path, archive);

    // Retention starts only after the active file was successfully archived.
    static const std::regex archiveName(R"(^native\.[0-9]{4}-[0-9]{2}-[0-9]{2}\.[0-9]+\.log$)");
    std::vector<std::pair<fs::file_time_type, fs::path>> archives;
    for (const auto& entry : fs::directory_iterator(directory))
        if (entry.is_regular_file() && std::regex_match(entry.path().filename().string(), archiveName))
            archives.emplace_back(entry.last_write_time(), entry.path());
    std::sort(archives.begin(), archives.end());
    for (size_t index = 0; index + MAX_ARCHIVES < archives.size(); ++index) fs::remove(archives[index].second);
}
}

const char* diagnosticLevelName(DiagnosticLogLevel level) {
    switch (level) {
    case DiagnosticLogLevel::Debug: return "DEBUG";
    case DiagnosticLogLevel::Info: return "INFO";
    case DiagnosticLogLevel::Warn: return "WARN";
    case DiagnosticLogLevel::Error: return "ERROR";
    default: throw std::invalid_argument("invalid diagnostic level");
    }
}

DiagnosticLogLevel parseDiagnosticLevel(const std::string& level) {
    if (level == "DEBUG") return DiagnosticLogLevel::Debug;
    if (level == "INFO") return DiagnosticLogLevel::Info;
    if (level == "WARN" || level == "WARNING") return DiagnosticLogLevel::Warn;
    if (level == "ERROR") return DiagnosticLogLevel::Error;
    throw std::invalid_argument("invalid diagnostic level");
}

DiagnosticLogLevel diagnosticRecordLevel(const std::string& record) {
    const auto end = record.find("  [", 21);
    if (record.size() < 21 || record.compare(19, 2, "  ") != 0 || end == std::string::npos)
        throw std::invalid_argument("invalid diagnostic record");
    return parseDiagnosticLevel(record.substr(21, end - 21));
}

std::string formatDiagnosticRecord(const std::string& message, const char* level, const char* source) {
    return formatDiagnosticRecord(message, parseDiagnosticLevel(level), source);
}

std::string formatDiagnosticRecord(const std::string& message, DiagnosticLogLevel level, const char* source) {
    SYSTEMTIME time{};
    GetLocalTime(&time);
    char timestamp[20];
    sprintf_s(timestamp, "%04u-%02u-%02u %02u:%02u:%02u", time.wYear, time.wMonth, time.wDay,
              time.wHour, time.wMinute, time.wSecond);
    return std::string(timestamp) + "  " + diagnosticLevelName(level) + "  [" + source + "] " + message;
}

std::string boundedDiagnosticRecord(const std::string& record) {
    constexpr char suffix[] = " ... [truncated]";
    size_t length = record.size();
    if (length > MAX_RECORD_BYTES) {
        length = MAX_RECORD_BYTES - (sizeof(suffix) - 1);
        while (length && (static_cast<unsigned char>(record[length]) & 0xc0) == 0x80) --length;
    }
    auto result = record.substr(0, length);
    std::replace(result.begin(), result.end(), '\r', ' ');
    std::replace(result.begin(), result.end(), '\n', ' ');
    if (length < record.size()) result += suffix;
    return result;
}

void appendNativeLog(const fs::path& directory, const std::string& record) {
    std::lock_guard<std::mutex> lock(fileMutex);
    fs::create_directories(directory);
    rotateNativeLog(directory);
    std::ofstream output(directory / "native.log", std::ios::binary | std::ios::app);
    output << record << '\n';
    output.flush();
    if (!output) throw std::runtime_error("native.log write failed");
}
}
