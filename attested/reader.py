"""Reader Agent (Muse Spark): JD -> structured JSON constraints. Nothing else."""
import json
import re

from . import meta_client

SYSTEM = (
    "You are the Reader Agent in a zero-hallucination career system. "
    "Extract the job description's concrete requirements into JSON only. "
    "No commentary, no prose outside the JSON object."
)

PROMPT = """Extract every concrete requirement from this job description.

Return exactly one JSON object with this shape:
{{"core_constraints": [{{"term": "<technology or skill>", "evidence": "<short quote from the JD>"}}],
  "seniority_signals": ["<signal>"],
  "deal_breakers": ["<hard requirement stated in the JD>"]}}

Rules:
- One entry per distinct technology or named skill in the JD.
- Do not emit generic platform names (e.g. "AWS", "cloud") when the JD names specific services on that platform.
- Keep the JD's own names for technologies (e.g. "Snowpark", not "Spark").
- evidence must be a short verbatim quote from the JD.

JOB DESCRIPTION:
---
{jd}
---"""


def extract(jd_text, emit):
    emit("reader", "Reader Agent calling Muse Spark for structured extraction")
    import time

    last = [0.0]

    def progress(ev):
        now = time.time()
        if now - last[0] >= 3.0:
            last[0] = now
            emit(
                "reader",
                f"Muse Spark reasoning in progress — streaming: "
                f"{ev['chunks']} chunks, {ev['chars']} chars received, "
                f"{ev['elapsed_s']}s elapsed",
            )

    result = meta_client.chat(
        [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": PROMPT.format(jd=jd_text)},
        ],
        max_tokens=16384,
        progress=progress,
    )
    emit(
        "reader",
        f"Muse Spark reasoning tokens: {result['reasoning_tokens']} | "
        f"total tokens: {result['usage'].get('total_tokens')}",
    )
    raw = result["content"]
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise RuntimeError("Reader returned no JSON object")
    data = json.loads(m.group(0))
    constraints = data.get("core_constraints") or []
    emit("reader", f"Extracted {len(constraints)} core constraints")
    return data
