import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from test_build import load_build


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "src/Desktop/BusinessScripts.targets"


@pytest.fixture(scope="module")
def msbuild():
    if os.name != "nt":
        pytest.skip("Business output tests require Windows MSBuild")
    return load_build().locate_msbuild(require_native=False)


@pytest.fixture
def project(tmp_path):
    shutil.copytree(ROOT / "src/Business", tmp_path / "src/Business",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    root = ET.Element("Project")
    properties = ET.SubElement(root, "PropertyGroup")
    for name, value in {
        "RepositoryRoot": str(tmp_path) + os.sep,
        "Configuration": "Debug",
        "OutputPath": "out/$(Configuration)/",
        "OutDir": "$(OutputPath)",
        "IntermediateOutputPath": "obj/$(Configuration)/",
        "TargetFrameworkVersion": "v4.8",
        "CopyRetryCount": "0",
    }.items():
        ET.SubElement(properties, name).text = value
    ET.SubElement(root, "Import", Project=str(TARGETS))
    ET.SubElement(root, "Import", Project=r"$(MSBuildToolsPath)\Microsoft.CSharp.targets")
    path = tmp_path / "BusinessCopy.csproj"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def run_copy(msbuild, project, configuration="Debug", *, target=None, properties=None):
    arguments = [str(msbuild), str(project), "/nologo", "/nr:false", "/v:minimal",
                 f"/p:Configuration={configuration}",
                 "/t:" + (target or "AssignTargetPaths;_CopySourceItemsToOutputDirectory")]
    arguments += [f"/p:{key}={value}" for key, value in (properties or {}).items()]
    return subprocess.run(arguments, cwd=project.parent, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def assert_success(result):
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning " not in result.stdout.lower(), result.stdout


def script_bytes(directory):
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*.py") if "__pycache__" not in path.parts}


def test_desktop_imports_business_copy_rules():
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj").getroot()
    assert len([item for item in project.findall("Import")
                if item.get("Project") == "BusinessScripts.targets"]) == 1


@pytest.mark.parametrize("configuration", ["Debug", "Release"])
def test_copy_restores_newer_output_and_tracks_source_changes(msbuild, project, configuration):
    source = project.parent / "src/Business"
    output = project.parent / "out" / configuration / "Business"
    assert_success(run_copy(msbuild, project, configuration))
    assert script_bytes(output) == script_bytes(source)

    generated = output / "scripts/contracts.py"
    generated.write_text("stale = True\n", encoding="utf-8")
    future = (source / "scripts/contracts.py").stat().st_mtime + 3600
    os.utime(generated, (future, future))
    (source / "scripts/new_module.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert_success(run_copy(msbuild, project, configuration))
    assert script_bytes(output) == script_bytes(source)

    (source / "scripts/new_module.py").rename(source / "scripts/renamed_module.py")
    (output / "scripts/untracked_old.py").write_text("old = True\n", encoding="utf-8")
    cache = output / "scripts/__pycache__/old.pyc"
    cache.parent.mkdir()
    cache.write_bytes(b"old bytecode")
    sentinel = output / "notes.txt"
    sentinel.write_text("keep", encoding="utf-8")
    assert_success(run_copy(msbuild, project, configuration))
    assert script_bytes(output) == script_bytes(source)
    assert not cache.exists()
    assert sentinel.read_text(encoding="utf-8") == "keep"

    (source / "scripts/renamed_module.py").unlink()
    assert_success(run_copy(msbuild, project, configuration))
    assert script_bytes(output) == script_bytes(source)
    before = {name: (output / name).stat().st_mtime_ns for name in script_bytes(output)}
    assert_success(run_copy(msbuild, project, configuration))
    assert before == {name: (output / name).stat().st_mtime_ns for name in script_bytes(output)}


def test_design_time_does_not_modify_output(msbuild, project):
    output = project.parent / "out/Debug/Business/scripts"
    output.mkdir(parents=True)
    stale = output / "old.py"
    stale.write_text("keep", encoding="utf-8")
    assert_success(run_copy(msbuild, project, target="PrepareBusinessScripts",
                            properties={"DesignTimeBuild": "true"}))
    assert stale.read_text(encoding="utf-8") == "keep"


def test_visual_studio_tracks_deleted_script_sources(msbuild, project):
    output = project.parent / "out/Debug/Business/scripts"
    output.mkdir(parents=True)
    stale = output / "removed.py"
    stale.write_text("keep", encoding="utf-8")
    result = subprocess.run(
        [str(msbuild), str(project), "/nologo", "/nr:false", "/p:DesignTimeBuild=true",
         "/t:CollectBusinessCopyInputs", "-getItem:UpToDateCheckBuilt",
         "-getProperty:AccelerateBuildsInVisualStudio"],
        cwd=project.parent, capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert_success(result)
    data = json.loads(result.stdout)
    assert data["Properties"]["AccelerateBuildsInVisualStudio"] == "false"
    item = next(item for item in data["Items"]["UpToDateCheckBuilt"]
                if Path(item["FullPath"]) == stale)
    assert Path(item["Original"]) == project.parent / "src/Business/scripts/removed.py"
    assert stale.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("relative", ["src", "src/Business/nested"])
def test_overlapping_source_and_output_are_rejected(msbuild, project, relative):
    before = script_bytes(project.parent / "src/Business")
    result = run_copy(msbuild, project, properties={"OutDir": str(project.parent / relative) + os.sep})
    assert result.returncode != 0
    assert "overlap" in result.stdout.lower()
    assert script_bytes(project.parent / "src/Business") == before


def test_missing_source_entry_stops_before_cleanup(msbuild, project):
    (project.parent / "src/Business/scripts/__init__.py").unlink()
    output = project.parent / "out/Debug/Business"
    output.mkdir(parents=True)
    stale = output / "old.py"
    stale.write_text("keep", encoding="utf-8")
    result = run_copy(msbuild, project)
    assert result.returncode != 0
    assert "entry" in result.stdout.lower()
    assert stale.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("relative", ["out", "out/Debug/Business", "src/Business/scripts/linked"])
def test_junctions_are_rejected_before_file_changes(msbuild, project, relative):
    outside = project.parent / "outside"
    outside.mkdir()
    sentinel = outside / "untouched.py"
    sentinel.write_text("keep", encoding="utf-8")
    link = project.parent / relative
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(outside)],
                            capture_output=True)
    assert result.returncode == 0, result.stdout + result.stderr
    try:
        result = run_copy(msbuild, project)
        assert result.returncode != 0
        assert "link" in result.stdout.lower()
        assert sentinel.read_text(encoding="utf-8") == "keep"
    finally:
        link.rmdir()


@pytest.mark.parametrize("filename", ["scripts/contracts.py", "obsolete.py"])
def test_copy_or_cleanup_error_fails_build(msbuild, project, filename):
    assert_success(run_copy(msbuild, project))
    path = project.parent / "out/Debug/Business" / filename
    path.write_text("old", encoding="utf-8")
    path.chmod(stat.S_IREAD)
    try:
        result = run_copy(msbuild, project)
        assert result.returncode != 0
        assert path.read_text(encoding="utf-8") == "old"
    finally:
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def test_output_runtime_loads_updated_packets_without_source_imports(msbuild, project):
    assert_success(run_copy(msbuild, project))
    output = project.parent / "out/Debug/Business"
    script = r'''
from pathlib import Path
import json
import struct
import sys

business, cache = map(Path, sys.argv[1:])
sys.path.insert(0, str(business / 'runtime'))
import host_runtime
assert Path(host_runtime.__file__).resolve() == (business / 'runtime/host_runtime.py').resolve()
runtime = host_runtime.Runtime(business / 'scripts', cache)
try:
    wire = sys.modules[runtime.active.name + '.contracts']
    app = sys.modules[runtime.active.name + '.app']
    scene = sys.modules[runtime.active.name + '.scene']
    protocol = sys.modules[runtime.active.name + '.codec.protocol']
    context = {'nowMs': 0}
    result = app.handleEvent({'type': 'connected', 'connection': 1, 'role': 'logic'},
                             app.createState(context), context)
    result = app.handleEvent({'type': 'authenticated', 'connection': 1}, result['state'], context)
    timer = result['timers'][0]
    result = app.handleEvent(timer['event'], result['state'], {'nowMs': timer['delayMs']})
    assert protocol.decode_minimal_login_success(result['send'][0]['body']).level == 100
    assert (wire.PREPARE_MS, wire.START_MS, wire.GAME_MS) == (30000, 5999, 1200000)
    state = app.createState({})
    state['sessions']['1'] = {'role': 'instance'}
    flow = app.Flow({'connection': 1}, state, {'nowMs': 1000})
    scene.timer(flow, 'instance-round-state')
    assert struct.unpack('>HQQiQQi', flow.result['send'][0]['body'])[5] == 30000
    flow.session['hydration'] = []
    scene.begin(flow)
    flow.session['actorImported'] = True
    scene.message(flow, 0x36, 0x14, b'\0\x14\0')
    scene.message(flow, 0x36, 0x32, struct.pack('>Hi', 0x32, 1))
    flow.result['send'].clear()
    scene.message(flow, 0x36, 0x64, struct.pack('>HHb', 0x64, 1, 0))
    assert flow.result['send'][-1]['body'] == wire.roundState(flow.now, 3, 5999)
    gameAt = flow.now + 5999
    assert flow.session['pending']['round-game'] == gameAt
    assert 'round-start' not in flow.session['pending']
    runtime.prepare()
    state = runtime.switch(host_runtime.encode(flow.state))
    context = host_runtime.encode({'nowMs': flow.now + 100, 'connections': [1]})
    confirm = host_runtime.encode({'type': 'message', 'connection': 1, 'command': 0x36,
                                   'body': struct.pack('>HHb', 0x64, 1, 0)})
    state, sends, timers, logs = runtime.dispatch(confirm, state, context)
    assert [packet['reason'] for packet in sends] == ['actor-play-result-zero'] and timers == []
    assert host_runtime.decode(state)['sessions']['1']['pending']['round-game'] == gameAt
    context = host_runtime.encode({'nowMs': gameAt, 'connections': [1]})
    event = host_runtime.encode({'type': 'timer', 'connection': 1, 'name': 'round-game'})
    state, sends, timers, logs = runtime.dispatch(event, state, context)
    assert sends[0]['body'] == wire.roundState(gameAt, 4, wire.GAME_MS)
    timer = next(item for item in timers if item[0] == '1:round-end')
    deadline = gameAt + wire.GAME_MS
    assert timer[1] == wire.GAME_MS
    assert host_runtime.decode(state)['sessions']['1']['pending']['round-end'] == deadline
    context = host_runtime.encode({'nowMs': deadline, 'connections': [1]})
    event = timer[2]
    state, sends, timers, logs = runtime.dispatch(event, state, context)
    assert [packet['reason'] for packet in sends][-2:] == ['instance-round-result', 'instance-round-state-end']
    session = host_runtime.decode(state)['sessions']['1']
    assert session['roundEnded'] and not session['groundEnabled']
    assert session['pending'] == {'round-finish': deadline + 5000}
    assert runtime.dispatch(event, state, context)[1:3] == ([], [])
    timer = next(item for item in timers if item[0] == '1:round-finish')
    assert timer[1] == 5000
    runtime.prepare()
    state = runtime.switch(state)
    context = host_runtime.encode({'nowMs': deadline + 4999, 'connections': [1]})
    assert runtime.dispatch(timer[2], state, context)[1:3] == ([], [])
    context = host_runtime.encode({'nowMs': deadline + 5000, 'connections': [1]})
    state, sends, timers, logs = runtime.dispatch(timer[2], state, context)
    assert [packet['reason'] for packet in sends] == ['instance-settlement', 'instance-finish-game']
    assert not host_runtime.decode(state)['sessions']['1']['pending']
    assert runtime.dispatch(timer[2], state, context)[1:3] == ([], [])
    print(json.dumps({'level': wire.USER_LEVEL, 'version': runtime.version()}))
finally:
    runtime.close()
'''
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(output),
                             str(project.parent / "revisions")], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["level"] == 100
