from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
from typing import Any


PROVIDERS = ("codex", "claude", "grok", "gemini", "minimax")
_CONFIG_FIELDS = {
    "schema_version",
    "default_heads",
    "chair",
    "concurrency",
    "timeout_seconds",
    "research_timeout_seconds",
    "retention",
    "providers",
}
_PROVIDER_FIELDS = {"enabled", "model"}


class ConfigError(ValueError):
    pass


@dataclass(slots=True)
class ProviderConfig:
    enabled: bool = True
    model: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ProviderConfig":
        unknown = set(value) - _PROVIDER_FIELDS
        if unknown:
            raise ConfigError(f"unknown provider configuration field: {sorted(unknown)[0]}")
        enabled = value.get("enabled", True)
        model = value.get("model")
        if not isinstance(enabled, bool):
            raise ConfigError("provider enabled must be boolean")
        if model is not None and (not isinstance(model, str) or not model.strip()):
            raise ConfigError("provider model must be a non-empty string or null")
        return cls(enabled=enabled, model=model)

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "model": self.model}


@dataclass(slots=True)
class Config:
    schema_version: int = 1
    default_heads: str | list[str] = "available"
    chair: str = "auto"
    concurrency: int = 5
    timeout_seconds: int = 300
    research_timeout_seconds: int = 600
    retention: str = "ephemeral"
    providers: dict[str, ProviderConfig] = field(
        default_factory=lambda: {name: ProviderConfig() for name in PROVIDERS}
    )

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ConfigError("schema_version must be 1")
        if self.default_heads != "available":
            if not isinstance(self.default_heads, list) or not self.default_heads:
                raise ConfigError("default_heads must be 'available' or a non-empty list")
            _validate_heads(self.default_heads)
        if self.chair != "auto" and self.chair not in PROVIDERS:
            raise ConfigError(f"unknown chair: {self.chair}")
        if not 1 <= self.concurrency <= len(PROVIDERS):
            raise ConfigError("concurrency must be between 1 and 5")
        if self.timeout_seconds <= 0 or self.research_timeout_seconds <= 0:
            raise ConfigError("timeouts must be positive")
        if self.retention != "ephemeral":
            raise ConfigError("retention must be ephemeral")
        unknown = set(self.providers) - set(PROVIDERS)
        if unknown:
            raise ConfigError(f"unknown provider: {sorted(unknown)[0]}")
        self.providers = {
            name: self.providers.get(name, ProviderConfig()) for name in PROVIDERS
        }

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
        providers = {
            name: ProviderConfig.from_dict(raw_providers.get(name, {})) for name in PROVIDERS
        }
        kwargs = {key: val for key, val in value.items() if key != "providers"}
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
