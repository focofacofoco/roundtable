from __future__ import annotations

import json

from facode_roundtable.models import (
    ChairResult,
    ClaimRecord,
    EvidenceLink,
    ProviderResponse,
    RunResult,
)
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


def test_markdown_renders_claim_ledger_from_chair_result():
    result = RunResult.create("Question", ["codex", "claude"], "deliberation")
    result.chair = ChairResult(
        "claude",
        "SPLIT",
        agreed=["codex"],
        dissent=["claude"],
        recommendation="Investigate.",
        claims=[
            ClaimRecord(
                "claim-1",
                "Option A is ready.",
                ["codex"],
                ["claude"],
                "disputed",
                [EvidenceLink("https://example.com/source", ["codex"], "supports")],
            )
        ],
    )

    markdown = render_markdown(result)

    assert "### Claim claim-1 — disputed" in markdown
    assert "Option A is ready." in markdown
    assert "https://example.com/source" in markdown
    assert "supports — codex" in markdown


def test_markdown_renders_resolution_record_by_claim_reference():
    result = RunResult.create("Question", ["codex", "claude"], "deliberation")
    result.chair = ChairResult(
        "claude",
        "CONSENSUS",
        agreed=["codex", "claude"],
        recommendation="Ship.",
        claims=[ClaimRecord("claim-1", "Ready.", ["codex", "claude"], [], "agreed")],
        rationale_claims=["claim-1"],
        alternatives=["Wait."],
        tradeoffs=["Speed versus observation."],
        review_conditions=["A blocker appears."],
    )

    markdown = render_markdown(result)

    assert "## Resolution" in markdown
    assert "Rationale claims: claim-1" in markdown
    assert "- Wait." in markdown
    assert "- Speed versus observation." in markdown
    assert "- A blocker appears." in markdown
