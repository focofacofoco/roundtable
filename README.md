# Roundtable

Roundtable convenes multiple frontier-model command-line tools for independent advice and structured deliberation. This fork is **login-only**: providers authenticate through their official CLI login flows. Roundtable never accepts API keys, calls provider HTTP APIs, reads credential stores, or copies tokens.

## Install

```text
uv tool install "https://github.com/focofacofoco/roundtable/archive/refs/heads/main.zip"
roundtable harness install
```

From this repository:

```text
uv tool install .
```

The package is user-global. It does not install anything into projects you consult.

`harness install` idempotently registers the stdio MCP server in Codex and Claude and installs the login-only agent skill. Inspect or remove those integrations with `roundtable harness status` and `roundtable harness remove`.

## Providers

| Head | Official CLI | Accepted authentication |
| --- | --- | --- |
| Codex | `codex` | ChatGPT login |
| Claude | `claude` | claude.ai first-party login |
| Grok | `grok` | OAuth/OIDC plus `disable_api_key_auth=true` |
| Gemini | `agy` | Google Sign-In/system keyring |
| MiniMax | `mmx` | OAuth device flow |

GLM is intentionally unsupported until an official login-authenticated headless CLI exists.

```text
roundtable providers
roundtable models codex
roundtable models claude
roundtable auth status
roundtable auth login grok
roundtable doctor
roundtable ask "Which option has the strongest evidence?"
roundtable ask -q "..." --heads codex,claude --format json
roundtable ask -q "..." --rounds 3 --chair auto
roundtable ask -q "..." --research
```

Research is fail-closed. A provider participates only when Roundtable can constrain it to web tools while denying local file, command and MCP tools.

OAuth/browser consent remains inside the official provider CLI. Roundtable never receives or stores the resulting credentials. A provider stays ineligible until `roundtable providers` can prove the supported first-party login method.

`roundtable models codex`, `roundtable models grok`, and `roundtable models gemini`
query the installed official CLI with bounded output and a sanitized environment. An installed
CLI must be logged in for account-scoped discovery. Claude and MiniMax do not expose a headless
model-catalog command, so Roundtable reports that limitation without fabricating aliases.

## Configuration

Roundtable defaults Codex to `gpt-5.6-sol` at `xhigh` effort and Claude to
`claude-opus-5` at `xhigh` effort. These values live in the same provider configuration used
by the CLI, MCP server, adapters, and `roundtable models` output.

```text
roundtable config show
roundtable config set providers.codex.model gpt-5.6-sol
roundtable config set providers.codex.effort xhigh
roundtable config set providers.claude.model claude-opus-5
roundtable config set providers.claude.effort xhigh
```

Set a model or effort to `null` to defer to that provider CLI. Per-run model overrides still
use `--model provider=id`. Authentication remains login-only in every case.

## Output and privacy

- Advisory answers are independent and concurrent.
- JSON and MCP use the same `RunResult` schema.
- Partial success exits `10`; no usable result exits `20`.
- Runs are ephemeral by default. Only `--out` and `--save` persist output.
- Provider identities, raw auth output and credentials are never retained.
- Questions, context, peer answers and chair output are untrusted text; Roundtable never
  executes them, but model-level prompt injection cannot be eliminated by delimiters.
- The JSON contract is published at [`docs/run-result.schema.json`](docs/run-result.schema.json).

## MCP

```text
roundtable mcp serve
```

Tools: `roundtable_ask`, `roundtable_providers`, and `roundtable_doctor`. The server uses the official MCP Python SDK and returns both model-readable text and structured output.

## Lifecycle

```text
roundtable update
roundtable uninstall
```

`update` reinstalls the current fork from GitHub through `uv`. `uninstall` first removes the exact Roundtable MCP/skill integrations, then removes the global tool. Neither command touches provider logins.

## Development

```text
uv run --with pytest pytest
uv run python scripts/check_reproducible_build.py
uv run python scripts/soak.py --iterations 200
```

CI runs the tests and build on Windows, macOS, and Linux with Python 3.11 and 3.14. Release gates additionally smoke-test the installed wheel, reproduce artifacts byte-for-byte, soak the orchestration core, and audit locked runtime dependencies.

MIT. Forked from [frontier-infra/roundtable](https://github.com/frontier-infra/roundtable).
