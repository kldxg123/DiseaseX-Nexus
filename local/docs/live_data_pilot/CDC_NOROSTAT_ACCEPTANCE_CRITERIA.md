# CDC NoroSTAT Controlled Live Dry-Run Acceptance Criteria

These criteria apply to the future controlled live dry-run. The current preparation step is not executed against CDC and does not access the public internet.

Before these criteria can be applied, manual review must confirm robots access,
terms review, and `max_requests <= 3`.

## Required Pass Criteria

- Request count does not exceed `3`.
- No sensitive information is printed, logged, or written to reports.
- `raw_documents` writes at least one row, or if the real page structure changes, the connector returns a safe failure reason.
- `raw_documents` deduplication works for URL and content hash constraints.
- CleanerAgent can process the stored sample without crashing.
- Data Quality report is generated.
- Crawler Optimizer report is generated.
- Smoke test or equivalent local pipeline summary runs against the pilot database.
- All outputs remain summary-only and require human review.

## Non-Goals

- No requirement that real prediction accuracy reaches any threshold.
- No production model generation.
- No automatic model replacement.
- No remote deployment.
- No production decision-making.

## Failure Handling

If any required pass criterion fails, record the safe failure reason, stop the pilot, run rollback and cleanup, and do not proceed to prediction or deployment. The system does not guarantee prediction accuracy.
