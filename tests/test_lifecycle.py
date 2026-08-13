from __future__ import annotations

import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import venv

import pytest


def lifecycle():
    return importlib.import_module("facode_roundtable.lifecycle")


def releases(*items):
    return json.dumps(list(items))


def release(tag, *, prerelease=True, draft=False, immutable=True):
    return {
        "tagName": tag,
        "isPrerelease": prerelease,
        "isDraft": draft,
        "isImmutable": immutable,
    }


def test_release_selection_is_strict_channel_aware_and_never_downgrades():
    module = lifecycle()
    payload = releases(
        release("v0.11.0"),
        release("v0.10.0", prerelease=False),
        release("v0.9.0"),
        release("not-semver"),
    )

    assert module.select_release(payload, "beta", "0.9.0").tag == "v0.11.0"
    assert module.select_release(payload, "stable", "0.9.0").tag == "v0.10.0"
    assert module.select_release(payload, "beta", "0.11.0") is None


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (releases(release("v0.9.0", draft=True)), "no eligible beta release"),
        (releases(release("v0.9.0", immutable=False)), "no eligible beta release"),
        (releases(release("v0.9.0")), "no eligible stable release"),
        (releases(release("0.9.0")), "no eligible beta release"),
        (releases({"tagName": "v0.9.0", "isDraft": False, "isImmutable": True}), "no eligible beta release"),
        ("{}", "invalid release catalog"),
    ],
)
def test_release_selection_fails_closed(payload, message):
    module = lifecycle()

    channel = "stable" if "stable" in message else "beta"
    with pytest.raises(module.LifecycleError, match=message):
        module.select_release(payload, channel, "0.8.1")


def test_updater_downloads_exact_wheel_verifies_attestation_then_installs(tmp_path):
    module = lifecycle()
    gh = tmp_path / "gh.exe"
    uv = tmp_path / "uv.exe"
    gh.touch()
    uv.touch()
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:3] == ["auth", "status"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["release", "list"]:
            return subprocess.CompletedProcess(argv, 0, releases(release("v0.9.0")), "")
        if argv[1:3] == ["release", "download"]:
            destination = Path(argv[argv.index("--dir") + 1])
            (destination / "facode_roundtable-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
            return subprocess.CompletedProcess(argv, 0, "", "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    updater = module.ReleaseUpdater(
        channel="beta",
        installed_version="0.8.1",
        resolver=lambda name: str({"gh": gh, "uv": uv}[name]),
        command_runner=run,
        windows=False,
    )

    assert updater.run() == 0
    assert [call[1:3] for call in calls] == [
        ["auth", "status"],
        ["release", "list"],
        ["release", "download"],
        ["release", "verify-asset"],
        ["tool", "install"],
    ]
    assert calls[-1][3] == "--force"
    assert calls[-1][4].endswith("facode_roundtable-0.9.0-py3-none-any.whl")
    assert not Path(calls[-1][4]).exists()


def test_updater_does_not_install_when_current(capsys, tmp_path):
    module = lifecycle()
    tool = tmp_path / "tool.exe"
    tool.touch()
    calls = []

    def run(argv, **kwargs):
        calls.append(list(argv))
        if argv[1:3] == ["release", "list"]:
            return subprocess.CompletedProcess(argv, 0, releases(release("v0.9.0")), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    updater = module.ReleaseUpdater(
        channel="beta",
        installed_version="0.9.0",
        resolver=lambda _name: str(tool),
        command_runner=run,
        windows=False,
    )

    assert updater.run() == 0
    assert capsys.readouterr().out == "roundtable: already at 0.9.0\n"
    assert not [call for call in calls if call[1:3] == ["release", "download"]]


def test_updater_maps_install_failure_to_typed_exit(tmp_path):
    module = lifecycle()
    tool = tmp_path / "tool.exe"
    tool.touch()

    def run(argv, **kwargs):
        if argv[1:3] == ["release", "list"]:
            return subprocess.CompletedProcess(argv, 0, releases(release("v0.9.0")), "")
        if argv[1:3] == ["release", "download"]:
            destination = Path(argv[argv.index("--dir") + 1])
            (destination / "facode_roundtable-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
        return subprocess.CompletedProcess(argv, 1 if argv[1:3] == ["tool", "install"] else 0, "", "")

    updater = module.ReleaseUpdater(
        channel="beta",
        installed_version="0.8.1",
        resolver=lambda _name: str(tool),
        command_runner=run,
        windows=False,
    )

    assert updater.run() == 3


def test_updater_removes_staging_after_verification_failure(tmp_path):
    module = lifecycle()
    tool = tmp_path / "tool.exe"
    tool.touch()
    staging = None

    def run(argv, **kwargs):
        nonlocal staging
        if argv[1:3] == ["release", "list"]:
            return subprocess.CompletedProcess(argv, 0, releases(release("v0.9.0")), "")
        if argv[1:3] == ["release", "download"]:
            staging = Path(argv[argv.index("--dir") + 1])
            (staging / "facode_roundtable-0.9.0-py3-none-any.whl").write_bytes(b"wheel")
        code = 1 if argv[1:3] == ["release", "verify-asset"] else 0
        return subprocess.CompletedProcess(argv, code, "", "")

    updater = module.ReleaseUpdater(
        channel="beta",
        installed_version="0.8.1",
        resolver=lambda _name: str(tool),
        command_runner=run,
        windows=False,
    )

    assert updater.run() == 3
    assert staging is not None
    assert not staging.exists()


def test_windows_update_helper_cleans_only_the_supplied_staging_directory():
    helper = (
        Path(__file__).parents[1] / "src" / "facode_roundtable" / "update.ps1"
    ).read_text(encoding="utf-8")

    assert "Remove-Item -LiteralPath $StagingPath -Recurse -Force" in helper
    assert "Resolve-Path -LiteralPath $StagingPath" in helper
    assert "Resolve-Path -LiteralPath $ToolPythonPath" in helper
    assert "Resolve-Path -LiteralPath $RoundtableLauncherPath" in helper
    assert "[regex]::Escape($resolvedToolPython)" in helper
    assert "$process.CommandLine -match $commandPattern" in helper
    assert "taskkill.exe" in helper
    assert all(value in helper for value in ("/PID", "/T", "/F"))
    assert "Stop-Process -Name python" not in helper


def test_windows_update_helper_receives_exact_uv_tool_identity(monkeypatch, tmp_path):
    module = lifecycle()
    pwsh = tmp_path / "pwsh.exe"
    uv = tmp_path / "uv.exe"
    wheel = tmp_path / "roundtable.whl"
    tool_python = tmp_path / "tool" / "Scripts" / "python.exe"
    launcher = tmp_path / "bin" / "roundtable.exe"
    for path in (pwsh, uv, wheel, tool_python, launcher):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    captured = {}

    monkeypatch.setattr(module, "resolve_cli", lambda _name: str(pwsh))

    def popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(module.subprocess, "Popen", popen)
    monkeypatch.setattr(
        module, "_windows_tool_identity", lambda: (tool_python, launcher)
    )

    assert module.schedule_windows_update(uv, wheel, tmp_path) == 0
    argv = captured["argv"]
    assert argv[argv.index("-ToolPythonPath") + 1] == str(tool_python)
    assert argv[argv.index("-RoundtableLauncherPath") + 1] == str(launcher)


def test_windows_tool_identity_rejects_shared_interpreter(monkeypatch, tmp_path):
    module = lifecycle()
    launcher = tmp_path / "bin" / "roundtable.exe"
    launcher.parent.mkdir()
    launcher.touch()
    shared_python = tmp_path / "shared" / "python.exe"
    shared_python.parent.mkdir()
    shared_python.touch()
    monkeypatch.setattr(module.sys, "executable", str(shared_python))
    monkeypatch.setattr(module, "resolve_cli", lambda _name: str(launcher))

    assert module._windows_tool_identity() is None


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree behavior")
def test_windows_update_helper_stops_exact_roundtable_process_tree(tmp_path):
    pwsh = shutil.which("pwsh") or shutil.which("powershell")
    assert pwsh is not None
    tool_root = tmp_path / "facode-roundtable"
    venv.EnvBuilder(with_pip=False).create(tool_root)
    tool_python = tool_root / "Scripts" / "python.exe"
    launcher = tmp_path / "bin" / "roundtable.exe"
    launcher.parent.mkdir()
    pid_file = tmp_path / "child.pid"
    launcher.write_text(
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "open(sys.argv[1], 'w').write(str(child.pid))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    unrelated = subprocess.Popen(
        [tool_python, "-c", "import time; time.sleep(60)"]
    )
    process = subprocess.Popen([tool_python, launcher, pid_file])
    try:
        deadline = time.monotonic() + 10
        child_pid_text = ""
        while not child_pid_text and time.monotonic() < deadline:
            if pid_file.exists():
                child_pid_text = pid_file.read_text(encoding="utf-8").strip()
            time.sleep(0.05)
        child_pid = int(child_pid_text)
        staging = tmp_path / "staging"
        staging.mkdir()
        wheel = staging / "roundtable.whl"
        wheel.touch()
        uv = tmp_path / "uv.cmd"
        uv.write_text("@exit /b 0\n", encoding="utf-8")
        helper = Path(__file__).parents[1] / "src" / "facode_roundtable" / "update.ps1"
        observed = subprocess.run(
            [
                pwsh, "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={process.pid}').CommandLine",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()

        result = subprocess.run(
            [
                pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", helper, "-ParentProcessId", "2147483647", "-UvPath", uv,
                "-WheelPath", wheel, "-StagingPath", staging,
                "-ToolPythonPath", tool_python,
                "-RoundtableLauncherPath", launcher,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

        assert result.returncode == 0, result.stderr
        try:
            returncode = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pytest.fail(f"roundtable process survived: {observed}\n{result.stdout}")
        assert returncode != 0
        assert unrelated.poll() is None
        child_check = subprocess.run(
            [
                pwsh, "-NoProfile", "-NonInteractive", "-Command",
                f"if (Get-Process -Id {child_pid} -ErrorAction SilentlyContinue) {{ exit 1 }}",
            ],
            timeout=10,
            check=False,
        )
        assert child_check.returncode == 0
    finally:
        if process.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        if unrelated.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(unrelated.pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )


def test_release_workflow_is_tag_only_and_validates_owner_annotation():
    root = Path(__file__).parents[1]
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    validator = (root / "scripts" / "validate_release.py").read_text(encoding="utf-8")
    verifier = (root / "scripts" / "verify_release_state.py").read_text(encoding="utf-8")

    assert "tags:" in workflow
    assert "--verify-tag" in workflow
    assert "--prerelease" in workflow
    assert "--latest=false" in workflow
    assert "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a" in workflow
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c" in workflow
    assert workflow.count("uv build --out-dir .artifacts/release") == 1
    assert "gh release verify \"$GITHUB_REF_NAME\"" in workflow
    assert "isImmutable" in verifier
    assert "facode-owned-tag" in validator
    assert "--first-parent" in validator


def test_release_annotation_requires_exact_marker_version_and_sha():
    validator_path = Path(__file__).parents[1] / "scripts" / "validate_release.py"
    spec = importlib.util.spec_from_file_location("validate_release", validator_path)
    assert spec is not None and spec.loader is not None
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    sha = "a" * 40
    annotation = f"facode-owned-tag\nversion: v0.9.0\nsha: {sha}\n"

    validator.validate_annotation(annotation, "v0.9.0", sha)
    with pytest.raises(ValueError, match="version"):
        validator.validate_annotation(annotation, "v0.10.0", sha)
    with pytest.raises(ValueError, match="SHA"):
        validator.validate_annotation(annotation, "v0.9.0", "b" * 40)
    with pytest.raises(ValueError, match="marker"):
        validator.validate_annotation(annotation.replace("facode-owned-tag", "foreign"), "v0.9.0", sha)
