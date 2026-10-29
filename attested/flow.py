"""The complete CMM flow, end to end:

    form -> sweep config -> SWEEPER -> raw -> PARSER -> postings
         -> keyword keep rule (>=2 match skills, production rule)
         -> READER (Muse Spark) -> VERIFIER (deterministic) -> matches
         -> BUILDER (Muse Spark, guarded) -> DOSSIER per top match
         -> CALL QUEUE: the human decides. The system never submits.

Each stage writes only its own table (store.py). The verdict logic is 100%
deterministic code; the LLMs propose, the code disposes.
"""
import json
import os
from datetime import datetime, timezone

from . import (applier, builder, dossier, parser, profile, reader, resume,
               store, sweeper, verifier)

READER_CAP = 5   # cost control: Reader (LLM) sees at most this many JDs per run
BUILD_TOP = 1    # dossiers built for this many top PASS matches per run


def _verdict_for(split, match_skills=()):
    """Deterministic match verdict — the production CMM rule, restated.

    Production verdict never demanded a majority of every line in a JD
    laundry list. A match is worth pursuing when the seeker's DEFENSIBLE
    claims cover the job's core pool skills (>=2 of the form's match skills
    required by the JD *and* attested) and at least 3 defensible claims
    overall. Every remaining gap is not hidden — it lands in the dossier
    as a Vulnerable claim with an Honest Pivot.
    """
    d, v = len(split["defensible"]), len(split["vulnerable"])
    def_terms = {verifier.canonical(x["term"]) for x in split["defensible"]}
    pool = [s for s in match_skills if verifier.canonical(s) in def_terms]
    if len(pool) >= 2 and d >= 3:
        return "PASS", (f"core pool skills defensible: {', '.join(pool)} "
                        f"({d} defensible / {v} vulnerable — gaps itemized in the dossier)")
    return "SKIP", (f"not enough provable overlap: {len(pool)} of your key "
                    f"skills matched, {d} provable claims total (a role needs "
                    f"2 key skills and 3+ provable claims), {v} gaps")


def run(form_path, out_dir, reader_cap=READER_CAP, build_top=BUILD_TOP,
        should_stop=None):
    os.makedirs(out_dir, exist_ok=True)
    events_file = open(os.path.join(out_dir, "events.jsonl"), "a")

    def stopped():
        return bool(should_stop and should_stop())

    def emit(actor, message):
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "actor": actor, "message": message}
        print(f"[{actor.upper()}] {message}", flush=True)
        events_file.write(json.dumps(event) + "\n")
        events_file.flush()

    form = profile.load_form(form_path)
    candidate = form["name"]
    profile_text = profile.render_profile_md(form)
    with open(os.path.join(out_dir, "profile_attested.md"), "w") as f:
        f.write(profile_text)
    attested = verifier.parse_profile(profile_text)
    emit("system", f"Form in: {candidate} — {len(attested)} attested claims")

    # Intake audit of the seeker's own outgoing resume, when the form names one.
    raw_text = ""
    raw_name = form.get("raw_resume_file")
    if raw_name:
        raw_path = os.path.join(os.path.dirname(os.path.abspath(form_path)), raw_name)
        if os.path.exists(raw_path):
            raw_text = open(raw_path).read()
            emit("guardrail", "Intake check: auditing the resume the seeker was about to send")
            for v in verifier.check_draft(raw_text, attested, profile_text):
                emit("guardrail", f"GUARDRAIL ALERT [intake:{v['type']}] {v['term']}: {v['detail']}")

    db = store.connect(os.path.join(out_dir, "attested.db"))
    config_id = store.add_config(db, form)
    config = db.execute("SELECT * FROM sweep_configs WHERE id=?", (config_id,)).fetchone()
    emit("shopper", f"Sweep config #{config_id}: keywords={form['search']['keywords']}")

    sweeper.sweep(db, store, config, emit)
    parser.parse_all(db, store, emit)

    match_skills = form["search"]["match_skills"]
    # The audit queue only draws from the sweep config's ACTIVE sources:
    # a source the seeker removed stops feeding the Reader, even though
    # its older postings stay in the store (owner's source cuts,
    # 2026-10-28 — the removed boards' backlog must not eat the cap).
    active_sources = set(json.loads(config["sources"]))
    pending = []
    for posting in store.postings_without_match(db, candidate):
        if posting["source"] not in active_sources:
            continue
        if len(posting["jd_text"] or "") < 200:
            continue
        hits = parser.keyword_hits(posting, match_skills)
        if len(hits) >= 2:  # production keep rule
            # Deterministic attested-coverage scan orders the Reader queue
            # (ordering only — the verdict itself comes from the full diff).
            found = verifier._draft_terms(posting["jd_text"] or "")
            coverage = len(set(found.keys()) & set(attested.keys()))
            pending.append((coverage, len(hits), posting))
    job_types = set(form["search"].get("job_types") or [])
    workplaces = set(form["search"].get("workplace") or [])
    if job_types or workplaces:
        def _type_ok(posting):
            t, w = parser.classify(posting["employment_type"],
                                   posting["location"], posting["source"],
                                   posting["jd_text"] or "")
            return ((not job_types or t == "unknown" or t in job_types)
                    and (not workplaces or w == "unknown"
                         or w in workplaces))
        before = len(pending)
        pending = [x for x in pending if _type_ok(x[2])]
        emit("verdict",
             f"Type filter: {before - len(pending)} posting(s) set aside "
             f"(job type: {', '.join(sorted(job_types)) or 'any'}; "
             f"workplace: {', '.join(sorted(workplaces)) or 'any'}; "
             f"postings whose type is unstated are kept)")
    pending = [p for _, _, p in sorted(pending, key=lambda x: (-x[0], -x[1]))]
    emit("verdict", f"Keep rule: {len(pending)} postings match >=2 skills; "
                    f"Reader will process top {min(reader_cap, len(pending))}")

    built = []
    for posting in pending[:reader_cap]:
        if stopped():
            emit("system", "Stopped by the user — everything built so far "
                           "is saved. Scan again anytime to continue.")
            break
        data = reader.extract(posting["jd_text"], emit)
        constraints = data.get("core_constraints") or []
        split = verifier.verify_requirements(constraints, attested)
        verdict, reason = _verdict_for(split, match_skills)
        store.insert_match(db, posting["id"], candidate, verdict,
                           len(split["defensible"]), len(split["vulnerable"]),
                           {"split": split, "reason": reason})
        emit("verdict", f"{verdict}: {posting['title']} @ {posting['company']} — {reason}")
        if verdict == "PASS" and len(built) < build_top:
            build_result = builder.build(
                attested, profile_text, constraints, emit, raw_resume_text=raw_text)
            match_dir = os.path.join(out_dir, "matches", str(posting["id"]))
            os.makedirs(match_dir, exist_ok=True)
            rendered = dossier.render(split, build_result, build_result["audit"],
                                      attested=attested)
            header = (f"# {posting['title']} — {posting['company']}\n"
                      f"Source: {posting['source']} · {posting['apply_url']}\n\n")
            with open(os.path.join(match_dir, "dossier.md"), "w") as f:
                f.write(header + rendered)
            resume.ensure_resume(db, store, form, posting, split,
                                 build_result["draft"], match_dir,
                                 profile_text, attested, emit)
            built.append({"posting_id": posting["id"], "title": posting["title"],
                          "company": posting["company"], "source": posting["source"],
                          "apply_url": posting["apply_url"], "dir": match_dir})
            emit("system", f"Dossier written: {match_dir}/dossier.md")

    queued = finish_pursuits(db, form, out_dir, emit)
    judged = db.execute(
        "SELECT COUNT(*) c FROM candidate_matches WHERE candidate=?",
        (candidate,)).fetchone()["c"]
    emit("system", f"Call queue written ({judged} judged, {queued} pursuits queued)")
    events_file.close()
    return {"built": built, "judged": judged}


def finish_pursuits(db, form, out_dir, emit):
    """Applier stage end-of-run: dispatch pursuits for eligible matches,
    prepare their application packages, and render the call queue."""
    queued = applier.dispatch(db, store, form["name"], emit)
    for pursuit in store.pursuits_for(db, form["name"]):
        if pursuit["status"] == "ready":
            match_dir = os.path.join(out_dir, "matches", str(pursuit["posting_id"]))
            applier.prepare_package(db, store, pursuit, form, match_dir, emit)
    queue_path = os.path.join(out_dir, "call_queue.md")
    with open(queue_path, "w") as f:
        f.write(applier.render_queue(db, form["name"]))
    return queued


def rejudge_and_build(form_path, out_dir, build_top=BUILD_TOP,
                      should_stop=None):
    """Re-run ONLY the verdict stage over already-stored Reader splits.

    The verdict is deterministic, so when its rule is refined the stage can
    be replayed from stored splits with zero new LLM calls — then dossiers
    are built for the top PASS matches that lack one. (The verdict stage
    owns candidate_matches; replaying it rewrites its own rows.)
    """
    import sqlite3

    events_file = open(os.path.join(out_dir, "events.jsonl"), "a")

    def emit(actor, message):
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "actor": actor, "message": message}
        print(f"[{actor.upper()}] {message}", flush=True)
        events_file.write(json.dumps(event) + "\n")
        events_file.flush()

    form = profile.load_form(form_path)
    candidate = form["name"]
    match_skills = form["search"]["match_skills"]
    profile_text = profile.render_profile_md(form)
    attested = verifier.parse_profile(profile_text)

    db = store.connect(os.path.join(out_dir, "attested.db"))
    rows = db.execute(
        "SELECT m.id, m.posting_id, m.detail, p.title, p.company, p.source,"
        " p.apply_url FROM candidate_matches m"
        " JOIN job_postings p ON p.id = m.posting_id WHERE m.candidate=?",
        (candidate,)).fetchall()
    built = []
    for row in rows:
        if should_stop and should_stop():
            emit("system", "Stopped by the user — builds so far are saved.")
            break
        detail = json.loads(row["detail"])
        split = detail["split"]
        verdict, reason = _verdict_for(split, match_skills)
        detail["reason"] = reason
        db.execute("UPDATE candidate_matches SET verdict=?, defensible=?,"
                   " vulnerable=?, detail=? WHERE id=?",
                   (verdict, len(split["defensible"]), len(split["vulnerable"]),
                    json.dumps(detail), row["id"]))
        db.commit()
        emit("verdict", f"REJUDGED {verdict}: {row['title']} @ {row['company']} — {reason}")
        if verdict == "PASS" and len(built) < build_top:
            match_dir = os.path.join(out_dir, "matches", str(row["posting_id"]))
            if os.path.exists(os.path.join(match_dir, "dossier.md")):
                continue
            constraints = (
                [{"term": x["term"], "evidence": x.get("jd_evidence", "")}
                 for x in split["defensible"]]
                + [{"term": x["term"], "evidence": x.get("jd_evidence", "")}
                   for x in split["vulnerable"]])
            build_result = builder.build(attested, profile_text, constraints, emit)
            os.makedirs(match_dir, exist_ok=True)
            rendered = dossier.render(split, build_result, build_result["audit"],
                                      attested=attested)
            header = (f"# {row['title']} — {row['company']}\n"
                      f"Source: {row['source']} · {row['apply_url']}\n\n")
            with open(os.path.join(match_dir, "dossier.md"), "w") as f:
                f.write(header + rendered)
            built.append({"posting_id": row["posting_id"], "title": row["title"],
                          "company": row["company"]})
            emit("system", f"Dossier written: {match_dir}/dossier.md")

    # Resume backfill: PASS matches that have a dossier but no resume yet
    # get their tailored resume built from the dossier's verified narrative.
    for row in rows:
        split = json.loads(row["detail"])["split"]
        verdict, _ = _verdict_for(split, match_skills)
        existing_resume = store.resume_for(db, row["posting_id"], candidate)
        if verdict != "PASS" or (existing_resume
                                  and existing_resume["guardrail"] == "PASSED"):
            continue
        match_dir = os.path.join(out_dir, "matches", str(row["posting_id"]))
        dossier_path = os.path.join(match_dir, "dossier.md")
        if not os.path.exists(dossier_path):
            continue
        narrative = resume.extract_narrative(open(dossier_path).read())
        posting = {"id": row["posting_id"], "title": row["title"],
                   "company": row["company"]}
        resume.ensure_resume(db, store, form, posting, split, narrative,
                             match_dir, profile_text, attested, emit)

    queued = finish_pursuits(db, form, out_dir, emit)
    emit("system", f"Call queue written ({len(rows)} judged, {queued} pursuits queued)")
    events_file.close()
    return {"built": built, "judged": len(rows)}
