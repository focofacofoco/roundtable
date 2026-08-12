from __future__ import annotations

import asyncio
import json

from facode_roundtable.providers.base import CommandResult, ProviderStatus
from facode_roundtable.providers.claude import ClaudeAdapter
from facode_roundtable.providers.codex import CodexAdapter
from facode_roundtable.providers.gemini import GeminiAdapter
from facode_roundtable.providers.grok import GrokAdapter
from facode_roundtable.providers.minimax import MiniMaxAdapter


class RecordingRunner:
    def __init__(self, results: list[CommandResult]):
        self.results = list(results)
        self.calls: list[tuple[list[str], str | None]] = []

    async def run(self, argv, *, input_text=None, timeout, environment=None):
        self.calls.append((list(argv), input_text))
        return self.results.pop(0)


def result(stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(tuple(), returncode, stdout, stderr, 1, False)


def test_codex_requires_chatgpt_login_and_never_accepts_api_key_status():
    chatgpt = CodexAdapter(RecordingRunner([result("Logged in using ChatGPT")]))
    api_key = CodexAdapter(RecordingRunner([result("Logged in using an API key")]))

    accepted = asyncio.run(chatgpt.status())
    rejected = asyncio.run(api_key.status())

    assert accepted == ProviderStatus(
        name="codex", installed=True, eligible=True, auth_method="chatgpt", research=False
    )
    assert rejected.eligible is False
    assert rejected.reason == "api_key_auth_forbidden"


def test_codex_invocation_is_ephemeral_isolated_and_parses_json_events():
    output = json.dumps(
        {"type": "item.completed", "item": {"type": "agent_message", "text": "Codex answer"}}
    )
    runner = RecordingRunner([result(output)])

    response = asyncio.run(CodexAdapter(runner).invoke("Question", timeout=20))
    argv, prompt = runner.calls[0]

    assert response.content == "Codex answer"
    assert prompt == "Question"
    assert argv == [
        "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "read-only", "--json", "-",
    ]


def test_claude_requires_first_party_login_and_invocation_disables_local_tools():
    status_runner = RecordingRunner(
        [result(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}))]
    )
    status = asyncio.run(ClaudeAdapter(status_runner).status())
    invoke_runner = RecordingRunner([result(json.dumps({"result": "Claude answer", "is_error": False}))])

    response = asyncio.run(ClaudeAdapter(invoke_runner).invoke("Question", timeout=20))
    argv, prompt = invoke_runner.calls[0]

    assert status.eligible is True
    assert status.auth_method == "first_party"
    assert response.content == "Claude answer"
    assert prompt == "Question"
    assert argv == [
        "claude", "--print", "--output-format", "json", "--no-session-persistence",
        "--tools", "", "--permission-mode", "dontAsk", "--safe-mode",
    ]


def test_grok_requires_oauth_and_disable_api_key_policy():
    valid = result(json.dumps({
        "authentication": {"authenticated": True, "method": "oauth"},
        "grok_com_config": {"disable_api_key_auth": True},
    }))
    invalid = result(json.dumps({
        "authentication": {"authenticated": True, "method": "api_key"},
        "grok_com_config": {"disable_api_key_auth": False},
    }))

    accepted = asyncio.run(GrokAdapter(RecordingRunner([valid])).status())
    rejected = asyncio.run(GrokAdapter(RecordingRunner([invalid])).status())

    assert accepted.eligible is True
    assert accepted.auth_method == "oauth"
    assert accepted.research is True
    assert rejected.reason == "api_key_auth_forbidden"


def test_grok_research_invocation_allows_only_web_tools():
    runner = RecordingRunner([result(json.dumps({"text": "Grok answer"}))])

    response = asyncio.run(GrokAdapter(runner).invoke("Question", timeout=20, research=True))
    argv, prompt = runner.calls[0]

    assert response.content == "Grok answer"
    assert prompt == "Question"
    assert argv == [
        "grok", "--prompt-file", "-", "--output-format", "json", "--no-auto-update",
        "--tools", "web_search,web_fetch", "--deny", "MCPTool(*)", "--permission-mode", "dontAsk",
    ]


def test_gemini_uses_models_as_keyring_login_probe_and_scoped_policy():
    status = asyncio.run(GeminiAdapter(RecordingRunner([result("gemini-pro Gemini Pro")])).status())
    runner = RecordingRunner([result(json.dumps({"status": "SUCCESS", "response": "Gemini answer"}))])

    response = asyncio.run(GeminiAdapter(runner).invoke("Question", timeout=20, research=True))
    argv, prompt = runner.calls[0]

    assert status.eligible is True
    assert status.auth_method == "google_sign_in"
    assert response.content == "Gemini answer"
    assert prompt is None
    assert argv[:5] == ["agy", "-p", "Question", "--output-format", "json"]
    assert "--policy" in argv


def test_minimax_rejects_api_key_auth_and_uses_oauth_chat():
    oauth = asyncio.run(MiniMaxAdapter(RecordingRunner([result("Authenticated via OAuth")])).status())
    key = asyncio.run(MiniMaxAdapter(RecordingRunner([result("Authenticated via API key")])).status())
    runner = RecordingRunner([result(json.dumps({"text": "MiniMax answer"}))])

    response = asyncio.run(MiniMaxAdapter(runner).invoke("Question", timeout=20))
    argv, prompt = runner.calls[0]

    assert oauth.eligible is True
    assert oauth.auth_method == "oauth"
    assert key.reason == "api_key_auth_forbidden"
    assert response.content == "MiniMax answer"
    assert argv == ["mmx", "text", "chat", "--messages-file", "-", "--output", "json"]
    assert json.loads(prompt) == [{"role": "user", "content": "Question"}]
