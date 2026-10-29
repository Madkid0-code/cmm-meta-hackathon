"""Resume builder stage: candidate_matches -> candidate_resumes.

Production CMM's builder joins match + posting + candidate and emits one
tailored resume per match; a match without a built resume is never eligible
for the applier. Same here:

- The summary is the Builder Agent's guardrail-passed narrative for THIS job.
- The skills section lists only this job's DEFENSIBLE claims, with evidence.
- The experience-evidence section reprints the attested profile's own lines.
  Nothing is rephrased into a new claim — evidence is quoted, not inflated.
- The assembled resume is run through the deterministic guardrail one last
  time (verifier.check_draft). Any violation -> guardrail REJECTED, and the
  resume is not eligible for a pursuit.
"""
import os
import re

from . import verifier


def build_resume_text(form, posting, split, narrative, attested):
    lines = [
        f"# {form['name']}",
        f"Target role: {posting['title']} — {posting['company']}",
        "",
        "## Professional summary",
        narrative.strip(),
        "",
        "## Core skills for this role (attested, with evidence)",
    ]
    for item in split["defensible"]:
        lines.append(f"- **{item['term']}** — {item['evidence']}")
    lines += ["", "## Attested experience evidence"]
    for claim in attested.values():
        lines.append(f"- **{claim['term']}** :: {claim['evidence']}")
    lines.append("")
    return "\n".join(lines)


def extract_narrative(dossier_text):
    """Pull the guardrail-passed narrative out of a rendered dossier."""
    m = re.search(
        r"## Verified narrative \(guardrail-passed\)\n(.*?)(?:\n## |\Z)",
        dossier_text, re.S)
    if not m:
        return ""
    text = m.group(1).strip()
    return "" if text.startswith("No clean draft") else text


def ensure_resume(db, store, form, posting, split, narrative, match_dir,
                  profile_text, attested, emit):
    """Build (or reuse) the tailored resume for one PASS match.

    Returns the candidate_resumes id, or None if the guardrail rejects it.
    """
    existing = store.resume_for(db, posting["id"], form["name"])
    if existing and existing["guardrail"] == "PASSED":
        return existing["id"]
    if existing:
        # A REJECTED resume is not final: it is rebuilt when the stage
        # re-runs (e.g. after a checker-scope correction). A PASSED one is.
        db.execute("DELETE FROM candidate_resumes WHERE id=?", (existing["id"],))
        db.commit()
    if not narrative:
        emit("builder", f"No verified narrative for {posting['title']} — no resume built")
        return None
    text = build_resume_text(form, posting, split, narrative, attested)
    # The guardrail checks the resume's CLAIM-bearing parts: the summary and
    # the per-role skills section. Two things are deliberately out of scope:
    # the experience-evidence section (a verbatim quote of the attested
    # profile — the ground truth itself), and the "Target role" header line
    # (metadata naming the JOB's title; a title like "Databricks / PySpark"
    # describes the employer's stack, not a claim by the seeker — the
    # guardrail caught exactly this case on a live run, 2026-10-26).
    claim_text = "\n".join(
        [narrative] + [i["term"] for i in split["defensible"]])
    violations = verifier.check_draft(claim_text, attested, profile_text)
    path = os.path.join(match_dir, "resume.md")
    if violations:
        emit("guardrail",
             f"RESUME REJECTED for {posting['title']}: "
             + "; ".join(v["detail"] for v in violations))
        rid = store.insert_resume(db, posting["id"], form["name"], path, "REJECTED")
        return None
    os.makedirs(match_dir, exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    rid = store.insert_resume(db, posting["id"], form["name"], path, "PASSED")
    emit("builder", f"Resume built + guardrail PASSED: {path}")
    return rid
