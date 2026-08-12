from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def hashes(directory: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir())
        if path.is_file() and (path.suffix == ".whl" or path.name.endswith(".tar.gz"))
    }


def main() -> int:
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required")
    environment = dict(os.environ)
    environment["SOURCE_DATE_EPOCH"] = "315532800"
    with tempfile.TemporaryDirectory(prefix="roundtable-build-check-") as temporary:
        root = Path(temporary)
        outputs = []
        for name in ("a", "b"):
            destination = root / name
            subprocess.run(
                [uv, "build", "--out-dir", str(destination)],
                cwd=ROOT,
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            outputs.append(hashes(destination))
    report = {"schema_version": 1, "reproducible": outputs[0] == outputs[1], "hashes": outputs[0]}
    print(json.dumps(report, indent=2))
    return 0 if report["reproducible"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
