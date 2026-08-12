from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    executable: str
    auth: str
    model_discovery: str
    model_command: tuple[str, ...] | None
    login: tuple[str, ...] | None
    logout: tuple[str, ...] | None
    default_model: str | None = None
    default_effort: str | None = None
    supports_effort: bool = False
    research: bool = False

    def capabilities(self) -> dict[str, str | bool]:
        return {
            "auth": self.auth,
            "model_discovery": self.model_discovery,
            "effort": self.supports_effort,
            "research": self.research,
        }


_SPECS = (
    ProviderSpec(
        "codex", "codex", "chatgpt", "official-cli", ("debug", "models"),
        ("login",), ("logout",), "gpt-5.6-sol", "high", True, False,
    ),
    ProviderSpec(
        "claude", "claude", "first_party", "unsupported-by-cli", None,
        ("auth", "login"), ("auth", "logout"), "claude-opus-5", "high", True, True,
    ),
    ProviderSpec(
        "grok", "grok", "oauth", "official-cli", ("models",),
        ("login", "--device-auth"), ("logout",), research=True,
    ),
    ProviderSpec(
        "gemini", "agy", "google_sign_in", "official-cli", ("models",),
        (), None,
    ),
    ProviderSpec(
        "minimax", "mmx", "oauth", "unsupported-by-cli", None,
        ("auth", "login", "--recommend", "--region=global"),
        ("auth", "logout"),
    ),
)

PROVIDER_SPECS = MappingProxyType({spec.name: spec for spec in _SPECS})
PROVIDER_NAMES = tuple(PROVIDER_SPECS)
_UNSUPPORTED_PROVIDERS = MappingProxyType(
    {"glm": "no_official_login_only_headless_cli"}
)


def capabilities_payload() -> dict[str, dict[str, str | bool]]:
    return {name: spec.capabilities() for name, spec in PROVIDER_SPECS.items()}


def unsupported_providers() -> dict[str, str]:
    return dict(_UNSUPPORTED_PROVIDERS)
