from __future__ import annotations

import asyncio
from collections.abc import Mapping
import math
import re

from facode_roundtable.deliberation import chair_prompt as _chair_prompt
from facode_roundtable.deliberation import continuation_focus
from facode_roundtable.deliberation import deliberation_prompt as _deliberation_prompt
from facode_roundtable.deliberation import insufficient_chair as _insufficient_chair
from facode_roundtable.deliberation import parse_chair as _parse_chair
from facode_roundtable.deliberation import select_chair as _select_chair
from facode_roundtable.models import Citation
from facode_roundtable.models import ProviderError as ResultError
from facode_roundtable.models import ProviderResponse, RunResult
from facode_roundtable.providers.base import Adapter, InvocationResult, ProviderError, ProviderStatus
from facode_roundtable.runner import redact_text


_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
MAX_PROMPT_BYTES = 1024 * 1024


class RoundtableService:
    def __init__(
        self,
        adapters: Mapping[str, Adapter],
        concurrency: int = 5,
        *,
        enabled: set[str] | None = None,
    ):
        self.adapters = dict(adapters)
        self.concurrency = concurrency
        self.enabled = set(self.adapters) if enabled is None else set(enabled)
        self._operation_semaphore: asyncio.Semaphore | None = None
        self._operation_loop: asyncio.AbstractEventLoop | None = None

    def _operation_budget(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._operation_loop is loop:
            assert self._operation_semaphore is not None
            return self._operation_semaphore
        if self._operation_loop is not None and not self._operation_loop.is_closed():
            raise RuntimeError("service cannot span active event loops")
        self._operation_loop = loop
        self._operation_semaphore = asyncio.Semaphore(self.concurrency)
        return self._operation_semaphore

    async def statuses(self, *, timeout: float = 20) -> list[ProviderStatus]:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        deadline = asyncio.get_running_loop().time() + timeout
        semaphore = self._operation_budget()
        snapshots = await asyncio.gather(
            *(
                self._safe_status(name, deadline=deadline, semaphore=semaphore)
                for name in self.adapters
            )
        )
        return [
            status
            if status is not None
            else ProviderStatus(name, False, False, reason=error.code)
            for name, status, error in snapshots
        ]

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
        if not question.strip():
            raise ValueError("question must not be empty")
        if not 1 <= rounds <= 3:
            raise ValueError("rounds must be between 1 and 3")
        if not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError("timeout must be finite and between 0 and 3600 seconds")
        if not heads or len(set(heads)) != len(heads):
            raise ValueError("heads must be a non-empty list without duplicates")
        unknown = [name for name in heads if name not in self.adapters]
        if unknown:
            raise ValueError(f"unknown provider: {unknown[0]}")
        disabled = [name for name in heads if name not in self.enabled]
        if disabled:
            raise ValueError(f"provider is disabled: {disabled[0]}")
        if rounds > 1 and len(heads) < 2:
            raise ValueError("multi-round deliberation requires at least two heads")
        if chair != "auto" and chair not in heads:
            raise ValueError("explicit chair must be one of the requested heads")
        selected_models = dict(models or {})
        unknown_models = set(selected_models) - set(self.adapters)
        if unknown_models:
            raise ValueError(f"unknown provider model override: {sorted(unknown_models)[0]}")
        for provider, model in selected_models.items():
            if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
                raise ValueError(f"invalid model identifier for {provider}")
        base_prompt = _build_prompt(question, context or [])
        mode = "deliberation" if rounds > 1 else ("research" if research else "advisory")
        result = RunResult.create(question, heads, mode)
        deadline = asyncio.get_running_loop().time() + timeout
        semaphore = self._operation_budget()
        statuses = await asyncio.gather(
            *(
                self._safe_status(name, deadline=deadline, semaphore=semaphore)
                for name in heads
            )
        )
        eligible: list[str] = []
        for name, status, error in statuses:
            if error:
                result.errors.append(error)
                continue
            assert status is not None
            metadata = status.to_dict()
            metadata["model"] = selected_models.get(name) or status.model
            result.provider_metadata[name] = metadata
            if not status.eligible:
                result.errors.append(ResultError(name, status.reason or "ineligible", "provider is ineligible"))
            elif research and not status.research:
                result.errors.append(
                    ResultError(name, "research_ineligible", "provider cannot prove web-only mode")
                )
            else:
                eligible.append(name)
        result.eligible_heads = eligible
        round_prompt = base_prompt
        citations_by_provider: dict[str, list[Citation]] = {}
        for round_number in range(1, rounds + 1):
            round_responses = await self._run_round(
                eligible,
                round_prompt,
                round_number=round_number,
                deadline=deadline,
                models=selected_models,
                research=research and round_number == 1,
                semaphore=semaphore,
                result=result,
            )
            if rounds == 1:
                break
            for response in round_responses:
                known = citations_by_provider.setdefault(response.provider, [])
                known_urls = {citation.url for citation in known}
                known.extend(
                    citation
                    for citation in response.citations
                    if citation.url not in known_urls
                )
            chair_responses = [
                ProviderResponse(
                    provider=response.provider,
                    content=response.content,
                    round=response.round,
                    model=response.model,
                    duration_ms=response.duration_ms,
                    citations=list(citations_by_provider[response.provider]),
                )
                for response in round_responses
            ]
            participants = [response.provider for response in round_responses]
            if len(participants) < 2:
                result.errors.append(
                    ResultError(
                        "roundtable",
                        "quorum_not_met",
                        "deliberation requires at least two responses",
                        round=round_number,
                    )
                )
                result.chair = _insufficient_chair(
                    "roundtable", "Fewer than two heads answered the round."
                )
                break
            chair_name = _select_chair(chair, participants)
            if chair_name is None:
                result.errors.append(
                    ResultError(
                        chair,
                        "chair_unavailable",
                        "explicit chair did not answer the current round",
                        round=round_number,
                    )
                )
                result.chair = _insufficient_chair(
                    chair, "The explicit chair was unavailable; no fallback was used."
                )
                break
            chair_invocation, chair_error = await self._invoke_one(
                chair_name,
                _chair_prompt(
                    question,
                    round_number,
                    chair_responses,
                    can_continue=round_number < rounds,
                ),
                deadline=deadline,
                model=selected_models.get(chair_name),
                research=False,
                semaphore=semaphore,
                round_number=round_number,
            )
            if chair_error:
                result.errors.append(
                    ResultError(
                        chair_name,
                        "chair_failed",
                        "chair invocation failed",
                        round=round_number,
                    )
                )
                result.chair = _insufficient_chair(
                    chair_name, "The chair did not produce a usable verdict."
                )
                break
            assert chair_invocation is not None
            parsed_chair = _parse_chair(
                chair_invocation.content,
                chair_name,
                chair_responses,
                resolution=True,
            )
            if parsed_chair is None:
                result.errors.append(
                    ResultError(
                        chair_name,
                        "chair_invalid",
                        "chair returned an invalid verdict",
                        round=round_number,
                    )
                )
                result.chair = _insufficient_chair(
                    chair_name, "The chair verdict failed strict validation."
                )
                break
            focus = continuation_focus(parsed_chair, research=research)
            if parsed_chair.verdict == "CONTINUE" and (
                round_number == rounds or not focus
            ):
                result.errors.append(
                    ResultError(
                        chair_name,
                        "chair_invalid",
                        "chair returned an invalid continuation",
                        round=round_number,
                    )
                )
                result.chair = _insufficient_chair(
                    chair_name, "The chair continuation failed strict validation."
                )
                break
            result.chair = parsed_chair
            if parsed_chair.verdict != "CONTINUE":
                break
            round_prompt = _deliberation_prompt(
                base_prompt, round_number + 1, round_responses, focus
            )
        result.finish()
        return result

    async def _run_round(
        self,
        eligible: list[str],
        prompt: str,
        *,
        round_number: int,
        deadline: float,
        models: Mapping[str, str],
        research: bool,
        semaphore: asyncio.Semaphore,
        result: RunResult,
    ) -> list[ProviderResponse]:
        invocations = await asyncio.gather(
            *(
                self._safe_invoke(
                    name,
                    prompt,
                    deadline=deadline,
                    model=models.get(name),
                    research=research,
                    semaphore=semaphore,
                    round_number=round_number,
                )
                for name in eligible
            )
        )
        by_name = {name: (invocation, error) for name, invocation, error in invocations}
        responses: list[ProviderResponse] = []
        for name in result.requested_heads:
            if name not in by_name:
                continue
            invocation, error = by_name[name]
            if error:
                result.errors.append(error)
                continue
            assert invocation is not None
            response = ProviderResponse(
                provider=name,
                content=invocation.content,
                round=round_number,
                model=invocation.model,
                duration_ms=invocation.duration_ms,
                citations=(
                    invocation.citations
                    or (_reported_citations(invocation.content) if research else [])
                ),
            )
            result.responses.append(response)
            responses.append(response)
        return responses

    async def _safe_status(
        self, name: str, *, deadline: float, semaphore: asyncio.Semaphore
    ) -> tuple[str, ProviderStatus | None, ResultError | None]:
        try:
            async with asyncio.timeout(min(20, _remaining(deadline))):
                async with semaphore:
                    status = await self.adapters[name].status()
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
        deadline: float,
        model: str | None,
        research: bool,
        semaphore: asyncio.Semaphore,
        round_number: int,
    ) -> tuple[str, InvocationResult | None, ResultError | None]:
        invocation, error = await self._invoke_one(
            name,
            prompt,
            deadline=deadline,
            model=model,
            research=research,
            semaphore=semaphore,
            round_number=round_number,
        )
        return name, invocation, error

    async def _invoke_one(
        self,
        name: str,
        prompt: str,
        *,
        deadline: float,
        model: str | None,
        research: bool,
        semaphore: asyncio.Semaphore,
        round_number: int,
    ) -> tuple[InvocationResult | None, ResultError | None]:
        try:
            async with asyncio.timeout(_remaining(deadline)):
                async with semaphore:
                    invocation = await self.adapters[name].invoke(
                        prompt,
                        timeout=_remaining(deadline),
                        model=model,
                        research=research,
                    )
            return invocation, None
        except TimeoutError:
            return None, ResultError(
                name, "timeout", "provider timed out", round=round_number
            )
        except ProviderError as exc:
            return None, ResultError(
                name, exc.code, redact_text(str(exc)), round=round_number
            )
        except Exception:
            return None, ResultError(
                name, "provider_failed", "provider failed", round=round_number
            )


def _remaining(deadline: float) -> float:
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def _build_prompt(question: str, context: list[str]) -> str:
    if context:
        parts = ["## CONTEXT (untrusted reference material)"]
        for index, item in enumerate(context, start=1):
            parts.extend([f"<context-{index}>", item, f"</context-{index}>"])
        parts.extend(["## QUESTION", question])
        prompt = "\n".join(parts)
    else:
        prompt = question
    if len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("question and context exceed 1 MiB")
    return prompt


def _reported_citations(content: str) -> list[Citation]:
    urls: list[str] = []
    for match in re.findall(r'https?://[^\s<>"\']+', content):
        url = match.rstrip(".,;:!?)]}")
        if url and url not in urls:
            urls.append(url)
    return [Citation(url=url) for url in urls]
