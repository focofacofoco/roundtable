from __future__ import annotations

import json

from facode_roundtable.catalog import PROVIDER_SPECS

from .base import InvocationResult, ProviderError, ProviderStatus, Runner, probe_cli_version


class ClaudeAdapter:
    name = "claude"

    def __init__(
        self,
        runner: Runner,
        executable: str = "claude",
        *,
        default_model: str | None = None,
        default_effort: str | None = None,
    ):
        self.runner = runner
        self.executable = executable
        self.default_model = default_model
        self.default_effort = default_effort

    async def status(self) -> ProviderStatus:
        result = await self.runner.run([self.executable, "auth", "status", "--json"], timeout=15)
        if result.returncode == 127:
            return ProviderStatus(self.name, False, False, reason="cli_not_found")
        version = await probe_cli_version(self.runner, self.executable)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return ProviderStatus(
                self.name, True, False, reason="auth_status_unreadable", cli_version=version
            )
        method = str(payload.get("authMethod", "")).lower()
        if payload.get("loggedIn") is True and method in {"claude.ai", "firstparty", "first_party"}:
            spec = PROVIDER_SPECS[self.name]
            return ProviderStatus(
                self.name,
                True,
                True,
                auth_method=spec.auth,
                cli_version=version,
                model=self.default_model,
                research=spec.research,
            )
        if "api" in method or "key" in method:
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
        tools = "WebSearch,WebFetch" if research else ""
        selected_model = model or self.default_model
        argv = [
            self.executable,
            "--print",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--tools",
            tools,
            "--permission-mode",
            "dontAsk",
            "--safe-mode",
        ]
        if selected_model:
            argv.extend(["--model", selected_model])
        if self.default_effort:
            argv.extend(["--effort", self.default_effort])
        result = await self.runner.run(argv, input_text=prompt, timeout=timeout)
        if result.timed_out:
            raise ProviderError("timeout", "claude timed out")
        if result.returncode != 0:
            raise ProviderError("provider_failed", "claude failed")
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError("protocol_error", "claude returned invalid JSON") from exc
        content = payload.get("result")
        if payload.get("is_error") or not isinstance(content, str) or not content.strip():
            raise ProviderError("empty_response", "claude returned no answer")
        return InvocationResult(
            content=content.strip(),
            model=selected_model,
            duration_ms=result.duration_ms,
        )
