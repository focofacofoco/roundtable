# Codex Research Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable Codex as a fail-closed web-research head.

**Architecture:** Reuse the Codex adapter's existing isolated invocation. Select either live web search or disabled search from the `research` argument, and expose the matching catalog capability.

**Tech Stack:** Python 3.11+, pytest, Codex CLI.

## Global Constraints

- Keep ChatGPT login authentication; never accept API keys.
- Permit native web search only during the first research round.
- Deny shell, code-mode host, browser automation, files, apps, plugins, skills, and MCP during research.
- Preserve non-research behavior.

---

### Task 1: Codex research capability

**Files:**
- Modify: `src/facode_roundtable/providers/codex.py`
- Modify: `src/facode_roundtable/catalog.py`
- Modify: `tests/test_providers.py`
- Modify: `tests/test_core_contracts.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_mcp.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `CodexAdapter.invoke(prompt, timeout, model=None, research=False)`
- Produces: `ProviderStatus.research=True` and an isolated `codex --search exec ...` research invocation.

- [ ] **Step 1: Write failing contract tests**

```python
def test_codex_research_invocation_allows_native_web_search_only():
    response = asyncio.run(CodexAdapter(runner).invoke("Question", timeout=20, research=True))
    argv, _ = runner.calls[0]
    assert argv[:3] == ["codex", "--search", "exec"]
    assert ["--config", "mcp_servers={}"] in adjacent_pairs(argv)
    assert "shell_tool" in disabled_features(argv)
```

Update catalog assertions from `research=False` to `research=True`.

- [ ] **Step 2: Verify red**

Run: `uv run --frozen pytest -q tests/test_providers.py tests/test_core_contracts.py tests/test_cli.py tests/test_mcp.py`

Expected: Codex research raises `research_ineligible` and capability assertions fail.

- [ ] **Step 3: Implement minimal routing**

Set Codex catalog `research=True`. For research calls, construct `codex --search exec`, add `mcp_servers={}`, and omit `web_search="disabled"`; for ordinary calls preserve `codex exec` plus disabled web search.

- [ ] **Step 4: Verify locally and live**

Run the focused tests, the full suite on Python 3.11 and 3.14, a live `roundtable doctor --live --json`, and a one-round Codex research query that returns an external URL.

- [ ] **Step 5: Run release gates and deliver**

Run reproducible build, wheel smoke test, 200-iteration soak, dependency audit, `git diff --check`, commit the implementation, push current `main`, and monitor the complete CI matrix.
