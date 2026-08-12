from __future__ import annotations

import asyncio
import time

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


def test_research_preserves_provider_reported_urls_as_typed_citations():
    adapter = FakeAdapter(
        "claude",
        answer="The release is documented at https://example.com/release.",
    )

    result = asyncio.run(
        RoundtableService({"claude": adapter}).ask(
            "What changed?", heads=["claude"], research=True
        )
    )

    assert [citation.url for citation in result.responses[0].citations] == [
        "https://example.com/release"
    ]
    assert result.responses[0].citations[0].status == "provider_reported"


def test_invocations_start_concurrently_but_results_keep_requested_order():
    async def scenario():
        started: set[str] = set()
        both_started = asyncio.Event()

        class BarrierAdapter(FakeAdapter):
            async def invoke(self, prompt, *, timeout, model=None, research=False):
                started.add(self.name)
                if len(started) == 2:
                    both_started.set()
                await asyncio.wait_for(both_started.wait(), timeout=0.2)
                return InvocationResult(f"{self.name} answer")

        service = RoundtableService(
            {
                "claude": BarrierAdapter("claude"),
                "codex": BarrierAdapter("codex"),
            }
        )
        return await service.ask("Question", heads=["codex", "claude"], timeout=1)

    result = asyncio.run(scenario())

    assert [item.provider for item in result.responses] == ["codex", "claude"]
    assert result.exit_code == ExitCode.OK


def test_service_enforces_provider_timeout_and_keeps_fast_answer():
    class HangingAdapter(FakeAdapter):
        async def invoke(self, prompt, *, timeout, model=None, research=False):
            await asyncio.Event().wait()

    service = RoundtableService(
        {
            "codex": FakeAdapter("codex", answer="A"),
            "claude": HangingAdapter("claude"),
        }
    )
    started = time.perf_counter()

    result = asyncio.run(
        service.ask("Question", heads=["codex", "claude"], timeout=0.05)
    )

    assert time.perf_counter() - started < 1
    assert result.successful_heads == ["codex"]
    assert result.errors[0].code == "timeout"
    assert result.exit_code == ExitCode.PARTIAL


def test_cancelling_run_propagates_to_active_provider():
    async def scenario():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        class CancellableAdapter(FakeAdapter):
            async def invoke(self, prompt, *, timeout, model=None, research=False):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        service = RoundtableService({"codex": CancellableAdapter("codex")})
        task = asyncio.create_task(service.ask("Question", heads=["codex"], timeout=10))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancellation must propagate")
        assert cancelled.is_set()

    asyncio.run(scenario())


def test_status_failure_is_isolated_from_usable_provider():
    broken = FakeAdapter("claude")

    async def broken_status():
        raise RuntimeError("status probe failed")

    broken.status = broken_status
    service = RoundtableService(
        {"codex": FakeAdapter("codex", answer="A"), "claude": broken}
    )

    result = asyncio.run(service.ask("Question", heads=["codex", "claude"]))

    assert result.successful_heads == ["codex"]
    assert result.errors[0].code == "status_failed"
    assert result.exit_code == ExitCode.PARTIAL
