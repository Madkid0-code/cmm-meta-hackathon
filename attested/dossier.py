"""Evidence Dossier renderer. Deterministic templates only — no LLM call."""
from . import verifier


def render(split, build_result, audit, attested=None):
    lines = ["# Evidence Dossier", ""]
    lines.append(
        "Every claim below was checked by deterministic code against the "
        "seeker's attested profile. There is no match score: a claim is "
        "either defensible in the interview room, or it is a liability."
    )
    lines.append("")
    lines.append("## Defensible claims — verified, with evidence")
    if split["defensible"]:
        for item in split["defensible"]:
            lines.append(f"- **{item['term']}** — {item['evidence']}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("## Vulnerable claims — the job asks, you cannot prove it")
    if split["vulnerable"]:
        for item in split["vulnerable"]:
            lines.append(f"- **{item['term']}** — {item['reason']}")
            if attested is not None:
                lines.append(f"  - {verifier.pivot_for(item['term'], attested)}")
    else:
        lines.append("- None.")
    lines.append("")
    lines.append("## Verified narrative (guardrail-passed)")
    lines.append(build_result["draft"] or "No clean draft could be produced.")
    lines.append("")
    if audit:
        lines.append("## Audit trail — hallucinations caught by the guardrail")
        labels = {"intake": "Intake", 0: "Baseline"}
        for a in audit:
            label = labels.get(a["round"], f"Round {a['round']}")
            lines.append(
                f"- {label} [{a['type']}] **{a['term']}** — {a['detail']}"
            )
        lines.append("")
    lines.append("## My call (the human decides — the system never does)")
    lines.append("Apply / Skip: ______  Reason: ______")
    lines.append("")
    return "\n".join(lines)
