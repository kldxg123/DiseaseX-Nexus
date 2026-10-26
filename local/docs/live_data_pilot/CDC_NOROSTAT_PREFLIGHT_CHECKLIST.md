# CDC NoroSTAT Preflight Checklist

This checklist is for the CDC NoroSTAT controlled live pilot preparation. It is not executed against CDC and does not access the public internet.

## Policy Checks

- [ ] Access policy file exists.
- [ ] `cdc_norostat` source policy exists.
- [ ] `live_fetch_default_enabled=false`.
- [ ] `requires_allow_network_flag=true`.
- [ ] `robots_check_required=true`.
- [ ] `terms_review_required=true`.
- [ ] `manual_review_required=true`.
- [ ] `max_requests_per_run <= 3`.
- [ ] `allowed_domains` includes `cdc.gov` or `www.cdc.gov`.
- [ ] CLI requires explicit `--allow-network`.

## Human Review Checks

- [ ] CDC URL manually confirmed.
- [ ] Terms manually reviewed.
- [ ] robots manually checked.
- [ ] Request count and interval confirmed.
- [ ] Output directory selected.
- [ ] Rollback and cleanup plan prepared.

## Safety Checks

- [ ] Output is redacted and summary-only.
- [ ] No raw credentials are printed.
- [ ] No server information is read or emitted.
- [ ] No token, password, or private-key material is included.
- [ ] The command is not executed in automated tests.

## Model Boundary

This preflight gate does not validate prediction accuracy. Future pilot outputs
remain auxiliary analysis, require human review, and do not guarantee prediction
accuracy.

## Expected Preflight Behavior

- Without `--allow-network`, guardrails must deny live access.
- With `--allow-network`, robots, terms, manual review, allowed CDC domain, and request count within policy, guardrails may allow the future live fetch.
