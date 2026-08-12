from __future__ import annotations

import asyncio
import json

from mcp import Client

from facode_roundtable.mcp_server import create_server
from facode_roundtable.cli import main
from facode_roundtable.models import ProviderError, ProviderResponse, RunResult


class FakeService:
    def __init__(self, *, fail: bool = False):
        self.fail = fail

    async def ask(self, question, **kwargs):
        result = RunResult.create(question, kwargs["heads"])
        result.eligible_heads = list(kwargs["heads"])
        if self.fail:
            result.errors.append(ProviderError(kwargs["heads"][0], "provider_failed", "failed", 1))
        else:
            result.responses.append(ProviderResponse(kwargs["heads"][0], "MCP answer", 1))
        result.finish()
        return result


async def call_tool(service, name, arguments):
    async with Client(create_server(service=service)) as client:
        return await client.call_tool(name, arguments)


def test_mcp_ask_returns_markdown_and_same_structured_result():
    result = asyncio.run(
        call_tool(FakeService(), "roundtable_ask", {"question": "Question", "heads": ["codex"]})
    )

    assert result.is_error is False
    assert result.content[0].text.startswith("# Roundtable")
    assert result.structured_content["responses"][0]["content"] == "MCP answer"


def test_mcp_marks_unusable_run_as_tool_error():
    result = asyncio.run(
        call_tool(FakeService(fail=True), "roundtable_ask", {"question": "Question", "heads": ["codex"]})
    )

    assert result.is_error is True
    assert result.structured_content["errors"][0]["code"] == "provider_failed"


def test_mcp_exposes_three_tools_with_output_schemas():
    async def inspect():
        async with Client(create_server(service=FakeService())) as client:
            return await client.list_tools()

    listing = asyncio.run(inspect())
    by_name = {tool.name: tool for tool in listing.tools}

    assert set(by_name) == {"roundtable_ask", "roundtable_providers", "roundtable_doctor"}
    assert all(tool.output_schema for tool in by_name.values())


def test_cli_and_mcp_share_the_same_structured_run_contract(capsys):
    service = FakeService()
    assert main(
        ["ask", "Question", "--heads", "codex", "--format", "json"],
        service=service,
    ) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    mcp_result = asyncio.run(
        call_tool(service, "roundtable_ask", {"question": "Question", "heads": ["codex"]})
    )
    mcp_payload = mcp_result.structured_content

    comparable_fields = (
        "schema_version",
        "mode",
        "question_hash",
        "requested_heads",
        "eligible_heads",
        "successful_heads",
        "failed_heads",
        "provider_metadata",
        "responses",
        "errors",
        "chair",
    )
    assert {key: cli_payload[key] for key in comparable_fields} == {
        key: mcp_payload[key] for key in comparable_fields
    }
