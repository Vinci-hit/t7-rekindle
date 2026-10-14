#pragma once
#include "../core/Common.h"
#include <functional>
#include <propidl.h>

namespace t7r {
std::string describeAudioFormat(const void* data, size_t size);
std::string describeSystemEffects(HRESULT result, const PROPVARIANT& value);
bool isAudioDiagnosticModule(const std::wstring& name);
std::string describeAudioModuleVersion(HANDLE file);
void logAudioEnvironment(const std::function<void(std::string)>& log);
}
