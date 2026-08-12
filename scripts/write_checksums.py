from __future__ import annotations

import hashlib
from pathlib import Path


def main() -> None:
    directory = Path(".artifacts/release")
    assets = sorted(path for path in directory.iterdir() if path.is_file())
    lines = [f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in assets]
    (directory / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
