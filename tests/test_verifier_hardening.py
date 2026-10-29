"""Verifier hardening: the traps a naive implementation fails.

Grouped by attack: term-identity confusion, alias abuse, duplicate
requirements, metric smuggling (bare-number attestation, unit swaps,
unicode/scale forms), draft-scan boundaries, and determinism.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from attested import verifier  # noqa: E402

PROFILE = """# Profile

## Attested skills
- Python :: 9 years
- PySpark :: 6 years on Amazon EMR
- Amazon EMR :: 6 years, production clusters
- Apache Kafka :: 2 years, internal event bus
- Kubernetes :: 3 years, managed clusters
- Snowflake :: 3 years, SQL models and SnowSQL only; pipeline cost down 30%

## Hard boundaries
- Never used Snowpark.
- Never used Terraform.

## Wishlist
- Rust :: would love to learn
"""


def terms(split, side):
    return {row["term"] for row in split[side]}


class TestTermIdentity(unittest.TestCase):
    def setUp(self):
        self.attested = verifier.parse_profile(PROFILE)

    def test_java_is_not_javascript(self):
        split = verifier.verify_requirements(
            [{"term": "Java"}, {"term": "JavaScript"}], self.attested)
        self.assertEqual(terms(split, "vulnerable"), {"Java", "JavaScript"})
        self.assertEqual(terms(split, "defensible"), set())

    def test_section_scoping(self):
        # 'Rust' sits under Wishlist, Snowpark under Hard boundaries:
        # neither is an attested skill.
        self.assertNotIn("rust", self.attested)
        self.assertNotIn("snowpark", self.attested)
        self.assertNotIn("terraform", self.attested)

    def test_alias_direction(self):
        # JD-side short forms resolve to attested canonical skills.
        split = verifier.verify_requirements(
            [{"term": "Kafka"}, {"term": "K8s"}], self.attested)
        self.assertEqual(terms(split, "defensible"), {"Kafka", "K8s"})

    def test_spark_bridge_but_not_streaming(self):
        # The one documented bridge: PySpark attests Spark / Apache Spark.
        split = verifier.verify_requirements(
            [{"term": "Spark"}, {"term": "Apache Spark"},
             {"term": "Spark Streaming"}], self.attested)
        self.assertEqual(terms(split, "defensible"), {"Spark"})
        self.assertEqual(terms(split, "vulnerable"), {"Spark Streaming"})
        # ^ dedupe: 'Spark' and 'Apache Spark' are one canonical claim (first wins)


class TestDuplicates(unittest.TestCase):
    def setUp(self):
        self.attested = verifier.parse_profile(PROFILE)

    def test_duplicate_requirements_collapse(self):
        split = verifier.verify_requirements(
            [{"term": "Kafka"}, {"term": "apache kafka"}, {"term": "KAFKA"},
             {"term": "Terraform"}, {"term": "terraform"}], self.attested)
        self.assertEqual(len(split["defensible"]), 1)
        self.assertEqual(len(split["vulnerable"]), 1)
        self.assertEqual(split["defensible"][0]["term"], "Kafka")


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.attested = verifier.parse_profile(PROFILE)

    def _metric_flags(self, draft):
        return [v for v in verifier.check_draft(draft, self.attested, PROFILE)
                if v["type"] == "invented_metric"]

    def test_bare_number_does_not_attest_a_percent(self):
        # Profile says '9 years' and '3 years' — neither licenses a %.
        self.assertTrue(self._metric_flags("Cut pipeline costs by 9%."))
        self.assertTrue(self._metric_flags("Improved throughput by 3%."))

    def test_attested_percent_in_both_spellings(self):
        self.assertEqual(self._metric_flags("Cut pipeline cost by 30%."), [])
        self.assertEqual(self._metric_flags("Cut pipeline cost by 30 percent."), [])

    def test_unit_swap_is_flagged(self):
        self.assertTrue(self._metric_flags("Made pipelines 30x faster."))
        self.assertTrue(self._metric_flags("Cut pipeline cost by 31%."))

    def test_unicode_and_scale_forms(self):
        self.assertTrue(self._metric_flags("Made jobs 10× faster."))
        self.assertTrue(self._metric_flags("Owned a $5M data platform."))
        self.assertTrue(self._metric_flags("Processed 5M records nightly."))
        self.assertTrue(self._metric_flags("Grew usage 2-fold."))

    def test_years_are_not_metrics(self):
        self.assertEqual(
            self._metric_flags("Python engineer with 9 years of experience."), [])


class TestDraftScanBoundaries(unittest.TestCase):
    def setUp(self):
        self.attested = verifier.parse_profile(PROFILE)

    def test_substring_lookalikes_pass(self):
        # 'Kafkaesque' contains 'kafka' but is not a Kafka claim.
        self.assertEqual(
            verifier.check_draft("A Kafkaesque release process.",
                                 self.attested, PROFILE), [])

    def test_hyphenated_claim_is_caught(self):
        v = verifier.check_draft("Built Snowpark-based features.",
                                 self.attested, PROFILE)
        self.assertTrue(any(x["type"] == "unattested_term" for x in v))

    def test_streaming_not_covered_by_spark_bridge(self):
        v = verifier.check_draft("Built Spark Streaming pipelines.",
                                 self.attested, PROFILE)
        self.assertTrue(any("streaming" in x["term"] for x in v))

    def test_plain_spark_claim_passes_via_bridge(self):
        self.assertEqual(
            verifier.check_draft("Built Spark batch jobs on EMR.",
                                 self.attested, PROFILE), [])


class TestDeterminism(unittest.TestCase):
    def setUp(self):
        self.attested = verifier.parse_profile(PROFILE)

    def test_repeat_runs_identical_and_inputs_untouched(self):
        reqs = [{"term": "Kafka", "evidence": "e1"},
                {"term": "Snowpark", "evidence": "e2"}]
        snapshot = copy.deepcopy(reqs)
        first = verifier.verify_requirements(reqs, self.attested)
        second = verifier.verify_requirements(reqs, self.attested)
        self.assertEqual(first, second)
        self.assertEqual(reqs, snapshot)

    def test_empty_inputs(self):
        self.assertEqual(verifier.verify_requirements([], self.attested),
                         {"defensible": [], "vulnerable": []})
        self.assertEqual(verifier.check_draft("", self.attested, PROFILE), [])

    def test_pivot_for_unknown_term_is_honest_gap(self):
        pivot = verifier.pivot_for("Rust", self.attested)
        self.assertIn("no production Rust", pivot)


if __name__ == "__main__":
    unittest.main()
