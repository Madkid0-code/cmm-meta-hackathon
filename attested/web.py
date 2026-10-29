"""Attested web UI — Mission Control over the complete flow.

Stdlib-only (http.server), one page, no external assets. The UI adds NO
state of its own: every number comes from the pipeline's own store and
files, so what a judge sees is what the pipeline actually did.

Layout (owner-directed, 2026-10-28 — "Tend" clinic-CRM style from the
reference reel he sent: dark teal sidebar, pale mint canvas, white
rounded cards, pill buttons, calm motion):
- Sidebar: brand, nav (Today / Your pipeline / Live Market Targets /
  Your profile / Safety check), the honesty-rule card, candidate chip.
- Main: greeting header, five big-number stat cards, then the board —
  four stage columns (Found -> Checking -> Safe to apply -> Ready for
  you). The plain-English story steps render as cards in the stage
  where they happened, and PASS roles render as job cards (pastel
  company avatar, honest split, one pill action). The raw technical
  log stays available, collapsed, under the board.
- Below: Live Market Targets (every audited role; a row or card opens
  the detail drawer — Dossier claim cards, Resume, Application
  Package) beside the Attested Profile + Profile Safety Check card.
"""
import html as html_mod
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import meta_client, profile, verifier

ET = ZoneInfo("America/New_York")
RANGE_KEYS = ("today", "yesterday", "month", "all")
RANGE_LABELS = {"today": "Today", "yesterday": "Yesterday",
                "month": "This month", "all": "All time"}


def range_window(key, now=None):
    """(start, end) UTC ISO bounds for a stats range, on ET calendar days.

    'today' starts at midnight ET *by construction* — the daily reset the
    owner asked for needs no cron and deletes nothing: at 12:00am ET the
    window simply rolls to the new day, and older days stay reachable
    through the Yesterday / This month / All time filters. Bounds are
    [start, end) so a row lands in exactly one day.
    """
    if key not in RANGE_KEYS:
        key = "all"
    if key == "all":
        return None, None
    now = now or datetime.now(ET)
    today = now.date()
    if key == "yesterday":
        start_d, end_d = today - timedelta(days=1), today
    elif key == "month":
        start_d, end_d = today.replace(day=1), today + timedelta(days=1)
    else:  # today
        start_d, end_d = today, today + timedelta(days=1)

    def bound(d):
        return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(
            timezone.utc).isoformat(timespec="seconds")

    return bound(start_d), bound(end_d)

ACTOR_COLORS = {
    "system": "#9caab8", "shopper": "#60a5fa", "sweeper": "#2dd4bf",
    "parser": "#2dd4bf", "reader": "#60a5fa", "guardrail": "#f87171",
    "verdict": "#fbbf24", "builder": "#4ade80", "applier": "#c084fc",
}


# ---------------------------------------------------------------- md-lite

def md_lite(text):
    """Render our own markdown subset to HTML. Escapes first — safe by default."""
    out, in_list = [], False
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("- "):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(line[2:])}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if line.startswith("### "):
            out.append(f"<h4>{_inline(line[4:])}</h4>")
        elif line.startswith("## "):
            out.append(f"<h3>{_inline(line[3:])}</h3>")
        elif line.startswith("# "):
            out.append(f"<h2>{_inline(line[2:])}</h2>")
        elif line.strip():
            out.append(f"<p>{_inline(line)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _inline(text):
    esc = html_mod.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)


def parse_audit(dossier_text):
    """Extract audit-trail entries from a rendered dossier."""
    entries = []
    in_audit = False
    for line in dossier_text.splitlines():
        if line.startswith("## Audit trail"):
            in_audit = True
            continue
        if in_audit:
            if line.startswith("## "):
                break
            m = re.match(r"- .*?\[(.+?)\] \*\*(.+?)\*\* — (.+)", line)
            if m:
                entries.append({"type": m.group(1), "term": m.group(2),
                                "detail": m.group(3)})
    return entries


def _read_events(path, limit=90):
    if not os.path.exists(path):
        return []
    events = []
    with open(path) as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events[-limit:]


# ------------------------------------------------------- human feed

SOURCE_NAMES = {"dice": "Dice", "remoteok": "RemoteOK",
                "arbeitnow": "Arbeitnow", "linkedin_guest": "LinkedIn",
                "linkedin": "LinkedIn"}


def human_feed(events):
    """Translate the raw event log into a plain-English story a person
    can read top to bottom: one step per thing that actually matters,
    in the order it happened. Token counts, streaming ticks, file paths
    and other machine noise are dropped — the raw log stays on disk as
    the audit trail; this is the human layer over it."""
    steps = []

    def add(icon, tone, title, text, ts):
        steps.append({"icon": icon, "tone": tone, "title": title,
                      "text": text, "ts": ts})

    for e in events:
        m = e.get("message") or ""
        ts = e.get("ts") or ""
        low = m.lower()
        if ("reasoning in progress" in low or "reasoning tokens" in low
                or "structured extraction" in low
                or "extracted " in low and "core constraints" in low
                or "builder agent drafting" in low
                or low.startswith("vulnerable:")
                or "unknown source" in low and "doctor" in low):
            continue
        mm = re.match(r"Form in: (.+?) — (\d+) attested claims", m)
        if mm:
            add("👋", "", f"Your profile is in, {mm.group(1)}",
                f"{mm.group(2)} skills, each with your proof behind it. "
                "Every job below is checked against exactly these — "
                "nothing more, nothing less.", ts)
            continue
        mm = re.match(r"Attested profile loaded: (\d+) claims", m)
        if mm:
            add("👋", "", "Profile loaded",
                f"{mm.group(1)} attested claims — the only facts the "
                "machine is allowed to use.", ts)
            continue
        if m.startswith("Intake check:"):
            add("🛡️", "", "First, your own resume gets checked",
                "Before hunting, the machine audits the resume you were "
                "about to send — so your own overclaims get caught at "
                "home, not in an interview.", ts)
            continue
        mm = re.match(r"GUARDRAIL ALERT \[intake:[^]]+\] (.+?):", m)
        if mm:
            add("🛡️", "warn", f"Kept out of your resume: {mm.group(1)}",
                "It was in your draft, but it isn't backed by your "
                "attested profile — so it never reaches an application.", ts)
            continue
        if m.startswith("Intake REJECTED"):
            continue  # the individual kept-out steps above tell this story
        mm = re.match(r"GUARDRAIL ALERT \[baseline:[^]]+\] (.+?):", m)
        if mm:
            add("🛡️", "warn", f"The normal AI draft tried to add: {mm.group(1)}",
                "A conventional resume writer put this in. The guardrail "
                "caught it — it is not in your attested profile.", ts)
            continue
        mm = re.match(r"Baseline REJECTED with (\d+)", m)
        if mm:
            add("🛡️", "warn", "That is what ships without a guardrail",
                f"The comparison draft was rejected with {mm.group(1)} "
                "invented or unprovable claim(s). Your real resume is "
                "built under the guardrail instead.", ts)
            continue
        mm = re.match(r"GUARDRAIL ALERT \[[^]]+\] (.+?):", m)
        if mm:
            add("🛡️", "warn", f"Blocked and rewritten: {mm.group(1)}",
                "The builder's draft claimed this, but you can't prove "
                "it — the draft was sent back and rewritten without it.", ts)
            continue
        if m.startswith("Execution halted"):
            continue
        if m.startswith("Sweep config"):
            kws = re.findall(r"'([^']+)'", m.split("keywords=", 1)[-1])
            add("🔎", "", "The hunt starts",
                "Searching for: " + "  ·  ".join(kws), ts)
            continue
        mm = re.match(r"(\w+): (\d+) fetched, (\d+) new raw postings", m)
        if mm:
            src = SOURCE_NAMES.get(mm.group(1), mm.group(1))
            add("🔎", "", f"Found {mm.group(2)} jobs on {src}",
                (f"{mm.group(3)} of them are new — the rest were already "
                 "in the pile from earlier scans." if mm.group(3) != mm.group(2)
                 else "All of them are new."), ts)
            continue
        mm = re.search(r"source (\w+) unavailable", m)
        if mm:
            add("🔎", "warn",
                f"{SOURCE_NAMES.get(mm.group(1), mm.group(1))} didn't answer",
                "That job board was unavailable — the hunt carried on "
                "with the others.", ts)
            continue
        mm = re.match(r"parsed (\d+) postings \((\d+)[^,]*dupes, (\d+) thin", m)
        if mm:
            extra = (f" and {mm.group(3)} postings with almost no "
                     "description" if int(mm.group(3)) else "")
            add("🧩", "", "Cleaned up the pile",
                f"{mm.group(1)} real postings left after removing "
                f"{mm.group(2)} duplicates{extra}.", ts)
            continue
        mm = re.match(r"Type filter: (\d+) posting\(s\) set aside", m)
        if mm:
            add("🎚️", "", "Filtered to what you asked for",
                f"{mm.group(1)} postings set aside — wrong job type or "
                "workplace for the choices on your form.", ts)
            continue
        mm = re.match(r"Keep rule: (\d+) postings match .*?top (\d+)", m)
        if mm:
            add("🎯", "", "Only close matches get a close look",
                f"{mm.group(1)} postings ask for at least 2 of your key "
                f"skills. The machine reads the top {mm.group(2)} in full "
                "and checks every requirement against your proof. "
                "Everything else is out — no maybes.", ts)
            continue
        mm = re.match(r"(?:REJUDGED )?PASS: (.+?) @ (.+?) — core pool skills "
                      r"defensible: (.+?) \((\d+) defensible / (\d+) vulnerable", m)
        if mm:
            title, comp, pool, d, v = mm.groups()
            stretch = (". Heads-up: this one is a stretch — the gaps "
                       "outnumber the strengths, so read its dossier "
                       "before you decide."
                       if int(v) >= 3 * max(int(d), 1) else "")
            add("✅", "good", f"Worth your time: {title} — {comp}",
                f"You can honestly prove: {pool} — {d} claims in total "
                f"backed by your evidence. The job also asks for {v} "
                "things you can't prove yet; they are listed openly as "
                f"gaps in its dossier, not hidden{stretch}", ts)
            continue
        mm = re.match(r"(?:REJUDGED )?SKIP: (.+?) @ (.+?) — .*?"
                      r"(\d+) (?:pool skill\(s\) defensible|of your key skills matched), "
                      r"(\d+) (?:defensible|provable claims total).*?(\d+) (?:vulnerable|gaps)", m)
        if mm:
            title, comp, pool, d, v = mm.groups()
            add("⏭️", "", f"Skipped: {title} — {comp}",
                f"Not enough you can prove: {pool} of your key skills "
                f"matched and only {d} provable claims in total (a role "
                f"needs at least 2 key skills and 3 provable claims). "
                f"{v} of its requirements would have been gaps.", ts)
            continue
        mm = re.match(r"Verifier diff complete: (\d+) defensible, (\d+) vulnerable", m)
        if mm:
            add("⚖️", "", "The check is done",
                f"{mm.group(1)} requirements you can prove, "
                f"{mm.group(2)} you can't — the full split is in the "
                "dossier.", ts)
            continue
        if m.startswith("Baseline draft:"):
            add("✍️", "", "First, a normal AI draft — for comparison",
                "An unguarded AI writes a resume the usual way, so you "
                "can see exactly what the guardrail protects you from.", ts)
            continue
        if m.startswith("Builder ingesting the raw"):
            add("✍️", "", "Building yours from your draft — honestly",
                "Your real resume starts from your own draft, keeping "
                "only the claims you can prove.", ts)
            continue
        if m.startswith("GUARDRAIL PASSED"):
            add("✅", "good", "Resume checked — every claim is provable",
                "The guardrail read the finished draft line by line. "
                "Nothing in it goes beyond what you attested.", ts)
            continue
        mm = re.match(r"GUARDRAIL FAILED", m)
        if mm:
            add("🛡️", "warn", "No honest resume could be built for this one",
                "The builder could not produce a draft that stayed "
                "within your attested claims, so nothing was shipped.", ts)
            continue
        mm = re.match(r"RESUME REJECTED for (.+?):", m)
        if mm:
            add("🛡️", "warn", f"Resume held back: {mm.group(1)}",
                "The finished resume failed the honesty check, so it "
                "was not kept. Better no resume than one you can't defend.", ts)
            continue
        mm = re.match(r"No verified narrative for (.+?) —", m)
        if mm:
            add("✍️", "", f"No resume built for {mm.group(1)}",
                "There was no verified story to build from, so the "
                "machine built nothing rather than inventing one.", ts)
            continue
        if m.startswith("Resume built"):
            add("✍️", "good", "Your tailored resume is ready",
                "Built for this role only, from your attested claims — "
                "open the role below to read it or download it.", ts)
            continue
        if m.startswith("Dossier written"):
            continue  # covered by the resume / verdict steps
        mm = re.match(r"Pursuit #(\d+) queued", m)
        if mm:
            add("📮", "good", f"Application #{mm.group(1)} prepared for you",
                "Resume and answers are packaged and waiting in Live "
                "Market Targets below. Nothing has been submitted — "
                "the final click is yours.", ts)
            continue
        if re.match(r"Pursuit #\d+ prepared:", m):
            continue  # same story as the queued step
        mm = re.match(r"Call queue written \((\d+) judged, (\d+) pursuits queued\)", m)
        if mm:
            add("🏁", "good", "Done — the rest is your call",
                f"{mm.group(1)} roles judged, {mm.group(2)} application(s) "
                "prepared and waiting for your review. Nothing was "
                "submitted on your behalf.", ts)
            continue
        if m.startswith("Stopped by the user"):
            add("⏹️", "", "Stopped — everything so far is saved",
                "What was already found and built stays. Scan again "
                "anytime and it continues from here.", ts)
            continue
        # Anything unrecognised keeps its own words, minus file paths.
        text = re.sub(r"\S*matches/\S+", "the role's folder", m)
        add("•", "", text if len(text) <= 140 else text[:137] + "…", "", ts)
    return steps


# ---------------------------------------------------------------- state

KNOWN_SOURCES = ("dice", "remoteok", "arbeitnow", "linkedin_guest")


class UI:
    def __init__(self, out_dir, form_path, demo_dir, fixture_out):
        self.out_dir = out_dir
        self.form_path = form_path
        self.demo_dir = demo_dir
        self.fixture_out = fixture_out
        self._home_out = out_dir
        self._home_form = form_path
        self._rides_root = os.path.join(
            os.path.dirname(os.path.abspath(out_dir)), "rides")
        self._ride_active = False
        self._lock = threading.Lock()
        self._running = {"sweep": False, "fixture": False}
        self._stop_req = {"sweep": False, "fixture": False}
        self._stop_mode = "keep"
        self._run_ctx = {}

    # -- runs ------------------------------------------------------
    def readonly(self):
        # ATTESTED_READONLY=1: view-only serving (e.g. a shared preview
        # link) — state and documents render, runs are refused server-side.
        return bool(os.environ.get("ATTESTED_READONLY"))

    def can_run(self):
        if self.readonly():
            return False
        try:
            meta_client._api_key()
            return True
        except Exception:
            return False

    def _snapshot_ids(self):
        snap = {"candidate_matches": 0, "candidate_resumes": 0, "pursuits": 0}
        db_path = os.path.join(self.out_dir, "attested.db")
        if os.path.exists(db_path):
            db = sqlite3.connect(db_path)
            for table in snap:
                try:
                    snap[table] = db.execute(
                        f"SELECT COALESCE(MAX(id), 0) FROM {table}"
                    ).fetchone()[0]
                except sqlite3.Error:
                    pass
            db.close()
        return snap

    def _discard_since(self, snap, candidate):
        """Undo a stopped run on an existing store: delete only the rows
        this run created (ids above the snapshot) and their build dirs."""
        import shutil
        db_path = os.path.join(self.out_dir, "attested.db")
        if not os.path.exists(db_path):
            return
        db = sqlite3.connect(db_path)
        posting_ids = [r[0] for r in db.execute(
            "SELECT posting_id FROM candidate_matches WHERE id > ? "
            "AND candidate = ?", (snap["candidate_matches"], candidate))]
        db.execute("DELETE FROM pursuits WHERE id > ?",
                   (snap["pursuits"],))
        db.execute("DELETE FROM candidate_resumes WHERE id > ?",
                   (snap["candidate_resumes"],))
        db.execute("DELETE FROM candidate_matches WHERE id > ? "
                   "AND candidate = ?",
                   (snap["candidate_matches"], candidate))
        db.commit()
        db.close()
        for pid in posting_ids:
            shutil.rmtree(os.path.join(self.out_dir, "matches", str(pid)),
                          ignore_errors=True)

    def _finish_run(self, kind):
        """Thread epilogue: apply a pending discard, reset stop state."""
        discard = self._stop_req[kind] and self._stop_mode == "discard"
        ctx = dict(self._run_ctx)
        with self._lock:
            self._running[kind] = False
            self._stop_req[kind] = False
            self._stop_mode = "keep"
        if discard:
            if ctx.get("kind") == "ride" and ctx.get("ride_dir"):
                import shutil
                shutil.rmtree(ctx["ride_dir"], ignore_errors=True)
                self.go_home()
            elif ctx.get("kind") == "sweep":
                self._discard_since(ctx["snap"], ctx["candidate"])

    def _append_event(self, actor, message):
        event = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "actor": actor, "message": message}
        with open(os.path.join(self.out_dir, "events.jsonl"), "a") as f:
            f.write(json.dumps(event) + "\n")

    def request_apply(self, posting_id):
        """The website's Apply button: queue a prepared pursuit for the
        applier worker. The submission itself happens on the seeker's own
        Dice account (production model); progress lands in this store's
        event feed, where the dashboard shows it."""
        if self.readonly():
            return False, "View-only preview — applying is disabled."
        db_path = os.path.join(self.out_dir, "attested.db")
        if not os.path.exists(db_path):
            return False, "No application prepared for this role."
        db = sqlite3.connect(db_path)
        row = db.execute(
            "SELECT id, status FROM pursuits WHERE posting_id = ? "
            "ORDER BY id DESC LIMIT 1", (posting_id,)).fetchone()
        if row is None:
            db.close()
            return False, "No application prepared for this role."
        if row[1] != "prepared":
            db.close()
            return False, f"This application is already '{row[1]}'."
        db.execute("UPDATE pursuits SET status = 'apply_requested', "
                   "updated_at = ? WHERE id = ?",
                   (datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    row[0]))
        db.commit()
        db.close()
        self._append_event(
            "applier",
            "Apply pressed on the website — the applier is picking this "
            "up and will submit it on the seeker's Dice account.")
        return True, "apply_requested"

    def start_build_missing(self):
        """Build dossiers + resumes + pursuits for PASS matches that do
        not have one yet (rejudge_and_build: zero new Reader calls)."""
        from . import flow
        if self.readonly():
            return False
        with self._lock:
            if self._running["sweep"]:
                return False
            self._running["sweep"] = True
            self._stop_req["sweep"] = False
        form = profile.load_form(self.form_path)
        self._run_ctx = {"kind": "sweep", "snap": self._snapshot_ids(),
                         "candidate": form["name"]}

        def go():
            try:
                flow.rejudge_and_build(
                    self.form_path, self.out_dir, build_top=99,
                    should_stop=lambda: self._stop_req["sweep"])
            finally:
                self._finish_run("sweep")
        threading.Thread(target=go, daemon=True).start()
        return True

    def request_stop(self, mode):
        """Ask the active run to stop. mode: 'keep' (results so far stay)
        or 'discard' (this run's results are removed when it halts)."""
        if mode not in ("keep", "discard"):
            mode = "keep"
        for kind in ("sweep", "fixture"):
            if self._running[kind]:
                self._stop_mode = mode
                self._stop_req[kind] = True
                return True
        return False

    def start_sweep(self):
        from . import flow
        if self.readonly():
            return False
        with self._lock:
            if self._running["sweep"]:
                return False
            self._running["sweep"] = True
            self._stop_req["sweep"] = False
        form = profile.load_form(self.form_path)
        self._run_ctx = {"kind": "sweep", "snap": self._snapshot_ids(),
                         "candidate": form["name"]}

        def go():
            try:
                flow.run(self.form_path, self.out_dir,
                         should_stop=lambda: self._stop_req["sweep"])
            finally:
                self._finish_run("sweep")
        threading.Thread(target=go, daemon=True).start()
        return True

    @staticmethod
    def _form_from_payload(payload):
        """Validate an onboarding/profile payload into a form dict.
        Returns (form, None) or (None, error-for-the-form). Shared by the
        ride (a new person's form) and the profile editor (the same person
        updating their profile + sweep), so both obey identical rules."""
        name = str(payload.get("name") or "").strip()
        if not name:
            return None, "Please add your name."
        skills = []
        for row in payload.get("attested_skills") or []:
            term = str(row.get("term") or "").strip()
            if term:
                skills.append({"term": term,
                               "evidence": str(row.get("evidence") or "").strip()})
        if not skills:
            return None, "Add at least one skill you can honestly claim."
        search = payload.get("search") or {}
        keywords = [str(k).strip() for k in search.get("keywords") or []
                    if str(k).strip()]
        match_skills = [str(k).strip().lower() for k in
                        search.get("match_skills") or [] if str(k).strip()]
        if not keywords:
            return None, "Add at least one search phrase (e.g. 'data engineer python')."
        if len(match_skills) < 2:
            return None, "Pick at least 2 match skills — a role stays in only if it asks for 2+ of them."
        sources = [s for s in (search.get("sources") or []) if s in KNOWN_SOURCES]
        if not sources:
            sources = list(KNOWN_SOURCES)
        boundaries = [str(b).strip() for b in payload.get("hard_boundaries") or []
                      if str(b).strip()]
        form = {
            "name": name,
            "attested_skills": skills,
            "hard_boundaries": boundaries,
            "search": {
                "keywords": keywords,
                "match_skills": match_skills,
                "location": str(search.get("location") or "United States").strip(),
                "job_types": [t for t in (search.get("job_types") or [])
                              if t in ("contract", "full_time", "part_time")],
                "workplace": [w for w in (search.get("workplace") or [])
                              if w in ("remote", "hybrid", "onsite")],
                "contract_only": (search.get("job_types") == ["contract"]
                                  or bool(search.get("contract_only", False))),
                "posted_within_hours": int(search.get("posted_within_hours") or 24),
                "sources": sources,
            },
        }
        return form, None

    def update_profile(self, payload):
        """The Your-profile tab's Save: rewrite the CURRENT form in place
        (attested skills, hard boundaries, and the whole sweep spec), and
        re-render the attested profile beside the store, so the very next
        Scan for New Roles shops with the new settings. Nothing already
        found, built, or applied is touched. Preserves form keys the
        editor does not manage (note, raw_resume_file)."""
        if self.readonly():
            return False, "View-only preview — profile editing is disabled."
        form, err = self._form_from_payload(payload)
        if form is None:
            return False, err
        try:
            current = profile.load_form(self.form_path)
        except Exception:
            current = {}
        for key, value in current.items():
            form.setdefault(key, value)
        with open(self.form_path, "w") as f:
            json.dump(form, f, indent=1)
        with open(os.path.join(self.out_dir, "profile_attested.md"), "w") as f:
            f.write(profile.render_profile_md(form))
        self._append_event(
            "system",
            f"Profile updated from the website — {form['name']}: "
            f"{len(form['attested_skills'])} attested skills, search is now "
            f"{' / '.join(form['search']['keywords'])} "
            f"({form['search']['location']}). The next scan uses these.")
        return True, "saved"

    # -- the ride: a visitor's own form -> their own store -> full flow --
    def start_ride(self, payload):
        """Validate a submitted onboarding form, write it as a ride, switch
        the UI to the ride's own store, and run the complete flow on it.
        Returns (True, slug) or (False, error-for-the-form)."""
        if self.readonly():
            return False, "This shared preview is view-only — rides run on the live machine."
        form, err = self._form_from_payload(payload)
        if form is None:
            return False, err
        name = form["name"]
        raw_text = str(payload.get("raw_resume") or "").strip()
        upload = payload.get("resume_upload") or {}
        if upload.get("data_b64"):
            import base64
            from . import ingest
            try:
                raw_text = ingest.extract_text(
                    upload.get("filename", ""),
                    base64.b64decode(upload["data_b64"]))
            except ValueError as e:
                return False, str(e)
        slug = (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "ride")
        slug = f"{slug}-{int(time.time())}"
        ride_dir = os.path.join(self._rides_root, slug)
        os.makedirs(ride_dir, exist_ok=True)
        if raw_text:
            with open(os.path.join(ride_dir, "raw_resume.md"), "w") as f:
                f.write(raw_text)
            form["raw_resume_file"] = "raw_resume.md"
        form_path = os.path.join(ride_dir, "form.json")
        with open(form_path, "w") as f:
            json.dump(form, f, indent=1)
        with self._lock:
            if self._running["sweep"]:
                return False, "A run is already in progress — watch the Activity feed."
            self._running["sweep"] = True
            self._stop_req["sweep"] = False
        from . import flow
        self.out_dir, self.form_path = ride_dir, form_path
        self._ride_active = True
        self._run_ctx = {"kind": "ride", "ride_dir": ride_dir}

        def go():
            try:
                flow.run(form_path, ride_dir, reader_cap=13, build_top=7,
                         should_stop=lambda: self._stop_req["sweep"])
            finally:
                self._finish_run("sweep")
        threading.Thread(target=go, daemon=True).start()
        return True, slug

    def go_home(self):
        self.out_dir, self.form_path = self._home_out, self._home_form
        self._ride_active = False
        return True

    def start_fixture(self):
        if self.readonly():
            return False
        from . import pipeline
        with self._lock:
            if self._running["fixture"]:
                return False
            self._running["fixture"] = True
            self._stop_req["fixture"] = False
        self._run_ctx = {"kind": "fixture"}

        def go():
            try:
                pipeline.run(
                    os.path.join(self.demo_dir, "jd_snowpark.md"),
                    os.path.join(self.demo_dir, "profile_attested.md"),
                    self.fixture_out,
                    should_stop=lambda: self._stop_req["fixture"])
            finally:
                self._finish_run("fixture")
        threading.Thread(target=go, daemon=True).start()
        return True

    # -- state -----------------------------------------------------
    def state(self, range_key="all"):
        if range_key not in RANGE_KEYS:
            range_key = "all"
        win_start, win_end = range_window(range_key)
        form = profile.load_form(self.form_path)
        profile_text = profile.render_profile_md(form)
        attested = verifier.parse_profile(profile_text)
        db_path = os.path.join(self.out_dir, "attested.db")
        counts = {"raw": 0, "postings": 0, "judged": 0, "pass": 0,
                  "resumes": 0, "pursuits": 0, "applied": 0}
        matches = []
        if os.path.exists(db_path):
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            db.row_factory = sqlite3.Row

            def win(col):
                """(sql fragment, params) limiting col to the range window."""
                if win_start is None:
                    return "", []
                return (f" AND {col} >= ? AND {col} < ?",
                        [win_start, win_end])

            w, p = win("fetched_at")
            counts["raw"] = db.execute(
                "SELECT COUNT(*) c FROM job_posting_raw WHERE 1=1" + w, p
            ).fetchone()["c"]
            w, p = win("m.created_at")
            counts["judged"] = db.execute(
                "SELECT COUNT(*) c FROM candidate_matches m WHERE 1=1" + w, p
            ).fetchone()["c"]
            counts["pass"] = db.execute(
                "SELECT COUNT(*) c FROM candidate_matches m "
                "WHERE m.verdict='PASS'" + w, p).fetchone()["c"]
            w, p = win("created_at")
            counts["resumes"] = db.execute(
                "SELECT COUNT(*) c FROM candidate_resumes WHERE 1=1" + w, p
            ).fetchone()["c"]
            counts["pursuits"] = db.execute(
                "SELECT COUNT(*) c FROM pursuits WHERE 1=1" + w, p
            ).fetchone()["c"]
            w, p = win("updated_at")
            counts["applied"] = db.execute(
                "SELECT COUNT(*) c FROM pursuits "
                "WHERE status='applied'" + w, p).fetchone()["c"]
            # Postings chip = postings with a real title. The LinkedIn guest
            # source yields ID-only stubs (no title/JD) — they sit in the
            # table as thin rows and are excluded from the funnel number.
            # A posting counts in the window its raw row was fetched.
            w, p = win("r.fetched_at")
            counts["postings"] = db.execute(
                "SELECT COUNT(*) c FROM job_postings p "
                "JOIN job_posting_raw r ON r.id = p.raw_id "
                "WHERE p.title != ''" + w, p).fetchone()["c"]
            rows = db.execute(
                "SELECT m.posting_id, m.verdict, m.defensible, m.vulnerable,"
                " m.detail, p.title, p.company, p.source, p.apply_url,"
                " p.location, p.employment_type,"
                " pu.route, pu.status AS pursuit_status, pu.id AS pursuit_id,"
                " pu.detail AS pursuit_detail"
                " FROM candidate_matches m"
                " JOIN job_postings p ON p.id = m.posting_id"
                " LEFT JOIN pursuits pu ON pu.posting_id = m.posting_id"
                " ORDER BY (m.verdict='SKIP'), m.defensible DESC").fetchall()
            for r in rows:
                split = json.loads(r["detail"])["split"]
                for item in split["vulnerable"]:
                    item["pivot"] = verifier.pivot_for(item["term"], attested)
                match_dir = os.path.join(self.out_dir, "matches", str(r["posting_id"]))
                dossier_path = os.path.join(match_dir, "dossier.md")
                audit = []
                if os.path.exists(dossier_path):
                    audit = parse_audit(open(dossier_path).read())
                matches.append({
                    "posting_id": r["posting_id"], "verdict": r["verdict"],
                    "title": r["title"], "company": r["company"],
                    "source": r["source"], "apply_url": r["apply_url"],
                    "defensible_n": r["defensible"],
                    "vulnerable_n": r["vulnerable"],
                    "split": split, "audit": audit,
                    "route": r["route"], "pursuit_status": r["pursuit_status"],
                    "pursuit_id": r["pursuit_id"],
                    "pursuit_detail": r["pursuit_detail"] or "",
                    "location": r["location"] or "",
                    "employment_type": r["employment_type"] or "",
                    "has_resume": os.path.exists(os.path.join(match_dir, "resume.md")),
                    "has_package": os.path.exists(
                        os.path.join(match_dir, "application_package.md")),
                })
            db.close()
        events = _read_events(os.path.join(self.out_dir, "events.jsonl"), 60)
        if self._ride_active:
            # A ride shows ONLY its own run — the demo persona's fixture
            # events are a different person's story.
            fixture_events = []
        else:
            fixture_events = _read_events(
                os.path.join(self.fixture_out, "events.jsonl"), 60)
        merged = sorted(events + fixture_events, key=lambda e: e["ts"])[-80:]
        # Intake alerts fire at the START of a run; a long run's event file
        # outgrows the feed window, so scan the full files for them.
        intake, seen = [], set()
        intake_paths = [os.path.join(self.out_dir, "events.jsonl")]
        if not self._ride_active:
            intake_paths.append(os.path.join(self.fixture_out, "events.jsonl"))
        for path in intake_paths:
            for e in _read_events(path, 100000):
                m = re.search(r"GUARDRAIL ALERT \[intake:(.+?)\] (.+?):",
                              e["message"])
                if m and m.group(2) not in seen:
                    seen.add(m.group(2))
                    intake.append({"type": m.group(1), "term": m.group(2),
                                   "message": e["message"]})
        return {
            "candidate": form["name"],
            "skills": form["attested_skills"],
            "boundaries": form.get("hard_boundaries", []),
            "spec": {
                "keywords": form["search"]["keywords"],
                "location": form["search"].get("location", ""),
                "contract_only": form["search"].get("contract_only", False),
                "posted_within_hours": form["search"].get("posted_within_hours", 24),
                "match_skills": form["search"]["match_skills"],
                "sources": form["search"].get("sources", []),
                "job_types": form["search"].get("job_types", []),
                "workplace": form["search"].get("workplace", []),
            },
            "counts": counts, "range": range_key,
            "range_label": RANGE_LABELS[range_key],
            "matches": matches, "events": merged,
            "feed": human_feed(merged),
            "intake": intake,
            "running": dict(self._running), "can_run": self.can_run(),
            "stopping": bool(self._stop_req["sweep"]
                             or self._stop_req["fixture"]),
            "mode": "ride" if self._ride_active else "demo",
            "prefill_form": profile.load_form(self._home_form),
        }

    def doc_html(self, posting_id, kind):
        if kind == "jd":
            db_path = os.path.join(self.out_dir, "attested.db")
            if not os.path.exists(db_path):
                return None
            db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            row = db.execute("SELECT jd_text FROM job_postings WHERE id = ?",
                             (posting_id,)).fetchone()
            db.close()
            if not row or not row[0]:
                return None
            return md_lite(row[0])
        fname = {"resume": "resume.md", "package": "application_package.md"}.get(kind)
        if not fname:
            return None
        path = os.path.join(self.out_dir, "matches", str(posting_id), fname)
        if not os.path.exists(path):
            return None
        return md_lite(open(path).read())

    def download_resume(self, posting_id, fmt):
        """The built resume as an editable file: Word (.docx) or PDF."""
        from . import export
        path = os.path.join(self.out_dir, "matches", str(posting_id),
                            "resume.md")
        if not os.path.exists(path):
            return None
        text = open(path).read()
        if fmt == "docx":
            return (export.to_docx(text),
                    "application/vnd.openxmlformats-officedocument."
                    "wordprocessingml.document",
                    f"tailored-resume-{posting_id}.docx")
        if fmt == "pdf":
            return (export.to_pdf(text), "application/pdf",
                    f"tailored-resume-{posting_id}.pdf")
        return None


# ---------------------------------------------------------------- page

PAGE = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CMM — Candidate Marketing Machine</title>
<style>
*{box-sizing:border-box}
:root{--ink:#12211C;--grey:#5F7169;--faint:#8CA298;--line:#DFEAE3;--mint:#EDF5F0;--panel:#E4EFE8;--teal:#0B3D34;--teal2:#0E4A3F;--green:#1FA855;--greenD:#128A43;--amber:#B45309;--rose:#B21F35}
body{margin:0;background:linear-gradient(180deg,#E7F3EC 0,#EDF5F0 240px);color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif}
.layout{display:flex;align-items:stretch;min-height:100vh}
/* ---------- sidebar ---------- */
aside{flex:0 0 248px;background:var(--teal);color:#DCEEE6;display:flex;flex-direction:column;padding:20px 16px;position:sticky;top:0;height:100vh}
.brand{display:flex;gap:11px;align-items:center;padding:2px 6px 18px}
.mark{flex:0 0 42px;width:42px;height:42px;border-radius:13px;background:var(--green);display:flex;align-items:center;justify-content:center;font-size:21px}
.brand .wm{font-size:21px;font-weight:800;letter-spacing:.4px;color:#fff;line-height:1.1}
.brand .sub{font-size:11.5px;color:#9FC4B4}
.navlabel{font-size:11px;letter-spacing:1.6px;color:#7FAF9C;margin:4px 8px 8px}
nav a{display:flex;align-items:center;gap:11px;color:#DCEEE6;text-decoration:none;padding:10px 12px;border-radius:12px;font-weight:600;margin-bottom:3px;font-size:14.5px}
nav a:hover{background:var(--teal2)}
nav a.on{background:#DFF5EA;color:var(--teal)}
nav a .ni{font-size:16px;width:20px;text-align:center}
.sidecard{margin-top:auto;background:var(--teal2);border:1px solid #17604F;border-radius:16px;padding:13px 14px}
.sidecard .t{font-weight:800;color:#fff;font-size:13.5px;margin-bottom:3px}
.sidecard .x{color:#B9DCD0;font-size:12.5px}
.candrow{display:flex;gap:10px;align-items:center;margin-top:14px;padding:12px 6px 2px;border-top:1px solid #17604F}
.candava{flex:0 0 38px;width:38px;height:38px;border-radius:50%;background:#FDE8D7;color:#8A4B12;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px}
.candrow .cn{font-weight:700;color:#fff;font-size:13.5px;line-height:1.25}
.candrow .cm{color:#9FC4B4;font-size:12px}
/* ---------- main ---------- */
.main{flex:1;min-width:0;padding:22px 26px 30px;max-width:1460px;margin:0 auto;width:100%}
.topbar{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap}
.dateline{color:var(--grey);font-size:13px;margin-bottom:2px}
h1.greet{font-size:30px;font-weight:800;letter-spacing:-.4px;margin:0;color:var(--ink)}
.topbtns{margin-left:auto;display:flex;gap:8px;align-items:center;flex-wrap:wrap}
button{border-radius:9999px;padding:9px 18px;font-weight:700;cursor:pointer;font-size:14px;font-family:inherit}
.btn1{background:var(--green);border:1px solid var(--green);color:#fff}.btn1:hover{background:var(--greenD)}
.btnT{background:var(--teal);border:1px solid var(--teal);color:#fff}.btnT:hover{background:var(--teal2)}
.btn2{background:#fff;border:1px solid #C6D6CD;color:var(--ink)}.btn2:hover{border-color:var(--teal);color:var(--teal)}
button:disabled{opacity:.45;cursor:default}
.hint{color:var(--grey);font-size:12px;font-weight:400}
.note{color:var(--grey);font-size:12.5px;margin-top:8px}
/* ---------- stat cards ---------- */
.stats{display:grid;grid-template-columns:repeat(5,1fr);gap:14px;margin:20px 0 18px}
@media(max-width:1200px){.stats{grid-template-columns:repeat(3,1fr)}}
@media(max-width:700px){.stats{grid-template-columns:repeat(2,1fr)}}
.stat{background:#fff;border:1px solid var(--line);border-radius:20px;padding:16px 18px;box-shadow:0 1px 3px rgba(11,61,52,.05)}
.stat .lb{color:var(--grey);font-size:12.5px;font-weight:700;letter-spacing:.4px;text-transform:uppercase;display:flex;gap:7px;align-items:center}
.stat .num{font-size:34px;font-weight:800;letter-spacing:-.6px;margin:3px 0 1px}
.stat .sb{color:var(--faint);font-size:12.5px}
.stat.hero .num{color:var(--greenD)}
/* ---------- cards & board ---------- */
.card{background:#fff;border:1px solid var(--line);border-radius:22px;padding:18px 20px;box-shadow:0 1px 3px rgba(11,61,52,.05);margin-bottom:18px}
.card h2,.cardhead{font-size:19px;font-weight:800;color:var(--ink);margin:0;letter-spacing:-.2px;text-transform:none}
.card .sub2{color:var(--grey);font-size:13px;margin-top:2px}
.board{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-top:14px}
@media(max-width:1250px){.board{grid-template-columns:repeat(2,1fr)}}
@media(max-width:680px){.board{grid-template-columns:1fr}}
.col{background:var(--panel);border-radius:18px;padding:12px;min-height:180px}
.colhead{display:flex;align-items:center;gap:8px;font-weight:800;font-size:14.5px;padding:2px 4px 10px}
.dotb{width:9px;height:9px;border-radius:50%;flex:0 0 9px}
.cnt{margin-left:auto;background:#fff;border-radius:9999px;padding:1px 10px;font-size:12.5px;font-weight:800;box-shadow:0 1px 2px rgba(11,61,52,.08)}
.colbody{display:flex;flex-direction:column;gap:10px;max-height:600px;overflow:auto;padding-right:1px}
.jcard{background:#fff;border:1px solid #E7EFEA;border-radius:16px;padding:12px 13px;box-shadow:0 1px 2px rgba(11,61,52,.05);cursor:pointer}
.jcard:hover{border-color:#BFE3D2}
.jcard .r1{display:flex;gap:10px;align-items:flex-start}
.ava{flex:0 0 38px;width:38px;height:38px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-weight:800;font-size:13px;color:#274035}
.jcard .tt{font-weight:700;font-size:14px;line-height:1.3}
.jcard .ss{color:var(--grey);font-size:12.5px}
.jcard .r2{display:flex;align-items:center;gap:8px;margin-top:9px}
.ok{color:var(--greenD);font-weight:700;font-size:12.5px}
.thin{color:var(--amber);font-weight:600;font-size:12.5px}
.openbtn{margin-left:auto;background:var(--teal);color:#fff;border:none;border-radius:9999px;padding:5px 14px;font-size:12.5px;font-weight:700;cursor:pointer}
.pursuit{color:#5B21B6;background:#EFE7FF;border-radius:9999px;padding:2px 10px;font-size:12px;font-weight:700;display:inline-block}
.scard{background:#fff;border:1px solid #E7EFEA;border-left:4px solid #C6D6CD;border-radius:14px;padding:10px 12px}
.scard.good{border-left-color:var(--green)}
.scard.warn{border-left-color:#E2445C}
.scard .st2{font-weight:700;font-size:13px;line-height:1.35}
.scard .sx{color:var(--grey);font-size:12.5px;margin-top:1px}
.scard .sm{color:var(--faint);font-size:11px;margin-top:3px}
.empty{color:var(--faint);font-size:13px;padding:8px 4px}
.bcard{background:#fff;border:1px solid #E7EFEA;border-radius:16px;padding:12px 13px;box-shadow:0 1px 2px rgba(11,61,52,.05)}
.bcard .r1{display:flex;gap:10px;align-items:flex-start}
.bcard .tt{font-weight:700;font-size:14px;line-height:1.3}
.bcard .ss{color:var(--grey);font-size:12.5px}
.xpand{margin-top:9px;border-top:1px dashed #E1EBE4;padding-top:9px}
.xpand .doc{max-height:340px;overflow:auto;font-size:12.5px}
.xbtn{background:#EFF6F1;border:1px solid #D8E6DC;color:#274035;border-radius:9999px;padding:5px 13px;font-size:12.5px;font-weight:700;cursor:pointer}
.xbtn:hover{border-color:#BFE3D2}
.rsplit{display:flex;gap:10px;margin-top:2px}
.rhalf{flex:1;min-width:0;background:#F8FBF9;border:1px solid #EAF1EC;border-radius:12px;padding:9px 10px}
.rhalf .rh{font-size:11px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:var(--faint);margin-bottom:4px}
.whyline{color:#8A5A00;font-size:12.5px}
.outok{color:var(--greenD);font-weight:800;font-size:13.5px}
.outbad{color:#B42318;font-weight:800;font-size:13.5px}
.outwhy{color:var(--grey);font-size:12.5px;margin-top:2px}
.applybtn{background:var(--teal);color:#fff;border:none;border-radius:9999px;padding:8px 18px;font-size:13px;font-weight:800;cursor:pointer}
.applybtn:disabled{opacity:.55;cursor:default}
/* ---------- lower grid ---------- */
.lower{display:grid;grid-template-columns:1fr 380px;gap:18px;align-items:start}
@media(max-width:1150px){.lower{grid-template-columns:1fr}}
.trow{display:flex;gap:12px;align-items:flex-start;padding:13px 6px;border-bottom:1px solid #EEF4F0;cursor:pointer}
.trow:hover{background:#F6FBF8}
.trow .tt{font-weight:700;font-size:14.5px}
.trow .ss{color:var(--grey);font-size:12.5px}
.pill{display:inline-block;border-radius:9999px;padding:2px 11px;font-size:12px;font-weight:800;border:1px solid;white-space:nowrap}
.pass{background:#E4F7EC;border-color:#9FE3BB;color:#0A6E46}
.skip{background:#F0F3F1;border-color:#D3DDD7;color:#5A6578}
.skill{margin:0 0 9px;color:#374151}.skill b{color:var(--ink)}
.never{color:var(--rose);background:#FFF1F3;border-radius:12px;padding:9px 12px;margin:10px 0;font-size:13px}
.spec{color:var(--grey);font-size:13px;border-top:1px solid #EEF4F0;padding-top:10px}
.flag{background:#FFF5F6;border:1px solid #F5C2CB;border-left:4px solid #E2445C;border-radius:12px;padding:9px 12px;margin:8px 0}
.flag b{color:var(--rose)}.flag s{color:#7A2434}.flag .why{color:#8A5A63;font-size:12px}
.shieldnote{color:var(--grey);font-size:13px;margin:0 0 6px}
.minih{font-size:12px;letter-spacing:1.2px;color:var(--grey);margin:18px 0 10px;text-transform:uppercase;font-weight:800}
/* ---------- drawer ---------- */
#drawer{display:none}
.tabs{display:flex;gap:8px;margin-bottom:12px;flex-wrap:wrap}
.tab{border:1px solid #C6D6CD;background:#fff;color:var(--ink);border-radius:9999px;padding:7px 16px;cursor:pointer;font-weight:700}
.tab.on{background:var(--teal);border-color:var(--teal);color:#fff}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:10px}
.claim{border-radius:16px;padding:12px 14px;border:1px solid}
.claim.def{background:#F2FBF6;border-color:#8FE3BC}.claim.vul{background:#FFFAF0;border-color:#F5C97B}
.claim b{font-size:15px;color:var(--ink)}
.claim .ev2{color:#4B5563;font-size:13px}.claim .rs{color:#7A4A00;font-size:13px}
.claim .pv{color:#9A5B00;font-size:13px;font-style:italic}
.badge{display:inline-block;background:#E4F7EC;border:1px solid #00C875;color:#0A6E46;border-radius:9999px;padding:5px 14px;font-weight:700;cursor:pointer;margin:4px 0 10px}
.auditbox{background:#FFF5F6;border:1px solid #F5C2CB;border-radius:12px;padding:10px 14px;color:#7A2434;font-size:13px;margin-bottom:10px}
.doc{white-space:normal;color:#1F2937}.doc h2{color:var(--ink);text-transform:none;letter-spacing:0;font-size:18px}
.doc h3{color:var(--ink)}.doc li{margin:3px 0}
.mycall{border-top:1px solid #EEF4F0;margin-top:14px;padding-top:10px;color:#374151}
a{color:var(--teal)}
/* ---------- ride form ---------- */
#rideView{display:none;scroll-margin-top:20px}
#rideView.open{display:block}
.fgrid{display:grid;grid-template-columns:1fr 1fr;gap:14px 22px;margin-top:12px}
@media(max-width:900px){.fgrid{grid-template-columns:1fr}}
.fld label{display:block;font-weight:700;color:var(--ink);margin-bottom:5px}
.fld input[type=text],.fld textarea{width:100%;border:1px solid #C6D6CD;border-radius:12px;padding:10px 12px;font:inherit;background:#fff}
.fld input[type=text]:focus,.fld textarea:focus{outline:2px solid #9FE3BB;border-color:var(--green)}
.fld textarea{resize:vertical}
.skillrow{display:flex;gap:8px;margin-bottom:8px}
.skillrow input{flex:1;border:1px solid #C6D6CD;border-radius:12px;padding:9px 11px;font:inherit}
.skillrow .del{border:1px solid #E5E7EB;background:#fff;border-radius:12px;width:40px;cursor:pointer;color:var(--grey)}
.srcs{display:flex;gap:14px;flex-wrap:wrap;color:#374151}
.err{background:#FEECEE;border:1px solid #F5C2CB;color:var(--rose);border-radius:12px;padding:9px 12px;display:none;margin-top:10px}
/* ---------- run state / misc ---------- */
.thinking{display:inline-flex;gap:8px;align-items:center;background:#EAFBF1;border:1px solid #9FE3BB;color:#0A6E46;border-radius:9999px;padding:4px 13px;font-size:12.5px;font-weight:700}
.pulse{display:inline-block;width:9px;height:9px;border-radius:50%;background:var(--green);animation:pl 1.1s infinite}
@keyframes pl{0%,100%{opacity:1}50%{opacity:.25}}
.btnStop{background:#E2445C;color:#fff;border:none;border-radius:9999px;padding:9px 16px;font-weight:700;cursor:pointer}
.rangebar{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin:2px 0 12px}
details.rawlog{margin-top:4px}details.rawlog summary{cursor:pointer;color:var(--faint);font-size:12.5px}
.ev{display:flex;gap:10px;padding:7px 4px;border-bottom:1px solid #EDF3EF}
.ev .ava2{flex:0 0 30px;width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;background:#E7EFEA}
.ev .who{font-weight:700;color:var(--ink);font-size:13px}
.ev .msg{color:#374151;font-size:13px;word-break:break-word}
.ev .tm{margin-left:auto;color:var(--faint);font-size:11px;flex:0 0 auto;padding-left:8px}
.feed{max-height:340px;overflow:auto;padding-right:2px}
#stopModal{display:none;position:fixed;inset:0;background:rgba(11,40,33,.5);z-index:60;align-items:center;justify-content:center}
#stopModal.open{display:flex}
#stopModal .mcard{background:#fff;border-radius:20px;max-width:430px;width:92%;padding:22px;box-shadow:0 18px 50px rgba(0,0,0,.25)}
@media(max-width:900px){.layout{flex-direction:column}aside{flex:none;height:auto;position:static}nav{display:flex;flex-wrap:wrap;gap:4px}nav a{margin-bottom:0}.sidecard{margin-top:14px}.main{padding:16px}}
</style></head><body>
<div class="layout">
<aside>
 <div class="brand"><div class="mark">⚡</div><div><div class="wm">CMM</div><div class="sub">Candidate Marketing Machine</div></div></div>
 <div class="navlabel">YOUR HUNT</div>
 <nav>
  <a href="#" data-nav="main" class="on"><span class="ni">⌂</span>Today</a>
  <a href="#" data-nav="main" data-scroll="boardSec"><span class="ni">▦</span>Your pipeline</a>
  <a href="#" data-nav="targets"><span class="ni">🎯</span>Live Market Targets</a>
  <a href="#" data-nav="profile"><span class="ni">👤</span>Your profile</a>
  <a href="#" data-nav="safety"><span class="ni">🛡️</span>Safety check</a>
 </nav>
 <div class="sidecard"><div class="t">🛡️ The honesty rule</div><div class="x">The LLM proposes. The code disposes. Nothing is ever submitted without your click.</div></div>
 <div class="candrow"><div class="candava" id="candAva">?</div><div><div class="cn" id="cand"></div><div class="cm" id="candMode"></div></div></div>
</aside>
<div class="main" id="top">
 <div class="topbar">
  <div><div class="dateline" id="dateLine"></div><h1 class="greet" id="greet">Your job hunt</h1></div>
  <div class="topbtns">
   <button class="btnT" id="rideBtn">✨ Try it with your own profile</button>
   <button class="btn2" id="homeBtn" style="display:none">← Back to demo results</button>
  </div>
 </div>
 <div id="viewMain">
 <div class="rangebar">
  <span style="font-weight:700;color:var(--ink)">Show numbers for:</span>
  <button class="tab rangebtn on" data-range="today" type="button">Today</button>
  <button class="tab rangebtn" data-range="yesterday" type="button">Yesterday</button>
  <button class="tab rangebtn" data-range="month" type="button">This month</button>
  <button class="tab rangebtn" data-range="all" type="button">All time</button>
  <span class="hint" id="rangeNote"></span>
 </div>
 <div class="stats">
  <div class="stat"><div class="lb">🔎 Jobs scanned</div><div class="num" id="statRaw">0</div><div class="sb" id="statRawSub"></div></div>
  <div class="stat"><div class="lb">⚖️ Roles audited</div><div class="num" id="statJudged">0</div><div class="sb">read in full, against your proof</div></div>
  <div class="stat hero"><div class="lb">✅ Worth your time</div><div class="num" id="statPass">0</div><div class="sb">passed the honesty audit</div></div>
  <div class="stat"><div class="lb">✍️ Resumes built</div><div class="num" id="statResumes">0</div><div class="sb">tailored — every claim provable</div></div>
  <div class="stat"><div class="lb">📮 Applications prepared</div><div class="num" id="statPursuits">0</div><div class="sb">waiting for your click — nothing auto-sent</div></div>
  <div class="stat"><div class="lb">🎉 Applied</div><div class="num" id="statApplied">0</div><div class="sb">submitted — the confirmation is saved on the board</div></div>
 </div>

 <section class="card" id="rideView">
  <div class="cardhead">Start your ride — tell the truth, the machine does the rest</div>
  <p class="shieldnote" style="margin-top:6px">Fill this in once. When you hit <b>Start my search</b>, the machine hunts live jobs, audits every role against what you can honestly claim, builds a tailored resume per role, and prepares your applications — watch it happen on the board below. The demo values are prefilled so you can just hit start.</p>
  <div class="fgrid">
   <div class="fld"><label>Your name</label><input type="text" id="fName"></div>
   <div class="fld"><label>Where should it look? <span class="hint">location</span></label><input type="text" id="fLoc"></div>
   <div class="fld" style="grid-column:1/-1"><label>Your skills — claim only what you can defend <span class="hint">skill + your proof (years, where, what you built)</span></label><div id="fSkills"></div><button class="btn2" id="addSkill" type="button">＋ Add a skill</button></div>
   <div class="fld" style="grid-column:1/-1"><label>Hard boundaries <span class="hint">one per line — things you have NOT done and will not claim</span></label><textarea id="fBounds" rows="3"></textarea></div>
   <div class="fld" style="grid-column:1/-1"><label>Your current resume <span class="hint">optional — upload the file or paste the text, and the Safety Check will flag anything in it you can't back up, before anything is built</span></label><input type="file" id="fResumeFile" accept=".docx,.pdf,.txt,.md" style="margin-bottom:6px"> <span class="hint" id="fResumeFileName">Word (.docx), PDF, or text file</span><textarea id="fResume" rows="4" placeholder="…or paste your resume text here (optional)"></textarea></div>
   <div class="fld"><label>Search phrases <span class="hint">comma-separated</span></label><input type="text" id="fKeywords"></div>
   <div class="fld"><label>Match skills <span class="hint">comma-separated — a role stays in only if it asks for ≥2 of these</span></label><input type="text" id="fMatch"></div>
   <div class="fld"><label>Look on</label><div class="srcs" id="fSources"></div></div>
   <div class="fld"><label>Job type</label><div class="srcs"><label style="font-weight:400"><input type="checkbox" class="fjt" value="contract"> Contract</label><label style="font-weight:400"><input type="checkbox" class="fjt" value="full_time"> Full-time</label><label style="font-weight:400"><input type="checkbox" class="fjt" value="part_time"> Part-time</label></div><span class="hint">none ticked = any type</span></div>
   <div class="fld"><label>Workplace</label><div class="srcs"><label style="font-weight:400"><input type="checkbox" class="fwp" value="remote"> Remote</label><label style="font-weight:400"><input type="checkbox" class="fwp" value="hybrid"> Hybrid</label><label style="font-weight:400"><input type="checkbox" class="fwp" value="onsite"> On-site</label></div><span class="hint">none ticked = anywhere · posted in the last</span> <select id="fHours"><option value="12">12h</option><option value="24">24h</option><option value="48">48h</option><option value="72">3 days</option><option value="168">7 days</option></select></div>
  </div>
  <div style="margin-top:14px;display:flex;gap:8px;align-items:center"><button class="btn1" id="startRide" type="button">▶ Start my search</button><button class="btn2" id="cancelRide" type="button">Cancel</button></div>
  <div class="err" id="rideErr"></div>
 </section>

 <section class="card" id="boardSec">
  <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center">
   <div><div class="cardhead">Your pipeline</div><div class="sub2">Only the jobs that fit your profile — each with its resume built beside it, one click to apply, and the outcome in plain sight.</div></div>
   <div style="margin-left:auto;display:flex;gap:8px;flex-wrap:wrap;align-items:center">
    <span id="runState" style="display:none"><span class="pulse"></span> <b id="runStateTxt">Running…</b></span>
    <button class="btn2" id="runFixture">Watch One Role Audited Live (~35 sec)</button>
    <button class="btnStop" id="btnStop" style="display:none">⏹ Stop</button>
    <button class="btn1" id="runSweep">▶ Scan for New Roles</button>
   </div>
  </div>
  <div class="note" id="runNote"></div>
  <div id="thinking" style="margin-top:8px"></div>
  <div class="board">
   <div class="col"><div class="colhead"><span class="dotb" style="background:#94A89E"></span>Found<span class="cnt" id="cntFound">0</span></div><div class="colbody" id="colFound"></div></div>
   <div class="col"><div class="colhead"><span class="dotb" style="background:#3B82C4"></span>Resume Builder<span class="cnt" id="cntCheck">0</span></div><div class="colbody" id="colCheck"></div></div>
   <div class="col"><div class="colhead"><span class="dotb" style="background:var(--green)"></span>Ready to Apply<span class="cnt" id="cntSafe">0</span></div><div class="colbody" id="colSafe"></div></div>
   <div class="col"><div class="colhead"><span class="dotb" style="background:#7C5CFC"></span>Outcome<span class="cnt" id="cntReady">0</span></div><div class="colbody" id="colReady"></div></div>
  </div>
  <details class="rawlog"><summary>For the curious: the full technical log, exactly as the machines wrote it</summary><div class="feed" id="rawfeed" style="margin-top:8px"></div></details>
 </section>
 </div>

 <div id="viewTargets" style="display:none">
  <section class="card" id="targetsSec">
   <div class="cardhead">Live Market Targets — roles audited for you</div>
   <div class="sub2">Every role the machine read in full, with the honest split. Tap any role to open its dossier.</div>
   <div id="buildMissingWrap" style="display:none;margin:10px 0 2px"><button class="btn1" id="buildMissing"></button> <span class="hint">for the roles that passed the audit but have no resume yet</span></div>
   <div id="rows"></div>
  </section>
 </div>

 <div id="viewProfile" style="display:none">
  <section class="card" id="profileSec">
   <div class="cardhead" style="font-size:17px">Your Attested Profile</div>
   <div style="margin-top:12px" id="skills"></div><div class="never" id="never"></div>
   <div class="spec" id="spec"></div>
   <div style="border-top:1px solid #EEF4F0;margin-top:16px;padding-top:14px">
    <div class="cardhead" style="font-size:15px">✏️ Update your profile &amp; your search</div>
    <p class="shieldnote">Change anything here and hit Save — your attested profile and your search (the sweep) update together, and the next <b>Scan for New Roles</b> uses the new settings. Jobs already found, resumes already built, and applications already sent are not changed.</p>
    <div class="fgrid">
     <div class="fld"><label>Your name</label><input type="text" id="pName"></div>
     <div class="fld"><label>Where should it look? <span class="hint">location</span></label><input type="text" id="pLoc"></div>
     <div class="fld" style="grid-column:1/-1"><label>Your skills — claim only what you can defend <span class="hint">skill + your proof</span></label><div id="pSkills"></div><button class="btn2" id="addPSkill" type="button">＋ Add a skill</button></div>
     <div class="fld" style="grid-column:1/-1"><label>Hard boundaries <span class="hint">one per line — things you have NOT done and will not claim</span></label><textarea id="pBounds" rows="3"></textarea></div>
     <div class="fld"><label>Search phrases <span class="hint">comma-separated</span></label><input type="text" id="pKeywords"></div>
     <div class="fld"><label>Match skills <span class="hint">comma-separated — a role stays in only if it asks for ≥2 of these</span></label><input type="text" id="pMatch"></div>
     <div class="fld"><label>Look on</label><div class="srcs" id="pSources"></div></div>
     <div class="fld"><label>Job type</label><div class="srcs"><label style="font-weight:400"><input type="checkbox" class="pjt" value="contract"> Contract</label><label style="font-weight:400"><input type="checkbox" class="pjt" value="full_time"> Full-time</label><label style="font-weight:400"><input type="checkbox" class="pjt" value="part_time"> Part-time</label></div><span class="hint">none ticked = any type</span></div>
     <div class="fld"><label>Workplace</label><div class="srcs"><label style="font-weight:400"><input type="checkbox" class="pwp" value="remote"> Remote</label><label style="font-weight:400"><input type="checkbox" class="pwp" value="hybrid"> Hybrid</label><label style="font-weight:400"><input type="checkbox" class="pwp" value="onsite"> On-site</label></div><span class="hint">none ticked = anywhere · posted in the last</span> <select id="pHours"><option value="12">12h</option><option value="24">24h</option><option value="48">48h</option><option value="72">3 days</option><option value="168">7 days</option></select></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center"><button class="btn1" id="saveProfile" type="button">💾 Save my profile &amp; search</button><span class="hint" id="profileSaved"></span></div>
    <div class="err" id="profileErr"></div>
   </div>
  </section>
 </div>

 <div id="viewSafety" style="display:none">
  <section class="card" id="safetySec">
   <div class="cardhead" style="font-size:17px">🛡️ Profile Safety Check — your own resume, checked first</div>
   <p class="shieldnote">Claims in your draft resume that aren't backed by your attested experience. They never reach an application — so they can't trip you up in an interview.</p>
   <div id="intake"></div>
  </section>
 </div>

 <section class="card" id="drawer">
  <div class="tabs">
   <button class="tab" data-t="dossier">Evidence Dossier</button>
   <button class="tab" data-t="resume">Tailored Resume</button>
   <button class="tab" data-t="package">Application Package</button>
   <button class="tab" id="closeDrawer">✕ Close</button>
  </div>
  <div id="drawerBody"></div>
 </section>
</div>
</div>
<div id="stopModal"><div class="mcard">
 <h3 style="margin:0 0 8px">Stop this run?</h3>
 <p style="margin:0 0 6px;color:#374151">The machine saves every step as it goes. You can:</p>
 <p style="margin:0 0 4px"><b>💾 Keep the results so far</b> — stop now, everything already found and built stays. Scan again later and it continues where it left off.</p>
 <p style="margin:0 0 14px"><b>🗑 Discard everything</b> — stop and throw away everything this run produced, like it never happened.</p>
 <div style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn1" id="stopKeep">💾 Keep results &amp; stop</button><button class="btnStop" id="stopDiscard">🗑 Discard &amp; stop</button><button class="btn2" id="stopCancel">Keep running</button></div>
</div></div>
<script>
let S=null,cur=null,tab='dossier';
const openDocs=new Set(),docCache={};
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ACTOR={SWEEPER:['🔎','Sweeper','#E0F2FE'],PARSER:['🧩','Parser','#EEF2F6'],READER:['📖','Reader Agent','#DBEAFE'],VERDICT:['⚖️','Verdict','#FEF3C7'],GUARDRAIL:['🛡️','Integrity Guardrail','#D1FAE5'],BUILDER:['✍️','Builder Agent','#EDE9FE'],APPLIER:['📮','Applier','#F3E8FF'],SYSTEM:['✨','CMM','#EEF2F6'],FIXTURE:['🎯','Live Audit','#DBEAFE']};
const TRIM=m=>{const i=m.indexOf('matches/');return i>0&&m.length>110?('…/'+m.slice(i)):m;};
const PASTELS=['#FDE8D7','#DCEBFF','#E7E0FF','#D9F2E4','#FFF3C4','#FFE0E6','#D8F3F0'];
const avaFor=name=>{const w=String(name||'?').split(/\s+/).filter(Boolean);const ini=((w[0]||'?')[0]+(w[1]?(w[1][0]):'')).toUpperCase();let h=0;for(const ch of String(name||''))h=(h*31+ch.charCodeAt(0))>>>0;return{ini,bg:PASTELS[h%PASTELS.length]};};
let _lastSig='';
let range='today';
async function load(){try{const r=await fetch('api/state?range='+range);const nxt=await r.json();const sig=JSON.stringify(nxt);if(sig===_lastSig)return;_lastSig=sig;S=nxt;render();}catch(e){}}
function render(){
 const first=(S.candidate||'').split(' ')[0]||'there';
 document.getElementById('cand').textContent=S.candidate;
 document.getElementById('candAva').textContent=avaFor(S.candidate).ini;
 document.getElementById('candMode').textContent=S.mode==='ride'?'Your ride — your own search':'Demo results';
 const nowET=new Date(new Date().toLocaleString('en-US',{timeZone:'America/New_York'}));
 const hr=nowET.getHours();
 document.getElementById('greet').textContent=(hr<12?'Good morning':hr<18?'Good afternoon':'Good evening')+', '+first;
 document.getElementById('dateLine').textContent=nowET.toLocaleDateString('en-US',{weekday:'long',month:'long',day:'numeric'})+' · CMM — it markets you, and it never lies for you';
 const c=S.counts;
 document.getElementById('statRaw').textContent=c.raw;
 document.getElementById('statRawSub').textContent=c.postings+' real postings after cleanup';
 document.getElementById('statJudged').textContent=c.judged;
 document.getElementById('statPass').textContent=c.pass;
 document.getElementById('statResumes').textContent=c.resumes;
 document.getElementById('statPursuits').textContent=c.pursuits;
 document.getElementById('statApplied').textContent=c.applied||0;
 document.getElementById('rangeNote').textContent=(S.range_label||'All time')+(S.range==='today'?' — fresh every morning, resets at midnight ET':'')+' · the board below always shows everything still waiting for you, from every day';
 populateProfile();
 document.getElementById('skills').innerHTML=S.skills.map(s=>'<p class="skill"><b>'+esc(s.term)+'</b> — '+esc(s.evidence)+'</p>').join('');
 document.getElementById('never').innerHTML='<b>Your hard boundaries:</b> '+S.boundaries.map(esc).join(' · ');
 const sp=S.spec;
 document.getElementById('spec').innerHTML='Your search: <b>'+esc(sp.keywords.join(' / '))+'</b> · '+esc(sp.location)+' · '+((sp.job_types&&sp.job_types.length)?esc(sp.job_types.join(', ').replace('_','-')):(sp.contract_only?'contract':'any type'))+((sp.workplace&&sp.workplace.length)?' · '+esc(sp.workplace.join(', ')):'')+' · last '+sp.posted_within_hours+'h · a role stays in only if it asks for ≥2 of: '+esc(sp.match_skills.join(', '));
 const why=t=>t==='invented_metric'?'a number with no source behind it':t==='boundary_violation'?'it crosses a hard boundary you set':'not in your attested profile';
 document.getElementById('intake').innerHTML=S.intake.length?S.intake.map(f=>'<div class="flag"><b>Kept out —</b> <s>'+esc(f.term)+'</s><div class="why">'+esc(why(f.type))+' · blocked by the safety check before anything was built</div></div>').join(''):'<p class="shieldnote">Nothing flagged — your draft matches your attested profile.</p>';
 const rf=document.getElementById('runFixture'),rs=document.getElementById('runSweep');
 rf.disabled=!S.can_run||S.running_fixture;rs.disabled=!S.can_run||S.running_sweep;
 document.getElementById('runNote').textContent=S.can_run?'':"You're viewing the real results. The live buttons need the API key (or the dev preview).";
 document.getElementById('rideBtn').style.display=S.mode==='ride'?'none':'';
 document.getElementById('homeBtn').style.display=S.mode==='ride'?'':'none';
 const runningNow=S.running.sweep||S.running.fixture;
 rf.style.display=runningNow||S.mode==='ride'?'none':'';
 rs.style.display=runningNow?'none':'';
 document.getElementById('runState').style.display=runningNow?'':'none';
 const bs=document.getElementById('btnStop');bs.style.display=runningNow?'':'none';bs.disabled=!!S.stopping;
 document.getElementById('runStateTxt').textContent=S.stopping?'Stopping — finishing the step it is on…':'Running — your agents are working';
 if(runningNow)document.getElementById('runNote').textContent='The machine is working — watch the cards move across the board. Every result is saved the moment it lands; stop anytime, nothing is lost unless you choose Discard.';
 if(S.mode==='ride'&&runningNow)document.getElementById('runNote').textContent='Your search is running — the machine is hunting, auditing and building right now. Watch the board.';
 document.getElementById('thinking').innerHTML=runningNow?'<span class="thinking"><span class="pulse"></span>⚡ Your agents are working — finished steps land on the board as cards</span>':'';
 const fmtT=ts=>{try{return new Date(ts).toLocaleTimeString('en-GB',{timeZone:'America/New_York',hour:'2-digit',minute:'2-digit'});}catch(_){return (ts||'').slice(11,16);}};
 // ---- the board: one set of jobs walks the four columns ----
 const M=S.matches||[];
 const meta2=m=>[m.company,m.location,m.employment_type].filter(Boolean).join(' · ');
 const head=m=>{const a=avaFor(m.company);return '<div class="r1"><div class="ava" style="background:'+a.bg+'">'+a.ini+'</div><div style="min-width:0"><div class="tt">'+esc(m.title)+'</div><div class="ss">'+esc(meta2(m))+' · via '+esc(m.source)+'</div></div></div>';};
 const xarea=(id,kind)=>{if(!openDocs.has(id+':'+kind))return '';const h=docCache[id+':'+kind];return '<div class="xpand">'+(h?'<div class="doc">'+h+'</div>':'<div class="ss">Loading…</div>')+'</div>';};
 const foundCard=m=>'<div class="bcard">'+head(m)+'<div style="margin-top:9px;display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button class="xbtn" data-x="'+m.posting_id+':jd">'+(openDocs.has(m.posting_id+':jd')?'▴ Hide the job post':'▾ See the full job post')+'</button>'+(m.apply_url?'<a class="ss" href="'+esc(m.apply_url)+'" target="_blank" rel="noopener">Original posting ↗</a>':'')+'</div>'+xarea(m.posting_id,'jd')+'</div>';
 const buildCard=m=>{
  let right;
  if(m.has_resume)right='<div class="rh">Its resume</div><div class="ok">✓ Built for this job</div><div style="margin-top:6px;display:flex;gap:6px;flex-wrap:wrap"><button class="xbtn" data-x="'+m.posting_id+':resume">'+(openDocs.has(m.posting_id+':resume')?'▴ Hide resume':'▾ View resume')+'</button><a class="xbtn" style="text-decoration:none;display:inline-block" href="api/download?posting='+m.posting_id+'&fmt=docx">⬇ Word</a></div>';
  else if(m.verdict==='PASS')right='<div class="rh">Its resume</div><div class="ss">Not built yet — use “Build the missing resumes” below.</div>';
  else{const terms=((m.split&&m.split.vulnerable)||[]).slice(0,3).map(v=>esc(v.term)).join(', ');right='<div class="rh">No resume — on purpose</div><div class="whyline">This job needs '+terms+(m.vulnerable_n>3?' and more':'')+' — not in your profile, so we didn\'t fake it.</div>';}
  const left='<div class="rh">The job</div><div class="tt" style="font-size:13px">'+esc(m.title)+'</div><div class="ss">'+esc(meta2(m))+'</div><div class="ss" style="margin-top:4px"><span class="ok">'+m.defensible_n+' ✓ provable</span>'+(m.vulnerable_n?' · <span class="thin">'+m.vulnerable_n+' can\'t prove</span>':'')+'</div><button class="xbtn" style="margin-top:6px" data-dossier="'+m.posting_id+'">See the check</button>';
  return '<div class="bcard"><div class="rsplit"><div class="rhalf">'+left+'</div><div class="rhalf">'+right+'</div></div>'+xarea(m.posting_id,'resume')+'</div>';};
 const readyList=M.filter(m=>m.pursuit_id&&m.pursuit_status==='prepared'&&!m.pursuit_detail);
 const readyCard=m=>'<div class="bcard">'+head(m)+'<div style="margin-top:9px"><button class="applybtn" data-apply="'+m.posting_id+'">🚀 Apply — we will apply for you</button></div><div class="ss" style="margin-top:6px">Resume built and checked. One click and CMM submits it on your Dice account.</div></div>';
 const outList=M.filter(m=>m.pursuit_status==='applied'||m.pursuit_status==='apply_requested'||m.pursuit_status==='verified_live'||(m.pursuit_status==='prepared'&&!!m.pursuit_detail));
 const outCard=m=>{
  if(m.pursuit_status==='applied')return '<div class="bcard">'+head(m)+'<div class="outok" style="margin-top:8px">✅ Applied</div><div class="outwhy">'+esc(m.pursuit_detail||'Confirmation received.')+'</div></div>';
  if(m.pursuit_status==='apply_requested'||m.pursuit_status==='verified_live')return '<div class="bcard">'+head(m)+'<div style="margin-top:8px"><b>⏳ Applying now…</b></div><div class="outwhy">The applier is submitting — the result lands here.</div></div>';
  return '<div class="bcard">'+head(m)+'<div class="outbad" style="margin-top:8px">❌ Not applied</div><div class="outwhy">'+esc(m.pursuit_detail)+'</div><div style="margin-top:7px"><button class="xbtn" data-apply="'+m.posting_id+'">Try applying again</button></div></div>';};
 document.getElementById('cntFound').textContent=M.length;
 document.getElementById('cntCheck').textContent=M.length;
 document.getElementById('cntSafe').textContent=readyList.length;
 document.getElementById('cntReady').textContent=outList.length;
 document.getElementById('colFound').innerHTML=M.length?M.map(foundCard).join(''):'<div class="empty">Nothing yet — hit “Scan for New Roles” and the jobs that fit your profile land here.</div>';
 document.getElementById('colCheck').innerHTML=M.length?M.map(buildCard).join(''):'<div class="empty">Each job gets its resume built right beside it here.</div>';
 document.getElementById('colSafe').innerHTML=readyList.length?readyList.map(readyCard).join(''):'<div class="empty">Jobs with a built resume wait here for your click.</div>';
 document.getElementById('colReady').innerHTML=outList.length?outList.map(outCard).join(''):'<div class="empty">Applied — or not, with the reason — shows here.</div>';
 document.querySelectorAll('[data-x]').forEach(b=>b.onclick=async()=>{const k=b.dataset.x;const pid=k.split(':')[0],kind=k.split(':')[1];if(openDocs.has(k)){openDocs.delete(k);render();return;}openDocs.add(k);render();if(!docCache[k]){try{const r=await fetch('api/doc?posting='+pid+'&kind='+kind);const j=await r.json();docCache[k]=j.html||'Nothing to show.';}catch(e){docCache[k]='Could not load it.';}render();}});
 document.querySelectorAll('[data-dossier]').forEach(b=>b.onclick=()=>{cur=S.matches.find(m=>m.posting_id==b.dataset.dossier);tab='dossier';showDrawer();document.getElementById('drawer').scrollIntoView({behavior:'smooth'});});
 document.querySelectorAll('[data-apply]').forEach(b=>b.onclick=async()=>{const pid=Number(b.dataset.apply);const old=b.textContent;b.disabled=true;b.textContent='Requesting…';try{const r=await fetch('api/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({posting_id:pid})});const j=await r.json();if(!j.started){b.disabled=false;b.textContent=old;alert(j.error||'Could not queue the application.');}}catch(e){b.disabled=false;b.textContent=old;}_lastSig='';load();});
 document.getElementById('rawfeed').innerHTML=S.events.slice().reverse().map(e=>{const a=ACTOR[(e.actor||'').toUpperCase()]||ACTOR.SYSTEM;return '<div class="ev"><div class="ava2" style="background:'+a[2]+'">'+a[0]+'</div><div><span class="who">'+a[1]+'</span><div class="msg">'+esc(TRIM(e.message))+'</div></div><div class="tm">'+esc(fmtT(e.ts))+'</div></div>';}).join('');
 const unbuilt=S.matches.filter(m=>m.verdict==='PASS'&&!m.has_resume).length;
 const bmw=document.getElementById('buildMissingWrap');bmw.style.display=(unbuilt>0&&S.can_run&&!runningNow)?'':'none';
 if(unbuilt>0)document.getElementById('buildMissing').textContent='🧾 Build the '+unbuilt+' missing resume'+(unbuilt>1?'s':'');
 document.getElementById('rows').innerHTML=S.matches.length?S.matches.map(m=>'<div class="trow" data-id="'+m.posting_id+'"><span class="pill '+(m.verdict==='PASS'?'pass':'skip')+'">'+m.verdict+'</span><div style="flex:1;min-width:0"><div class="tt">'+esc(m.title)+'</div><div class="ss">@ '+esc(m.company)+' · via '+esc(m.source)+(m.route&&m.route!==m.source?' · '+esc(m.route):'')+'</div></div><div style="text-align:right"><div><span class="ok">'+m.defensible_n+' ✓ provable</span>'+(m.vulnerable_n?' · <span class="thin">'+m.vulnerable_n+' thin</span>':'')+'</div><div class="ss">'+(m.pursuit_id?'Application #'+m.pursuit_id+' '+esc((m.pursuit_status||'').replace(/_/g,' ')):(m.verdict==='PASS'?(m.has_resume?'Resume built — tap to open':'Resume not built yet'):'Set aside — not enough you can prove'))+'</div></div></div>').join(''):'<p class="shieldnote">No roles audited yet.</p>';
 document.querySelectorAll('.jcard,.trow').forEach(el=>el.onclick=()=>{cur=S.matches.find(m=>m.posting_id==el.dataset.id);tab='dossier';showDrawer();document.getElementById('drawer').scrollIntoView({behavior:'smooth'});});
 if(cur){cur=S.matches.find(m=>m.posting_id===cur.posting_id)||cur;showDrawer();}
}
function showDrawer(){
 const d=document.getElementById('drawer'),b=document.getElementById('drawerBody');
 if(!cur){d.style.display='none';return;} d.style.display='block';
 document.querySelectorAll('.tab[data-t]').forEach(t=>t.classList.toggle('on',t.dataset.t===tab));
 if(tab==='dossier'){
  const def=cur.split.defensible.map(x=>'<div class="claim def"><b>'+esc(x.term)+'</b><div class="ev2">'+esc(x.evidence)+'</div></div>').join('');
  const vul=cur.split.vulnerable.map(x=>'<div class="claim vul"><b>'+esc(x.term)+'</b><div class="rs">'+esc(x.reason)+'</div><div class="pv">'+esc(x.pivot)+'</div></div>').join('');
  const badge=cur.audit&&cur.audit.length?'<span class="badge" id="auditBadge">🛡️ '+cur.audit.length+' interview trap'+(cur.audit.length>1?'s':'')+' prevented — see what the AI tried to add</span><div class="auditbox" id="auditBox" style="display:none">'+cur.audit.map(a=>'<div><b>'+esc(a.label)+'</b> ['+esc(a.type)+'] <b>'+esc(a.term)+'</b> — '+esc(a.detail)+'</div>').join('')+'</div>':'';
  b.innerHTML='<h3 style="margin:4px 0 10px">'+esc(cur.title)+' — '+esc(cur.company)+'</h3>'+badge
   +'<div class="minih" style="margin-top:6px">Claims you can defend — verified, with your evidence</div><div class="cards">'+def+'</div>'
   +'<div class="minih">Claims that are thin — the job asks, you cannot prove it yet</div><div class="cards">'+vul+'</div>'
   +'<div class="mycall"><b>My call (you decide — the system never does):</b> Apply / Skip — the dossier informs, you sign. Role via '+esc(cur.source)+'</div>';
  const bd=document.getElementById('auditBadge');if(bd)bd.onclick=()=>{const x=document.getElementById('auditBox');x.style.display=x.style.display==='none'?'block':'none';};
 } else {
  b.innerHTML='<div class="doc">Loading…</div>';
  fetch('api/doc?posting='+cur.posting_id+'&kind='+tab).then(r=>r.json()).then(j=>{const dl=tab==='resume'?'<p style="margin:0 0 10px"><a class="btn1" style="text-decoration:none;display:inline-block;border-radius:9999px;padding:9px 18px;font-weight:700" href="api/download?posting='+cur.posting_id+'&fmt=docx">⬇ Word (.docx)</a> <a class="btn2" style="text-decoration:none;display:inline-block;border-radius:9999px;padding:9px 18px;font-weight:700" href="api/download?posting='+cur.posting_id+'&fmt=pdf">⬇ PDF</a> <span class="hint"> — your resume, editable. Update it, then it\'s yours to send.</span></p>':'';const ap=tab==='package'?applyHtml():'';b.innerHTML=dl+ap+'<div class="doc">'+(j.html||'Not built for this role yet.')+'</div>';if(tab==='package')wireApply();}).catch(()=>{b.innerHTML='Not built for this role yet.';});
 }
}
// ---- sidebar views: one page per click, nothing stacked ----
function showView(v){['viewMain','viewTargets','viewProfile','viewSafety'].forEach(id=>{document.getElementById(id).style.display=(id==='view'+v.charAt(0).toUpperCase()+v.slice(1)?'':'none');});document.querySelectorAll('nav a[data-nav]').forEach(a=>a.classList.toggle('on',a.dataset.nav===v));window.scrollTo({top:0,behavior:'smooth'});}
document.querySelectorAll('nav a[data-nav]').forEach(a=>a.onclick=e=>{e.preventDefault();showView(a.dataset.nav);if(a.dataset.scroll)setTimeout(()=>{const el=document.getElementById(a.dataset.scroll);if(el)el.scrollIntoView({behavior:'smooth'});},40);});
document.querySelectorAll('.tab[data-t]').forEach(t=>t.onclick=()=>{tab=t.dataset.t;showDrawer();});
document.getElementById('closeDrawer').onclick=()=>{cur=null;document.getElementById('drawer').style.display='none';};
document.getElementById('runSweep').onclick=async()=>{await fetch('api/run',{method:'POST'});load();};
document.getElementById('runFixture').onclick=async()=>{await fetch('api/run_fixture',{method:'POST'});load();};
// ---- the ride form ----
let ridePrefilled=false;
function skillRow(t,e){const d=document.createElement('div');d.className='skillrow';d.innerHTML='<input type="text" class="st" placeholder="Skill (e.g. PySpark)" value="'+esc(t||'')+'"><input type="text" class="se" placeholder="Your proof (e.g. 6 years on Amazon EMR)" value="'+esc(e||'')+'"><button type="button" class="del">✕</button>';d.querySelector('.del').onclick=()=>d.remove();return d;}
function openRide(){
 showView('main');
 const v=document.getElementById('rideView');v.classList.add('open');
 if(!ridePrefilled&&S&&S.prefill_form){ridePrefilled=true;const f=S.prefill_form;
  document.getElementById('fName').value=f.name||'';
  document.getElementById('fLoc').value=(f.search&&f.search.location)||'';
  const sk=document.getElementById('fSkills');sk.innerHTML='';(f.attested_skills||[]).forEach(s=>sk.appendChild(skillRow(s.term,s.evidence)));
  document.getElementById('fBounds').value=(f.hard_boundaries||[]).join('\n');
  document.getElementById('fKeywords').value=((f.search||{}).keywords||[]).join(', ');
  document.getElementById('fMatch').value=((f.search||{}).match_skills||[]).join(', ');
  const jts=(f.search||{}).job_types||((f.search||{}).contract_only?['contract']:[]);
  document.querySelectorAll('.fjt').forEach(c=>c.checked=jts.includes(c.value));
  const wps=(f.search||{}).workplace||[];
  document.querySelectorAll('.fwp').forEach(c=>c.checked=wps.includes(c.value));
  document.getElementById('fHours').value=String((f.search||{}).posted_within_hours||24);
  const srcs=(f.search||{}).sources||[];
  document.getElementById('fSources').innerHTML=['dice','remoteok','arbeitnow','linkedin_guest'].map(s=>'<label style="font-weight:400"><input type="checkbox" class="fsrc" value="'+s+'"'+(srcs.includes(s)?' checked':'')+'> '+s+'</label>').join('');
 }
 v.scrollIntoView({behavior:'smooth'});
}
async function submitRide(){
 const err=document.getElementById('rideErr');err.style.display='none';
 if(S&&!S.can_run){err.textContent='This shared preview is view-only — rides run on the live machine.';err.style.display='block';return;}
 const skills=[...document.querySelectorAll('#fSkills .skillrow')].map(r=>({term:r.querySelector('.st').value,evidence:r.querySelector('.se').value})).filter(s=>s.term.trim());
 let upload=null;
 const fileEl=document.getElementById('fResumeFile');
 if(fileEl.files&&fileEl.files[0]){const f=fileEl.files[0];const bytes=new Uint8Array(await f.arrayBuffer());let bin='';for(let i=0;i<bytes.length;i+=8192){bin+=String.fromCharCode.apply(null,bytes.subarray(i,i+8192));}upload={filename:f.name,data_b64:btoa(bin)};}
 const payload={name:document.getElementById('fName').value,attested_skills:skills,
  hard_boundaries:document.getElementById('fBounds').value.split('\n'),
  raw_resume:document.getElementById('fResume').value,resume_upload:upload,
  search:{keywords:document.getElementById('fKeywords').value.split(','),match_skills:document.getElementById('fMatch').value.split(','),location:document.getElementById('fLoc').value,job_types:[...document.querySelectorAll('.fjt:checked')].map(c=>c.value),workplace:[...document.querySelectorAll('.fwp:checked')].map(c=>c.value),posted_within_hours:parseInt(document.getElementById('fHours').value),sources:[...document.querySelectorAll('.fsrc:checked')].map(c=>c.value)}};
 const btn=document.getElementById('startRide');btn.disabled=true;btn.textContent='Starting…';
 try{const r=await fetch('api/ride',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j=await r.json();
  if(j.started){document.getElementById('rideView').classList.remove('open');cur=null;window.scrollTo({top:0,behavior:'smooth'});load();}
  else{err.textContent=j.error||'Could not start the ride.';err.style.display='block';}
 }catch(e){err.textContent='Could not reach the server.';err.style.display='block';}
 btn.disabled=false;btn.textContent='▶ Start my search';
}
document.getElementById('rideBtn').onclick=openRide;
document.getElementById('cancelRide').onclick=()=>document.getElementById('rideView').classList.remove('open');
document.getElementById('addSkill').onclick=()=>document.getElementById('fSkills').appendChild(skillRow('',''));
document.getElementById('startRide').onclick=submitRide;
document.getElementById('fResumeFile').onchange=e=>{const f=e.target.files&&e.target.files[0];document.getElementById('fResumeFileName').textContent=f?('Selected: '+f.name+' — the Safety Check will read this file'):'';};
document.getElementById('homeBtn').onclick=async()=>{await fetch('api/home',{method:'POST'});cur=null;load();};
document.getElementById('buildMissing').onclick=async()=>{await fetch('api/build_missing',{method:'POST'});load();};
// ---- stats range filter: Today | Yesterday | This month | All time ----
document.querySelectorAll('.rangebtn').forEach(b=>b.onclick=()=>{range=b.dataset.range;document.querySelectorAll('.rangebtn').forEach(x=>x.classList.toggle('on',x===b));_lastSig='';load();});
// ---- profile editor (Your profile tab): profile + sweep, saved together ----
let profSig='',profDirty=false;
function populateProfile(){
 if(!S)return;
 const sig=JSON.stringify([S.candidate,S.skills,S.boundaries,S.spec]);
 if(sig===profSig||profDirty)return;
 profSig=sig;
 const sp=S.spec||{};
 document.getElementById('pName').value=S.candidate||'';
 document.getElementById('pLoc').value=sp.location||'';
 const sk=document.getElementById('pSkills');sk.innerHTML='';(S.skills||[]).forEach(s=>sk.appendChild(skillRow(s.term,s.evidence)));
 document.getElementById('pBounds').value=(S.boundaries||[]).join('\n');
 document.getElementById('pKeywords').value=(sp.keywords||[]).join(', ');
 document.getElementById('pMatch').value=(sp.match_skills||[]).join(', ');
 const jts=sp.job_types||(sp.contract_only?['contract']:[]);
 document.querySelectorAll('.pjt').forEach(c=>c.checked=jts.includes(c.value));
 const wps=sp.workplace||[];
 document.querySelectorAll('.pwp').forEach(c=>c.checked=wps.includes(c.value));
 document.getElementById('pHours').value=String(sp.posted_within_hours||24);
 const srcs=sp.sources||[];
 document.getElementById('pSources').innerHTML=['dice','remoteok','arbeitnow','linkedin_guest'].map(s=>'<label style="font-weight:400"><input type="checkbox" class="psrc" value="'+s+'"'+(srcs.includes(s)?' checked':'')+'> '+s+'</label>').join('');
}
document.getElementById('profileSec').addEventListener('input',()=>{profDirty=true;});
document.getElementById('pSkills').addEventListener('click',()=>{profDirty=true;});
document.getElementById('addPSkill').onclick=()=>{profDirty=true;document.getElementById('pSkills').appendChild(skillRow('',''));};
document.getElementById('saveProfile').onclick=async()=>{
 const err=document.getElementById('profileErr');err.style.display='none';
 const saved=document.getElementById('profileSaved');saved.textContent='';
 const skills=[...document.querySelectorAll('#pSkills .skillrow')].map(r=>({term:r.querySelector('.st').value,evidence:r.querySelector('.se').value})).filter(s=>s.term.trim());
 const payload={name:document.getElementById('pName').value,attested_skills:skills,
  hard_boundaries:document.getElementById('pBounds').value.split('\n'),
  search:{keywords:document.getElementById('pKeywords').value.split(','),match_skills:document.getElementById('pMatch').value.split(','),location:document.getElementById('pLoc').value,job_types:[...document.querySelectorAll('.pjt:checked')].map(c=>c.value),workplace:[...document.querySelectorAll('.pwp:checked')].map(c=>c.value),posted_within_hours:parseInt(document.getElementById('pHours').value),sources:[...document.querySelectorAll('.psrc:checked')].map(c=>c.value)}};
 const btn=document.getElementById('saveProfile');btn.disabled=true;btn.textContent='Saving…';
 try{const r=await fetch('api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j=await r.json();
  if(j.saved){profDirty=false;profSig='';saved.textContent='Saved ✓ — the next Scan for New Roles uses these settings.';_lastSig='';load();}
  else{err.textContent=j.error||'Could not save.';err.style.display='block';}
 }catch(e){err.textContent='Could not reach the server.';err.style.display='block';}
 btn.disabled=false;btn.textContent='💾 Save my profile & search';
};
// ---- apply from the website ----
function applyHtml(){
 if(!cur||!cur.pursuit_id)return '';
 if(cur.pursuit_status==='prepared')return '<p style="margin:0 0 10px"><button class="btn1" id="applyBtn">🚀 Apply to this job now</button> <span class="hint">CMM prepared everything above. Press Apply and the applier submits it on your Dice account — the confirmation lands on the board.</span></p>';
 if(cur.pursuit_status==='apply_requested')return '<p style="margin:0 0 10px"><b>⏳ Apply requested</b> <span class="hint">— the applier is submitting now. Watch the board.</span></p>';
 if(cur.pursuit_status==='applied')return '<p style="margin:0 0 10px"><b>✅ Applied</b> <span class="hint">— the confirmation is on the board.</span></p>';
 return '';
}
function wireApply(){const btn=document.getElementById('applyBtn');if(!btn)return;btn.onclick=async()=>{btn.disabled=true;btn.textContent='Requesting…';try{const r=await fetch('api/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({posting_id:cur.posting_id})});const j=await r.json();if(!j.started){btn.disabled=false;btn.textContent='🚀 Apply to this job now';alert(j.error||'Could not queue the application.');}}catch(e){btn.disabled=false;}load();};}
// ---- stop flow ----
document.getElementById('btnStop').onclick=()=>document.getElementById('stopModal').classList.add('open');
document.getElementById('stopCancel').onclick=()=>document.getElementById('stopModal').classList.remove('open');
async function sendStop(mode){document.getElementById('stopModal').classList.remove('open');await fetch('api/stop',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({mode})});load();}
document.getElementById('stopKeep').onclick=()=>sendStop('keep');
document.getElementById('stopDiscard').onclick=()=>sendStop('discard');
load();setInterval(load,4000);
if(new URLSearchParams(location.search).get('form')){const iv2=setInterval(()=>{if(S){openRide();clearInterval(iv2);}},400);setTimeout(()=>clearInterval(iv2),8000);}
const _open=new URLSearchParams(location.search).get('open');
if(_open){const iv=setInterval(()=>{if(S){const m=S.matches.find(x=>x.posting_id==_open);if(m){cur=m;tab='dossier';showDrawer();clearInterval(iv);}}},400);setTimeout(()=>clearInterval(iv),8000);}
</script></body></html>
"""


# ---------------------------------------------------------------- server

def make_handler(ui):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                body = PAGE.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/state":
                qs = parse_qs(parsed.query)
                self._json(ui.state(qs.get("range", ["all"])[0]))
            elif parsed.path == "/api/download":
                qs = parse_qs(parsed.query)
                try:
                    posting = int(qs.get("posting", ["0"])[0])
                except ValueError:
                    self._json({"error": "bad posting id"}, 400)
                    return
                got = ui.download_resume(posting, qs.get("fmt", [""])[0])
                if got is None:
                    self._json({"error": "not found"}, 404)
                    return
                data, ctype, fname = got
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{fname}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            elif parsed.path == "/api/doc":
                q = parse_qs(parsed.query)
                try:
                    pid = int(q.get("posting", ["0"])[0])
                except ValueError:
                    return self._json({"error": "bad posting"}, 400)
                rendered = ui.doc_html(pid, q.get("kind", [""])[0])
                if rendered is None:
                    return self._json({"error": "not found"}, 404)
                self._json({"html": rendered})
            else:
                self._json({"error": "not found"}, 404)

        def do_POST(self):
            if self.path == "/api/run":
                self._json({"started": ui.start_sweep()})
            elif self.path == "/api/build_missing":
                self._json({"started": ui.start_build_missing()})
            elif self.path == "/api/run_fixture":
                self._json({"started": ui.start_fixture()})
            elif self.path == "/api/home":
                self._json({"started": ui.go_home()})
            elif self.path == "/api/apply":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    posting = int(payload.get("posting_id") or 0)
                except (ValueError, json.JSONDecodeError):
                    self._json({"started": False, "error": "Bad request."}, 400)
                    return
                ok, info = ui.request_apply(posting)
                self._json({"started": ok, "status": info} if ok
                           else {"started": False, "error": info},
                           200 if ok else 400)
            elif self.path == "/api/stop":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    payload = {}
                self._json({"stopping": ui.request_stop(
                    payload.get("mode", "keep"))})
            elif self.path == "/api/profile":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._json({"saved": False, "error": "Bad form data."}, 400)
                    return
                ok, info = ui.update_profile(payload)
                self._json({"saved": ok} if ok
                           else {"saved": False, "error": info},
                           200 if ok else 400)
            elif self.path == "/api/ride":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._json({"started": False, "error": "Bad form data."}, 400)
                    return
                ok, info = ui.start_ride(payload)
                self._json({"started": ok, "slug": info} if ok
                           else {"started": False, "error": info},
                           200 if ok else 400)
            else:
                self._json({"error": "not found"}, 404)

    return Handler


def serve(out_dir, form_path, demo_dir, fixture_out, port=8765):
    ui = UI(out_dir, form_path, demo_dir, fixture_out)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ui))
    print(f"CMM Mission Control: http://127.0.0.1:{port}")
    server.serve_forever()
