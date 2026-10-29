"""Offline tests for the resume-builder and applier stages."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from attested import applier, resume, store, verifier  # noqa: E402

FORM = {
    "name": "Test Person",
    "search": {"keywords": ["x"], "match_skills": ["python", "sql"],
               "location": "United States", "contract_only": False,
               "posted_within_hours": 24, "sources": ["dice"]},
    "attested_skills": [
        {"term": "Python", "evidence": "9 years"},
        {"term": "SQL", "evidence": "9 years"},
        {"term": "PySpark", "evidence": "6 years on EMR"},
    ],
}
PROFILE_MD = (
    "# Attested profile — Test Person\n\n## Attested skills\n"
    "- Python :: 9 years\n- SQL :: 9 years\n- PySpark :: 6 years on EMR\n"
)
SPLIT = {
    "defensible": [{"term": "Python", "evidence": "9 years"},
                   {"term": "SQL", "evidence": "9 years"}],
    "vulnerable": [{"term": "Kafka", "jd_evidence": "", "reason": "no proof"}],
}
POSTING = {"id": 1, "title": "Data Engineer", "company": "Acme"}


class TestResumeStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = store.connect(os.path.join(self.tmp, "t.db"))
        self.attested = verifier.parse_profile(PROFILE_MD)

    def test_clean_narrative_builds_and_passes_guardrail(self):
        match_dir = os.path.join(self.tmp, "m1")
        rid = resume.ensure_resume(
            self.db, store, FORM, POSTING, SPLIT,
            "Built pipelines in Python and SQL for nine years.",
            match_dir, PROFILE_MD, self.attested, lambda a, m: None)
        self.assertIsNotNone(rid)
        row = store.resume_for(self.db, 1, "Test Person")
        self.assertEqual(row["guardrail"], "PASSED")
        self.assertTrue(os.path.exists(os.path.join(match_dir, "resume.md")))

    def test_dirty_narrative_is_rejected(self):
        match_dir = os.path.join(self.tmp, "m2")
        rid = resume.ensure_resume(
            self.db, store, FORM, POSTING, SPLIT,
            "Expert in Kafka streaming and Python.",
            match_dir, PROFILE_MD, self.attested, lambda a, m: None)
        self.assertIsNone(rid)
        row = store.resume_for(self.db, 1, "Test Person")
        self.assertEqual(row["guardrail"], "REJECTED")

    def test_job_title_tech_is_metadata_not_a_claim(self):
        # A posting titled "Databricks / PySpark" must not fail the resume:
        # the employer's title is not the seeker claiming Databricks.
        posting = {"id": 1, "title": "Data Analyst - Databricks / PySpark",
                   "company": "Highbrow"}
        rid = resume.ensure_resume(
            self.db, store, FORM, posting, SPLIT,
            "Built pipelines in Python and SQL for nine years.",
            os.path.join(self.tmp, "m3"), PROFILE_MD, self.attested,
            lambda a, m: None)
        self.assertIsNotNone(rid)

    def test_extract_narrative(self):
        dossier = ("## Verified narrative (guardrail-passed)\n"
                   "Clean summary text.\n\n## Audit trail\n- x\n")
        self.assertEqual(resume.extract_narrative(dossier), "Clean summary text.")


class TestApplierStage(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = store.connect(os.path.join(self.tmp, "t.db"))

    def _seed(self, verdict, with_resume):
        cur = self.db.execute(
            "INSERT INTO job_postings (raw_id, source, title, company, jd_text,"
            " apply_url) VALUES (abs(random()) % 100000, 'dice', 'DE', 'Acme',"
            " 'x', 'https://apply')")
        self.db.commit()
        pid = cur.lastrowid
        store.insert_match(self.db, pid, "Test Person", verdict, 3, 1, {"split": SPLIT})
        if with_resume:
            store.insert_resume(self.db, pid, "Test Person", "/tmp/r.md", "PASSED")
        return pid

    def test_pass_with_resume_queues_pursuit(self):
        self._seed("PASS", True)
        n = applier.dispatch(self.db, store, "Test Person", lambda a, m: None)
        self.assertEqual(n, 1)
        rows = store.pursuits_for(self.db, "Test Person")
        self.assertEqual(rows[0]["route"], "dice")
        self.assertEqual(rows[0]["status"], "ready")

    def test_pass_without_resume_not_eligible(self):
        self._seed("PASS", False)
        self.assertEqual(
            applier.dispatch(self.db, store, "Test Person", lambda a, m: None), 0)

    def test_skip_never_queued(self):
        self._seed("SKIP", True)
        self.assertEqual(
            applier.dispatch(self.db, store, "Test Person", lambda a, m: None), 0)

    def test_route_mapping(self):
        self.assertEqual(applier.route_for("linkedin_guest"), "linkedin")
        self.assertEqual(applier.route_for("dice"), "dice")
        self.assertEqual(applier.route_for("anything_else"), "company_portal")


if __name__ == "__main__":
    unittest.main()
