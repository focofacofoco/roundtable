from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import sys
import time

from facode_roundtable.providers.base import InvocationResult, ProviderStatus
from facode_roundtable.service import RoundtableService


ROOT = Path(__file__).resolve().parents[1]


class SoakAdapter:
    def __init__(self, name: str):
        self.name = name

    async def status(self):
        return ProviderStatus(self.name, True, True, auth_method="soak")

    async def invoke(self, prompt, *, timeout, model=None, research=False):
        return InvocationResult(f"{self.name}: {prompt}", duration_ms=0)


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(len(ordered) * fraction + 0.999999) - 1))
    return ordered[index]


def fingerprint() -> str:
    digest = hashlib.sha256()
    paths = [ROOT / "pyproject.toml", ROOT / "uv.lock"]
    paths.extend(sorted((ROOT / "src").rglob("*.py")))
    for path in paths:
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


async def run_soak(iterations: int) -> dict[str, object]:
    service = RoundtableService(
        {"codex": SoakAdapter("codex"), "claude": SoakAdapter("claude")}
    )
    latencies: list[float] = []
    run_ids: set[str] = set()
    failures = 0
    started = time.perf_counter()
    for index in range(iterations):
        attempt = time.perf_counter()
        result = await service.ask(f"Question {index}", heads=["codex", "claude"])
        latencies.append((time.perf_counter() - attempt) * 1000)
        run_ids.add(result.run_id)
        failures += int(bool(result.errors) or len(result.responses) != 2)
    wall_ms = (time.perf_counter() - started) * 1000
    return {
        "iterations": iterations,
        "failures": failures,
        "unique_run_ids": len(run_ids),
        "wall_ms": round(wall_ms, 3),
        "latency_ms": {
            "min": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "p95_descriptive": round(percentile(latencies, 0.95), 3),
            "max": round(max(latencies), 3),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.iterations < 20:
        parser.error("iterations must be at least 20")
    started_at = datetime.now(timezone.utc).isoformat()
    result = asyncio.run(run_soak(args.iterations))
    report = {
        "schema_version": 1,
        "campaign": "release-soak",
        "workload": "in-process two-head advisory orchestration",
        "workspace": str(ROOT),
        "workspace_id": hashlib.sha256(str(ROOT).encode()).hexdigest()[:12],
        "source_fingerprint": fingerprint(),
        "argv": sys.argv,
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "python": sys.version,
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "cache_policy": "not-applicable",
        **result,
    }
    rendered = json.dumps(report, indent=2) + "\n"
    print(rendered, end="")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    return 0 if report["failures"] == 0 and report["unique_run_ids"] == args.iterations else 1


if __name__ == "__main__":
    raise SystemExit(main())
