from __future__ import annotations

import json
import os
import subprocess


def main() -> int:
    result = subprocess.run(
        [
            "gh", "release", "view", os.environ["GITHUB_REF_NAME"],
            "--repo", os.environ["GITHUB_REPOSITORY"], "--json", "isImmutable",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode != 0:
        return 1
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return 1
    return 0 if payload.get("isImmutable") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
