from __future__ import annotations

import json
import os

from facode_roundtable.cli import default_service, main, resolve_cli
from facode_roundtable.config import Config, ProviderConfig, load_config, save_config
from facode_roundtable.models import ProviderResponse, RunResult
from facode_roundtable.providers.base import ProviderStatus
from facode_roundtable.runner import CommandResult


class FakeService:
    def __init__(self):
        self.adapters = {"codex": FakeStatusAdapter("codex")}
        self.calls: list[tuple[str, dict]] = []

    async def ask(self, question, **kwargs):
        self.calls.append((question, kwargs))
        result = RunResult.create(question, kwargs["heads"])
        result.eligible_heads = list(kwargs["heads"])
        result.responses.append(ProviderResponse(kwargs["heads"][0], "CLI answer", 1))
        result.finish()
        return result


class FakeStatusAdapter:
    def __init__(self, name):
        self.name = name

    async def status(self):
        return ProviderStatus(self.name, True, True, auth_method="chatgpt")


class FakeHarness:
    def __init__(self, ok=True):
        self.ok = ok
        self.actions: list[str] = []

    def _report(self, action):
        self.actions.append(action)
        return {"action": action, "ok": self.ok, "components": {}}

    def status(self):
        return self._report("status")

    def install(self):
        return self._report("install")

    def remove(self):
        return self._report("remove")


class FakeCommandRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def run(self, argv, *, input_text=None, timeout, environment=None):
        self.calls.append((list(argv), input_text, timeout, environment))
        return self.results.pop(0)


def command_result(stdout="", stderr="", returncode=0, *, timed_out=False):
    return CommandResult(tuple(), returncode, stdout, stderr, 1, timed_out)


def test_version_contract(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out == "roundtable 0.9.0\n"


def test_lightweight_cli_import_does_not_load_mcp_sdk():
    import sys

    sys.modules.pop("mcp", None)
    sys.modules.pop("facode_roundtable.mcp_server", None)
    __import__("importlib").reload(__import__("facode_roundtable.cli").cli)

    assert "mcp" not in sys.modules
    assert "facode_roundtable.mcp_server" not in sys.modules


def test_default_service_exposes_exact_five_head_catalog():
    service = default_service()

    assert tuple(service.adapters) == ("codex", "claude", "grok", "gemini", "minimax")
    assert service.adapters["codex"].default_model == "gpt-5.6-sol"
    assert service.adapters["codex"].default_effort == "high"
    assert service.adapters["claude"].default_model == "claude-opus-5"
    assert service.adapters["claude"].default_effort == "high"


def test_cli_resolution_finds_official_user_install_before_shell_restart(tmp_path, monkeypatch):
    executable = "grok.exe" if os.name == "nt" else "grok"
    grok = tmp_path / ".grok" / "bin" / executable
    grok.parent.mkdir(parents=True)
    grok.touch()
    monkeypatch.setattr("facode_roundtable.executables.shutil.which", lambda _: None)

    assert __import__("pathlib").Path(resolve_cli("grok", home=tmp_path)).samefile(grok)


def test_cli_resolution_rejects_project_local_path_hijack(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    fake = project / "codex.exe"
    fake.touch()
    monkeypatch.setattr(
        "facode_roundtable.executables.shutil.which", lambda _name: str(fake)
    )

    resolved = resolve_cli(
        "codex", home=tmp_path / "home", local_app_data=tmp_path / "app"
    )

    assert "__facode_roundtable_cli_not_found__" in resolved


def test_ask_json_writes_only_json_to_stdout(capsys):
    code = main(
        ["ask", "Question", "--heads", "codex", "--format", "json"],
        service=FakeService(),
    )
    captured = capsys.readouterr()

    assert code == 0
    assert '"content": "CLI answer"' in captured.out
    assert captured.err == ""


def test_cli_maps_keyboard_interrupt_to_typed_exit(monkeypatch, capsys):
    def interrupt(coroutine):
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr("facode_roundtable.cli.asyncio.run", interrupt)

    code = main(["ask", "Question", "--heads", "codex"], service=FakeService())

    assert code == 130
    assert capsys.readouterr().err == "roundtable: interrupted\n"


def test_providers_json_reports_sanitized_login_status(capsys):
    code = main(["providers", "--json"], service=FakeService())
    output = capsys.readouterr().out

    assert code == 0
    assert '"auth_method": "chatgpt"' in output
    assert "email" not in output.lower()


def test_config_set_show_and_reset_are_strict_and_atomic(tmp_path, capsys):
    path = tmp_path / "config.json"

    assert main(["config", "set", "concurrency", "3"], config_file=path) == 0
    assert load_config(path).concurrency == 3
    assert main(["config", "show"], config_file=path) == 0
    assert '"concurrency": 3' in capsys.readouterr().out
    assert main(["config", "set", "api_key", "forbidden"], config_file=path) == 2
    assert load_config(path).concurrency == 3
    assert main(["config", "reset"], config_file=path) == 0
    assert load_config(path).concurrency == 5


def test_output_is_ephemeral_unless_explicitly_persisted(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["ask", "Question", "--heads", "codex"], service=FakeService()) == 0
    capsys.readouterr()
    assert list(tmp_path.iterdir()) == []

    target = tmp_path / "answer.md"
    assert main(
        ["ask", "Question", "--heads", "codex", "--out", str(target)],
        service=FakeService(),
    ) == 0
    assert target.read_text(encoding="utf-8").startswith("# Roundtable")
    assert not target.with_suffix(".tmp").exists()


def test_harness_cli_returns_machine_readable_idempotent_report(capsys):
    harness = FakeHarness()

    code = main(["harness", "status", "--json"], harness_manager=harness)
    payload = __import__("json").loads(capsys.readouterr().out)

    assert code == 0
    assert payload == {"action": "status", "ok": True, "components": {}}
    assert harness.actions == ["status"]


def test_cli_surface_is_frozen():
    import argparse
    from facode_roundtable.cli import _parser

    def manifest(parser):
        result = {"arguments": {}, "commands": {}}
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction):
                result["commands"] = {
                    name: manifest(child) for name, child in action.choices.items()
                }
            elif action.dest != "help":
                result["arguments"][action.dest] = {
                    "options": action.option_strings,
                    "nargs": action.nargs,
                    "required": action.required,
                }
        return result

    surface = manifest(_parser())

    assert tuple(surface["commands"]) == (
        "version", "update", "uninstall", "harness", "providers", "doctor",
        "auth", "models", "config", "mcp", "ask",
    )
    assert tuple(surface["commands"]["harness"]["commands"]) == (
        "status", "install", "remove",
    )
    assert tuple(surface["commands"]["auth"]["commands"]) == (
        "status", "login", "logout",
    )
    assert tuple(surface["commands"]["config"]["commands"]) == (
        "show", "path", "reset", "set",
    )
    assert tuple(surface["commands"]["mcp"]["commands"]) == ("serve",)
    assert tuple(surface["commands"]["ask"]["arguments"]) == (
        "question", "question_flag", "context", "heads", "rounds", "chair",
        "research", "timeout", "model", "format", "out", "save",
    )
    expected_arguments = {
        ("harness", "status"): {"json": ("--json",)},
        ("harness", "install"): {"json": ("--json",)},
        ("harness", "remove"): {"json": ("--json",)},
        ("providers",): {"json": ("--json",)},
        ("doctor",): {"live": ("--live",), "json": ("--json",)},
        ("auth", "status"): {"provider": ()},
        ("auth", "login"): {"provider": ()},
        ("auth", "logout"): {"provider": ()},
        ("models",): {"provider": ()},
        ("config", "set"): {"field": (), "value": ()},
        ("ask",): {
            "question": (), "question_flag": ("-q", "--question"),
            "context": ("-c", "--context"), "heads": ("--heads",),
            "rounds": ("--rounds",), "chair": ("--chair",),
            "research": ("--research",), "timeout": ("--timeout",),
            "model": ("--model",), "format": ("--format",),
            "out": ("--out",), "save": ("--save",),
        },
    }
    for path, expected in expected_arguments.items():
        node = surface
        for command in path:
            node = node["commands"][command]
        assert {
            name: tuple(details["options"])
            for name, details in node["arguments"].items()
        } == expected
    empty_argument_paths = (
        (), ("version",), ("update",), ("uninstall",), ("harness",),
        ("auth",), ("config",), ("config", "show"), ("config", "path"),
        ("config", "reset"), ("mcp",), ("mcp", "serve"),
    )
    for path in empty_argument_paths:
        node = surface
        for command in path:
            node = node["commands"][command]
        assert node["arguments"] == {}


def test_grok_login_uses_device_oauth_and_api_key_lockdown(monkeypatch):
    captured = {}

    def run(argv, **kwargs):
        captured["argv"] = argv
        captured["environment"] = kwargs["env"]
        return __import__("subprocess").CompletedProcess(argv, 0)

    monkeypatch.setattr("facode_roundtable.cli.subprocess.run", run)
    monkeypatch.setattr(
        "facode_roundtable.cli.resolve_cli", lambda name: f"resolved-{name}"
    )
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")

    assert main(["auth", "login", "grok"], service=FakeService()) == 0
    assert captured["argv"] == ["resolved-grok", "login", "--device-auth"]
    assert captured["environment"]["GROK_DISABLE_API_KEY_AUTH"] == "1"
    assert "OPENAI_API_KEY" not in captured["environment"]


def test_codex_model_discovery_uses_official_catalog_and_hides_internal_models(
    monkeypatch, capsys
):
    runner = FakeCommandRunner(
        [
            command_result(
                json.dumps(
                    {
                        "models": [
                            {"slug": "gpt-5.6-sol", "visibility": "list"},
                            {"slug": "gpt-5.6-sol-wm", "visibility": "hide"},
                            {"slug": "gpt-5.6-terra", "visibility": "list"},
                        ]
                    }
                )
            )
        ]
    )
    monkeypatch.setattr(
        "facode_roundtable.cli.resolve_cli", lambda name: f"resolved-{name}"
    )

    assert main(["models", "codex"], command_runner=runner) == 0

    assert runner.calls == [(["resolved-codex", "debug", "models"], None, 20, None)]
    assert capsys.readouterr().out == (
        "codex: default=gpt-5.6-sol effort=high discovery=official-cli\n"
        "  gpt-5.6-sol\n"
        "  gpt-5.6-terra\n"
    )


def test_claude_models_reports_effective_defaults_without_fabricated_catalog(capsys):
    runner = FakeCommandRunner([])

    assert main(["models", "claude"], command_runner=runner) == 0

    assert runner.calls == []
    assert capsys.readouterr().out == (
        "claude: default=claude-opus-5 effort=high "
        "discovery=unsupported-by-cli\n"
    )


def test_gemini_model_discovery_is_bounded_runner_command(monkeypatch, capsys):
    runner = FakeCommandRunner([command_result("gemini-2.5-pro\n")])
    monkeypatch.setattr(
        "facode_roundtable.cli.resolve_cli", lambda name: f"resolved-{name}"
    )

    assert main(["models", "gemini"], command_runner=runner) == 0

    assert runner.calls == [(["resolved-agy", "models"], None, 20, None)]
    assert capsys.readouterr().out == (
        "gemini: default=cli-default effort=cli-default discovery=official-cli\n"
        "  gemini-2.5-pro\n"
    )


def test_model_discovery_returns_typed_error_for_invalid_catalog(capsys):
    runner = FakeCommandRunner([command_result("not-json")])

    assert main(["models", "codex"], command_runner=runner) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "roundtable: codex model discovery failed: invalid_response\n"
    )


def test_model_discovery_maps_runner_failures_to_typed_errors(capsys):
    cases = (
        (command_result(returncode=None, timed_out=True), "timeout"),
        (command_result(returncode=127), "cli_not_found"),
        (command_result(returncode=1), "provider_failed"),
        (
            CommandResult(
                tuple(), 70, "", "too much", 1, False, "output_limit"
            ),
            "output_limit",
        ),
        (
            CommandResult(
                tuple(), 70, "", "cleanup failed", 1, False, "cleanup_failed"
            ),
            "cleanup_failed",
        ),
    )

    for result, expected in cases:
        assert main(
            ["models", "codex"], command_runner=FakeCommandRunner([result])
        ) == 3
        assert capsys.readouterr().err == (
            f"roundtable: codex model discovery failed: {expected}\n"
        )


def test_grok_model_discovery_fails_closed_on_zero_exit_login_message(capsys):
    runner = FakeCommandRunner(
        [
            command_result(
                "You are not authenticated.\n\n"
                "Default model: grok-4.5\n\n"
                "Available models:\n  * grok-4.5 (default)\n"
            )
        ]
    )

    assert main(["models", "grok"], command_runner=runner) == 3

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "roundtable: grok model discovery failed: login_required\n"
    )


def test_plain_model_catalog_filters_banners_and_terminal_controls(capsys):
    runner = FakeCommandRunner(
        [
            command_result(
                "Fetching available models...\n"
                "Available models:\n"
                "  * gemini-3.1-pro-high (default)\n"
                "  - gemini-3.6-flash-high\x9b\n"
            )
        ]
    )

    assert main(["models", "gemini"], command_runner=runner) == 0

    assert capsys.readouterr().out == (
        "gemini: default=cli-default effort=cli-default discovery=official-cli\n"
        "  gemini-3.1-pro-high\n"
        "  gemini-3.6-flash-high\n"
    )


def test_ask_uses_effective_config_defaults_and_cli_model_override(tmp_path, capsys):
    path = tmp_path / "config.json"
    config = Config(
        default_heads=["codex"],
        chair="codex",
        timeout_seconds=17,
        providers={
            "codex": ProviderConfig(model="configured-model"),
        },
    )
    save_config(config, path)
    service = FakeService()

    assert main(
        ["ask", "Question", "--model", "codex=cli-model"],
        service=service,
        config_file=path,
    ) == 0
    capsys.readouterr()

    assert service.calls[0][1]["heads"] == ["codex"]
    assert service.calls[0][1]["chair"] == "codex"
    assert service.calls[0][1]["timeout"] == 17
    assert service.calls[0][1]["models"] == {"codex": "cli-model"}


def test_disabled_provider_is_rejected_before_service_status(tmp_path, capsys):
    path = tmp_path / "config.json"
    save_config(
        Config(providers={"codex": ProviderConfig(enabled=False)}),
        path,
    )
    service = FakeService()

    assert main(
        ["ask", "Question", "--heads", "codex"],
        service=service,
        config_file=path,
    ) == 2

    assert service.calls == []
    assert "provider is disabled: codex" in capsys.readouterr().err


def test_context_file_is_size_bounded_before_reading_into_service(tmp_path, capsys):
    context = tmp_path / "large.txt"
    context.write_text("x" * (1024 * 1024 + 1), encoding="utf-8")
    service = FakeService()

    assert main(
        ["ask", "Question", "--heads", "codex", "--context", str(context)],
        service=service,
    ) == 2

    assert service.calls == []
    assert "question and context exceed 1 MiB" in capsys.readouterr().err
