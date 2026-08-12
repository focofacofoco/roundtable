from __future__ import annotations

import json

from .base import InvocationResult, ProviderError, ProviderStatus, Runner


class CodexAdapter:
    name = "codex"

    def __init__(self, runner: Runner, executable: str = "codex"):
        self.runner = runner
        self.executable = executable

    async def status(self) -> ProviderStatus:
        result = await self.runner.run([self.executable, "login", "status"], timeout=15)
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        output = f"{result.stdout}\n{result.stderr}".lower()
        if "chatgpt" in output and result.returncode == 0:
            return ProviderStatus(self.name, True, True, auth_method="chatgpt", research=False)
        if "api key" in output or "api_key" in output:
            return ProviderStatus(self.name, True, False, reason="api_key_auth_forbidden")
        return ProviderStatus(self.name, True, False, reason="login_required")

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult:
        if research:
            raise ProviderError("research_ineligible", "codex cannot prove web-only tool access")
        argv = [
            self.executable,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "read-only",
            "--json",
        ]
        if model:
            argv.extend(["--model", model])
        argv.append("-")
        result = await self.runner.run(argv, input_text=prompt, timeout=timeout)
        if result.timed_out:
            raise ProviderError("timeout", "codex timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "codex failed")
        content = _parse_output(result.stdout)
        if not content:
            raise ProviderError("empty_response", "codex returned no answer")
        return InvocationResult(content=content, model=model, duration_ms=result.duration_ms)


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
