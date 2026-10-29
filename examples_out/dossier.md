# Evidence Dossier

Every claim below was checked by deterministic code against the seeker's attested profile. There is no match score: a claim is either defensible in the interview room, or it is a liability.

## Defensible claims — verified, with evidence
- **Snowflake** — 3 years as the analytics warehouse; wrote SQL models and SnowSQL jobs loaded from the lake
- **Python** — 9 years across all roles; primary language for pipelines and tooling
- **SQL** — 9 years; complex window functions, query tuning on warehouse and lake
- **dbt** — 2 years of transformation models on the Snowflake warehouse
- **Apache Airflow** — 5 years authoring and operating production DAGs

## Vulnerable claims — the job asks, you cannot prove it
- **Snowpark** — No attested proof of Snowpark. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: lead with PySpark (6 years building batch pipelines on Amazon EMR). Position Snowpark as adjacent to that record — never as claimed experience.
- **Apache Kafka** — No attested proof of Apache Kafka. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: lead with PySpark (6 years building batch pipelines on Amazon EMR). Position Apache Kafka as adjacent to that record — never as claimed experience.
- **Terraform** — No attested proof of Terraform. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: state plainly that you have no production Terraform experience. An honest gap survives an interview; an invented claim does not.
- **Looker** — No attested proof of Looker. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: lead with SQL (9 years; complex window functions, query tuning on warehouse and lake). Position Looker as adjacent to that record — never as claimed experience.
- **Apache Flink** — No attested proof of Apache Flink. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: lead with PySpark (6 years building batch pipelines on Amazon EMR). Position Apache Flink as adjacent to that record — never as claimed experience.
- **Kubernetes** — No attested proof of Kubernetes. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: state plainly that you have no production Kubernetes experience. An honest gap survives an interview; an invented claim does not.
- **CockroachDB** — No attested proof of CockroachDB. Claiming it is an interview liability: one deep follow-up question ends the claim.
  - Honest pivot: lead with SQL (9 years; complex window functions, query tuning on warehouse and lake). Position CockroachDB as adjacent to that record — never as claimed experience.

## Verified narrative (guardrail-passed)
Built pipelines and tooling in Python across 9 years of roles.
Wrote SQL with complex window functions and tuned queries on warehouse and lake across 9 years.
Built batch pipelines in PySpark on Amazon EMR and operated Spark clusters for nightly and hourly loads over 6 years.
Authored and operated production DAGs in Apache Airflow over 5 years.
Authored transformation models in dbt on Snowflake warehouse over 2 years and wrote SQL models for Snowflake analytics warehouse loaded from the lake over 3 years.
Designed data lake storage and partitioning on Amazon S3 over 7 years, managed ACID tables on Delta Lake over 4 years, and used AWS Glue Catalog as metastore for EMR Spark jobs over 5 years.

## Audit trail — hallucinations caught by the guardrail
- Intake [unattested_term] **kafka** — 'kafka' appears in the draft but is not in the attested profile.
- Intake [unattested_term] **snowpark** — 'snowpark' appears in the draft but is not in the attested profile.
- Intake [unattested_term] **terraform** — 'terraform' appears in the draft but is not in the attested profile.
- Intake [invented_metric] **40%** — Metric '40%' appears in the draft but no such figure (number + unit) exists in the attested profile.

## My call (the human decides — the system never does)
Apply / Skip: ______  Reason: ______
