from __future__ import annotations

import json
from typing import Any

from .base import InvocationResult, ProviderError, ProviderStatus, Runner, probe_cli_version


_LOGIN_ONLY_ENVIRONMENT = {"GROK_DISABLE_API_KEY_AUTH": "1"}


class GrokAdapter:
    name = "grok"

    def __init__(self, runner: Runner, executable: str = "grok"):
        self.runner = runner
        self.executable = executable

    async def status(self) -> ProviderStatus:
        result, policy_enforced = await self._inspect_policy()
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        version = await probe_cli_version(self.runner, self.executable)
        if result.returncode != 0 or not result.stdout.strip():
            return ProviderStatus(
                self.name, True, False, reason="auth_status_unreadable", cli_version=version
            )
        if not policy_enforced:
            return ProviderStatus(
                self.name,
                True,
                False,
                reason="api_key_auth_forbidden",
                cli_version=version,
            )
        models = await self.runner.run(
            [self.executable, "models"],
            timeout=20,
            environment=_LOGIN_ONLY_ENVIRONMENT,
        )
        models_output = f"{models.stdout}\n{models.stderr}".lower()
        if (
            models.returncode == 0
            and models.stdout.strip()
            and "not authenticated" not in models_output
            and "sign in" not in models_output
        ):
            _, policy_still_enforced = await self._inspect_policy()
            if not policy_still_enforced:
                return ProviderStatus(
                    self.name,
                    True,
                    False,
                    reason="api_key_auth_forbidden",
                    cli_version=version,
                )
            return ProviderStatus(
                self.name,
                True,
                True,
                auth_method="oauth",
                cli_version=version,
                research=True,
            )
        return ProviderStatus(
            self.name, True, False, reason="login_required", cli_version=version
        )

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult:
        policy_result, policy_enforced = await self._inspect_policy()
        if policy_result.returncode == 127:
            raise ProviderError("cli_not_found", "grok CLI is not installed")
        if not policy_enforced:
            raise ProviderError(
                "api_key_auth_forbidden", "grok API-key authentication is not disabled"
            )
        tools = "web_search,web_fetch" if research else ""
        argv = [
            self.executable,
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
        result = await self.runner.run(
            argv,
            input_text=prompt,
            timeout=timeout,
            environment=_LOGIN_ONLY_ENVIRONMENT,
        )
        if result.timed_out:
            raise ProviderError("timeout", "grok timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "grok failed")
        content = _json_text(result.stdout)
        if not content:
            raise ProviderError("empty_response", "grok returned no answer")
        return InvocationResult(content=content, model=model, duration_ms=result.duration_ms)

    async def _inspect_policy(self):
        result = await self.runner.run(
            [self.executable, "inspect", "--json"],
            timeout=20,
            environment=_LOGIN_ONLY_ENVIRONMENT,
        )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return result, False
        policy = (
            _find(payload, "disable_api_key_auth") is True
            or _find(payload, "disableApiKeyAuth") is True
        )
        enforced = (
            _find(payload, "api_key_auth_disabled") is True
            or _find(payload, "apiKeyAuthDisabled") is True
        )
        return result, policy and enforced


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
