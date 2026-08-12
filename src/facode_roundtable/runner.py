from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, replace
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import tempfile
import time


_SECRET_MARKERS = ("API_KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
_SAFE_SECURITY_CONTROLS = frozenset({"GROK_DISABLE_API_KEY_AUTH"})
_SECRET_ASSIGNMENT = re.compile(
    r'''(?ix)
    (?P<prefix>["']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)["']?\s*[:=]\s*)
    (?P<quote>["']?)(?P<value>[^\s,"'}]+)(?P=quote)
    '''
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]+")
_TOKEN_PREFIX = re.compile(r"\b(?:sk|xai)-[A-Za-z0-9_-]{8,}\b")


def sanitize_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in environment.items()
        if name.upper() in _SAFE_SECURITY_CONTROLS
        or not any(marker in name.upper() for marker in _SECRET_MARKERS)
    }


def redact_text(text: str, secret_values: tuple[str, ...] = ()) -> str:
    redacted = text
    for value in sorted(secret_values, key=len, reverse=True):
        if len(value) >= 8:
            redacted = redacted.replace(value, "[REDACTED]")
    redacted = _BEARER.sub(r"\1[REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(_redact_assignment, redacted)
    return _TOKEN_PREFIX.sub("[REDACTED]", redacted)


def _redact_assignment(match: re.Match[str]) -> str:
    if not match.group("quote") and match.group("value").lower() in {
        "null",
        "true",
        "false",
    }:
        return match.group(0)
    return (
        f"{match.group('prefix')}{match.group('quote')}"
        f"[REDACTED]{match.group('quote')}"
    )


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
        secret_values = tuple(
            value
            for name, value in child_environment.items()
            if name.upper() not in _SAFE_SECURITY_CONTROLS
            and any(marker in name.upper() for marker in _SECRET_MARKERS)
        )
        child_environment = sanitize_environment(child_environment)
        started = time.perf_counter()
        work = Path(tempfile.mkdtemp(prefix="facode-roundtable-"))
        result: CommandResult
        try:
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE if input_text is not None else asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=work,
                    env=child_environment,
                    creationflags=creationflags,
                    start_new_session=os.name != "nt",
                )
            except FileNotFoundError:
                result = CommandResult(
                    command, 127, "", "command not found", _elapsed(started), False
                )
            else:
                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(
                            input_text.encode("utf-8") if input_text is not None else None
                        ),
                        timeout=timeout,
                    )
                except TimeoutError:
                    await _terminate_tree(process)
                    stdout, stderr = await process.communicate()
                    result = CommandResult(
                        command,
                        None,
                        redact_text(
                            stdout.decode("utf-8", errors="replace"), secret_values
                        ),
                        redact_text(
                            stderr.decode("utf-8", errors="replace"), secret_values
                        ),
                        _elapsed(started),
                        True,
                    )
                except asyncio.CancelledError:
                    await asyncio.shield(_terminate_tree(process))
                    await asyncio.shield(process.communicate())
                    raise
                else:
                    result = CommandResult(
                        command,
                        process.returncode,
                        redact_text(
                            stdout.decode("utf-8", errors="replace"), secret_values
                        ),
                        redact_text(
                            stderr.decode("utf-8", errors="replace"), secret_values
                        ),
                        _elapsed(started),
                        False,
                    )
        finally:
            cleaned = await asyncio.shield(_remove_workdir(work))
        if not cleaned:
            return replace(
                result,
                returncode=70,
                stdout="",
                stderr="isolated workspace cleanup failed",
            )
        return result


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


async def _remove_workdir(path: Path) -> bool:
    for delay in (0, 0.05, 0.1, 0.25, 0.5, 1.0):
        if delay:
            await asyncio.sleep(delay)
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            continue
    return False
