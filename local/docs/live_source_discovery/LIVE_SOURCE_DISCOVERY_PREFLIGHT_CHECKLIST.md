# Live Source Discovery Preflight Checklist

- `allow-network` must be explicitly provided for any future live run.
- Provider must appear in the live discovery provider allowlist.
- `max_requests` must be less than or equal to the provider policy limit.
- Free web search must remain disabled unless a later reviewed policy permits it.
- Follow-link behavior must remain disabled unless a later reviewed policy permits it.
- Search-engine browsing must not be used.
- Source access policy must be configured and validated.
- Terms review must be complete.
- Manual review must be complete.
- Output candidates must remain `pending_review`.
- No full HTML, raw text, complete metadata, provenance JSON, feature JSON, lesson JSON, or sensitive configuration may be printed.
