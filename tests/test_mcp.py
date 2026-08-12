from __future__ import annotations

import asyncio
import json

from mcp import Client

from facode_roundtable.mcp_server import create_server
from facode_roundtable.cli import main
from facode_roundtable.config import Config, ProviderConfig
from facode_roundtable.models import ProviderError, ProviderResponse, RunResult
from facode_roundtable.providers.base import ProviderStatus


class FakeStatusAdapter:
    async def status(self):
        return ProviderStatus("codex", True, True, auth_method="chatgpt")


class FakeService:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.adapters = {"codex": FakeStatusAdapter()}
        self.calls: list[dict] = []

    async def ask(self, question, **kwargs):
        self.calls.append(kwargs)
        result = RunResult.create(question, kwargs["heads"])
        result.eligible_heads = list(kwargs["heads"])
        if self.fail and kwargs["heads"]:
            result.errors.append(ProviderError(kwargs["heads"][0], "provider_failed", "failed", 1))
        elif kwargs["heads"]:
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
    assert set(by_name["roundtable_ask"].input_schema["properties"]) == {
        "question", "heads", "rounds", "research", "chair", "timeout", "models"
    }
    assert by_name["roundtable_providers"].input_schema["properties"] == {}
    assert by_name["roundtable_doctor"].input_schema["properties"] == {}


def test_mcp_provider_and_doctor_contracts_share_catalog_capabilities(tmp_path):
    service = FakeService()

    providers = asyncio.run(call_tool(service, "roundtable_providers", {}))

    async def doctor():
        async with Client(
            create_server(service=service, config_file=tmp_path / "config.json")
        ) as client:
            return await client.call_tool("roundtable_doctor", {})

    diagnosis = asyncio.run(doctor())

    assert providers.structured_content["schema_version"] == 1
    assert diagnosis.structured_content["schema_version"] == 1
    assert providers.structured_content["capabilities"] == (
        diagnosis.structured_content["capabilities"]
    )
    assert providers.structured_content["capabilities"]["minimax"] == {
        "auth": "oauth",
        "model_discovery": "unsupported-by-cli",
        "effort": False,
        "research": False,
    }


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


def test_mcp_preserves_explicit_empty_heads_instead_of_expanding_to_all():
    service = FakeService()

    result = asyncio.run(
        call_tool(service, "roundtable_ask", {"question": "Question", "heads": []})
    )

    assert service.calls[0]["heads"] == []
    assert result.structured_content["requested_heads"] == []


def test_mcp_uses_same_config_defaults_as_cli():
    service = FakeService()
    config = Config(
        default_heads=["codex"],
        chair="codex",
        timeout_seconds=19,
        providers={"codex": ProviderConfig(model="configured-model")},
    )

    async def invoke():
        async with Client(create_server(service=service, config=config)) as client:
            return await client.call_tool("roundtable_ask", {"question": "Question"})

    asyncio.run(invoke())

    assert service.calls[0]["heads"] == ["codex"]
    assert service.calls[0]["chair"] == "codex"
    assert service.calls[0]["timeout"] == 19
    assert service.calls[0]["models"] == {"codex": "configured-model"}
