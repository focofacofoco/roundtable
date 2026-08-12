---
name: roundtable
description: Use when a decision benefits from independent frontier-model perspectives, web-backed research, cross-model deliberation, or a second opinion from Grok, Codex, Claude, Gemini, or MiniMax.
---

# Roundtable

Convene login-authenticated official model CLIs through `roundtable ask` or the `roundtable_ask` MCP tool.

## Quick reference

| Need | Invocation |
| --- | --- |
| Independent advice | `roundtable ask "QUESTION"` |
| Selected heads | `roundtable ask "QUESTION" --heads codex,claude` |
| Deliberation | `roundtable ask "QUESTION" --rounds 2 --chair auto` |
| Current web facts | `roundtable ask "QUESTION" --research` |
| Machine output | `roundtable ask "QUESTION" --format json` |

Default to one blind advisory round. Use two or three rounds only when cross-examination is material. Research is fail-closed: heads without provable web-only isolation are excluded.

Report each answer, failures, chair verdict, agreement, dissent, and a decided synthesis. Never request or accept API keys; authentication belongs exclusively to each official CLI login flow. Check readiness with `roundtable providers` or `roundtable doctor`.

## Common mistakes

- Treating missing heads as agreement: show them as unavailable.
- Averaging incompatible answers: surface the decisive disagreement.
- Using research for local code: pass explicit context instead.
- Persisting by default: use `--out` or `--save` only when requested.
