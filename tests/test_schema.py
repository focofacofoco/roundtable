from __future__ import annotations

import asyncio
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from facode_roundtable.providers.base import InvocationResult, ProviderStatus
from facode_roundtable.service import RoundtableService


class SchemaAdapter:
    name = "codex"

    async def status(self):
        return ProviderStatus(
            self.name,
            installed=True,
            eligible=True,
            auth_method="chatgpt",
            cli_version="test",
        )

    async def invoke(self, prompt, *, timeout, model=None, research=False):
        return InvocationResult("Answer", model=model, duration_ms=1)


def test_run_result_matches_published_json_schema():
    schema_path = Path(__file__).parents[1] / "docs" / "run-result.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    result = asyncio.run(
        RoundtableService({"codex": SchemaAdapter()}).ask(
            "Question", heads=["codex"]
        )
    )

    validator.validate(result.to_dict())


def test_published_json_schema_is_itself_valid():
    schema_path = Path(__file__).parents[1] / "docs" / "run-result.schema.json"
    Draft202012Validator.check_schema(
        json.loads(schema_path.read_text(encoding="utf-8"))
    )


def test_archived_v1_schema_remains_valid():
    schema_path = Path(__file__).parents[1] / "docs" / "run-result-v1.schema.json"
    Draft202012Validator.check_schema(
        json.loads(schema_path.read_text(encoding="utf-8"))
    )


def test_claim_ledger_matches_published_json_schema():
    class LedgerAdapter(SchemaAdapter):
        async def invoke(self, prompt, *, timeout, model=None, research=False):
            if "neutral chair" in prompt:
                return InvocationResult(
                    '{"verdict":"CONSENSUS","agreed":'
                    '["participant-1","participant-2"],"dissent":[],'
                    '"recommendation":"Ship.","claims":[{"id":"claim-1",'
                    '"statement":"The option is ready.","supporters":'
                    '["participant-1","participant-2"],"dissenters":[],'
                    '"evidence":[]}]}'
                )
            return InvocationResult("Answer", model=model, duration_ms=1)

    codex = LedgerAdapter()
    claude = LedgerAdapter()
    claude.name = "claude"
    result = asyncio.run(
        RoundtableService({"codex": codex, "claude": claude}).ask(
            "Question", heads=["codex", "claude"], rounds=2
        )
    )
    schema = json.loads(
        (Path(__file__).parents[1] / "docs" / "run-result.schema.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator(schema, format_checker=FormatChecker()).validate(
        result.to_dict()
    )
    assert result.schema_version == 2
