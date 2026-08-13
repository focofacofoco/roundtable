from __future__ import annotations

import subprocess
from pathlib import Path

import facode_roundtable.harness as harness_module
from facode_roundtable.harness import HarnessManager, _run_command
from facode_roundtable.runner import CommandRunner


class FakeCommands:
    def __init__(self):
        self.configured: set[str] = set()
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        provider = argv[0]
        command = argv[2]
        if command == "get":
            output = (
                "roundtable:\n  Scope: User config\n  Status: Connected\n"
                if provider == "claude" and provider in self.configured
                else "command: roundtable\nargs: mcp serve\n"
            )
            missing = (
                'No MCP server named "roundtable". Configured servers: existing'
                if provider == "claude"
                else "Error: No MCP server named 'roundtable' found."
            )
            return subprocess.CompletedProcess(
                argv,
                0 if provider in self.configured else 1,
                output if provider in self.configured else "",
                "" if provider in self.configured else missing,
            )
        if command == "list":
            output = "roundtable: roundtable mcp serve - connected\n"
            return subprocess.CompletedProcess(argv, 0, output, "")
        if command == "add":
            self.configured.add(provider)
            return subprocess.CompletedProcess(argv, 0, "", "")
        if command == "remove":
            self.configured.discard(provider)
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)


def skill_source() -> Path:
    return (
        Path(__file__).parents[1]
        / "plugins"
        / "roundtable"
        / "skills"
        / "roundtable"
        / "SKILL.md"
    )


def test_harness_install_status_remove_are_idempotent(tmp_path):
    commands = FakeCommands()
    manager = HarnessManager(
        home=tmp_path,
        command_runner=commands,
        skill_text=skill_source().read_text(encoding="utf-8"),
    )

    first = manager.install()
    second = manager.install()
    status = manager.status()

    assert first["ok"] is True
    assert second["ok"] is True
    assert status["ok"] is True
    assert len([call for call in commands.calls if call[2] == "add"]) == 2
    assert (tmp_path / ".agents" / "skills" / "roundtable" / "SKILL.md").exists()
    assert (tmp_path / ".claude" / "skills" / "roundtable" / "SKILL.md").exists()

    removed = manager.remove()
    removed_again = manager.remove()

    assert removed["ok"] is True
    assert removed_again["ok"] is True
    assert commands.configured == set()
    assert not (tmp_path / ".agents" / "skills" / "roundtable" / "SKILL.md").exists()
    assert not (tmp_path / ".claude" / "skills" / "roundtable" / "SKILL.md").exists()


def test_harness_replaces_known_legacy_roundtable_skill_but_preserves_custom_file(tmp_path):
    current_skill = skill_source().read_text(encoding="utf-8")
    legacy = tmp_path / ".agents" / "skills" / "roundtable" / "SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        current_skill.replace("<!-- facode-roundtable-managed -->\n", "", 1),
        encoding="utf-8",
    )
    custom = tmp_path / ".claude" / "skills" / "roundtable" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom instructions\n", encoding="utf-8")
    manager = HarnessManager(
        home=tmp_path,
        command_runner=FakeCommands(),
        skill_text=current_skill,
    )

    report = manager.install()

    assert "facode-roundtable-managed" in legacy.read_text(encoding="utf-8")
    assert custom.read_text(encoding="utf-8") == "custom instructions\n"
    assert report["ok"] is False
    assert report["components"]["claude_skill"]["reason"] == "conflict"


def test_harness_rejects_extra_mcp_arguments_that_share_expected_prefix(tmp_path):
    commands = FakeCommands()
    commands.configured.update({"codex", "claude"})

    def ambiguous_command(argv):
        if argv[:3] == ["codex", "mcp", "get"]:
            return subprocess.CompletedProcess(
                argv, 0, "command: roundtable\nargs: mcp serve --foreign\n", ""
            )
        if argv[:4] == ["claude", "mcp", "get", "roundtable"]:
            return subprocess.CompletedProcess(
                argv, 0, "roundtable:\n  Scope: User config\n", ""
            )
        if argv[:3] == ["claude", "mcp", "list"]:
            return subprocess.CompletedProcess(
                argv, 0, "roundtable: roundtable mcp serve --foreign - connected\n", ""
            )
        return commands(argv)

    manager = HarnessManager(
        home=tmp_path,
        command_runner=ambiguous_command,
        skill_text=skill_source().read_text(encoding="utf-8"),
    )

    report = manager.remove()

    assert report["ok"] is False
    assert report["components"]["codex_mcp"]["reason"] == "conflict"
    assert report["components"]["claude_mcp"]["reason"] == "conflict"
    assert not [call for call in commands.calls if call[2] == "remove"]


def test_harness_never_removes_foreign_claude_mcp_or_similarly_named_skill(tmp_path):
    commands = FakeCommands()
    commands.configured.add("claude")

    def foreign_command(argv):
        if argv[:4] == ["claude", "mcp", "get", "roundtable"]:
            return subprocess.CompletedProcess(
                argv, 0, "roundtable:\n  Scope: User config\n", ""
            )
        if argv[:3] == ["claude", "mcp", "list"]:
            return subprocess.CompletedProcess(
                argv, 0, "roundtable: foreign-command - connected\n", ""
            )
        return commands(argv)

    custom = tmp_path / ".claude" / "skills" / "roundtable" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text(
        "---\nname: roundtable\n---\nCustom roundtable ask API_KEY notes.\n",
        encoding="utf-8",
    )
    manager = HarnessManager(
        home=tmp_path,
        command_runner=foreign_command,
        skill_text=skill_source().read_text(encoding="utf-8"),
    )

    report = manager.remove()

    assert report["ok"] is False
    assert custom.exists()
    assert not [call for call in commands.calls if call[2] == "remove"]


def test_packaged_skill_is_login_only_and_uses_current_cli_contract():
    content = skill_source().read_text(encoding="utf-8")

    for forbidden in (
        "API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "curl -",
        "openai,glm",
    ):
        assert forbidden not in content
    assert "roundtable ask" in content
    assert "official CLI login" in content


def test_harness_status_failure_is_not_mistaken_for_absence_or_installed(tmp_path):
    calls = []

    def failing(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 70, "", "output_limit")

    manager = HarnessManager(
        home=tmp_path,
        command_runner=failing,
        skill_text=skill_source().read_text(encoding="utf-8"),
    )

    report = manager.install()

    assert report["ok"] is False
    assert report["components"]["codex_mcp"]["reason"] == "status_failed"
    assert report["components"]["claude_mcp"]["reason"] == "status_failed"
    assert not [call for call in calls if call[2] == "add"]


def test_harness_requires_exact_provider_specific_absence_message(tmp_path):
    calls = []

    def ambiguous(argv):
        calls.append(argv)
        return subprocess.CompletedProcess(
            argv,
            1,
            "",
            "transient failure: no MCP server named roundtable while reading state",
        )

    report = HarnessManager(
        home=tmp_path,
        command_runner=ambiguous,
        skill_text=skill_source().read_text(encoding="utf-8"),
    ).install()

    assert report["ok"] is False
    assert report["components"]["codex_mcp"]["reason"] == "status_failed"
    assert report["components"]["claude_mcp"]["reason"] == "status_failed"
    assert not [call for call in calls if call[2] == "add"]


def test_harness_default_runner_is_bounded_and_sanitized():
    names = set(_run_command.__code__.co_names)

    assert harness_module.CommandRunner is CommandRunner
    assert "CommandRunner" in names
    assert "run" in names
