from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from facode_roundtable.executables import resolve_cli
from facode_roundtable.runner import sanitize_environment


REPOSITORY = "focofacofoco/roundtable"
_SEMVER = re.compile(r"v?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")
_MAX_COMMAND_OUTPUT = 1024 * 1024


class LifecycleError(RuntimeError):
    pass


@dataclass(frozen=True, order=True, slots=True)
class Version:
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, value: str) -> "Version":
        match = _SEMVER.fullmatch(value)
        if match is None:
            raise LifecycleError(f"invalid version: {value}")
        return cls(*(int(part) for part in match.groups()))

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"


@dataclass(frozen=True, slots=True)
class Release:
    tag: str
    version: Version


def select_release(payload: str, channel: str, installed_version: str) -> Release | None:
    try:
        values = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise LifecycleError("invalid release catalog") from exc
    if not isinstance(values, list):
        raise LifecycleError("invalid release catalog")
    eligible: list[Release] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        tag = value.get("tagName")
        if (
            not isinstance(tag, str)
            or not tag.startswith("v")
            or value.get("isDraft") is not False
            or value.get("isImmutable") is not True
            or not isinstance(value.get("isPrerelease"), bool)
            or (channel == "stable" and value.get("isPrerelease") is not False)
        ):
            continue
        try:
            version = Version.parse(tag)
        except LifecycleError:
            continue
        eligible.append(Release(tag, version))
    if not eligible:
        raise LifecycleError(f"no eligible {channel} release")
    selected = max(eligible, key=lambda item: item.version)
    if selected.version <= Version.parse(installed_version):
        return None
    return selected


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class ReleaseUpdater:
    def __init__(
        self,
        *,
        channel: str,
        installed_version: str,
        resolver: Callable[[str], str] = resolve_cli,
        command_runner: CommandRunner = subprocess.run,
        windows: bool | None = None,
    ):
        self.channel = channel
        self.installed_version = installed_version
        self._resolver = resolver
        self._run_command = command_runner
        self._windows = os.name == "nt" if windows is None else windows

    def run(self) -> int:
        try:
            return self._update()
        except LifecycleError as exc:
            print(f"roundtable: {exc}", file=sys.stderr)
            return 3

    def _update(self) -> int:
        gh = self._tool("gh")
        uv = self._tool("uv")
        self._checked([gh, "auth", "status", "--hostname", "github.com"], "gh login is required")
        catalog = self._checked(
            [
                gh, "release", "list", "--repo", REPOSITORY, "--limit", "100",
                "--exclude-drafts", "--json",
                "tagName,isPrerelease,isDraft,isImmutable",
            ],
            "release discovery failed",
        ).stdout
        selected = select_release(catalog, self.channel, self.installed_version)
        if selected is None:
            print(f"roundtable: already at {self.installed_version}")
            return 0
        staging = Path(tempfile.mkdtemp(prefix="facode-roundtable-update-"))
        keep_staging = False
        try:
            wheel_name = f"facode_roundtable-{selected.version}-py3-none-any.whl"
            self._checked(
                [
                    gh, "release", "download", selected.tag, "--repo", REPOSITORY,
                    "--pattern", wheel_name, "--dir", str(staging),
                ],
                "release download failed",
            )
            wheel = staging / wheel_name
            if not wheel.is_file():
                raise LifecycleError("release wheel is missing")
            self._checked(
                [
                    gh, "release", "verify-asset", selected.tag, str(wheel),
                    "--repo", REPOSITORY,
                ],
                "release attestation verification failed",
            )
            if self._windows:
                code = schedule_windows_update(uv, wheel, staging)
                keep_staging = code == 0
                return code
            result = self._command([uv, "tool", "install", "--force", str(wheel)])
            if result.returncode != 0:
                raise LifecycleError("installation failed")
            return 0
        finally:
            if not keep_staging:
                shutil.rmtree(staging, ignore_errors=True)

    def _tool(self, name: str) -> str:
        value = self._resolver(name)
        if not Path(value).is_file():
            raise LifecycleError(f"{name} is required for this operation")
        return value

    def _checked(self, argv: list[str], message: str) -> subprocess.CompletedProcess[str]:
        result = self._command(argv)
        if result.returncode != 0:
            raise LifecycleError(message)
        if len(result.stdout.encode("utf-8")) + len(result.stderr.encode("utf-8")) > _MAX_COMMAND_OUTPUT:
            raise LifecycleError("lifecycle command output exceeded 1 MiB")
        return result

    def _command(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._run_command(
                argv,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
                env=sanitize_environment(os.environ),
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise LifecycleError("lifecycle command failed") from exc


def _windows_tool_identity() -> tuple[Path, Path] | None:
    tool_python = Path(sys.executable).resolve()
    if (
        not tool_python.is_file()
        or tool_python.parent.name.casefold() != "scripts"
        or tool_python.parent.parent.name.casefold() != "facode-roundtable"
    ):
        return None
    launcher = Path(resolve_cli("roundtable")).resolve()
    if not launcher.is_file() or launcher.name.casefold() != "roundtable.exe":
        return None
    return tool_python, launcher


def schedule_windows_update(uv: str, wheel: Path, staging: Path) -> int:
    powershell = resolve_cli("pwsh")
    if not Path(powershell).is_file():
        powershell = resolve_cli("powershell")
    if not Path(powershell).is_file():
        raise LifecycleError("PowerShell is required to update on Windows")
    helper = Path(__file__).with_name("update.ps1")
    identity = _windows_tool_identity()
    tool_python, launcher = identity or ("", "")
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(
        subprocess, "CREATE_NO_WINDOW", 0
    )
    try:
        subprocess.Popen(
            [
                powershell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(helper), "-ParentProcessId", str(os.getpid()),
                "-UvPath", uv, "-WheelPath", str(wheel), "-StagingPath", str(staging),
                "-ToolPythonPath", str(tool_python),
                "-RoundtableLauncherPath", str(launcher),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as exc:
        raise LifecycleError("failed to schedule update") from exc
    print("roundtable: update scheduled; it will start after this process exits")
    return 0
