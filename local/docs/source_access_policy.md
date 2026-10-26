# Source Access Policy

This document describes the local safety gate for future live data access. It is
not a statement that any real source has already been legally or operationally
approved for live collection.

## Purpose

Every future live connector must pass a source access policy review before any
real network request is allowed. The policy review records the allowed access
method, domain boundary, robots.txt requirement, terms-of-service review,
rate-limit settings, cache assumptions, and manual approval status.

The current MVP does not execute live fetches. It only provides local guardrail
checks so future connector work cannot accidentally bypass safety controls.

## Required Checks

- Live fetch is disabled by default.
- A caller must explicitly pass an `--allow-network` style flag for any future
  live access path.
- Robots.txt or equivalent access-policy review must pass before access.
- Site terms and allowed-use constraints must be reviewed before access.
- Manual review must be completed for every real source policy.
- Request count and minimum interval limits must be enforced by future live
  connectors.
- Requested URLs must stay within approved domains and outside disallowed paths.

## Prohibited Behavior

- Do not bypass login, captcha, paywalls, access controls, anti-abuse systems,
  or private APIs.
- Do not write cookies, credentials, private-key paths, tokens, server
  information, or secret values into policy files.
- Do not treat this example policy as authorization to access any real source.
- Do not run live network collection from tests.

## Example Static Checks

```bash
python scripts/check_source_access_policy.py --policy configs/source_access_policy.example.json
python scripts/check_source_access_policy.py --policy configs/source_access_policy.example.json --source-key cdc_norostat --url https://example.invalid/test --allow-network --terms-reviewed --manual-reviewed --robots-allowed
```

Denied guard decisions are expected for incomplete reviews and are not a script
failure. Malformed policy files are failures and return a non-zero exit code.

## Operational Notes

Before a real source is enabled in a future step, replace placeholder
`example.invalid` entries with reviewed source-specific policies, confirm the
allowed access method, record the review status, and keep a human approval path.
Live access should remain disabled in default local tests and smoke tests.

## CDC NoroSTAT Pilot Policy

The example policy includes a `cdc_norostat` entry for future CDC NoroSTAT live
connector work. It keeps live fetch disabled by default and still requires all
guardrail confirmations:

- explicit `--allow-network`
- robots access check
- site terms review
- manual review
- request count within the configured limit

The `cdc_norostat` policy includes CDC host names so future live connector code
can be checked against the correct domain boundary. This is only a pre-access
safety policy. It does not mean legal, terms-of-service, robots.txt, citation,
or operational approval has been completed.

Automated tests must continue to use mock HTML or fixture files. They must not
request CDC, WHO, ECDC, GDELT, or any other public website.

## CDC NoroSTAT Controlled Live Pilot Preparation

The controlled live pilot preparation package is local-only. It prepares the
runbook, preflight gate, plan files, acceptance criteria, and rollback notes for
a future CDC NoroSTAT dry-run. It does not fetch from CDC and does not access
the public internet.

Preparation commands:

```bash
python scripts/live_data_preflight.py --source-key cdc_norostat --policy configs/source_access_policy.example.json --json
python scripts/plan_live_data_pilot.py --source-key cdc_norostat --country "United States" --disease "norovirus" --output-dir reports/live_pilot
python scripts/run_controlled_live_pilot.py --source-key cdc_norostat --dry-run
```

Before any future live run, a human reviewer must confirm:

- the exact CDC URL
- CDC terms and allowed use
- robots access result
- explicit `--allow-network`
- `max_requests <= 3`
- output path and rollback strategy

After a future live run, generate a data quality report, run crawler optimizer
diagnostics, and run a local smoke test summary against the pilot database. Do
not place real server information, credentials, tokens, or private-key material
in commands, reports, or policy files.
