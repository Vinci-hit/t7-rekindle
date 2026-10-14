#pragma once
#include "../../src/Runtime/launcher/AudioPlaybackTrace.h"
#include "../../src/Runtime/launcher/ClientImports.h"
#include "../../src/Runtime/launcher/RemoteImage.h"
#include <cstring>
#include <algorithm>
#include <fstream>

namespace audioFixture {
constexpr uint32_t BASE = 0x400000, ENTRY = BASE + 0x1220739, DATA = BASE + 0x4000;
constexpr uint32_t COMMAND = DATA, DONE = DATA + 4, BANK_ID = DATA + 8, CALLS = DATA + 12;
constexpr uint32_t STATS = DATA + 0x20, ARGUMENTS = DATA + 0x80, WORKERS = DATA + 0xB0;
constexpr uint32_t NAME = DATA + 0x100, BANK_NAME = DATA + 0x200, RTPC_NAME = DATA + 0x300;
inline void require(bool value, const char* message) { if (!value) throw std::runtime_error(message); }
inline void word(t7r::Bytes& bytes, uint32_t value) {
    const auto at = bytes.size(); bytes.resize(at + 4); std::memcpy(bytes.data() + at, &value, 4);
}
inline uint32_t read(HANDLE process, uint32_t at) {
    const auto bytes = t7r::readClientMemory(process, at, 4); uint32_t result; std::memcpy(&result, bytes.data(), 4); return result;
}
inline void write(HANDLE process, uint32_t at, uint32_t value) { t7r::Bytes bytes; word(bytes, value); t7r::writeClientMemory(process, at, bytes); }
struct Code {
    t7r::Bytes bytes;
    uint32_t base;
    explicit Code(uint32_t address) : base(address) {}
    void emit(std::initializer_list<unsigned char> values) { bytes.insert(bytes.end(), values); }
    void imm(unsigned char opcode, uint32_t value) { emit({opcode}); word(bytes, value); }
    void store(uint32_t address, uint32_t value) { emit({0xC7,0x05}); word(bytes, address); word(bytes, value); }
    void indirect(uint32_t address) { emit({0xFF,0x15}); word(bytes, address); }
    size_t branch(unsigned char condition = 0) {
        if (condition) emit({0x0F,condition}); else emit({0xE9});
        const auto at = bytes.size(); word(bytes, 0); return at;
    }
    void bind(size_t at, size_t target) { const auto delta = static_cast<uint32_t>(target - at - 4); std::memcpy(bytes.data() + at, &delta, 4); }
    void call(uint32_t address, std::initializer_list<uint32_t> args) {
        for (auto it = args.end(); it != args.begin();) imm(0x68, *--it);
        indirect(address);
        emit({0x83,0xC4,static_cast<unsigned char>(args.size() * 4)});
    }
};
inline const t7r::ClientImportModule& module(const char* name) {
    for (const auto& value : t7r::clientImportModules()) if (value.dll == name) return value;
    throw std::runtime_error("fixture import module missing");
}
inline uint32_t iat(const t7r::ClientImportModule& imports, const char* prefix) {
    for (size_t i = 0; i < imports.symbols.size(); ++i)
        if (imports.symbols[i].rfind(prefix, 0) == 0) return BASE + imports.iatRva + static_cast<uint32_t>(i * 4);
    throw std::runtime_error("fixture import symbol missing");
}
inline void put(t7r::Bytes& image, uint32_t rva, const t7r::Bytes& data) {
    require(rva + data.size() <= image.size(), "fixture image bounds");
    std::memcpy(image.data() + rva, data.data(), data.size());
}
inline void pe(const t7r::fs::path& path, const t7r::Bytes& image, uint32_t base, uint32_t entry,
               IMAGE_DATA_DIRECTORY imports, IMAGE_DATA_DIRECTORY exports, bool dll) {
    t7r::Bytes file(0x200, 0);
    IMAGE_DOS_HEADER dos{}; dos.e_magic = IMAGE_DOS_SIGNATURE; dos.e_lfanew = 0x80;
    std::memcpy(file.data(), &dos, sizeof(dos));
    IMAGE_NT_HEADERS32 nt{}; nt.Signature = IMAGE_NT_SIGNATURE;
    nt.FileHeader.Machine = IMAGE_FILE_MACHINE_I386; nt.FileHeader.NumberOfSections = 1;
    nt.FileHeader.SizeOfOptionalHeader = sizeof(nt.OptionalHeader);
    nt.FileHeader.Characteristics = IMAGE_FILE_EXECUTABLE_IMAGE | IMAGE_FILE_32BIT_MACHINE | IMAGE_FILE_RELOCS_STRIPPED
        | (dll ? IMAGE_FILE_DLL : 0);
    nt.OptionalHeader.Magic = IMAGE_NT_OPTIONAL_HDR32_MAGIC; nt.OptionalHeader.ImageBase = base;
    nt.OptionalHeader.AddressOfEntryPoint = entry; nt.OptionalHeader.BaseOfCode = 0x1000;
    nt.OptionalHeader.SectionAlignment = 0x1000; nt.OptionalHeader.FileAlignment = 0x200;
    nt.OptionalHeader.MajorOperatingSystemVersion = 6; nt.OptionalHeader.MajorSubsystemVersion = 6;
    nt.OptionalHeader.SizeOfImage = static_cast<DWORD>(image.size()); nt.OptionalHeader.SizeOfHeaders = 0x200;
    nt.OptionalHeader.Subsystem = IMAGE_SUBSYSTEM_WINDOWS_GUI;
    nt.OptionalHeader.SizeOfStackReserve = 0x100000; nt.OptionalHeader.SizeOfStackCommit = 0x1000;
    nt.OptionalHeader.SizeOfHeapReserve = 0x100000; nt.OptionalHeader.SizeOfHeapCommit = 0x1000;
    nt.OptionalHeader.NumberOfRvaAndSizes = IMAGE_NUMBEROF_DIRECTORY_ENTRIES;
    nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT] = imports;
    nt.OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_EXPORT] = exports;
    std::memcpy(file.data() + dos.e_lfanew, &nt, sizeof(nt));
    IMAGE_SECTION_HEADER section{}; std::memcpy(section.Name, ".fixture", 8);
    section.VirtualAddress = 0x1000; section.Misc.VirtualSize = nt.OptionalHeader.SizeOfImage - 0x1000;
    section.PointerToRawData = 0x200; section.SizeOfRawData = section.Misc.VirtualSize;
    section.Characteristics = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE | IMAGE_SCN_MEM_EXECUTE;
    std::memcpy(file.data() + dos.e_lfanew + sizeof(nt), &section, sizeof(section));
    std::ofstream output(path, std::ios::binary);
    output.write(reinterpret_cast<const char*>(file.data()), static_cast<std::streamsize>(file.size()));
    output.write(reinterpret_cast<const char*>(image.data() + 0x1000), static_cast<std::streamsize>(image.size() - 0x1000));
    require(output.good(), "audio fixture write");
}
inline void dll(const t7r::fs::path& root) {
    auto names = module("aksoundenginedll.dll").symbols;
    std::sort(names.begin(), names.end()); names.erase(std::unique(names.begin(), names.end()), names.end());
    t7r::Bytes image(0x10000, 0);
    IMAGE_EXPORT_DIRECTORY exports{}; exports.Base = 1;
    exports.NumberOfNames = exports.NumberOfFunctions = static_cast<DWORD>(names.size());
    exports.AddressOfFunctions = 0x5100; exports.AddressOfNames = 0x5300; exports.AddressOfNameOrdinals = 0x5500;
    uint32_t stringRva = 0x5600;
    for (size_t i = 0; i < names.size(); ++i) {
        const uint32_t function = 0x1000 + static_cast<uint32_t>(i * 0x100);
        const auto ordinal = static_cast<WORD>(i);
        std::memcpy(image.data() + exports.AddressOfFunctions + i * 4, &function, 4);
        std::memcpy(image.data() + exports.AddressOfNames + i * 4, &stringRva, 4);
        std::memcpy(image.data() + exports.AddressOfNameOrdinals + i * 2, &ordinal, 2);
        std::memcpy(image.data() + stringRva, names[i].c_str(), names[i].size() + 1);
        stringRva += static_cast<uint32_t>(names[i].size() + 1);
        Code code(0x10000000 + function);
        if (names[i].rfind("?LoadBank@", 0) == 0) {
            code.emit({0x83,0x7C,0x24,0x08,99}); const auto failed = code.branch(0x84);
            code.emit({0x8B,0x44,0x24,0x0C,0xC7,0}); word(code.bytes, 12345);
            code.emit({0x8B,0x44,0x24,0x04,0xC6,0,static_cast<unsigned char>('X')});
            code.imm(0xB8, 1); code.emit({0xC3}); code.bind(failed, code.bytes.size());
            code.imm(0xB8, 2); code.emit({0xC3});
        } else if (names[i].rfind("?PostEvent@", 0) == 0) {
            code.emit({0xF0,0xFF,0x05}); word(code.bytes, CALLS);
            for (unsigned arg = 0; arg < 8; ++arg) {
                code.emit({0x8B,0x44,0x24,static_cast<unsigned char>(4 + arg * 4)}); code.imm(0xA3, ARGUMENTS + arg * 4);
            }
            code.emit({0x64,0xA1}); word(code.bytes, 0x34); code.imm(0xA3, STATS + 32);
            code.imm(0xB8, 0x76543210); code.emit({0x64,0xA3}); word(code.bytes, 0x34);
            code.imm(0xB8, names[i].find("YAKPBD") != std::string::npos ? 456 : 0); code.emit({0xC3});
        } else { code.imm(0xB8, names[i].rfind("?GetIDFromString@", 0) == 0 ? 77 : 1); code.emit({0xC3}); }
        require(code.bytes.size() < 0x100, "fixture export code extent"); put(image, function, code.bytes);
    }
    require(stringRva < 0x9000, "fixture export name capacity");
    std::memcpy(image.data() + 0x5000, &exports, sizeof(exports));
    pe(root / "aksoundenginedll.dll", image, 0x10000000, 0, {}, {0x5000, 0x4000}, true);
}
inline t7r::fs::path create(const t7r::fs::path& root) {
    dll(root);
    const auto& audio = module("aksoundenginedll.dll"); auto kernel = module("KERNEL32.dll");
    kernel.symbols.push_back("ExitProcess");
    const auto post = iat(audio, "?PostEvent@SoundEngine@AK@@YAKPBD");
    const auto sleep = iat(kernel, "Sleep"), exit = iat(kernel, "ExitProcess");
    t7r::Bytes memory(0x1700000, 0);
    const auto imports = t7r::writeClientImports(memory, 0x6000, 0x10000, {audio, kernel});
    std::memcpy(memory.data() + NAME - BASE, "Play_voice_fixture", 19);
    std::memcpy(memory.data() + BANK_NAME - BASE, "Media_Vox_System.bnk", 21);
    std::memcpy(memory.data() + RTPC_NAME - BASE, "Voice_Volume", 13);
    Code worker(BASE + 0x2000);
    worker.emit({0x57}); worker.imm(0xBF, 1000); const auto repeat = worker.bytes.size();
    worker.call(post, {NAME, 42, 0, 0, 0, 0, 0, 0}); worker.emit({0x4F}); worker.bind(worker.branch(0x85), repeat);
    worker.emit({0xF0,0xFF,0x05}); word(worker.bytes, WORKERS); worker.emit({0x5F,0x33,0xC0,0xC2,0x04,0});
    put(memory, 0x2000, worker.bytes);
    Code main(BASE + 0x1000);
    const auto loop = main.bytes.size();
    main.emit({0x6A,0x01}); main.indirect(sleep);
    main.imm(0xA1, COMMAND); main.emit({0x85,0xC0}); main.bind(main.branch(0x84), loop);
    main.store(COMMAND, 0); main.emit({0x83,0xF8,0x01}); const auto stress = main.branch(0x85);
    main.emit({0x89,0x25}); word(main.bytes, STATS);
    main.imm(0xBB, 0x11223344); main.imm(0xBE, 0x22334455); main.imm(0xBF, 0x33445566); main.imm(0xBD, 0x44556677);
    main.imm(0xB8, 0x1234); main.emit({0x64,0xA3}); word(main.bytes, 0x34);
    main.call(post, {NAME, 42, 8, 0x123400, 0x234500, 2, 0x345600, 99}); main.imm(0xA3, STATS + 4);
    main.emit({0x89,0x25}); word(main.bytes, STATS + 8);
    for (const auto pair : {std::pair<uint32_t,uint32_t>{0x1D,12},{0x35,16},{0x3D,20},{0x2D,24}}) {
        main.emit({0x89,static_cast<unsigned char>(pair.first)}); word(main.bytes, STATS + pair.second);
    }
    main.emit({0x64,0xA1}); word(main.bytes, 0x34); main.imm(0xA3, STATS + 28);
    main.call(iat(audio, "?LoadBank@"), {BANK_NAME, 0, BANK_ID});
    main.call(iat(audio, "?LoadBank@"), {BANK_NAME, 99, 1});
    main.call(iat(audio, "?UnloadBank@"), {BANK_NAME, 0, 0});
    main.call(iat(audio, "?GetIDFromString@"), {NAME});
    main.call(iat(audio, "?SetRTPCValue@SoundEngine@AK@@YA?AW4AKRESULT@@PBD"), {RTPC_NAME, 0x3F000000, 42, 0, 0, 0});
    main.call(iat(audio, "?SetRTPCValue@SoundEngine@AK@@YA?AW4AKRESULT@@KM"), {81, 0x3F800000, 42, 0, 0, 0});
    main.call(iat(audio, "?Init@SOUNDENGINE_DLL@"), {1, 2, 3, 4, 5, 6});
    main.store(DONE, 1); main.bind(main.branch(), loop);
    main.bind(stress, main.bytes.size());
    main.emit({0x83,0xF8,0x02}); const auto rejected = main.branch(0x85);
    for (int i = 0; i < 2; ++i) {
        for (const auto arg : {0u,0u,0u,BASE + 0x2000,0u,0u}) main.imm(0x68, arg);
        main.indirect(iat(kernel, "CreateThread")); main.emit({0x50}); main.indirect(iat(kernel, "CloseHandle"));
    }
    const auto wait = main.bytes.size(); main.emit({0x6A,0x01}); main.indirect(sleep);
    main.emit({0x83,0x3D}); word(main.bytes, WORKERS); main.emit({2}); main.bind(main.branch(0x85), wait);
    main.store(DONE, 2); main.bind(main.branch(), loop);
    main.bind(rejected, main.bytes.size()); main.emit({0x83,0xF8,0x03}); const auto finish = main.branch(0x85);
    main.call(iat(audio, "?PostEvent@SoundEngine@AK@@YAKK"), {77, 42, 0, 0, 0, 0, 0, 0});
    main.store(DONE, 3); main.bind(main.branch(), loop);
    main.bind(finish, main.bytes.size()); main.emit({0x6A,0}); main.indirect(exit);
    require(main.bytes.size() < 0x1000, "fixture main code extent"); put(memory, 0x1000, main.bytes);
    Code entry(ENTRY); entry.emit({0xE9}); word(entry.bytes, BASE + 0x1000 - ENTRY - 5); put(memory, ENTRY - BASE, entry.bytes);
    const auto path = root / "AudioFixture.exe";
    pe(path, memory, BASE, ENTRY - BASE, {imports.rva, imports.size}, {}, false);
    return path;
}
}
