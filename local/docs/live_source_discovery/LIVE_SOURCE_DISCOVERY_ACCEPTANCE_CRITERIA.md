# Live Source Discovery Acceptance Criteria

A future controlled live discovery run is acceptable only if:

- Request count does not exceed the approved limit.
- Network access is limited to the allowed provider endpoint.
- Candidates found are at least one, or the run fails safely with a clear reason.
- All discovered sources are written as `pending_review`.
- Source scores are explainable from authority, relevance, freshness, structured-data signal, and policy penalty.
- ConnectorSpec suggestions can be generated for review.
- Output does not include sensitive fields or full response bodies.
- No source is automatically activated.
- No production crawler is generated or deployed.
- No production prediction is triggered.
- Human review remains required before live use.

Controlled failure is also acceptable if:

- The provider returns HTTP 429 or another safe failure response.
- Exactly one provider request was made.
- No retry occurred.
- No fallback provider was contacted automatically.
- No candidate URL was visited.
- The failure is classified, for example `rate_limited`.
- Cooldown or fallback readiness guidance is generated.
