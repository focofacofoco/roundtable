from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import platform
from typing import Any

from facode_roundtable import __version__
from facode_roundtable.catalog import capabilities_payload
from facode_roundtable.config import Config, config_path


_PROBE = "Reply with exactly: OK"
_QUORUM = 2


def runtime_evidence(config: Config) -> dict[str, str]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "roundtable_version": __version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "update_channel": config.update_channel,
        "runtime_fingerprint": _runtime_fingerprint(),
    }


async def build_diagnosis(
    service: Any,
    config: Config,
    *,
    config_file: Path | None,
    config_valid: bool,
    live: bool,
) -> dict[str, Any]:
    statuses = await _statuses(service)
    qualification, live_results = await _qualification(
        service, config, statuses, live=live, config_valid=config_valid
    )
    return {
        "schema_version": 1,
        "config_path": str(config_file or config_path()),
        "config_valid": config_valid,
        "providers": [status.to_dict() for status in statuses],
        "live": live_results,
        "capabilities": capabilities_payload(),
        "evidence": runtime_evidence(config),
        "qualification": qualification,
    }


async def _statuses(service: Any) -> list[Any]:
    if hasattr(service, "statuses"):
        return await service.statuses()
    return await asyncio.gather(
        *(adapter.status() for adapter in service.adapters.values())
    )


async def _qualification(
    service: Any,
    config: Config,
    statuses: list[Any],
    *,
    live: bool,
    config_valid: bool,
) -> tuple[dict[str, Any], dict[str, str]]:
    if not live:
        return {
            "performed": False,
            "qualified": None,
            "quorum_required": _QUORUM,
            "successful": [],
            "results": [],
        }, {}
    if not config_valid:
        return {
            "performed": False,
            "qualified": False,
            "quorum_required": _QUORUM,
            "successful": [],
            "results": [],
        }, {}

    probes = {
        status.name: asyncio.create_task(
            service.ask(_PROBE, heads=[status.name], timeout=60)
        )
        for status in statuses
        if config.providers[status.name].enabled and status.eligible
    }
    outcomes = await asyncio.gather(*probes.values(), return_exceptions=True)
    completed = dict(zip(probes, outcomes, strict=True))
    results = []
    successful = []
    live_results = {}
    for status in statuses:
        name = status.name
        if not config.providers[name].enabled:
            item = _qualification_item(name, "disabled", status.model, None)
        elif not status.eligible:
            item = _qualification_item(
                name, status.reason or "ineligible", status.model, None
            )
        else:
            run = completed[name]
            if isinstance(run, BaseException):
                item = _qualification_item(
                    name, "probe_failed", status.model, None
                )
                live_results[name] = "failed"
                results.append(item)
                continue
            response = run.responses[0] if run.responses else None
            if response is not None and response.content == "OK":
                item = _qualification_item(
                    name, "pass", response.model, response.duration_ms
                )
                successful.append(name)
                live_results[name] = "ok"
            else:
                error = run.errors[0].code if run.errors else "invalid_response"
                item = _qualification_item(
                    name,
                    error,
                    response.model if response is not None else status.model,
                    response.duration_ms if response is not None else None,
                )
                live_results[name] = "failed"
        results.append(item)
    return {
        "performed": True,
        "qualified": len(successful) >= _QUORUM,
        "quorum_required": _QUORUM,
        "successful": successful,
        "results": results,
    }, live_results


def _qualification_item(
    provider: str, status: str, model: str | None, duration_ms: int | None
) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": status,
        "model": model,
        "duration_ms": duration_ms,
    }


def _runtime_fingerprint() -> str:
    package = Path(__file__).parent
    digest = hashlib.sha256()
    files = [
        path
        for path in package.rglob("*")
        if path.is_file()
        and "__pycache__" not in path.parts
        and (path.suffix in {".py", ".ps1"} or path.name == "SKILL.md")
    ]
    for path in sorted(files, key=lambda item: item.relative_to(package).as_posix()):
        relative = path.relative_to(package).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
