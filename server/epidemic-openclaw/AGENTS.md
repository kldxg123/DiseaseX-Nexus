# AGENTS.md

- Complete only one stage per task and stop for the next prompt.
- Store large files under `USER_HOME/autodl-tmp`.
- Do not use Docker inside the AutoDL container.
- Do not modify GPU drivers or CUDA.
- Never add server connection information or credentials to Git.
- Never delete user data.
- Never report unexecuted work as complete.
- New features require tests and a report.
- Deploy OpenClaw, databases, Qwen, and business features in separate approved stages.


## PostgreSQL rules

- Never run PostgreSQL as root.
- Never add database passwords to Git.
- Never expose port 5432 publicly.
- Use the later approved Alembic workflow for migrations.
- Never change the production database schema without a recorded migration.


## Local model rules

- Never add model weights to Git or enable `trust_remote_code`.
- Never bind vLLM to a public interface.
- Model or revision changes require rerunning structured tool-call tests.
- Tool-like text never counts as a structured `tool_calls` result.
- Model generations must not be written to the database as case facts.

## P0-4 OpenClaw boundary

Keep the Gateway loopback-only, preserve token authentication, keep bundled skills/plugins disabled, and expose only `session_status` until a later stage explicitly authorizes a change. Never commit OpenClaw state, sessions, logs, cache, runtime, or `.env`.

## P1-1 database invariants

Use Alembic for every production/test schema change; never call `Base.metadata.create_all()`. Keep all domain SQL explicitly schema-qualified, facts append-only, source provenance mandatory, and dataset snapshot ordering deterministic. Do not expose database/file/web/shell tools to OpenClaw. RLS is deferred until a real tenant boundary exists.
