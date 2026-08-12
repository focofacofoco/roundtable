from __future__ import annotations

from facode_roundtable.cli import default_service, main, resolve_cli
from facode_roundtable.config import load_config
from facode_roundtable.models import ProviderResponse, RunResult
from facode_roundtable.providers.base import ProviderStatus


class FakeService:
    def __init__(self):
        self.adapters = {"codex": FakeStatusAdapter("codex")}

    async def ask(self, question, **kwargs):
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


def test_version_contract(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out == "roundtable 0.5.0\n"


def test_default_service_exposes_exact_five_head_catalog():
    assert tuple(default_service().adapters) == ("codex", "claude", "grok", "gemini", "minimax")


def test_cli_resolution_finds_official_user_install_before_shell_restart(tmp_path, monkeypatch):
    grok = tmp_path / ".grok" / "bin" / "grok.exe"
    grok.parent.mkdir(parents=True)
    grok.touch()
    monkeypatch.setattr("facode_roundtable.cli.shutil.which", lambda _: None)

    assert resolve_cli("grok", home=tmp_path) == str(grok)


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
