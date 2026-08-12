from __future__ import annotations

import json

from .base import InvocationResult, ProviderError, ProviderStatus, Runner


class GeminiAdapter:
    name = "gemini"

    def __init__(self, runner: Runner, executable: str = "agy"):
        self.runner = runner
        self.executable = executable

    async def status(self) -> ProviderStatus:
        result = await self.runner.run([self.executable, "models"], timeout=20)
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        if result.returncode == 0 and result.stdout.strip():
            return ProviderStatus(
                self.name, True, True, auth_method="google_sign_in", research=False
            )
        return ProviderStatus(self.name, True, False, reason="login_required")

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult:
        if research:
            raise ProviderError(
                "research_ineligible", "gemini cannot enforce web-only tools in this CLI version"
            )
        if len(prompt) > 24_000:
            raise ProviderError("input_too_large", "gemini prompt exceeds the safe command-line limit")
        argv = [
            self.executable, "-p", prompt, "--output-format", "json", "--sandbox", "--mode", "plan",
            "--disable-slash-commands",
        ]
        if model:
            argv.extend(["--model", model])
        result = await self.runner.run(argv, timeout=timeout)
        if result.timed_out:
            raise ProviderError("timeout", "gemini timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "gemini failed")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError("protocol_error", "gemini returned invalid JSON") from exc
        content = payload.get("response")
        if payload.get("status") != "SUCCESS" or not isinstance(content, str) or not content.strip():
            raise ProviderError("empty_response", "gemini returned no answer")
        return InvocationResult(content=content.strip(), model=model, duration_ms=result.duration_ms)
