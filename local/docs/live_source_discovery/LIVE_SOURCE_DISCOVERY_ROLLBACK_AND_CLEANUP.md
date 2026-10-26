# Live Source Discovery Rollback And Cleanup

## Report Cleanup

- Delete the run output directory under `reports/source_discovery/live_readiness/` if the dry-run or plan should be discarded.
- Archive safe summaries only if required for review.
- Do not archive raw provider bodies.

## Temporary Database Cleanup

- Delete temporary SQLite files created for readiness or dry-run checks.
- Confirm the active development database was not used accidentally.

## Source Registry Rollback

- Review candidate rows created by the live discovery run.
- Delete or disable only the `pending_review` candidates associated with the specific run after human confirmation.
- Do not modify unrelated source rows.
- Confirm no candidate was promoted to `active`.

## Production Safety Checks

- Confirm no production database was written.
- Confirm no complete response body was persisted.
- Confirm no production connector or scheduled job was enabled.
- Confirm no remote deployment or SSH command was executed.
