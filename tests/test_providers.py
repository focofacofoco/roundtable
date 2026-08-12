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
        self.environments: list[dict[str, str] | None] = []

    async def run(self, argv, *, input_text=None, timeout, environment=None):
        self.calls.append((list(argv), input_text))
        self.environments.append(environment)
        return self.results.pop(0)


def result(stdout: str = "", stderr: str = "", returncode: int = 0) -> CommandResult:
    return CommandResult(tuple(), returncode, stdout, stderr, 1, False)


def test_codex_requires_chatgpt_login_and_never_accepts_api_key_status():
    chatgpt = CodexAdapter(
        RecordingRunner([result("Logged in using ChatGPT"), result("codex-cli 1.2.3")])
    )
    api_key = CodexAdapter(
        RecordingRunner([result("Logged in using an API key"), result("codex-cli 1.2.3")])
    )

    accepted = asyncio.run(chatgpt.status())
    rejected = asyncio.run(api_key.status())

    assert accepted == ProviderStatus(
        name="codex",
        installed=True,
        eligible=True,
        auth_method="chatgpt",
        cli_version="codex-cli 1.2.3",
        research=False,
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
    assert argv[:9] == [
        "codex", "exec", "--ephemeral", "--ignore-user-config", "--ignore-rules",
        "--skip-git-repo-check", "--sandbox", "read-only", "--disable",
    ]
    disabled = {argv[index + 1] for index, value in enumerate(argv) if value == "--disable"}
    assert {"shell_tool", "code_mode_host", "apps", "plugins", "multi_agent", "view_image"} <= disabled
    assert ["--config", 'web_search="disabled"'] == argv[-4:-2]
    assert argv[-2:] == ["--json", "-"]


def test_claude_requires_first_party_login_and_invocation_disables_local_tools():
    status_runner = RecordingRunner(
        [
            result(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"})),
            result("2.1.0 (Claude Code)"),
        ]
    )
    status = asyncio.run(ClaudeAdapter(status_runner).status())
    invoke_runner = RecordingRunner([result(json.dumps({"result": "Claude answer", "is_error": False}))])

    response = asyncio.run(ClaudeAdapter(invoke_runner).invoke("Question", timeout=20))
    argv, prompt = invoke_runner.calls[0]

    assert status.eligible is True
    assert status.auth_method == "first_party"
    assert status.cli_version == "2.1.0 (Claude Code)"
    assert response.content == "Claude answer"
    assert prompt == "Question"
    assert argv == [
        "claude", "--print", "--output-format", "json", "--no-session-persistence",
        "--tools", "", "--permission-mode", "dontAsk", "--safe-mode",
    ]


def test_grok_requires_oauth_and_disable_api_key_policy():
    valid = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": True, "apiKeyAuthDisabled": True,
    }}))
    invalid = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": False, "apiKeyAuthDisabled": False,
    }}))
    accepted_runner = RecordingRunner(
        [valid, result("grok 1.0.3"), result("grok-build"), valid]
    )

    accepted = asyncio.run(GrokAdapter(accepted_runner).status())
    rejected = asyncio.run(
        GrokAdapter(RecordingRunner([invalid, result("grok 1.0.3")])).status()
    )

    assert accepted.eligible is True
    assert accepted.auth_method == "oauth"
    assert accepted.research is True
    assert accepted.cli_version == "grok 1.0.3"
    assert len(accepted_runner.calls) == 4
    assert accepted_runner.environments == [
        {"GROK_DISABLE_API_KEY_AUTH": "1"},
        None,
        {"GROK_DISABLE_API_KEY_AUTH": "1"},
        {"GROK_DISABLE_API_KEY_AUTH": "1"},
    ]
    assert rejected.reason == "api_key_auth_forbidden"


def test_grok_rechecks_api_key_lockdown_after_auth_probe():
    valid = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": True, "apiKeyAuthDisabled": True,
    }}))
    invalid = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": False, "apiKeyAuthDisabled": False,
    }}))
    adapter = GrokAdapter(
        RecordingRunner([valid, result("grok 1.0.3"), result("grok-build"), invalid])
    )

    status = asyncio.run(adapter.status())

    assert status.eligible is False
    assert status.reason == "api_key_auth_forbidden"


def test_grok_rejects_zero_exit_models_probe_when_output_says_unauthenticated():
    policy = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": True, "apiKeyAuthDisabled": True,
    }}))
    adapter = GrokAdapter(
        RecordingRunner(
            [policy, result("grok 1.0.3"), result("You are not authenticated.")]
        )
    )

    status = asyncio.run(adapter.status())

    assert status.eligible is False
    assert status.reason == "login_required"


def test_grok_research_invocation_allows_only_web_tools():
    policy = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": True, "apiKeyAuthDisabled": True,
    }}))
    runner = RecordingRunner([policy, result(json.dumps({"text": "Grok answer"}))])

    response = asyncio.run(GrokAdapter(runner).invoke("Question", timeout=20, research=True))
    argv, prompt = runner.calls[1]

    assert response.content == "Grok answer"
    assert prompt == "Question"
    assert argv == [
        "grok", "--prompt-file", "-", "--output-format", "json", "--no-auto-update",
        "--tools", "web_search,web_fetch", "--deny", "MCPTool(*)", "--permission-mode", "dontAsk",
    ]
    assert runner.environments == [
        {"GROK_DISABLE_API_KEY_AUTH": "1"},
        {"GROK_DISABLE_API_KEY_AUTH": "1"},
    ]


def test_grok_invocation_fails_closed_when_api_key_lockdown_is_not_observable():
    invalid = result(json.dumps({"loginPolicy": {
        "disableApiKeyAuth": False, "apiKeyAuthDisabled": False,
    }}))
    runner = RecordingRunner([invalid])

    try:
        asyncio.run(GrokAdapter(runner).invoke("Question", timeout=20))
    except Exception as error:
        assert getattr(error, "code", None) == "api_key_auth_forbidden"
    else:
        raise AssertionError("Grok invocation must fail closed")
    assert len(runner.calls) == 1


def test_grok_rejects_policy_json_from_failed_or_timed_out_inspection():
    payload = json.dumps({"loginPolicy": {
        "disableApiKeyAuth": True, "apiKeyAuthDisabled": True,
    }})
    for inspection in (
        CommandResult(tuple(), 1, payload, "failed", 1, False),
        CommandResult(tuple(), None, payload, "", 1, True),
    ):
        runner = RecordingRunner([inspection])
        try:
            asyncio.run(GrokAdapter(runner).invoke("Question", timeout=20))
        except Exception as error:
            assert getattr(error, "code", None) == "api_key_auth_forbidden"
        else:
            raise AssertionError("failed policy inspection must fail closed")


def test_grok_requires_both_lockdown_flags_in_the_same_policy_object():
    inspection = result(json.dumps({
        "configured": {"disableApiKeyAuth": True},
        "effective": {"apiKeyAuthDisabled": True},
    }))
    runner = RecordingRunner([inspection])

    try:
        asyncio.run(GrokAdapter(runner).invoke("Question", timeout=20))
    except Exception as error:
        assert getattr(error, "code", None) == "api_key_auth_forbidden"
    else:
        raise AssertionError("split policy evidence must fail closed")


def test_gemini_uses_models_as_keyring_login_probe_and_sandboxed_plan_mode():
    status = asyncio.run(
        GeminiAdapter(
            RecordingRunner([result("gemini-pro Gemini Pro"), result("agy 1.1.12")])
        ).status()
    )
    runner = RecordingRunner([result(json.dumps({"status": "SUCCESS", "response": "Gemini answer"}))])

    response = asyncio.run(GeminiAdapter(runner).invoke("Question", timeout=20))
    argv, prompt = runner.calls[0]

    assert status.eligible is True
    assert status.auth_method == "google_sign_in"
    assert status.cli_version == "agy 1.1.12"
    assert status.research is False
    assert response.content == "Gemini answer"
    assert prompt == "Question"
    assert argv == [
        "agy", "-p", "--output-format", "json", "--sandbox", "--mode", "plan",
        "--disable-slash-commands",
    ]


def test_minimax_rejects_api_key_auth_and_uses_oauth_chat():
    oauth = asyncio.run(
        MiniMaxAdapter(
            RecordingRunner([result("Authenticated via OAuth"), result("mmx 1.0.19")])
        ).status()
    )
    key = asyncio.run(
        MiniMaxAdapter(
            RecordingRunner([result("Authenticated via API key"), result("mmx 1.0.19")])
        ).status()
    )
    runner = RecordingRunner([result(json.dumps({"text": "MiniMax answer"}))])

    response = asyncio.run(MiniMaxAdapter(runner).invoke("Question", timeout=20))
    argv, prompt = runner.calls[0]

    assert oauth.eligible is True
    assert oauth.auth_method == "oauth"
    assert oauth.cli_version == "mmx 1.0.19"
    assert key.reason == "api_key_auth_forbidden"
    assert response.content == "MiniMax answer"
    assert argv == ["mmx", "text", "chat", "--messages-file", "-", "--output", "json"]
    assert json.loads(prompt) == [{"role": "user", "content": "Question"}]
