"""Signed-in LinkedIn source: scout deposits -> sweeper -> parser.

The guest LinkedIn endpoint returned ID-only stubs and could not filter
for contract jobs, so it was retired (owner, 2026-10-28). The signed-in
scout deposits full job records into <store>/linkedin_inbox/; these
tests prove the ingestion half of that loop. RemoteOK / Arbeitnow were
removed as sweep sources the same day.
"""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone

from attested import applier, parser, store, sweeper, web

FORM = {
    "name": "Test Person",
    "search": {"keywords": ["data engineer pyspark"],
               "match_skills": ["python", "sql"],
               "location": "United States", "contract_only": True,
               "posted_within_hours": 24, "sources": ["linkedin"]},
    "attested_skills": [{"term": "Python", "evidence": "9 years"}],
    "hard_boundaries": [],
}

JD = ("Senior Data Engineer role. Build PySpark pipelines on AWS. "
      "Python and SQL daily. Contract role, 12 months. " * 4)


def _job(job_id, posted_at, title="Senior Data Engineer"):
    return {"job_id": job_id, "title": title, "company": "Acme Corp",
            "location": "Remote, US", "employment_type": "Contract",
            "posted_at": posted_at,
            "apply_url": f"https://www.linkedin.com/jobs/view/{job_id}",
            "description_text": JD}


class TestLinkedInSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = store.connect(os.path.join(self.tmp, "attested.db"))
        self.inbox = os.path.join(self.tmp, "linkedin_inbox")
        os.makedirs(self.inbox)
        self.events = []
        self.emit = lambda a, m: self.events.append(m)

    def _config(self):
        cid = store.add_config(self.db, FORM)
        return self.db.execute(
            "SELECT * FROM sweep_configs WHERE id=?", (cid,)).fetchone()

    def _deposit(self, name, jobs, keywords=("data engineer pyspark",)):
        with open(os.path.join(self.inbox, name), "w") as f:
            json.dump({"searched_keywords": list(keywords), "jobs": jobs}, f)

    def test_deposit_ingested_and_parsed(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        old = "2020-01-01T00:00:00+00:00"
        self._deposit("scout-1.json", [_job("111", now), _job("222", old)])
        counts = sweeper.sweep(self.db, store, self._config(), self.emit)
        # The stale job is filtered by the recency window.
        self.assertEqual(counts, {"linkedin": 1})
        result = parser.parse_all(self.db, store, self.emit)
        self.assertEqual(result["parsed"], 1)
        row = self.db.execute(
            "SELECT * FROM job_postings WHERE source='linkedin'").fetchone()
        self.assertEqual(row["title"], "Senior Data Engineer")
        self.assertEqual(row["company"], "Acme Corp")
        self.assertEqual(row["employment_type"], "Contract")
        self.assertGreater(len(row["jd_text"]), 200)

    def test_fragment_for_other_keywords_skipped(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._deposit("scout-2.json", [_job("333", now)],
                      keywords=("product manager",))
        counts = sweeper.sweep(self.db, store, self._config(), self.emit)
        self.assertEqual(counts, {"linkedin": 0})
        doctor = [m for m in self.events if "signed-in scout" in m]
        self.assertTrue(doctor)

    def test_scout_workplace_badge_rides_into_location(self):
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        job = _job("444", now)
        job["workplace"] = "Remote"
        job["location"] = "United States"
        self._deposit("scout-3.json", [job])
        sweeper.sweep(self.db, store, self._config(), self.emit)
        parser.parse_all(self.db, store, self.emit)
        row = self.db.execute(
            "SELECT * FROM job_postings WHERE source='linkedin'").fetchone()
        self.assertTrue(row["location"].startswith("Remote"))
        _, w = parser.classify(row["employment_type"], row["location"],
                               row["source"], row["jd_text"])
        self.assertEqual(w, "remote")

    def test_removed_sources_and_linkedin_route(self):
        self.assertNotIn("remoteok", sweeper.SOURCES)
        self.assertNotIn("arbeitnow", sweeper.SOURCES)
        self.assertNotIn("linkedin_guest", sweeper.SOURCES)
        self.assertEqual(set(web.KNOWN_SOURCES), {"dice", "linkedin"})
        self.assertEqual(applier.route_for("linkedin"), "linkedin")


if __name__ == "__main__":
    unittest.main()
