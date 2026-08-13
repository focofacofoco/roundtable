# Codex Research Design

## Goal

Allow the login-authenticated Codex head to participate in Roundtable research while preserving the existing fail-closed, web-only boundary.

## Design

When `CodexAdapter.invoke(..., research=True)` runs, place the documented global `--search` flag before `exec`, keep user configuration and rules ignored, keep the read-only sandbox, disable local execution and extension features, and force an empty MCP server map. Non-research calls continue to set `web_search="disabled"`.

Advertise Codex research support from the provider catalog so status checks admit it into research rounds. Research remains enabled only in round one; later deliberation and chair calls remain offline under the service's existing behavior.

## Verification

Unit tests must prove the exact research argv, the unchanged non-research lockdown, and the advertised capability. A live probe must return a sourced web result with Codex while local tools remain disabled. The full test, build, soak, and dependency-audit gates must remain green.

Official interface: <https://developers.openai.com/codex/cli/reference#global-flags> documents `--search` as enabling live web search.
