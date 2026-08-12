# Roundtable

Roundtable convenes multiple frontier-model command-line tools for independent advice and structured deliberation. This fork is **login-only**: providers authenticate through their official CLI login flows. Roundtable never accepts API keys, calls provider HTTP APIs, reads credential stores, or copies tokens.

## Install

```text
uv tool install facode-roundtable
```

From this repository:

```text
uv tool install .
```

The package is user-global. It does not install anything into projects you consult.

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
roundtable auth status
roundtable doctor
roundtable ask "Which option has the strongest evidence?"
roundtable ask -q "..." --heads codex,claude --format json
roundtable ask -q "..." --rounds 3 --chair auto
roundtable ask -q "..." --research
```

Research is fail-closed. A provider participates only when Roundtable can constrain it to web tools while denying local file, command and MCP tools.

## Output and privacy

- Advisory answers are independent and concurrent.
- JSON and MCP use the same `RunResult` schema.
- Partial success exits `10`; no usable result exits `20`.
- Runs are ephemeral by default. Only `--out` and `--save` persist output.
- Provider identities, raw auth output and credentials are never retained.

## MCP

```text
roundtable mcp serve
```

Tools: `roundtable_ask`, `roundtable_providers`, and `roundtable_doctor`. The server uses the official MCP Python SDK and returns both model-readable text and structured output.

## Development

```text
uv run --with pytest pytest
```

MIT. Forked from [frontier-infra/roundtable](https://github.com/frontier-infra/roundtable).
