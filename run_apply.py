#!/usr/bin/env python3
"""Pursuit tracker — the outcome loop.

Usage:
  python3 run_apply.py [out_dir]                 list pursuits
  python3 run_apply.py [out_dir] applied <id> "confirmation text"
  python3 run_apply.py [out_dir] failed <id> "reason"
  python3 run_apply.py [out_dir] expired <id>

Outcomes are recorded with their evidence (the quoted confirmation),
exactly like the production pursuits loop.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from attested import applier, store  # noqa: E402

if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "examples_out/flow"
    db = store.connect(os.path.join(out, "attested.db"))
    cmd = sys.argv[2] if len(sys.argv) > 2 else "list"
    if cmd in ("applied", "failed", "expired") and len(sys.argv) > 3:
        pid = int(sys.argv[3])
        detail = sys.argv[4] if len(sys.argv) > 4 else ""
        if cmd == "applied":
            applier.mark_applied(db, store, pid, detail)
        else:
            store.set_pursuit_status(db, pid, cmd, detail or None)
        print(f"Pursuit #{pid} -> {cmd}")
    rows = db.execute(
        "SELECT pu.id, pu.route, pu.status, pu.detail, p.title, p.company"
        " FROM pursuits pu JOIN job_postings p ON p.id = pu.posting_id"
        " ORDER BY pu.id").fetchall()
    if not rows:
        print("No pursuits yet — run the flow first.")
    for r in rows:
        print(f"#{r['id']} [{r['status']}] ({r['route']}) "
              f"{r['title']} @ {r['company']}"
              + (f" — {r['detail']}" if r["detail"] else ""))
