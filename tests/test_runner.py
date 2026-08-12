from __future__ import annotations

import asyncio
import json
import sys

import pytest

from facode_roundtable.runner import CommandRunner, _remove_workdir


def test_runner_uses_disposable_cwd_and_scrubs_secret_environment(tmp_path):
    script = tmp_path / "inspect_child.py"
    script.write_text(
        "import json, os, pathlib\n"
        "pathlib.Path('child-artifact').write_text('x')\n"
        "print(json.dumps({'cwd': os.getcwd(), 'secret': os.getenv('OPENAI_API_KEY'), "
        "'unrelated': os.getenv('ROUNDTABLE_SAFE_TEST'), 'path': os.getenv('PATH')}))\n",
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
    assert payload["unrelated"] is None
    assert payload["path"] == "safe-path"
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

    process = FakeProcess()
    terminated = False
    communicating = False

    async def create_process(*_args, **_kwargs):
        return process

    async def terminate_tree(_process):
        nonlocal terminated
        terminated = True

    async def communicate_bounded(_process, _input, _limit):
        nonlocal communicating
        communicating = True
        await asyncio.Event().wait()

    monkeypatch.setattr("facode_roundtable.runner.asyncio.create_subprocess_exec", create_process)
    monkeypatch.setattr("facode_roundtable.runner._terminate_tree", terminate_tree)
    monkeypatch.setattr(
        "facode_roundtable.runner._communicate_bounded", communicate_bounded
    )

    async def scenario():
        task = asyncio.create_task(CommandRunner().run(["fake"], timeout=10))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert terminated is True
    assert communicating is True


def test_runner_redacts_environment_secrets_and_token_shaped_output(tmp_path):
    script = tmp_path / "emit_secrets.py"
    script.write_text(
        "import os\n"
        "print('database=' + str(os.environ.get('DATABASE_URL')))\n"
        "print('Authorization: Bearer abc.def.ghi')\n"
        "print('refresh_token=raw-refresh-secret')\n"
        "print('api_key: sk-examplevalue')\n"
        "print('stderr password=very-secret', file=__import__('sys').stderr)\n",
        encoding="utf-8",
    )
    runner = CommandRunner(
        base_environment={
            "DATABASE_URL": "postgres://user:database-password@example.test/db",
            "OPENAI_API_KEY": "environment-secret-value",
        }
    )

    result = asyncio.run(runner.run([sys.executable, str(script)], timeout=10))
    combined = f"{result.stdout}\n{result.stderr}"

    for secret in (
        "database-password",
        "environment-secret-value",
        "abc.def.ghi",
        "raw-refresh-secret",
        "sk-examplevalue",
        "very-secret",
    ):
        assert secret not in combined
    assert "[REDACTED]" in combined


def test_runner_bounds_provider_output(tmp_path):
    script = tmp_path / "large_output.py"
    script.write_text("print('x' * 1024)\n", encoding="utf-8")

    result = asyncio.run(
        CommandRunner(max_output_bytes=128).run([sys.executable, str(script)], timeout=10)
    )

    assert result.returncode == 70
    assert result.failure == "output_limit"
    assert result.stdout == ""
    assert result.stderr == "provider output exceeded 128 bytes"


def test_runner_applies_output_limit_across_stdout_and_stderr(tmp_path):
    script = tmp_path / "combined_output.py"
    script.write_text(
        "import sys\n"
        "sys.stdout.write('x' * 80)\n"
        "sys.stderr.write('y' * 80)\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        CommandRunner(max_output_bytes=128).run(
            [sys.executable, str(script)], timeout=10
        )
    )

    assert result.failure == "output_limit"
    assert result.stdout == ""
    assert result.stderr == "provider output exceeded 128 bytes"


def test_workdir_cleanup_retries_transient_windows_handle(monkeypatch, tmp_path):
    work = tmp_path / "isolated"
    work.mkdir()
    actual_rmtree = __import__("shutil").rmtree
    attempts = 0

    def flaky_rmtree(path):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError("transient handle")
        actual_rmtree(path)

    monkeypatch.setattr("facode_roundtable.runner.shutil.rmtree", flaky_rmtree)

    assert asyncio.run(_remove_workdir(work)) is True
    assert attempts == 2
    assert not work.exists()
