"""Offline tests for the web UI: md-lite safety, audit parsing, state + server."""
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from attested import store, web  # noqa: E402

FORM = {
    "name": "Test Person",
    "search": {"keywords": ["data engineer"], "match_skills": ["python", "sql"],
               "location": "United States", "contract_only": True,
               "posted_within_hours": 24, "sources": ["dice"]},
    "attested_skills": [{"term": "Python", "evidence": "9 years"},
                        {"term": "SQL", "evidence": "9 years"}],
    "hard_boundaries": ["Never used Snowpark"],
}
SPLIT = {"defensible": [{"term": "Python", "evidence": "9 years",
                         "jd_evidence": ""}],
         "vulnerable": [{"term": "Kafka", "jd_evidence": "",
                         "reason": "No attested proof of Kafka."}]}


class TestMdLite(unittest.TestCase):
    def test_escapes_html(self):
        out = web.md_lite("# Hi <script>alert(1)</script>\n- **bold** ok")
        self.assertNotIn("<script>", out)
        self.assertIn("<strong>bold</strong>", out)

    def test_parse_audit(self):
        dossier = ("## Audit trail — hallucinations caught by the guardrail\n"
                   "- Baseline [unattested_term] **databricks** — 'databricks' "
                   "appears in the draft but is not in the attested profile.\n\n"
                   "## My call\n")
        entries = web.parse_audit(dossier)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["term"], "databricks")


class TestHumanFeed(unittest.TestCase):
    # The exact event stream from a real run (2026-10-27) that the owner
    # found unreadable — the human feed must tell this story in order,
    # in plain English, with the machine noise gone.
    EVENTS = [
        ("system", "Form in: Alex Rivera — 10 attested claims"),
        ("shopper", "Sweep config #1: keywords=['data engineer pyspark', 'data engineer python sql']"),
        ("sweeper", "arbeitnow: 300 fetched, 300 new raw postings"),
        ("parser", "parsed 287 postings (13 company+title dupes, 0 thin JDs)"),
        ("verdict", "Type filter: 20 posting(s) set aside (job type: full_time; workplace: remote; postings whose type is unstated are kept)"),
        ("verdict", "Keep rule: 2 postings match >=2 skills; Reader will process top 2"),
        ("reader", "Reader Agent calling Muse Spark for structured extraction"),
        ("reader", "Muse Spark reasoning in progress — streaming: 1 chunks, 20 chars received, 14.3s elapsed"),
        ("reader", "Muse Spark reasoning tokens: 4624 | total tokens: 10281"),
        ("reader", "Extracted 26 core constraints"),
        ("verdict", "PASS: Engineering Manager - Data Platform (m/f/d) @ autoscout24 — core pool skills defensible: python, sql (4 defensible / 22 vulnerable — gaps itemized in the dossier)"),
        ("builder", "Baseline draft: a conventional AI resume writer, unguarded"),
        ("builder", "Builder Agent drafting (round 1)"),
        ("builder", "Muse Spark reasoning tokens: 2986 | total tokens: 3476"),
        ("guardrail", "GUARDRAIL PASSED — draft is fully attested"),
        ("builder", "Resume built + guardrail PASSED: /x/matches/19/resume.md"),
        ("system", "Dossier written: /x/matches/19/dossier.md"),
        ("reader", "Extracted 7 core constraints"),
        ("verdict", "SKIP: Senior Data Analyst - Marketplace @ prolific — core pool overlap too thin: 2 pool skill(s) defensible, 2 defensible / 5 vulnerable"),
        ("applier", "Pursuit #1 queued (board) for posting 19"),
        ("applier", "Pursuit #1 prepared: /x/matches/19/application_package.md"),
        ("system", "Call queue written (2 judged, 1 pursuits queued)"),
    ]

    def feed(self):
        return web.human_feed([{"ts": "2026-10-27T21:02:00+00:00",
                                "actor": a, "message": m}
                               for a, m in self.EVENTS])

    def test_story_in_order(self):
        titles = [s["title"] for s in self.feed()]
        self.assertTrue(titles[0].startswith("Your profile is in"))
        self.assertIn("The hunt starts", titles)
        self.assertIn("Found 300 jobs on Arbeitnow", titles)
        self.assertIn("Cleaned up the pile", titles)
        self.assertTrue(any(t.startswith("Worth your time: Engineering Manager")
                            for t in titles))
        self.assertTrue(any(t.startswith("Skipped: Senior Data Analyst")
                            for t in titles))
        self.assertTrue(titles[-1].startswith("Done"))
        # story order: the PASS is told before the SKIP, hunt before verdicts
        self.assertLess(titles.index("The hunt starts"),
                        next(i for i, t in enumerate(titles)
                             if t.startswith("Worth your time")))

    def test_linkedin_sweep_narrated_as_browser_scout(self):
        steps = web.human_feed([{
            "ts": "2026-10-28T20:52:00+00:00", "actor": "sweeper",
            "message": "linkedin: 8 fetched, 8 new raw postings"}])
        self.assertEqual(len(steps), 1)
        self.assertIn("browser scout", steps[0]["title"])
        self.assertIn("signed-in", steps[0]["text"])

    def test_noise_and_paths_gone(self):
        blob = " ".join(s["title"] + " " + s["text"] for s in self.feed())
        for junk in ("tokens", "streaming", "chunks", "matches/",
                     "core constraints", "calling Muse"):
            self.assertNotIn(junk, blob)

    def test_skip_explains_the_rule(self):
        skip = next(s for s in self.feed()
                    if s["title"].startswith("Skipped"))
        self.assertIn("only 2 provable claims", skip["text"])
        self.assertIn("at least 2 key skills and 3 provable claims",
                      skip["text"])

    def test_pass_flags_a_stretch(self):
        ps = next(s for s in self.feed()
                  if s["title"].startswith("Worth your time"))
        self.assertIn("python, sql", ps["text"])
        self.assertIn("stretch", ps["text"])  # 22 gaps vs 4 strengths


class TestServer(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "flow")
        os.makedirs(os.path.join(self.out, "matches", "1"))
        db = store.connect(os.path.join(self.out, "attested.db"))
        cur = db.execute(
            "INSERT INTO job_postings (raw_id, source, title, company, jd_text,"
            " apply_url) VALUES (1, 'dice', 'Data Engineer', 'Acme', 'jd',"
            " 'https://apply')")
        db.commit()
        pid = cur.lastrowid
        store.insert_match(db, pid, "Test Person", "PASS", 1, 1, {"split": SPLIT})
        store.insert_resume(db, pid, "Test Person",
                            os.path.join(self.out, "matches", "1", "resume.md"),
                            "PASSED")
        store.insert_pursuit(db, pid, "Test Person", "dice",
                             store.resume_for(db, pid, "Test Person")["id"],
                             "https://apply")
        with open(os.path.join(self.out, "matches", "1", "resume.md"), "w") as f:
            f.write("# Test Person\n\n## Summary\nBuilt things in **Python**.\n")
        with open(os.path.join(self.out, "matches", "1", "dossier.md"), "w") as f:
            f.write("# Evidence Dossier\n\n## Audit trail — x\n"
                    "- Baseline [unattested_term] **kafka** — not attested.\n")
        self.form_path = os.path.join(self.tmp, "form.json")
        with open(self.form_path, "w") as f:
            json.dump(FORM, f)
        self.ui = web.UI(self.out, self.form_path, self.tmp, self.tmp)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(self.ui))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()

    def _get(self, path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{path}", timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def test_state_shape(self):
        status, body = self._get("/api/state")
        self.assertEqual(status, 200)
        state = json.loads(body)
        self.assertEqual(state["counts"]["pass"], 1)
        self.assertEqual(state["matches"][0]["verdict"], "PASS")
        self.assertIn("pivot", state["matches"][0]["split"]["vulnerable"][0])
        self.assertEqual(state["matches"][0]["audit"][0]["term"], "kafka")
        self.assertEqual(state["matches"][0]["pursuit_status"], "ready")

    def test_state_range_filter_excludes_old_rows(self):
        import sqlite3 as _sq
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        db.execute(
            "INSERT INTO candidate_matches (posting_id, candidate, verdict,"
            " defensible, vulnerable, detail, created_at) VALUES"
            " (1, 'Old Person', 'PASS', 3, 0, '{\"split\": {\"defensible\": [],"
            " \"vulnerable\": []}}', '2020-01-01T00:00:00+00:00')")
        db.commit()
        db.close()
        all_counts = self.ui.state("all")["counts"]
        month_counts = self.ui.state("month")["counts"]
        today_counts = self.ui.state("today")["counts"]
        self.assertEqual(all_counts["judged"], month_counts["judged"] + 1)
        self.assertEqual(all_counts["pass"], month_counts["pass"] + 1)
        self.assertLessEqual(today_counts["judged"], month_counts["judged"])
        state = self.ui.state("nonsense")
        self.assertEqual(state["range"], "all")

    def test_update_profile_saves_person_only(self):
        before = json.load(open(self.form_path))
        ok, info = self.ui.update_profile({
            "name": "Test Person",
            "attested_skills": [{"term": "Python", "evidence": "5 years"},
                                {"term": "SQL", "evidence": "5 years"}],
            "hard_boundaries": ["No Kafka"]})
        self.assertTrue(ok, info)
        form = json.load(open(self.form_path))
        self.assertEqual([s["term"] for s in form["attested_skills"]],
                         ["Python", "SQL"])
        self.assertEqual(form["hard_boundaries"], ["No Kafka"])
        # Search carried over untouched — it has its own save. (The form
        # validator normalizes the dict, adding empty job_types/workplace
        # lists; the shopper's settings themselves must not change.)
        for key in ("keywords", "match_skills", "location",
                    "posted_within_hours", "sources", "contract_only"):
            self.assertEqual(form["search"][key], before["search"][key])
        self.assertIn("Python", open(os.path.join(
            self.out, "profile_attested.md")).read())

    def test_update_search_saves_sweep_only(self):
        ok, info = self.ui.update_search({"search": {
            "keywords": ["data engineer python"],
            "match_skills": ["python", "sql"],
            "location": "Remote US",
            "job_types": ["full_time"], "workplace": ["remote"],
            "posted_within_hours": 48, "sources": ["dice"]}})
        self.assertTrue(ok, info)
        form = json.load(open(self.form_path))
        self.assertEqual(form["search"]["keywords"], ["data engineer python"])
        self.assertEqual(form["search"]["posted_within_hours"], 48)
        # Person carried over untouched.
        self.assertEqual(form["name"], "Test Person")
        state = self.ui.state()
        self.assertEqual(state["spec"]["location"], "Remote US")
        ok, err = self.ui.update_search({"search": {
            "keywords": ["x"], "match_skills": ["python"]}})
        self.assertFalse(ok)
        self.assertIn("2 match skills", err)

    def test_page_has_range_filter_and_profile_editor(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn('data-range="today"', body)
        self.assertIn('data-range="all"', body)
        self.assertIn('id="statApplied"', body)
        self.assertIn('id="saveProfile"', body)
        self.assertIn('id="pSkills"', body)
        self.assertIn('data-nav="search"', body)
        self.assertIn('data-nav="how"', body)
        self.assertIn('id="viewHow"', body)
        self.assertIn("How CMM works", body)
        self.assertIn('id="hwLinkedin"', body)
        self.assertIn('id="saveSearch"', body)
        self.assertIn('id="sKeywords"', body)
        self.assertNotIn("Save my profile &amp; search", body)
        self.assertNotIn('id="pKeywords"', body)

    def test_profile_first_in_sidebar_and_no_ride_button(self):
        # Owner's account model (2026-10-28): one login = one person, so
        # there is no 'try it with another profile' entry point, and Your
        # profile is the first button in the left sidebar, before Today.
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertNotIn("Try it with your own profile", body)
        self.assertNotIn('id="rideBtn"', body)
        self.assertLess(body.index('data-nav="profile"'),
                        body.index('data-nav="main"'))

    def test_page_and_doc(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("Live Market Targets", body)
        status, body = self._get("/api/doc?posting=1&kind=resume")
        self.assertEqual(status, 200)
        self.assertIn("<strong>Python</strong>", json.loads(body)["html"])

    def test_ride_validation(self):
        ok, err = self.ui.start_ride({"name": "", "attested_skills": []})
        self.assertFalse(ok)
        ok, err = self.ui.start_ride({
            "name": "Ride Tester",
            "attested_skills": [{"term": "Python", "evidence": "5 years"}],
            "search": {"keywords": ["data engineer"], "match_skills": ["python"]}})
        self.assertFalse(ok)  # match_skills needs >= 2
        self.assertIn("2 match skills", err)

    def test_ride_rebinds_state_and_home_restores(self):
        import time as _time
        from attested import flow, profile, store as store_mod

        def fake_run(form_path, out_dir, reader_cap=None, build_top=None):
            os.makedirs(out_dir, exist_ok=True)
            db = store_mod.connect(os.path.join(out_dir, "attested.db"))
            store_mod.add_config(db, profile.load_form(form_path))
            with open(os.path.join(out_dir, "events.jsonl"), "a") as f:
                f.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00",
                                    "actor": "system",
                                    "message": "Form in: ride"}) + "\n")

        orig = flow.run
        flow.run = fake_run
        try:
            ok, slug = self.ui.start_ride({
                "name": "Ride Tester",
                "attested_skills": [{"term": "Python", "evidence": "5 years"},
                                    {"term": "SQL", "evidence": "5 years"}],
                "hard_boundaries": ["Never used Kafka"],
                "raw_resume": "# Ride Tester\nPython dev.",
                "search": {"keywords": ["data engineer"],
                           "match_skills": ["python", "sql"],
                           "sources": ["dice"]}})
            self.assertTrue(ok, slug)
            for _ in range(50):
                if not self.ui._running["sweep"]:
                    break
                _time.sleep(0.1)
            state = self.ui.state()
            self.assertEqual(state["mode"], "ride")
            self.assertEqual(state["candidate"], "Ride Tester")
            self.assertTrue(os.path.exists(
                os.path.join(self.ui.out_dir, "raw_resume.md")))
            self.ui.go_home()
            state = self.ui.state()
            self.assertEqual(state["mode"], "demo")
            self.assertEqual(state["candidate"], "Test Person")
        finally:
            flow.run = orig

    def test_intake_scan_covers_full_event_file(self):
        # Intake alerts sit at the START of the event file; a long run must
        # not push them out of the Safety Check panel.
        path = os.path.join(self.out, "events.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps({"ts": "2026-01-01T00:00:00+00:00",
                                "actor": "guardrail",
                                "message": "GUARDRAIL ALERT [intake:unattested_term] kafka: 'kafka' appears in the draft but is not in the attested profile."}) + "\n")
            for i in range(100):
                f.write(json.dumps({"ts": "2026-01-01T00:01:00+00:00",
                                    "actor": "system",
                                    "message": f"noise {i}"}) + "\n")
        state = self.ui.state()
        self.assertEqual([i["term"] for i in state["intake"]], ["kafka"])

    def test_download_resume_docx_and_pdf(self):
        import urllib.error
        import urllib.request
        srv = ThreadingHTTPServer(("127.0.0.1", 0),
                                  web.make_handler(self.ui))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            with urllib.request.urlopen(
                    base + "/api/download?posting=1&fmt=docx") as r:
                self.assertIn("wordprocessingml", r.headers["Content-Type"])
                self.assertTrue(r.read().startswith(b"PK"))
            with urllib.request.urlopen(
                    base + "/api/download?posting=1&fmt=pdf") as r:
                self.assertEqual(r.headers["Content-Type"], "application/pdf")
                self.assertTrue(r.read().startswith(b"%PDF"))
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(
                    base + "/api/download?posting=1&fmt=exe")
        finally:
            srv.shutdown()

    def test_ride_with_uploaded_resume(self):
        import base64 as _b64
        import time as _time
        from attested import export, flow

        docx = export.to_docx(
            "# Up Loader\n\nPython dev with Kafka streaming experience "
            "and a long enough resume body to pass the size check.\n")
        orig = flow.run
        flow.run = lambda *a, **k: {"built": [], "judged": 0}
        try:
            ok, info = self.ui.start_ride({
                "name": "Up Loader",
                "attested_skills": [{"term": "Python", "evidence": "5 years"},
                                    {"term": "SQL", "evidence": "5 years"}],
                "search": {"keywords": ["data engineer"],
                           "match_skills": ["python", "sql"],
                           "sources": ["dice"]},
                "resume_upload": {
                    "filename": "resume.docx",
                    "data_b64": _b64.b64encode(docx).decode()}})
            self.assertTrue(ok, info)
            for _ in range(50):
                if not self.ui._running["sweep"]:
                    break
                _time.sleep(0.1)
            raw = open(os.path.join(self.ui.out_dir, "raw_resume.md")).read()
            self.assertIn("Kafka streaming", raw)
            ok, err = self.ui.start_ride({
                "name": "Bad File",
                "attested_skills": [{"term": "Python", "evidence": "5 years"}],
                "search": {"keywords": ["data engineer"],
                           "match_skills": ["python", "sql"]},
                "resume_upload": {"filename": "resume.exe",
                                  "data_b64": _b64.b64encode(
                                      b"MZ" + b"x" * 100).decode()}})
            self.assertFalse(ok)
            self.ui.go_home()
        finally:
            flow.run = orig

    def test_stop_keep_and_discard(self):
        import sqlite3 as _sq
        import time as _time
        from attested import flow, store as store_mod

        def stub_run(form_path, out_dir, reader_cap=None, build_top=None,
                     should_stop=None):
            db = store_mod.connect(os.path.join(out_dir, "attested.db"))
            n = db.execute("SELECT COUNT(*) c FROM candidate_matches"
                           ).fetchone()["c"]
            store_mod.insert_match(db, 7, "Test Person", "PASS", 3, 1,
                                   {"split": {"defensible": [], "vulnerable": []},
                                    "reason": "stub"})
            db.close()
            for _ in range(100):
                if should_stop and should_stop():
                    return {"built": [], "judged": n + 1}
                _time.sleep(0.05)
            return {"built": [], "judged": n + 1}

        def match_count():
            db = _sq.connect(os.path.join(self.out, "attested.db"))
            n = db.execute("SELECT COUNT(*) FROM candidate_matches"
                           ).fetchone()[0]
            db.close()
            return n

        orig = flow.run
        flow.run = stub_run
        try:
            before = match_count()
            self.assertTrue(self.ui.start_sweep())
            _time.sleep(0.3)
            self.assertTrue(self.ui.request_stop("keep"))
            for _ in range(60):
                if not self.ui._running["sweep"]:
                    break
                _time.sleep(0.1)
            self.assertEqual(match_count(), before + 1)  # kept

            self.assertTrue(self.ui.start_sweep())
            _time.sleep(0.3)
            self.assertTrue(self.ui.request_stop("discard"))
            for _ in range(60):
                if not self.ui._running["sweep"]:
                    break
                _time.sleep(0.1)
            self.assertEqual(match_count(), before + 1)  # second run discarded
        finally:
            flow.run = orig

    def test_board_state_fields_and_jd_doc(self):
        # The board needs location/type/outcome detail on every match,
        # and the JD served for the in-place job-post dropdown.
        st = self.ui.state()
        self.assertEqual(len(st["matches"]), 1)
        m = st["matches"][0]
        for key in ("location", "employment_type", "pursuit_detail"):
            self.assertIn(key, m)
        html = self.ui.doc_html(m["posting_id"], "jd")
        self.assertIn("jd", html)

    def test_request_apply_queues_pursuit(self):
        import sqlite3 as _sq
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        db.execute("UPDATE pursuits SET status='prepared'")
        db.commit()
        pid = db.execute("SELECT posting_id FROM pursuits LIMIT 1").fetchone()[0]
        db.close()
        # The fixture pursuit is a Dice application: since the connect
        # build, Apply requires Dice connected first (just-in-time rule).
        cdb = store.connect(os.path.join(self.out, "attested.db"))
        store.set_connection(cdb, "dice", "Test Person", "connected",
                             display_name="Test Person")
        cdb.close()
        ok, info = self.ui.request_apply(pid)
        self.assertTrue(ok, info)
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        status = db.execute("SELECT status FROM pursuits").fetchone()[0]
        db.close()
        self.assertEqual(status, "apply_requested")
        ok, _ = self.ui.request_apply(pid)
        self.assertFalse(ok)  # cannot queue twice

    def test_connections_lifecycle_and_state(self):
        st = self.ui.state()
        self.assertEqual(st["connections"]["dice"]["status"], "not_connected")
        self.assertEqual(st["connections"]["linkedin"]["status"],
                         "not_connected")
        ok, info = self.ui.connect_portal("dice")
        self.assertTrue(ok, info)
        st = self.ui.state()
        self.assertEqual(st["connections"]["dice"]["status"], "connecting")
        # The agent layer completes the sign-in and writes back the name
        # the portal itself displays.
        db = store.connect(os.path.join(self.out, "attested.db"))
        store.set_connection(db, "dice", "Test Person", "connected",
                             display_name="Test Person")
        db.close()
        st = self.ui.state()
        self.assertEqual(st["connections"]["dice"]["status"], "connected")
        self.assertEqual(st["connections"]["dice"]["display_name"],
                         "Test Person")
        self.assertIsNotNone(st["connections"]["dice"]["connected_at"])
        ok, _ = self.ui.disconnect_portal("dice")
        self.assertTrue(ok)
        st = self.ui.state()
        self.assertEqual(st["connections"]["dice"]["status"], "not_connected")
        self.assertIsNone(st["connections"]["dice"]["display_name"])
        ok, _ = self.ui.connect_portal("myspace")
        self.assertFalse(ok)  # unknown portal refused

    def test_scout_pairing_and_own_browser_confirm(self):
        # Unpaired: state says so, and a confirm with any code fails.
        st = self.ui.state()
        self.assertFalse(st["scout"]["paired"])
        ok, _ = self.ui.confirm_connection("dice", "WHATEVER", "Some One")
        self.assertFalse(ok)
        # Pair: a code is generated and served back in state.
        ok, code = self.ui.generate_scout_code()
        self.assertTrue(ok)
        self.assertEqual(len(code), 8)
        st = self.ui.state()
        self.assertTrue(st["scout"]["paired"])
        self.assertEqual(st["scout"]["code"], code)
        # With a pairing in place, Connect is an own-browser request:
        # the executor must skip it (detail marker), no agent confirm.
        ok, _ = self.ui.connect_portal("linkedin")
        self.assertTrue(ok)
        st = self.ui.state()
        self.assertEqual(st["connections"]["linkedin"]["status"],
                         "connecting")
        self.assertEqual(st["connections"]["linkedin"]["detail"],
                         "own_browser")
        # Wrong code cannot confirm; the right code can, and the card
        # takes the name the portal showed in the seeker's browser.
        ok, _ = self.ui.confirm_connection("linkedin", "WRONGCODE",
                                           "Some One")
        self.assertFalse(ok)
        ok, _ = self.ui.confirm_connection("linkedin", code,
                                           "  Some   One ")
        self.assertTrue(ok)
        st = self.ui.state()
        self.assertEqual(st["connections"]["linkedin"]["status"],
                         "connected")
        self.assertEqual(st["connections"]["linkedin"]["display_name"],
                         "Some One")
        # The Scout zip download is buildable.
        self.assertTrue(self.ui.scout_zip())

    def test_sandbox_connect_instant_and_judge_framed_page(self):
        # Judge demo link (sandbox mode): Connect lands Connected in
        # one click, under the name in the form, marked as the demo
        # sandbox — no 'connecting' state ever persists for an
        # executor, and no credentials are involved.
        sb = web.UI(self.out, self.form_path, self.tmp, self.tmp,
                    sandbox=True)
        st = sb.state()
        self.assertTrue(st["sandbox"])
        self.assertNotIn("sandbox", self.ui.state())
        ok, info = sb.connect_portal("dice")
        self.assertTrue(ok, info)
        self.assertEqual(info, "connected")
        st = sb.state()
        self.assertEqual(st["connections"]["dice"]["status"], "connected")
        self.assertEqual(st["connections"]["dice"]["display_name"],
                         "Test Person")
        self.assertEqual(st["connections"]["dice"]["detail"],
                         "demo sandbox — one-click connect")
        self.assertIsNotNone(st["connections"]["dice"]["connected_at"])
        # Disconnect behaves exactly as in normal mode.
        ok, _ = sb.disconnect_portal("dice")
        self.assertTrue(ok)
        self.assertEqual(sb.state()["connections"]["dice"]["status"],
                         "not_connected")
        # The served page is judge-framed only in sandbox mode: banner,
        # hidden Scout box, disclaimer under the accounts panel, no
        # sign-in-tab claim, and no portal tab opened on Connect.
        page = sb.page()
        self.assertIn("Judge demo", page)
        self.assertIn("no password needed", page)
        self.assertIn("Demo link: Connect here is one click", page)
        self.assertIn("never store your password or cookies", page)
        self.assertIn('id="scoutBox" style="display:none"', page)
        self.assertNotIn("sign-in page opens in a new tab", page)
        self.assertIn("!(S&&S.sandbox)&&loginUrls[portal]", page)
        # Normal mode serves PAGE itself, untouched.
        self.assertEqual(self.ui.page(), web.PAGE)
        self.assertNotIn("Judge demo", self.ui.page())

    def test_sandbox_apply_completes_instantly_in_demo_mode(self):
        # Judge demo link (sandbox mode): Apply lands 'applied' at
        # once with Dice's success line + the plain demo note as the
        # outcome — no 'apply_requested' state ever persists for an
        # executor, and no real application is sent. Normal mode is
        # untouched: it still queues for the real applier.
        import sqlite3 as _sq
        sb = web.UI(self.out, self.form_path, self.tmp, self.tmp,
                    sandbox=True)
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        db.execute("UPDATE pursuits SET status='prepared', detail=NULL")
        db.commit()
        pid = db.execute(
            "SELECT posting_id FROM pursuits LIMIT 1").fetchone()[0]
        db.close()
        ok, info = sb.connect_portal("dice")
        self.assertTrue(ok, info)
        ok, info = sb.request_apply(pid)
        self.assertTrue(ok, info)
        self.assertEqual(info, "applied")
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        status, detail = db.execute(
            "SELECT status, detail FROM pursuits").fetchone()
        db.close()
        self.assertEqual(status, "applied")
        self.assertIn("Fantastic! Your application is on its way!",
                      detail)
        self.assertIn("no real application was sent", detail)
        self.assertIn("never sees or stores your password", detail)
        self.assertIn("confirmation to your own email address", detail)
        # The board state carries the demo outcome for the judge.
        m = sb.state()["matches"][0]
        self.assertEqual(m["pursuit_status"], "applied")
        self.assertEqual(m["pursuit_detail"], detail)
        # The same store through a normal instance never demo-applies:
        # a fresh prepared pursuit queues as apply_requested.
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        db.execute("UPDATE pursuits SET status='prepared', detail=NULL")
        db.commit()
        db.close()
        ok, info = self.ui.request_apply(pid)
        self.assertTrue(ok, info)
        self.assertEqual(info, "apply_requested")

    def test_apply_blocked_until_portal_connected(self):
        import sqlite3 as _sq
        db = _sq.connect(os.path.join(self.out, "attested.db"))
        db.execute("UPDATE pursuits SET status='prepared'")
        db.commit()
        pid = db.execute("SELECT posting_id FROM pursuits LIMIT 1").fetchone()[0]
        db.close()
        ok, info = self.ui.request_apply(pid)
        self.assertFalse(ok)
        self.assertIn("Connect Dice first", info)
        status = _sq.connect(os.path.join(self.out, "attested.db")).execute(
            "SELECT status FROM pursuits").fetchone()[0]
        self.assertEqual(status, "prepared")  # untouched by the refusal

    def test_build_missing_starts_rejudge(self):
        import time as _time
        from attested import flow
        calls = []
        orig = flow.rejudge_and_build
        flow.rejudge_and_build = lambda *a, **k: calls.append(k) or {}
        try:
            self.assertTrue(self.ui.start_build_missing())
            for _ in range(50):
                if not self.ui._running["sweep"]:
                    break
                _time.sleep(0.1)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].get("build_top"), 99)
        finally:
            flow.rejudge_and_build = orig

    def test_readonly_blocks_runs(self):
        os.environ["ATTESTED_READONLY"] = "1"
        try:
            self.assertFalse(self.ui.can_run())
            self.assertFalse(self.ui.start_sweep())
            self.assertFalse(self.ui.start_fixture())
        finally:
            del os.environ["ATTESTED_READONLY"]

    def test_profile_resume_upload_audits_and_reports_fill_in(self):
        # Owner (2026-10-30): upload first on Your profile — the SAME
        # deterministic intake audit the pipeline runs strikes the
        # upload's unbacked claims, confirms the attested skills the
        # resume really shows, detects the name for the fill-in, and
        # the upload becomes the form's raw resume. The attested
        # profile itself must NOT change (the human attests at Save).
        import base64
        draft = ("Test Person\n\nData Engineer skilled in Python and SQL. "
                 "Built Kafka streaming pipelines and improved pipeline "
                 "performance by 40%.\n")
        ok, info = self.ui.profile_resume({
            "resume_upload": {"filename": "resume.md",
                              "data_b64": base64.b64encode(
                                  draft.encode()).decode()}})
        self.assertTrue(ok, info)
        struck = {s["term"].lower() for s in info["strikes"]}
        self.assertIn("kafka", struck)
        self.assertIn("40%", struck)
        self.assertEqual({c.lower() for c in info["confirmed"]},
                         {"python", "sql"})
        self.assertEqual(info["name_detected"], "Test Person")
        saved = json.load(open(self.form_path))
        self.assertEqual(saved["raw_resume_file"], "uploaded_resume.md")
        self.assertEqual(saved["name"], "Test Person")
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "uploaded_resume.md")))

    def test_profile_resume_upload_rejects_missing_file(self):
        ok, info = self.ui.profile_resume({})
        self.assertFalse(ok)
        self.assertIn("Choose a resume file", info["error"])

    def test_reset_demo_empties_pipeline_keeps_person(self):
        # Owner (2026-10-30): reset wipes the RUN (postings, matches,
        # resumes, pursuits, feed) and keeps the ACCOUNT (the form).
        before = self.ui.state("all")["counts"]
        self.assertGreater(before["judged"], 0)
        self.assertGreater(before["pursuits"], 0)
        ok, info = self.ui.reset_demo()
        self.assertTrue(ok, info)
        after = self.ui.state("all")["counts"]
        for key in ("raw", "postings", "judged", "pass", "resumes",
                    "pursuits", "applied"):
            self.assertEqual(after[key], 0, key)
        self.assertEqual(self.ui.state()["candidate"], "Test Person")
        self.assertFalse(os.path.exists(
            os.path.join(self.out, "call_queue.md")))
        feed = self.ui.state()["feed"]
        self.assertTrue(any("Demo reset" in s["text"] or
                            "Demo reset" in s["title"] for s in feed))

    def test_sandbox_reset_restores_starting_state(self):
        # Judge demo link (owner, 2026-11-03): reset means EVERYTHING —
        # run wiped, profile + search restored to the shipped starting
        # form, portals back to not-connected. The live form is a
        # working copy inside the store; the shipped file never moves.
        sb = web.UI(self.out, self.form_path, self.tmp, self.tmp,
                    sandbox=True)
        live_form = os.path.join(self.out, "form.json")
        self.assertTrue(os.path.exists(live_form))
        # The judge personalises the profile + search and connects.
        form = json.load(open(live_form))
        form["name"] = "Judge Judy"
        form["search"]["keywords"] = ["something else"]
        with open(live_form, "w") as f:
            json.dump(form, f)
        ok, info = sb.connect_portal("dice")
        self.assertTrue(ok, info)
        self.assertEqual(sb.state()["connections"]["dice"]["status"],
                         "connected")
        self.assertEqual(sb.state()["candidate"], "Judge Judy")
        ok, info = sb.reset_demo()
        self.assertTrue(ok, info)
        st = sb.state("all")
        self.assertEqual(st["candidate"], "Test Person")
        self.assertEqual(st["connections"]["dice"]["status"],
                         "not_connected")
        self.assertEqual(st["connections"]["linkedin"]["status"],
                         "not_connected")
        for key in ("raw", "postings", "judged", "pass", "resumes",
                    "pursuits", "applied"):
            self.assertEqual(st["counts"][key], 0, key)
        restored = json.load(open(live_form))
        self.assertEqual(restored["name"], "Test Person")
        self.assertEqual(restored["search"]["keywords"],
                         ["data engineer"])
        # The shipped starting form was never edited.
        shipped = json.load(open(self.form_path))
        self.assertEqual(shipped["name"], "Test Person")
        self.assertEqual(shipped["search"]["keywords"],
                         ["data engineer"])
        # The sandbox page's reset warning tells the truth.
        self.assertIn("also reset to the starting state", sb.page())
        self.assertNotIn("also reset to the starting state",
                         self.ui.page())

    def test_reset_keeps_connections_in_normal_mode(self):
        # Normal mode is untouched by the sandbox full reset: the
        # account's portal connections survive a run-only reset.
        db = store.connect(os.path.join(self.out, "attested.db"))
        store.set_connection(db, "dice", "Test Person", "connected",
                             display_name="Test Person")
        db.close()
        self.assertEqual(self.ui.state()["connections"]["dice"]["status"],
                         "connected")
        ok, info = self.ui.reset_demo()
        self.assertTrue(ok, info)
        self.assertEqual(self.ui.state()["connections"]["dice"]["status"],
                         "connected")

    def test_page_has_upload_and_reset_controls(self):
        status, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn('id="prFile"', body)
        self.assertIn('id="prUpload"', body)
        self.assertIn('id="resetDemo"', body)

    def test_doc_traversal_blocked(self):
        status, _ = self._get("/api/doc?posting=..%2F..&kind=resume")
        self.assertIn(status, (400, 404))


if __name__ == "__main__":
    unittest.main()
