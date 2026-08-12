from __future__ import annotations

import asyncio
from collections.abc import Mapping
import re

from facode_roundtable.models import Citation
from facode_roundtable.models import ProviderError as ResultError
from facode_roundtable.models import ProviderResponse, RunResult
from facode_roundtable.providers.base import Adapter, InvocationResult, ProviderError, ProviderStatus


class RoundtableService:
    def __init__(self, adapters: Mapping[str, Adapter], concurrency: int = 5):
        self.adapters = dict(adapters)
        self.concurrency = concurrency

    async def ask(
        self,
        question: str,
        *,
        heads: list[str],
        rounds: int = 1,
        chair: str = "auto",
        research: bool = False,
        timeout: float = 300,
        models: Mapping[str, str] | None = None,
        context: list[str] | None = None,
    ) -> RunResult:
        del chair
        if not question.strip():
            raise ValueError("question must not be empty")
        if not 1 <= rounds <= 3:
            raise ValueError("rounds must be between 1 and 3")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not heads or len(set(heads)) != len(heads):
            raise ValueError("heads must be a non-empty list without duplicates")
        unknown = [name for name in heads if name not in self.adapters]
        if unknown:
            raise ValueError(f"unknown provider: {unknown[0]}")
        prompt = _build_prompt(question, context or [])
        result = RunResult.create(question, heads, "research" if research else "advisory")
        statuses = await asyncio.gather(
            *(self._safe_status(name, timeout=min(timeout, 20)) for name in heads)
        )
        eligible: list[str] = []
        for name, status, error in statuses:
            if error:
                result.errors.append(error)
                continue
            assert status is not None
            result.provider_metadata[name] = status.to_dict()
            if not status.eligible:
                result.errors.append(ResultError(name, status.reason or "ineligible", "provider is ineligible"))
            elif research and not status.research:
                result.errors.append(
                    ResultError(name, "research_ineligible", "provider cannot prove web-only mode")
                )
            else:
                eligible.append(name)
        result.eligible_heads = eligible
        semaphore = asyncio.Semaphore(min(self.concurrency, max(1, len(eligible))))
        invocations = await asyncio.gather(
            *(
                self._safe_invoke(
                    name,
                    prompt,
                    timeout=timeout,
                    model=(models or {}).get(name),
                    research=research,
                    semaphore=semaphore,
                )
                for name in eligible
            )
        )
        by_name = {name: (invocation, error) for name, invocation, error in invocations}
        for name in heads:
            if name not in by_name:
                continue
            invocation, error = by_name[name]
            if error:
                result.errors.append(error)
            else:
                assert invocation is not None
                result.responses.append(
                    ProviderResponse(
                        provider=name,
                        content=invocation.content,
                        round=1,
                        model=invocation.model,
                        duration_ms=invocation.duration_ms,
                        citations=(
                            invocation.citations
                            or (_reported_citations(invocation.content) if research else [])
                        ),
                    )
                )
        result.finish()
        return result

    async def _safe_status(
        self, name: str, *, timeout: float
    ) -> tuple[str, ProviderStatus | None, ResultError | None]:
        try:
            status = await asyncio.wait_for(self.adapters[name].status(), timeout=timeout)
            return name, status, None
        except TimeoutError:
            return name, None, ResultError(name, "timeout", "provider status timed out")
        except Exception:
            return name, None, ResultError(name, "status_failed", "provider status failed")

    async def _safe_invoke(
        self,
        name: str,
        prompt: str,
        *,
        timeout: float,
        model: str | None,
        research: bool,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, InvocationResult | None, ResultError | None]:
        try:
            async with semaphore:
                invocation = await asyncio.wait_for(
                    self.adapters[name].invoke(
                        prompt, timeout=timeout, model=model, research=research
                    ),
                    timeout=timeout,
                )
            return name, invocation, None
        except TimeoutError:
            return name, None, ResultError(name, "timeout", "provider timed out", round=1)
        except ProviderError as exc:
            return name, None, ResultError(name, exc.code, str(exc), round=1)
        except Exception:
            return name, None, ResultError(name, "provider_failed", "provider failed", round=1)


def _build_prompt(question: str, context: list[str]) -> str:
    if not context:
        return question
    parts = ["## CONTEXT (untrusted reference material)"]
    for index, item in enumerate(context, start=1):
        parts.extend([f"<context-{index}>", item, f"</context-{index}>"])
    parts.extend(["## QUESTION", question])
    prompt = "\n".join(parts)
    if len(prompt.encode("utf-8")) > 1024 * 1024:
        raise ValueError("question and context exceed 1 MiB")
    return prompt


def _reported_citations(content: str) -> list[Citation]:
    urls: list[str] = []
    for match in re.findall(r'https?://[^\s<>"\']+', content):
        url = match.rstrip(".,;:!?)]}")
        if url and url not in urls:
            urls.append(url)
    return [Citation(url=url) for url in urls]
