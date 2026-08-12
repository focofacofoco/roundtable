from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys
import tomllib


_TAG = re.compile(r"v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def validate_annotation(annotation: str, tag: str, sha: str) -> None:
    lines = {line.strip() for line in annotation.splitlines() if line.strip()}
    if "facode-owned-tag" not in lines:
        raise ValueError("tag annotation marker is missing")
    if f"version: {tag}" not in lines:
        raise ValueError("tag annotation version does not match")
    if f"sha: {sha}" not in lines:
        raise ValueError("tag annotation SHA does not match")


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False, timeout=30
    )
    if result.returncode != 0:
        raise ValueError("git validation failed")
    return result.stdout.strip()


def validate_release(tag: str, sha: str, root: Path) -> None:
    match = _TAG.fullmatch(tag)
    if match is None:
        raise ValueError("release tag must be vMAJOR.MINOR.PATCH")
    version = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    if version != tag[1:]:
        raise ValueError("project version does not match tag")
    reference = f"refs/tags/{tag}"
    if _git("cat-file", "-t", reference) != "tag":
        raise ValueError("release tag must be annotated")
    annotation = _git("for-each-ref", "--format=%(contents)", reference)
    validate_annotation(annotation, tag, sha)
    if _git("rev-list", "-n", "1", reference) != sha:
        raise ValueError("tag target SHA does not match workflow SHA")
    first_parent = _git("rev-list", "--first-parent", "origin/main").splitlines()
    if sha not in first_parent:
        raise ValueError("release SHA is not on main first-parent history")


def main() -> int:
    try:
        validate_release(
            os.environ["GITHUB_REF_NAME"],
            os.environ["GITHUB_SHA"],
            Path(__file__).parents[1],
        )
    except (KeyError, OSError, ValueError) as exc:
        print(f"release validation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
