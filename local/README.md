# Infectious Disease Intelligence Platform

This repository is being prepared as a local-first project for infectious disease intelligence collection, cleaning, forecasting, visualization, and later deployment.

The system is intended for public-health monitoring and analyst decision support. It must preserve source provenance, expose uncertainty, and include human review. Forecasts are not guaranteed predictions and must not be presented as medical advice.

## MVP Scope

The MVP should focus on a small, auditable pipeline:

- Source registry for approved public sources.
- Robots.txt-aware and rate-limited collection jobs.
- Raw observation storage with retrieval metadata.
- Cleaning and normalization into a validated event schema.
- Deduplication and basic REDACTED checks.
- Baseline forecasting with uncertainty intervals.
- Human review states for observations and forecast outputs.
- Dashboard views for trends, sources, alerts, and review status.
- Docker Compose local runtime for development and smoke testing.

Out of scope for the MVP:

- Bypassing access controls, captcha, paywalls, login walls, or anti-abuse systems.
- Claims of guaranteed forecast accuracy.
- Automated public-health recommendations without human review.
- Remote server deployment before explicit user approval.

## Planned Architecture

```text
Approved public sources
        |
        v
Ingestion jobs -> raw archive -> cleaning/validation -> normalized storage
        |                                   |
        |                                   v
        +---------------------------- provenance metadata
                                            |
                                            v
                               forecasting + uncertainty
                                            |
                                            v
                         review workflow + visualization
```

## System Modules

- `ingestion`: compliant source adapters, robots.txt checks, rate limiting, retry backoff, and fetch logs.
- `cleaning`: schema validation, normalization, deduplication, and data-quality reports.
- `storage`: local database, raw source archive, lineage metadata, and migration scripts.
- `forecasting`: reproducible baseline models, backtesting, confidence/credible intervals, and model cards.
- `review`: human review queue, analyst decisions, source traceability, and audit trail.
- `visualization`: local dashboard for trends, maps, source coverage, forecast uncertainty, and review status.
- `deployment`: Docker Compose local setup first, then remote deployment only after explicit approval.

## Local Startup

The project must be runnable locally with Docker Compose before any remote deployment work starts.

Static local Docker Compose validation:

```bash
python scripts/docker_local_check.py --static-only
```

Optional local container validation, only when Docker is available:

```bash
python scripts/docker_local_check.py --build
python scripts/docker_local_check.py --up
python scripts/docker_local_check.py --smoke
python scripts/docker_local_check.py --down
```

Expected local workflow:

```bash
cp .env.example .env
docker compose up --build
```

Do not place real secrets in `.env.example`. Keep local `.env` files untracked.
The `.dockerignore` file excludes local server information files, real `.env`
files, private-key files, token files, local caches, local databases, and model
artifacts from the Docker build context. This step does not perform remote
deployment; real deployment must first pass dry-run review and requires explicit
approval.

## Environment & Packaging

Use a project-local virtual environment and install the package with standard
editable packaging when possible:

```bash
python -m pip install -e ".[dev]"
```

`ruff` is kept in the separate optional `lint` group so a temporarily
unavailable lint package does not block core runtime and test installation:

```bash
python -m pip install -e ".[lint]"
```

If the current pip source cannot provide optional tools, run the core local
verification path first:

```bash
python -m pip install -e . --no-deps
python scripts/check_environment.py
python scripts/smoke_test.py --temp-db
```

`scripts/check_environment.py` validates Python version, pyproject parsing,
core imports, pytest/setuptools availability, package discovery, and whether a
manual local path workaround is present. The script outputs safe JSON only and
does not read server information files or print environment variables.

This repository uses a local no-network editable build backend so the current
project can be installed even when the virtual environment does not have
`setuptools` or `wheel` importable. Do not use a hand-written `.pth` file or a
manual `PYTHONPATH=packages` setting as the official workflow. Editable
installation should make `common`, `crawler_agent`, `cleaner_agent`,
`predictor_agent`, `worker_agent`, `crawler_optimizer_agent`,
`data_quality_agent`, `source_discovery_agent`, and `apps.api.main` importable.

After installation, the source discovery CLI should run without manual
`PYTHONPATH`:

```bash
python -m source_discovery_agent.cli discover-fixture \
  --database-url sqlite:///tmp/source_discovery.db \
  --country "United States" \
  --disease "norovirus" \
  --fixture tests/fixtures/source_discovery_norovirus_search_results.json \
  --output-dir reports/source_discovery
```

If environment checks report `setuptools_available=false`, that is diagnostic
context for the local environment; do not treat a manual path workaround as the
fix. Re-run the editable install command and `python scripts/check_environment.py`.

Never include local server information files, `.env`, private keys, token
files, credentials, local databases, raw collected data, logs, or generated
model artifacts in a package build or Docker image.

## Configuration

Use `.env.example` as the placeholder-only template for local development settings. Real credentials, tokens, private key paths, server addresses, and deployment values must stay outside git.

Sensitive local files are intentionally ignored, including:

- `服务器信息.txt`
- `.env`
- key and token files
- local logs
- local databases
- raw collected data
- generated model artifacts

## Data Collection Rules

All crawlers and connectors must:

- Respect `robots.txt` and site terms.
- Use explicit rate limits and retry backoff.
- Identify source, retrieval time, parser version, and transformation lineage.
- Avoid login-gated, captcha-protected, paid, private, or access-controlled content unless lawful access and implementation approval are provided.
- Use fixtures or local test servers for automated tests instead of uncontrolled live scraping.

## Data Source Registry And Seed Sources

The current source registry only manages local metadata. It does not fetch pages, call
external APIs, check live `robots.txt`, or run any crawler.

Default seed sources include logical metadata entries for WHO, ECDC, CDC, and GDELT
sources. They are stored so later crawler work can be reviewed before any network
collection is implemented.

Example local commands:

```bash
python scripts/seed_sources.py --database-url sqlite:///tmp/test.db --dry-run
python scripts/seed_sources.py --database-url sqlite:///tmp/test.db
```

Source statuses:

- `active`: approved for future collection work.
- `pending_review`: candidate source awaiting review.
- `degraded`: repeated failures reached the local threshold.
- `disabled`: source must not be used by future collection work.

Future real crawlers must obey `robots.txt`, rate limits, source provenance, and site
terms before fetching any content.

## Live Data Access Guardrails MVP

Real network collection remains disabled by default. Before any future live
connector can access a public source, it must pass a source access policy check
and a live fetch guard decision. The current MVP only implements the guardrails;
it does not perform live fetches.

Source access policies live in:

```bash
configs/source_access_policy.example.json
```

Each future live source must define its reviewed access method, allowed domains,
robots.txt requirement, terms review requirement, manual review requirement,
request limit, and minimum interval. The example policy uses placeholder
`example.invalid` URLs and is not authorization to access any real source.

Static local checks:

```bash
python scripts/check_source_access_policy.py --policy configs/source_access_policy.example.json
python scripts/check_source_access_policy.py --policy configs/source_access_policy.example.json --source-key cdc_norostat --url https://example.invalid/test --allow-network --terms-reviewed --manual-reviewed --robots-allowed
```

Guard decisions require an explicit `--allow-network` flag plus robots, terms,
and manual-review confirmations when the policy requires them. Denied decisions
are expected for incomplete reviews and do not perform network access.

The same guardrail layer is available through:

- API: `GET /access-policy` and `POST /access-policy/check`
- Dashboard: `Access Guardrails`
- Worker: `check_access_policies`
- Smoke test: `access_policy` safe summary

These entry points only load policy files, validate review flags, and return a
safe decision summary. They do not issue HTTP requests, check live websites,
store policy rows in the database, or claim that any real source has already
passed compliance review.

Do not write credentials, cookies, server information, private-key paths, tokens,
or secret values into policy files. Any real source must pass a separate human
compliance review before live access is enabled in a later approved step.

### CDC NoroSTAT Live Connector Pilot

CDC NoroSTAT now has a guarded live connector pilot path in code. It remains
disabled by default and is not exercised against CDC in automated tests.

The connector can still parse local fixtures. A future live run must explicitly
set `--allow-network` and pass all guardrail conditions:

- source access policy permits the requested URL domain
- `robots_allowed=True`
- `terms_reviewed=True`
- `manual_reviewed=True`
- request count is within the configured limit

All tests use mock HTML or monkeypatched connector calls. They do not request
CDC or any other public website. Real CDC access requires a separate human
compliance review before use.

Fixture example:

```bash
python scripts/parse_official_connector.py --connector cdc_norostat --fixture tests/fixtures/cdc_norostat_live_mock.html --dry-run
```

Future live-access example, not for automated tests:

```bash
python scripts/parse_official_connector.py --connector cdc_norostat --allow-network --live-url https://www.cdc.gov/norovirus/php/reporting/norostat-data-table.html --terms-reviewed --manual-reviewed --robots-allowed --max-requests 1
```

Do not pass tokens, cookies, server information, credentials, or private-key
paths into connector CLI commands.

### CDC NoroSTAT Controlled Live Pilot Preparation

The controlled live pilot preparation package sets up the final local safety
gate before any real CDC NoroSTAT request. It creates runbooks, preflight
checks, plan files, acceptance criteria, rollback notes, and a dry-run executor
skeleton. It does not access CDC, does not execute live fetch, and does not
start Docker or remote deployment.

Preparation commands:

```bash
python scripts/live_data_preflight.py --source-key cdc_norostat --policy configs/source_access_policy.example.json --json
python scripts/plan_live_data_pilot.py --source-key cdc_norostat --country "United States" --disease "norovirus" --output-dir reports/live_pilot
python scripts/run_controlled_live_pilot.py --source-key cdc_norostat --dry-run
```

Runbook files:

```bash
docs/live_data_pilot/CDC_NOROSTAT_LIVE_DRY_RUN_RUNBOOK.md
docs/live_data_pilot/CDC_NOROSTAT_PREFLIGHT_CHECKLIST.md
docs/live_data_pilot/CDC_NOROSTAT_ACCEPTANCE_CRITERIA.md
docs/live_data_pilot/CDC_NOROSTAT_ROLLBACK_AND_CLEANUP.md
```

The next phase may perform a 1-3 request live dry-run only after explicit human
approval, manual URL confirmation, terms review, robots review, and
`--allow-network`. After any future live run, generate a data quality report,
run crawler optimizer diagnostics, and run a smoke test summary. Do not put real
server information, credentials, tokens, or private-key material into commands
or reports.

## Crawler Optimization Agent MVP

The crawler optimization agent analyzes local `source_registry` and
`raw_documents` records to diagnose crawler and source-quality problems. It can
identify repeated source failures, robots denial, rate limiting, empty or failed
parses, low relevance, duplicate content, stale sources, and candidate sources
that may deserve human review.

The agent only produces safe recommendations and draft `ConnectorSpec`
summaries. It does not rewrite crawler code, does not run live fetches, does not
auto-activate pending sources, and does not automatically disable high-risk
sources. High-risk recommendations always require human review.

Example local commands:

```bash
python -m crawler_optimizer_agent.cli analyze --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus"
python -m crawler_optimizer_agent.cli analyze-source --database-url sqlite:///tmp/test.db --source-id 1 --disease "norovirus"
python -m crawler_optimizer_agent.cli connector-spec --database-url sqlite:///tmp/test.db --source-id 1
```

Reports intentionally omit raw text, HTML, full metadata payloads, credentials,
tokens, private-key paths, and server information. Future Dashboard/API views
can display these optimization recommendations as analyst-facing review items.
Before real data access, every source still needs robots.txt checks, site terms
review, rate-limit policy, and the Live Data Access Guardrails layer.

### Crawler Optimization Agent Integration

The crawler optimizer is now exposed through the local API, Dashboard, Worker,
and smoke test path. It diagnoses crawler failures, parser failures, low
relevance, duplicate content, stale sources, and failed data sources from local
`source_registry` and `raw_documents` rows.

It can produce recommended actions and draft `ConnectorSpec` summaries for
human review. It does not automatically put new crawler code online, enable
`pending_review` sources, run live fetches, or execute high-risk actions. Any
high-risk action remains marked as requiring human review.

API examples:

```bash
POST /crawler-optimizer/analyze
POST /crawler-optimizer/analyze-source
POST /crawler-optimizer/connector-spec
```

Worker example:

```bash
python -m worker_agent.cli run-task --database-url sqlite:///tmp/test.db --task optimize_crawlers --country "United States" --disease "norovirus"
```

Dashboard page:

- `Crawler Optimizer`

Responses intentionally return safe summaries only. They omit full HTML, raw
text, full metadata payloads, full provenance payloads, feature payloads,
lesson payloads, credentials, tokens, private-key paths, and server information.

## Source Discovery Agent MVP

`source_discovery_agent` discovers candidate data sources from `country + disease`
using local fixture/mock search results only. The MVP does not run live search,
does not request CDC, WHO, ECDC, GDELT, search engines, or any external site, and
does not call online APIs.

Current behavior:

- Builds offline query suggestions from country and disease aliases.
- Parses a caller-provided search-results fixture.
- Scores candidates for authority, relevance, structured-data likelihood,
  freshness, and compliance risk.
- Writes selected candidates to `source_registry` with status `pending_review`.
- Never automatically marks a source `active`.
- Generates safe `ConnectorSpec` draft suggestions for higher-value candidates.
- Does not write connector code or enable live access.
- Requires human review for all candidates and ConnectorSpec suggestions.

CLI example:

```bash
python -m source_discovery_agent.cli discover-fixture \
  --database-url sqlite:///tmp/source_discovery.db \
  --country "United States" \
  --disease "norovirus" \
  --fixture tests/fixtures/source_discovery_norovirus_search_results.json \
  --output-dir reports/source_discovery
```

Worker task, disabled for scheduling by default:

```bash
python -m worker_agent.cli run-task \
  --database-url sqlite:///tmp/source_discovery.db \
  --task discover_sources_fixture \
  --country "United States" \
  --disease "norovirus" \
  --fixture tests/fixtures/source_discovery_norovirus_search_results.json \
  --output-dir reports/source_discovery
```

API and Dashboard integration is pending. Any future live discovery must go
through Live Access Guardrails, explicit scope limits, rate limits, and human
approval before network access is enabled.

## Cleaner Agent MVP

The current `cleaner_agent` MVP is fully local and rule based. It does not use
external NLP models, LLMs, translation services, geocoding APIs, or public web
requests.

It reads `raw_documents`, cleans text, applies simple language, disease, country,
date, setting, and numeric extraction rules, then writes candidate
`epidemic_events`. The first supported extraction path focuses on basic English
and Chinese norovirus event text, while preserving provenance and quality scores
for later human review.

Example local commands:

```bash
python -m cleaner_agent.agent --database-url sqlite:///tmp/test.db --limit 100
python -m cleaner_agent.agent --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --limit 100
```

Cleaner output is preprocessing input for later modeling. Events require manual
review and quality scoring, and the project does not claim medical, public-health,
or forecasting accuracy from these rule-based extraction results.

## Feature Engineering MVP

The feature engineering MVP builds `model_features` from reviewed or candidate
`epidemic_events`. It aggregates events by country, optional admin region,
disease, and ISO week, then creates lag, rolling-window, source mix, data quality,
seasonality, and placeholder holiday features.

Current targets support 7-day and 28-day horizons. Feature rows are skipped when
future target windows are incomplete, and lag/rolling features only use the
current week and prior weeks to avoid future-data leakage.

Example local command:

```bash
python -m cleaner_agent.features_cli --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --horizons-days 7,28
```

These features are preparation for later model training. They do not mean a
prediction model has been trained, validated, or deployed.

## Predictor Agent MVP

The `predictor_agent` MVP reads weekly `model_features`, runs local baseline
models, performs rolling-origin backtests, computes basic metrics, and writes
the latest baseline outputs to `predictions`.

Current baseline interfaces:

- `moving_average`
- `seasonal_naive`
- `feature_rule`

Prediction rows include a point estimate, lower and upper uncertainty bounds,
the uncertainty method, backtest metrics, provenance, and a pending review state.
The implementation uses Python standard-library logic and does not call external
ML services, LLMs, APIs, or live data sources.

Example local command:

```bash
python -m predictor_agent.agent --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --horizons-days 7,28
python -m predictor_agent.agent backtest --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --horizon-days 28 --model moving_average
python -m predictor_agent.agent predict --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --horizon-days 28 --model feature_rule
```

These outputs are baseline decision-support artifacts for later evaluation. They
do not claim medical or public-health forecasting accuracy.

## Feedback & Experience Bank MVP

The feedback and experience-bank MVP lets analysts review a stored `prediction`,
mark it as success, failure, partial success, or unknown, attach optional
observed case counts, outbreak labels, reasons, and notes, then records derived
failure analysis in `model_experience_bank`.
The final feedback table name is `human_feedback`; `PredictionFeedback*` names
remain as compatibility imports for older local code.

Recommended long-term interfaces:

- `HumanFeedback`
- `FeedbackInput`
- `FeedbackStore`
- `ExperienceBankStore`

Database tables:

- `human_feedback`
- `model_experience_bank`

Failure and partial-success feedback can create experience-bank rows. These rows
store failure type, absolute error where available, model version, horizon,
target date, a retraining-weight suggestion, and safe lesson metadata. Full raw
texts, full notes, credentials, and server information are not stored in lesson
JSON.

The champion/challenger governance helper only performs offline promotion
checks. It compares backtest metrics, warning state, and optional false-alarm or
missed-outbreak regressions, then returns a recommendation. It does not retrain
models, replace production models, run deployments, or claim that a model will
automatically improve. Any model replacement must go through backtesting and
human review.

Example local commands:

```bash
python -m predictor_agent.feedback_cli --database-url sqlite:///tmp/test.db --prediction-id 1 --status failure --actual-value 12 --actual-outbreak-label true --failure-reason under_prediction
python -m predictor_agent.feedback_cli --database-url sqlite:///tmp/test.db --summary
python -m predictor_agent.feedback_cli --database-url sqlite:///tmp/test.db --experiences
python -m predictor_agent.agent submit-feedback --database-url sqlite:///tmp/test.db --prediction-id 1 --verdict fail --true-cases 42 --true-outbreak-label true --reason "false negative"
python -m predictor_agent.agent build-experience --database-url sqlite:///tmp/test.db --feedback-id 1
python -m predictor_agent.agent summarize-feedback --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus"
python -m predictor_agent.agent summarize-experiences --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus"
```

Experience records are for later human analysis, dataset diagnostics, and future
model work. This MVP does not trigger automatic retraining,
champion/challenger replacement, public-health decisions, or claims of
prediction accuracy.

## Minimum Data Analyzer MVP

The Minimum Data Analyzer uses existing `model_features` rows and rolling
backtests to estimate the smallest feature-group set and history window needed
to reach a requested backtest metric threshold under the current data.

It supports:

- Learning curves across configurable history windows.
- Feature group ablation.
- Cost-aware feature subset search and greedy-compatible feature-group
  selection.
- Canonical feature groups such as `case_history`, `outbreak_history`,
  `source_volume`, `quality`, `seasonality`, and `holiday`, with older aliases
  such as `case_counts` and `data_quality` kept for compatibility.
- Metrics: `pr_auc`, `auroc`, `f1_score`, `brier_score`, `mae`, `rmse`, `mape`,
  `smape`, and `interval_coverage`.

Example local command:

```bash
python -m predictor_agent.minimum_data_cli analyze --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --horizon-days 28 --target-metric pr_auc --min-score 0.75 --feature-groups case_counts,outbreak_counts,media_signals,official_signals,data_quality,seasonality
python -m predictor_agent.minimum_data_cli --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus" --horizon-days 28 --target-metric mae --min-score 10 --candidate-feature-groups case_history,outbreak_history,seasonality,quality
```

Small datasets, single-class labels, or insufficient rolling windows may return
`null` scores for some metrics. Results describe the current historical data and
backtest settings only; they do not guarantee future accuracy. This MVP does not
access live sources, retrain models, or replace production models automatically.
Outputs include `minimum_feature_set`, `minimum_history_weeks`,
`expected_score`, a simple confidence interval, and removable/non-removable
feature groups. Results require human review and must not be used as the sole
basis for public-health decisions.

## Data Quality Report & Label Definition MVP

The data-quality MVP profiles local database tables and audits model-feature
labels before any real data source is used for modeling. It covers
`source_registry`, `raw_documents`, `epidemic_events`, `model_features`,
`predictions`, `human_feedback`, and `model_experience_bank`.

The label definition document is:

```bash
docs/label_definition.md
```

The report checks missing fields, duplicate summaries where the schema permits
them, extraction confidence, feature-key coverage, target availability, and
possible label-leakage risks such as feature keys containing `future`,
`next_`, `target`, or `label`. It emits safe JSON and Markdown summaries and
does not include raw text, complete HTML, full `feature_json`, full
`lesson_json`, full provenance, or full metadata payloads.

CLI examples:

```bash
python -m data_quality_agent.cli profile --database-url sqlite:///tmp/test.db
python -m data_quality_agent.cli label-audit --database-url sqlite:///tmp/test.db --country "United States" --disease "norovirus"
python -m data_quality_agent.cli report --database-url sqlite:///tmp/test.db --output-dir reports
```

Local API endpoints:

- `GET /data-quality/profile`
- `GET /data-quality/label-audit`
- `POST /data-quality/report`

Worker task:

```bash
python -m worker_agent.cli run-task --database-url sqlite:///tmp/test.db --task data_quality_report --params-json "{\"output_dir\":\"reports\"}"
```

Dashboard page:

- `Data Quality`

These outputs are data-readiness and label-consistency checks only. They do not
guarantee model accuracy and must be reviewed by an analyst before real-world
use.

## Release Readiness & Handoff

The current handoff package is stored in:

```bash
reports/release_readiness/
```

It contains the system acceptance report, feature matrix, test and validation
report, security boundary report, next-phase plan, handoff commands, and known
limitations. The package states that the current system is a local MVP: real
data validation, production deployment, authentication, monitoring, and model
accuracy validation remain next-phase work.

Run the release summary check:

```bash
python scripts/release_summary.py
```

Do not place real server information, `.env` values, credentials,令牌, or
密钥 material in release reports. Reports should describe safety boundaries and
handoff commands only, not operational secrets.

## FastAPI MVP

The FastAPI layer is a local development MVP for dashboard and integration work.
It exposes existing local services without enabling live external crawling,
remote deployment, task scheduling, automatic retraining, or automatic production
model replacement.

Primary endpoints:

- `GET /health`
- `GET /sources`, `GET /sources/health`, `POST /sources/seed`,
  `POST /sources/discover/gdelt/mock`
- `GET /access-policy`, `POST /access-policy/check`
- `POST /crawl/mock`, `POST /crawl/mock-url`,
  `POST /crawl/discover-gdelt-mock`, `POST /connectors/official/parse`,
  `POST /crawl/parse-official-fixture`
- `POST /clean/run`, `GET /events`
- `POST /features/build`, `GET /features`
- `POST /models/backtest`, `GET /metrics/backtest`,
  `POST /models/predict`, `GET /predictions`
- `POST /feedback`, `GET /feedback`, `GET /feedback/summary`
- `GET /experiences`, `GET /experiences/summary`,
  `POST /experience/build`, `GET /experience/summary`
- `POST /analysis/minimum-data`, `POST /analysis/model-promotion-check`
- `POST /crawler-optimizer/analyze`,
  `POST /crawler-optimizer/analyze-source`,
  `POST /crawler-optimizer/connector-spec`
- `GET /data-quality/profile`, `GET /data-quality/label-audit`,
  `POST /data-quality/report`

Mock crawl, mock GDELT discovery, and official connector parse endpoints are for
local fixture validation only. Forecasting and minimum-data outputs describe the
current data and backtest settings, require human review, and do not guarantee
future accuracy.

### FastAPI Business API MVP

This API layer exposes the completed local crawler mock, official fixture
parser, cleaner, feature builder, baseline predictor, feedback, experience
bank, and minimum-data analyzer workflows through synchronous local endpoints.
It does not start real crawlers, request public-health websites, deploy
remotely, retrain models automatically, or promote a model into production.

Responses intentionally return summaries. Source listings include source score
and status but not full metadata. Event and feature listings expose provenance
keys and feature keys rather than full provenance or `feature_json`. Prediction,
experience, minimum-data, and promotion-check outputs include limitations and
review-required semantics.

Safe local examples:

```bash
curl -X POST http://localhost:8000/crawl/mock-url -H "Content-Type: application/json" -d "{\"source_id\":\"1\",\"url\":\"https://fixture.invalid/example\",\"html\":\"<html><body>local fixture only</body></html>\"}"
curl "http://localhost:8000/metrics/backtest?country=United%20States&disease=norovirus&horizon_days=28&model_name=feature_rule"
curl -X POST http://localhost:8000/analysis/model-promotion-check -H "Content-Type: application/json" -d "{\"champion\":{\"model_version\":\"v1\",\"model_name\":\"moving_average\",\"metrics\":{\"regression\":{\"mae\":10}},\"warnings\":[]},\"challenger\":{\"model_version\":\"v2\",\"model_name\":\"feature_rule\",\"metrics\":{\"regression\":{\"mae\":8}},\"warnings\":[]}}"
```

Run locally:

```bash
uvicorn apps.api.main:app --host 0.0.0.0 --port 8000
```

Targeted test example:

```bash
python -m pytest tests/test_api_health.py tests/test_api_sources.py tests/test_api_crawl_mock.py tests/test_api_clean_features.py tests/test_api_models.py tests/test_api_feedback.py tests/test_api_minimum_data.py
```

## Streamlit Dashboard MVP

The Streamlit dashboard is a local visualization MVP for the FastAPI Business
API. It uses `API_BASE_URL` and defaults to `http://localhost:8000`. Importing
dashboard modules does not start Streamlit, open HTTP clients, launch Docker, or
access external public-health websites.

Dashboard pages:

- Overview: API status, source health, recent event/prediction rows, risk
  summary, and feedback counts.
- Sources: registry filters, source health, source score, and dry-run seed
  action.
- Access Guardrails: local source access policy summaries and guard decisions;
  it does not perform network access.
- Ingestion: local mock URL crawl, official fixture parsing, and mock GDELT
  discovery forms.
- Crawler Optimizer: local crawler failure diagnosis, recommended actions,
  connector spec drafts, and human-review warnings.
- Events and Features: local cleaner results and feature-key summaries.
- Models: baseline backtest, metrics lookup, latest prediction, offline model
  promotion check, prediction table, and risk summary.
- Feedback: analyst feedback submission, explicit experience build,
  feedback summary, experience summary, and experience bank table.
- Minimum Data: local minimum-data result summary, evaluation preview, learning
  curve, and ablation result view.
- Data Quality: table-level profiling, label audit warnings, and report
  generation summary.

The dashboard client sanitizes API payloads before rendering. It omits complete
HTML, raw text, fixture payloads, full metadata, full `feature_json`, and full
`lesson_json` fields, while preserving safe summary fields such as
`feature_keys`, `metadata_keys`, and `provenance_keys`.

Run locally after starting the FastAPI app separately:

```bash
streamlit run apps/dashboard/streamlit_app.py
```

The dashboard is for research and decision support only. Forecasts and minimum
data analysis results describe current local data and backtest settings, do not
guarantee future accuracy, and require human review before any public-health
decision.

## Local End-to-End Smoke Test

The local smoke test runs the main engineering path inside a temporary SQLite
database by default. It uses synthetic raw documents and local fixtures only.

It chains:

- source access policy static validation
- synthetic raw document creation
- cleaner processing into `epidemic_events`
- feature building into `model_features`
- predictor backtest and latest prediction
- human feedback and experience bank creation
- minimum data analysis
- local crawler optimizer safety summary
- optional FastAPI `TestClient` checks
- optional local synchronous worker pipeline summary

It does not access real networks, start Docker, start a real FastAPI service,
start Streamlit, start a resident worker, run remote deployment, read server
information files, or request external outbreak websites.

Run:

```bash
python scripts/smoke_test.py --temp-db
python scripts/smoke_test.py --temp-db --include-api
python scripts/smoke_test.py --temp-db --include-worker
```

Optional file database:

```bash
python scripts/smoke_test.py --database-url sqlite:///tmp/epidemic_smoke.db
python scripts/smoke_test.py --database-url sqlite:///tmp/epidemic_smoke.db --include-api
```

`--include-api` uses FastAPI `TestClient` with the same SQLite database and does
not start `uvicorn`. `--include-worker` calls the local synchronous
`PipelineRunner` and does not start a resident worker process.

The JSON result is a safe engineering summary. It omits full HTML, raw text,
full feature payloads, full lesson payloads, full provenance payloads, database
URLs, and sensitive configuration. Passing the smoke test means the local module
chain can execute together; it does not represent or guarantee forecast
accuracy, and minimum-data analysis remains an auxiliary local check requiring
human review.

## Worker / Scheduler MVP

The worker MVP is a local synchronous task orchestrator for tests, demos, and
manual development workflows. It registers tasks, runs one task, runs the local
synthetic pipeline, records in-memory job status, and describes a default
schedule configuration. It is not a production asynchronous queue and it does
not start a resident background worker.

It uses the Python standard library plus existing local modules. It does not
depend on Celery, Prefect, APScheduler, or any live external service. The
default schedule is only a configuration description; it does not execute
automatically.

Registered jobs:

- `seed_sources`
- `mock_crawl_url` (caller-provided HTML fixture only)
- `parse_official_fixture` (caller-provided fixture content only)
- `discover_gdelt_mock` / `mock_source_discovery` (mock GDELT JSON only)
- `clean_pending`
- `build_features`
- `backtest_model`
- `predict_latest`
- `summarize_feedback`
- `summarize_experiences`
- `minimum_data_analysis`
- `optimize_crawlers` (local diagnostics only)
- `check_access_policies` (static guardrail validation only)
- `data_quality_report` (local data quality and label-audit report only)
- `full_local_pipeline` (synthetic local data only)
- `smoke_test` (manual only by default)

Default schedule entries include:

- `daily_source_discovery_mock` (disabled by default)
- `hourly_mock_crawl_disabled_by_default` (disabled by default)
- `daily_clean`
- `daily_feature_build`
- `weekly_backtest`
- `daily_predict`
- `weekly_minimum_data_analysis`
- `daily_crawler_optimization`
- `daily_access_policy_check`
- `weekly_data_quality_report`

CLI examples:

```bash
python -m worker_agent.cli list-tasks
python -m worker_agent.cli describe-schedule
python -m worker_agent.cli run-task --database-url sqlite:///tmp/test.db --task seed_sources
python -m worker_agent.cli run-pipeline --database-url sqlite:///tmp/test.db --pipeline full_local_pipeline --country "United States" --disease "norovirus"
```

Older compatibility commands such as `list`, `run`, `run-many`, and
`run-due-once` are still available for existing tests. Do not pass secrets,
server information, tokens, private-key paths, or deployment credentials through
worker parameters.

The Docker Compose `worker` command remains a safe non-resident placeholder:

```bash
python -m worker_agent.cli describe-schedule
```

Production can later replace this MVP with Celery, Prefect, or another scheduler
or with cron/systemd timers after explicit design approval. The current worker
does not run real network crawls, does not automatically retrain models, and
does not automatically replace production models.

## Source Discovery Agent Integration

The Source Discovery Agent is wired into the outer MVP through FastAPI,
Dashboard, worker tasks, and the local smoke test. It is still fixture/mock
only: it does not run live search, does not request CDC, WHO, ECDC, GDELT, or
search engines, and does not activate new sources.

FastAPI endpoint:

```bash
POST /source-discovery/discover-fixture
```

The endpoint accepts JSON search results in the request body, evaluates
candidate source domains, writes accepted candidates to `source_registry` as
`pending_review`, and returns a safe summary with top candidates and
ConnectorSpec suggestions. It does not accept a fixture file path and does not
store or return full snippets, `metadata_json`, raw text, HTML, feature JSON,
lesson JSON, or provenance JSON.

Dashboard page:

```text
Source Discovery
```

The page lets analysts paste fixture/mock search-result JSON, run local
candidate discovery, and review source scores plus ConnectorSpec suggestions.
Every new source remains `pending_review`; ConnectorSpec output is advisory
only and does not generate or enable live connectors. Human review and Live
Access Guardrails are required before any real data-source access.

Worker task and smoke coverage:

```bash
python -m worker_agent.cli run-task --database-url sqlite:///tmp/test.db --task discover_sources_fixture --param fixture=tests/fixtures/source_discovery_norovirus_search_results.json
python scripts/smoke_test.py --temp-db
```

The smoke test uses an inline synthetic search-results payload and reports only
counts such as candidates found, candidates inserted or updated, and
ConnectorSpec suggestions generated.

## Milestone 1 Influenza Extensibility Pilot

The influenza pilot validates that the Milestone 1 data-foundation workflow can
extend beyond the original norovirus/NoroSTAT path without changing database
schema or using live network access.

Local fixtures:

- `tests/fixtures/source_discovery_influenza_search_results.json`
- `tests/fixtures/cdc_fluview_ili.csv`
- `tests/fixtures/cdc_fluview_table.html`
- `tests/fixtures/cdc_fluview_report.html`

Connector:

- `cdc_fluview` parses local CDC FluView-style CSV rows, HTML tables, and an
  official-report-page fixture into `raw_documents`.

Validated local chain:

- Source Discovery Agent discovers `United States + influenza` candidate
  sources and stores them as `pending_review`.
- Connector fixture parsing writes CDC FluView records to `raw_documents`.
- Cleaner Agent extracts influenza `epidemic_events` from structured metadata
  and official report text.
- Feature Builder generates `model_features`.
- Data Quality Agent and Crawler Optimizer Agent produce safe local summaries.

This pilot does not run free web search, search APIs, real CDC/ECDC requests,
or live connector execution. Any future live FluView access must be handled as
a separate Controlled Live Fetch step through Live Access Guardrails and human
approval.

## Milestone 1 Multi-Format Source Acquisition

The multi-format acquisition layer generalizes fixture parsing beyond one
connector. `crawler_agent.generic_parsers` supports local-only parsing for:

- CSV / downloadable dataset fixtures through `csv_dataset`.
- HTML table fixtures through `html_table`.
- JSON API response fixtures through `json_api`.
- Official report pages through `official_report_page`.
- Dashboard-like JSON metadata fixtures through `dashboard_data_source`.

Each parser returns `ParsedRecord` objects, and the adapter converts them into
the existing `ConnectorDocument` shape. Safe structured fields are stored in
metadata as `structured_fields` and `structured_event` so `CleanerAgent` can
produce `epidemic_events` without a database migration. Full HTML, raw fixture
payloads, complete metadata blobs, and feature payloads are not placed in
parser metadata.

Source Discovery can later select a parser from a `ConnectorSpec` or
`parser_strategy`, but newly discovered sources still enter `pending_review`.
This layer does not perform free web search, uncontrolled live fetch, production
prediction, or automatic source activation.

Local validation:

```bash
python scripts/influenza_extension_smoke.py --temp-db
```

## Source Discovery To Generic Parser Bridge

The bridge connects Source Discovery candidate sources and draft
`parser_strategy` values to the generic parser dispatcher. It is a local MVP for
validating the handoff from discovery to ingestion:

- Source Discovery reads fixture/mock search results and writes candidate
  sources to `source_registry` as `pending_review`.
- The bridge maps a reviewed parser strategy to a local fixture.
- `parse_by_strategy()` runs the matching generic parser.
- Parsed records are adapted to `ConnectorDocument`.
- Documents are saved to `raw_documents`, then Cleaner Agent creates
  `epidemic_events`, and Feature Builder creates `model_features`.
- Bridge reports include counts and warnings only; full fixture payloads,
  raw text, complete metadata, provenance, and feature JSON are omitted.

Supported bridge parser strategies are `csv_dataset`, `html_table`, `json_api`,
and `official_report_page`. The current fixture mapping focuses on the
influenza / FluView pilot. The bridge does not perform live source discovery,
free network search, real CDC/WHO/ECDC/GDELT requests, source activation, or
production prediction. Human review is required before live access or activation.

Local validation:

```bash
python scripts/source_discovery_bridge_smoke.py --temp-db
python -m source_discovery_agent.bridge_cli run-fixture-bridge \
  --database-url sqlite:///tmp/bridge.db \
  --country "United States" \
  --disease "influenza" \
  --parser-strategy csv_dataset
```

## Controlled Live Source Discovery Readiness

The live source discovery readiness layer prepares the project for a later,
explicitly approved one-request discovery pilot. It does not execute real
network discovery.

Readiness components:

- Provider abstraction: `mock`, `gdelt`, and `official_directory`.
- Provider policy in `configs/live_source_discovery_policy.example.json`.
- Guard decisions in `source_discovery_agent.live_guard`.
- Local preflight, plan generation, and controlled dry-run skeleton scripts.
- Runbook, preflight checklist, acceptance criteria, and rollback guidance in
  `docs/live_source_discovery/`.

Current safety boundary:

- Live discovery is disabled by default.
- Free web search is disabled.
- Follow-link behavior is disabled.
- Provider access requires explicit network approval, terms review, and manual
  review in a future step.
- Any future candidates must remain `pending_review`.
- No source is automatically activated and no production connector is created.

Readiness commands:

```bash
python scripts/live_source_discovery_preflight.py --provider gdelt --country "United States" --disease "influenza" --policy configs/live_source_discovery_policy.example.json --json
python scripts/plan_live_source_discovery.py --provider gdelt --country "United States" --disease "influenza" --output-dir reports/source_discovery/live_readiness
python scripts/run_controlled_live_source_discovery.py --provider gdelt --country "United States" --disease "influenza" --dry-run
```

Live discovery may also end in a controlled failure. For example, HTTP 429 from
GDELT is treated as a valid guardrail outcome when the system stops after one
request, does not retry, does not switch provider automatically, and does not
visit candidate URLs. Analyze such runs locally:

```bash
python scripts/analyze_live_source_discovery_run.py --run-dir reports/source_discovery/live_runs/<run_dir>
```

The postmortem classifies rate limits as `rate_limited`, recommends cooldown,
and can prepare a fallback readiness plan such as `official_directory` with
`max_requests=1`, no follow links, and all candidates still `pending_review`.

`official_directory` is the preferred fallback after a GDELT 429. It is not a
free web search provider. A later live run must provide one human-confirmed
official directory URL, keep `max_requests=1`, disable follow links, avoid
candidate-page visits, and store all candidates as `pending_review`.

## DeepSeek General Web Search Intelligence

The DeepSeek web search agent provides a guarded disease-country search workflow:

- `plan` builds safe query plans without network access.
- `run-mock` uses local mock search results and writes safe summaries to the local database.
- `run-controlled` requires explicit DeepSeek/search, terms-reviewed, and manual-reviewed flags before any live API/search request.
- Page reading is separately guarded by `allow_page_read`; candidate URLs are never followed automatically.
- Stored records are safe summaries only. Full HTML, raw page text, prompts, completions, secrets, and credentials must not be stored or displayed.
- Extracted records remain auxiliary evidence and require human review before cleaning, feature use, modeling, or source activation.

CLI examples:

```bash
python -m deepseek_web_search_agent.cli plan --country "United States" --disease "influenza"
python -m deepseek_web_search_agent.cli run-mock --country "United States" --disease "influenza"
```

The FastAPI routes live under `/deepseek-web-search/*`, and the Streamlit
Dashboard includes a `DeepSeek 全网检索` page for local demonstration.

## Deployment Dry-run

The deployment script is safe by default. It only builds a redacted deployment
plan unless real execution is explicitly requested.

Dry-run:

```bash
python scripts/deploy.py --server-info <path> --dry-run
python scripts/deploy.py --server-info <path>
```

The `--server-info` path must be provided explicitly. The script does not
default to reading any local server-info file. Dry-run does not run SSH, rsync,
Docker, FastAPI, Streamlit, or remote commands.

Real execution, for a later explicitly approved deployment step, requires both
flags:

```bash
python scripts/deploy.py --server-info <path> --execute --confirm-execute
```

Before any real deployment, run local checks:

```bash
python scripts/smoke_test.py
python scripts/check_compose.py
python -m pytest tests/test_api_health.py tests/test_smoke_test.py
```

Do not commit server information, local `.env` files, private keys, token files,
or deployment credentials. Remote deployment results still require human review.

## Forecasting Rules

Every forecast output must include:

- Input data version or snapshot identifier.
- Source provenance summary.
- Model name and version.
- Run timestamp.
- Prediction interval or other uncertainty estimate.
- Backtesting metrics where available.
- Human-review status.
- Limitations and assumptions.

Forecasts must not claim guaranteed accuracy.

## Current Status

This repository currently has project documentation and configuration guardrails only. Business modules should be implemented after the module boundaries, schema, Docker Compose services, and test strategy are confirmed.
