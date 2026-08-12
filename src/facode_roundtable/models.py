from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
import hashlib
import uuid
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ExitCode(IntEnum):
    OK = 0
    PARTIAL = 10
    NO_RESULT = 20
    INVALID = 2
    INELIGIBLE = 3
    TIMEOUT = 5
    INTERNAL = 70
    INTERRUPTED = 130


@dataclass(slots=True)
class Citation:
    url: str
    title: str | None = None
    status: str = "provider_reported"


@dataclass(slots=True)
class ProviderResponse:
    provider: str
    content: str
    round: int
    model: str | None = None
    duration_ms: int | None = None
    citations: list[Citation] = field(default_factory=list)


@dataclass(slots=True)
class ProviderError:
    provider: str
    code: str
    message: str
    round: int | None = None


@dataclass(slots=True)
class ChairResult:
    chair: str
    verdict: str
    agreed: list[str] = field(default_factory=list)
    dissent: list[str] = field(default_factory=list)
    recommendation: str = ""


@dataclass(slots=True)
class RunResult:
    schema_version: int
    run_id: str
    mode: str
    question_hash: str
    started_at: str
    requested_heads: list[str]
    eligible_heads: list[str] = field(default_factory=list)
    successful_heads: list[str] = field(default_factory=list)
    failed_heads: list[str] = field(default_factory=list)
    provider_metadata: dict[str, dict[str, Any]] = field(default_factory=dict)
    responses: list[ProviderResponse] = field(default_factory=list)
    errors: list[ProviderError] = field(default_factory=list)
    chair: ChairResult | None = None
    finished_at: str | None = None

    @classmethod
    def create(cls, question: str, requested_heads: list[str], mode: str = "advisory") -> "RunResult":
        return cls(
            schema_version=1,
            run_id=str(uuid.uuid4()),
            mode=mode,
            question_hash=hashlib.sha256(question.encode("utf-8")).hexdigest(),
            started_at=_now(),
            requested_heads=list(requested_heads),
        )

    def finish(self) -> None:
        self.successful_heads = list(dict.fromkeys(item.provider for item in self.responses))
        successful = set(self.successful_heads)
        self.failed_heads = list(
            dict.fromkeys(
                error.provider
                for error in self.errors
                if error.provider in self.requested_heads and error.provider not in successful
            )
        )
        self.finished_at = _now()

    @property
    def exit_code(self) -> ExitCode:
        if not self.successful_heads:
            if self.errors and all(
                error.code in {"ineligible", "research_ineligible", "login_required", "cli_not_found"}
                for error in self.errors
            ):
                return ExitCode.INELIGIBLE
            if self.errors and all(error.code == "timeout" for error in self.errors):
                return ExitCode.TIMEOUT
            return ExitCode.NO_RESULT
        if self.errors or set(self.successful_heads) != set(self.requested_heads):
            return ExitCode.PARTIAL
        return ExitCode.OK

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
