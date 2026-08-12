# Roundtable contributor contract

Roundtable is a login-only Python CLI and MCP server. It invokes only installed official provider CLIs and accepts authentication only when those CLIs prove a first-party login. Never add API-key configuration, direct provider HTTP transports, token copying, credential-store reads, or fallback authentication.

## Runtime contracts

- Heads are exactly `codex`, `claude`, `grok`, `gemini`, and `minimax`.
- GLM remains explicitly unsupported until an official login-authenticated headless CLI exists.
- Advisory round 1 is blind and concurrent.
- Deliberation is limited to 2–3 rounds, requires two responses, and validates chair JSON strictly.
- Research is fail-closed unless a provider can be constrained to web-only tools.
- Runs use disposable working directories and retain nothing unless `--out` or `--save` is explicit.
- CLI and MCP return the same structured `RunResult` contract.

## Development

```text
uv run --with pytest pytest -q
uv build
roundtable providers --json
roundtable doctor
```

Use tests first for behavior changes. Preserve typed exit codes, per-provider failure isolation, secret redaction, requested-head ordering, and the architectural source scan.

## Global lifecycle

```text
uv tool install .
roundtable harness install
roundtable harness status
roundtable update
roundtable harness remove
roundtable uninstall
```

Provider login/logout is delegated to official CLIs through `roundtable auth`. Do not automate browser consent or fabricate a successful login state.
