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
