# Live Source Discovery Runbook

## Goal

Run controlled live source discovery for a reviewed `country + disease` pair after explicit human approval.

Suggested first pilots:

- `country = United States`, `disease = influenza`, `provider = gdelt`
- `country = United States`, `disease = norovirus`, `provider = curated_official_directory`

This readiness step does not execute real network requests. The next phase must confirm provider, endpoint, request count, terms review, robots review, and manual approval before any live discovery command is run.

## Operating Rules

- Live discovery must pass the provider access policy.
- Newly discovered sources must be stored as `pending_review`.
- Do not automatically activate sources.
- Do not automatically generate production crawlers.
- Do not follow links unless a later policy explicitly permits it.
- Do not run free web search or search-engine browsing.
- Do not output full response bodies, raw text, complete metadata, provenance JSON, feature JSON, or sensitive configuration.

## Readiness Commands

```bash
python scripts/live_source_discovery_preflight.py --provider gdelt --country "United States" --disease "influenza" --policy configs/live_source_discovery_policy.example.json --json
python scripts/plan_live_source_discovery.py --provider gdelt --country "United States" --disease "influenza" --output-dir reports/source_discovery/live_readiness
python scripts/run_controlled_live_source_discovery.py --provider gdelt --country "United States" --disease "influenza" --dry-run
```

These commands are local readiness checks only.

## Human Review Required

Before live execution, an analyst must confirm:

- Provider is allowlisted.
- Target endpoint and terms are reviewed.
- Request count is within policy.
- Results will remain `pending_review`.
- Output location is non-production.
- Rollback and cleanup steps are understood.

## Rate Limit Handling

HTTP 429 is an expected controlled-failure path, not an application crash. When
a provider returns 429:

- Do not retry immediately.
- Do not switch provider automatically.
- Do not visit candidate URLs.
- Record `failure_type = rate_limited`.
- Recommend cooldown before any future GDELT attempt.
- Consider a separately approved fallback provider such as `official_directory`.

Postmortem command:

```bash
python scripts/analyze_live_source_discovery_run.py --run-dir reports/source_discovery/live_runs/<run_dir>
```

## Official Directory Fallback

`official_directory` is the recommended fallback after a GDELT rate-limit
failure. It is not a free search provider. It may only access one
human-confirmed official directory URL in a separately approved live step.

Rules:

- `max_requests = 1`
- No follow links
- No candidate URL visits
- Directory URL domain must be in the live discovery policy allowlist
- Candidate sources remain `pending_review`
- No automatic connector generation or activation
