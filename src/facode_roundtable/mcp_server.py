from __future__ import annotations

import asyncio
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from facode_roundtable.config import config_path, load_config
from facode_roundtable.render import render_markdown
from facode_roundtable.service import RoundtableService


class RunResultWire(BaseModel):
    schema_version: int
    run_id: str
    mode: str
    question_hash: str
    started_at: str
    requested_heads: list[str]
    eligible_heads: list[str]
    successful_heads: list[str]
    failed_heads: list[str]
    provider_metadata: dict[str, dict[str, Any]]
    responses: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    chair: dict[str, Any] | None
    finished_at: str | None


class ProvidersWire(BaseModel):
    providers: list[dict[str, Any]]
    unsupported: dict[str, str]


class DoctorWire(BaseModel):
    config_path: str
    config_valid: bool
    providers: list[dict[str, Any]]


def create_server(*, service: RoundtableService) -> MCPServer:
    server = MCPServer(
        "facode-roundtable",
        instructions="Convene independent login-authenticated model CLIs; use rounds>1 for deliberation.",
    )

    @server.tool()
    async def roundtable_ask(
        question: str,
        heads: list[str] | None = None,
        rounds: int = 1,
        research: bool = False,
        chair: str = "auto",
        timeout: float | None = None,
    ) -> Annotated[CallToolResult, RunResultWire]:
        """Ask eligible Roundtable heads a question."""
        selected = heads or list(service.adapters)
        result = await service.ask(
            question,
            heads=selected,
            rounds=rounds,
            research=research,
            chair=chair,
            timeout=timeout if timeout is not None else (600 if research else 300),
        )
        payload = result.to_dict()
        return CallToolResult(
            content=[TextContent(type="text", text=render_markdown(result))],
            structured_content=payload,
            is_error=int(result.exit_code) >= 20,
        )

    @server.tool()
    async def roundtable_providers() -> Annotated[CallToolResult, ProvidersWire]:
        """List provider eligibility without invoking a model."""
        statuses = await asyncio.gather(*(adapter.status() for adapter in service.adapters.values()))
        payload = {
            "providers": [status.to_dict() for status in statuses],
            "unsupported": {"glm": "no_official_login_only_headless_cli"},
        }
        text = "\n".join(
            f"- {item.name}: {'eligible' if item.eligible else item.reason}" for item in statuses
        )
        return CallToolResult(
            content=[TextContent(type="text", text=text)], structured_content=payload, is_error=False
        )

    @server.tool()
    async def roundtable_doctor() -> Annotated[CallToolResult, DoctorWire]:
        """Inspect local configuration and provider login status without inference."""
        valid = True
        try:
            load_config()
        except Exception:
            valid = False
        statuses = await asyncio.gather(*(adapter.status() for adapter in service.adapters.values()))
        payload = {
            "config_path": str(config_path()),
            "config_valid": valid,
            "providers": [status.to_dict() for status in statuses],
        }
        text = f"Config: {'valid' if valid else 'invalid'}\n" + "\n".join(
            f"- {item.name}: {'eligible' if item.eligible else item.reason}" for item in statuses
        )
        return CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=payload,
            is_error=not valid,
        )

    return server


def serve(service: RoundtableService) -> None:
    create_server(service=service).run(transport="stdio")
