PROVIDER_NAMES = ("codex", "claude", "grok", "gemini", "minimax")


def unsupported_providers() -> dict[str, str]:
    return {"glm": "no_official_login_only_headless_cli"}
