# Project Progress And Handoff

## Current Status

The project is now a local-first infectious disease intelligence MVP with a working end-to-end engineering loop. It supports local data-source registration, mock/fixture ingestion, cleaning, feature generation, baseline prediction, human feedback, minimum-data analysis, FastAPI business APIs, Streamlit dashboard scaffolding, worker scheduling scaffolding, local smoke tests, deployment dry-run checks, and Docker Compose static validation.

The current implementation is intentionally conservative. It does not perform live public-health website crawling by default, does not claim forecast accuracy, does not automatically retrain models, and does not automatically promote models into production. Forecasts and analysis outputs are auxiliary decision-support artifacts that require human review.

## Work Completed

### Project Foundation

- `pyproject.toml` manages Python dependencies and test configuration.
- `AGENTS.md`, `README.md`, `.gitignore`, `.dockerignore`, `.env.example`, `Dockerfile`, and `docker-compose.yml` are in place.
- Docker Compose defines local services for Postgres, Redis, MinIO, API, worker, dashboard, and MLflow.
- `.dockerignore` excludes server information files, local env files, key files, token files, local databases, caches, logs, and generated artifacts from the Docker build context.

### Database And Migrations

- SQLAlchemy ORM models are implemented in `packages/common/models.py`.
- Alembic migrations currently cover the main local MVP schema through:
  - initial schema
  - source registry
  - raw documents
  - epidemic events
  - model features
  - predictions
  - human feedback and experience bank
  - feedback table name migration
- Migration consistency tests validate upgrade-to-head and ORM/schema alignment with SQLite temporary databases.

### Common Infrastructure

- `packages/common/config.py` reads local configuration from environment variables.
- `packages/common/db.py` provides engine/session/init helpers.
- `packages/common/logging.py` provides structured logging and sensitive-field masking.
- `scripts/read_server_info.py` implements safe parsing/validation utilities for future deployment preparation without performing connections.

### Crawler And Source Layer

- `crawler_agent` includes source registry, source scoring, default seed sources, URL utilities, robots policy interface, HTTP fetcher, content extraction, raw document store, and crawler MVP.
- GDELT candidate discovery logic is implemented with query construction, disease/country term expansion, mock response parsing, source scoring, and source-registry writes.
- Official connector framework exists for WHO, ECDC, and CDC-style local fixture parsing through stable connector interfaces.
- CLI helpers exist for source seeding, source discovery, URL crawling, and official fixture parsing.
- Real external requests remain disabled in tests and are not part of the default local MVP flow.

### Cleaner And Feature Engineering

- `cleaner_agent` can clean raw documents, normalize text/date/disease/location signals, extract epidemic events, score quality, and store events.
- Feature engineering can aggregate epidemic events into weekly `model_features`.
- Feature store and CLI tests cover local synthetic/fixture data paths.

### Predictor Agent

- Baseline prediction supports moving-average, seasonal-naive, and feature-rule predictors.
- Rolling-origin backtesting, regression/classification metrics, prediction storage, and CLI support are implemented.
- Human feedback and experience bank are implemented with stable compatibility interfaces:
  - `HumanFeedback`
  - `FeedbackInput`
  - `FeedbackStore`
  - `ExperienceBankStore`
  - failure classification and lesson-building helpers
- Minimum Data Analyzer MVP evaluates feature-group/history-window subsets using local model features and baseline backtests.
- Model governance helpers support offline promotion checks; they do not auto-promote models.

### API And Dashboard

- FastAPI exposes local business APIs for health, sources, crawl/mock ingest, clean, features, models, feedback, experience, analysis, and jobs.
- API responses are sanitized to avoid full HTML, raw text, full feature payloads, full lesson payloads, and sensitive configuration.
- Streamlit dashboard modules include an API client, formatters, components, pages, safety helpers, and an app entrypoint.
- Dashboard tests use mock clients or local test clients rather than a live Streamlit server.

### Worker, Smoke Tests, And Docker Local Checks

- `worker_agent` includes job registry, task definitions, pipeline orchestration, scheduler config, runner, and CLI.
- `scripts/smoke_test.py` runs a local end-to-end flow with temporary SQLite and synthetic/local data.
- `scripts/check_compose.py` validates required Compose services, ports, safe commands, and forbidden mounts.
- `scripts/docker_local_check.py` supports:
  - static validation
  - optional local Docker build
  - optional local Compose up
  - optional container smoke test
  - optional Compose down
- In the current environment, Docker CLI was unavailable, so real container startup was skipped safely. Static checks and mock Docker command tests passed.

## Recent Verification Snapshot

- Docker-related local regression command passed: `72 passed, 1 warning`.
- `compileall` passed for `apps`, `packages`, `scripts`, `migrations`, and `tests`.
- Docker static check passed.
- Docker real build/up/smoke/down was not executed because Docker CLI was unavailable in the current environment.
- Ruff remains unavailable in the current environment and has not been repeatedly reinstalled.

## Important Constraints To Preserve

- Do not read, print, log, commit, or expose the real server information file.
- Do not execute SSH or remote deployment without explicit future approval.
- Do not place real `.env`, keys, tokens, server information, or credentials in Docker images, logs, tests, docs, or API responses.
- Do not access WHO, ECDC, CDC, GDELT, or other external outbreak websites unless a future task explicitly permits live access and the implementation obeys robots.txt, terms, rate limits, and retry backoff.
- Do not call external LLMs, translation APIs, geocoding APIs, or online services from tests.
- Do not modify existing `src/` business code unless a future task explicitly requires a narrow compatibility change.
- Do not claim that forecasts are guaranteed accurate.

## Estimated Next Difficulties

### 1. Moving From Fixtures To Lawful Live Connectors

The crawler stack is structurally ready, but live collection is the hardest compliance boundary. Each source needs robots.txt checks, terms review, rate limits, cache policy, user-agent policy, failure backoff, observability, and a clear switch that prevents accidental live network access in tests.

### 2. Data Quality And Label Reliability

Predictions will only be as useful as the cleaned event labels and feature quality. The system needs stronger duplicate detection, source conflict handling, quality scoring, lineage checks, and analyst review workflows before model outputs can be trusted operationally.

### 3. Model Evaluation With Sparse And Delayed Signals

Outbreak data is sparse, delayed, and inconsistently reported. Baselines are appropriate for MVP, but choosing thresholds, backtest windows, lead times, uncertainty intervals, and model promotion criteria will require domain decisions and careful validation.

### 4. Operationalizing Docker Compose

Static Docker checks pass, but real container validation still needs an environment with Docker available. Build failures may surface dependency, network, image pull, or service readiness issues. Compose health checks and startup sequencing will likely need refinement after a real run.

### 5. Analyst Workflow Design

The Dashboard/API currently expose MVP summaries. The next product challenge is designing a workflow where analysts can inspect provenance, compare source confidence, review predictions, submit feedback, and understand limitations without being overloaded.

### 6. Governance And Safety Boundaries

The project already has guardrails, but future deployment will need explicit decisions on secret management, deployment target, backup strategy, audit logs, data retention, access control, and incident response.

## Recommended Direction

1. Finish real local Docker Compose validation on a machine with Docker available.
2. Harden live connector readiness without enabling live crawling by default.
3. Expand data-quality checks and lineage summaries for raw documents, events, and features.
4. Improve analyst-facing review flows in API and Dashboard.
5. Define model evaluation gates and promotion criteria as policy, not automatic behavior.
6. Prepare deployment only through dry-run first, then require explicit approval for any real remote action.

## Decision Points

- Target scope: which countries, regions, diseases, and time horizons are in the first operational scope?
- Approved sources: which public sources are legally and operationally approved for live access?
- Live crawling policy: allowed schedules, user-agent string, rate limits, caching duration, and robots.txt handling.
- Review workflow: who can mark feedback, what statuses are required, and what evidence is enough for review completion?
- Forecast objective: case-count forecast, outbreak-risk forecast, early warning, or source-coverage monitoring?
- Evaluation gates: minimum acceptable backtest metrics, uncertainty coverage targets, and manual review requirements.
- Data retention: how long to retain raw documents, raw text, model features, feedback notes, and generated artifacts?
- Deployment target: local-only, internal VM, cloud VM, or managed container platform.
- Secret management: local `.env`, vault, platform secrets, or another approved mechanism.
- Container validation standard: whether real Docker Compose smoke test is mandatory before each release.

## Suggested Work Package For A New Team Member

### Title

Live Connector Readiness And Compliance Hardening

### Goal

Prepare the ingestion layer for future approved live public-source collection while keeping tests and default local runs offline. The work should turn the current fixture/mock connector framework into a clearly gated, auditable connector-readiness layer.

### Background

The crawler and connector MVP already supports source registry metadata, mock GDELT discovery, official fixture parsing, raw document storage, and safe local tests. The missing bridge is a production-quality readiness layer that proves each connector can be configured safely before any real network access is enabled.

### In Scope

- Add connector readiness metadata for each official or candidate source:
  - source name
  - base URL
  - allowed use summary
  - robots policy status
  - expected parser
  - rate limit
  - retry/backoff policy
  - cache policy
  - live access disabled by default
- Add a dry-run readiness CLI, for example:
  - `python -m crawler_agent.connector_readiness --source <name> --dry-run`
- Add tests that use local fixtures or mocked HTTP only.
- Add validation that refuses live fetch unless an explicit future flag and configuration are provided.
- Add safe summaries to README or a dedicated docs page.
- Ensure no response, log, or test output contains secrets, full raw HTML, credentials, server information, or private paths.

### Out Of Scope

- Do not perform real WHO, ECDC, CDC, GDELT, or other website requests.
- Do not implement deployment.
- Do not change predictor behavior.
- Do not add external paid APIs or LLM calls.
- Do not alter existing `src/` code.
- Do not add automatic model retraining or model promotion.

### Expected Deliverables

- `packages/crawler_agent/connector_readiness.py`
- Optional CLI wrapper or module entrypoint for readiness dry-run.
- Tests such as:
  - `tests/test_connector_readiness.py`
  - updates to connector registry tests if needed
- Documentation section describing connector readiness, live-access gating, and compliance checklist.
- A safe JSON output format for readiness summaries.

### Acceptance Criteria

- All readiness tests pass using only mocked/local data.
- Existing crawler, connector, API, smoke, and safety tests continue to pass.
- Default behavior performs no live network access.
- Live access cannot be triggered accidentally by normal tests or local smoke tests.
- Outputs include compliance limitations and review-required wording.
- No sensitive values, server information, credentials, tokens, private key paths, or full raw content appear in logs or test output.

### Suggested Test Command

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_connector_readiness.py tests/test_official_connector_registry.py tests/test_official_connectors.py tests/test_crawler_mvp.py tests/test_api_sources.py tests/test_smoke_test_safety.py tests/test_read_server_info.py
```

### Notes For The New Team Member

Start by reading:

- `AGENTS.md`
- `README.md`
- `packages/crawler_agent/source_registry.py`
- `packages/crawler_agent/default_sources.py`
- `packages/crawler_agent/connectors/`
- `packages/crawler_agent/http_fetcher.py`
- `packages/crawler_agent/robots.py`
- `tests/test_official_connectors.py`
- `tests/test_crawler_mvp.py`

Do not read or print real server information files. Do not run SSH. Do not enable live network calls without a separate explicit approval step.
