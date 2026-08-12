from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


_SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


def sanitize_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in environment.items()
        if not any(marker in name.upper() for marker in _SECRET_MARKERS)
    }


@dataclass(frozen=True, slots=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_ms: int
    timed_out: bool


class CommandRunner:
    def __init__(self, base_environment: Mapping[str, str] | None = None):
        self._base_environment = dict(base_environment if base_environment is not None else os.environ)

    async def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        input_text: str | None = None,
        timeout: float,
        environment: Mapping[str, str] | None = None,
    ) -> CommandResult:
        command = tuple(str(value) for value in argv)
        child_environment = dict(self._base_environment)
        if environment:
            child_environment.update(environment)
        child_environment = sanitize_environment(child_environment)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="facode-roundtable-") as work:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=Path(work),
                    env=child_environment,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
            except FileNotFoundError:
                return CommandResult(command, 127, "", "command not found", _elapsed(started), False)
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(input_text.encode("utf-8") if input_text is not None else None),
                    timeout=timeout,
                )
            except TimeoutError:
                await _terminate_tree(process)
                stdout, stderr = await process.communicate()
                return CommandResult(
                    command,
                    None,
                    stdout.decode("utf-8", errors="replace"),
                    stderr.decode("utf-8", errors="replace"),
                    _elapsed(started),
                    True,
                )
        return CommandResult(
            command,
            process.returncode,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
            _elapsed(started),
            False,
        )


def _elapsed(started: float) -> int:
    return max(1, round((time.perf_counter() - started) * 1000))


async def _terminate_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
