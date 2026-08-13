# Codex Research Design

## Goal

Allow the login-authenticated Codex head to participate in Roundtable research on Windows while preserving the existing fail-closed, web-only boundary.

## Design

When `CodexAdapter.invoke(..., research=True)` runs, place the documented global `--search` flag before `exec`, keep user configuration and rules ignored, keep the read-only sandbox, disable local execution and extension features, and force an empty MCP server map. Non-research calls continue to set `web_search="disabled"`.

Advertise Codex research support from the provider catalog only on Windows so status checks admit it into research rounds there. On other platforms, the catalog and status report `research=false`, and direct research invocation fails with `research_ineligible` before starting the CLI. Research remains enabled only in round one; later deliberation and chair calls remain offline under the service's existing behavior.

The live dual-head probe exposed that Claude's web tools were available but not preapproved under `dontAsk`. Add `--allowedTools WebSearch,WebFetch` for research and reject any non-empty `permission_denials` payload so an unverified refusal cannot be counted as successful research.

## Verification

Unit tests must prove the exact Codex and Claude research argv, denial handling, the unchanged non-research lockdown, and both sides of the Windows capability gate. A Windows live probe must return sourced web results from both default heads while local tools remain disabled. The full cross-platform test, build, soak, and dependency-audit gates must remain green.

Official interface: <https://developers.openai.com/codex/cli/reference#global-flags> documents `--search` as enabling live web search.
