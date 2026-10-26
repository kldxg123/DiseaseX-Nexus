# CDC NoroSTAT Controlled Live Dry-Run Runbook

## Purpose

Prepare a controlled live dry-run for CDC NoroSTAT / United States / norovirus. The future dry-run will verify whether the guarded CDC NoroSTAT connector can fetch, parse, deduplicate, and store a very small number of records under the Live Access Guardrails.

This preparation step is not executed against CDC. It does not access the public internet and does not run live fetch.

## Scope

- Source: `cdc_norostat`
- Geography: United States
- Disease: norovirus
- Maximum requests: `max_requests <= 3`
- Data path: guarded live fetch -> official connector parser -> `source_registry` / `raw_documents` -> cleaner -> features/prediction only after human review

## Required Human Review Before Live Fetch

Before any real request, a human reviewer must confirm:

- the CDC URL to use
- CDC terms and usage requirements
- robots access result
- request count and rate limit
- output directory and database path
- rollback and cleanup plan

## Future Live Command Template

Do not execute this in automated tests. It is for the next phase after explicit human approval.

```bash
python scripts/parse_official_connector.py --connector cdc_norostat --allow-network --live-url <CDC_NOROSTAT_URL_REVIEWED_BY_HUMAN> --terms-reviewed --manual-reviewed --robots-allowed --max-requests 1
```

The URL placeholder must be replaced only after manual review. This runbook does not execute that command.

## Output Directory

Future live pilot outputs should be grouped under:

```text
reports/live_pilot/cdc_norostat_<timestamp>/
```

Recommended contents:

- command summary
- safe execution log
- connector parse summary
- data quality report
- crawler optimizer report
- smoke test summary
- rollback notes

## Post-Run Local Checks

After the future live dry-run, execute local checks using the pilot database:

```bash
python -m data_quality_agent.cli report --database-url <PILOT_DATABASE_URL> --output-dir reports/live_pilot/cdc_norostat_<timestamp>
python -m crawler_optimizer_agent.cli analyze --database-url <PILOT_DATABASE_URL> --country "United States" --disease "norovirus"
python scripts/smoke_test.py --database-url <PILOT_DATABASE_URL>
```

## Production Boundary

The controlled live dry-run must not enter production prediction or production deployment automatically. Any prediction, data quality result, or optimizer recommendation remains auxiliary analysis and requires human review. The project does not guarantee prediction accuracy.
