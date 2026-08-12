from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated, Any

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel

from facode_roundtable.catalog import capabilities_payload, unsupported_providers
from facode_roundtable.config import Config, config_path, load_config
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
    schema_version: int
    providers: list[dict[str, Any]]
    unsupported: dict[str, str]
    capabilities: dict[str, dict[str, Any]]


class DoctorWire(BaseModel):
    schema_version: int
    config_path: str
    config_valid: bool
    providers: list[dict[str, Any]]
    capabilities: dict[str, dict[str, Any]]


def create_server(
    *,
    service: RoundtableService,
    config: Config | None = None,
    config_file: Path | None = None,
) -> MCPServer:
    effective = config or load_config(config_file)
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
        chair: str | None = None,
        timeout: float | None = None,
        models: dict[str, str] | None = None,
    ) -> Annotated[CallToolResult, RunResultWire]:
        """Ask eligible Roundtable heads a question."""
        available = [
            name
            for name in service.adapters
            if effective.providers[name].enabled
        ]
        if heads is None:
            selected = (
                available
                if effective.default_heads == "available"
                else list(effective.default_heads)
            )
        else:
            selected = heads
        disabled = [name for name in selected if name not in available]
        if disabled:
            raise ValueError(f"provider is disabled: {disabled[0]}")
        configured_models = {
            name: provider.model
            for name, provider in effective.providers.items()
            if provider.model is not None and name in selected
        }
        configured_models.update(models or {})
        result = await service.ask(
            question,
            heads=selected,
            rounds=rounds,
            research=research,
            chair=chair or effective.chair,
            timeout=(
                timeout
                if timeout is not None
                else (
                    effective.research_timeout_seconds
                    if research
                    else effective.timeout_seconds
                )
            ),
            models=configured_models,
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
            "schema_version": 1,
            "providers": [status.to_dict() for status in statuses],
            "unsupported": unsupported_providers(),
            "capabilities": capabilities_payload(),
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
            load_config(config_file)
        except Exception:
            valid = False
        statuses = await asyncio.gather(*(adapter.status() for adapter in service.adapters.values()))
        payload = {
            "schema_version": 1,
            "config_path": str(config_file or config_path()),
            "config_valid": valid,
            "providers": [status.to_dict() for status in statuses],
            "capabilities": capabilities_payload(),
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


def serve(
    service: RoundtableService,
    *,
    config: Config | None = None,
    config_file: Path | None = None,
) -> None:
    create_server(service=service, config=config, config_file=config_file).run(
        transport="stdio"
    )
