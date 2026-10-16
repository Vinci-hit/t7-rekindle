#include "../../src/Runtime/launcher/DebugClient.h"
#include <algorithm>
#include <atomic>
#include <cstring>
#include <fstream>
#include <iostream>

namespace {
constexpr uint32_t BASE = 0x400000, LEVEL_RVA = 0x495DA6, SETTINGS_RVA = 0x4810E0;
constexpr uint32_t HELPER = BASE + 0x8000, SERVICE = BASE + 0x8100, SETTING = BASE + 0x8500;
constexpr uint32_t STATE = BASE + 0x8600, RESULT = BASE + 0x9000, RESULT_SIZE = 48;
constexpr uint32_t REGISTER_CALLS = STATE + 4, UNREGISTER_CALLS = STATE + 8;
constexpr uint32_t DEFENSE_CALLS = STATE + 12, ATTACK_CALLS = STATE + 16, BAD_CONTEXT = STATE + 20;
const t7r::Bytes LEVEL_GATE{
    0x3B,0x9E,0xA8,0x02,0,0,0x7F,0x21,0x39,0x38,0x74,0x1D,
    0x8B,0x0D,0xC8,0x5A,0x23,0x03,0xE8,0xC3,0x5B,0x25,0,0x84,0xC0,0x74,0x0E,
    0x8B,0x8E,0xA4,0x02,0,0,0x85,0xC9,0x74,0x15,0x6A,0x02,0xEB,0x0C,
    0x8B,0x8E,0xA4,0x02,0,0,0x85,0xC9,0x74,0x07,0x6A,0,0xE8,0xC0,0x95,0xFE,0xFF,0xC3};
const t7r::Bytes SETTINGS_CALLBACK{
    0x56,0x8B,0xF1,0x8B,0x0D,0xC8,0x5A,0x23,0x03,0xE8,0x92,0xA8,0x26,0,
    0x8B,0xCE,0x84,0xC0,0x74,0x12,0xE8,0xB7,0,0,0,0x8B,0xCE,0xE8,0x20,0,0,0,
    0xC6,0x46,0x54,0x01,0x5E,0xC3,0xE8,0xA5,0,0,0,0xC6,0x46,0x54,0,0x5E,0xC3};
struct Scenario {
    uint32_t level;
    bool profession, enabled, fightable;
    uint32_t attackMode;
    std::vector<bool> updates;
};
void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
void word(t7r::Bytes& bytes, uint32_t value) {
    for (unsigned shift = 0; shift < 32; shift += 8) bytes.push_back(static_cast<unsigned char>(value >> shift));
}
std::vector<t7r::MemoryPatch> defensePatches() {
    t7r::Bytes image(0x3B54000, 0); size_t offset = 0x1000;
    for (const auto* name : {"TerSafe", "TerSafe", "GeTssAntiService", "GeTssAntiService", "GeTssAntiService",
                             "GeTssAntiService", "GeTssAntiService", "QQPCfix.dll"}) {
        std::copy_n(name, std::strlen(name), image.begin() + offset); offset += 32;
    }
    const auto plan = t7r::clientMemoryPatches(image);
    std::vector<t7r::MemoryPatch> patches;
    for (const auto rva : {LEVEL_RVA, SETTINGS_RVA}) {
        const auto found = std::find_if(plan.begin(), plan.end(), [=](const auto& patch) { return patch.rva == rva; });
        require(found != plan.end(), "native defense assist adaptation missing");
        patches.push_back(*found);
    }
    require(patches[0].expected == t7r::Bytes(LEVEL_GATE.begin(), LEVEL_GATE.begin() + 8)
        && patches[0].replacement.size() == 8
        && std::equal(LEVEL_GATE.begin(), LEVEL_GATE.begin() + 6, patches[0].replacement.begin())
        && patches[0].replacement[6] == 0x90 && patches[0].replacement[7] == 0x90,
        "defense assist changed more than the level gate");
    require(patches[1].expected == SETTINGS_CALLBACK && patches[1].replacement.size() == SETTINGS_CALLBACK.size(),
            "defense settings callback signature/extent mismatch");
    return patches;
}
uint32_t createFixture(const t7r::fs::path& path, const std::vector<Scenario>& scenarios) {
    constexpr uint32_t HEADER = 0x200, RAW_SIZE = 0x6EC000;
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
    nt.OptionalHeader.SizeOfImage = 0x2E36000; nt.OptionalHeader.SizeOfHeaders = HEADER;
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
        require(raw + bytes.size() <= image.size(), "defense fixture extent");
        std::copy(bytes.begin(), bytes.end(), image.begin() + raw);
    };
    put(LEVEL_RVA, LEVEL_GATE); put(SETTINGS_RVA, SETTINGS_CALLBACK);
    // Keep the native mode setter and input dispatcher; replace engine effects with counted calls.
    put(0x47F3A0, {0x55,0x8B,0xEC,0x8B,0x45,0x08,0x89,0x41,0x20,0x85,0xC0,0x75,0x11,
        0x39,0x41,0x24,0x75,0x1B,0x88,0x41,0x54,0xE8,0xF6,0x1D,0,0,0x5D,0xC2,0x04,0,
        0x83,0x79,0x24,0,0x75,0x09,0xC6,0x41,0x54,0x01,0xE8,0x53,0x1D,0,0,0x5D,0xC2,0x04,0});
    put(0x47FA10, {0x55,0x8B,0xEC,0x56,0x8B,0xF1,0x83,0x7E,0x20,0x02,0x75,0x11,
        0x83,0x7E,0x28,0,0x74,0x0B,0xFF,0x75,0x10,0xFF,0x75,0x0C,0xE8,0x63,0x0C,0,0,
        0x83,0x7E,0x24,0x01,0x75,0x13,0x83,0x7E,0x28,0,0x74,0x0D,0xFF,0x75,0x10,
        0x8B,0xCE,0xFF,0x75,0x0C,0xE8,0x9A,0xF9,0xFF,0xFF,0x5E,0x5D,0xC2,0x10,0});
    put(0x6EB980, {0x8A,0x01,0xC3}); // bool getter leaves the upper EAX bits unspecified.
    const auto counter = [&](uint32_t rva, uint32_t address, bool input) {
        t7r::Bytes stub{0x81,0xF9}; word(stub, HELPER);
        stub.insert(stub.end(), {0x74,0x06,0xFF,0x05}); word(stub, BAD_CONTEXT);
        stub.insert(stub.end(), {0xFF,0x05}); word(stub, address);
        stub.insert(stub.end(), {0x33,0xC0,0x33,0xC9,0x33,0xD2});
        if (input) stub.insert(stub.end(), {0xC2,0x08,0}); else stub.push_back(0xC3);
        put(rva, stub);
    };
    counter(0x481120, REGISTER_CALLS, false); counter(0x4811B0, UNREGISTER_CALLS, false);
    counter(0x480690, DEFENSE_CALLS, true); counter(0x47F3E0, ATTACK_CALLS, true);
    t7r::Bytes record;
    unsigned char field = 0;
    for (const auto address : {HELPER + 0x20, HELPER + 0x54, HELPER + 0x24,
                               REGISTER_CALLS, UNREGISTER_CALLS, DEFENSE_CALLS, ATTACK_CALLS}) {
        record.push_back(0xA1); word(record, address);
        record.insert(record.end(), {0x89,0x42,field}); field += 4;
    }
    record.insert(record.end(), {0x89,0x5A,28,0x89,0x72,32,0x89,0x7A,36,0x89,0x6A,40,
                                 0x8D,0x44,0x24,4,0x89,0x42,44,0xC3}); put(0x7000, record);
    t7r::Bytes code;
    const auto set = [&](uint32_t address, uint32_t value) { code.insert(code.end(), {0xC7,0x05}); word(code, address); word(code, value); };
    const auto reg = [&](unsigned char opcode, uint32_t value) { code.push_back(opcode); word(code, value); };
    const auto call = [&](uint32_t rva) { code.push_back(0xE8); word(code, rva - (0x1000 + static_cast<uint32_t>(code.size()) + 4)); };
    code.insert(code.end(), {0x89,0x25}); word(code, STATE);
    set(0x3235AC8, SETTING); set(SERVICE + 0x2A4, HELPER); set(SERVICE + 0x2A8, 30);
    uint32_t observations = 0;
    const auto dispatchAndRecord = [&] {
        for (int argument = 0; argument < 4; ++argument) code.insert(code.end(), {0x6A,0});
        reg(0xB9, HELPER); call(0x47FA10);
        reg(0xBA, RESULT + observations++ * RESULT_SIZE); call(0x7000);
    };
    for (const auto& scenario : scenarios) {
        for (const auto address : {REGISTER_CALLS, UNREGISTER_CALLS, DEFENSE_CALLS, ATTACK_CALLS, HELPER + 0x20}) set(address, 0);
        set(HELPER + 0x54, 0xA5A5A500); set(HELPER + 0x24, scenario.attackMode);
        set(HELPER + 0x28, scenario.fightable ? 1u : 0u);
        set(SETTING, scenario.enabled); set(SETTING + 4, scenario.profession);
        reg(0xBB, scenario.level); reg(0xBE, SERVICE); reg(0xBF, 0); reg(0xB8, SETTING + 4); call(LEVEL_RVA);
        reg(0xBB, 0x11223344); reg(0xBE, 0x22334455); reg(0xBF, 0x33445566); reg(0xBD, 0x44556677);
        dispatchAndRecord();
        for (const bool enabled : scenario.updates) {
            set(SETTING, enabled); reg(0xB8, 0xFFFFFFFF); reg(0xB9, HELPER); call(SETTINGS_RVA);
            dispatchAndRecord();
        }
    }
    const auto complete = BASE + 0x1000 + static_cast<uint32_t>(code.size());
    code.insert(code.end(), {0xCC,0xEB,0xFE});
    require(code.size() < 0x6000, "defense fixture entry overlaps recorder"); put(0x1000, code);
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(image.data()), static_cast<std::streamsize>(image.size()));
    require(output.good(), "defense fixture write");
    return complete;
}
void execute(const t7r::fs::path& path, const std::vector<t7r::MemoryPatch>& patches,
             const std::vector<Scenario>& scenarios, uint32_t complete, bool adapted) {
    std::atomic<bool> finished{false}; t7r::DebugClient client;
    client.start(path, t7r::fileHash(path), {}, [&](HANDLE process, HANDLE) {
        if (adapted) t7r::applyRemotePatches(process, BASE, patches);
    }, {}, {}, {}, [&](DWORD, uintptr_t address) {
        if (address != complete) return false;
        finished.store(true); return true;
    });
    const auto deadline = GetTickCount64() + 10000;
    while (!finished.load() && GetTickCount64() < deadline) { client.check(); Sleep(2); }
    require(finished.load(), "defense fixture did not complete");
    const auto read = [&](uint32_t address) {
        const auto bytes = t7r::readClientMemory(client.process(), address, 4);
        uint32_t value = 0; std::memcpy(&value, bytes.data(), 4); return value;
    };
    uint32_t observations = 0;
    for (const auto& scenario : scenarios) {
        uint32_t mode = scenario.profession && scenario.enabled && (adapted || scenario.level <= 30) ? 2u : 0u;
        bool enabled = mode != 0 && scenario.attackMode == 0;
        uint32_t registered = enabled ? 1u : 0u, unregistered = mode == 0 && scenario.attackMode == 0 ? 1u : 0u;
        uint32_t defense = 0, attack = 0;
        const auto check = [&] {
            if (mode == 2 && scenario.fightable) ++defense;
            if (scenario.attackMode == 1 && scenario.fightable) ++attack;
            const auto result = RESULT + observations++ * RESULT_SIZE;
            require(read(result) == mode && read(result + 4) == (0xA5A5A500u | static_cast<uint32_t>(enabled)),
                    "defense mode/flag or adjacent bytes mismatch");
            require(read(result + 8) == scenario.attackMode, "defense setting changed attack mode");
            require(read(result + 12) == registered && read(result + 16) == unregistered,
                    "defense settings did not refresh native listeners exactly once");
            require(read(result + 20) == defense && read(result + 24) == attack,
                    "native input dispatch did not follow defense mode/local actor");
            require(read(result + 28) == 0x11223344 && read(result + 32) == 0x22334455
                && read(result + 36) == 0x33445566 && read(result + 40) == 0x44556677
                && read(result + 44) == read(STATE), "defense callback changed x86 nonvolatile registers/stack");
        };
        check();
        for (const bool update : scenario.updates) {
            if (adapted) mode = update ? 2u : 0u;
            enabled = update; ++unregistered; if (update) ++registered;
            check();
        }
    }
    require(read(BAD_CONTEXT) == 0, "defense engine call received the wrong this pointer");
    client.stop(); client.check();
}
}
bool verifyDefenseAssist() {
    const auto root = t7r::fs::temp_directory_path() / (L"t7r-defense-" + std::to_wstring(GetCurrentProcessId())
                                                      + L"-" + std::to_wstring(GetTickCount64()));
    const auto cleanup = [&] {
        require(t7r::fs::canonical(root).parent_path() == t7r::fs::canonical(t7r::fs::temp_directory_path()),
                "defense fixture cleanup boundary");
        t7r::fs::remove_all(root);
    };
    try {
        const auto patches = defensePatches();
        require(t7r::fs::create_directory(root), "fresh defense fixture directory");
        std::vector<Scenario> scenarios;
        for (const auto level : {29u,30u,31u,100u}) for (const bool profession : {false,true})
            for (const bool enabled : {false,true}) scenarios.push_back({level,profession,enabled,true,0,{}});
        for (const bool fightable : {false,true}) for (const auto attackMode : {0u,1u})
            scenarios.push_back({100,true,false,fightable,attackMode,{false,true,true,false,true}});
        scenarios.push_back({100,true,true,true,0,{false,true,false}});
        const auto path = root / "defense.exe";
        const auto complete = createFixture(path, scenarios);
        const auto diskHash = t7r::fileHash(path);
        execute(path, patches, scenarios, complete, false);
        execute(path, patches, scenarios, complete, true);
        require(t7r::fileHash(path) == diskHash, "defense adaptation changed the disk fixture");
        cleanup();
        std::cout << "Native defense assist level, profession, live settings, dispatch and x86 calling convention cases passed\n";
        return true;
    } catch (const std::exception& error) {
        std::cerr << "Defense assist test failure: " << error.what() << '\n';
        try { if (t7r::fs::exists(root)) cleanup(); }
        catch (const std::exception& cleanupError) { std::cerr << "Defense fixture cleanup: " << cleanupError.what() << '\n'; }
        return false;
    }
}
