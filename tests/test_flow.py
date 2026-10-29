"""Offline tests for the complete-flow plumbing: store, parser, sweeper
extraction, keep rule. No network, no LLM."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from attested import parser, profile, store, sweeper  # noqa: E402

DICE_HTML_SNIPPET = """
<script>self.__next_f.push([1,"10:[\\"$\\",\\"$L45\\",null,{\\"jobList\\":{\\"data\\":[
{\\"id\\":\\"a1\\",\\"guid\\":\\"guid-1\\",\\"detailsPageUrl\\":\\"https://www.dice.com/job-detail/guid-1\\",
\\"companyName\\":\\"Acme Corp\\",\\"employmentType\\":\\"Contract\\",
\\"jobLocation\\":{\\"displayName\\":\\"Remote, USA\\"},\\"postedDate\\":\\"2026-10-26T10:00:00Z\\",
\\"title\\":\\"Data Engineer\\",\\"summary\\":\\"Data Engineer python sql\\"}
]}]"])</script>
"""

FORM = {
    "name": "Test Person",
    "search": {
        "keywords": ["data engineer pyspark"],
        "match_skills": ["python", "sql", "pyspark", "tableau", "power bi"],
        "location": "United States",
        "contract_only": True,
        "posted_within_hours": 24,
        "sources": ["dice"],
    },
    "attested_skills": [
        {"term": "Python", "evidence": "9 years"},
        {"term": "PySpark", "evidence": "6 years on EMR"},
    ],
}


class TestDiceExtraction(unittest.TestCase):
    def test_joblist_parsed(self):
        cards = sweeper._extract_joblist(DICE_HTML_SNIPPET)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["guid"], "guid-1")
        self.assertEqual(cards[0]["companyName"], "Acme Corp")

    def test_description_balance(self):
        html = ('<div class="x" data-testid="jobDescription"><p>Hello <b>JD</b></p>'
                '<div><p>nested</p></div><p>tail</p></div><div>after</div>')
        out = sweeper._dice_description(html)
        self.assertIn("Hello", out)
        self.assertIn("tail", out)
        self.assertNotIn("after", out)


class TestStoreAndParser(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = store.connect(os.path.join(self.tmp, "t.db"))

    def test_form_to_config_to_raw_to_posting(self):
        cid = store.add_config(self.db, FORM)
        self.assertEqual(cid, 1)
        payload = {"card": {"guid": "g1", "detailsPageUrl": "https://x",
                            "companyName": "Acme Corp", "title": "Data Engineer",
                            "employmentType": "Contract",
                            "jobLocation": {"displayName": "Remote, USA"},
                            "postedDate": "2026-10-26T10:00:00Z",
                            "summary": "python sql pyspark " * 30},
                   "description_html": "<p>We need python, sql and pyspark. " * 10}
        _, new = store.insert_raw(self.db, "dice", "g1", "https://x", json.dumps(payload))
        self.assertTrue(new)
        _, new2 = store.insert_raw(self.db, "dice", "g1", "https://x", json.dumps(payload))
        self.assertFalse(new2)  # dedupe layer 1
        res = parser.parse_all(self.db, store, lambda a, m: None)
        self.assertEqual(res["parsed"], 1)
        rows = store.postings_without_match(self.db, "Test Person")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["company"], "Acme Corp")
        self.assertIn("pyspark", rows[0]["jd_text"].lower())

    def test_keep_rule_two_skills(self):
        posting = {"title": "Data Engineer", "jd_text": "python and sql needed"}
        hits = parser.keyword_hits(posting, FORM["search"]["match_skills"])
        self.assertEqual(sorted(hits), ["python", "sql"])
        thin = {"title": "Analyst", "jd_text": "excel only"}
        self.assertEqual(parser.keyword_hits(thin, FORM["search"]["match_skills"]), [])


class TestProfileForm(unittest.TestCase):
    def test_render_parse_roundtrip(self):
        from attested import verifier
        md = profile.render_profile_md(FORM)
        attested = verifier.parse_profile(md)
        self.assertIn("python", attested)
        self.assertIn("pyspark", attested)
        self.assertEqual(attested["pyspark"]["evidence"], "6 years on EMR")


if __name__ == "__main__":
    unittest.main()
