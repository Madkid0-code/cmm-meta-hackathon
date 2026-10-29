"""Deterministic verifier. No LLM anywhere in this module.

Two jobs:
1. verify_requirements(): diff the Reader's extracted JD constraints against
   the seeker's attested profile -> Defensible vs Vulnerable claims.
2. check_draft(): scan a Builder draft for claims the seeker cannot defend:
   unattested technology terms and invented metrics.

Term identity is canonical: matching is done on normalized whole terms with
an explicit alias table, never on substrings. That is the entire point —
naive substring logic waves "Snowpark" through on the back of "PySpark" and
"Snowflake"; this verifier does not.
"""
import re

# Canonical term -> accepted surface forms (lowercase). Only true synonyms
# belong here. Related-but-different tools must NEVER share a canonical id.
ALIASES = {
    "apache kafka": ["kafka"],
    "apache airflow": ["airflow"],
    "apache flink": ["flink"],
    # The one documented bridge (mirrors production CMM): generic "Spark" /
    # "Apache Spark" claims are defensible for a PySpark holder — PySpark IS
    # the Spark API they used. The bridge never extends to Spark Streaming,
    # which canonicalizes separately and stays its own claim.
    "pyspark": ["py spark", "spark", "apache spark"],
    "aws glue catalog": ["glue catalog", "aws glue", "glue"],
    "amazon emr": ["emr"],
    "amazon s3": ["s3"],
    "sql": [],
    "python": [],
    "snowflake": [],
    "snowpark": ["snowpark dataframes"],
    "dbt": [],
    "terraform": [],
    "kubernetes": ["k8s"],
    "cockroachdb": [],
    "delta lake": [],
    "looker": [],
}

# Terms the draft checker watches for even when the Reader missed them.
LEXICON_EXTRA = [
    "snowpark", "kafka", "flink", "terraform", "kubernetes", "cockroachdb",
    "dbt", "airflow", "looker", "pyspark", "snowflake", "delta lake",
    "apache spark", "spark streaming", "kinesis", "redshift", "bigquery",
    "databricks", "airbyte", "fivetran",
]


def canonical(term):
    t = re.sub(r"\s+", " ", term.strip().lower())
    for canon, forms in ALIASES.items():
        if t == canon or t in forms:
            return canon
    return t


def parse_profile(profile_text):
    """Parse '- Term :: evidence' bullets under the attested-skills section."""
    attested = {}
    in_section = False
    for line in profile_text.splitlines():
        if line.startswith("## "):
            in_section = "attested" in line.lower()
            continue
        if in_section:
            m = re.match(r"^-\s+(.+?)\s*::\s*(.+)$", line)
            if m:
                attested[canonical(m.group(1))] = {
                    "term": m.group(1).strip(),
                    "evidence": m.group(2).strip(),
                }
    return attested


def verify_requirements(requirements, attested):
    """requirements: list of dicts with at least 'term'. Returns the dossier split.

    Requirements are deduped by canonical term (first occurrence wins): the
    Reader can emit the same constraint as 'Kafka', 'Apache Kafka' and
    'KAFKA', and the dossier must show the claim once, not three times.
    """
    defensible, vulnerable = [], []
    seen = set()
    for req in requirements:
        canon = canonical(req["term"])
        if canon in seen:
            continue
        seen.add(canon)
        if canon in attested:
            defensible.append({
                "term": req["term"],
                "evidence": attested[canon]["evidence"],
                "jd_evidence": req.get("evidence", ""),
            })
        else:
            vulnerable.append({
                "term": req["term"],
                "jd_evidence": req.get("evidence", ""),
                "reason": (
                    f"No attested proof of {req['term']}. Claiming it is an "
                    "interview liability: one deep follow-up question ends the claim."
                ),
            })
    return {"defensible": defensible, "vulnerable": vulnerable}


# Honest nearest neighbours: for a vulnerable term, which attested skills are
# truthfully adjacent? Used to build deterministic pivot lines. Only genuine
# adjacency belongs here — a pivot may never imply the vulnerable term itself.
PIVOT_NEAREST = {
    "snowpark": ["pyspark", "snowflake"],
    "apache kafka": ["pyspark", "apache airflow"],
    "apache flink": ["pyspark"],
    "terraform": [],
    "kubernetes": [],
    "looker": ["sql", "dbt"],
    "cockroachdb": ["sql"],
}


def pivot_for(term, attested):
    """Deterministic honest-pivot line for a vulnerable term. Facts only."""
    canon = canonical(term)
    for near in PIVOT_NEAREST.get(canon, []):
        if near in attested:
            a = attested[near]
            return (
                f"Honest pivot: lead with {a['term']} ({a['evidence']}). "
                f"Position {term} as adjacent to that record — never as claimed experience."
            )
    return (
        f"Honest pivot: state plainly that you have no production {term} "
        "experience. An honest gap survives an interview; an invented claim does not."
    )


# A metric claim is a number bound to a unit. The unit is part of the claim:
# a profile saying "5 years" attests the number 5, not "5%". Attestation
# therefore matches (number, normalized-unit) pairs, never bare numbers.
_METRIC_RE = re.compile(
    r"\d+(?:\.\d+)?\s*-?\s*(?:%|percent|×|x\b|fold\b|million|billion|k\b)", re.I)
_SCALE_RE = re.compile(
    r"\$\s*\d+(?:\.\d+)?\s*[Mm]\b"
    r"|\d+(?:\.\d+)?\s*[Mm]\s+(?:records|rows|events|users|requests|transactions)\b",
    re.I)
_UNIT_NORM = {"%": "%", "percent": "%", "×": "x", "x": "x", "fold": "fold",
              "million": "million", "billion": "billion", "k": "k", "m": "m"}


def _metric_pairs(text):
    pairs = set()
    for m in _METRIC_RE.finditer(text):
        num = re.search(r"\d+(?:\.\d+)?", m.group(0)).group(0)
        unit = re.search(r"(%|percent|×|x|fold|million|billion|k)\s*$",
                         m.group(0), re.I).group(1).lower()
        pairs.add((num, _UNIT_NORM[unit]))
    for m in _SCALE_RE.finditer(text):
        num = re.search(r"\d+(?:\.\d+)?", m.group(0)).group(0)
        pairs.add((num, "m"))
    return pairs


def _draft_terms(text):
    low = text.lower()
    found = {}
    terms = list(ALIASES.keys()) + LEXICON_EXTRA
    for term in terms:
        forms = [term] + ALIASES.get(term, [])
        for form in forms:
            if re.search(r"(?<![a-z0-9])" + re.escape(form) + r"(?![a-z0-9])", low):
                found[canonical(term)] = term
                break
    return found


def check_draft(draft_text, attested, profile_text):
    """Return a list of violations: unattested terms and invented metrics."""
    violations = []
    for canon, surface in sorted(_draft_terms(draft_text).items()):
        if canon not in attested:
            violations.append({
                "type": "unattested_term",
                "term": surface,
                "detail": f"'{surface}' appears in the draft but is not in the attested profile.",
            })
    attested_pairs = _metric_pairs(profile_text)
    tokens = [m.group(0) for m in _METRIC_RE.finditer(draft_text)]
    tokens += [m.group(0) for m in _SCALE_RE.finditer(draft_text)]
    for token in tokens:
        if _metric_pairs(token) - attested_pairs:
            violations.append({
                "type": "invented_metric",
                "term": token.strip(),
                "detail": f"Metric '{token.strip()}' appears in the draft but no such figure (number + unit) exists in the attested profile.",
            })
    return violations
