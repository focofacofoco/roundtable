from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from facode_roundtable.models import Citation
from facode_roundtable.runner import CommandResult


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    name: str
    installed: bool
    eligible: bool
    auth_method: str | None = None
    reason: str | None = None
    cli_version: str | None = None
    model: str | None = None
    research: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "installed": self.installed,
            "eligible": self.eligible,
            "auth_method": self.auth_method,
            "reason": self.reason,
            "cli_version": self.cli_version,
            "model": self.model,
            "research": self.research,
        }


@dataclass(frozen=True, slots=True)
class InvocationResult:
    content: str
    model: str | None = None
    duration_ms: int | None = None
    citations: list[Citation] = field(default_factory=list)


class ProviderError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Runner(Protocol):
    async def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float,
        environment: dict[str, str] | None = None,
    ) -> CommandResult: ...


class Adapter(Protocol):
    name: str

    async def status(self) -> ProviderStatus: ...

    async def invoke(
        self, prompt: str, *, timeout: float, model: str | None = None, research: bool = False
    ) -> InvocationResult: ...
