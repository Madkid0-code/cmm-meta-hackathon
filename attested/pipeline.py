"""End-to-end pipeline: JD + attested profile -> Evidence Dossier."""
import json
import os
from datetime import datetime, timezone

from . import builder, dossier, reader, verifier


def run(jd_path, profile_path, out_dir, should_stop=None):
    os.makedirs(out_dir, exist_ok=True)
    events_path = os.path.join(out_dir, "events.jsonl")
    events_file = open(events_path, "w")

    def emit(actor, message):
        event = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "actor": actor,
            "message": message,
        }
        print(f"[{actor.upper()}] {message}", flush=True)
        events_file.write(json.dumps(event) + "\n")
        events_file.flush()

    jd_text = open(jd_path).read()
    profile_text = open(profile_path).read()
    attested = verifier.parse_profile(profile_text)
    emit("system", f"Attested profile loaded: {len(attested)} claims")

    # Intake check: the seeker's own outgoing resume, audited by the same
    # deterministic guardrail before any agent runs.
    intake_audit = []
    raw_text = ""
    raw_resume_path = os.path.join(os.path.dirname(profile_path), "raw_resume.md")
    if os.path.exists(raw_resume_path):
        raw_text = open(raw_resume_path).read()
        emit("guardrail", "Intake check: auditing the resume the seeker was about to send")
        for v in verifier.check_draft(raw_text, attested, profile_text):
            emit(
                "guardrail",
                f"GUARDRAIL ALERT [intake:{v['type']}] {v['term']}: {v['detail']}",
            )
            intake_audit.append({"round": "intake", **v, "draft": raw_text})
        if intake_audit:
            emit(
                "guardrail",
                f"Intake REJECTED {len(intake_audit)} claim(s) in the outgoing resume",
            )

    data = reader.extract(jd_text, emit)
    constraints = data.get("core_constraints") or []

    split = verifier.verify_requirements(constraints, attested)
    emit(
        "guardrail",
        f"Verifier diff complete: {len(split['defensible'])} defensible, "
        f"{len(split['vulnerable'])} vulnerable",
    )
    for item in split["vulnerable"]:
        emit("guardrail", f"VULNERABLE: {item['term']} — no attested proof")

    if should_stop and should_stop():
        emit("system", "Stopped by the user — audit saved, no resume built.")
        events_file.close()
        return {"split": split, "audit": intake_audit, "stopped": True}
    build_result = builder.build(
        attested, profile_text, constraints, emit, raw_resume_text=raw_text
    )
    full_audit = intake_audit + build_result["audit"]
    drafts_dir = os.path.join(out_dir, "drafts")
    os.makedirs(drafts_dir, exist_ok=True)
    seen = set()
    for a in full_audit:
        name = f"rejected_round_{a['round']}.md"
        if name not in seen:
            seen.add(name)
            with open(os.path.join(drafts_dir, name), "w") as f:
                f.write(a["draft"])
    if build_result["draft"]:
        with open(os.path.join(drafts_dir, "accepted.md"), "w") as f:
            f.write(build_result["draft"])

    rendered = dossier.render(split, build_result, full_audit, attested=attested)
    with open(os.path.join(out_dir, "dossier.md"), "w") as f:
        f.write(rendered)
    emit("system", f"Dossier written to {out_dir}/dossier.md")
    events_file.close()
    return {"split": split, "build": build_result}
