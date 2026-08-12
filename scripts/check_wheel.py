from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    wheels = sorted(args.directory.glob("facode_roundtable-*.whl"))
    if len(wheels) != 1:
        parser.error(f"expected exactly one wheel in {args.directory}, found {len(wheels)}")
    with tempfile.TemporaryDirectory(prefix="roundtable-wheel-check-") as temporary:
        environment = Path(temporary) / "venv"
        subprocess.run(["uv", "venv", str(environment)], check=True)
        python = environment / ("Scripts/python.exe" if __import__("os").name == "nt" else "bin/python")
        subprocess.run(
            ["uv", "pip", "install", "--python", str(python), str(wheels[0])],
            check=True,
        )
        result = subprocess.run(
            [str(python), "-m", "facode_roundtable", "version"],
            check=True,
            capture_output=True,
            text=True,
        )
    expected = wheels[0].name.split("-")[1]
    actual = result.stdout.strip()
    if actual != f"roundtable {expected}":
        raise SystemExit(f"wheel smoke mismatch: {actual!r}")
    print(actual)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
