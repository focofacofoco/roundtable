from __future__ import annotations

import asyncio
import json
import sys

import pytest

from facode_roundtable.runner import CommandRunner


def test_runner_uses_disposable_cwd_and_scrubs_secret_environment(tmp_path):
    script = tmp_path / "inspect_child.py"
    script.write_text(
        "import json, os, pathlib\n"
        "pathlib.Path('child-artifact').write_text('x')\n"
        "print(json.dumps({'cwd': os.getcwd(), 'secret': os.getenv('OPENAI_API_KEY'), "
        "'safe': os.getenv('ROUNDTABLE_SAFE_TEST')}))\n",
        encoding="utf-8",
    )
    runner = CommandRunner(base_environment={
        "PATH": "safe-path",
        "OPENAI_API_KEY": "must-not-leak",
        "ROUNDTABLE_SAFE_TEST": "visible",
    })

    result = asyncio.run(runner.run([sys.executable, str(script)], timeout=10))
    payload = json.loads(result.stdout)

    assert result.returncode == 0
    assert payload["secret"] is None
    assert payload["safe"] == "visible"
    assert not __import__("pathlib").Path(payload["cwd"]).exists()


def test_runner_preserves_explicit_api_key_lockdown_control(tmp_path):
    script = tmp_path / "inspect_lockdown.py"
    script.write_text(
        "import json, os\n"
        "print(json.dumps({"
        "'lockdown': os.getenv('GROK_DISABLE_API_KEY_AUTH'), "
        "'key': os.getenv('GROK_API_KEY')}))\n",
        encoding="utf-8",
    )
    runner = CommandRunner(base_environment={"GROK_API_KEY": "must-not-leak"})

    result = asyncio.run(
        runner.run(
            [sys.executable, str(script)],
            timeout=10,
            environment={"GROK_DISABLE_API_KEY_AUTH": "1"},
        )
    )
    payload = json.loads(result.stdout)

    assert payload == {"lockdown": "1", "key": None}


def test_runner_returns_typed_timeout(tmp_path):
    script = tmp_path / "wait.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    result = asyncio.run(CommandRunner().run([sys.executable, str(script)], timeout=0.05))

    assert result.timed_out is True
    assert result.returncode is None


def test_runner_terminates_child_when_caller_cancels(monkeypatch):
    class FakeProcess:
        returncode = None

        def __init__(self):
            self.communicate_calls = 0

        async def communicate(self, _input=None):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                await asyncio.Event().wait()
            return b"", b""

    process = FakeProcess()
    terminated = False

    async def create_process(*_args, **_kwargs):
        return process

    async def terminate_tree(_process):
        nonlocal terminated
        terminated = True

    monkeypatch.setattr("facode_roundtable.runner.asyncio.create_subprocess_exec", create_process)
    monkeypatch.setattr("facode_roundtable.runner._terminate_tree", terminate_tree)

    async def scenario():
        task = asyncio.create_task(CommandRunner().run(["fake"], timeout=10))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert terminated is True
    assert process.communicate_calls == 2
