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


_SAFE_SECURITY_CONTROLS = frozenset({"GROK_DISABLE_API_KEY_AUTH"})
_ALLOWED_ENVIRONMENT = frozenset(
    {
        "APPDATA",
        "COLORTERM",
        "COMSPEC",
        "FORCE_COLOR",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LOCALAPPDATA",
        "NO_COLOR",
        "NUMBER_OF_PROCESSORS",
        "PATH",
        "PATHEXT",
        "PROCESSOR_ARCHITECTURE",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USERDOMAIN",
        "USERNAME",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
    }
)
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
        if _environment_name_allowed(name)
    }


def _environment_name_allowed(name: str) -> bool:
    normalized = name.upper()
    return (
        normalized in _SAFE_SECURITY_CONTROLS
        or normalized in _ALLOWED_ENVIRONMENT
        or normalized.startswith("LC_")
    )


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
    failure: str | None = None


class CommandRunner:
    def __init__(
        self,
        base_environment: Mapping[str, str] | None = None,
        *,
        max_output_bytes: int = 8 * 1024 * 1024,
    ):
        self._base_environment = dict(base_environment if base_environment is not None else os.environ)
        if max_output_bytes <= 0:
            raise ValueError("max_output_bytes must be positive")
        self._max_output_bytes = max_output_bytes

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
            if not _environment_name_allowed(name)
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
                    command,
                    127,
                    "",
                    "command not found",
                    _elapsed(started),
                    False,
                    "cli_not_found",
                )
            else:
                try:
                    stdout, stderr = await asyncio.wait_for(
                        _communicate_bounded(
                            process,
                            input_text.encode("utf-8") if input_text is not None else None,
                            self._max_output_bytes,
                        ),
                        timeout=timeout,
                    )
                except _OutputLimitExceeded:
                    await _terminate_tree(process)
                    result = CommandResult(
                        command,
                        70,
                        "",
                        f"provider output exceeded {self._max_output_bytes} bytes",
                        _elapsed(started),
                        False,
                        "output_limit",
                    )
                except TimeoutError:
                    await _terminate_tree(process)
                    result = CommandResult(
                        command,
                        None,
                        "",
                        "provider timed out",
                        _elapsed(started),
                        True,
                        "timeout",
                    )
                except asyncio.CancelledError:
                    await asyncio.shield(_terminate_tree(process))
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
                failure="cleanup_failed",
            )
        return result


def _elapsed(started: float) -> int:
    return max(1, round((time.perf_counter() - started) * 1000))


class _OutputLimitExceeded(Exception):
    pass


async def _communicate_bounded(
    process: asyncio.subprocess.Process,
    input_bytes: bytes | None,
    limit: int,
) -> tuple[bytes, bytes]:
    total_size = 0

    async def feed_stdin() -> None:
        if input_bytes is None or process.stdin is None:
            return
        try:
            process.stdin.write(input_bytes)
            await process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            process.stdin.close()

    async def read_stream(stream: asyncio.StreamReader | None) -> bytes:
        nonlocal total_size
        if stream is None:
            return b""
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return b"".join(chunks)
            total_size += len(chunk)
            if total_size > limit:
                raise _OutputLimitExceeded
            chunks.append(chunk)

    tasks = [
        asyncio.create_task(feed_stdin()),
        asyncio.create_task(read_stream(process.stdout)),
        asyncio.create_task(read_stream(process.stderr)),
    ]
    try:
        _, stdout, stderr = await asyncio.gather(*tasks)
        await process.wait()
        return stdout, stderr
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _terminate_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "nt":
        system_root = Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        taskkill = system_root / "System32" / "taskkill.exe"
        try:
            killer = await asyncio.create_subprocess_exec(
                str(taskkill),
                "/PID",
                str(process.pid),
                "/T",
                "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=3)
        except (FileNotFoundError, OSError, TimeoutError):
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await _wait_for_exit(process)
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
        await _wait_for_exit(process)


async def _wait_for_exit(process: asyncio.subprocess.Process) -> None:
    try:
        await asyncio.wait_for(process.wait(), timeout=2)
    except TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(process.wait(), timeout=1)
        except TimeoutError:
            return


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
