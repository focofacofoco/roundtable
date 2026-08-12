from __future__ import annotations

import subprocess
from pathlib import Path

from facode_roundtable.harness import HarnessManager


class FakeCommands:
    def __init__(self):
        self.configured: set[str] = set()
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        provider = argv[0]
        command = argv[2]
        if command == "get":
            return subprocess.CompletedProcess(
                argv,
                0 if provider in self.configured else 1,
                "command: roundtable\nargs: mcp serve\n",
                "",
            )
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
    legacy = tmp_path / ".agents" / "skills" / "roundtable" / "SKILL.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("---\nname: roundtable\n---\nUse XAI_API_KEY.\n", encoding="utf-8")
    custom = tmp_path / ".claude" / "skills" / "roundtable" / "SKILL.md"
    custom.parent.mkdir(parents=True)
    custom.write_text("custom instructions\n", encoding="utf-8")
    manager = HarnessManager(
        home=tmp_path,
        command_runner=FakeCommands(),
        skill_text=skill_source().read_text(encoding="utf-8"),
    )

    report = manager.install()

    assert "XAI_API_KEY" not in legacy.read_text(encoding="utf-8")
    assert custom.read_text(encoding="utf-8") == "custom instructions\n"
    assert report["ok"] is False
    assert report["components"]["claude_skill"]["reason"] == "conflict"


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
