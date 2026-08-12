from __future__ import annotations

import json
from pathlib import Path

import pytest

from facode_roundtable.config import Config, ConfigError, PROVIDERS, load_config, save_config
from facode_roundtable.catalog import PROVIDER_NAMES, PROVIDER_SPECS
from facode_roundtable.models import ExitCode, ProviderError, ProviderResponse, RunResult
from facode_roundtable.providers import unsupported_providers
from facode_roundtable.runner import sanitize_environment


def test_config_round_trips_without_credentials(tmp_path):
    path = tmp_path / "roundtable" / "config.json"
    config = Config(default_heads=["codex", "claude"], chair="auto")

    save_config(config, path)

    assert load_config(path) == config
    assert json.loads(path.read_text(encoding="utf-8"))["retention"] == "ephemeral"


def test_config_has_single_source_defaults_for_codex_and_claude():
    config = Config()

    assert config.providers["codex"].model == "gpt-5.6-sol"
    assert config.providers["codex"].effort == "high"
    assert config.providers["claude"].model == "claude-opus-5"
    assert config.providers["claude"].effort == "high"
    assert config.providers["grok"].model is None
    assert config.providers["grok"].effort is None
    assert config.schema_version == 3
    assert config.update_channel == "beta"


def test_partial_provider_config_inherits_defaults_and_allows_explicit_null():
    inherited = Config.from_dict({"providers": {"codex": {"enabled": False}}})
    inherited.providers["codex"].enabled = True
    explicit_cli_default = Config.from_dict(
        {"providers": {"codex": {"model": None, "effort": None}}}
    )

    assert inherited.providers["codex"].model == "gpt-5.6-sol"
    assert inherited.providers["codex"].effort == "high"
    assert explicit_cli_default.providers["codex"].model is None
    assert explicit_cli_default.providers["codex"].effort is None


def test_v080_config_migrates_automatic_nulls_to_v081_defaults():
    legacy = {
        "schema_version": 1,
        "default_heads": "available",
        "chair": "auto",
        "concurrency": 5,
        "timeout_seconds": 300,
        "research_timeout_seconds": 600,
        "retention": "ephemeral",
        "providers": {
            name: {"enabled": True, "model": None}
            for name in ("codex", "claude", "grok", "gemini", "minimax")
        },
    }

    migrated = Config.from_dict(legacy)

    assert migrated.schema_version == 3
    assert migrated.providers["codex"].model == "gpt-5.6-sol"
    assert migrated.providers["codex"].effort == "high"
    assert migrated.providers["claude"].model == "claude-opus-5"
    assert migrated.providers["claude"].effort == "high"


def test_v080_config_migration_preserves_custom_models():
    migrated = Config.from_dict(
        {
            "schema_version": 1,
            "providers": {
                "codex": {"enabled": True, "model": "custom-codex"},
                "claude": {"enabled": True, "model": "custom-claude"},
            },
        }
    )

    assert migrated.schema_version == 3
    assert migrated.providers["codex"].model == "custom-codex"
    assert migrated.providers["claude"].model == "custom-claude"


def test_v081_config_migrates_only_exact_old_default_pairs():
    migrated = Config.from_dict(
        {
            "schema_version": 2,
            "providers": {
                "codex": {"model": "gpt-5.6-sol", "effort": "xhigh"},
                "claude": {"model": "custom-claude", "effort": "xhigh"},
            },
        }
    )

    assert migrated.schema_version == 3
    assert migrated.providers["codex"].effort == "high"
    assert migrated.providers["claude"].effort == "xhigh"


def test_update_channel_is_strict_and_round_trips(tmp_path):
    path = tmp_path / "config.json"
    config = Config(update_channel="stable")

    save_config(config, path)

    assert load_config(path).update_channel == "stable"
    with pytest.raises(ConfigError, match="update_channel"):
        Config.from_dict({"update_channel": "nightly"})


@pytest.mark.parametrize("channel", [None, True, 1, ["beta"], {"value": "beta"}])
def test_update_channel_rejects_non_strings_with_typed_error(channel):
    with pytest.raises(ConfigError, match="update_channel"):
        Config.from_dict({"update_channel": channel})


@pytest.mark.parametrize("schema_version", [True, 0, 4, "3"])
def test_config_rejects_invalid_schema_versions(schema_version):
    with pytest.raises(ConfigError, match="schema_version"):
        Config.from_dict({"schema_version": schema_version})


@pytest.mark.parametrize("effort", ["minimal", "max", "xhigh&whoami", 1])
def test_config_rejects_unsupported_effort(effort):
    with pytest.raises(ConfigError, match="provider effort"):
        Config.from_dict({"providers": {"codex": {"effort": effort}}})


def test_config_rejects_effort_for_provider_without_cli_support():
    with pytest.raises(ConfigError, match="effort is unsupported for grok"):
        Config.from_dict({"providers": {"grok": {"effort": "high"}}})


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


def test_config_rejects_disabled_defaults_unsafe_models_and_unbounded_timeouts():
    with pytest.raises(ConfigError, match="default head is disabled"):
        Config.from_dict({
            "default_heads": ["codex"],
            "providers": {"codex": {"enabled": False}},
        })
    with pytest.raises(ConfigError, match="safe model identifier"):
        Config.from_dict({"providers": {"minimax": {"model": "model&whoami"}}})
    with pytest.raises(ConfigError, match="between 1 and 3600"):
        Config.from_dict({"timeout_seconds": 999999})


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
    assert PROVIDERS is PROVIDER_NAMES
    assert unsupported_providers() == {"glm": "no_official_login_only_headless_cli"}


def test_provider_specs_are_the_single_source_for_defaults_and_capabilities():
    assert {
        name: {
            "executable": spec.executable,
            "model": spec.default_model,
            "effort": spec.default_effort,
            "capabilities": spec.capabilities(),
        }
        for name, spec in PROVIDER_SPECS.items()
    } == {
        "codex": {
            "executable": "codex",
            "model": "gpt-5.6-sol",
            "effort": "high",
            "capabilities": {
                "auth": "chatgpt",
                "model_discovery": "official-cli",
                "effort": True,
                "research": False,
            },
        },
        "claude": {
            "executable": "claude",
            "model": "claude-opus-5",
            "effort": "high",
            "capabilities": {
                "auth": "first_party",
                "model_discovery": "unsupported-by-cli",
                "effort": True,
                "research": True,
            },
        },
        "grok": {
            "executable": "grok",
            "model": None,
            "effort": None,
            "capabilities": {
                "auth": "oauth",
                "model_discovery": "official-cli",
                "effort": False,
                "research": True,
            },
        },
        "gemini": {
            "executable": "agy",
            "model": None,
            "effort": None,
            "capabilities": {
                "auth": "google_sign_in",
                "model_discovery": "official-cli",
                "effort": False,
                "research": False,
            },
        },
        "minimax": {
            "executable": "mmx",
            "model": None,
            "effort": None,
            "capabilities": {
                "auth": "oauth",
                "model_discovery": "unsupported-by-cli",
                "effort": False,
                "research": False,
            },
        },
    }
    config = Config()
    assert {
        name: (provider.model, provider.effort)
        for name, provider in config.providers.items()
    } == {
        name: (spec.default_model, spec.default_effort)
        for name, spec in PROVIDER_SPECS.items()
    }
    with pytest.raises(TypeError):
        PROVIDER_SPECS["codex"] = PROVIDER_SPECS["claude"]


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
