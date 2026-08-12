from __future__ import annotations

from collections.abc import Callable
import importlib.resources
import os
from pathlib import Path
import subprocess
from typing import Any

from facode_roundtable.executables import resolve_cli
from facode_roundtable.runner import sanitize_environment


CommandRunner = Callable[[list[str]], subprocess.CompletedProcess[str]]

_MCP_COMMANDS = {
    "codex": {
        "get": ["codex", "mcp", "get", "roundtable"],
        "add": ["codex", "mcp", "add", "roundtable", "--", "roundtable", "mcp", "serve"],
        "remove": ["codex", "mcp", "remove", "roundtable"],
    },
    "claude": {
        "get": ["claude", "mcp", "list"],
        "add": [
            "claude",
            "mcp",
            "add",
            "--scope",
            "user",
            "--transport",
            "stdio",
            "roundtable",
            "--",
            "roundtable",
            "mcp",
            "serve",
        ],
        "remove": ["claude", "mcp", "remove", "roundtable", "--scope", "user"],
    },
}


class HarnessManager:
    def __init__(
        self,
        *,
        home: Path | None = None,
        command_runner: CommandRunner | None = None,
        skill_text: str | None = None,
    ):
        self.home = home or Path.home()
        self.command_runner = command_runner or _run_command
        self.skill_text = skill_text or _packaged_skill()

    def status(self) -> dict[str, Any]:
        components = {
            f"{provider}_mcp": self._mcp_status(provider)
            for provider in _MCP_COMMANDS
        }
        components.update(
            {
                name: self._skill_status(path)
                for name, path in self._skill_targets().items()
            }
        )
        return _report("status", components)

    def install(self) -> dict[str, Any]:
        components: dict[str, dict[str, Any]] = {}
        for provider in _MCP_COMMANDS:
            state = self._mcp_status(provider)
            if not state["configured"] and state.get("reason") != "conflict":
                added = self.command_runner(_MCP_COMMANDS[provider]["add"])
                state = self._mcp_status(provider) if added.returncode == 0 else {
                    "configured": False,
                    "reason": "install_failed",
                }
            components[f"{provider}_mcp"] = state
        for name, path in self._skill_targets().items():
            state = self._skill_status(path)
            if not state["configured"] or not state["current"]:
                if path.exists() and not _is_roundtable_skill(
                    path.read_text(encoding="utf-8", errors="replace")
                ):
                    state = {"configured": False, "current": False, "reason": "conflict"}
                else:
                    _atomic_write(path, self.skill_text)
                    state = self._skill_status(path)
            components[name] = state
        return _report("install", components)

    def remove(self) -> dict[str, Any]:
        components: dict[str, dict[str, Any]] = {}
        for provider in _MCP_COMMANDS:
            state = self._mcp_status(provider)
            if state["configured"]:
                removed = self.command_runner(_MCP_COMMANDS[provider]["remove"])
                state = self._mcp_status(provider) if removed.returncode == 0 else {
                    "configured": True,
                    "reason": "remove_failed",
                }
            components[f"{provider}_mcp"] = state
        for name, path in self._skill_targets().items():
            if path.exists():
                content = path.read_text(encoding="utf-8", errors="replace")
                if not _is_roundtable_skill(content):
                    components[name] = {
                        "configured": True,
                        "current": False,
                        "reason": "conflict",
                    }
                    continue
                path.unlink()
                try:
                    path.parent.rmdir()
                except OSError:
                    pass
            components[name] = self._skill_status(path)
        return _report("remove", components, expect_configured=False)

    def _mcp_status(self, provider: str) -> dict[str, Any]:
        result = self.command_runner(_MCP_COMMANDS[provider]["get"])
        if result.returncode != 0:
            return {"configured": False, "reason": "not_configured"}
        if provider == "codex":
            normalized = result.stdout.lower().replace("\r", "")
            fields = {
                key.strip(): value.strip()
                for line in normalized.splitlines()
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            if fields.get("command") != "roundtable" or fields.get("args") != "mcp serve":
                return {"configured": False, "reason": "conflict"}
        else:
            normalized = result.stdout.lower().replace("\r", "")
            matching = [
                line
                for line in normalized.splitlines()
                if line.startswith("roundtable:")
            ]
            if len(matching) != 1 or not matching[0].startswith(
                "roundtable: roundtable mcp serve - "
            ):
                return {"configured": False, "reason": "conflict"}
        return {"configured": True, "reason": None}

    def _skill_targets(self) -> dict[str, Path]:
        relative = Path("skills") / "roundtable" / "SKILL.md"
        return {
            "agents_skill": self.home / ".agents" / relative,
            "claude_skill": self.home / ".claude" / relative,
        }

    def _skill_status(self, path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"configured": False, "current": False, "reason": "not_configured"}
        content = path.read_text(encoding="utf-8", errors="replace")
        if not _is_roundtable_skill(content):
            return {"configured": True, "current": False, "reason": "conflict"}
        current = content == self.skill_text
        return {
            "configured": True,
            "current": current,
            "reason": None if current else "outdated",
        }


def _report(
    action: str,
    components: dict[str, dict[str, Any]],
    *,
    expect_configured: bool = True,
) -> dict[str, Any]:
    ok = all(
        item.get("configured") is expect_configured
        and (not expect_configured or item.get("current", True))
        and item.get("reason") not in {"conflict", "install_failed", "remove_failed"}
        for item in components.values()
    )
    return {"action": action, "ok": ok, "components": components}


def _packaged_skill() -> str:
    resource = importlib.resources.files("facode_roundtable").joinpath(
        "skill", "SKILL.md"
    )
    if resource.is_file():
        return resource.read_text(encoding="utf-8")
    checkout = (
        Path(__file__).parents[2]
        / "plugins"
        / "roundtable"
        / "skills"
        / "roundtable"
        / "SKILL.md"
    )
    return checkout.read_text(encoding="utf-8")


def _is_roundtable_skill(content: str) -> bool:
    if "name: roundtable" not in content:
        return False
    marker = "<!-- facode-roundtable-managed -->"
    if marker in content:
        return True
    legacy = _packaged_skill().replace(f"{marker}\n", "", 1)
    return content == legacy


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(path)


def _run_command(argv: list[str]) -> subprocess.CompletedProcess[str]:
    environment = sanitize_environment(os.environ)
    command = [resolve_cli(argv[0]), *argv[1:]]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(command, 127, "", type(exc).__name__)
