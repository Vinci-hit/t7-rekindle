#include "AudioDiagnostics.h"
#include <initguid.h>
#include <algorithm>
#include <audioclient.h>
#include <cstring>
#include <cwctype>
#include <propkeydef.h>
#include <functiondiscoverykeys_devpkey.h>
#include <memory>
#include <mmdeviceapi.h>
#include <sstream>
#include <wrl/client.h>

namespace t7r {
namespace {
using Microsoft::WRL::ComPtr;
std::string hexValue(uint32_t value) {
    std::ostringstream text; text << "0x" << std::hex << std::uppercase << value; return text.str();
}
std::string unknown(HRESULT result) { return "unknown; hr=" + hexValue(static_cast<uint32_t>(result)); }
struct Property {
    PROPVARIANT value{};
    ~Property() { PropVariantClear(&value); }
};
struct Apartment {
    HRESULT result = CoInitializeEx(nullptr, COINIT_MULTITHREADED);
    ~Apartment() { if (SUCCEEDED(result)) CoUninitialize(); }
};
struct ServiceHandle {
    SC_HANDLE value;
    ~ServiceHandle() { if (value) CloseServiceHandle(value); }
};
std::string readProperty(IPropertyStore* store, const PROPERTYKEY& key) {
    Property property;
    const auto result = store->GetValue(key, &property.value);
    if (IsEqualPropertyKey(key, PKEY_AudioEndpoint_Disable_SysFx))
        return describeSystemEffects(result, property.value);
    if (FAILED(result)) return unknown(result);
    const auto& value = property.value;
    if (value.vt == VT_LPWSTR && value.pwszVal) return jsonString(utf8(value.pwszVal));
    if (value.vt == VT_UI4) return hexValue(value.ulVal);
    if (value.vt == VT_BLOB) return describeAudioFormat(value.blob.pBlobData, value.blob.cbSize);
    return "unknown; vt=" + std::to_string(value.vt);
}
std::string mixFormat(IMMDevice* device) {
    ComPtr<IAudioClient> client;
    auto result = device->Activate(__uuidof(IAudioClient), CLSCTX_INPROC_SERVER, nullptr,
        reinterpret_cast<void**>(client.GetAddressOf()));
    if (FAILED(result)) return unknown(result);
    WAVEFORMATEX* raw = nullptr;
    result = client->GetMixFormat(&raw);
    const std::unique_ptr<WAVEFORMATEX, decltype(&CoTaskMemFree)> format(raw, CoTaskMemFree);
    if (FAILED(result)) return unknown(result);
    // Query only: never initialize/start a stream or request a different device format.
    return raw ? describeAudioFormat(raw, sizeof(WAVEFORMATEX) + raw->cbSize) : "unknown; reason=null-format";
}
void logEndpoint(IMMDeviceEnumerator* enumerator, ERole role, const char* roleName,
                 const std::function<void(std::string)>& log) {
    const auto prefix = std::string("Audio endpoint; role=") + roleName;
    ComPtr<IMMDevice> device;
    auto result = enumerator->GetDefaultAudioEndpoint(eRender, role, &device);
    if (FAILED(result)) { log(prefix + "; status=" + unknown(result)); return; }
    DWORD state = 0;
    result = device->GetState(&state);
    log(prefix + "; state=" + (FAILED(result) ? unknown(result) : hexValue(state)));
    ComPtr<IPropertyStore> properties;
    result = device->OpenPropertyStore(STGM_READ, &properties);
    if (FAILED(result)) log(prefix + "; properties=" + unknown(result));
    else {
        log(prefix + "; name=" + readProperty(properties.Get(), PKEY_Device_FriendlyName)
            + "; sysFx=" + readProperty(properties.Get(), PKEY_AudioEndpoint_Disable_SysFx)
            + "; physicalSpeakers=" + readProperty(properties.Get(), PKEY_AudioEndpoint_PhysicalSpeakers));
        log(prefix + "; deviceFormat={" + readProperty(properties.Get(), PKEY_AudioEngine_DeviceFormat) + "}");
    }
    log(prefix + "; mixFormat={" + mixFormat(device.Get()) + "}");
}
const char* serviceState(DWORD state) {
    switch (state) {
    case SERVICE_STOPPED: return "stopped";
    case SERVICE_START_PENDING: return "start-pending";
    case SERVICE_STOP_PENDING: return "stop-pending";
    case SERVICE_RUNNING: return "running";
    case SERVICE_CONTINUE_PENDING: return "continue-pending";
    case SERVICE_PAUSE_PENDING: return "pause-pending";
    case SERVICE_PAUSED: return "paused";
    default: return "unknown";
    }
}
void logService(SC_HANDLE manager, const wchar_t* name, const std::function<void(std::string)>& log) {
    const auto prefix = "Audio service; name=" + utf8(name);
    ServiceHandle service{OpenServiceW(manager, name, SERVICE_QUERY_STATUS | SERVICE_QUERY_CONFIG)};
    if (!service.value) {
        const auto error = GetLastError();
        log(prefix + (error == ERROR_SERVICE_DOES_NOT_EXIST ? "; status=not-installed" : "; status=unknown")
            + "; win32=" + std::to_string(error));
        return;
    }
    SERVICE_STATUS_PROCESS status{}; DWORD needed = 0;
    if (QueryServiceStatusEx(service.value, SC_STATUS_PROCESS_INFO, reinterpret_cast<BYTE*>(&status), sizeof(status), &needed))
        log(prefix + "; state=" + serviceState(status.dwCurrentState) + "; PID=" + std::to_string(status.dwProcessId));
    else log(prefix + "; state=unknown; win32=" + std::to_string(GetLastError()));
    QueryServiceConfigW(service.value, nullptr, 0, &needed);
    const auto error = GetLastError();
    if (error != ERROR_INSUFFICIENT_BUFFER || needed > 8192 || needed < sizeof(QUERY_SERVICE_CONFIGW)) {
        log(prefix + "; startType=unknown; win32=" + std::to_string(error)); return;
    }
    Bytes bytes(needed);
    auto config = reinterpret_cast<QUERY_SERVICE_CONFIGW*>(bytes.data());
    if (!QueryServiceConfigW(service.value, config, needed, &needed)) {
        log(prefix + "; startType=unknown; win32=" + std::to_string(GetLastError())); return;
    }
    const auto type = config->dwStartType;
    const char* label = type == SERVICE_AUTO_START ? "automatic" : type == SERVICE_DEMAND_START ? "manual"
        : type == SERVICE_DISABLED ? "disabled" : "other";
    log(prefix + "; startType=" + label + "(" + std::to_string(type) + ")");
}
}
std::string describeAudioFormat(const void* data, size_t size) {
    constexpr size_t PCM_SIZE = 16;
    if (!data || size < PCM_SIZE) return "unknown; reason=truncated-format";
    WAVEFORMATEX format{};
    std::memcpy(&format, data, std::min(size, sizeof(format)));
    if (size >= sizeof(format) && sizeof(format) + format.cbSize > size)
        return "unknown; reason=truncated-extension";
    auto text = "formatTag=" + hexValue(format.wFormatTag) + "; sampleRate=" + std::to_string(format.nSamplesPerSec)
        + "; channels=" + std::to_string(format.nChannels) + "; bitsPerSample=" + std::to_string(format.wBitsPerSample)
        + "; blockAlign=" + std::to_string(format.nBlockAlign);
    if (format.wFormatTag == WAVE_FORMAT_EXTENSIBLE) {
        if (size < sizeof(WAVEFORMATEXTENSIBLE) || format.cbSize < sizeof(WAVEFORMATEXTENSIBLE) - sizeof(WAVEFORMATEX))
            return "unknown; reason=truncated-extensible-format";
        WAVEFORMATEXTENSIBLE extended{}; std::memcpy(&extended, data, sizeof(extended));
        wchar_t subtype[40]{};
        StringFromGUID2(extended.SubFormat, subtype, static_cast<int>(std::size(subtype)));
        text += "; validBits=" + std::to_string(extended.Samples.wValidBitsPerSample)
            + "; channelMask=" + hexValue(extended.dwChannelMask) + "; subFormat=" + utf8(subtype);
    }
    return text;
}
std::string describeSystemEffects(HRESULT result, const PROPVARIANT& value) {
    if (FAILED(result)) return unknown(result);
    if (value.vt != VT_UI4) return "unknown; vt=" + std::to_string(value.vt);
    if (value.ulVal == ENDPOINT_SYSFX_DISABLED) return "disabled";
    if (value.ulVal == ENDPOINT_SYSFX_ENABLED) return "enabled";
    return "unknown; value=" + std::to_string(value.ulVal);
}
bool isAudioDiagnosticModule(const std::wstring& name) {
    auto normalized = name;
    std::transform(normalized.begin(), normalized.end(), normalized.begin(), [](wchar_t c) { return static_cast<wchar_t>(std::towlower(c)); });
    return normalized == L"aksoundenginedll.dll" || normalized.rfind(L"xaudio2_", 0) == 0
        || normalized == L"audiodevprops2.dll" || normalized == L"asm.plugin.audiodevprops2.dll"
        || normalized.rfind(L"nahimic", 0) == 0;
}
std::string describeAudioModuleVersion(HANDLE file) {
    if (!file || file == INVALID_HANDLE_VALUE) return "version=unknown; reason=no-file-handle";
    std::wstring path(32768, L'\0');
    const auto length = GetFinalPathNameByHandleW(file, path.data(), static_cast<DWORD>(path.size()), FILE_NAME_NORMALIZED);
    if (!length) return "version=unknown; win32=" + std::to_string(GetLastError());
    if (length >= path.size()) return "version=unknown; reason=path-too-long";
    path.resize(length);
    DWORD ignored = 0;
    const auto size = GetFileVersionInfoSizeW(path.c_str(), &ignored);
    if (!size) return "version=unknown; win32=" + std::to_string(GetLastError());
    Bytes bytes(size);
    if (!GetFileVersionInfoW(path.c_str(), 0, size, bytes.data()))
        return "version=unknown; win32=" + std::to_string(GetLastError());
    VS_FIXEDFILEINFO* info = nullptr; UINT infoSize = 0;
    if (!VerQueryValueW(bytes.data(), L"\\", reinterpret_cast<void**>(&info), &infoSize)
        || !info || infoSize < sizeof(*info) || info->dwSignature != 0xFEEF04BD)
        return "version=unknown; reason=no-fixed-version";
    return "version=" + std::to_string(HIWORD(info->dwFileVersionMS)) + "." + std::to_string(LOWORD(info->dwFileVersionMS))
        + "." + std::to_string(HIWORD(info->dwFileVersionLS)) + "." + std::to_string(LOWORD(info->dwFileVersionLS));
}
void logAudioEnvironment(const std::function<void(std::string)>& log) {
    if (!log) return;
    log("Audio diagnostics begin; scope=system-default-render-endpoints; mode=read-only; gameRoute=not-observed");
    try {
        ServiceHandle manager{OpenSCManagerW(nullptr, nullptr, SC_MANAGER_CONNECT)};
        if (!manager.value) log("Audio services; status=unknown; win32=" + std::to_string(GetLastError()));
        else for (const auto* service : {L"Audiosrv", L"AudioEndpointBuilder", L"NahimicService"}) logService(manager.value, service, log);
    } catch (const std::exception&) { log("Audio services; status=unknown; reason=probe-exception"); }
    try {
        Apartment apartment;
        if (FAILED(apartment.result) && apartment.result != RPC_E_CHANGED_MODE) {
            log("Audio endpoints; initialize=" + unknown(apartment.result));
        } else {
            ComPtr<IMMDeviceEnumerator> enumerator;
            const auto result = CoCreateInstance(__uuidof(MMDeviceEnumerator), nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&enumerator));
            if (FAILED(result)) log("Audio endpoints; enumerator=" + unknown(result));
            else {
                const char* names[]{"console", "multimedia", "communications"};
                for (int role = 0; role < 3; ++role) {
                    try { logEndpoint(enumerator.Get(), static_cast<ERole>(role), names[role], log); }
                    catch (const std::exception&) { log(std::string("Audio endpoint; role=") + names[role] + "; status=unknown; reason=probe-exception"); }
                }
            }
        }
    } catch (const std::exception&) { log("Audio endpoints; status=unknown; reason=probe-exception"); }
    log("Audio diagnostics end; playbackStatus=not-observed");
}
}
