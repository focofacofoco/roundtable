from __future__ import annotations

import json
import os

from facode_roundtable.catalog import PROVIDER_SPECS

from .base import InvocationResult, ProviderError, ProviderStatus, Runner, probe_cli_version


_DISABLED_FEATURES = (
    "apps",
    "browser_use",
    "browser_use_external",
    "browser_use_full_cdp_access",
    "code_mode_host",
    "computer_use",
    "hooks",
    "image_generation",
    "multi_agent",
    "plugins",
    "remote_plugin",
    "shell_tool",
    "skill_search",
    "tool_suggest",
    "view_image",
    "workspace_dependencies",
)


class CodexAdapter:
    name = "codex"

    def __init__(
        self,
        runner: Runner,
        executable: str = "codex",
        *,
        default_model: str | None = None,
        default_effort: str | None = None,
        windows: bool | None = None,
    ):
        self.runner = runner
        self.executable = executable
        self.default_model = default_model
        self.default_effort = default_effort
        self._windows = os.name == "nt" if windows is None else windows

    async def status(self) -> ProviderStatus:
        result = await self.runner.run([self.executable, "login", "status"], timeout=15)
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        version = await probe_cli_version(self.runner, self.executable)
        output = f"{result.stdout}\n{result.stderr}".lower()
        if "chatgpt" in output and result.returncode == 0:
            spec = PROVIDER_SPECS[self.name]
            return ProviderStatus(
                self.name,
                True,
                True,
                auth_method=spec.auth,
                cli_version=version,
                model=self.default_model,
                research=spec.supports_research(windows=self._windows),
            )
        if "api key" in output or "api_key" in output:
            return ProviderStatus(
                self.name,
                True,
                False,
                reason="api_key_auth_forbidden",
                cli_version=version,
                model=self.default_model,
            )
        return ProviderStatus(
            self.name,
            True,
            False,
            reason="login_required",
            cli_version=version,
            model=self.default_model,
        )

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult:
        if research and not PROVIDER_SPECS[self.name].supports_research(
            windows=self._windows
        ):
            raise ProviderError(
                "research_ineligible", "codex research is supported only on Windows"
            )
        selected_model = model or self.default_model
        argv = [self.executable]
        if research:
            argv.append("--search")
        argv.extend([
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
        ])
        for feature in _DISABLED_FEATURES:
            argv.extend(["--disable", feature])
        argv.extend(["--config", "mcp_servers={}"])
        if not research:
            argv.extend(["--config", 'web_search="disabled"'])
        if self.default_effort:
            argv.extend(
                ["--config", f'model_reasoning_effort="{self.default_effort}"']
            )
        if selected_model:
            argv.extend(["--model", selected_model])
        argv.extend(["--json", "-"])
        result = await self.runner.run(argv, input_text=prompt, timeout=timeout)
        if result.timed_out:
            raise ProviderError("timeout", "codex timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "codex failed")
        content = _parse_output(result.stdout)
        if not content:
            raise ProviderError("empty_response", "codex returned no answer")
        return InvocationResult(
            content=content,
            model=selected_model,
            duration_ms=result.duration_ms,
        )


def _parse_output(output: str) -> str:
    answers: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item", {}) if isinstance(event, dict) else {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                answers.append(text.strip())
    return answers[-1] if answers else ""
