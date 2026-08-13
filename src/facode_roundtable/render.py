from __future__ import annotations

import json
import re

from facode_roundtable.models import RunResult


_OSC = re.compile(r"\x1b\].*?(?:\x07|\x1b\\)", re.DOTALL)
_CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def terminal_safe(value: str) -> str:
    return _CONTROL.sub("", _CSI.sub("", _OSC.sub("", value)))


def render_json(result: RunResult) -> str:
    return json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n"


def render_markdown(result: RunResult) -> str:
    lines = ["# Roundtable", "", f"Run: `{result.run_id}`", ""]
    current_round = 0
    for response in result.responses:
        if response.round != current_round and response.round > 1:
            lines.extend([f"# Round {response.round}", ""])
        current_round = response.round
        lines.extend(
            [f"## {response.provider.title()}", "", terminal_safe(response.content), ""]
        )
    if result.errors:
        lines.extend(["## Errors", ""])
        for error in result.errors:
            lines.append(
                f"- `{error.provider}` — `{error.code}`: {terminal_safe(error.message)}"
            )
        lines.append("")
    if result.chair:
        lines.extend(
            [
                f"## Chair — {result.chair.chair.title()}",
                "",
                f"**Verdict:** `{result.chair.verdict}`",
                "",
                terminal_safe(result.chair.recommendation),
                "",
            ]
        )
        if result.chair.agreed:
            lines.extend(["**Agreed**", *[f"- {item}" for item in result.chair.agreed], ""])
        if result.chair.dissent:
            lines.extend(["**Dissent**", *[f"- {item}" for item in result.chair.dissent], ""])
        if result.chair.claims:
            lines.extend(["## Claims", ""])
            for claim in result.chair.claims:
                lines.extend(
                    [
                        f"### Claim {claim.id} — {claim.status}",
                        "",
                        terminal_safe(claim.statement),
                        "",
                    ]
                )
                if claim.supporters:
                    lines.append(f"- Supporters: {', '.join(claim.supporters)}")
                if claim.dissenters:
                    lines.append(f"- Dissenters: {', '.join(claim.dissenters)}")
                for evidence in claim.evidence:
                    lines.append(
                        f"- Evidence: {terminal_safe(evidence.url)} "
                        f"({evidence.relation} — {', '.join(evidence.providers)})"
                    )
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"
