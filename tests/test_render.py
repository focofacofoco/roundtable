from __future__ import annotations

import json

from facode_roundtable.models import ProviderResponse, RunResult
from facode_roundtable.render import render_json, render_markdown


def test_renderers_share_the_canonical_result():
    result = RunResult.create("Question", ["codex"])
    result.eligible_heads = ["codex"]
    result.responses.append(ProviderResponse("codex", "Answer", 1, model="test-model"))
    result.finish()

    markdown = render_markdown(result)
    payload = json.loads(render_json(result))

    assert "## Codex" in markdown
    assert "Answer" in markdown
    assert payload == result.to_dict()


def test_markdown_render_strips_terminal_control_sequences():
    result = RunResult.create("Question", ["codex"])
    result.eligible_heads = ["codex"]
    result.responses.append(
        ProviderResponse("codex", "safe\x1b[2J\x1b]52;c;payload\x07text\x00", 1)
    )
    result.finish()

    markdown = render_markdown(result)

    assert "safe" in markdown and "text" in markdown
    assert "\x1b" not in markdown
    assert "\x00" not in markdown
    assert "payload" not in markdown
