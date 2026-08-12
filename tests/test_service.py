from __future__ import annotations

import asyncio

from facode_roundtable.models import ExitCode
from facode_roundtable.providers.base import InvocationResult, ProviderError, ProviderStatus
from facode_roundtable.service import RoundtableService


class FakeAdapter:
    def __init__(self, name: str, *, answer: str | None = None, failure: str | None = None):
        self.name = name
        self.answer = answer
        self.failure = failure
        self.prompts: list[str] = []

    async def status(self):
        return ProviderStatus(self.name, True, True, auth_method="test", research=True)

    async def invoke(self, prompt, *, timeout, model=None, research=False):
        self.prompts.append(prompt)
        if self.failure:
            raise ProviderError(self.failure, f"{self.name} failed")
        return InvocationResult(self.answer or f"{self.name} answer", model=model, duration_ms=1)


def test_advisory_returns_independent_answers_in_requested_order():
    codex = FakeAdapter("codex", answer="A")
    claude = FakeAdapter("claude", answer="B")
    service = RoundtableService({"claude": claude, "codex": codex})

    result = asyncio.run(service.ask("Question", heads=["codex", "claude"]))

    assert [item.provider for item in result.responses] == ["codex", "claude"]
    assert [item.content for item in result.responses] == ["A", "B"]
    assert codex.prompts == ["Question"]
    assert claude.prompts == ["Question"]
    assert result.exit_code == ExitCode.OK


def test_partial_failure_preserves_usable_answer_and_typed_error():
    service = RoundtableService(
        {"codex": FakeAdapter("codex", answer="A"), "claude": FakeAdapter("claude", failure="timeout")}
    )

    result = asyncio.run(service.ask("Question", heads=["codex", "claude"]))

    assert result.successful_heads == ["codex"]
    assert result.failed_heads == ["claude"]
    assert result.errors[0].code == "timeout"
    assert result.exit_code == ExitCode.PARTIAL


def test_research_excludes_provider_that_cannot_prove_web_only_mode():
    adapter = FakeAdapter("codex")

    async def status_without_research():
        return ProviderStatus("codex", True, True, auth_method="test", research=False)

    adapter.status = status_without_research
    result = asyncio.run(RoundtableService({"codex": adapter}).ask("Question", heads=["codex"], research=True))

    assert result.exit_code == ExitCode.INELIGIBLE
    assert result.errors[0].code == "research_ineligible"
    assert adapter.prompts == []
