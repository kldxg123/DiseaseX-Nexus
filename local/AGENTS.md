# AGENTS.md

## Project Goal

Build a local-first infectious disease intelligence platform for public-health monitoring workflows:

- Collect outbreak-related public information from approved, lawful sources.
- Clean, normalize, deduplicate, and preserve source provenance for every record.
- Produce forecasting outputs with uncertainty intervals, assumptions, and review status.
- Visualize trends, alerts, source coverage, and model confidence for human analysts.
- Run locally with Docker Compose first, with remote deployment only after explicit approval.

This project must support a defensible workflow. It is an intelligence and decision-support system, not a source of guaranteed predictions or medical advice.

## Expected Modules

- `ingestion`: robots.txt-aware crawlers and source connectors.
- `cleaning`: schema normalization, validation, deduplication, and enrichment.
- `storage`: local development database, raw-source archive, and lineage metadata.
- `forecasting`: baseline statistical models with uncertainty and backtesting.
- `review`: human review queue, source traceability, and analyst notes.
- `visualization`: dashboards, maps, time series, and alert views.
- `deployment`: Docker Compose runtime, environment templates, and later remote deployment scripts.

## Coding Standards

- Prefer clear, typed Python with small modules and explicit interfaces.
- Keep side effects at the boundary: network access, filesystem writes, database writes, and model artifact writes should be easy to test.
- Use structured parsers and validated schemas instead of ad hoc string processing where practical.
- Store timestamps with timezone awareness.
- Keep raw observations, cleaned observations, model inputs, model outputs, and human-review decisions logically separate.
- Preserve source URLs, retrieval timestamps, parser versions, and transformation metadata.
- Do not add unrelated refactors while implementing a focused change.

## Security And Compliance Constraints

- Never print, commit, log, document, or expose server IPs, usernames, passwords, private-key paths, tokens, API keys, or secret values.
- Do not read or display `服务器信息.txt` unless the user explicitly asks for deployment preparation that requires it. Even then, never echo its contents.
- Do not perform SSH connections or remote deployment unless the user explicitly requests it in a later turn.
- Keep `服务器信息.txt`, `.env`, key files, token files, credentials, local databases, logs, generated model artifacts, and raw scraped data out of git.
- Crawlers must obey `robots.txt`, site terms of service, rate limits, retry backoff, and cache policies.
- Crawlers must not bypass login, captcha, paywalls, access controls, anti-abuse systems, or private APIs.
- Forecasts must include uncertainty, source provenance, model version, run timestamp, and human-review state.
- Forecasts must not claim guaranteed accuracy.
- Health-related outputs must include analyst review paths and clear limitations.

## Testing Requirements

- Add unit tests for parsing, schema validation, deduplication, and feature generation.
- Add integration tests for source adapters using recorded fixtures or local test servers, not uncontrolled live scraping.
- Add model tests for reproducibility, input validation, uncertainty output shape, and backtesting metrics.
- Add dashboard/API tests for key workflows once those modules exist.
- Add Docker Compose smoke tests before declaring local startup complete.
- Tests must not require real secrets, SSH access, live credentials, or paid/private data.

## Operational Rules For Agents

- Inspect directory structure without printing sensitive file contents.
- Before touching deployment scripts, verify whether the change could reveal credentials.
- Keep `.env.example` placeholder-only.
- Prefer local development and local Docker Compose verification before any remote action.
- If a task risks exposing secrets or violating data-access rules, stop and ask for clarification.
