from __future__ import annotations

import json

from .base import InvocationResult, ProviderError, ProviderStatus, Runner, probe_cli_version
from .grok import _json_text


class MiniMaxAdapter:
    name = "minimax"

    def __init__(self, runner: Runner, executable: str = "mmx"):
        self.runner = runner
        self.executable = executable

    async def status(self) -> ProviderStatus:
        result = await self.runner.run([self.executable, "auth", "status"], timeout=20)
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        version = await probe_cli_version(self.runner, self.executable)
        output = f"{result.stdout}\n{result.stderr}".lower()
        if "api key" in output or "api_key" in output:
            return ProviderStatus(
                self.name,
                True,
                False,
                reason="api_key_auth_forbidden",
                cli_version=version,
            )
        if result.returncode == 0 and "oauth" in output:
            return ProviderStatus(
                self.name,
                True,
                True,
                auth_method="oauth",
                cli_version=version,
                research=False,
            )
        return ProviderStatus(
            self.name, True, False, reason="login_required", cli_version=version
        )

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult:
        if research:
            raise ProviderError("research_ineligible", "minimax chat cannot prove web-only mode")
        argv = [self.executable, "text", "chat", "--messages-file", "-", "--output", "json"]
        if model:
            argv.extend(["--model", model])
        messages = json.dumps([{"role": "user", "content": prompt}], ensure_ascii=False)
        result = await self.runner.run(argv, input_text=messages, timeout=timeout)
        if result.timed_out:
            raise ProviderError("timeout", "minimax timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "minimax failed")
        content = _json_text(result.stdout)
        if not content:
            raise ProviderError("empty_response", "minimax returned no answer")
        return InvocationResult(content=content, model=model, duration_ms=result.duration_ms)
