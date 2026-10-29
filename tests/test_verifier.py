import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from attested import verifier  # noqa: E402

PROFILE = """# Profile

## Attested skills
- Python :: 9 years
- PySpark :: 6 years on Amazon EMR
- Snowflake :: 3 years, SQL and SnowSQL only

## Hard boundaries
- Never used Snowpark.
"""


class VerifierTests(unittest.TestCase):
    def setUp(self):
        self.attested = verifier.parse_profile(PROFILE)

    def test_parse_profile(self):
        self.assertEqual(set(self.attested), {"python", "pyspark", "snowflake"})

    def test_snowpark_is_not_pyspark(self):
        split = verifier.verify_requirements(
            [{"term": "Snowpark"}, {"term": "PySpark"}], self.attested
        )
        defensible = {d["term"] for d in split["defensible"]}
        vulnerable = {v["term"] for v in split["vulnerable"]}
        self.assertEqual(defensible, {"PySpark"})
        self.assertEqual(vulnerable, {"Snowpark"})

    def test_draft_with_snowpark_is_rejected(self):
        draft = "Built Snowpark DataFrame pipelines in production."
        violations = verifier.check_draft(draft, self.attested, PROFILE)
        self.assertTrue(any(v["term"].startswith("snowpark") for v in violations))

    def test_draft_with_attested_terms_passes(self):
        draft = "Built PySpark pipelines and modelled data in Snowflake with Python."
        self.assertEqual(verifier.check_draft(draft, self.attested, PROFILE), [])

    def test_invented_metric_is_rejected(self):
        draft = "Improved PySpark pipeline throughput by 400%."
        violations = verifier.check_draft(draft, self.attested, PROFILE)
        self.assertTrue(any(v["type"] == "invented_metric" for v in violations))

    def test_pivot_uses_only_attested_neighbours(self):
        pivot = verifier.pivot_for("Snowpark", self.attested)
        self.assertIn("PySpark", pivot)
        self.assertNotIn("master", pivot.lower())
        pivot = verifier.pivot_for("Terraform", self.attested)
        self.assertIn("no production Terraform", pivot)

    def test_profile_metric_is_allowed(self):
        draft = "Python engineer with 9 years of experience."
        violations = verifier.check_draft(draft, self.attested, PROFILE)
        self.assertFalse(any(v["type"] == "invented_metric" for v in violations))


if __name__ == "__main__":
    unittest.main()
