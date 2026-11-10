# ADR 0001 — AWS for ingestion/ops, Databricks for transformation/ML

- **Status:** accepted
- **Date:** 2026-10-08

## Context

A portfolio pipeline over San Diego's Get It Done (311) data: a daily CSV
snapshot of open requests plus yearly closed files, transformed into
bronze/silver/gold tables and a model that predicts time-to-close. Budget is
~$0, one maintainer (a student), and the goal is to show skills employers ask
for: cloud infra as code, orchestration, data quality, ML monitoring, CI/CD.

## Decision

- **AWS** does the "plumbing": scheduled download (Lambda + EventBridge
  Scheduler), immutable raw storage (S3, versioned), secrets (SSM), metrics,
  alarms and budgets (CloudWatch, SNS, Budgets). All in Terraform.
- **Databricks** does the "data work": bronze → silver → gold (Delta, dbt),
  model training/scoring, and writes a small `run_summary.json` that AWS reads.
- The boundary is two narrow contracts: Parquet files in a UC Volume
  (AWS → Databricks) and `run_summary.json` (Databricks → AWS).

## Alternatives considered

1. **AWS only** (Lambda + S3 + Athena or Redshift Serverless + SageMaker).
   Cheapest at this scale with Athena; one cloud, one IAM. But Redshift
   Serverless has a minimum charge that can blow a $5 budget, SageMaker is
   pricey/complex for a student, and we lose notebooks + Delta + MLflow.
2. **Databricks only** (Jobs fetch the CSV themselves, Databricks alerts).
   Fewer moving parts. But Free Edition's serverless compute has restricted
   outbound internet and quotas, no IaC story for alerts/budgets, and the
   portfolio would show no cloud-infrastructure skills.

## Consequences

- (+) Each side does what it is good at; failures are isolated (S3 keeps raw
  data even if Databricks is down; a re-run just re-uploads).
- (+) Shows Terraform, IAM, OIDC CI/CD *and* Spark/dbt/MLflow.
- (−) Two consoles, two credential sets, a cross-cloud token in SSM to rotate.
- (−) Contracts can drift; mitigated by `docs/run_summary_schema.md`, tests on
  both sides, and the `JobMissing` alarm.

## Free-tier notes (verify current terms before relying on them)

- **AWS:** new accounts (since July 2025) choose a *Free plan* with credits;
  that plan **closes after 6 months** (or when credits run out) unless you
  upgrade to a paid plan. Our steady-state cost is cents/month (Lambda,
  S3 < 5 GB, a few custom metrics and alarms — custom metrics and alarms are
  the main line items beyond the always-free tiers). Budgets email at $1/$5.
- **Databricks Free Edition:** serverless only, small fixed quotas on
  compute/warehouse size, limited outbound network, one workspace, no SLA,
  not for commercial use. Jobs can be throttled or stopped if quotas are hit;
  the `JobMissing` alarm is how we notice.
