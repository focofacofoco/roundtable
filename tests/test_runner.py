from __future__ import annotations

import asyncio
import json
import sys

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


def test_runner_returns_typed_timeout(tmp_path):
    script = tmp_path / "wait.py"
    script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    result = asyncio.run(CommandRunner().run([sys.executable, str(script)], timeout=0.05))

    assert result.timed_out is True
    assert result.returncode is None
