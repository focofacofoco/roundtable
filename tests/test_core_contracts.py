from __future__ import annotations

import json
from pathlib import Path

import pytest

from facode_roundtable.config import Config, ConfigError, load_config, save_config
from facode_roundtable.models import ExitCode, ProviderError, ProviderResponse, RunResult
from facode_roundtable.providers import PROVIDER_NAMES, unsupported_providers
from facode_roundtable.runner import sanitize_environment


def test_config_round_trips_without_credentials(tmp_path):
    path = tmp_path / "roundtable" / "config.json"
    config = Config(default_heads=["codex", "claude"], chair="auto")

    save_config(config, path)

    assert load_config(path) == config
    assert json.loads(path.read_text(encoding="utf-8"))["retention"] == "ephemeral"


@pytest.mark.parametrize("field", ["api_key", "access_token", "password", "secret"])
def test_config_rejects_credential_fields(field):
    payload = Config().to_dict()
    payload[field] = "forbidden"

    with pytest.raises(ConfigError, match="unknown configuration field"):
        Config.from_dict(payload)


def test_config_rejects_unknown_provider_and_unknown_provider_field():
    with pytest.raises(ConfigError, match="unknown provider"):
        Config.from_dict({"schema_version": 1, "providers": {"glm": {"enabled": True}}})

    with pytest.raises(ConfigError, match="unknown provider configuration field"):
        Config.from_dict(
            {"schema_version": 1, "providers": {"codex": {"enabled": True, "token": "x"}}}
        )


def test_run_result_has_stable_public_shape_and_exit_semantics():
    result = RunResult.create("Why?", requested_heads=["codex", "claude"])
    result.eligible_heads = ["codex", "claude"]
    result.responses.append(ProviderResponse(provider="codex", content="Because.", round=1))
    result.errors.append(ProviderError(provider="claude", code="timeout", message="timed out", round=1))
    result.finish()

    payload = result.to_dict()

    assert payload["schema_version"] == 1
    assert payload["question_hash"] != "Why?"
    assert payload["successful_heads"] == ["codex"]
    assert payload["failed_heads"] == ["claude"]
    assert result.exit_code == ExitCode.PARTIAL


def test_provider_catalog_is_exact_and_glm_is_explicitly_unsupported():
    assert PROVIDER_NAMES == ("codex", "claude", "grok", "gemini", "minimax")
    assert unsupported_providers() == {"glm": "no_official_login_only_headless_cli"}


def test_environment_sanitization_removes_credentials_without_touching_safe_values():
    clean = sanitize_environment(
        {
            "PATH": "safe-path",
            "LANG": "pt_BR.UTF-8",
            "OPENAI_API_KEY": "must-not-leak",
            "XAI_TOKEN": "must-not-leak",
            "CUSTOM_PASSWORD": "must-not-leak",
        }
    )

    assert clean == {"PATH": "safe-path", "LANG": "pt_BR.UTF-8"}


def test_runtime_source_has_no_direct_provider_transport_or_credential_store_access():
    source_root = Path(__file__).parents[1] / "src" / "facode_roundtable"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(source_root.rglob("*.py"))
    ).lower()

    forbidden = (
        "import requests",
        "import httpx",
        "urllib.request",
        "aiohttp",
        '"curl"',
        "auth.json",
        ".api_keys",
        "config.env",
        ".claude.json",
        "credentials.json",
    )
    assert not [item for item in forbidden if item in source]
