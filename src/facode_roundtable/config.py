from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from typing import Any

from facode_roundtable.catalog import PROVIDER_NAMES, PROVIDER_SPECS

PROVIDERS = PROVIDER_NAMES
_CONFIG_FIELDS = {
    "schema_version",
    "default_heads",
    "chair",
    "concurrency",
    "timeout_seconds",
    "research_timeout_seconds",
    "retention",
    "update_channel",
    "providers",
}
_PROVIDER_FIELDS = {"enabled", "model", "effort"}
_MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}\Z")
_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


class ConfigError(ValueError):
    pass


@dataclass(slots=True)
class ProviderConfig:
    enabled: bool = True
    model: str | None = None
    effort: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProviderConfig":
        if not isinstance(value, dict):
            raise ConfigError("provider configuration must be an object")
        unknown = set(value) - _PROVIDER_FIELDS
        if unknown:
            raise ConfigError(f"unknown provider configuration field: {sorted(unknown)[0]}")
        enabled = value.get("enabled", True)
        model = value.get("model")
        effort = value.get("effort")
        if not isinstance(enabled, bool):
            raise ConfigError("provider enabled must be boolean")
        if model is not None and (
            not isinstance(model, str) or not _MODEL_ID.fullmatch(model)
        ):
            raise ConfigError("provider model must be a safe model identifier or null")
        if effort is not None and (
            not isinstance(effort, str) or effort not in _EFFORTS
        ):
            raise ConfigError(
                "provider effort must be low, medium, high, xhigh, or null"
            )
        return cls(enabled=enabled, model=model, effort=effort)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "model": self.model,
            "effort": self.effort,
        }


def default_provider_config(name: str) -> ProviderConfig:
    spec = PROVIDER_SPECS[name]
    return ProviderConfig(model=spec.default_model, effort=spec.default_effort)


def _default_providers() -> dict[str, ProviderConfig]:
    return {name: default_provider_config(name) for name in PROVIDERS}


@dataclass(slots=True)
class Config:
    schema_version: int = 3
    default_heads: str | list[str] = "available"
    chair: str = "auto"
    concurrency: int = 5
    timeout_seconds: int = 300
    research_timeout_seconds: int = 600
    retention: str = "ephemeral"
    update_channel: str = "beta"
    providers: dict[str, ProviderConfig] = field(default_factory=_default_providers)

    def __post_init__(self) -> None:
        if self.schema_version != 3:
            raise ConfigError("schema_version must be 3")
        if self.default_heads != "available":
            if not isinstance(self.default_heads, list) or not self.default_heads:
                raise ConfigError("default_heads must be 'available' or a non-empty list")
            _validate_heads(self.default_heads)
        if self.chair != "auto" and self.chair not in PROVIDERS:
            raise ConfigError(f"unknown chair: {self.chair}")
        if (
            not isinstance(self.concurrency, int)
            or isinstance(self.concurrency, bool)
            or not 1 <= self.concurrency <= len(PROVIDERS)
        ):
            raise ConfigError("concurrency must be between 1 and 5")
        if any(
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 < value <= 3600
            for value in (self.timeout_seconds, self.research_timeout_seconds)
        ):
            raise ConfigError("timeouts must be integers between 1 and 3600")
        if self.retention != "ephemeral":
            raise ConfigError("retention must be ephemeral")
        if not isinstance(self.update_channel, str) or self.update_channel not in {"beta", "stable"}:
            raise ConfigError("update_channel must be beta or stable")
        unknown = set(self.providers) - set(PROVIDERS)
        if unknown:
            raise ConfigError(f"unknown provider: {sorted(unknown)[0]}")
        self.providers = {
            name: self.providers.get(name, default_provider_config(name))
            for name in PROVIDERS
        }
        for name, provider in self.providers.items():
            if (
                provider.effort is not None
                and not PROVIDER_SPECS[name].supports_effort
            ):
                raise ConfigError(f"provider effort is unsupported for {name}")
        if self.default_heads != "available":
            disabled = [name for name in self.default_heads if not self.providers[name].enabled]
            if disabled:
                raise ConfigError(f"default head is disabled: {disabled[0]}")
        if self.chair != "auto" and not self.providers[self.chair].enabled:
            raise ConfigError(f"chair is disabled: {self.chair}")

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Config":
        if not isinstance(value, dict):
            raise ConfigError("configuration must be an object")
        unknown = set(value) - _CONFIG_FIELDS
        if unknown:
            raise ConfigError(f"unknown configuration field: {sorted(unknown)[0]}")
        raw_providers = value.get("providers", {})
        if not isinstance(raw_providers, dict):
            raise ConfigError("providers must be an object")
        unknown_providers = set(raw_providers) - set(PROVIDERS)
        if unknown_providers:
            raise ConfigError(f"unknown provider: {sorted(unknown_providers)[0]}")
        schema_version = value.get("schema_version")
        if schema_version is None:
            schema_version = (
                2
                if any(
                    isinstance(provider, dict) and "effort" in provider
                    for provider in raw_providers.values()
                )
                else 1
            )
        if (
            not isinstance(schema_version, int)
            or isinstance(schema_version, bool)
            or schema_version not in {1, 2, 3}
        ):
            raise ConfigError("schema_version must be 1, 2, or 3")
        providers = {}
        for name in PROVIDERS:
            values = default_provider_config(name).to_dict()
            raw_provider = raw_providers.get(name, {})
            if not isinstance(raw_provider, dict):
                raise ConfigError("provider configuration must be an object")
            raw_provider = dict(raw_provider)
            if (
                schema_version == 1
                and PROVIDER_SPECS[name].default_model is not None
                and raw_provider.get("model") is None
            ):
                raw_provider.pop("model", None)
            if (
                schema_version == 2
                and raw_provider.get("model") == PROVIDER_SPECS[name].default_model
                and raw_provider.get("effort") == "xhigh"
            ):
                raw_provider["effort"] = "high"
            values.update(raw_provider)
            providers[name] = ProviderConfig.from_dict(values)
        kwargs = {key: val for key, val in value.items() if key != "providers"}
        kwargs["schema_version"] = 3
        return cls(providers=providers, **kwargs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "default_heads": self.default_heads,
            "chair": self.chair,
            "concurrency": self.concurrency,
            "timeout_seconds": self.timeout_seconds,
            "research_timeout_seconds": self.research_timeout_seconds,
            "retention": self.retention,
            "update_channel": self.update_channel,
            "providers": {name: config.to_dict() for name, config in self.providers.items()},
        }


def _validate_heads(heads: list[str]) -> None:
    unknown = set(heads) - set(PROVIDERS)
    if unknown:
        raise ConfigError(f"unknown provider: {sorted(unknown)[0]}")
    if len(set(heads)) != len(heads):
        raise ConfigError("default_heads must not contain duplicates")


def config_path() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    return (Path(root) if root else Path.home() / ".config") / "roundtable" / "config.json"


def load_config(path: Path | None = None) -> Config:
    target = path or config_path()
    if not target.exists():
        return Config()
    try:
        return Config.from_dict(json.loads(target.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid configuration: {exc}") from exc


def save_config(config: Config, path: Path | None = None) -> Path:
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(target)
    return target
