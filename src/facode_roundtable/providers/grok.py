from __future__ import annotations

import json
from typing import Any

from .base import InvocationResult, ProviderError, ProviderStatus, Runner


class GrokAdapter:
    name = "grok"

    def __init__(self, runner: Runner):
        self.runner = runner

    async def status(self) -> ProviderStatus:
        result = await self.runner.run(["grok", "inspect", "--json"], timeout=20)
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ProviderStatus(self.name, True, False, reason="auth_status_unreadable")
        method = str(_find(payload, "method") or "").lower()
        authenticated = _find(payload, "authenticated") is True
        policy = _find(payload, "disable_api_key_auth") is True
        if "api" in method or "key" in method or not policy:
            return ProviderStatus(self.name, True, False, reason="api_key_auth_forbidden")
        if authenticated and method in {"oauth", "oidc", "device_auth", "device_code"}:
            return ProviderStatus(self.name, True, True, auth_method="oauth", research=True)
        return ProviderStatus(self.name, True, False, reason="login_required")

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult:
        tools = "web_search,web_fetch" if research else ""
        argv = [
            "grok",
            "--prompt-file",
            "-",
            "--output-format",
            "json",
            "--no-auto-update",
            "--tools",
            tools,
            "--deny",
            "MCPTool(*)",
            "--permission-mode",
            "dontAsk",
        ]
        if model:
            argv.extend(["--model", model])
        result = await self.runner.run(argv, input_text=prompt, timeout=timeout)
        if result.timed_out:
            raise ProviderError("timeout", "grok timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "grok failed")
        content = _json_text(result.stdout)
        if not content:
            raise ProviderError("empty_response", "grok returned no answer")
        return InvocationResult(content=content, model=model, duration_ms=result.duration_ms)


def _find(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find(child, key)
            if found is not None:
                return found
    return None


def _json_text(output: str) -> str:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return ""
    for key in ("text", "response", "result", "content"):
        value = _find(payload, key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""
