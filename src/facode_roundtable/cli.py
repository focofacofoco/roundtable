from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Sequence

from facode_roundtable import __version__
from facode_roundtable.config import Config, ConfigError, config_path, load_config, save_config
from facode_roundtable.mcp_server import serve
from facode_roundtable.providers.claude import ClaudeAdapter
from facode_roundtable.providers.codex import CodexAdapter
from facode_roundtable.render import render_json, render_markdown
from facode_roundtable.runner import CommandRunner
from facode_roundtable.service import RoundtableService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="roundtable")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    providers = subparsers.add_parser("providers")
    providers.add_argument("--json", action="store_true")
    doctor = subparsers.add_parser("doctor")
    doctor.add_argument("--live", action="store_true")
    doctor.add_argument("--json", action="store_true")
    auth = subparsers.add_parser("auth")
    auth_subparsers = auth.add_subparsers(dest="auth_command", required=True)
    for command in ("status", "login", "logout"):
        auth_parser = auth_subparsers.add_parser(command)
        auth_parser.add_argument("provider", nargs="?" if command == "status" else None)
    models = subparsers.add_parser("models")
    models.add_argument("provider", nargs="?")
    config = subparsers.add_parser("config")
    config_subparsers = config.add_subparsers(dest="config_command", required=True)
    config_subparsers.add_parser("show")
    config_subparsers.add_parser("path")
    config_subparsers.add_parser("reset")
    config_set = config_subparsers.add_parser("set")
    config_set.add_argument("field")
    config_set.add_argument("value")
    mcp = subparsers.add_parser("mcp")
    mcp_subparsers = mcp.add_subparsers(dest="mcp_command", required=True)
    mcp_subparsers.add_parser("serve")
    ask = subparsers.add_parser("ask")
    ask.add_argument("question", nargs="?")
    ask.add_argument("-q", "--question", dest="question_flag")
    ask.add_argument("-c", "--context", action="append", default=[])
    ask.add_argument("--heads", default="codex,claude")
    ask.add_argument("--rounds", type=int, default=1)
    ask.add_argument("--chair", default="auto")
    ask.add_argument("--research", action="store_true")
    ask.add_argument("--timeout", type=float)
    ask.add_argument("--model", action="append", default=[])
    ask.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ask.add_argument("--out", type=Path)
    ask.add_argument("--save", action="store_true")
    return parser


def default_service() -> RoundtableService:
    runner = CommandRunner()
    return RoundtableService({"codex": CodexAdapter(runner), "claude": ClaudeAdapter(runner)})


def main(
    argv: Sequence[str] | None = None,
    *,
    service: RoundtableService | None = None,
    config_file: Path | None = None,
) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "version":
        print(f"roundtable {__version__}")
        return 0
    application = service or default_service()
    if args.command == "providers":
        return _providers(application, as_json=args.json)
    if args.command == "doctor":
        return _doctor(application, as_json=args.json, live=args.live, path=config_file)
    if args.command == "auth":
        return _auth(application, args.auth_command, args.provider)
    if args.command == "models":
        return _list_models(args.provider)
    if args.command == "config":
        return _config(args, config_file)
    if args.command == "mcp":
        serve(application)
        return 0
    try:
        question = _question(args)
        heads = [item.strip().lower() for item in args.heads.split(",") if item.strip()]
        context = [path.read_text(encoding="utf-8") for path in args.context]
        models = _models(args.model)
        result = asyncio.run(
            application.ask(
                question,
                heads=heads,
                rounds=args.rounds,
                chair=args.chair,
                research=args.research,
                timeout=args.timeout or (600 if args.research else 300),
                models=models,
                context=context,
            )
        )
    except (OSError, ValueError) as exc:
        print(f"roundtable: {exc}", file=sys.stderr)
        return 2
    rendered = render_json(result) if args.format == "json" else render_markdown(result)
    sys.stdout.write(rendered)
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    if args.save:
        Path(f"roundtable-{result.run_id}.json").write_text(render_json(result), encoding="utf-8")
    return int(result.exit_code)


def _providers(service: RoundtableService, *, as_json: bool) -> int:
    statuses = asyncio.run(_statuses(service))
    payload = [status.to_dict() for status in statuses]
    if as_json:
        print(json.dumps({"providers": payload, "unsupported": {"glm": "no_official_login_only_headless_cli"}}, indent=2))
    else:
        for status in statuses:
            state = "eligible" if status.eligible else status.reason
            print(f"{status.name:8} {state}")
        print("glm      unsupported (no official login-only headless CLI)")
    return 0


async def _statuses(service: RoundtableService):
    return await asyncio.gather(*(adapter.status() for adapter in service.adapters.values()))


def _doctor(
    service: RoundtableService, *, as_json: bool, live: bool, path: Path | None
) -> int:
    try:
        load_config(path)
        config_valid = True
    except ConfigError:
        config_valid = False
    statuses = asyncio.run(_statuses(service))
    live_results: dict[str, str] = {}
    if live:
        for status in statuses:
            if not status.eligible:
                continue
            result = asyncio.run(service.ask("Reply with exactly: OK", heads=[status.name], timeout=60))
            live_results[status.name] = "ok" if result.successful_heads else "failed"
    payload = {
        "config_path": str(path or config_path()),
        "config_valid": config_valid,
        "providers": [status.to_dict() for status in statuses],
        "live": live_results,
    }
    if as_json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"Config: {'valid' if config_valid else 'invalid'} ({payload['config_path']})")
        for status in statuses:
            state = "eligible" if status.eligible else status.reason
            suffix = f", live={live_results[status.name]}" if status.name in live_results else ""
            print(f"{status.name:8} {state}{suffix}")
    return 0 if config_valid else 2


def _auth(service: RoundtableService, command: str, provider: str | None) -> int:
    if command == "status":
        statuses = asyncio.run(_statuses(service))
        selected = [item for item in statuses if provider is None or item.name == provider]
        if not selected:
            print(f"roundtable: unknown provider: {provider}", file=sys.stderr)
            return 2
        for status in selected:
            state = "eligible" if status.eligible else status.reason
            print(f"{status.name}: {state} ({status.auth_method or 'none'})")
        return 0 if all(item.eligible for item in selected) else 3
    commands = {
        ("codex", "login"): ["codex", "login"],
        ("codex", "logout"): ["codex", "logout"],
        ("claude", "login"): ["claude", "auth", "login"],
        ("claude", "logout"): ["claude", "auth", "logout"],
        ("grok", "login"): ["grok", "login"],
        ("grok", "logout"): ["grok", "logout"],
        ("gemini", "login"): ["agy"],
        ("minimax", "login"): ["mmx", "auth", "login", "--recommend"],
        ("minimax", "logout"): ["mmx", "auth", "logout"],
    }
    argv = commands.get((provider or "", command))
    if not argv:
        print(f"roundtable: {command} is not automatable for {provider}", file=sys.stderr)
        return 3
    environment = {
        name: value
        for name, value in os.environ.items()
        if not any(marker in name.upper() for marker in ("API_KEY", "TOKEN", "SECRET", "PASSWORD"))
    }
    try:
        return subprocess.run(argv, env=environment, check=False).returncode
    except FileNotFoundError:
        print(f"roundtable: CLI not found for {provider}", file=sys.stderr)
        return 3


def _list_models(provider: str | None) -> int:
    if provider == "gemini":
        try:
            return subprocess.run(["agy", "models"], check=False).returncode
        except FileNotFoundError:
            return 3
    selected = [provider] if provider else ["codex", "claude", "grok", "gemini", "minimax"]
    for name in selected:
        print(f"{name}: discovery=unsupported" if name != "gemini" else "gemini: run `roundtable models gemini`")
    return 0


def _config(args: argparse.Namespace, path: Path | None) -> int:
    target = path or config_path()
    try:
        if args.config_command == "path":
            print(target)
            return 0
        if args.config_command == "show":
            print(json.dumps(load_config(target).to_dict(), indent=2))
            return 0
        if args.config_command == "reset":
            save_config(Config(), target)
            return 0
        config = load_config(target)
        payload = config.to_dict()
        value = _config_value(args.value)
        parts = args.field.split(".")
        if len(parts) == 1:
            payload[parts[0]] = value
        elif len(parts) == 3 and parts[0] == "providers":
            payload["providers"].setdefault(parts[1], {})[parts[2]] = value
        else:
            raise ConfigError(f"unknown configuration field: {args.field}")
        save_config(Config.from_dict(payload), target)
        return 0
    except ConfigError as exc:
        print(f"roundtable: {exc}", file=sys.stderr)
        return 2


def _config_value(value: str):
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    if "," in value:
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def _question(args: argparse.Namespace) -> str:
    values = [value for value in (args.question, args.question_flag) if value is not None]
    if len(values) > 1:
        raise ValueError("provide the question exactly once")
    if values:
        question = values[0]
    elif not sys.stdin.isatty():
        question = sys.stdin.read()
    else:
        raise ValueError("question is required")
    if not question.strip():
        raise ValueError("question must not be empty")
    return question.strip()


def _models(values: list[str]) -> dict[str, str]:
    models: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("model overrides must use provider=id")
        provider, model = value.split("=", 1)
        if not provider or not model:
            raise ValueError("model overrides must use provider=id")
        models[provider] = model
    return models


if __name__ == "__main__":
    raise SystemExit(main())
