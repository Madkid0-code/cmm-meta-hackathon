"""Builder Agent (Muse Spark) inside the deterministic guardrail loop.

The Builder drafts. The Verifier (verifier.check_draft) disposes. Every
rejection is emitted as a visible event with its full payload — the loop is
the product demo, not a hidden retry.
"""
from . import meta_client, verifier

SYSTEM = (
    "You are the Builder Agent in a zero-hallucination career system. "
    "You write short, strong resume narrative using ONLY claims the seeker "
    "has attested. Any other technology or metric is forbidden."
)

DRAFT_PROMPT = """Write a 5-7 line professional summary for this job seeker, tailored to the job.

ATTESTED CLAIMS (the only technologies and facts you may use):
{attested}

JOB'S CORE CONSTRAINTS:
{constraints}
{raw_section}
Rules:
- Use only attested claims. Never name a technology that is not attested.
- Never invent metrics, percentages, team sizes, or years for a technology.
- Write in first person plural past tense ("Built...", "Operated...")."""

BASELINE_SYSTEM = (
    "You are a conventional AI resume writer. Your job is to make the "
    "candidate sound as strong as possible for the target job."
)

BASELINE_PROMPT = """Write a 5-7 line professional summary for this candidate, tailored aggressively to the job's core technologies so it passes screening.

CANDIDATE BACKGROUND:
{attested}

JOB'S CORE CONSTRAINTS (emphasize these):
{constraints}"""


REWRITE_PROMPT = """Your draft was REJECTED by the deterministic verifier.

REJECT PAYLOAD:
{reject}

DRAFT THAT FAILED:
{draft}

Rewrite the summary removing every rejected claim. Re-centre on attested
claims only. Do not argue, do not explain — output only the rewritten summary."""


def build(attested, profile_text, constraints, emit, max_rounds=3, raw_resume_text=""):
    attested_block = "\n".join(
        f"- {v['term']} :: {v['evidence']}" for v in attested.values()
    )
    constraints_block = "\n".join(f"- {c['term']}" for c in constraints)
    audit = []
    # Round 0 — the baseline every other AI resume tool ships: an
    # unconstrained writer told to tailor aggressively to the job.
    # The guardrail runs on it exactly as on any other draft.
    emit("builder", "Baseline draft: a conventional AI resume writer, unguarded")
    baseline = meta_client.chat(
        [
            {"role": "system", "content": BASELINE_SYSTEM},
            {
                "role": "user",
                "content": BASELINE_PROMPT.format(
                    attested=attested_block, constraints=constraints_block
                ),
            },
        ],
        max_tokens=8192,
    )
    baseline_draft = baseline["content"].strip()
    baseline_violations = verifier.check_draft(
        baseline_draft, attested, profile_text
    )
    for v in baseline_violations:
        emit(
            "guardrail",
            f"GUARDRAIL ALERT [baseline:{v['type']}] {v['term']}: {v['detail']}",
        )
        audit.append({"round": 0, **v, "draft": baseline_draft})
    if baseline_violations:
        emit(
            "guardrail",
            f"Baseline REJECTED with {len(baseline_violations)} violation(s). "
            "This is what ships without a guardrail.",
        )
    raw_section = ""
    if raw_resume_text.strip():
        raw_section = (
            "\nTHE SEEKER'S RAW DRAFT RESUME (unaudited — it contains claims "
            "the intake audit already flagged; reconcile it against the JD "
            "using ONLY the attested claims as the boundary; never adopt a "
            "claim from this draft that is not attested):\n"
            + raw_resume_text.strip()
            + "\n"
        )
        emit("builder", "Builder ingesting the raw unaudited resume — reconcile mode")
    messages = [
        {"role": "system", "content": SYSTEM},
        {
            "role": "user",
            "content": DRAFT_PROMPT.format(
                attested=attested_block,
                constraints=constraints_block,
                raw_section=raw_section,
            ),
        },
    ]
    import time

    last = [0.0]

    def progress(ev):
        now = time.time()
        if now - last[0] >= 3.0:
            last[0] = now
            emit(
                "builder",
                f"Muse Spark reasoning in progress — streaming: "
                f"{ev['chunks']} chunks, {ev['chars']} chars received, "
                f"{ev['elapsed_s']}s elapsed",
            )

    for round_no in range(1, max_rounds + 1):
        emit("builder", f"Builder Agent drafting (round {round_no})")
        result = meta_client.chat(messages, max_tokens=8192, progress=progress)
        draft = result["content"].strip()
        emit(
            "builder",
            f"Muse Spark reasoning tokens: {result['reasoning_tokens']} | "
            f"total tokens: {result['usage'].get('total_tokens')}",
        )
        violations = verifier.check_draft(draft, attested, profile_text)
        if not violations:
            emit("guardrail", "GUARDRAIL PASSED — draft is fully attested")
            return {"draft": draft, "audit": audit, "rounds": round_no}
        reject_lines = []
        for v in violations:
            reject_lines.append(f"{v['type']}: {v['term']} — {v['detail']}")
            emit(
                "guardrail",
                f"GUARDRAIL ALERT [{v['type']}] {v['term']}: {v['detail']}",
            )
            audit.append({"round": round_no, **v, "draft": draft})
        emit("guardrail", "Execution halted. Reject payload returned to Builder.")
        messages.append({"role": "assistant", "content": draft})
        messages.append(
            {
                "role": "user",
                "content": REWRITE_PROMPT.format(
                    reject="\n".join(reject_lines), draft=draft
                ),
            }
        )
    emit("guardrail", f"GUARDRAIL FAILED — no clean draft after {max_rounds} rounds")
    return {"draft": None, "audit": audit, "rounds": max_rounds}
