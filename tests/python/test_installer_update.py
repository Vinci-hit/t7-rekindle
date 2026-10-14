"""Exercise update installation and launcher restart using disposable fixtures."""
from __future__ import annotations

import os
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid

import pytest

ROOT = Path(__file__).resolve().parents[2]
LEGACY_LIBRARIES = ("T7.Core.dll", "T7.NativeBridge.dll")
CURRENT_LIBRARIES = ("T7R.Core.dll", "T7R.NativeBridge.dll")
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows installer integration")


@pytest.fixture(scope="module")
def update_installer(tmp_path_factory):
    return _compile_installer(tmp_path_factory.mktemp("update-installer"))


def _compile_installer(work, settings_directory=None, startup_key=None, fail_reset=False, legacy=False):
    candidates = [
        shutil.which("ISCC.exe"),
        ROOT / ".local/toolchains/InnoSetup-7.1.0/ISCC.exe",
        Path(os.environ.get("RUNNER_TEMP", ".")) / "InnoSetup/ISCC.exe",
    ]
    compiler = next((Path(path) for path in candidates if path and Path(path).is_file()), None)
    if compiler is None:
        pytest.skip("Inno Setup compiler is not installed")
    csharp_compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
    if not csharp_compiler.is_file():
        pytest.skip(".NET Framework C# compiler is not installed")
    payload = work / "payload"
    payload.mkdir()
    (payload / "payload.txt").write_text("old release" if legacy else "new release", encoding="utf-8")
    (payload / "added.txt").write_text("new file", encoding="utf-8")
    libraries = LEGACY_LIBRARIES if legacy else CURRENT_LIBRARIES
    for name in libraries:
        (payload / name).write_bytes(b"old library" if legacy else b"current library")
    library_names = ", ".join(json.dumps(name) for name in libraries)
    launcher_source = work / "Launcher.cs"
    launcher_source.write_text(
        'using System;\nusing System.IO;\n'
        'class Launcher { static void Main() {\n'
        '    string directory = AppDomain.CurrentDomain.BaseDirectory;\n'
        f'    foreach (var name in new[] {{ {library_names} }})\n'
        '        if (!File.Exists(Path.Combine(directory, name))) throw new FileNotFoundException(name);\n'
        '    File.AppendAllText(Path.Combine(directory, "launches.txt"),\n'
        '        File.ReadAllText(Path.Combine(directory, "payload.txt")) + Environment.NewLine);\n'
        '} }\n', encoding="utf-8",
    )
    compiled_launcher = subprocess.run(
        [str(csharp_compiler), "/nologo", "/warnaserror+", "/target:winexe",
         f'/out:{payload / "T7-Rekindle.exe"}', str(launcher_source)],
        capture_output=True, timeout=30,
    )
    assert compiled_launcher.returncode == 0, compiled_launcher.stdout + compiled_launcher.stderr
    source = (ROOT / "installer/T7-Rekindle.iss").read_text(encoding="utf-8")
    run_entries = source.split("[Run]", 1)[1].split("[Code]", 1)[0]
    code = source.split("[Code]", 1)[1]
    if legacy:
        code = ('function AllowLauncherRun(): Boolean; begin Result := True; end;\n'
                'function WebView2Missing(): Boolean; begin Result := False; end;\n')
    if fail_reset:
        code = code.replace("RollbackReady := True;", "RaiseException('reset failure fixture');\n    RollbackReady := True;")
    definitions = ""
    if settings_directory:
        definitions = (f'#define RollbackDataDirectory "{settings_directory}"\n'
                       f'#define RollbackRunKey "{startup_key}"\n')
    script = work / "fixture.iss"
    script.write_text(
        definitions + '#define MyAppExeName "T7-Rekindle.exe"\n'
        '[Setup]\nAppId=T7R.Update.Installation.Test\nAppName=T7R update test\nAppVersion=1.0\n'
        'DefaultDirName={tmp}\\T7R-update-test\nPrivilegesRequired=lowest\nUninstallable=no\n'
        'DisableProgramGroupPage=yes\nOutputBaseFilename=Setup\n'
        f'OutputDir={work}\n[Files]\nSource: "{payload}\\*"; DestDir: "{{app}}"; Flags: ignoreversion\n'
        f'[Run]\n{run_entries}\n[Code]\n{code}', encoding="utf-8-sig",
    )
    compiled = subprocess.run([str(compiler), "--messages-jsonl", str(script)], capture_output=True, timeout=60)
    assert compiled.returncode == 0, compiled.stdout + compiled.stderr
    assert b"warning" not in (compiled.stdout + compiled.stderr).lower()
    return work / "Setup.exe"


def _target(work):
    target = work / "current launcher 重燃"
    target.mkdir()
    (target / "payload.txt").write_text("old release", encoding="utf-8")
    (target / "legacy.txt").write_text("keep", encoding="utf-8")
    for name in LEGACY_LIBRARIES:
        (target / name).write_bytes(b"old library")
    (target / "nested").mkdir()
    (target / "nested/T7.Core.dll").write_bytes(b"unrelated library")
    return target


def _start_installer(installer, target, launcher_pid, extra=""):
    log = target.parent / "install.log"
    # Match the launcher's quoted directory, including its trailing separator.
    command = (f'"{installer}" /VERYSILENT /SP- /SUPPRESSMSGBOXES /NORESTART /NORESTARTAPPLICATIONS '
               f'/DIR="{target}{os.sep}" /LAUNCHERPID={launcher_pid} /LOG="{log}" {extra}')
    return subprocess.Popen(command, creationflags=subprocess.CREATE_NO_WINDOW), log


def _start_launcher():
    return subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],
                            stdin=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)


def _assert_unchanged(target):
    assert (target / "payload.txt").read_text(encoding="utf-8") == "old release"
    assert not (target / "added.txt").exists()
    assert not (target / "launches.txt").exists()
    for name in LEGACY_LIBRARIES:
        assert (target / name).read_bytes() == b"old library"
    assert all(not (target / name).exists() for name in CURRENT_LIBRARIES)


def _assert_installed(target):
    assert (target / "payload.txt").read_text(encoding="utf-8") == "new release"
    assert (target / "added.txt").read_text(encoding="utf-8") == "new file"
    assert (target / "legacy.txt").read_text(encoding="utf-8") == "keep"
    assert all(not (target / name).exists() for name in LEGACY_LIBRARIES)
    for name in CURRENT_LIBRARIES:
        assert (target / name).read_bytes() == b"current library"
    assert (target / "nested/T7.Core.dll").read_bytes() == b"unrelated library"


def _assert_relaunched(target):
    launches = target / "launches.txt"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if launches.exists() and launches.read_text(encoding="utf-8-sig").splitlines():
            assert launches.read_text(encoding="utf-8-sig").splitlines() == ["new release"]
            return
        time.sleep(0.05)
    pytest.fail("successful update did not launch the newly installed launcher")


def test_installer_waits_for_launcher_before_writing(update_installer, tmp_path):
    target = _target(tmp_path)
    launcher = _start_launcher()
    installer = None
    try:
        installer, log = _start_installer(update_installer, target, launcher.pid)
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if log.exists() and "Waiting for launcher process to exit." in log.read_text(encoding="utf-8-sig"):
                break
            assert installer.poll() is None, "installer exited before waiting for the launcher"
            time.sleep(0.05)
        else:
            pytest.fail("installer did not reach the launcher-exit gate")
        _assert_unchanged(target)
        assert launcher.poll() is None
        launcher.communicate(timeout=5)
        assert installer.wait(timeout=30) == 0
        _assert_installed(target)
        _assert_relaunched(target)
    finally:
        if launcher.poll() is None:
            launcher.communicate(timeout=5)
        if installer is not None and installer.poll() is None:
            installer.wait(timeout=45)


def test_installer_handles_an_already_exited_launcher(update_installer, tmp_path):
    target = _target(tmp_path)
    with _start_launcher() as launcher:
        launcher.communicate(timeout=5)
        installer, _ = _start_installer(update_installer, target, launcher.pid)
        with installer:
            assert installer.wait(timeout=30) == 0
    _assert_installed(target)
    _assert_relaunched(target)


def test_installer_handles_missing_old_libraries_and_repeat_install(update_installer, tmp_path):
    target = _target(tmp_path)
    for name in LEGACY_LIBRARIES:
        (target / name).unlink()
    for _ in range(2):
        with _start_launcher() as launcher:
            launcher.communicate(timeout=5)
            installer, _ = _start_installer(update_installer, target, launcher.pid)
            with installer:
                assert installer.wait(timeout=30) == 0
        _assert_installed(target)


def test_old_package_can_restore_old_libraries_after_t7r_upgrade(update_installer, tmp_path):
    target = _target(tmp_path)
    work = tmp_path / "old-installer"
    work.mkdir()
    old_installer = _compile_installer(work, legacy=True)
    with _start_launcher() as launcher:
        launcher.communicate(timeout=5)
        process, _ = _start_installer(update_installer, target, launcher.pid)
        with process:
            assert process.wait(timeout=30) == 0
        _assert_installed(target)
        _assert_relaunched(target)
        process, _ = _start_installer(old_installer, target, launcher.pid)
        with process:
            assert process.wait(timeout=30) == 0
    assert (target / "payload.txt").read_text(encoding="utf-8") == "old release"
    assert all((target / name).read_bytes() == b"old library" for name in LEGACY_LIBRARIES)
    assert all((target / name).read_bytes() == b"current library" for name in CURRENT_LIBRARIES)
    assert (target / "legacy.txt").read_text(encoding="utf-8") == "keep"
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if (target / "launches.txt").read_text(encoding="utf-8-sig").splitlines() == ["new release", "old release"]:
            return
        time.sleep(0.05)
    pytest.fail("restored launcher did not find its old libraries")


@pytest.mark.parametrize("failure", ["directory", "locked"])
def test_legacy_library_cleanup_failure_stops_restart_and_can_retry(update_installer, tmp_path, failure):
    import ctypes
    from ctypes import wintypes
    target = _target(tmp_path)
    blocked = target / LEGACY_LIBRARIES[0]
    handle = None
    if failure == "directory":
        blocked.unlink()
        blocked.mkdir()
        (blocked / "keep.txt").write_text("keep", encoding="utf-8")
    else:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                      ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(blocked), 0x80000000, 1, None, 3, 0, None)
        assert handle != ctypes.c_void_p(-1).value, ctypes.get_last_error()
    try:
        with _start_launcher() as launcher:
            launcher.communicate(timeout=5)
            installer, log = _start_installer(update_installer, target, launcher.pid)
            with installer:
                assert installer.wait(timeout=30) == 20
        assert not (target / "launches.txt").exists()
        assert blocked.exists()
        assert (target / "legacy.txt").read_text(encoding="utf-8") == "keep"
        assert all((target / name).read_bytes() == b"current library" for name in CURRENT_LIBRARIES)
        assert LEGACY_LIBRARIES[0] in log.read_text(encoding="utf-8-sig")
    finally:
        if handle is not None:
            assert kernel.CloseHandle(handle)
    if failure == "directory":
        assert (blocked / "keep.txt").read_text(encoding="utf-8") == "keep"
        (blocked / "keep.txt").unlink()
        blocked.rmdir()
    with _start_launcher() as launcher:
        launcher.communicate(timeout=5)
        installer, _ = _start_installer(update_installer, target, launcher.pid)
        with installer:
            assert installer.wait(timeout=30) == 0
    _assert_installed(target)
    _assert_relaunched(target)


def test_installer_leaves_files_unchanged_on_exit_timeout(update_installer, tmp_path):
    target = _target(tmp_path)
    with _start_launcher() as launcher:
        try:
            installer, _ = _start_installer(update_installer, target, launcher.pid)
            with installer:
                assert installer.wait(timeout=45) != 0
            assert launcher.poll() is None, "installer killed a process instead of waiting for cleanup"
            _assert_unchanged(target)
        finally:
            launcher.communicate(timeout=5)


def test_installer_rejects_an_invalid_launcher_pid(update_installer, tmp_path):
    target = _target(tmp_path)
    installer, _ = _start_installer(update_installer, target, "invalid")
    with installer:
        assert installer.wait(timeout=30) != 0
    _assert_unchanged(target)


@pytest.fixture
def rollback_installation(tmp_path, request):
    import winreg
    data = tmp_path / "settings"
    data.mkdir()
    key = "Software\\T7-Rekindle-Tests\\rollback-" + uuid.uuid4().hex
    originals = {"settings.json": b'{"schemaVersion":2,"playerName":"fixture"}',
                 "settings.json.bak": b'{"schemaVersion":2}',
                 "update-settings.json": b'{"schemaVersion":1,"channel":"stable"}',
                 "update-settings.json.bak": b'{"schemaVersion":1,"channel":"preview"}'}
    for name, content in originals.items():
        (data / name).write_bytes(content)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as handle:
        winreg.SetValueEx(handle, "T7-Rekindle", 0, winreg.REG_SZ, "fixture-launcher.exe")
        winreg.SetValueEx(handle, "Unrelated", 0, winreg.REG_SZ, "keep")
    work = tmp_path / "compiler"
    work.mkdir()
    try:
        installer = _compile_installer(work, data, key, getattr(request, "param", "") == "fail-reset")
        yield installer, data, key, originals
    finally:
        assert key.startswith("Software\\T7-Rekindle-Tests\\rollback-")
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)


def _rollback(installer, tmp_path, reset=True, target=None):
    target = _target(tmp_path) if target is None else target
    with _start_launcher() as launcher:
        launcher.communicate(timeout=5)
        process, _ = _start_installer(installer, target, launcher.pid,
            f"/ROLLBACK=1 /RESETSETTINGS={int(reset)} /UPDATECHANNEL=preview")
        with process:
            code = process.wait(timeout=30)
    return code, target


@pytest.mark.parametrize("reset", [False, True])
def test_rollback_backs_up_settings_and_resets_only_after_install(rollback_installation, tmp_path, reset):
    import winreg
    installer, data, key, originals = rollback_installation
    code, target = _rollback(installer, tmp_path, reset)
    assert code == 0
    _assert_installed(target)
    _assert_relaunched(target)
    backups = list((data / "settings-backups").glob("*.backup"))
    assert len(backups) == 1 and (backups[0] / "backup.ini").is_file()
    assert {name: (backups[0] / name).read_bytes() for name in originals} == originals
    if reset:
        assert json.loads((data / "settings.json").read_text()) == {"schemaVersion": 1, "updateChannel": "preview"}
        assert all(not (data / name).exists() for name in originals if name != "settings.json")
    else:
        assert {name: (data / name).read_bytes() for name in originals} == originals
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
        assert winreg.QueryValueEx(handle, "Unrelated")[0] == "keep"
        if reset:
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(handle, "T7-Rekindle")
        else:
            assert winreg.QueryValueEx(handle, "T7-Rekindle")[0] == "fixture-launcher.exe"


def test_rollback_backup_failure_does_not_install_or_reset(rollback_installation, tmp_path):
    installer, data, _, originals = rollback_installation
    (data / "settings-backups").write_text("fixture")
    code, target = _rollback(installer, tmp_path)
    assert code != 0
    _assert_unchanged(target)
    assert {name: (data / name).read_bytes() for name in originals} == originals


def test_cleanup_failure_preserves_completed_settings_reset(rollback_installation, tmp_path):
    installer, data, _, originals = rollback_installation
    target = _target(tmp_path)
    blocked = target / LEGACY_LIBRARIES[0]
    blocked.unlink()
    blocked.mkdir()
    code, _ = _rollback(installer, tmp_path, target=target)
    assert code == 20 and not (target / "launches.txt").exists()
    assert json.loads((data / "settings.json").read_text()) == {"schemaVersion": 1, "updateChannel": "preview"}
    backups = list((data / "settings-backups").glob("*.backup"))
    assert len(backups) == 1
    assert {name: (backups[0] / name).read_bytes() for name in originals} == originals
    assert blocked.is_dir()


@pytest.mark.parametrize("rollback_installation", ["fail-reset"], indirect=True)
def test_rollback_reset_failure_restores_settings_and_does_not_restart(rollback_installation, tmp_path):
    import winreg
    installer, data, key, originals = rollback_installation
    code, target = _rollback(installer, tmp_path)
    assert code == 20 and not (target / "launches.txt").exists()
    assert all((target / name).read_bytes() == b"old library" for name in LEGACY_LIBRARIES)
    assert {name: (data / name).read_bytes() for name in originals} == originals
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
        assert winreg.QueryValueEx(handle, "T7-Rekindle")[0] == "fixture-launcher.exe"


def test_rollback_exit_gate_precedes_backup(rollback_installation, tmp_path):
    installer, data, _, originals = rollback_installation
    target = _target(tmp_path)
    process, _ = _start_installer(installer, target, "invalid", "/ROLLBACK=1 /UPDATECHANNEL=preview /RESETSETTINGS=1")
    with process:
        assert process.wait(timeout=30) != 0
    assert not (data / "settings-backups").exists()
    assert {name: (data / name).read_bytes() for name in originals} == originals
    _assert_unchanged(target)


def test_rollback_preserves_missing_files_and_existing_backups(rollback_installation, tmp_path):
    installer, data, _, originals = rollback_installation
    for name in originals:
        if name != "settings.json":
            (data / name).unlink()
    for number in range(2):
        run = tmp_path / str(number)
        run.mkdir()
        assert _rollback(installer, run, reset=False)[0] == 0
    backups = list((data / "settings-backups").glob("*.backup"))
    assert len(backups) == 2
    for backup in backups:
        assert (backup / "settings.json").read_bytes() == originals["settings.json"]
        assert all(not (backup / name).exists() for name in originals if name != "settings.json")


@pytest.mark.parametrize("extra", ["/RESETSETTINGS=1", "/ROLLBACK=2", "/ROLLBACK=1 /RESETSETTINGS=2 /UPDATECHANNEL=stable",
                                  "/ROLLBACK=1 /UPDATECHANNEL=unknown"])
def test_invalid_rollback_options_do_not_install(update_installer, tmp_path, extra):
    target = _target(tmp_path)
    with _start_launcher() as launcher:
        launcher.communicate(timeout=5)
        process, _ = _start_installer(update_installer, target, launcher.pid, extra)
        with process:
            assert process.wait(timeout=30) != 0
    _assert_unchanged(target)
