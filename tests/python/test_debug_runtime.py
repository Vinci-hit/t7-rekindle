import os
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET

import pytest

from test_build import load_build


ROOT = Path(__file__).resolve().parents[2]
TARGETS = ROOT / "src/Desktop/DebugRuntime.targets"


@pytest.fixture(scope="module")
def msbuild():
    if os.name != "nt":
        pytest.skip("Debug runtime copy tests require Windows MSBuild")
    return load_build().locate_msbuild(require_native=False)


@pytest.fixture
def project(tmp_path):
    python_home = tmp_path / "python toolchain"
    for name in (
        "python314.dll", "python3.dll", "vcruntime140.dll", "vcruntime140_1.dll",
        "DLLs/_socket.pyd", "DLLs/_ssl.pyd", "DLLs/libcrypto-3.dll",
        "Lib/encodings/__init__.py", "Lib/socket.py", "Lib/ctypes/__init__.py",
        "Lib/ensurepip/_bundled/fixture.whl", "Lib/site-packages/private.py",
        "Lib/test/test_socket.py", "Lib/__pycache__/socket.pyc",
    ):
        path = python_home / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name, encoding="utf-8")

    root = ET.Element("Project")
    properties = ET.SubElement(root, "PropertyGroup")
    for name, value in {
        "Configuration": "Debug",
        "PythonHome": str(python_home),
        "OutputPath": "out/$(MSBuildProjectName)/$(Configuration)/",
        "OutDir": "$(OutputPath)",
        "TargetFrameworkVersion": "v4.8",
        "CopyRetryCount": "0",
    }.items():
        ET.SubElement(properties, name).text = value
    ET.SubElement(root, "Import", Project=str(TARGETS))
    ET.SubElement(root, "Import", Project=r"$(MSBuildToolsPath)\Microsoft.CSharp.targets")
    path = tmp_path / "T7R.DebugFixture.csproj"
    ET.ElementTree(root).write(path, encoding="utf-8")
    return path


def run_copy(msbuild, project, *, properties=None, target=None):
    arguments = [str(msbuild), str(project), "/nologo", "/nr:false", "/v:minimal",
                 "/t:" + (target or "AssignTargetPaths;_CopySourceItemsToOutputDirectory")]
    arguments += [f"/p:{key}={value}" for key, value in (properties or {}).items()]
    return subprocess.run(arguments, cwd=project.parent, capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def assert_success(result):
    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning " not in result.stdout.lower(), result.stdout


def test_desktop_imports_debug_runtime_rules():
    project = ET.parse(ROOT / "src/Desktop/T7R.Desktop.csproj").getroot()
    assert len([item for item in project.findall("Import")
                if item.get("Project") == "DebugRuntime.targets"]) == 1


def test_debug_output_contains_runtime_stdlib_and_extensions(msbuild, project):
    assert_success(run_copy(msbuild, project))
    output = project.parent / "out" / project.stem / "Debug"
    expected = {
        "python314.dll": "python314.dll",
        "python3.dll": "python3.dll",
        "vcruntime140.dll": "vcruntime140.dll",
        "vcruntime140_1.dll": "vcruntime140_1.dll",
        "python/_socket.pyd": "DLLs/_socket.pyd",
        "python/_ssl.pyd": "DLLs/_ssl.pyd",
        "python/libcrypto-3.dll": "DLLs/libcrypto-3.dll",
        "python/Lib/encodings/__init__.py": "Lib/encodings/__init__.py",
        "python/Lib/socket.py": "Lib/socket.py",
        "python/Lib/ctypes/__init__.py": "Lib/ctypes/__init__.py",
        "python/Lib/ensurepip/_bundled/fixture.whl": "Lib/ensurepip/_bundled/fixture.whl",
    }
    assert {path.relative_to(output).as_posix(): path.read_text(encoding="utf-8")
            for path in output.rglob("*") if path.is_file()} == expected

    before = {name: (output / name).stat().st_mtime_ns for name in expected}
    assert_success(run_copy(msbuild, project))
    assert before == {name: (output / name).stat().st_mtime_ns for name in expected}
    (output / "python314.dll").unlink()
    assert_success(run_copy(msbuild, project))
    assert (output / "python314.dll").read_text(encoding="utf-8") == "python314.dll"

    renamed = project.with_name("T7R.RenamedFixture.csproj")
    project.rename(renamed)
    assert_success(run_copy(msbuild, renamed))
    assert (project.parent / "out" / renamed.stem / "Debug/python314.dll").is_file()


@pytest.mark.parametrize("properties", [{"Configuration": "Release"}, {"PythonHome": ""}])
def test_release_and_managed_only_builds_do_not_stage_python(msbuild, project, properties):
    assert_success(run_copy(msbuild, project, properties=properties))
    assert not list((project.parent / "out").rglob("python314.dll"))


@pytest.mark.parametrize("missing", ["python314.dll", "Lib/encodings/__init__.py", "DLLs/_socket.pyd"])
def test_incomplete_configured_toolchain_fails_before_copy(msbuild, project, missing):
    (project.parent / "python toolchain" / missing).unlink()
    result = run_copy(msbuild, project)
    assert result.returncode != 0
    assert "PythonHome" in result.stdout
    assert not list((project.parent / "out").rglob("python314.dll"))


def test_design_time_validation_does_not_prepare_runtime(msbuild, project):
    (project.parent / "python toolchain/python314.dll").unlink()
    assert_success(run_copy(msbuild, project, properties={"DesignTimeBuild": "true"},
                            target="T7RValidateDebugRuntime"))
    assert not (project.parent / "out").exists()
