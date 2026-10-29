"""Applier stage: candidate_resumes -> pursuits (the outcome loop).

Mirrors the production CMM applier dispatcher:
- A pursuit is queued ONLY for a PASS match that has a guardrail-passed
  built resume. No resume -> not eligible, exactly like production.
- Each pursuit carries its route (from the posting's source), its resume,
  and its apply URL. Status lifecycle: ready -> prepared -> applied /
  failed / expired. Outcomes are recorded with their evidence.

The submission click itself is the human's. This public build assembles
the complete application package — tailored resume, attested answers,
target URL — and tracks the pursuit to its outcome. It never enters
credentials and never submits as the seeker: the demo persona is
fictional, and applying as a fictional person to real employers would be
fraud. In the production system this same stage hands off to signed-in,
per-route appliers running on the seeker's own accounts.
"""
import os

ROUTES = {
    "dice": "dice",
    "linkedin_guest": "linkedin",
    "remoteok": "board",
    "arbeitnow": "board",
}


def route_for(source):
    return ROUTES.get(source, "company_portal")


def dispatch(db, store, candidate, emit):
    """Queue pursuits for every eligible match. Returns count queued."""
    rows = db.execute(
        "SELECT m.posting_id, p.source, p.apply_url FROM candidate_matches m"
        " JOIN job_postings p ON p.id = m.posting_id"
        " WHERE m.candidate=? AND m.verdict='PASS'", (candidate,)).fetchall()
    queued = 0
    for row in rows:
        resume = store.resume_for(db, row["posting_id"], candidate)
        if not resume or resume["guardrail"] != "PASSED":
            continue  # production rule: no built resume -> not eligible
        pid = store.insert_pursuit(
            db, row["posting_id"], candidate, route_for(row["source"]),
            resume["id"], row["apply_url"])
        if pid:
            queued += 1
            emit("applier", f"Pursuit #{pid} queued ({route_for(row['source'])}) "
                            f"for posting {row['posting_id']}")
    return queued


def prepare_package(db, store, pursuit, form, match_dir, emit):
    """Assemble the application package for one ready pursuit."""
    if pursuit["status"] != "ready":
        return None
    resume_path = os.path.join(match_dir, "resume.md")
    lines = [
        f"# Application package — pursuit #{pursuit['id']}",
        f"Role: {pursuit['title']} @ {pursuit['company']} (route: {pursuit['route']})",
        f"Apply at: {pursuit['apply_url']}",
        "",
        "## Attested answers (from the seeker's form — nothing invented)",
        f"- Name: {form['name']}",
        "- Attested skills: " + ", ".join(
            s["term"] for s in form["attested_skills"]),
        "",
        "## Tailored resume",
        f"Attach: {resume_path}",
        "",
        "## Submission",
        "The seeker opens the apply link, submits with the attached resume,",
        "and records the outcome here (applied + the confirmation text,",
        "failed, or expired). The system tracks; the human submits.",
    ]
    path = os.path.join(match_dir, "application_package.md")
    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")
    store.set_pursuit_status(db, pursuit["id"], "prepared")
    emit("applier", f"Pursuit #{pursuit['id']} prepared: {path}")
    return path


def mark_applied(db, store, pursuit_id, confirmation):
    """Record a human-confirmed application with its quoted evidence."""
    store.set_pursuit_status(db, pursuit_id, "applied", detail=confirmation)


def render_queue(db, candidate):
    """The call queue: pursuits + judged matches, human-readable."""
    lines = [f"# Call queue for {candidate} — pursuits and verdicts", ""]
    pursuits = db.execute(
        "SELECT pu.*, p.title, p.company, p.source, m.defensible, m.vulnerable"
        " FROM pursuits pu JOIN job_postings p ON p.id = pu.posting_id"
        " JOIN candidate_matches m ON m.posting_id = pu.posting_id"
        " AND m.candidate = pu.candidate WHERE pu.candidate=?"
        " ORDER BY pu.id", (candidate,)).fetchall()
    if pursuits:
        lines.append("## Pursuits (PASS + built resume — ready for the human's call)")
        for r in pursuits:
            lines.append(
                f"- [#{r['id']} {r['status']}] {r['title']} @ {r['company']} "
                f"({r['route']}) — {r['defensible']} defensible / "
                f"{r['vulnerable']} vulnerable — {r['apply_url']}")
        lines.append("")
    rest = db.execute(
        "SELECT p.title, p.company, p.source, p.apply_url, m.verdict,"
        " m.defensible, m.vulnerable FROM candidate_matches m"
        " JOIN job_postings p ON p.id = m.posting_id"
        " WHERE m.candidate=? AND m.verdict='SKIP'"
        " ORDER BY m.defensible DESC", (candidate,)).fetchall()
    if rest:
        lines.append("## Judged SKIP (no pursuit — core overlap too thin)")
        for r in rest:
            lines.append(
                f"- [SKIP] {r['title']} @ {r['company']} ({r['source']}) — "
                f"{r['defensible']} defensible / {r['vulnerable']} vulnerable")
        lines.append("")
    return "\n".join(lines)
