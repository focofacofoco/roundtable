from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Sequence

from facode_roundtable import __version__
from facode_roundtable.catalog import (
    PROVIDER_SPECS,
    capabilities_payload,
    unsupported_providers,
)
from facode_roundtable.config import (
    PROVIDERS,
    Config,
    ConfigError,
    config_path,
    load_config,
    save_config,
)
from facode_roundtable.executables import resolve_cli
from facode_roundtable.harness import HarnessManager
from facode_roundtable.lifecycle import ReleaseUpdater
from facode_roundtable.models import ExitCode
from facode_roundtable.providers.base import Runner
from facode_roundtable.providers.claude import ClaudeAdapter
from facode_roundtable.providers.codex import CodexAdapter
from facode_roundtable.providers.gemini import GeminiAdapter
from facode_roundtable.providers.grok import GrokAdapter
from facode_roundtable.providers.minimax import MiniMaxAdapter
from facode_roundtable.render import render_json, render_markdown, terminal_safe
from facode_roundtable.runner import CommandResult, CommandRunner, sanitize_environment
from facode_roundtable.service import MAX_PROMPT_BYTES, RoundtableService


_MODEL_ID = r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}"
_BULLET_MODEL = re.compile(
    rf"^\s*[*-]\s+(?P<model>{_MODEL_ID})(?:\s+\(default\))?\s*$"
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="roundtable")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("version")
    subparsers.add_parser("update")
    subparsers.add_parser("uninstall")
    harness = subparsers.add_parser("harness")
    harness_subparsers = harness.add_subparsers(dest="harness_command", required=True)
    for command in ("status", "install", "remove"):
        harness_parser = harness_subparsers.add_parser(command)
        harness_parser.add_argument("--json", action="store_true")
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
    ask.add_argument("-c", "--context", action="append", type=Path, default=[])
    ask.add_argument("--heads")
    ask.add_argument("--rounds", type=int, default=1)
    ask.add_argument("--chair")
    ask.add_argument("--research", action="store_true")
    ask.add_argument("--timeout", type=float)
    ask.add_argument("--model", action="append", default=[])
    ask.add_argument("--format", choices=("markdown", "json"), default="markdown")
    ask.add_argument("--out", type=Path)
    ask.add_argument("--save", action="store_true")
    return parser


def default_service(config: Config | None = None) -> RoundtableService:
    effective = config or Config()
    runner = CommandRunner()
    codex = effective.providers["codex"]
    claude = effective.providers["claude"]
    return RoundtableService(
        {
            "codex": CodexAdapter(
                runner,
                resolve_cli(PROVIDER_SPECS["codex"].executable),
                default_model=codex.model,
                default_effort=codex.effort,
            ),
            "claude": ClaudeAdapter(
                runner,
                resolve_cli(PROVIDER_SPECS["claude"].executable),
                default_model=claude.model,
                default_effort=claude.effort,
            ),
            "grok": GrokAdapter(runner, resolve_cli(PROVIDER_SPECS["grok"].executable)),
            "gemini": GeminiAdapter(runner, resolve_cli(PROVIDER_SPECS["gemini"].executable)),
            "minimax": MiniMaxAdapter(runner, resolve_cli(PROVIDER_SPECS["minimax"].executable)),
        },
        concurrency=effective.concurrency,
        enabled={
            name for name, provider in effective.providers.items() if provider.enabled
        },
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    service: RoundtableService | None = None,
    config_file: Path | None = None,
    harness_manager: HarnessManager | None = None,
    command_runner: Runner | None = None,
) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "version":
        print(f"roundtable {__version__}")
        return 0
    if args.command == "harness":
        manager = harness_manager or HarnessManager()
        return _harness(manager, args.harness_command, as_json=args.json)
    if args.command == "update":
        try:
            update_config = load_config(config_file)
        except ConfigError as exc:
            print(f"roundtable: {exc}", file=sys.stderr)
            return 2
        return ReleaseUpdater(
            channel=update_config.update_channel,
            installed_version=__version__,
        ).run()
    if args.command == "uninstall":
        manager = harness_manager or HarnessManager()
        report = manager.remove()
        if not report["ok"]:
            print("roundtable: harness removal failed", file=sys.stderr)
            return 3
        return _tool_lifecycle("uninstall")
    if args.command == "config":
        return _config(args, config_file)
    if args.command == "doctor":
        try:
            doctor_config = load_config(config_file)
        except ConfigError:
            doctor_config = Config()
        application = service or default_service(doctor_config)
        return _doctor(application, as_json=args.json, live=args.live, path=config_file)
    try:
        effective_config = load_config(config_file)
    except ConfigError as exc:
        print(f"roundtable: {exc}", file=sys.stderr)
        return 2
    if args.command == "models":
        return _list_models(
            args.provider,
            effective_config,
            command_runner or CommandRunner(max_output_bytes=1024 * 1024),
        )
    application = service or default_service(effective_config)
    if args.command == "providers":
        return _providers(application, as_json=args.json)
    if args.command == "auth":
        return _auth(application, args.auth_command, args.provider)
    if args.command == "mcp":
        from facode_roundtable.mcp_server import serve

        serve(application, config=effective_config, config_file=config_file)
        return 0
    try:
        question = _question(args)
        heads = _heads(args.heads, effective_config)
        context = _read_context(args.context, question)
        models = {
            name: provider.model
            for name, provider in effective_config.providers.items()
            if provider.model is not None and name in heads
        }
        models.update(_models(args.model))
        result = asyncio.run(
            application.ask(
                question,
                heads=heads,
                rounds=args.rounds,
                chair=args.chair or effective_config.chair,
                research=args.research,
                timeout=(
                    args.timeout
                    if args.timeout is not None
                    else (
                        effective_config.research_timeout_seconds
                        if args.research
                        else effective_config.timeout_seconds
                    )
                ),
                models=models,
                context=context,
            )
        )
    except KeyboardInterrupt:
        print("roundtable: interrupted", file=sys.stderr)
        return int(ExitCode.INTERRUPTED)
    except (OSError, ValueError) as exc:
        print(f"roundtable: {exc}", file=sys.stderr)
        return 2
    rendered = render_json(result) if args.format == "json" else render_markdown(result)
    sys.stdout.write(rendered)
    if args.out:
        _write_output(args.out, rendered)
    if args.save:
        _write_output(Path(f"roundtable-{result.run_id}.json"), render_json(result))
    return int(result.exit_code)


def _providers(service: RoundtableService, *, as_json: bool) -> int:
    statuses = asyncio.run(_statuses(service))
    payload = [status.to_dict() for status in statuses]
    if as_json:
        print(json.dumps({
            "schema_version": 1,
            "providers": payload,
            "unsupported": unsupported_providers(),
            "capabilities": capabilities_payload(),
        }, indent=2))
    else:
        for status in statuses:
            state = "eligible" if status.eligible else status.reason
            print(f"{status.name:8} {state}")
        for name, reason in unsupported_providers().items():
            print(f"{name:8} unsupported ({reason.replace('_', ' ')})")
    return 0


def _harness(manager: HarnessManager, action: str, *, as_json: bool) -> int:
    report = getattr(manager, action)()
    if as_json:
        print(json.dumps(report, indent=2))
    else:
        for name, state in report["components"].items():
            configured = state.get("configured", False)
            current = state.get("current", True)
            label = "ready" if configured and current else state.get("reason", "not_configured")
            print(f"{name}: {label}")
    return 0 if report["ok"] else 3


def _tool_lifecycle(action: str) -> int:
    uv = resolve_cli("uv")
    if not Path(uv).is_file():
        print("roundtable: uv is required for this operation", file=sys.stderr)
        return 3
    argv = [uv, "tool", "uninstall", "facode-roundtable"]
    try:
        return subprocess.run(
            argv,
            check=False,
            env=sanitize_environment(os.environ),
        ).returncode
    except FileNotFoundError:
        print("roundtable: uv is not available", file=sys.stderr)
        return 3
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
        "schema_version": 1,
        "config_path": str(path or config_path()),
        "config_valid": config_valid,
        "providers": [status.to_dict() for status in statuses],
        "live": live_results,
        "capabilities": capabilities_payload(),
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
    spec = PROVIDER_SPECS.get(provider or "")
    command_args = getattr(spec, command, None) if spec is not None else None
    if command_args is None:
        print(f"roundtable: {command} is not automatable for {provider}", file=sys.stderr)
        return 3
    argv = [resolve_cli(spec.executable), *command_args]
    environment = sanitize_environment(os.environ)
    if provider == "grok":
        environment["GROK_DISABLE_API_KEY_AUTH"] = "1"
    try:
        return subprocess.run(argv, env=environment, check=False).returncode
    except FileNotFoundError:
        print(f"roundtable: CLI not found for {provider}", file=sys.stderr)
        return 3


def _list_models(provider: str | None, config: Config, runner: Runner) -> int:
    if provider is not None and provider not in PROVIDERS:
        print(f"roundtable: unknown provider: {provider}", file=sys.stderr)
        return 2
    selected = [provider] if provider else list(PROVIDERS)
    try:
        return asyncio.run(_list_models_async(selected, config, runner))
    except KeyboardInterrupt:
        print("roundtable: interrupted", file=sys.stderr)
        return int(ExitCode.INTERRUPTED)


async def _list_models_async(
    providers: list[str], config: Config, runner: Runner
) -> int:
    failed = False
    for name in providers:
        settings = config.providers[name]
        spec = PROVIDER_SPECS[name]
        if spec.model_command is None:
            print(
                _model_catalog_header(
                    name,
                    settings.model,
                    settings.effort,
                    spec.model_discovery,
                )
            )
            continue
        command = [resolve_cli(spec.executable), *spec.model_command]
        result = await runner.run(
            command,
            timeout=20,
            environment={"GROK_DISABLE_API_KEY_AUTH": "1"} if name == "grok" else None,
        )
        error = _model_discovery_error(result)
        models: list[str] | None = None
        if error is None and name == "codex":
            models = _codex_models(result.stdout)
            if models is None:
                error = "invalid_response"
        elif error is None:
            models = _plain_cli_models(result.stdout)
            if models is None:
                error = "invalid_response"
        if error is not None:
            print(
                f"roundtable: {name} model discovery failed: {error}",
                file=sys.stderr,
            )
            failed = True
            continue
        print(_model_catalog_header(
            name, settings.model, settings.effort, spec.model_discovery
        ))
        for model in models:
            print(f"  {model}")
    return 3 if failed else 0


def _model_catalog_header(
    provider: str,
    model: str | None,
    effort: str | None,
    discovery: str,
) -> str:
    return (
        f"{provider}: default={model or 'cli-default'} "
        f"effort={effort or 'cli-default'} discovery={discovery}"
    )


def _model_discovery_error(result: CommandResult) -> str | None:
    output = f"{result.stdout}\n{result.stderr}".lower()
    if any(
        marker in output
        for marker in (
            "not authenticated",
            "please sign in",
            "login required",
            "please log in",
        )
    ):
        return "login_required"
    if result.failure is not None:
        return result.failure
    if result.timed_out:
        return "timeout"
    if result.returncode == 127:
        return "cli_not_found"
    if result.returncode != 0:
        return "provider_failed"
    return None


def _codex_models(output: str) -> list[str] | None:
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return None
    models: list[str] = []
    for item in payload["models"]:
        if not isinstance(item, dict) or item.get("visibility") != "list":
            continue
        slug = item.get("slug")
        if (
            isinstance(slug, str)
            and re.fullmatch(_MODEL_ID, slug)
            and slug not in models
        ):
            models.append(slug)
    return models or None


def _plain_cli_models(output: str) -> list[str] | None:
    models: list[str] = []
    for line in terminal_safe(output).splitlines():
        match = _BULLET_MODEL.fullmatch(line)
        if match:
            model = match.group("model")
        else:
            candidate = line.strip()
            model = candidate if re.fullmatch(_MODEL_ID, candidate) and "-" in candidate else None
        if model is not None and model not in models:
            models.append(model)
    return models or None


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
        question = sys.stdin.read(MAX_PROMPT_BYTES + 1)
    else:
        raise ValueError("question is required")
    if not question.strip():
        raise ValueError("question must not be empty")
    return question.strip()


def _heads(value: str | None, config: Config) -> list[str]:
    enabled = [
        name for name, provider in config.providers.items() if provider.enabled
    ]
    if value is None:
        selected = (
            enabled
            if config.default_heads == "available"
            else list(config.default_heads)
        )
    else:
        selected = [item.strip().lower() for item in value.split(",") if item.strip()]
    disabled = [name for name in selected if name not in enabled]
    if disabled:
        raise ValueError(f"provider is disabled: {disabled[0]}")
    return selected


def _read_context(paths: list[Path], question: str) -> list[str]:
    remaining = MAX_PROMPT_BYTES - len(question.encode("utf-8"))
    if remaining < 0:
        raise ValueError("question and context exceed 1 MiB")
    context: list[str] = []
    for path in paths:
        with path.open("rb") as handle:
            content = handle.read(remaining + 1)
        if len(content) > remaining:
            raise ValueError("question and context exceed 1 MiB")
        try:
            context.append(content.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise ValueError(f"context file is not valid UTF-8: {path}") from exc
        remaining -= len(content)
    return context


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


def _write_output(target: Path, content: str) -> None:
    temporary = target.with_suffix(".tmp")
    temporary.write_text(content, encoding="utf-8")
    try:
        os.chmod(temporary, 0o600)
    except OSError:
        pass
    temporary.replace(target)


if __name__ == "__main__":
    raise SystemExit(main())
