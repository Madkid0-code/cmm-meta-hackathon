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

from . import meta_client, profile, store, verifier

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
                "linkedin": "LinkedIn (signed in)"}


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
            if mm.group(1) == "linkedin":
                # The signed-in source gets its own story step: judges
                # and seekers alike must SEE that a Muse browser agent in the
                # seeker's own session did this — it is the advanced
                # part, not a public feed.
                add("🕵️", "", f"Your browser scout brought "
                    f"{mm.group(2)} jobs from LinkedIn",
                    "A Muse browser agent working inside YOUR signed-in "
                    "LinkedIn session ran your filters (contract · last "
                    "24 hours) and read each job in full — that is why "
                    "these are fresher and richer than anything a public "
                    "listing or a guest visit can see. "
                    f"({mm.group(3)} of them are new.)", ts)
            else:
                add("🔎", "", f"Found {mm.group(2)} jobs on {src}",
                    (f"{mm.group(3)} of them are new — the rest were "
                     "already in the pile from earlier scans."
                     if mm.group(3) != mm.group(2)
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

KNOWN_SOURCES = ("dice", "linkedin")


class UI:
    def __init__(self, out_dir, form_path, demo_dir, fixture_out,
                 sandbox=False):
        self.out_dir = out_dir
        self.form_path = form_path
        self.demo_dir = demo_dir
        self.fixture_out = fixture_out
        # Sandbox mode = the judge demo link: Connect lands Connected in
        # one click under the name typed into the form, no credentials
        # are asked, seen, or stored, and the page says so (banner +
        # disclaimer, Scout box hidden). Off by default — a normal
        # instance behaves exactly as before.
        self.sandbox = bool(sandbox)
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

    # -- portal connections ------------------------------------------
    PORTAL_NAMES = {"dice": "Dice", "linkedin": "LinkedIn"}

    def _connections_db(self):
        """(db, candidate) for the connections table, or (None, name)."""
        from attested import store as _store
        form = profile.load_form(self.form_path)
        db_path = os.path.join(self.out_dir, "attested.db")
        if not os.path.exists(db_path):
            _store.connect(db_path).close()
        return _store.connect(db_path), form["name"]

    def connect_portal(self, portal):
        """The Connect button: record the request; a Muse browser agent
        opens the portal's OWN sign-in page and the seeker signs in there.
        CMM never sees or stores the password."""
        if self.readonly():
            return False, "View-only preview — connecting is disabled."
        if portal not in self.PORTAL_NAMES:
            return False, "Unknown portal."
        db, candidate = self._connections_db()
        from attested import store as _store
        name = self.PORTAL_NAMES[portal]
        if self.sandbox:
            # Judge demo link: Connect is one click and lands Connected
            # instantly, under the name the judge typed into the form.
            # No credentials are asked, seen, or stored, and no
            # 'connecting' state persists for any executor to pick up —
            # the disclaimer on the page states exactly this.
            _store.set_connection(
                db, portal, candidate, "connected",
                display_name=candidate,
                detail="demo sandbox — one-click connect")
            db.close()
            self._append_event(
                "connector",
                f"Connect {name} pressed — judge demo link: connected "
                f"instantly as {candidate}. No credentials were asked "
                "or stored; in the real product CMM Scout confirms "
                "the connection inside your own browser.")
            return True, "connected"
        own_browser = _store.get_scout_code(db, candidate) is not None
        _store.set_connection(db, portal, candidate, "connecting",
                              detail="own_browser" if own_browser else None)
        db.close()
        if own_browser:
            self._append_event(
                "connector",
                f"Connect {name} pressed — {name}'s own sign-in page "
                f"opens in a new tab in your browser; sign in there. "
                "CMM Scout confirms it from your own browser — no "
                "agent involved. CMM never sees or stores your "
                "password.")
        else:
            self._append_event(
                "connector",
                f"Connect {name} pressed — {name}'s own sign-in page "
                f"opens in a new tab in your browser; sign in there. "
                f"A Muse browser agent confirms the connection and "
                "this card flips to Connected. CMM never sees or "
                "stores your password.")
        return True, "connecting"

    def disconnect_portal(self, portal):
        if self.readonly():
            return False, "View-only preview — connecting is disabled."
        if portal not in self.PORTAL_NAMES:
            return False, "Unknown portal."
        db, candidate = self._connections_db()
        from attested import store as _store
        _store.set_connection(db, portal, candidate, "not_connected",
                              display_name=None, detail=None)
        db.execute(
            "UPDATE portal_connections SET display_name=NULL,"
            " connected_at=NULL WHERE portal=? AND candidate=?",
            (portal, candidate))
        db.commit()
        db.close()
        self._append_event(
            "connector",
            f"{self.PORTAL_NAMES[portal]} disconnected — the agent "
            "forgets that session. You can reconnect anytime.")
        return True, "not_connected"

    # -- CMM Scout: confirmation inside the seeker's OWN browser -------
    # The Scout browser helper lives in the seeker's browser, paired by
    # a one-time code. It reads the name the portal itself displays in
    # that browser and posts it here. From pairing on, connections are
    # confirmed with NO agent and NO saved login on CMM's side — the
    # session never leaves the seeker's browser, and CMM stores only
    # portal / status / displayed name, exactly like the agent path.
    def scout_state(self):
        db, candidate = self._connections_db()
        from attested import store as _store
        code = _store.get_scout_code(db, candidate)
        db.close()
        return {"paired": code is not None, "code": code}

    def generate_scout_code(self):
        if self.readonly():
            return False, "View-only preview — pairing is disabled."
        db, candidate = self._connections_db()
        from attested import store as _store
        import secrets
        alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        _store.set_scout_code(db, candidate, code)
        db.close()
        self._append_event(
            "connector",
            "CMM Scout pairing code created — from now on, Connect "
            "confirms inside your own browser. No agent, no saved "
            "login on CMM's side.")
        return True, code

    def confirm_connection(self, portal, code, display_name):
        """CMM Scout posts here from the seeker's own browser: the
        portal page there shows the seeker signed in as display_name.
        The pairing code is the only credential — it identifies whose
        browser this is, never a portal password."""
        if portal not in self.PORTAL_NAMES:
            return False, "Unknown portal."
        name = " ".join(str(display_name or "").split())[:80]
        if not name:
            return False, "No signed-in name was reported."
        db, _candidate = self._connections_db()
        from attested import store as _store
        owner = _store.candidate_for_scout_code(db, str(code or ""))
        if owner is None:
            db.close()
            return False, "Pairing code not recognised."
        _store.set_connection(db, portal, owner, "connected",
                              display_name=name, detail=None)
        db.close()
        self._append_event(
            "connector",
            f"{self.PORTAL_NAMES[portal]} connected — CMM Scout "
            f"confirmed it from your own browser: signed in as "
            f"{name}. No agent involved; CMM stored no password.")
        return True, "connected"

    def scout_zip(self):
        """The CMM Scout helper as a downloadable zip (built on the
        fly from the repo's extension/ folder, stdlib zipfile)."""
        import io
        import zipfile
        root = os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))
        ext_dir = os.path.join(root, "extension")
        if not os.path.isdir(ext_dir):
            return None
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _dirs, files in os.walk(ext_dir):
                for fn in sorted(files):
                    full = os.path.join(dirpath, fn)
                    arc = os.path.join(
                        "cmm-scout",
                        os.path.relpath(full, ext_dir))
                    z.write(full, arc)
        return buf.getvalue()

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
        # Just-in-time connection: submitting happens inside the seeker's
        # signed-in portal session, so a Dice/LinkedIn application first
        # needs that portal connected.
        src = db.execute(
            "SELECT p.source FROM job_postings p WHERE p.id = ?",
            (posting_id,)).fetchone()
        if src and src[0] in self.PORTAL_NAMES:
            portal = src[0]
            form = profile.load_form(self.form_path)
            from attested import store as _store
            cdb = _store.connect(db_path)
            conn = _store.get_connection(cdb, portal, form["name"])
            cdb.close()
            if conn is None or conn["status"] != "connected":
                db.close()
                pname = self.PORTAL_NAMES[portal]
                self._append_event(
                    "connector",
                    f"Apply needs {pname} connected first — applications "
                    f"go out inside your signed-in {pname} session. "
                    f"Use Connect {pname} in Your search.")
                return False, (
                    f"Connect {pname} first — this application is "
                    f"submitted inside your signed-in {pname} session. "
                    f"You'll find Connect {pname} in Your search.")
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

    def _save_merged_form(self, merged_payload, event_message):
        """Validate a full payload (profile + search merged over the
        current form), write it back to the current form file, re-render
        the attested profile beside the store, and log the change.
        Preserves form keys no editor manages (note, raw_resume_file).
        Nothing already found, built, or applied is touched."""
        form, err = self._form_from_payload(merged_payload)
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
        self._append_event("system", event_message(form))
        return True, "saved"

    def _current_payload(self):
        """The current form, shaped like an editor payload."""
        try:
            cur = profile.load_form(self.form_path)
        except Exception:
            cur = {"name": "", "attested_skills": [], "hard_boundaries": [],
                   "search": {}}
        return {"name": cur.get("name", ""),
                "attested_skills": cur.get("attested_skills", []),
                "hard_boundaries": cur.get("hard_boundaries", []),
                "search": cur.get("search", {})}

    def update_profile(self, payload):
        """The Your-profile button's Save my profile: ONLY the person
        changes (name, attested skills, hard boundaries). The search
        settings are carried over untouched — search is a separate
        button with its own save (owner, 2026-10-28)."""
        if self.readonly():
            return False, "View-only preview — profile editing is disabled."
        merged = self._current_payload()
        for key in ("name", "attested_skills", "hard_boundaries"):
            if key in payload:
                merged[key] = payload[key]
        return self._save_merged_form(
            merged,
            lambda form: f"Profile updated from the website — "
                         f"{form['name']}: {len(form['attested_skills'])} "
                         f"attested skills saved.")

    def update_search(self, payload):
        """The Your-search button's Save my search: ONLY the sweep
        changes (phrases, match skills, location, sources, job types,
        workplace, posted-within). The person's attested profile is
        carried over untouched, and the next Scan for New Roles shops
        with the new settings."""
        if self.readonly():
            return False, "View-only preview — search editing is disabled."
        merged = self._current_payload()
        search = payload.get("search") or payload
        merged["search"] = search
        return self._save_merged_form(
            merged,
            lambda form: f"Search updated from the website — "
                         f"{' / '.join(form['search']['keywords'])} "
                         f"({form['search']['location']}). "
                         f"The next scan uses these.")

    def profile_resume(self, payload):
        """The Your-profile upload (owner, 2026-10-30 — 'upload first,
        then the profile fills in around it'): read the seeker's resume
        file, audit it against their attested profile with the SAME
        deterministic guardrail the pipeline runs at intake
        (verifier.check_draft), and report the claims struck plus the
        attested skills the resume confirms. The upload becomes the
        form's raw resume, so the next Scan's intake check re-runs this
        exact audit in the open. Uploading NEVER edits the attested
        profile itself: the person reviews the editor and attests with
        Save my profile — the human attests, the machine only checks."""
        if self.readonly():
            return False, {"error": "View-only preview — resume upload "
                                    "is disabled."}
        upload = payload.get("resume_upload") or {}
        if not upload.get("data_b64"):
            return False, {"error": "Choose a resume file first "
                                    "(.docx, .pdf, .txt or .md)."}
        import base64
        from . import ingest
        try:
            text = ingest.extract_text(
                upload.get("filename", ""),
                base64.b64decode(upload["data_b64"]))
        except ValueError as e:
            return False, {"error": str(e)}
        except Exception:
            return False, {"error": "That file could not be read — "
                                    "try a .docx, .pdf, .txt or .md."}
        form = profile.load_form(self.form_path)
        profile_text = profile.render_profile_md(form)
        attested = verifier.parse_profile(profile_text)
        strikes = verifier.check_draft(text, attested, profile_text)
        low = text.lower()
        confirmed = [t for t in attested if t.lower() in low]
        name_detected = None
        for line in text.splitlines():
            cand = line.strip().strip("#").strip()
            if not cand:
                continue
            # A resume's first line is usually 'Name — Title (note)':
            # the name is the part before the first dash or bracket.
            cand = re.split(r"\s+[—–(]", cand)[0].strip()
            words = cand.split()
            if (2 <= len(words) <= 5 and "@" not in cand
                    and not any(ch.isdigit() for ch in cand)
                    and len(cand) <= 60):
                name_detected = cand
            break
        form_dir = os.path.dirname(os.path.abspath(self.form_path))
        with open(os.path.join(form_dir, "uploaded_resume.md"), "w") as f:
            f.write(text)
        form["raw_resume_file"] = "uploaded_resume.md"
        with open(self.form_path, "w") as f:
            json.dump(form, f, indent=1)
        self._append_event(
            "guardrail",
            f"Resume uploaded on Your profile — intake check struck "
            f"{len(strikes)} claim(s); the resume confirms "
            f"{len(confirmed)} attested skill(s).")
        return True, {"strikes": strikes, "confirmed": confirmed,
                      "name_detected": name_detected,
                      "skills_total": len(attested)}

    def reset_demo(self):
        """Reset demo (owner, 2026-10-30): wipe the demo account's
        pipeline back to empty — every posting, match, built resume and
        pursuit, plus the generated dossier files, the event feed and
        the call queue — so a fresh run (or a demo take) starts from a
        truly blank board. The PERSON stays: form, attested profile and
        the scout's inbox deposits are the account, not the run.
        Home store only; never during a run."""
        if self.readonly():
            return False, "View-only preview — reset is disabled."
        if os.path.abspath(self.form_path) != os.path.abspath(self._home_form):
            return False, "Reset belongs to the demo account, not a ride."
        if any(self._running.values()):
            return False, ("A run is in progress — let it finish (or stop "
                           "it) before resetting.")
        db = store.connect(os.path.join(self.out_dir, "attested.db"))
        try:
            for table in ("pursuits", "candidate_resumes",
                          "candidate_matches", "job_postings",
                          "job_posting_raw", "sweep_configs"):
                db.execute(f"DELETE FROM {table}")
            db.commit()
        finally:
            db.close()
        import shutil
        matches_dir = os.path.join(self.out_dir, "matches")
        if os.path.isdir(matches_dir):
            shutil.rmtree(matches_dir)
        for name in ("events.jsonl", "call_queue.md"):
            path = os.path.join(self.out_dir, name)
            if os.path.exists(path):
                os.remove(path)
        self._append_event("system", "Demo reset — the board is empty. "
                                     "A fresh run starts from nothing.")
        return True, "reset"

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
        connections = {p: {"status": "not_connected", "display_name": None,
                           "detail": None, "connected_at": None}
                       for p in ("dice", "linkedin")}
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
            connections = {p: {"status": "not_connected",
                               "display_name": None, "detail": None,
                               "connected_at": None}
                           for p in ("dice", "linkedin")}
            try:
                for crow in db.execute(
                        "SELECT portal, status, display_name, detail,"
                        " connected_at FROM portal_connections"
                        " WHERE candidate=?", (form["name"],)):
                    if crow["portal"] in connections:
                        connections[crow["portal"]] = {
                            "status": crow["status"],
                            "display_name": crow["display_name"],
                            "detail": crow["detail"],
                            "connected_at": crow["connected_at"]}
            except Exception:
                pass  # pre-connections store: everything unconnected
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
        result = {
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
            "connections": connections,
            "scout": self.scout_state(),
            "matches": matches, "events": merged,
            "feed": human_feed(merged),
            "intake": intake,
            "running": dict(self._running), "can_run": self.can_run(),
            "stopping": bool(self._stop_req["sweep"]
                             or self._stop_req["fixture"]),
            "mode": "ride" if self._ride_active else "demo",
            "prefill_form": profile.load_form(self._home_form),
        }
        if self.sandbox:
            # Only the judge demo link advertises sandbox mode; a normal
            # instance's state payload is unchanged.
            result["sandbox"] = True
        return result

    # -- served page ---------------------------------------------------
    def page(self):
        """The Mission Control page. In normal mode this is PAGE itself,
        byte-for-byte. In sandbox mode (the judge demo link) the same
        page is adjusted in exactly four places: a judge banner across
        the top, an accounts hint that makes no sign-in-tab claim, the
        CMM Scout box hidden, no portal tab opened by the Connect
        click, and the demo-vs-real disclaimer directly under the
        accounts panel."""
        if not self.sandbox:
            return PAGE
        html = PAGE
        banner = (
            '<div style="background:#FFF7E0;border-bottom:2px solid '
            '#E8C96A;color:#6B5200;padding:12px 20px;font-size:15px;'
            'text-align:center"><b>⚖️ Judge demo</b> — enter your name '
            'in <b>Your profile</b>: that is your sign-in on this link, '
            'no password needed.</div>\n')
        html = html.replace('<div class="layout">',
                            banner + '<div class="layout">', 1)
        html = html.replace(
            "press Connect and the portal's own sign-in page opens in "
            "a new tab — you sign in there yourself; CMM never sees or "
            "stores your password",
            "press Connect — on this judge demo link you are connected "
            "instantly, and no credentials are asked (see the note "
            "below)", 1)
        html = html.replace(
            '<div id="scoutBox" style="margin-top:10px"></div>',
            '<div id="scoutBox" style="display:none"></div>', 1)
        disclaimer = (
            '</div>\n'
            '     <p style="grid-column:1/-1;background:#FFF7E0;'
            'border:1px solid #E8C96A;border-radius:12px;'
            'padding:10px 14px;color:#6B5200;font-size:13.5px;'
            'margin:0">Demo link: Connect here is one click and asks '
            'for no credentials, so you can evaluate CMM safely. In '
            'the real product, you sign in on LinkedIn&rsquo;s or '
            'Dice&rsquo;s own page yourself, and only with the help '
            'of CMM Scout do you get connected to CMM. We never ask '
            'for your password, and we never store your password or '
            'cookies.</p>')
        html = html.replace(
            '<div id="scoutBox" style="display:none"></div></div>',
            '<div id="scoutBox" style="display:none"></div>'
            + disclaimer, 1)
        html = html.replace(
            "if(loginUrls[portal])window.open(loginUrls[portal],"
            "'_blank');",
            "if(!(S&&S.sandbox)&&loginUrls[portal])"
            "window.open(loginUrls[portal],'_blank');", 1)
        return html

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
  <a href="#" data-nav="profile"><span class="ni">👤</span>Your profile</a>
  <a href="#" data-nav="search"><span class="ni">🔎</span>Your search</a>
  <a href="#" data-nav="main" class="on"><span class="ni">⌂</span>Today</a>
  <a href="#" data-nav="main" data-scroll="boardSec"><span class="ni">▦</span>Your pipeline</a>
  <a href="#" data-nav="how"><span class="ni">🧠</span>How CMM works</a>
  <a href="#" data-nav="targets"><span class="ni">🎯</span>Live Market Targets</a>
  <a href="#" data-nav="safety"><span class="ni">🛡️</span>Safety check</a>
 </nav>
 <div class="sidecard"><div class="t">🛡️ The honesty rule</div><div class="x">The LLM proposes. The code disposes. Nothing is ever submitted without your click.</div></div>
 <div class="candrow"><div class="candava" id="candAva">?</div><div><div class="cn" id="cand"></div><div class="cm" id="candMode"></div></div></div>
</aside>
<div class="main" id="top">
 <div class="topbar">
  <div><div class="dateline" id="dateLine"></div><h1 class="greet" id="greet">Your job hunt</h1></div>
  <div class="topbtns">
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
    <button class="btn2" id="resetDemo" title="Wipe every found job, resume and application — your profile and search stay">⟲ Reset demo</button>
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
   <div style="border-top:1px solid #EEF4F0;margin-top:16px;padding-top:14px">
    <div class="cardhead" style="font-size:15px">📄 Start with your resume</div>
    <p class="shieldnote">Upload the resume you were about to send. The machine reads it and checks every claim against your attested profile below — claims it cannot back get struck in the open, and the skills your resume does back are confirmed. The profile then fills in around the upload: review it, and attest with <b>Save my profile</b>. Uploading never changes your profile by itself — you stay the one who attests.</p>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <input type="file" id="prFile" accept=".docx,.pdf,.txt,.md">
      <button class="btn1" id="prUpload" type="button">Upload &amp; check my resume</button>
      <span class="hint" id="prFileName"></span>
    </div>
    <div id="prResult" style="display:none;margin-top:12px"></div>
    <div class="err" id="prErr"></div>
   </div>
   <div style="border-top:1px solid #EEF4F0;margin-top:16px;padding-top:14px">
    <div class="cardhead" style="font-size:15px">✏️ Update your profile</div>
    <p class="shieldnote">This is you — the skills you can defend and the lines you will not cross. Change anything and hit <b>Save my profile</b>. Your search settings live under <b>Your search</b>, with their own save. Jobs already found, resumes already built, and applications already sent are not changed.</p>
    <div class="fgrid">
     <div class="fld"><label>Your name</label><input type="text" id="pName"></div>
     <div class="fld" style="grid-column:1/-1"><label>Your skills — claim only what you can defend <span class="hint">skill + your proof</span></label><div id="pSkills"></div><button class="btn2" id="addPSkill" type="button">＋ Add a skill</button></div>
     <div class="fld" style="grid-column:1/-1"><label>Hard boundaries <span class="hint">one per line — things you have NOT done and will not claim</span></label><textarea id="pBounds" rows="3"></textarea></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center"><button class="btn1" id="saveProfile" type="button">💾 Save my profile</button><span class="hint" id="profileSaved"></span></div>
    <div class="err" id="profileErr"></div>
   </div>
  </section>
 </div>

 <div id="viewSearch" style="display:none">
  <section class="card" id="searchSec">
   <div class="cardhead" style="font-size:17px">🔎 Your Search</div>
   <div class="spec" id="spec"></div>
   <div style="border-top:1px solid #EEF4F0;margin-top:16px;padding-top:14px">
    <div class="cardhead" style="font-size:15px">✏️ Update your search</div>
    <p class="shieldnote">This is what the machine shops for. Change anything and hit <b>Save my search</b> — the next <b>Scan for New Roles</b> uses the new settings. Your attested profile lives under <b>Your profile</b>, with its own save.</p>
    <div class="fgrid">
     <div class="fld"><label>Where should it look? <span class="hint">location</span></label><input type="text" id="sLoc"></div>
     <div class="fld"><label>Search phrases <span class="hint">comma-separated</span></label><input type="text" id="sKeywords"></div>
     <div class="fld"><label>Match skills <span class="hint">comma-separated — a role stays in only if it asks for ≥2 of these</span></label><input type="text" id="sMatch"></div>
     <div class="fld"><label>Look on</label><div class="srcs" id="sSources"></div></div>
     <div class="fld" style="grid-column:1/-1"><label>Your accounts <span class="hint">press Connect and the portal's own sign-in page opens in a new tab — you sign in there yourself; CMM never sees or stores your password</span></label><div id="connList"></div><div id="scoutBox" style="margin-top:10px"></div></div>
     <div class="fld"><label>Job type</label><div class="srcs"><label style="font-weight:400"><input type="checkbox" class="sjt" value="contract"> Contract</label><label style="font-weight:400"><input type="checkbox" class="sjt" value="full_time"> Full-time</label><label style="font-weight:400"><input type="checkbox" class="sjt" value="part_time"> Part-time</label></div><span class="hint">none ticked = any type</span></div>
     <div class="fld"><label>Workplace</label><div class="srcs"><label style="font-weight:400"><input type="checkbox" class="swp" value="remote"> Remote</label><label style="font-weight:400"><input type="checkbox" class="swp" value="hybrid"> Hybrid</label><label style="font-weight:400"><input type="checkbox" class="swp" value="onsite"> On-site</label></div><span class="hint">none ticked = anywhere · posted in the last</span> <select id="sHours"><option value="12">12h</option><option value="24">24h</option><option value="48">48h</option><option value="72">3 days</option><option value="168">7 days</option></select></div>
    </div>
    <div style="margin-top:14px;display:flex;gap:8px;align-items:center"><button class="btn1" id="saveSearch" type="button">💾 Save my search</button><span class="hint" id="searchSaved"></span></div>
    <div class="err" id="searchErr"></div>
   </div>
  </section>
 </div>

 <div id="viewHow" style="display:none">
  <section class="card">
   <div class="cardhead" style="font-size:17px">🧠 How CMM works — the whole machine, in the open</div>
   <p class="shieldnote" style="font-size:14px"><b>Code fetches and judges. Muse Spark reads and writes. Muse browser agents walk in your signed-in sessions. You decide.</b> Every worker below is real, and the number on each step is counted live from this account's own pipeline.</p>
   <div style="display:flex;flex-direction:column;gap:10px;margin-top:14px">
    <div style="display:flex;gap:12px;align-items:flex-start;background:#F7FBF8;border:1px solid #E3EFE6;border-radius:12px;padding:12px 14px"><div style="font-size:22px">🔎</div><div style="flex:1"><b>The Shopper</b> <span style="background:#E8F5E9;color:#1B7A43;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">PLAIN CODE — NO AI</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">Takes your search settings and reads Dice directly — search results and every job's full description. No login needed; Dice shows its listings to anyone.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwRaw">–</div><div style="font-size:11px;color:#7A9186">jobs collected</div></div></div>
    <div style="display:flex;gap:12px;align-items:flex-start;background:#F7FBF8;border:1px solid #E3EFE6;border-radius:12px;padding:12px 14px"><div style="font-size:22px">🕵️</div><div style="flex:1"><b>The Scout</b> <span style="background:#FFF3E0;color:#B26A00;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">MUSE BROWSER AGENT — SIGNED IN AS YOU</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">LinkedIn hides its real search — contract filter, last 24 hours, full descriptions — behind a sign-in wall. So a Muse browser agent walks in <i>inside your own signed-in LinkedIn session</i>, runs your filters like a person would, reads each job in full, and hands the jobs to the Shopper. Read-only: it never applies, never saves, never messages — and it stops cold if LinkedIn asks for a security check.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwLinkedin">–</div><div style="font-size:11px;color:#7A9186">LinkedIn roles audited</div></div></div>
    <div style="display:flex;gap:12px;align-items:flex-start;background:#F7FBF8;border:1px solid #E3EFE6;border-radius:12px;padding:12px 14px"><div style="font-size:22px">📖</div><div style="flex:1"><b>The Reader</b> <span style="background:#EDE9FE;color:#5B4BC4;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">MUSE SPARK — META MODEL API</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">Reads every surviving job description in full and lists exactly what the job demands — skills, tools, years. It only reads; it is not allowed to judge.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwJudged">–</div><div style="font-size:11px;color:#7A9186">roles read in full</div></div></div>
    <div style="display:flex;gap:12px;align-items:flex-start;background:#F7FBF8;border:1px solid #E3EFE6;border-radius:12px;padding:12px 14px"><div style="font-size:22px">⚖️</div><div style="flex:1"><b>The Checker</b> <span style="background:#E8F5E9;color:#1B7A43;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">PLAIN CODE — NEVER AI</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">The heart of CMM. Takes every demand from the Reader and checks it against your attested profile, one by one: provable = <b>Defensible</b>, unproven = <b>Vulnerable</b>. It knows Snowflake is not Snowpark. An AI can be talked into things; this code cannot — which is why the verdict (pass / set aside) is decided here, never by a model, and never as a score.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwPass">–</div><div style="font-size:11px;color:#7A9186">roles passed the audit</div></div></div>
    <div style="display:flex;gap:12px;align-items:flex-start;background:#F7FBF8;border:1px solid #E3EFE6;border-radius:12px;padding:12px 14px"><div style="font-size:22px">✍️</div><div style="flex:1"><b>The Writer</b> <span style="background:#EDE9FE;color:#5B4BC4;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">MUSE SPARK — GUARDED</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">Writes the tailored resume for each passed role — but it may only use claims the Checker marked Defensible. The Checker then re-reads the finished resume; one unprovable word and the resume is rejected and rewritten.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwResumes">–</div><div style="font-size:11px;color:#7A9186">tailored resumes built</div></div></div>
    <div style="display:flex;gap:12px;align-items:flex-start;background:#F7FBF8;border:1px solid #E3EFE6;border-radius:12px;padding:12px 14px"><div style="font-size:22px">📮</div><div style="flex:1"><b>The Applier</b> <span style="background:#FFF3E0;color:#B26A00;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">MUSE BROWSER AGENT — ON YOUR CLICK</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">Prepares the complete application package for every passed role. When you press <b>Apply</b> on a Dice role, a Muse browser agent submits it on your own signed-in Dice session and brings back the employer's confirmation, word for word. Roles that apply on a company's own site are prepared for you, and the machine says so honestly instead of pretending.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwPursuits">–</div><div style="font-size:11px;color:#7A9186">applications prepared</div></div></div>
    <div style="display:flex;gap:12px;align-items:flex-start;background:#EFF8F1;border:1px solid #CFE8D4;border-radius:12px;padding:12px 14px"><div style="font-size:22px">🧑</div><div style="flex:1"><b>You</b> <span style="background:#1B7A43;color:#fff;border-radius:20px;padding:1px 9px;font-size:11px;font-weight:700">THE HUMAN — FINAL WORD</span><div style="color:#46645A;font-size:13.5px;margin-top:3px">Nothing is ever sent without your click. Your profile is the law the whole machine answers to — change it under <b>Your profile</b>, change the hunt under <b>Your search</b>.</div></div><div style="text-align:right"><div style="font-size:20px;font-weight:800" id="hwApplied">–</div><div style="font-size:11px;color:#7A9186">applied, with proof</div></div></div>
   </div>
   <p class="shieldnote" style="margin-top:14px">Where the seats are: the Shopper, Checker, Reader and Writer live inside CMM itself. The two Muse browser-agent seats — Scout and Applier — must work inside <i>your</i> signed-in sessions, because that is the only place LinkedIn and Dice let a member act. In the product, the Scout ships as a small add-on in your own browser, so your login never leaves your machine; in this live demo those seats are worked by <b>Muse browser agents</b> (Meta's Muse agent platform) so you can watch them happen — Muse Spark does the thinking, Muse browser agents do the acting.</p>
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
 const hwSet=(id,val)=>{const el=document.getElementById(id);if(el)el.textContent=val;};
 hwSet('hwRaw',c.raw);hwSet('hwJudged',c.judged);hwSet('hwPass',c.pass);
 hwSet('hwResumes',c.resumes);hwSet('hwPursuits',c.pursuits);hwSet('hwApplied',c.applied||0);
 hwSet('hwLinkedin',(S.matches||[]).filter(m=>m.source==='linkedin').length);
 document.getElementById('rangeNote').textContent=(S.range_label||'All time')+(S.range==='today'?' — fresh every morning, resets at midnight ET':'')+' · the board below always shows everything still waiting for you, from every day';
 populateProfile();populateSearch();renderConnections();
 document.getElementById('skills').innerHTML=S.skills.map(s=>'<p class="skill"><b>'+esc(s.term)+'</b> — '+esc(s.evidence)+'</p>').join('');
 document.getElementById('never').innerHTML='<b>Your hard boundaries:</b> '+S.boundaries.map(esc).join(' · ');
 const sp=S.spec;
 document.getElementById('spec').innerHTML='Your search: <b>'+esc(sp.keywords.join(' / '))+'</b> · '+esc(sp.location)+' · '+((sp.job_types&&sp.job_types.length)?esc(sp.job_types.join(', ').replace('_','-')):(sp.contract_only?'contract':'any type'))+((sp.workplace&&sp.workplace.length)?' · '+esc(sp.workplace.join(', ')):'')+' · last '+sp.posted_within_hours+'h · a role stays in only if it asks for ≥2 of: '+esc(sp.match_skills.join(', '));
 const why=t=>t==='invented_metric'?'a number with no source behind it':t==='boundary_violation'?'it crosses a hard boundary you set':'not in your attested profile';
 document.getElementById('intake').innerHTML=S.intake.length?S.intake.map(f=>'<div class="flag"><b>Kept out —</b> <s>'+esc(f.term)+'</s><div class="why">'+esc(why(f.type))+' · blocked by the safety check before anything was built</div></div>').join(''):'<p class="shieldnote">Nothing flagged — your draft matches your attested profile.</p>';
 const rf=document.getElementById('runFixture'),rs=document.getElementById('runSweep');
 rf.disabled=!S.can_run||S.running_fixture;rs.disabled=!S.can_run||S.running_sweep;
 document.getElementById('runNote').textContent=S.can_run?'':"You're viewing the real results. The live buttons need the API key (or the dev preview).";
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
function showView(v){['viewMain','viewTargets','viewProfile','viewSearch','viewHow','viewSafety'].forEach(id=>{document.getElementById(id).style.display=(id==='view'+v.charAt(0).toUpperCase()+v.slice(1)?'':'none');});document.querySelectorAll('nav a[data-nav]').forEach(a=>a.classList.toggle('on',a.dataset.nav===v));window.scrollTo({top:0,behavior:'smooth'});}
document.querySelectorAll('nav a[data-nav]').forEach(a=>a.onclick=e=>{e.preventDefault();showView(a.dataset.nav);if(a.dataset.scroll)setTimeout(()=>{const el=document.getElementById(a.dataset.scroll);if(el)el.scrollIntoView({behavior:'smooth'});},40);});
document.querySelectorAll('.tab[data-t]').forEach(t=>t.onclick=()=>{tab=t.dataset.t;showDrawer();});
document.getElementById('closeDrawer').onclick=()=>{cur=null;document.getElementById('drawer').style.display='none';};
document.getElementById('runSweep').onclick=async()=>{await fetch('api/run',{method:'POST'});load();};
document.getElementById('resetDemo').onclick=async()=>{if(!confirm('Reset the demo? Every found job, built resume and application on this board is wiped. Your profile and your search stay exactly as they are.'))return;try{const r=await fetch('api/reset-demo',{method:'POST'});const j=await r.json();if(!j.ok){alert(j.error||'Could not reset.');return;}}catch(e){alert('Could not reach the server.');return;}cur=null;_lastSig='';load();};
// ---- profile resume upload: audit + fill-in (Your profile) ----
document.getElementById('prFile').addEventListener('change',e=>{const f=e.target.files&&e.target.files[0];document.getElementById('prFileName').textContent=f?('Selected: '+f.name):'';});
document.getElementById('prUpload').onclick=async()=>{
 const err=document.getElementById('prErr');err.style.display='none';
 const inp=document.getElementById('prFile');const f=inp.files&&inp.files[0];
 if(!f){err.textContent='Choose a resume file first.';err.style.display='block';return;}
 const btn=document.getElementById('prUpload');btn.disabled=true;btn.textContent='Checking…';
 try{
  const buf=await f.arrayBuffer();const bytes=new Uint8Array(buf);let bin='';
  for(let i=0;i<bytes.length;i+=32768){bin+=String.fromCharCode.apply(null,bytes.subarray(i,i+32768));}
  const r=await fetch('api/profile-resume',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({resume_upload:{filename:f.name,data_b64:btoa(bin)}})});
  const j=await r.json();
  if(!j.ok){err.textContent=j.error||'Could not check that resume.';err.style.display='block';}
  else{
   const box=document.getElementById('prResult');box.style.display='block';let h='';
   if(j.name_detected){document.getElementById('pName').value=j.name_detected;profDirty=true;h+='<p class="shieldnote">We read the name on the resume as <b>'+esc(j.name_detected)+'</b> — it is filled into your profile below; change it there if it is wrong.</p>';}
   if(j.strikes&&j.strikes.length){h+='<div class="auditbox"><b>🛡️ Intake check — '+j.strikes.length+' claim'+(j.strikes.length>1?'s':'')+' struck:</b>'+j.strikes.map(s=>'<div style="margin-top:4px"><b>'+esc(s.term)+'</b> — '+esc(s.detail)+'</div>').join('')+'</div>';}
   else{h+='<p class="shieldnote">🛡️ Intake check: nothing struck — every claim in this resume is backed by your attested profile.</p>';}
   if(j.confirmed&&j.confirmed.length){h+='<p class="shieldnote" style="margin-top:8px">✓ Your resume backs <b>'+j.confirmed.length+' of your '+j.skills_total+' attested skills</b>: '+j.confirmed.map(esc).join(', ')+'.</p>';}
   box.innerHTML=h;_lastSig='';load();
  }
 }catch(e){err.textContent='Could not reach the server.';err.style.display='block';}
 btn.disabled=false;btn.textContent='Upload & check my resume';
};
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
  document.getElementById('fSources').innerHTML=['dice','linkedin'].map(s=>'<label style="font-weight:400"><input type="checkbox" class="fsrc" value="'+s+'"'+(srcs.includes(s)?' checked':'')+'> '+s+'</label>').join('');
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
document.getElementById('cancelRide').onclick=()=>document.getElementById('rideView').classList.remove('open');
document.getElementById('addSkill').onclick=()=>document.getElementById('fSkills').appendChild(skillRow('',''));
document.getElementById('startRide').onclick=submitRide;
document.getElementById('fResumeFile').onchange=e=>{const f=e.target.files&&e.target.files[0];document.getElementById('fResumeFileName').textContent=f?('Selected: '+f.name+' — the Safety Check will read this file'):'';};
document.getElementById('homeBtn').onclick=async()=>{await fetch('api/home',{method:'POST'});cur=null;load();};
document.getElementById('buildMissing').onclick=async()=>{await fetch('api/build_missing',{method:'POST'});load();};
// ---- stats range filter: Today | Yesterday | This month | All time ----
document.querySelectorAll('.rangebtn').forEach(b=>b.onclick=()=>{range=b.dataset.range;document.querySelectorAll('.rangebtn').forEach(x=>x.classList.toggle('on',x===b));_lastSig='';load();});
// ---- profile editor (Your profile): ONLY the person, 'Save my profile' ----
let profSig='',profDirty=false;
function populateProfile(){
 if(!S)return;
 const sig=JSON.stringify([S.candidate,S.skills,S.boundaries]);
 if(sig===profSig||profDirty)return;
 profSig=sig;
 document.getElementById('pName').value=S.candidate||'';
 const sk=document.getElementById('pSkills');sk.innerHTML='';(S.skills||[]).forEach(s=>sk.appendChild(skillRow(s.term,s.evidence)));
 document.getElementById('pBounds').value=(S.boundaries||[]).join('\n');
}
document.getElementById('profileSec').addEventListener('input',()=>{profDirty=true;});
document.getElementById('pSkills').addEventListener('click',()=>{profDirty=true;});
document.getElementById('addPSkill').onclick=()=>{profDirty=true;document.getElementById('pSkills').appendChild(skillRow('',''));};
document.getElementById('saveProfile').onclick=async()=>{
 const err=document.getElementById('profileErr');err.style.display='none';
 const saved=document.getElementById('profileSaved');saved.textContent='';
 const skills=[...document.querySelectorAll('#pSkills .skillrow')].map(r=>({term:r.querySelector('.st').value,evidence:r.querySelector('.se').value})).filter(s=>s.term.trim());
 const payload={name:document.getElementById('pName').value,attested_skills:skills,
  hard_boundaries:document.getElementById('pBounds').value.split('\n')};
 const btn=document.getElementById('saveProfile');btn.disabled=true;btn.textContent='Saving…';
 try{const r=await fetch('api/profile',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j=await r.json();
  if(j.saved){profDirty=false;profSig='';saved.textContent='Saved ✓';_lastSig='';load();}
  else{err.textContent=j.error||'Could not save.';err.style.display='block';}
 }catch(e){err.textContent='Could not reach the server.';err.style.display='block';}
 btn.disabled=false;btn.textContent='💾 Save my profile';
};
// ---- search editor (Your search): ONLY the sweep, 'Save my search' ----
let searchSig='',searchDirty=false;
function renderConnections(){
 if(!S)return;
 const names={dice:'Dice',linkedin:'LinkedIn'};
 const conns=S.connections||{};
 document.getElementById('connList').innerHTML=['dice','linkedin'].map(p=>{
  const c=conns[p]||{status:'not_connected'};
  let pill,btn;
  if(c.status==='connected'){pill='<span style="color:#0E7A4F;font-weight:700">✓ Connected as '+esc(c.display_name||'')+'</span>';btn='<button class="btn2" data-disconnect="'+p+'" type="button">Disconnect</button>';}
  else if(c.status==='connecting'){pill='<span style="color:#9A6A00;font-weight:700">⏳ Connecting… '+names[p]+'’s sign-in page is open in a new tab — sign in there; this flips to ✓ Connected once you’re in'+(c.detail==='own_browser'?' — CMM Scout confirms it from your own browser':' (a minute or two)')+'</span>';btn='<button class="btn2" data-disconnect="'+p+'" type="button">Cancel</button>';}
  else if(c.status==='failed'){pill='<span style="color:#B3261E;font-weight:700">Couldn’t connect'+(c.detail?' — '+esc(c.detail):'')+'</span>';btn='<button class="btn2" data-connect="'+p+'" type="button">Try again</button>';}
  else{pill='<span style="color:#5A6B62">Not connected</span>';btn='<button class="btn2" data-connect="'+p+'" type="button">Connect '+names[p]+'</button>';}
  return '<div style="display:flex;gap:10px;align-items:center;padding:7px 0;border-bottom:1px solid #EEF4F0"><b style="min-width:70px">'+names[p]+'</b>'+pill+'<span style="flex:1"></span>'+btn+'</div>';
 }).join('');
 document.querySelectorAll('[data-connect]').forEach(b=>b.onclick=()=>portalConn('api/connect',b.dataset.connect));
 document.querySelectorAll('[data-disconnect]').forEach(b=>b.onclick=()=>portalConn('api/disconnect',b.dataset.disconnect));
 renderScout();
}
function renderScout(){
 if(!S)return;
 const box=document.getElementById('scoutBox');if(!box)return;
 const sc=S.scout||{};
 if(!sc.paired){
  box.innerHTML='<div style="border:1px dashed #BFD8CC;border-radius:8px;padding:10px 12px">'
   +'<b>🦉 CMM Scout — make CMM independent of any agent</b><br>'
   +'<span style="color:#5A6B62">Pair the small CMM Scout helper in <b>your own browser</b>: from then on, Connect confirms inside your browser, from the name the portal shows <b>you</b> — no agent, no saved login anywhere else. Your password never leaves the portal’s own page.</span><br>'
   +'<button class="btn2" id="scoutGen" type="button" style="margin-top:8px">Get my pairing code</button> '
   +'<a class="btn2" href="scout.zip" style="display:inline-block;margin-top:8px;text-decoration:none">⬇ Download CMM Scout</a></div>';
  const g=document.getElementById('scoutGen');if(g)g.onclick=async()=>{try{await fetch('api/scout-code',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch(e){}_lastSig='';load();};
 }else{
  box.innerHTML='<div style="border:1px solid #BFD8CC;border-radius:8px;padding:10px 12px;background:#F4FAF6">'
   +'<b>🦉 CMM Scout — paired ✓</b> <span style="color:#5A6B62">Connect now confirms in your own browser; no agent is involved.</span><br>'
   +'<span style="color:#5A6B62">Your pairing code (paste it into the Scout helper once):</span> '
   +'<code style="font-size:16px;font-weight:700;letter-spacing:2px;background:#fff;border:1px solid #BFD8CC;border-radius:6px;padding:2px 8px">'+esc(sc.code||'')+'</code><br>'
   +'<span style="color:#5A6B62">Steps: <a href="scout.zip">download CMM Scout</a>, unzip it, open <b>chrome://extensions</b>, turn on <b>Developer mode</b>, <b>Load unpacked</b> the folder, click the Scout icon and paste the code.</span> '
   +'<button class="btn2" id="scoutGen" type="button">New code</button></div>';
  const g=document.getElementById('scoutGen');if(g)g.onclick=async()=>{try{await fetch('api/scout-code',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});}catch(e){}_lastSig='';load();};
 }
}
async function portalConn(path,portal){
 if(path==='api/connect'){
  // The click must DO something the seeker can see: open the portal's own
  // sign-in page in a new tab in THEIR browser, synchronously inside the
  // click so popup blockers allow it. They sign in on the portal itself;
  // the card here flips to Connected once the agent layer confirms it.
  const loginUrls={dice:'https://www.dice.com/dashboard/login',linkedin:'https://www.linkedin.com/login'};
  if(loginUrls[portal])window.open(loginUrls[portal],'_blank');
 }
 try{await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({portal})});}catch(e){}
 _lastSig='';load();
}
function populateSearch(){
 if(!S)return;
 const sig=JSON.stringify(S.spec);
 if(sig===searchSig||searchDirty)return;
 searchSig=sig;
 const sp=S.spec||{};
 document.getElementById('sLoc').value=sp.location||'';
 document.getElementById('sKeywords').value=(sp.keywords||[]).join(', ');
 document.getElementById('sMatch').value=(sp.match_skills||[]).join(', ');
 const jts=sp.job_types||(sp.contract_only?['contract']:[]);
 document.querySelectorAll('.sjt').forEach(c=>c.checked=jts.includes(c.value));
 const wps=sp.workplace||[];
 document.querySelectorAll('.swp').forEach(c=>c.checked=wps.includes(c.value));
 document.getElementById('sHours').value=String(sp.posted_within_hours||24);
 const srcs=sp.sources||[];
 document.getElementById('sSources').innerHTML=['dice','linkedin'].map(s=>'<label style="font-weight:400"><input type="checkbox" class="ssrc" value="'+s+'"'+(srcs.includes(s)?' checked':'')+'> '+s+'</label>').join('');
}
document.getElementById('searchSec').addEventListener('input',()=>{searchDirty=true;});
document.getElementById('saveSearch').onclick=async()=>{
 const err=document.getElementById('searchErr');err.style.display='none';
 const saved=document.getElementById('searchSaved');saved.textContent='';
 const payload={search:{keywords:document.getElementById('sKeywords').value.split(','),match_skills:document.getElementById('sMatch').value.split(','),location:document.getElementById('sLoc').value,job_types:[...document.querySelectorAll('.sjt:checked')].map(c=>c.value),workplace:[...document.querySelectorAll('.swp:checked')].map(c=>c.value),posted_within_hours:parseInt(document.getElementById('sHours').value),sources:[...document.querySelectorAll('.ssrc:checked')].map(c=>c.value)}};
 const btn=document.getElementById('saveSearch');btn.disabled=true;btn.textContent='Saving…';
 try{const r=await fetch('api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const j=await r.json();
  if(j.saved){searchDirty=false;searchSig='';saved.textContent='Saved ✓ — the next Scan for New Roles uses these settings.';_lastSig='';load();}
  else{err.textContent=j.error||'Could not save.';err.style.display='block';}
 }catch(e){err.textContent='Could not reach the server.';err.style.display='block';}
 btn.disabled=false;btn.textContent='💾 Save my search';
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
                body = ui.page().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parsed.path == "/api/state":
                qs = parse_qs(parsed.query)
                self._json(ui.state(qs.get("range", ["all"])[0]))
            elif parsed.path == "/scout.zip":
                data = ui.scout_zip()
                if data is None:
                    self._json({"error": "not found"}, 404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 'attachment; filename="cmm-scout.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
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
            elif self.path == "/api/connect" or self.path == "/api/disconnect":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                    portal = str(payload.get("portal") or "")
                except (ValueError, json.JSONDecodeError):
                    self._json({"started": False, "error": "Bad request."},
                               400)
                    return
                if self.path == "/api/connect":
                    ok, info = ui.connect_portal(portal)
                else:
                    ok, info = ui.disconnect_portal(portal)
                self._json({"started": ok, "status": info} if ok
                           else {"started": False, "error": info},
                           200 if ok else 400)
            elif self.path == "/api/scout-code":
                ok, info = ui.generate_scout_code()
                self._json({"started": ok, "code": info} if ok
                           else {"started": False, "error": info},
                           200 if ok else 400)
            elif self.path == "/api/connect-confirm":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._json({"ok": False, "error": "Bad request."}, 400)
                    return
                ok, info = ui.confirm_connection(
                    str(payload.get("portal") or ""),
                    str(payload.get("code") or ""),
                    str(payload.get("display_name") or ""))
                self._json({"ok": ok, "status": info} if ok
                           else {"ok": False, "error": info},
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
            elif self.path == "/api/search":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._json({"saved": False, "error": "Bad form data."}, 400)
                    return
                ok, info = ui.update_search(payload)
                self._json({"saved": ok} if ok
                           else {"saved": False, "error": info},
                           200 if ok else 400)
            elif self.path == "/api/profile-resume":
                try:
                    length = int(self.headers.get("Content-Length") or 0)
                    payload = json.loads(self.rfile.read(length) or b"{}")
                except (ValueError, json.JSONDecodeError):
                    self._json({"ok": False, "error": "Bad form data."}, 400)
                    return
                ok, info = ui.profile_resume(payload)
                if ok:
                    self._json({"ok": True, **info})
                else:
                    self._json({"ok": False,
                                "error": info.get("error", "Upload failed.")},
                               400)
            elif self.path == "/api/reset-demo":
                ok, info = ui.reset_demo()
                self._json({"ok": ok} if ok
                           else {"ok": False, "error": info},
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


def serve(out_dir, form_path, demo_dir, fixture_out, port=8765,
          sandbox=False):
    ui = UI(out_dir, form_path, demo_dir, fixture_out, sandbox=sandbox)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(ui))
    print(f"CMM Mission Control: http://127.0.0.1:{port}")
    server.serve_forever()
