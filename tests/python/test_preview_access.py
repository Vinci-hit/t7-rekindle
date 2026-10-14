"""Preview authorization remains an offline, public-key-only launcher feature."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def test_public_key_build_and_publication_contract():
    project = ET.parse(ROOT / "src/Desktop/T7.Desktop.csproj")
    metadata = {item.attrib["Include"]: item.attrib["Value"] for item in project.iter("AssemblyMetadata")}
    assert metadata["PreviewAccessPublicKey"] == "$(T7_PREVIEW_ACCESS_PUBLIC_KEY)"
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "T7_PREVIEW_ACCESS_PUBLIC_KEY: ${{ vars.T7_PREVIEW_ACCESS_PUBLIC_KEY }}" in workflow
    step = workflow.split("- name: Validate published preview authorization key", 1)[1].split("- name:", 1)[0]
    assert "github.ref_type == 'tag'" in step and "github.event.repository.default_branch" in step
    assert "--preview-key-check" in step
    assert workflow.index("--preview-key-check") < workflow.index("- name: Package application")
    assert "PRIVATE_KEY" not in workflow


def test_authority_is_not_part_of_the_runtime_package():
    script = (ROOT / "scripts/preview-access.ps1").read_text(encoding="utf-8")
    assert script.isascii(), "Windows PowerShell 5.1 requires ASCII or a BOM for script source"
    assert "CreateNew" in script and "DataProtectionScope]::CurrentUser" in script
    assert "expiresAt = $issuedAt + 604800" in script
    desktop_sources = "\n".join(path.read_text(encoding="utf-8-sig") for path in (ROOT / "src/Desktop").rglob("*.cs"))
    assert "EccPrivateBlob" not in desktop_sources and "SignData(" not in desktop_sources


def test_no_preview_gate_fields_added_to_settings_schema():
    settings = (ROOT / "src/Core/Settings.cs").read_text(encoding="utf-8")
    assert "CurrentVersion = 1" in settings
    assert "PreviewAccess" not in settings and "GrantCode" not in settings
    store = (ROOT / "src/Desktop/Services/PreviewAccessStore.cs").read_text(encoding="utf-8")
    assert '"preview-access.json"' in store and "DataProtectionScope.CurrentUser" in store


def test_request_copy_feedback_uses_dialog_toast_overlay():
    root = ET.parse(ROOT / "src/Desktop/Views/PreviewAccessDialog.xaml").getroot()
    ns = "{http://schemas.microsoft.com/winfx/2006/xaml/presentation}"
    host = root.find(".//{clr-namespace:T7.Rekindle.Desktop.Views}ToastHost")
    assert host is not None, "copy feedback must use the shared ToastHost inside the dialog"
    scroll = root.find(".//" + ns + "ScrollViewer")
    parents = {child: parent for parent in root.iter() for child in parent}
    assert parents[host] is parents[scroll]
    assert host.attrib["Grid.Row"] == scroll.attrib["Grid.Row"]
    assert host.attrib["VerticalAlignment"] == "Top"
    assert int(host.attrib["Panel.ZIndex"]) > 0
    assert not any(item.attrib.get("Text") == "{Binding Feedback}" for item in root.iter(ns + "TextBlock"))
