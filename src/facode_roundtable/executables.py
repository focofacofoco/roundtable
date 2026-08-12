from __future__ import annotations

import os
from pathlib import Path
import shutil


def resolve_cli(
    name: str, *, home: Path | None = None, local_app_data: Path | None = None
) -> str:
    user_home = (home or Path.home()).resolve()
    app_data = (
        local_app_data
        or Path(os.environ.get("LOCALAPPDATA", user_home / "AppData" / "Local"))
    ).resolve()
    suffix = ".exe" if os.name == "nt" else ""
    candidates = {
        "codex": [
            app_data / "Programs" / "OpenAI" / "Codex" / "bin" / f"codex{suffix}",
            app_data / "Microsoft" / "WinGet" / "Links" / f"codex{suffix}",
        ],
        "claude": [
            user_home / ".local" / "bin" / f"claude{suffix}",
            app_data / "Microsoft" / "WinGet" / "Links" / f"claude{suffix}",
        ],
        "grok": [user_home / ".grok" / "bin" / f"grok{suffix}"],
        "agy": [app_data / "agy" / "bin" / f"agy{suffix}"],
        "uv": [user_home / ".local" / "bin" / f"uv{suffix}"],
    }
    for candidate in candidates.get(name, []):
        if candidate.is_file():
            return str(candidate.resolve())

    detected = shutil.which(name)
    if detected:
        candidate = Path(detected)
        if candidate.is_absolute() and candidate.is_file():
            resolved = candidate.resolve()
            if _trusted_location(resolved, user_home, app_data):
                if resolved.suffix.lower() not in {".bat", ".cmd"} or name == "mmx":
                    return str(resolved)
    return str(_missing_path(name))


def _trusted_location(path: Path, home: Path, local_app_data: Path) -> bool:
    roots = [
        home / ".local" / "bin",
        home / ".grok" / "bin",
        local_app_data / "agy" / "bin",
        local_app_data / "Programs",
        local_app_data / "Microsoft" / "WinGet" / "Links",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")),
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")),
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32",
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
    ]
    return any(path == root.resolve() or path.is_relative_to(root.resolve()) for root in roots)


def _missing_path(name: str) -> Path:
    if os.name == "nt":
        drive = Path(os.environ.get("SYSTEMDRIVE", "C:"))
        return drive / "__facode_roundtable_cli_not_found__" / f"{name}.exe"
    return Path("/") / "__facode_roundtable_cli_not_found__" / name
