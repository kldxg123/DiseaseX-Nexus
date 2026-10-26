# CDC NoroSTAT Rollback And Cleanup

This rollback guide is for the future controlled live dry-run. The current preparation step is not executed against CDC and does not access the public internet.

The future run must only happen after manual review of robots, terms, and
`max_requests <= 3`.

## Cleanup Targets

- Pilot SQLite database file, if one was created.
- `reports/live_pilot/cdc_norostat_<timestamp>/` output directory.
- Any `source_registry` or `raw_documents` rows created by the live pilot in a non-production database.
- Temporary command summaries or safe logs generated for the pilot.

## Suggested Cleanup Steps

1. Stop any pilot command that is still running.
2. Confirm no production service is using the pilot database.
3. Archive summary-only reports if needed for audit.
4. Delete the pilot SQLite file.
5. Delete `reports/live_pilot/cdc_norostat_<timestamp>/` if the run should be fully discarded.
6. If using a shared local database, remove rows marked with the pilot connector/source metadata after human confirmation.
7. Run the data quality report again to confirm pilot rows are absent if cleanup was intended.

## Safety Verification

- Confirm reports do not include real server information.
- Confirm reports do not include token, password, secret, or private-key material.
- Confirm no `.env` values were copied into pilot outputs.
- Confirm no live command is embedded into automated tests.

## Rollback Boundary

Rollback is local-only for this MVP. Remote deployment and production cleanup are out of scope until explicitly approved. All data-quality and prediction outputs remain auxiliary analysis, require human review, and do not guarantee prediction accuracy.
