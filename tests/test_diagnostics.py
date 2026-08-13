from __future__ import annotations

import asyncio
import json

from facode_roundtable.config import Config
from facode_roundtable.diagnostics import build_diagnosis, runtime_evidence
from facode_roundtable.models import ProviderError, ProviderResponse, RunResult
from facode_roundtable.providers.base import ProviderStatus


class DiagnosticService:
    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    async def statuses(self):
        return [
            ProviderStatus("codex", True, True, "chatgpt", model="gpt-5.6-sol"),
            ProviderStatus("claude", True, True, "first_party", model="claude-opus-5"),
            ProviderStatus("grok", True, False, reason="login_required"),
            ProviderStatus("gemini", False, False, reason="cli_not_found"),
            ProviderStatus("minimax", True, False, reason="login_required"),
        ]

    async def ask(self, question, **kwargs):
        name = kwargs["heads"][0]
        self.calls.append((question, name, kwargs))
        await asyncio.sleep(0)
        result = RunResult.create(question, [name])
        result.eligible_heads = [name]
        answer = self.answers[name]
        if isinstance(answer, ProviderError):
            result.errors.append(answer)
        else:
            result.responses.append(
                ProviderResponse(
                    name,
                    answer,
                    1,
                    model=f"{name}-model",
                    duration_ms=12,
                )
            )
        result.finish()
        return result


def test_live_diagnosis_produces_content_free_qualification_receipt(tmp_path):
    service = DiagnosticService({"codex": "OK", "claude": "OK"})

    payload = asyncio.run(
        build_diagnosis(
            service,
            Config(),
            config_file=tmp_path / "config.json",
            config_valid=True,
            live=True,
        )
    )

    assert payload["schema_version"] == 1
    assert payload["qualification"] == {
        "performed": True,
        "qualified": True,
        "quorum_required": 2,
        "successful": ["codex", "claude"],
        "results": [
            {
                "provider": "codex",
                "status": "pass",
                "model": "codex-model",
                "duration_ms": 12,
            },
            {
                "provider": "claude",
                "status": "pass",
                "model": "claude-model",
                "duration_ms": 12,
            },
            {
                "provider": "grok",
                "status": "login_required",
                "model": None,
                "duration_ms": None,
            },
            {
                "provider": "gemini",
                "status": "cli_not_found",
                "model": None,
                "duration_ms": None,
            },
            {
                "provider": "minimax",
                "status": "login_required",
                "model": None,
                "duration_ms": None,
            },
        ],
    }
    assert payload["live"] == {"codex": "ok", "claude": "ok"}
    assert {call[1] for call in service.calls} == {"codex", "claude"}
    serialized = json.dumps(payload).lower()
    assert '"content"' not in serialized
    assert "reply with exactly" not in serialized
    assert str(tmp_path).lower() not in json.dumps(payload["evidence"]).lower()


def test_live_diagnosis_fails_closed_on_non_exact_answer(tmp_path):
    service = DiagnosticService({"codex": "OK", "claude": "Certainly: OK"})

    payload = asyncio.run(
        build_diagnosis(
            service,
            Config(),
            config_file=tmp_path / "config.json",
            config_valid=True,
            live=True,
        )
    )

    assert payload["qualification"]["qualified"] is False
    assert payload["qualification"]["successful"] == ["codex"]
    assert payload["qualification"]["results"][1]["status"] == "invalid_response"


def test_live_diagnosis_isolates_probe_exception_without_leaking_message(tmp_path):
    class RaisingService(DiagnosticService):
        async def ask(self, question, **kwargs):
            if kwargs["heads"] == ["claude"]:
                raise RuntimeError("Bearer secret-token-value")
            return await super().ask(question, **kwargs)

    payload = asyncio.run(
        build_diagnosis(
            RaisingService({"codex": "OK"}),
            Config(),
            config_file=tmp_path / "config.json",
            config_valid=True,
            live=True,
        )
    )

    assert payload["qualification"]["qualified"] is False
    assert payload["qualification"]["results"][1]["status"] == "probe_failed"
    assert "secret-token-value" not in json.dumps(payload)


def test_live_probes_start_concurrently(tmp_path):
    class BarrierService(DiagnosticService):
        def __init__(self):
            super().__init__({"codex": "OK", "claude": "OK"})
            self.started = set()
            self.all_started = asyncio.Event()

        async def ask(self, question, **kwargs):
            self.started.add(kwargs["heads"][0])
            if len(self.started) == 2:
                self.all_started.set()
            await asyncio.wait_for(self.all_started.wait(), timeout=0.2)
            return await super().ask(question, **kwargs)

    service = BarrierService()

    payload = asyncio.run(
        build_diagnosis(
            service,
            Config(),
            config_file=tmp_path / "config.json",
            config_valid=True,
            live=True,
        )
    )

    assert service.started == {"codex", "claude"}
    assert payload["qualification"]["qualified"] is True


def test_static_diagnosis_does_not_invoke_models(tmp_path):
    service = DiagnosticService({})

    payload = asyncio.run(
        build_diagnosis(
            service,
            Config(update_channel="stable"),
            config_file=tmp_path / "config.json",
            config_valid=True,
            live=False,
        )
    )

    assert service.calls == []
    assert payload["qualification"] == {
        "performed": False,
        "qualified": None,
        "quorum_required": 2,
        "successful": [],
        "results": [],
    }
    assert payload["evidence"]["update_channel"] == "stable"


def test_invalid_config_cannot_be_qualified_or_trigger_inference(tmp_path):
    service = DiagnosticService({"codex": "OK", "claude": "OK"})

    payload = asyncio.run(
        build_diagnosis(
            service,
            Config(),
            config_file=tmp_path / "invalid.json",
            config_valid=False,
            live=True,
        )
    )

    assert service.calls == []
    assert payload["qualification"]["performed"] is False
    assert payload["qualification"]["qualified"] is False


def test_runtime_evidence_is_path_free_and_fingerprint_is_deterministic():
    first = runtime_evidence(Config())
    second = runtime_evidence(Config())

    assert first["runtime_fingerprint"] == second["runtime_fingerprint"]
    assert len(first["runtime_fingerprint"]) == 64
    assert first["roundtable_version"] == "0.14.0"
    assert set(first) == {
        "timestamp",
        "roundtable_version",
        "python",
        "platform",
        "update_channel",
        "runtime_fingerprint",
    }
