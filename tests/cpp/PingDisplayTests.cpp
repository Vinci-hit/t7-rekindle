#include "../../src/Runtime/launcher/DebugClient.h"
#include "../../src/Runtime/launcher/WorldHookCode.h"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, HUD_RVA = 0x795285, GETTER_RVA = 0x3B9090;
constexpr uint32_t SERVICE_POINTER = 0x31EC59C, MILLISECONDS = 0x1E74724;
constexpr uint32_t STATE = BASE + 0x8000, UI = BASE + 0x9000, SERVICES = BASE + 0x10000;
constexpr uint32_t RESULT = BASE + 0x16000, RESULT_SIZE = 24;
const t7r::Bytes HEARTBEAT_GETTER{
    0x8B,0x41,0x28,0x8B,0x10,0x3B,0xC2,0x74,0x1A,0x0F,0x57,0xC9,0x8D,0x64,0x24,0,
    0x8B,0x48,4,0xF3,0x0F,0x10,0x41,0x0C,0x0F,0x2F,0xC1,0x77,9,0x8B,0xC1,0x3B,
    0xC2,0x75,0xED,0x33,0xC0,0xC3,0xF3,0x0F,0x59,5,0x24,0x47,0xE7,1,0xF3,0x0F,
    0x2C,0xC0,0xC3};
const t7r::Bytes LOGIC_PING_DISPLAY{
    0x80,0x7F,0x21,0,0x74,0x14,0x8B,0x0D,0x9C,0xC5,0x1E,3,0x8A,0x41,0x1C,0x84,
    0xC0,0x74,7,0xE8,0x91,0x3C,0xC2,0xFF,0x8B,0xD8};
struct Sample {
    bool present, active;
    std::vector<float> seconds;
    int32_t expectedMs;
};
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
uint32_t floatBits(float value) {
    uint32_t bits = 0; std::memcpy(&bits, &value, sizeof(bits)); return bits;
}
std::vector<t7r::MemoryPatch> pingPatches() {
    t7r::Bytes image(0x3B54000, 0); size_t offset = 0x1000;
    for (const auto* name : {"TerSafe", "TerSafe", "GeTssAntiService", "GeTssAntiService", "GeTssAntiService",
                             "GeTssAntiService", "GeTssAntiService", "QQPCfix.dll"}) {
        std::copy_n(name, std::strlen(name), image.begin() + offset); offset += 32;
    }
    const auto plan = t7r::clientMemoryPatches(image);
    std::vector<t7r::MemoryPatch> patches;
    for (const auto rva : {HUD_RVA, GETTER_RVA + 0x18}) {
        const auto found = std::find_if(plan.begin(), plan.end(), [=](const auto& patch) { return patch.rva == rva; });
        require(found != plan.end(), "latest heartbeat Ping adaptation missing");
        patches.push_back(*found);
    }
    require(patches[0].expected.size() == 37 && patches[1].expected == t7r::Bytes({0x0F,0x2F,0xC1,0x77,9})
        && patches[1].replacement == t7r::Bytes({0x0F,0x2F,0xC1,0x73,9}), "Ping adaptation signature/extent mismatch");
    return patches;
}
uint32_t createFixture(const t7r::fs::path& path, const std::vector<t7r::MemoryPatch>& patches,
                       const std::vector<Sample>& samples) {
    constexpr uint32_t HEADER = 0x200, RAW_SIZE = 0x795000;
    t7r::Bytes image(HEADER + RAW_SIZE, 0);
    IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
    std::memcpy(image.data(), &dos, sizeof(dos));
    IMAGE_NT_HEADERS32 nt{}; nt.Signature = IMAGE_NT_SIGNATURE;
    nt.FileHeader.Machine = IMAGE_FILE_MACHINE_I386; nt.FileHeader.NumberOfSections = 1;
    nt.FileHeader.SizeOfOptionalHeader = sizeof(nt.OptionalHeader);
    nt.FileHeader.Characteristics = IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_32BIT_MACHINE | IMAGE_FILE_RELOCS_STRIPPED;
    nt.OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR32_MAGIC;
    nt.OptionalHeader.ImageBase = BASE; nt.OptionalHeader.AddressOfEntryPoint = 0x1000;
    nt.OptionalHeader.BaseOfCode = 0x1000; nt.OptionalHeader.SizeOfCode = RAW_SIZE;
    nt.OptionalHeader.SectionAlignment = 0x1000; nt.OptionalHeader.FileAlignment = 0x200;
    nt.OptionalHeader.MajorOperatingSystemVersion = 6; nt.OptionalHeader.MajorSubsystemVersion = 6;
    nt.OptionalHeader.SizeOfImage = 0x3B54000; nt.OptionalHeader.SizeOfHeaders = HEADER;
    nt.OptionalHeader.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    nt.OptionalHeader.SizeOfStackReserve = 0x100000; nt.OptionalHeader.SizeOfStackCommit = 0x1000;
    nt.OptionalHeader.SizeOfHeapReserve = 0x100000; nt.OptionalHeader.SizeOfHeapCommit = 0x1000;
    nt.OptionalHeader.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
    std::memcpy(image.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = nt.OptionalHeader.SizeOfImage - 0x1000;
    section.PointerToRawData = HEADER; section.SizeOfRawData = RAW_SIZE;
    section.Characteristics = IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE;
    std::memcpy(image.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    const auto put = [&](uint32_t rva, const t7r::Bytes& bytes) {
        const auto raw = HEADER + rva - 0x1000;
        require(raw + bytes.size() <= image.size(), "Ping fixture extent");
        std::copy(bytes.begin(), bytes.end(), image.begin() + raw);
    };
    put(GETTER_RVA, HEARTBEAT_GETTER);
    put(HUD_RVA, patches[0].expected);
    put(HUD_RVA + static_cast<uint32_t>(patches[0].expected.size()), {0xCC});
    put(0x7953E7, LOGIC_PING_DISPLAY);
    put(0x795401, {0xC3}); // Resume the existing HUD with ESI=Ping and EBX=optional logic Ping.
    t7r::WorldHookCode code;
    code.store(MILLISECONDS, floatBits(1000.f));
    code.emit({0xBD}); code.word(STATE + 0x100);
    code.emit({0xBF}); code.word(UI);
    code.store(STATE + 0xFC, 1); // Both HUD string locals are already constructed at the patch site.
    code.store(STATE + 0x98, floatBits(60.f));
    const auto store = [&](unsigned char modrm, uint32_t address) { code.emit({0x89,modrm}); code.word(address); };
    store(0x25, STATE);
    for (size_t index = 0; index < samples.size(); ++index) {
        const auto& sample = samples[index];
        const auto service = SERVICES + static_cast<uint32_t>(index) * 0x200, head = service + 0x40;
        code.store(SERVICE_POINTER, sample.present ? service : 0);
        code.store(service + 0x1C, static_cast<uint32_t>(sample.active));
        code.store(service + 0x28, head);
        code.store(head, sample.seconds.empty() ? head : head + 16);
        code.store(head + 4, head + static_cast<uint32_t>(sample.seconds.size()) * 16);
        for (size_t i = 0; i < sample.seconds.size(); ++i) {
            const auto node = head + static_cast<uint32_t>(i + 1) * 16;
            code.store(node, i + 1 == sample.seconds.size() ? head : node + 16);
            code.store(node + 4, node - 16);
            code.store(node + 8, static_cast<uint32_t>(i + 1));
            code.store(node + 12, floatBits(sample.seconds[i]));
        }
        for (uint32_t diagnostic = 0; diagnostic < 2; ++diagnostic) {
            code.store(UI + 0x21, diagnostic);
            code.emit({0x83,0xCB,0xFF,0x83,0xCE,0xFF});
            code.call(BASE + HUD_RVA);
            const auto result = RESULT + (static_cast<uint32_t>(index) * 2 + diagnostic) * RESULT_SIZE;
            store(0x35, result); store(0x1D, result + 4); store(0x3D, result + 8);
            store(0x2D, result + 12); store(0x25, result + 16);
            code.emit({0xA1}); code.word(STATE + 0xFC); store(0x05, result + 20);
        }
    }
    const auto complete = BASE + 0x1000 + static_cast<uint32_t>(code.bytes.size());
    code.emit({0xCC,0xEB,0xFE});
    require(code.bytes.size() < 0x7000, "Ping fixture code overlaps state");
    put(0x1000, code.bytes);
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(image.data()), static_cast<std::streamsize>(image.size()));
    require(output.good(), "Ping fixture write");
    return complete;
}
void execute(const t7r::fs::path& path, const std::vector<t7r::MemoryPatch>& patches,
             const std::vector<Sample>& samples, uint32_t complete) {
    std::atomic<bool> finished{false}; t7r::DebugClient client;
    client.start(path, t7r::fileHash(path), {}, [&](HANDLE process, HANDLE) {
        t7r::applyRemotePatches(process, BASE, patches);
    }, {}, {}, {}, [&](DWORD, uintptr_t address) {
        if (address != complete) return false;
        finished.store(true); return true;
    });
    const auto deadline = GetTickCount64() + 10000;
    while (!finished.load() && GetTickCount64() < deadline) { client.check(); Sleep(2); }
    require(finished.load(), "Ping fixture did not complete");
    const auto read = [&](uint32_t address) {
        const auto bytes = t7r::readClientMemory(client.process(), address, 4);
        uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
    };
    for (size_t index = 0; index < samples.size(); ++index) {
        const auto& sample = samples[index];
        for (uint32_t diagnostic = 0; diagnostic < 2; ++diagnostic) {
            const auto result = RESULT + (static_cast<uint32_t>(index) * 2 + diagnostic) * RESULT_SIZE;
            require(static_cast<int32_t>(read(result)) == sample.expectedMs, "HUD Ping is not the latest completed heartbeat RTT");
            const auto logic = diagnostic && sample.present && sample.active ? sample.expectedMs : -1;
            require(static_cast<int32_t>(read(result + 4)) == logic, "Ping adaptation changed optional diagnostics");
            require(read(result + 8) == UI && read(result + 12) == STATE + 0x100
                && read(result + 16) == read(STATE) && read(result + 20) == 1,
                "Ping adaptation changed frame, stack or exception cleanup state");
        }
    }
    require(read(STATE + 0x98) == floatBits(60.f), "Ping adaptation changed FPS");
    client.stop(); client.check();
}
}
bool verifyPingDisplay() {
    const auto root = t7r::fs::temp_directory_path() / (L"t7r-ping-" + std::to_wstring(GetCurrentProcessId())
                                                     + L"-" + std::to_wstring(GetTickCount64()));
    const auto cleanup = [&] {
        require(t7r::fs::canonical(root).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()),
                "Ping fixture cleanup boundary");
        t7r::fs::remove_all(root);
    };
    try {
        const auto patches = pingPatches();
        const std::vector<Sample> samples{
            {false,false,{},-1}, {true,false,{.125f},-1}, {true,true,{},0},
            {true,true,{-1.f},0}, {true,true,{.03125f},31}, {true,true,{.03125f,.125f},125},
            {true,true,{.125f,.00390625f},3}, {true,true,{.125f,-1.f},125},
            {true,true,{.125f,0.f,-1.f},0}, {true,true,{.125f,.00025f},0},
            {true,true,{.03125f,1.5f},1500}, {true,false,{1.5f},-1}, {true,true,{.0625f},62}};
        require(t7r::fs::create_directory(root), "fresh Ping fixture directory");
        const auto path = root / "ping.exe";
        const auto complete = createFixture(path, patches, samples);
        execute(path, patches, samples, complete);
        cleanup();
        std::cout << "Latest heartbeat RTT, zero/pending samples, HUD diagnostics and x86 calling convention passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Ping display test failure: " << error.what() << '\n';
        try { if (t7r::fs::exists(root)) cleanup(); }
        catch (const std::exception& cleanupError) { std::cerr << "Ping fixture cleanup: " << cleanupError.what() << '\n'; }
        return false;
    }
}
